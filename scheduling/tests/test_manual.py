"""The manual buttons on the PPM and calibration pages: schedule, complete."""
from datetime import date

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from calSchedules.models import CalibrationSchedule
from CalSoft.models import CalibrationProcedure, CalibrationSession
from Inventory.models import Department, Equipment
from ppms.models import PPMSchedule
from scheduling import planner
from scheduling.tests.test_views import ViewTestBase

THIS_MONTH = date.today().replace(day=1)


class ManualScheduleTests(ViewTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.incharge)

    def test_ppm_button_uses_the_plan(self):
        self.active_plan(months=(3, 9))
        eq = self.equipment[0]
        PPMSchedule.objects.filter(equipment=eq).delete()
        self.client.post(reverse("schedule_equipment", args=[eq.id]))
        sched = PPMSchedule.open_schedules().get(equipment=eq)
        self.assertIn(sched.scheduled_month.month, (3, 9))
        self.assertEqual(sched.generation_source, "plan")

    def test_ppm_button_creates_a_plan_when_the_workshop_has_none(self):
        eq = self.equipment[0]
        self.client.post(reverse("schedule_equipment", args=[eq.id]))
        plan = planner.active_plan(self.ws.id, "ppm")
        self.assertIsNotNone(plan)
        sched = PPMSchedule.open_schedules().get(equipment=eq)
        self.assertIn(sched.scheduled_month.month, plan.rules.get(description=self.monitor).months)

    def test_ppm_bulk_ignores_other_workshops_equipment(self):
        self.active_plan()
        other = Equipment.objects.create(
            description=self.monitor, workshop=self.other_ws, model="M", serial_number="OTHER-1",
            department=Department.objects.create(name="Ward X", workshop=self.other_ws), status="Working")
        PPMSchedule.objects.filter(equipment=other).delete()
        self.client.post(reverse("bulk_schedule_unscheduled"), {"equipment_ids": [str(other.id)]})
        self.assertFalse(PPMSchedule.objects.filter(equipment=other).exists())

    def test_calibration_button_uses_the_plan_not_this_month(self):
        eq = self.equipment[0]
        self.client.post(reverse("schedule:schedule_calibration_equipment", args=[eq.id]))
        sched = CalibrationSchedule.open_schedules().get(equipment=eq)
        self.assertEqual(sched.generation_source, "plan")
        self.assertGreater(sched.scheduled_month, THIS_MONTH)

    def test_calibration_bulk_uses_the_plan(self):
        self.client.post(reverse("schedule:bulk_schedule_unscheduled_calibration"),
                         {"equipment_ids": [str(e.id) for e in self.equipment]})
        self.assertEqual(CalibrationSchedule.open_schedules().filter(generation_source="plan").count(), 3)


class ManualCompletionTests(ViewTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.incharge)

    def _open_calibration(self):
        planner.schedule(planner.ensure_plan(self.ws, "calibration"))
        return CalibrationSchedule.open_schedules().filter(equipment=self.equipment[0]).get()

    def test_calibration_needs_a_certificate(self):
        sched = self._open_calibration()
        self.client.post(reverse("schedule:mark_calibration_completed", args=[sched.id]))
        sched.refresh_from_db()
        self.assertNotEqual(sched.status, "completed")

    def test_calibration_with_a_certificate_is_recorded_and_the_next_one_planned(self):
        sched = self._open_calibration()
        user = get_user_model().objects.create_user(username="calib", password="pw12345!")
        procedure = CalibrationProcedure.objects.create(name="Proc", created_by=user)
        CalibrationSession.objects.bulk_create([CalibrationSession(
            schedule=sched, procedure=procedure, performed_by=user, status="approved",
            certificate_number="BNH-0001", timestamp=timezone.now())])
        self.client.post(reverse("schedule:mark_calibration_completed", args=[sched.id]))
        sched.refresh_from_db()
        self.assertEqual(sched.status, "completed")
        self.assertIsNotNone(sched.completed_date)
        nxt = CalibrationSchedule.open_schedules().get(equipment=self.equipment[0])
        self.assertGreater(nxt.scheduled_month, sched.scheduled_month)
        self.assertEqual(nxt.parent_schedule_id, sched.id)

    def test_ppm_completion_plans_the_next_visit(self):
        plan = planner.ensure_plan(self.ws, "ppm")
        planner.schedule(plan)
        sched = PPMSchedule.open_schedules().get(equipment=self.equipment[0])
        self.client.post(reverse("mark_completed", args=[sched.id]))
        sched.refresh_from_db()
        self.assertEqual(sched.status, "completed")
        nxt = PPMSchedule.open_schedules().get(equipment=self.equipment[0])
        self.assertGreater(nxt.scheduled_month, sched.scheduled_month)
