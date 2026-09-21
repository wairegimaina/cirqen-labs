"""Targeted regrouping — move a chosen set of schedules.

The only regrouping tool was a global planning-logic flip, which is the wrong
instrument for "this ward is being refurbished, move its devices to May".

These tests pin two things: that a targeted move works, and that it cannot move
anything a full reorganisation would refuse to move. The second matters more —
regrouping reaches into the same data the auto-scheduler owns, so it has to
respect the same protections.

    ./venv/bin/python manage.py test calSchedules.tests.test_regroup \
        --settings=Equiper.test_settings
"""

from datetime import date

from django.test import TestCase

from Inventory.models import Department, Equipment, EquipmentDescription
from calSchedules.models import CalibrationSchedule
from calSchedules.regroup import (
    group_snapshot,
    preview_regroup,
    protection_reason,
    regroup,
)
from workshop.models import Workshop


class RegroupTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.workshop = Workshop.objects.create(name="Biomed")
        cls.dept_a = Department.objects.create(name="Radiology", workshop=cls.workshop)
        cls.dept_b = Department.objects.create(name="Theatre", workshop=cls.workshop)
        cls.desc = EquipmentDescription.objects.create(name="Infusion Pump")
        cls.march = date(2027, 3, 1)
        cls.may = date(2027, 5, 1)

    def _equipment(self, serial, department=None, active=True):
        return Equipment.objects.create(
            department=department or self.dept_a, workshop=self.workshop,
            description=self.desc, model="M", serial_number=serial,
            status="Working", active_status=active,
        )

    def _schedule(self, equipment, month=None, *, status="pending",
                  locked=False, source="manual", active=True):
        return CalibrationSchedule.objects.create(
            equipment=equipment, workshop=self.workshop,
            scheduled_month=month or self.march, status=status,
            active_status=active, planning_logic="department",
            calibration_period=12, is_locked=locked, generation_source=source,
        )


class RegroupMovesTests(RegroupTestCase):
    def test_a_schedule_moves_to_the_target_month(self):
        schedule = self._schedule(self._equipment("SN-1"))
        result = regroup([schedule.id], self.may)

        schedule.refresh_from_db()
        self.assertEqual(schedule.scheduled_month, self.may)
        self.assertEqual(len(result["moved"]), 1)
        self.assertEqual(result["moved"][0]["from"], self.march)
        self.assertEqual(result["moved"][0]["to"], self.may)

    def test_several_schedules_move_together(self):
        schedules = [self._schedule(self._equipment(f"SN-M{i}")) for i in range(3)]
        result = regroup([s.id for s in schedules], self.may)

        self.assertEqual(len(result["moved"]), 3)
        for s in schedules:
            s.refresh_from_db()
            self.assertEqual(s.scheduled_month, self.may)

    def test_any_day_in_the_target_month_is_accepted(self):
        schedule = self._schedule(self._equipment("SN-2"))
        regroup([schedule.id], date(2027, 5, 19))

        schedule.refresh_from_db()
        self.assertEqual(schedule.scheduled_month, self.may)

    def test_a_month_string_is_accepted(self):
        schedule = self._schedule(self._equipment("SN-3"))
        result = regroup([schedule.id], "2027-05")
        self.assertEqual(result["target_month"], self.may)

    def test_equipment_from_different_departments_can_be_grouped(self):
        """The point of a targeted move: cut across the usual grouping."""
        a = self._schedule(self._equipment("SN-4", self.dept_a))
        b = self._schedule(self._equipment("SN-5", self.dept_b))

        regroup([a.id, b.id], self.may)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual(a.scheduled_month, b.scheduled_month)

    def test_a_no_op_move_is_reported_as_skipped_not_moved(self):
        schedule = self._schedule(self._equipment("SN-6"))
        result = regroup([schedule.id], self.march)

        self.assertEqual(result["moved"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("Already in this month", result["skipped"][0]["reason"])

    def test_the_planning_logic_is_left_alone_unless_asked(self):
        """Moving a group and regrouping the estate are different decisions."""
        schedule = self._schedule(self._equipment("SN-7"))
        regroup([schedule.id], self.may)

        schedule.refresh_from_db()
        self.assertEqual(schedule.planning_logic, "department")

    def test_the_planning_logic_can_be_restamped_deliberately(self):
        schedule = self._schedule(self._equipment("SN-8"))
        regroup([schedule.id], self.may, planning_logic="description")

        schedule.refresh_from_db()
        self.assertEqual(schedule.planning_logic, "description")

    def test_a_missing_target_month_is_rejected(self):
        schedule = self._schedule(self._equipment("SN-9"))
        with self.assertRaises(ValueError):
            regroup([schedule.id], None)


class RegroupProtectionTests(RegroupTestCase):
    """Nothing a reorganisation would refuse to move may be moved here."""

    def test_a_completed_schedule_is_not_moved(self):
        schedule = self._schedule(self._equipment("SN-C"), status="completed")
        result = regroup([schedule.id], self.may)

        schedule.refresh_from_db()
        self.assertEqual(schedule.scheduled_month, self.march)
        self.assertIn("Completed", result["skipped"][0]["reason"])

    def test_a_locked_schedule_is_not_moved(self):
        schedule = self._schedule(self._equipment("SN-L"), locked=True)
        result = regroup([schedule.id], self.may)

        schedule.refresh_from_db()
        self.assertEqual(schedule.scheduled_month, self.march)
        self.assertIn("Locked", result["skipped"][0]["reason"])

    def test_an_automatically_created_schedule_is_not_moved(self):
        schedule = self._schedule(self._equipment("SN-S"), source="signal")
        result = regroup([schedule.id], self.may)

        schedule.refresh_from_db()
        self.assertEqual(schedule.scheduled_month, self.march)
        self.assertIn("automatically", result["skipped"][0]["reason"])

    def test_the_reason_names_which_protection_applied(self):
        """An operator needs to know the remedy, which differs by protection."""
        completed = self._schedule(self._equipment("SN-R1"), status="completed")
        locked = self._schedule(self._equipment("SN-R2"), locked=True)

        self.assertIn("Completed", protection_reason(completed))
        self.assertIn("unlock", protection_reason(locked).lower())
        self.assertIsNone(protection_reason(self._schedule(self._equipment("SN-R3"))))

    def test_protected_and_movable_schedules_in_one_request(self):
        """A mixed selection moves what it can and explains the rest."""
        movable = self._schedule(self._equipment("SN-MIX1"))
        locked = self._schedule(self._equipment("SN-MIX2"), locked=True)

        result = regroup([movable.id, locked.id], self.may)

        self.assertEqual(len(result["moved"]), 1)
        self.assertEqual(len(result["skipped"]), 1)
        movable.refresh_from_db(); locked.refresh_from_db()
        self.assertEqual(movable.scheduled_month, self.may)
        self.assertEqual(locked.scheduled_month, self.march)


class RegroupPreviewTests(RegroupTestCase):
    def test_a_preview_changes_nothing(self):
        schedule = self._schedule(self._equipment("SN-P"))
        result = preview_regroup([schedule.id], self.may)

        schedule.refresh_from_db()
        self.assertEqual(schedule.scheduled_month, self.march, "preview must not move anything")
        self.assertTrue(result["dry_run"])

    def test_a_preview_reports_what_would_happen(self):
        movable = self._schedule(self._equipment("SN-P1"))
        locked = self._schedule(self._equipment("SN-P2"), locked=True)

        result = preview_regroup([movable.id, locked.id], self.may)
        self.assertEqual(len(result["moved"]), 1)
        self.assertEqual(len(result["skipped"]), 1)

    def test_the_preview_and_the_real_call_report_the_same_shape(self):
        schedule = self._schedule(self._equipment("SN-P3"))
        preview = preview_regroup([schedule.id], self.may)
        actual = regroup([schedule.id], self.may)

        self.assertEqual(set(preview), set(actual))
        self.assertEqual(len(preview["moved"]), len(actual["moved"]))


class GroupSnapshotTests(RegroupTestCase):
    """What the schedules page shows: the estate as the scheduler sees it."""

    def test_groups_are_listed_with_their_completion(self):
        a = self._equipment("SN-G1", self.dept_a)
        b = self._equipment("SN-G2", self.dept_a)
        self._schedule(a, status="completed")
        self._schedule(b, status="pending")

        groups = group_snapshot(self.march)
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["name"], "Radiology")
        self.assertEqual((group["total"], group["completed"], group["remaining"]), (2, 1, 1))
        self.assertEqual(group["percent"], 50)
        self.assertFalse(group["all_completed"])

    def test_a_finished_group_is_marked_complete(self):
        for serial in ("SN-G3", "SN-G4"):
            self._schedule(self._equipment(serial), status="completed")

        group = group_snapshot(self.march)[0]
        self.assertTrue(group["all_completed"])
        self.assertEqual(group["percent"], 100)

    def test_the_next_period_is_shown(self):
        """The operator should see where the group rolls to when it finishes."""
        self._schedule(self._equipment("SN-G5"))
        group = group_snapshot(self.march)[0]
        self.assertEqual(group["next_period_month"], date(2028, 3, 1))

    def test_departments_are_separate_groups(self):
        self._schedule(self._equipment("SN-G6", self.dept_a))
        self._schedule(self._equipment("SN-G7", self.dept_b))

        self.assertEqual(len(group_snapshot(self.march)), 2)

    def test_description_logic_groups_across_departments(self):
        self._schedule(self._equipment("SN-G8", self.dept_a))
        self._schedule(self._equipment("SN-G9", self.dept_b))

        groups = group_snapshot(self.march, planning_logic="description")
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["name"], "Infusion Pump")

    def test_retired_equipment_is_excluded(self):
        """The same canonical rule the scheduler uses."""
        self._schedule(self._equipment("SN-G10"))
        self._schedule(self._equipment("SN-G11", active=False))

        self.assertEqual(group_snapshot(self.march)[0]["total"], 1)

    def test_a_finished_group_reports_nothing_as_blocked(self):
        """Completed schedules lock automatically; that is not worth flagging."""
        for serial in ("SN-B1", "SN-B2"):
            self._schedule(self._equipment(serial), status="completed", locked=True)

        group = group_snapshot(self.march)[0]
        self.assertTrue(group["all_completed"])
        self.assertEqual(group["blocked"], 0, "a finished group has nothing holding it back")

    def test_an_unfinished_locked_member_is_reported_as_blocked(self):
        self._schedule(self._equipment("SN-B3"))
        self._schedule(self._equipment("SN-B4"), locked=True)

        self.assertEqual(group_snapshot(self.march)[0]["blocked"], 1)

    def test_members_say_whether_they_can_be_moved(self):
        self._schedule(self._equipment("SN-G12"))
        self._schedule(self._equipment("SN-G13"), locked=True)

        members = group_snapshot(self.march)[0]["members"]
        movable = [m for m in members if m["movable"]]
        blocked = [m for m in members if not m["movable"]]
        self.assertEqual(len(movable), 1)
        self.assertEqual(len(blocked), 1)
        self.assertIn("Locked", blocked[0]["reason"])

    def test_groups_closest_to_rolling_over_come_first(self):
        self._schedule(self._equipment("SN-G14", self.dept_a), status="completed")
        self._schedule(self._equipment("SN-G15", self.dept_b))

        groups = group_snapshot(self.march)
        self.assertEqual(groups[0]["name"], "Radiology")
        self.assertEqual(groups[0]["percent"], 100)

    def test_an_empty_month_has_no_groups(self):
        self.assertEqual(group_snapshot(date(2030, 1, 1)), [])


class AlignmentSuppressionTests(RegroupTestCase):
    """A deliberate move is not undone by the group aligner.

    ``instant_grouping_alignment`` enforces "equipment never jump between
    months within a group", which is right for accidental drift and fatal for a
    deliberate regroup: moving the first member snapped it back to where the
    rest still were, so the move could never get started. This is the behaviour
    that made regrouping impossible.
    """

    def test_a_whole_group_moves_together(self):
        schedules = [self._schedule(self._equipment(f"SN-A{i}")) for i in range(4)]
        regroup([s.id for s in schedules], self.may)

        for s in schedules:
            s.refresh_from_db()
            self.assertEqual(s.scheduled_month, self.may)

    def test_the_aligner_still_guards_accidental_drift(self):
        """Suppression is scoped to the regroup; normal saves realign."""
        from calSchedules.instant_reconciliation.helpers import alignment_suppressed

        self.assertFalse(alignment_suppressed(), "not suppressed before")
        regroup([self._schedule(self._equipment("SN-B1")).id], self.may)
        self.assertFalse(alignment_suppressed(), "released afterwards")

    def test_suppression_is_released_even_if_the_move_raises(self):
        from calSchedules.instant_reconciliation.helpers import (
            alignment_suppressed,
            suppress_alignment,
        )

        with self.assertRaises(RuntimeError):
            with suppress_alignment():
                self.assertTrue(alignment_suppressed())
                raise RuntimeError("boom")
        self.assertFalse(alignment_suppressed())

    def test_nested_suppression_restores_the_outer_state(self):
        from calSchedules.instant_reconciliation.helpers import (
            alignment_suppressed,
            suppress_alignment,
        )

        with suppress_alignment():
            with suppress_alignment():
                self.assertTrue(alignment_suppressed())
            self.assertTrue(alignment_suppressed(), "inner exit must not clear the outer")
        self.assertFalse(alignment_suppressed())
