"""The stock ledger: every change is a movement, and a part's count is the
total of its movements, so two PCs apart both count."""
import uuid

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.urls import reverse

from Inventory.models import Department, Equipment, EquipmentDescription
from core.testing import requires_postgres
from parts_tools import stock
from parts_tools.models import Accessories, Accessoriesname, AccessoryRequest, StockMovement
from parts_tools.tasks import reconcile_stock
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()


class LedgerBase(TestCase):
    def setUp(self):
        self.workshop = Workshop.objects.create(name="Biomed", category="maintenance")
        self.department = Department.objects.create(name="ICU", workshop=self.workshop)
        self.vent = EquipmentDescription.objects.create(name="Ventilator")
        self.equipment = Equipment.objects.create(description=self.vent, model="V", serial_number="V-1",
                                                  department=self.department, status="Working")
        self.hod = self._user("l_hod", "HOD")
        self.tech = self._user("l_tech", "Tech", workshop=self.workshop, level="Engineer")
        self.nic = self._user("l_nic", "NIC", department=self.department)
        self.part = Accessories.objects.create(name=Accessoriesname.objects.create(name="Flow Sensor"),
                                               equipment_description=self.vent, stock_count=10,
                                               workshop=self.workshop)

    def _user(self, username, role, **profile):
        user = User.objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **profile})
        return user

    def _count(self):
        self.part.refresh_from_db()
        return self.part.stock_count

    def _moves(self):
        return list(StockMovement.objects.filter(accessory=self.part).order_by("created_at")
                    .values_list("reason", "change"))


class LedgerTests(LedgerBase):
    def test_a_new_part_opens_with_what_it_was_created_with(self):
        self.assertEqual(self._moves(), [("opening", 10)])
        self.assertEqual(StockMovement.objects.get(reason="opening").pk, stock.opening_id(self.part.pk))

    def test_a_work_order_takes_and_a_decline_returns(self):
        from jobcard.models import SparePartUsed, jobcard

        wo = jobcard.objects.create(department=self.department, equipment=self.equipment, workshop=self.workshop,
                                    priority_level="Medium", action_taken="Repair", job_description="x",
                                    performed_by=self.tech)
        SparePartUsed.objects.create(job_card=wo, part=self.part, quantity=3)
        wo.approve_job_card(self.nic)
        self.assertEqual((self._count(), self._moves()), (7, [("opening", 10), ("work_order", -3)]))
        taken = StockMovement.objects.get(reason="work_order")
        self.assertEqual((taken.job_card_id, taken.created_by_id), (wo.pk, self.nic.pk))
        wo.decline_job_card(self.nic, "Wrong device")
        self.assertEqual((self._count(), self._moves()[-1]), (10, ("work_order_undone", 3)))

    def test_a_stock_take_records_the_difference(self):
        self.assertIsNone(stock.set_count(self.part.pk, 10))
        stock.set_count(self.part.pk, 6, note="Counted in store", created_by=self.hod)
        self.assertEqual((self._count(), self._moves()[-1]), (6, ("adjustment", -4)))

    def test_the_hods_edit_form_is_a_stock_take(self):
        self.client.force_login(self.hod)
        self.client.post(reverse("partstools:edit_accessory", args=[self.part.pk]), {
            "name": self.part.name_id, "equipment_description": self.vent.pk, "stock_count": 12,
            "unit_cost": "150", "note": "Shelf B"})
        self.assertEqual((self._count(), self._moves()[-1]), (12, ("adjustment", 2)))
        self.assertEqual(str(self.part.unit_cost), "150.00")

    def test_receiving_a_restock_is_a_movement_linked_to_the_request(self):
        req = AccessoryRequest.objects.create(request_type="restock", existing_accessory=self.part,
                                              equipment_description=self.vent, requested_quantity=5,
                                              requested_by=self.tech.userprofile, workshop=self.workshop,
                                              status="Approved", unit_cost=100)
        self.client.force_login(self.tech)
        self.client.post(reverse("partstools:accept_accessory_request", args=[req.pk]))
        self.assertEqual(self._count(), 15)
        received = StockMovement.objects.get(reason="received")
        self.assertEqual((received.change, received.request_id), (5, req.pk))

    def test_a_new_part_from_a_request_opens_at_zero_then_is_received(self):
        req = AccessoryRequest.objects.create(request_type="new", accessory_name="Oxygen Cell",
                                              equipment_description=self.vent, requested_quantity=4,
                                              requested_by=self.tech.userprofile, workshop=self.workshop,
                                              status="Approved", unit_cost=100)
        self.client.force_login(self.tech)
        self.client.post(reverse("partstools:accept_accessory_request", args=[req.pk]))
        part = Accessories.objects.get(name__name="Oxygen Cell")
        self.assertEqual(part.stock_count, 4)
        self.assertEqual(list(part.movements.order_by("created_at").values_list("reason", "change")),
                         [("opening", 0), ("received", 4)])


@requires_postgres
class TwoPcTests(LedgerBase):
    """What the ledger is for: two PCs each using the same part while apart."""

    def _arrives_by_sync(self, change):
        # The sync agent writes rows with SQL: no ORM, no signals.
        with connection.cursor() as cursor:
            cursor.execute(
                'INSERT INTO "parts_tools_stockmovement" (id, accessory_id, change, reason, note, needs_sync, '
                'created_at, updated_at, pending_delete, active_status) '
                "VALUES (%s, %s, %s, 'work_order', '', false, now(), now(), false, true)",
                [uuid.uuid4(), self.part.pk, change])

    def test_both_deductions_count_after_sync(self):
        stock.take(self.part.pk, 2)            # this PC: 10 -> 8
        self._arrives_by_sync(-3)              # the other PC took 3 while apart
        Accessories.objects.filter(pk=self.part.pk).update(stock_count=7)  # its count won last-write-wins
        self.assertEqual(self._count(), 7)     # without the ledger, 2 were lost
        stock.reconcile()
        self.assertEqual(self._count(), 5)     # 10 - 2 - 3

    def test_reconciling_does_not_upload_the_correction(self):
        self._arrives_by_sync(-1)
        before = Accessories.objects.get(pk=self.part.pk).updated_at
        stock.reconcile()
        self.part.refresh_from_db()
        self.assertEqual((self.part.stock_count, self.part.updated_at), (9, before))

    def test_a_part_without_an_opening_keeps_its_synced_count(self):
        StockMovement.objects.filter(accessory=self.part).delete()
        self._arrives_by_sync(-3)
        stock.reconcile()
        self.assertEqual(self._count(), 10)

    def test_a_shortfall_found_by_reconciling_still_alerts(self):
        from notifications.models import EmailOutbox

        self.tech.email = "t@h.org"
        self.tech.save()
        Accessories.objects.filter(pk=self.part.pk).update(reorder_level=4)
        self._arrives_by_sync(-7)
        with self.captureOnCommitCallbacks(execute=True):
            stock.reconcile()
        self.assertTrue(EmailOutbox.objects.filter(kind="stock_low").exists())


class OpeningBalanceTests(LedgerBase):
    def setUp(self):
        super().setUp()
        # A part from before the ledger: no opening yet.
        StockMovement.objects.filter(accessory=self.part).delete()

    def test_the_opening_is_the_count_less_movements_already_known(self):
        StockMovement.objects.create(accessory=self.part, change=-2, reason="work_order")
        self.assertEqual(stock.create_missing_openings(), 1)
        self.assertEqual(StockMovement.objects.get(reason="opening").change, 12)
        stock.reconcile()
        self.assertEqual(self._count(), 10)

    def test_it_is_written_once_whoever_writes_it(self):
        stock.create_missing_openings()
        self.assertEqual(stock.create_missing_openings(), 0)
        self.assertIsNone(stock.ensure_opening(self.part))
        self.assertEqual(StockMovement.objects.filter(reason="opening").count(), 1)

    @override_settings(NOTIFICATIONS_DIGEST_SENDER=False)
    def test_only_the_site_sender_pc_writes_openings(self):
        self.assertEqual(reconcile_stock(), (0, 0))
        self.assertFalse(StockMovement.objects.filter(reason="opening").exists())

    @override_settings(NOTIFICATIONS_DIGEST_SENDER=True)
    def test_the_sender_pc_writes_them_in_the_background_task(self):
        self.assertEqual(reconcile_stock(), (1, 0))
