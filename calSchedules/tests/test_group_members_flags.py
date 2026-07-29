"""Regression guard: group_members_qs must reproduce BOTH legacy filter styles.

The old `tasks._get_group_members` and `instant.find_group_members` diverged
(schedule-active vs equipment-active). The consolidation preserves both via
explicit flags — this test pins that so the divergence can't be accidentally
"unified" without a deliberate decision (see SCHEDULING_NOTES.md).
"""
from datetime import date

from django.test import TestCase

from Inventory.models import Department, Equipment, EquipmentDescription
from workshop.models import Workshop
from calSchedules.models import CalibrationSchedule
from calSchedules import grouping


class GroupMembersFlagTests(TestCase):
    def setUp(self):
        self.workshop = Workshop.objects.create(name="WS")
        self.dept = Department.objects.create(name="Lab", workshop=self.workshop)
        self.desc = EquipmentDescription.objects.create(name="Scale")
        self.month = date.today().replace(day=1)

        # eqA: equipment active, schedule active
        self.eqA = self._equipment("A", active=True)
        self.sA = self._schedule(self.eqA, active=True)
        # eqB: equipment INACTIVE, schedule active
        self.eqB = self._equipment("B", active=False)
        self.sB = self._schedule(self.eqB, active=True)
        # eqC: equipment active, schedule INACTIVE
        self.eqC = self._equipment("C", active=True)
        self.sC = self._schedule(self.eqC, active=False)

    def _equipment(self, serial, *, active):
        return Equipment.objects.create(
            department=self.dept, workshop=self.workshop, description=self.desc,
            model="M", serial_number=f"SN-{serial}", status="Working", active_status=active,
        )

    def _schedule(self, equipment, *, active):
        return CalibrationSchedule.objects.create(
            equipment=equipment, workshop=self.workshop, scheduled_month=self.month,
            status="pending", calibration_period=12, planning_logic="department",
            active_status=active,
        )

    def test_tasks_style_filters_on_schedule_active(self):
        # require_schedule_active=True, require_equipment_active=False
        ids = set(
            grouping.group_members_qs(
                self.eqA, self.month, "department",
                require_schedule_active=True, require_equipment_active=False,
            ).values_list("id", flat=True)
        )
        # includes sA and sB (schedules active); excludes sC (schedule inactive)
        self.assertEqual(ids, {self.sA.id, self.sB.id})

    def test_instant_style_filters_on_equipment_active(self):
        # require_equipment_active=True, require_schedule_active=False
        ids = set(
            grouping.group_members_qs(
                self.eqA, self.month, "department",
                require_equipment_active=True, require_schedule_active=False,
            ).values_list("id", flat=True)
        )
        # includes sA and sC (equipment active); excludes sB (equipment inactive)
        self.assertEqual(ids, {self.sA.id, self.sC.id})

    def test_status_filter_is_optional(self):
        self.sA.status = "completed"
        self.sA.save(update_fields=["status"])
        pending_only = set(
            grouping.group_members_qs(
                self.eqA, self.month, "department",
                require_schedule_active=True, require_equipment_active=False,
                statuses=["pending"],
            ).values_list("id", flat=True)
        )
        self.assertEqual(pending_only, {self.sB.id})  # sA now completed, sC schedule-inactive
