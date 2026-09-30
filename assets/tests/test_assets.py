"""Assets: KPIs, machine page, QR labels, service contracts, stock alerts, alerts and the monthly report."""
import datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from assets.kpis import compute, repair_hours
from assets.models import ServiceContract
from Inventory.models import Supplier, Warranty
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
        # One open schedule per equipment: replace the ones the new-equipment signal made.
        PPMSchedule.objects.filter(equipment__in=[self.vent, self.pump]).delete()
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

    def test_the_old_kpi_page_opens_machine_reports(self):
        self.client.force_login(self.nic)
        self.assertRedirects(self.client.get(reverse("assets:kpis")), reverse("equipment_dashboard"),
                             fetch_redirect_response=False)


class NotificationTests(AssetsBase):
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
        with self.captureOnCommitCallbacks(execute=True):  # the stock email and bell go after commit
            part.stock_count = 2
            part.save()
            part.stock_count = 1
            part.save()
        request = AccessoryRequest.objects.get()
        self.assertEqual((request.request_type, request.requested_by.user, request.status),
                         ("restock", self.lead, "Pending"))
        self.assertTrue(CalibrationNotification.objects.filter(recipient=self.lead,
                                                               notification_type="stock_low").exists())

    @override_settings(NOTIFICATIONS_DIGEST_SENDER=False)
    def test_daily_alerts_run_on_the_site_sender_pc_only(self):
        from assets.tasks import daily_alerts

        self.assertEqual(daily_alerts(), {})

    @override_settings(NOTIFICATIONS_DIGEST_SENDER=True)
    def test_daily_alerts_cover_standards_and_contracts_only(self):
        from assets.tasks import daily_alerts

        today = timezone.localdate()
        ServiceContract.objects.create(equipment=self.pump, start_date=today - datetime.timedelta(days=300),
                                       end_date=today + datetime.timedelta(days=20))
        created = daily_alerts()
        # PPM etc. are in the digest; stock is mailed when it runs low (notifications.stock)
        self.assertEqual(set(created), {"standards", "contracts"})
        self.assertGreaterEqual(created["contracts"], 2)  # HOD and the in-charge
        self.assertEqual(daily_alerts()["contracts"], 0)  # not repeated while unread


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
                         {"expected_life_years": 8, "purchase_cost": "1250000"})
        self.vent.refresh_from_db()
        self.assertEqual((self.vent.expected_life_years, str(self.vent.purchase_cost)), (8, "1250000.00"))

    def test_machine_page_shows_the_warranty_and_failure_risk(self):
        today = timezone.localdate()
        Warranty.objects.create(equipment=self.vent, start_date=today.replace(year=today.year - 1),
                                expiry_date=today.replace(year=today.year + 1))
        self.client.force_login(self.lead)
        page = self.client.get(reverse("assets:machine", args=[self.vent.pk]))
        self.assertEqual(page.context["warranty"].equipment, self.vent)
        self.assertContains(page, "Chance of a repair in 90 days")

    def test_qr_labels_encode_the_machine_page(self):
        self.client.force_login(self.lead)
        captured = []
        real_make = __import__("qrcode").make

        def spy(data, **kwargs):
            captured.append(data)
            return real_make(data, **kwargs)

        with mock.patch("assets.labels.qrcode.make", side_effect=spy), \
                override_settings(SITE_URL="https://cirqen.hospital.local"):
            response = self.client.post(reverse("equipment_labels"), {"equipment": [self.vent.pk]})
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

    def test_lower_limits_are_set_on_the_parts_page_and_the_old_page_leads_there(self):
        part = Accessories.objects.create(name=Accessoriesname.objects.create(name="Battery"),
                                          equipment_description=self.vent_type, workshop=self.workshop,
                                          stock_count=4)
        self.client.force_login(self.lead)
        self.client.post(reverse("partstools:set_lower_limit", args=[part.pk]), {"reorder_level": 5})
        part.refresh_from_db()
        self.assertEqual(part.reorder_level, 5)
        self.assertEqual(AccessoryRequest.objects.count(), 1)  # 4 <= 5 raised a restock request
        self.assertRedirects(self.client.get(reverse("assets:stock_alerts")),
                             reverse("partstools:accessories_dashboard") + "?stock=low",
                             fetch_redirect_response=False)


class MonthlyReportTests(AssetsBase):
    @override_settings(EMAIL_HOST_USER="cirqen@hospital.example", NOTIFICATIONS_DIGEST_SENDER=True)
    def test_the_hod_gets_a_pdf(self):
        from assets.tasks import monthly_hod_report
        from notifications.mailer import send_pending

        self._repair(self.vent, TODAY - datetime.timedelta(days=20))
        self.assertEqual(monthly_hod_report(today=datetime.date(2026, 10, 1)), 1)
        self.assertEqual(monthly_hod_report(today=datetime.date(2026, 10, 1)), 0)  # queued once
        self.assertEqual(send_pending(), (1, 0))
        message = mail.outbox[0]
        self.assertIn("September 2026", message.subject)
        name, content, mimetype = message.attachments[0]
        self.assertEqual((name, mimetype), ("cirqen-report-2026-09.pdf", "application/pdf"))
        self.assertTrue(content.startswith(b"%PDF"))

    @override_settings(EMAIL_HOST_USER="cirqen@hospital.example", NOTIFICATIONS_DIGEST_SENDER=False)
    def test_only_the_site_sender_pc_sends_it(self):
        from assets.tasks import monthly_hod_report

        self.assertEqual(monthly_hod_report(today=datetime.date(2026, 10, 1)), 0)


class SyncTableTests(SimpleTestCase):
    def test_service_contracts_sync_after_suppliers_and_equipment(self):
        from config import CirqenConfig

        tables = CirqenConfig.DEFAULT_CONFIG["sync_tables"]
        self.assertLess(tables.index("public.Inventory_supplier"), tables.index("public.assets_servicecontract"))
        self.assertLess(tables.index("public.Inventory_equipment"), tables.index("public.assets_servicecontract"))
        self.assertLess(tables.index("public.Inventory_supplier"), tables.index("public.parts_tools_accessories"))
        self.assertNotIn("public.assets_supplier", tables)
