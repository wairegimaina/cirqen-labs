"""Phase 1 data audit and its two repairs."""
from datetime import date
from io import StringIO

from django.core.management import call_command

from ppms.models import PPMSchedule
from scheduling import audit
from scheduling.tests.test_planner import PlanTestBase, d, imported


class AuditTests(PlanTestBase):
    def setUp(self):
        super().setUp()
        self.dup = self.equipment()
        self.broken = self.equipment()
        self.never = self.equipment()
        self.pushed = self.equipment()
        self.done = imported(PPMSchedule, equipment=self.dup, workshop=self.ws,
                                               scheduled_month=d(2026, 2), status="completed")
        imported(PPMSchedule, equipment=self.dup, workshop=self.ws, scheduled_month=d(2026, 8))
        imported(PPMSchedule, equipment=self.dup, workshop=self.ws, scheduled_month=d(2027, 2))
        imported(PPMSchedule, equipment=self.broken, workshop=self.ws, scheduled_month=d(2026, 3),
                                   status="completed")
        imported(PPMSchedule, equipment=self.pushed, workshop=self.ws, scheduled_month=date(2026, 12, 31))

    def test_audit_reports_every_problem(self):
        # Legacy rows: completed before completed_date was recorded.
        PPMSchedule.objects.filter(status="completed").update(completed_date=None)
        r = audit.audit("ppm", today=date(2026, 9, 15))
        self.assertEqual(r.equipment, 4)
        self.assertEqual(r.scheduled, 2)
        self.assertEqual(r.history_only, 1)
        self.assertEqual(r.never_scheduled, 1)
        self.assertEqual([(eq, ms) for eq, _, ms in r.duplicates], [(self.dup.id, [d(2026, 8), d(2027, 2)])])
        self.assertEqual(r.off_first, 1)
        self.assertEqual(r.overdue, 1)
        self.assertEqual(r.completed_without_date, 2)

    def test_retire_duplicates_lists_then_applies(self):
        decisions = audit.retire_duplicates("ppm")
        self.assertEqual(decisions, [(self.dup.serial_number, d(2026, 8), [d(2027, 2)])])
        self.assertEqual(PPMSchedule.open_schedules().filter(equipment=self.dup).count(), 2)

        audit.retire_duplicates("ppm", apply=True)
        self.assertEqual(list(PPMSchedule.open_schedules().filter(equipment=self.dup)
                              .values_list("scheduled_month", flat=True)), [d(2026, 8)])
        self.assertEqual(PPMSchedule.objects.filter(equipment=self.dup).count(), 3)  # retired, not deleted
        self.done.refresh_from_db()
        self.assertTrue(self.done.active_status)

    def test_work_under_way_is_kept_over_an_earlier_schedule(self):
        # Calibration has statuses for work under way (PPM's column can't hold them).
        from calSchedules.models import CalibrationSchedule
        eq = self.equipment()
        imported(CalibrationSchedule, equipment=eq, workshop=self.ws, scheduled_month=d(2026, 11))
        imported(CalibrationSchedule, equipment=eq, workshop=self.ws, scheduled_month=d(2027, 2),
                                           status="in_progress", generation_source="signal")
        decisions = audit.retire_duplicates("calibration")
        self.assertEqual(decisions, [(eq.serial_number, d(2027, 2), [d(2026, 11)])])

    def test_floor_dates(self):
        moved, blocked = audit.floor_dates("ppm")
        self.assertEqual(moved, [(self.pushed.serial_number, date(2026, 12, 31), d(2026, 12))])
        self.assertTrue(PPMSchedule.objects.filter(scheduled_month=date(2026, 12, 31)).exists())
        audit.floor_dates("ppm", apply=True)
        self.assertTrue(PPMSchedule.objects.filter(equipment=self.pushed, scheduled_month=d(2026, 12)).exists())

    def test_floor_dates_never_collides(self):
        imported(PPMSchedule, equipment=self.pushed, workshop=self.ws, scheduled_month=d(2026, 12),
                                   status="completed")
        moved, blocked = audit.floor_dates("ppm", apply=True)
        self.assertEqual(moved, [])
        self.assertEqual(len(blocked), 1)

    def test_command(self):
        out = StringIO()
        call_command("scheduling_audit", "--program", "ppm", stdout=out)
        self.assertIn("More than one open schedule   1", out.getvalue())
        out = StringIO()
        call_command("scheduling_audit", "--program", "ppm", "--retire-duplicates", stdout=out)
        self.assertIn("Would retire", out.getvalue())
        self.assertEqual(PPMSchedule.open_schedules().filter(equipment=self.dup).count(), 2)
