"""Corrective-maintenance prediction."""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from Inventory.models import Department, Equipment, EquipmentDescription, Warranty
from jobcard.models import jobcard
from machineReports.prediction import backtest, predict
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()
TODAY = datetime.date(2026, 9, 24)


class PredictionTests(TestCase):
    def setUp(self):
        self.workshop = Workshop.objects.create(name="Biomed", category="maintenance")
        self.department = Department.objects.create(name="ICU", workshop=self.workshop)
        self.pump = EquipmentDescription.objects.create(name="Infusion Pump")
        self.tech = User.objects.create_user(username="p_tech", password="pw12345!")
        UserProfile.objects.update_or_create(user=self.tech, defaults={
            "role": "Tech", "workshop": self.workshop, "level": "Engineer",
            "must_change_password": False, "has_uploaded_signature": True})
        self.devices = [self._device(f"P-{i}") for i in range(10)]

    def _device(self, serial):
        device = Equipment.objects.create(description=self.pump, model="X", serial_number=serial,
                                          department=self.department, status="Working")
        # In service since the warranty started (prediction._in_service_date).
        Warranty.objects.create(equipment=device, start_date=datetime.date(2023, 9, 1), period_months=12)
        return device

    def _repair(self, device, date):
        card = jobcard.objects.create(department=self.department, equipment=device, workshop=self.workshop,
                                      priority_level="High", action_taken="Repair", job_description="fix",
                                      status="Approved")
        jobcard.objects.filter(pk=card.pk).update(date_issued=date)

    def test_frequent_failer_ranks_first(self):
        bad = self.devices[0]
        for month in (1, 3, 5, 7, 9):
            self._repair(bad, datetime.date(2026, month, 10))
        self._repair(self.devices[1], datetime.date(2025, 2, 1))
        results = predict(Equipment.objects.all(), horizon_days=90, today=TODAY)
        self.assertEqual(results[0].equipment_id, bad.id)
        self.assertEqual(results[0].risk_level, "High")
        self.assertTrue(any("repaired" in r for r in results[0].reasons))

    def test_clean_device_borrows_the_fleet_rate(self):
        for d in self.devices[:5]:
            self._repair(d, datetime.date(2026, 6, 1))
        clean = next(p for p in predict(Equipment.objects.all(), today=TODAY) if p.equipment_id == self.devices[9].id)
        self.assertGreater(clean.rate_per_year, 0)
        self.assertIn("fleet rate", clean.reasons[-1])

    def test_declined_work_orders_are_not_failures(self):
        card = jobcard.objects.create(department=self.department, equipment=self.devices[0], workshop=self.workshop,
                                      priority_level="High", action_taken="Repair", job_description="x",
                                      status="Declined")
        jobcard.objects.filter(pk=card.pk).update(date_issued=datetime.date(2026, 9, 1))
        result = next(p for p in predict(Equipment.objects.all(), today=TODAY) if p.equipment_id == self.devices[0].id)
        self.assertEqual(result.repairs_in_window, 0)

    def test_backtest_uses_only_past_data(self):
        bad = self.devices[0]
        for month in (1, 2, 3, 4, 5):
            self._repair(bad, datetime.date(2026, month, 5))
        self._repair(bad, datetime.date(2026, 6, 20))  # the "future" failure
        Equipment.objects.update(created_at=datetime.datetime(2023, 1, 1, tzinfo=datetime.timezone.utc))
        result = backtest(Equipment.objects.all(), as_of=datetime.date(2026, 6, 1), horizon_days=60, top_fraction=0.1)
        self.assertEqual(result["failed_in_horizon"], 1)
        self.assertEqual(result["hits_in_top"], 1)

    def test_page_renders(self):
        self.client.force_login(self.tech)
        response = self.client.get(reverse("failure_risk"), {"horizon": 60})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "P-0")
