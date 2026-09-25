"""The old manual "schedule" buttons on the PPM and calibration pages."""
from datetime import date
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.urls import reverse

from calSchedules.models import CalibrationSchedule
from ppms.models import PPMSchedule
from scheduling.tests.test_planner import SYNC_TARGETS
from scheduling.tests.test_views import ViewTestBase

THIS_MONTH = date.today().replace(day=1)


class ManualScheduleTests(ViewTestBase):
    def setUp(self):
        super().setUp()
        for target in SYNC_TARGETS:
            patcher = patch(target, return_value=0)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.client.force_login(self.incharge)

    def test_ppm_button_uses_the_plan(self):
        self.active_plan(months=(3, 9))
        eq = self.equipment[0]
        PPMSchedule.objects.filter(equipment=eq).delete()
        self.client.post(reverse("schedule_equipment", args=[eq.id]))
        sched = PPMSchedule.open_schedules().get(equipment=eq)
        self.assertIn(sched.scheduled_month.month, (3, 9))
        self.assertEqual(sched.generation_source, "plan")

    def test_ppm_button_continues_the_cycle_without_a_plan(self):
        eq = self.equipment[0]
        last = THIS_MONTH - relativedelta(months=2)
        PPMSchedule.objects.create(equipment=eq, workshop=self.ws, scheduled_month=last,
                                   status="completed", maintenance_period=6)
        self.client.post(reverse("schedule_equipment", args=[eq.id]))
        # History is not "already scheduled"; the next visit is 6 months on.
        self.assertEqual(list(PPMSchedule.open_schedules().filter(equipment=eq)
                              .values_list("scheduled_month", flat=True)),
                         [last + relativedelta(months=6)])

    def test_ppm_bulk_ignores_other_workshops_equipment(self):
        self.active_plan()
        from Inventory.models import Department, Equipment
        other = Equipment.objects.create(
            description=self.monitor, workshop=self.other_ws, model="M", serial_number="OTHER-1",
            department=Department.objects.create(name="Ward X", workshop=self.other_ws), status="Working")
        PPMSchedule.objects.filter(equipment=other).delete()
        with patch("ppms.views.bulk.initialize_ppm_schedule_with_logic.delay") as delay:
            self.client.post(reverse("bulk_schedule_unscheduled"), {"equipment_ids": [str(other.id)]})
        self.assertFalse(PPMSchedule.objects.filter(equipment=other).exists())
        delay.assert_not_called()

    def test_calibration_button_no_longer_schedules_for_this_month(self):
        eq = self.equipment[0]
        CalibrationSchedule.objects.create(equipment=eq, workshop=self.ws, status="completed",
                                           scheduled_month=THIS_MONTH - relativedelta(months=3),
                                           calibration_period=12)
        self.client.post(reverse("schedule:schedule_calibration_equipment", args=[eq.id]))
        self.assertEqual(list(CalibrationSchedule.open_schedules().filter(equipment=eq)
                              .values_list("scheduled_month", flat=True)),
                         [THIS_MONTH + relativedelta(months=9)])

    def test_calibration_bulk_passes_arguments_by_name(self):
        with patch("calSchedules.views.bulk.initialize_calibration_schedule_with_logic.delay") as delay:
            delay.return_value.id = "t"
            self.client.post(reverse("schedule:bulk_schedule_unscheduled_calibration"),
                             {"equipment_ids": [str(self.equipment[0].id)], "calibration_period": "12"})
        args, kwargs = delay.call_args
        self.assertEqual(args, ())
        self.assertEqual(kwargs["calibration_period"], 12)
        self.assertEqual(kwargs["planning_logic"], "department")
        self.assertFalse(kwargs["normalize_existing"])
        self.assertEqual(kwargs["specific_equipment_ids"], [str(self.equipment[0].id)])
