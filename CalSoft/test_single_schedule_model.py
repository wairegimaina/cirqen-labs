"""Calibration pages read the one live schedule model (calSchedules).

CalSoft had its own CalibrationSchedule that nothing wrote, so the CalSoft
dashboard counted zero pending or overdue calibrations whatever the real state.
"""
import datetime

from django.test import TestCase
from django.utils import timezone

from calSchedules.models import CalibrationSchedule
from CalSoft.view_modules.dashboard import _calibration_dashboard_data
from Inventory.models import Department, Equipment, EquipmentDescription
from workshop.models import Workshop


class SingleScheduleModelTests(TestCase):
    def test_dashboard_counts_the_live_schedules(self):
        workshop = Workshop.objects.create(name="Cal Centre", category="calibration_center")
        department = Department.objects.create(name="Renal", workshop=workshop)
        today = timezone.localdate()
        for n, month in enumerate((today.replace(day=1), (today.replace(day=1) - datetime.timedelta(days=40)))):
            # Different types: schedules of one type are grouped into one month.
            equipment = Equipment.objects.create(
                description=EquipmentDescription.objects.create(name=f"Type {n}"), model="D", serial_number=f"DIA-{n}", department=department,
                workshop=workshop, status="Working")
            schedule = CalibrationSchedule.objects.create(
                equipment=equipment, workshop=workshop, scheduled_month=today.replace(day=1), status="pending")
            # Saving rolls an overdue month forward; set the month directly.
            CalibrationSchedule.objects.filter(pk=schedule.pk).update(scheduled_month=month.replace(day=1))
        counts = _calibration_dashboard_data(today)["schedule_counts"]
        self.assertEqual(counts["pending"], 2)
        self.assertEqual(counts["overdue"], 1)

    def test_the_old_model_is_gone(self):
        import CalSoft.models as calsoft_models
        self.assertFalse(hasattr(calsoft_models, "CalibrationSchedule"))
