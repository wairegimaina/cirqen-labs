"""Calibration schedule integrity (scheduling redesign, phase 0).

Completed schedules keep their month, cleanup retires rather than deletes,
overdue schedules stay in their due month, the daily auto-scheduler picks up
equipment whose chain broke, and no writer gives a device a second open
schedule.
"""
from datetime import date

from dateutil.relativedelta import relativedelta
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from Inventory.models import Department, Equipment, EquipmentDescription
from calSchedules import grouping
from calSchedules.models import CalibrationSchedule
from calSchedules.tasks import (
    auto_schedule_unscheduled_equipment,
    cleanup_orphaned_calibration_schedules,
    push_overdue_schedules,
    remove_inactive_equipment_schedules,
    validate_calibration_schedules,
)
from calSchedules.tests.test_cycle import NoSyncMixin
from workshop.models import Workshop

THIS_MONTH = date.today().replace(day=1)


class ResumeMonthTests(SimpleTestCase):
    def test_next_period_when_still_ahead(self):
        self.assertEqual(
            grouping.resume_month(date(2026, 8, 1), 6, date(2026, 9, 1)), date(2027, 2, 1)
        )

    def test_steps_whole_periods_past_missed_visits(self):
        # Feb 2025 on 6 months: Aug 2025 and Feb 2026 were missed -> Aug 2026 is
        # also past (Sept 2026 now) -> Feb 2027, still a February/August slot.
        self.assertEqual(
            grouping.resume_month(date(2025, 2, 1), 6, date(2026, 9, 1)), date(2027, 2, 1)
        )

    def test_this_month_counts(self):
        self.assertEqual(
            grouping.resume_month(date(2025, 9, 1), 12, date(2026, 9, 1)), date(2026, 9, 1)
        )

    def test_day_is_floored_to_the_first(self):
        self.assertEqual(
            grouping.resume_month(date(2026, 3, 31), 3, date(2026, 1, 1)), date(2026, 6, 1)
        )


class CalIntegrityBase(NoSyncMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.workshop = Workshop.objects.create(name="Cal WS")
        self.lab = Department.objects.create(name="Lab", workshop=self.workshop)
        self.desc = EquipmentDescription.objects.create(name="Balance")
        self.serial = 0

    def equipment(self):
        self.serial += 1
        return Equipment.objects.create(
            description=self.desc, model="M", serial_number=f"CAL-{self.serial}",
            department=self.lab, workshop=self.workshop, status="Working",
        )

    def schedule(self, eq, month, status="pending", **extra):
        return CalibrationSchedule.objects.create(
            equipment=eq, workshop=self.workshop, scheduled_month=month, status=status,
            calibration_period=12, planning_logic="department", **extra,
        )

    def open_months(self, eq):
        return list(
            CalibrationSchedule.open_schedules().filter(equipment=eq)
            .order_by("scheduled_month").values_list("scheduled_month", flat=True)
        )


class CompletedIsImmutableTests(CalIntegrityBase):
    def test_completed_month_cannot_change(self):
        s = self.schedule(self.equipment(), THIS_MONTH, status="completed")
        s.scheduled_month = THIS_MONTH + relativedelta(months=1)
        with self.assertRaises(ValidationError):
            s.save()


class OverdueTests(CalIntegrityBase):
    def test_overdue_schedule_stays_in_its_due_month(self):
        due = THIS_MONTH - relativedelta(months=3)
        # System-created, so the group aligner leaves it where it is: a manual
        # past-month schedule is snapped to this month on save (phase 2 retires
        # that aligner).
        s = self.schedule(self.equipment(), due, generation_source="signal")
        push_overdue_schedules()
        s.refresh_from_db()
        self.assertEqual(s.scheduled_month, due)
        self.assertTrue(s.is_overdue)


class CleanupKeepsHistoryTests(CalIntegrityBase):
    def _inactive_with_history(self):
        eq = self.equipment()
        done = self.schedule(eq, THIS_MONTH - relativedelta(months=12), status="completed")
        todo = self.schedule(eq, THIS_MONTH)
        Equipment.objects.filter(pk=eq.pk).update(active_status=False)
        return done, todo

    def assert_history_kept(self, done, todo):
        done.refresh_from_db()
        todo.refresh_from_db()
        self.assertEqual(done.status, "completed")
        self.assertTrue(done.active_status)
        self.assertFalse(todo.active_status)

    def test_remove_inactive_keeps_completed(self):
        done, todo = self._inactive_with_history()
        remove_inactive_equipment_schedules()
        self.assert_history_kept(done, todo)

    def test_cleanup_orphaned_keeps_completed(self):
        done, todo = self._inactive_with_history()
        cleanup_orphaned_calibration_schedules()
        self.assert_history_kept(done, todo)

    def test_validate_keeps_completed_and_one_open(self):
        eq = self.equipment()
        done = self.schedule(eq, THIS_MONTH - relativedelta(months=12), status="completed")
        first = self.schedule(eq, THIS_MONTH, generation_source="signal")
        self.schedule(eq, THIS_MONTH + relativedelta(months=2), generation_source="signal")

        validate_calibration_schedules()

        self.assertTrue(CalibrationSchedule.objects.filter(pk=done.pk, active_status=True).exists())
        self.assertEqual(self.open_months(eq), [first.scheduled_month])


class AutoScheduleTests(CalIntegrityBase):
    def test_broken_chain_resumes_in_its_own_month_slot(self):
        eq = self.equipment()
        last = THIS_MONTH - relativedelta(months=14)
        self.schedule(eq, last, status="completed")

        auto_schedule_unscheduled_equipment()

        # 12-month cycle: due 2 months ago (missed) -> 10 months from now.
        self.assertEqual(self.open_months(eq), [THIS_MONTH + relativedelta(months=10)])

    def test_running_repeatedly_creates_nothing_new(self):
        broken, fresh = self.equipment(), self.equipment()
        self.schedule(broken, THIS_MONTH - relativedelta(months=12), status="completed")
        auto_schedule_unscheduled_equipment()
        before = CalibrationSchedule.objects.count()
        for _ in range(3):
            auto_schedule_unscheduled_equipment()
        self.assertEqual(CalibrationSchedule.objects.count(), before)
        self.assertEqual(len(self.open_months(broken)), 1)
        self.assertEqual(len(self.open_months(fresh)), 1)


class NoSecondOpenScheduleTests(CalIntegrityBase):
    def test_completion_does_not_add_a_second_open_schedule(self):
        eq = self.equipment()
        s = self.schedule(eq, THIS_MONTH)
        # Already has an open schedule elsewhere (e.g. resumed or manual).
        self.schedule(eq, THIS_MONTH + relativedelta(months=5), generation_source="signal")

        s.status = "completed"
        s.save()

        self.assertEqual(len(self.open_months(eq)), 1)
