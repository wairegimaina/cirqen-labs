"""A group completes, and the next period is created — plan: auto-reschedule.

Auto-rescheduling is group-aware by design: when every member of a planning
group finishes, the whole group moves to the next period together. It had
stopped firing, and the cause was not the reschedule logic but disagreement
about **who is in the group**:

* ``instant_reconciliation`` required the equipment active and ignored the
  schedule flag, so a soft-deleted schedule stayed a member and the group could
  never read as complete;
* ``tasks`` required the schedule active and ignored the equipment flag, so a
  retired device held the group open forever.

Either way the group never completed and the next period was never created.
These tests pin the canonical rule — **both must be active** — and the
behaviour that depends on it.

    ./venv/bin/python manage.py test calSchedules.tests.test_group_completion \
        --settings=Equiper.test_settings
"""

from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from Inventory.models import Department, Equipment, EquipmentDescription
from calSchedules import grouping
from calSchedules.models import CalibrationSchedule
from workshop.models import Workshop


class GroupMembershipRuleTests(TestCase):
    """One definition: the equipment and the schedule must both be active."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="sched", password="x", email="sched@example.test"
        )
        cls.workshop = Workshop.objects.create(name="Biomed")
        cls.department = Department.objects.create(name="Radiology", workshop=cls.workshop)
        cls.description = EquipmentDescription.objects.create(name="Infusion Pump")
        cls.month = date(2027, 3, 1)

    def _equipment(self, serial, *, active=True):
        return Equipment.objects.create(
            department=self.department, workshop=self.workshop,
            description=self.description, model="M", serial_number=serial,
            status="Working", active_status=active,
        )

    def _schedule(self, equipment, *, status="pending", active=True):
        return CalibrationSchedule.objects.create(
            equipment=equipment, workshop=self.workshop, scheduled_month=self.month,
            status=status, active_status=active, planning_logic="department",
            calibration_period=12,
        )

    def _members(self, equipment):
        return grouping.group_members_qs(equipment, self.month, "department")

    def test_active_equipment_with_an_active_schedule_is_a_member(self):
        equipment = self._equipment("SN-A")
        self._schedule(equipment)
        self.assertEqual(self._members(equipment).count(), 1)

    def test_a_retired_device_is_not_a_member(self):
        """It used to hold the group open forever via the tasks path."""
        live = self._equipment("SN-LIVE")
        self._schedule(live)
        retired = self._equipment("SN-RETIRED", active=False)
        self._schedule(retired)

        self.assertEqual(self._members(live).count(), 1)

    def test_a_deleted_schedule_is_not_a_member(self):
        """It used to hold the group open forever via the instant path."""
        live = self._equipment("SN-LIVE2")
        self._schedule(live)
        other = self._equipment("SN-OTHER")
        self._schedule(other, active=False)

        self.assertEqual(self._members(live).count(), 1)

    def test_the_defaults_are_the_canonical_rule(self):
        """A new caller cannot reintroduce the divergence by omission."""
        live = self._equipment("SN-DEF")
        self._schedule(live)
        retired = self._equipment("SN-DEF-RETIRED", active=False)
        self._schedule(retired)

        explicit = grouping.group_members_qs(
            live, self.month, "department",
            require_equipment_active=True, require_schedule_active=True,
        )
        self.assertEqual(self._members(live).count(), explicit.count())

    def test_the_wider_view_is_still_available_when_asked_for(self):
        """Reporting on retired equipment remains possible, but deliberately."""
        live = self._equipment("SN-WIDE")
        self._schedule(live)
        retired = self._equipment("SN-WIDE-RETIRED", active=False)
        self._schedule(retired)

        wide = grouping.group_members_qs(
            live, self.month, "department", require_equipment_active=False,
        )
        self.assertEqual(wide.count(), 2)


class GroupCompletionTests(TestCase):
    """A group with a retired member can still finish."""

    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="sched2", password="x", email="sched2@example.test"
        )
        cls.workshop = Workshop.objects.create(name="Biomed2")
        cls.department = Department.objects.create(name="Theatre", workshop=cls.workshop)
        cls.description = EquipmentDescription.objects.create(name="Ventilator")
        cls.month = date(2027, 6, 1)

    def _equipment(self, serial, *, active=True):
        return Equipment.objects.create(
            department=self.department, workshop=self.workshop,
            description=self.description, model="M", serial_number=serial,
            status="Working", active_status=active,
        )

    def _schedule(self, equipment, *, status="pending", active=True):
        return CalibrationSchedule.objects.create(
            equipment=equipment, workshop=self.workshop, scheduled_month=self.month,
            status=status, active_status=active, planning_logic="department",
            calibration_period=12,
        )

    def _completion(self, equipment):
        from calSchedules.instant_reconciliation.helpers import (
            check_group_completion_status,
        )
        return check_group_completion_status(equipment, self.month, "department")

    def test_a_group_of_live_devices_completes(self):
        a, b = self._equipment("SN-1"), self._equipment("SN-2")
        self._schedule(a, status="completed")
        self._schedule(b, status="completed")

        status = self._completion(a)
        self.assertEqual(status["total"], 2)
        self.assertEqual(status["completed"], 2)
        self.assertTrue(status["all_completed"])

    def test_a_group_is_not_complete_while_one_member_is_pending(self):
        a, b = self._equipment("SN-3"), self._equipment("SN-4")
        self._schedule(a, status="completed")
        self._schedule(b, status="pending")

        self.assertFalse(self._completion(a)["all_completed"])

    def test_a_retired_member_no_longer_blocks_completion(self):
        """The regression. Before the fix this group could never finish."""
        a = self._equipment("SN-5")
        self._schedule(a, status="completed")
        retired = self._equipment("SN-6", active=False)
        self._schedule(retired, status="pending")   # never going to be done

        status = self._completion(a)
        self.assertEqual(status["total"], 1, "the retired device is not a member")
        self.assertTrue(status["all_completed"], "the live device finishing completes the group")

    def test_a_deleted_schedule_no_longer_blocks_completion(self):
        a = self._equipment("SN-7")
        self._schedule(a, status="completed")
        other = self._equipment("SN-8")
        self._schedule(other, status="pending", active=False)   # soft-deleted

        status = self._completion(a)
        self.assertEqual(status["total"], 1)
        self.assertTrue(status["all_completed"])

    def test_both_paths_now_agree_on_the_membership_count(self):
        """The two paths used to answer differently for the same group."""
        from calSchedules.instant_reconciliation.helpers import find_group_members
        from calSchedules.tasks.helpers import _get_group_members

        a = self._equipment("SN-9")
        live_schedule = self._schedule(a, status="completed")
        self._schedule(self._equipment("SN-10", active=False), status="pending")
        self._schedule(self._equipment("SN-11"), status="pending", active=False)

        instant = find_group_members(a, self.month, "department").count()
        batch = _get_group_members(live_schedule, "department").count()
        self.assertEqual(instant, batch, "the two paths must agree on membership")
        self.assertEqual(instant, 1)
