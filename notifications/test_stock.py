"""Phase 2: a part at or below its lower limit is "running low"; everyone in
its workshop is emailed once (HOD copied), and only the HOD and that
workshop's Engineer In-charge can change the limit."""
import datetime

from django.test import override_settings
from django.urls import reverse

from Inventory.models import EquipmentDescription
from notifications import digest
from notifications.models import EmailOutbox
from parts_tools.models import Accessories, Accessoriesname
from workshop.models import Workshop

from .tests import EMAIL, Base

LIMIT_URL = "partstools:set_lower_limit"


@override_settings(**EMAIL, SITE_URL="https://cirqen.hospital.test")
class StockAlertTests(Base):
    def setUp(self):
        super().setUp()
        self.part = Accessories.objects.create(
            name=Accessoriesname.objects.create(name="Flow Sensor"),
            equipment_description=EquipmentDescription.objects.get(name="Ventilator"),
            stock_count=6, reorder_level=3, workshop=self.workshop)

    def _set_stock(self, count):
        self.part.refresh_from_db()
        self.part.stock_count = count
        with self.captureOnCommitCallbacks(execute=True):
            self.part.save(update_fields=["stock_count", "updated_at"])  # as jobcard.deduct_stock does

    def _alerts(self):
        return EmailOutbox.objects.filter(kind="stock_low")

    def test_reaching_the_limit_emails_the_whole_workshop_copying_the_hod(self):
        self._set_stock(3)
        msg = self._alerts().get()
        self.assertEqual(set(msg.to.split(",")), {"n_tech@hospital.test", "n_deputy@hospital.test"})
        self.assertEqual(msg.cc, "n_hod@hospital.test")
        self.assertIn("Flow Sensor is running low: 3 left (Biomed)", msg.subject)
        self.assertIn("Lower limit:   3", msg.body_text)
        self.assertIn("?stock=low", msg.body_text)
        # assets.signals raised a restock request for the HOD; the email says so.
        self.assertIn("was raised automatically and is pending: waiting for the HOD's approval", msg.body_text)
        self.part.refresh_from_db()
        self.assertIsNotNone(self.part.low_stock_alerted_at)

    def test_it_is_mailed_once_while_it_stays_low(self):
        self._set_stock(3)
        self._set_stock(2)
        self._set_stock(0)
        self.assertEqual(self._alerts().count(), 1)

    def test_a_restock_above_the_limit_rearms_it(self):
        self._set_stock(2)
        self._set_stock(10)
        self.part.refresh_from_db()
        self.assertIsNone(self.part.low_stock_alerted_at)
        self._set_stock(1)
        self.assertEqual(self._alerts().count(), 2)

    def test_out_of_stock_says_so(self):
        self._set_stock(0)
        self.assertIn("is out of stock: 0 left", self._alerts().get().subject)

    def test_no_limit_means_no_alert(self):
        self.part.reorder_level = 0
        self.part.save()
        self._set_stock(0)
        self.assertFalse(self._alerts().exists())

    def test_other_workshops_are_not_mailed(self):
        other = Workshop.objects.create(name="Renal", category="maintenance")
        self._user("n_renal", "Tech", workshop=other, level="Engineer")
        self._set_stock(1)
        self.assertNotIn("n_renal@", self._alerts().get().to)

    def test_rows_changed_through_sync_send_nothing(self):
        # Sync writes with SQL, not the ORM: no signal, so the PC that made
        # the change is the only one that mails it.
        Accessories.objects.filter(pk=self.part.pk).update(stock_count=1)
        self.assertFalse(self._alerts().exists())

    def test_lowering_the_limit_to_the_current_stock_alerts(self):
        self.client.force_login(self.deputy)  # the workshop's Engineer In-charge
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse(LIMIT_URL, args=[self.part.pk]), {"reorder_level": 6})
        self.assertEqual(self._alerts().count(), 1)

    def test_the_hods_digest_lists_parts_running_low(self):
        self._set_stock(2)
        sections = {s["title"]: s for s in digest.build(self.hod, datetime.date(2026, 9, 28))}
        self.assertIn("Flow Sensor (Biomed): 2 left, lower limit 3", sections["Parts running low"]["items"])


@override_settings(**EMAIL)
class LowerLimitTests(Base):
    def setUp(self):
        super().setUp()
        self.part = Accessories.objects.create(
            name=Accessoriesname.objects.create(name="Oxygen Cell"),
            equipment_description=EquipmentDescription.objects.get(name="Ventilator"),
            stock_count=4, reorder_level=0, workshop=self.workshop)
        self.url = reverse(LIMIT_URL, args=[self.part.pk])

    def _set(self, user, value):
        self.client.force_login(user)
        response = self.client.post(self.url, {"reorder_level": value})
        self.part.refresh_from_db()
        return response

    def test_the_hod_and_the_workshops_in_charge_can_set_it(self):
        self._set(self.hod, 5)
        self.assertEqual(self.part.reorder_level, 5)
        self._set(self.deputy, 2)  # Engineer In-charge of Biomed
        self.assertEqual(self.part.reorder_level, 2)

    def test_an_ordinary_technologist_cannot(self):
        self._set(self.tech, 9)
        self.assertEqual(self.part.reorder_level, 0)

    def test_another_workshops_in_charge_cannot(self):
        other = Workshop.objects.create(name="Renal", category="maintenance")
        lead = self._user("n_lead", "Tech", workshop=other, level="Engineer Incharge")
        self._set(lead, 9)
        self.assertEqual(self.part.reorder_level, 0)

    def test_it_must_be_a_whole_number(self):
        self._set(self.hod, "-1")
        self._set(self.hod, "lots")
        self.assertEqual(self.part.reorder_level, 0)

    def test_the_dashboard_shows_running_low_and_filters_by_it(self):
        self._set(self.hod, 4)
        self.client.force_login(self.deputy)
        page = self.client.get(reverse("partstools:accessories_dashboard"))
        self.assertContains(page, "Running low")
        self.assertContains(page, f'action="{self.url}"')  # the in-charge can edit it
        low = self.client.get(reverse("partstools:accessories_dashboard"), {"stock": "low"})
        self.assertEqual([a.pk for a in low.context["accessories"]], [self.part.pk])
        self.client.force_login(self.tech)
        self.assertNotContains(self.client.get(reverse("partstools:accessories_dashboard")), f'action="{self.url}"')

    def test_the_api_carries_the_state_for_the_redrawn_table(self):
        self._set(self.hod, 4)
        self.client.force_login(self.tech)
        row = self.client.get(reverse("partstools:api_accessories")).json()["accessories"][0]
        self.assertEqual((row["stock_state"], row["stock_label"], row["reorder_level"], row["can_set_limit"]),
                         ("low", "Running low", 4, False))


@override_settings(**EMAIL)
class WorkOrderStockTests(Base):
    """Stock taken by approving a work order: one database update per part,
    all or nothing, and the running-low email still fires."""

    def setUp(self):
        super().setUp()
        from jobcard.models import jobcard

        vent = EquipmentDescription.objects.get(name="Ventilator")
        self.sensor = Accessories.objects.create(name=Accessoriesname.objects.create(name="Flow Sensor"),
                                                 equipment_description=vent, stock_count=4, reorder_level=3,
                                                 workshop=self.workshop)
        self.cell = Accessories.objects.create(name=Accessoriesname.objects.create(name="Oxygen Cell"),
                                               equipment_description=vent, stock_count=5, workshop=self.workshop)
        self.wo = jobcard.objects.create(department=self.department, equipment=self.equipment,
                                         workshop=self.workshop, priority_level="Medium", action_taken="Repair",
                                         job_description="Replace sensor", performed_by=self.tech)

    def _use(self, part, quantity):
        from jobcard.models import SparePartUsed

        return SparePartUsed.objects.create(job_card=self.wo, part=part, quantity=quantity)

    def test_approval_takes_the_parts_and_mails_the_shortage(self):
        self._use(self.sensor, 2)
        self._use(self.cell, 1)
        with self.captureOnCommitCallbacks(execute=True):
            self.wo.approve_job_card(self.nic)
        self.sensor.refresh_from_db()
        self.cell.refresh_from_db()
        self.assertEqual((self.sensor.stock_count, self.cell.stock_count), (2, 4))
        self.assertIn("Flow Sensor is running low: 2 left",
                      EmailOutbox.objects.get(kind="stock_low").subject)

    def test_a_part_short_of_stock_undoes_the_whole_approval(self):
        from django.core.exceptions import ValidationError

        self._use(self.cell, 1)
        short = self._use(self.sensor, 1)
        type(short).objects.filter(pk=short.pk).update(quantity=9)  # more than the 4 in stock
        with self.assertRaises(ValidationError):
            self.wo.approve_job_card(self.nic)
        self.sensor.refresh_from_db()
        self.cell.refresh_from_db()
        self.wo.refresh_from_db()
        self.assertEqual((self.sensor.stock_count, self.cell.stock_count, self.wo.stock_deducted), (4, 5, False))

    def test_declining_after_approval_puts_the_parts_back(self):
        self._use(self.sensor, 2)
        self.wo.approve_job_card(self.nic)
        self.wo.decline_job_card(self.nic, "Wrong device")
        self.sensor.refresh_from_db()
        self.assertEqual(self.sensor.stock_count, 4)
