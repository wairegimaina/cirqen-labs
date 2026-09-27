"""Phase 3: notifications, KPIs, risk, QR labels, contracts and stock alerts."""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from assets.kpis import compute, repair_hours
from assets.models import ServiceContract, Supplier
from assets.risk import score_machines
from CalSoft.models import CalibrationNotification, CalibrationProcedure, CalibrationSession
from Inventory.models import Department, Equipment, EquipmentDescription
from jobcard.models import jobcard
from parts_tools.models import Accessories, AccessoryRequest, Accessoriesname
from ppms.models import PPMSchedule
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()
TODAY = datetime.date(2026, 9, 15)


class AssetsBase(TestCase):
    def setUp(self):
        self.workshop = Workshop.objects.create(name="Biomed", category="maintenance")
        self.icu = Department.objects.create(name="ICU", workshop=self.workshop)
        self.renal = Department.objects.create(name="Renal", workshop=self.workshop)
        self.vent_type = EquipmentDescription.objects.create(name="Ventilator")
        self.vent = self._machine("VENT-1", self.icu, self.vent_type)
        self.pump = self._machine("PUMP-1", self.renal, EquipmentDescription.objects.create(name="Infusion pump"))
        self.hod = self._user("a_hod", "HOD", email="hod@hospital.example")
        self.lead = self._user("a_lead", "Tech", workshop=self.workshop, level="Engineer Incharge",
                               email="lead@hospital.example")
        self.tech = self._user("a_tech", "Tech", workshop=self.workshop, level="Engineer")
        self.nic = self._user("a_nic", "NIC", department=self.icu, email="nic@hospital.example")

    def _machine(self, serial, department, description, **extra):
        return Equipment.objects.create(description=description, model="M", serial_number=serial,
                                        department=department, workshop=department.workshop,
                                        status=extra.pop("status", "Working"), **extra)

    def _user(self, username, role, email="", **profile):
        user = User.objects.create_user(username=username, password="pw12345!", email=email)
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **profile})
        return user

    def _repair(self, machine, day, start="09:00", end="11:00", status="Approved"):
        card = jobcard.objects.create(department=machine.department, equipment=machine, workshop=self.workshop,
                                      priority_level="High", action_taken="Repair", job_description="fix",
                                      status=status)
        # update(): older records can hold a finish past midnight, which the form now refuses.
        jobcard.objects.filter(pk=card.pk).update(date_issued=day, time_started=start, time_completed=end)
        return card


class RepairHoursTests(SimpleTestCase):
    def test_overnight_repairs_wrap(self):
        self.assertEqual(repair_hours(datetime.time(22), datetime.time(2)), 4)
        self.assertIsNone(repair_hours(None, datetime.time(2)))


class KpiTests(AssetsBase):
    def test_mtbf_mttr_uptime_and_ppm(self):
        self._repair(self.vent, TODAY - datetime.timedelta(days=30), "09:00", "13:00")
        self._repair(self.vent, TODAY - datetime.timedelta(days=60), "22:00", "02:00")
        self._repair(self.pump, TODAY - datetime.timedelta(days=10), status="Declined")  # not a failure
        month = TODAY.replace(day=1)
        PPMSchedule.objects.bulk_create([
            PPMSchedule(equipment=self.vent, workshop=self.workshop, scheduled_month=month, status="completed"),
            PPMSchedule(equipment=self.pump, workshop=self.workshop, scheduled_month=month, status="pending"),
        ])
        k = compute(Equipment.objects.all(), months=12, today=TODAY)
        self.assertEqual(k.failures, 2)
        self.assertEqual(k.mttr_hours, 4.0)
        self.assertAlmostEqual(k.mtbf_days, 2 * k.period_days / 2, places=0)
        self.assertLess(k.uptime_percent, 100)
        self.assertEqual((k.ppm_completed, k.ppm_due, k.ppm_completion_percent), (1, 2, 50.0))
        self.assertEqual(k.by_type[0]["type"], "Ventilator")

    def test_kpi_page_is_scoped_for_an_in_charge(self):
        self._repair(self.pump, TODAY)
        self.client.force_login(self.nic)
        page = self.client.get(reverse("assets:kpis"))
        self.assertEqual(page.context["k"].machines, 1)  # ICU only


class RiskTests(AssetsBase):
    def test_score_ranks_and_explains(self):
        today = timezone.localdate()
        for day in (5, 40, 80, 120):
            self._repair(self.vent, today - datetime.timedelta(days=day))
        self.vent.purchase_date = today - datetime.timedelta(days=365 * 9)
        self.vent.expected_life_years = 10
        self.vent.status = "Not working"
        self.vent.save()
        procedure = CalibrationProcedure.objects.create(name="Flow", created_by=self.hod)
        CalibrationSession.objects.create(procedure=procedure, performed_by=self.hod, device_serial="VENT-1",
                                          status="approved", overall_pass=False)
        rows = score_machines(Equipment.objects.all())
        self.assertEqual(rows[0]["equipment"], self.vent)
        self.assertGreaterEqual(rows[0]["score"], 90)
        reasons = " ".join(rows[0]["reasons"])
        for text in ("4 repairs", "Not working", "failed its last calibration", "expected life"):
            self.assertIn(text, reasons)
        self.assertNotIn(self.pump, [r["equipment"] for r in rows])

    def test_a_current_contract_removes_the_cover_part(self):
        today = timezone.localdate()
        self.pump.warranty_end = today - datetime.timedelta(days=1)
        self.pump.save()
        self.assertEqual(score_machines(Equipment.objects.filter(pk=self.pump.pk))[0]["score"], 5)
        ServiceContract.objects.create(equipment=self.pump, start_date=today - datetime.timedelta(days=10),
                                       end_date=today + datetime.timedelta(days=300))
        self.assertEqual(score_machines(Equipment.objects.filter(pk=self.pump.pk)), [])


class NotificationTests(AssetsBase):
    @override_settings(EMAIL_HOST_USER="cirqen@hospital.example")
    def test_a_new_work_order_notifies_the_departments_in_charge_once(self):
        card = jobcard.objects.create(department=self.icu, equipment=self.vent, workshop=self.workshop,
                                      priority_level="High", action_taken="Repair", job_description="x",
                                      status="Waiting Approval")
        notes = CalibrationNotification.objects.filter(recipient=self.nic, notification_type="approval_needed")
        self.assertEqual(notes.count(), 1)
        self.assertIn(str(card.id), notes.get().action_url)
        self.assertEqual([m.to for m in mail.outbox], [["nic@hospital.example"]])
        card.save()  # an update is not a new work order
        self.assertEqual(notes.count(), 1)

    def test_sms_goes_through_africas_talking_when_configured(self):
        from core.notify import notify

        UserProfile.objects.filter(user=self.nic).update(phone_number="+254700000001")
        self.nic.refresh_from_db()
        env = {"AFRICASTALKING_USERNAME": "sandbox", "AFRICASTALKING_API_KEY": "k"}
        with mock.patch.dict("os.environ", env), mock.patch("requests.post") as post:
            post.return_value.status_code = 201
            notify([self.nic], "approval_needed", "Approve", "A work order waits", email=False)
        self.assertEqual(post.call_args.kwargs["data"]["to"], "+254700000001")

    def test_low_stock_raises_one_restock_request(self):
        part = Accessories.objects.create(name=Accessoriesname.objects.create(name="Flow sensor"),
                                          equipment_description=self.vent_type, workshop=self.workshop,
                                          stock_count=10, reorder_level=3)
        self.assertFalse(AccessoryRequest.objects.exists())
        part.stock_count = 2
        part.save()
        part.stock_count = 1
        part.save()
        request = AccessoryRequest.objects.get()
        self.assertEqual((request.request_type, request.requested_by.user, request.status),
                         ("restock", self.lead, "Pending"))
        self.assertTrue(CalibrationNotification.objects.filter(recipient=self.lead,
                                                               notification_type="stock_low").exists())

    def test_daily_alerts_cover_ppm_contracts_and_stock(self):
        from assets.tasks import daily_alerts

        today = timezone.localdate()
        PPMSchedule.objects.bulk_create([PPMSchedule(
            equipment=self.vent, workshop=self.workshop, status="pending",
            scheduled_month=(today.replace(day=1) - datetime.timedelta(days=40)).replace(day=1))])
        ServiceContract.objects.create(equipment=self.pump, start_date=today - datetime.timedelta(days=300),
                                       end_date=today + datetime.timedelta(days=20))
        created = daily_alerts()
        self.assertEqual(created["ppm"], 1)
        self.assertGreaterEqual(created["contracts"], 2)  # HOD and the in-charge
        self.assertEqual(daily_alerts()["ppm"], 0)  # not repeated while unread


class PageTests(AssetsBase):
    def test_machine_page_is_scoped_and_offers_a_work_order(self):
        self.client.force_login(self.tech)
        page = self.client.get(reverse("assets:machine", args=[self.vent.pk]))
        self.assertContains(page, f"?equipment={self.vent.pk}")
        self.client.force_login(self.nic)
        self.assertEqual(self.client.get(reverse("assets:machine", args=[self.pump.pk])).status_code, 404)

    def test_work_order_form_opens_with_the_scanned_machine(self):
        self.client.force_login(self.tech)
        page = self.client.get(reverse("jobcard:create_job_card"), {"equipment": self.vent.pk})
        self.assertEqual(page.context["form_data"]["equipment"], str(self.vent.pk))
        self.assertEqual(self.client.get(reverse("jobcard:create_job_card"),
                                         {"equipment": "not-a-uuid"}).status_code, 200)

    def test_only_managers_change_asset_details(self):
        self.client.force_login(self.tech)
        denied = self.client.post(reverse("assets:machine", args=[self.vent.pk]), {"expected_life_years": 8})
        self.assertEqual(denied.status_code, 403)
        self.client.force_login(self.lead)
        self.client.post(reverse("assets:machine", args=[self.vent.pk]),
                         {"expected_life_years": 8, "warranty_end": "2027-01-31"})
        self.vent.refresh_from_db()
        self.assertEqual((self.vent.expected_life_years, str(self.vent.warranty_end)), (8, "2027-01-31"))

    def test_qr_labels_encode_the_machine_page(self):
        self.client.force_login(self.lead)
        captured = []
        real_make = __import__("qrcode").make

        def spy(data, **kwargs):
            captured.append(data)
            return real_make(data, **kwargs)

        with mock.patch("assets.labels.qrcode.make", side_effect=spy), \
                override_settings(SITE_URL="https://cirqen.hospital.local"):
            response = self.client.get(reverse("assets:labels"), {"department": self.icu.pk, "download": 1})
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertEqual(captured, [f"https://cirqen.hospital.local{reverse('assets:machine', args=[self.vent.pk])}"])

    def test_contract_by_serial_within_scope(self):
        supplier = Supplier.objects.create(name="MedEquip Ltd")
        self.client.force_login(self.lead)
        self.client.post(reverse("assets:contracts"), {
            "serial_number": "vent-1", "supplier": supplier.pk, "cover": "full",
            "start_date": "2026-01-01", "end_date": "2026-12-31"})
        self.assertEqual(ServiceContract.objects.get().equipment, self.vent)
        bad = self.client.post(reverse("assets:contracts"), {
            "serial_number": "NOPE", "cover": "full", "start_date": "2026-01-01", "end_date": "2025-01-01"})
        self.assertContains(bad, "No machine with that serial number")
        self.assertContains(bad, "end date is before the start date")

    def test_reorder_levels_are_set_on_the_stock_page(self):
        part = Accessories.objects.create(name=Accessoriesname.objects.create(name="Battery"),
                                          equipment_description=self.vent_type, workshop=self.workshop,
                                          stock_count=4)
        self.client.force_login(self.lead)
        self.client.post(reverse("assets:stock_alerts"), {"part": part.pk, "reorder_level": 5})
        part.refresh_from_db()
        self.assertEqual(part.reorder_level, 5)
        self.assertContains(self.client.get(reverse("assets:stock_alerts")), "At or below reorder level")
        self.assertEqual(AccessoryRequest.objects.count(), 1)  # 4 <= 5 raised a restock request


class MonthlyReportTests(AssetsBase):
    @override_settings(EMAIL_HOST_USER="cirqen@hospital.example")
    def test_the_hod_gets_a_pdf(self):
        from assets.tasks import monthly_hod_report

        self._repair(self.vent, TODAY - datetime.timedelta(days=20))
        self.assertEqual(monthly_hod_report(today=datetime.date(2026, 10, 1)), 1)
        message = mail.outbox[0]
        self.assertIn("September 2026", message.subject)
        name, content, mimetype = message.attachments[0]
        self.assertEqual((name, mimetype), ("cirqen-report-2026-09.pdf", "application/pdf"))
        self.assertTrue(content.startswith(b"%PDF"))


class SyncTableMergeTests(SimpleTestCase):
    def test_new_tables_reach_old_config_files_in_dependency_order(self):
        from config import merge_sync_tables

        old = ["public.workshop_workshop", "public.Inventory_equipment", "public.CalSoft_calibrationschedule",
               "public.parts_tools_accessories"]
        merged = merge_sync_tables(old)
        self.assertNotIn("public.CalSoft_calibrationschedule", merged)
        self.assertIn("public.assets_servicecontract", merged)
        self.assertLess(merged.index("public.assets_supplier"), merged.index("public.parts_tools_accessories"))
        self.assertLess(merged.index("public.Inventory_equipment"), merged.index("public.assets_servicecontract"))
