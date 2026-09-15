"""End-to-end test of the cert -> complete -> reschedule cycle.

Pins the PPM auto-reschedule behaviour that the grouping consolidation must not
break: assigning a certificate number to an approved CalibrationSession should
mark its schedule completed + locked and create the next schedule one period
later, with the same planning_logic. Sync side-effects are patched to no-op so
the test never touches Redis/Kafka/HQ.
"""
from datetime import date
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase

from Inventory.models import Department, Equipment, EquipmentDescription
from workshop.models import Workshop
from calSchedules.models import CalibrationSchedule
from CalSoft.models import CalibrationProcedure, CalibrationSession

User = get_user_model()

# trigger_sync_for_changes is imported by name into reconciliation's callers, so
# it must be patched in every module that looks it up, not on the package.
SYNC_TARGETS = (
    "calSchedules.reconciliation.trigger_sync_for_changes",
    "calSchedules.instant_reconciliation.helpers.trigger_sync_for_changes",
    "calSchedules.instant_reconciliation.alignment.trigger_sync_for_changes",
    "calSchedules.instant_reconciliation.signals.trigger_sync_for_changes",
)


class NoSyncMixin:
    def setUp(self):
        super().setUp()
        for target in SYNC_TARGETS:
            patcher = patch(target, return_value=0)
            patcher.start()
            self.addCleanup(patcher.stop)


class CertCompleteRescheduleCycleTests(NoSyncMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="tech", password="pw12345!")
        self.workshop = Workshop.objects.create(name="WS")
        self.dept = Department.objects.create(name="Lab", workshop=self.workshop)
        self.desc = EquipmentDescription.objects.create(name="Scale")
        self.equipment = Equipment.objects.create(
            department=self.dept, workshop=self.workshop, description=self.desc,
            model="M", serial_number="SN-CYCLE-1", status="Working", active_status=True,
        )
        self.month = date.today().replace(day=1)
        self.schedule = CalibrationSchedule.objects.create(
            equipment=self.equipment, workshop=self.workshop, scheduled_month=self.month,
            status="pending", calibration_period=12, planning_logic="department",
        )
        self.procedure = CalibrationProcedure.objects.create(name="Proc", created_by=self.user)

    def _approve_with_certificate(self):
        return CalibrationSession.objects.create(
            schedule=self.schedule,
            procedure=self.procedure,
            performed_by=self.user,
            status="approved",
            certificate_number="BNH-0001",
        )

    def test_certificate_completes_and_locks_schedule(self):
        session = self._approve_with_certificate()
        session.refresh_from_db()
        self.schedule.refresh_from_db()
        self.assertEqual(self.schedule.status, "completed")
        self.assertTrue(self.schedule.is_locked)
        # completed_date records the ACTUAL calibration date (the session timestamp),
        # NOT the scheduled month. Compare against the session's own timestamp to
        # avoid UTC-vs-local date-boundary flakiness.
        self.assertEqual(self.schedule.completed_date, session.timestamp.date())

    def test_next_schedule_created_one_period_later(self):
        self._approve_with_certificate()

        expected_month = self.month + relativedelta(months=12)
        nxt = CalibrationSchedule.objects.filter(
            equipment=self.equipment, scheduled_month=expected_month,
        ).first()

        self.assertIsNotNone(nxt, "next-period schedule should have been auto-created")
        self.assertEqual(nxt.status, "pending")
        self.assertEqual(nxt.planning_logic, "department")
        self.assertEqual(nxt.calibration_period, 12)
        # generation_source marks it as PPM-auto-created (protected source)
        self.assertEqual(nxt.generation_source, "signal")

    def test_certificate_number_is_sequential_and_pure(self):
        # generate_certificate_number now delegates to the pure helper
        self.assertEqual(CalibrationSession.generate_certificate_number(), "BNH-0001")
        self._approve_with_certificate()  # persists BNH-0001
        self.assertEqual(CalibrationSession.generate_certificate_number(), "BNH-0002")


class DateBasedGroupsByDepartmentTests(NoSyncMixin, TestCase):
    """Proves the vocabulary fix: a schedule stored as 'date_based' is grouped by
    DEPARTMENT (not description) during the PPM auto-reschedule cycle.

    Two equipment in the SAME department but DIFFERENT descriptions, scheduled the
    same month, planning_logic='date_based'. Completing only one must NOT trigger a
    reschedule (the department group is not complete). Before the fix, each would
    be its own description-group and reschedule prematurely.
    """
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(username="tech2", password="pw12345!")
        self.workshop = Workshop.objects.create(name="WS")
        self.dept = Department.objects.create(name="Lab", workshop=self.workshop)
        self.descX = EquipmentDescription.objects.create(name="Scale")
        self.descY = EquipmentDescription.objects.create(name="Thermometer")
        self.month = date.today().replace(day=1)
        self.procedure = CalibrationProcedure.objects.create(name="Proc", created_by=self.user)

        self.eqA = self._equipment(self.descX, "A")
        self.eqB = self._equipment(self.descY, "B")
        self.sA = self._schedule(self.eqA)
        self.sB = self._schedule(self.eqB)

    def _equipment(self, desc, serial):
        return Equipment.objects.create(
            department=self.dept, workshop=self.workshop, description=desc,
            model="M", serial_number=f"SN-DB-{serial}", status="Working", active_status=True,
        )

    def _schedule(self, equipment):
        return CalibrationSchedule.objects.create(
            equipment=equipment, workshop=self.workshop, scheduled_month=self.month,
            status="pending", calibration_period=12, planning_logic="date_based",
        )

    def _complete(self, schedule, cert):
        return CalibrationSession.objects.create(
            schedule=schedule, procedure=self.procedure, performed_by=self.user,
            status="approved", certificate_number=cert,
        )

    def _next_exists(self, equipment):
        nxt = self.month + relativedelta(months=12)
        return CalibrationSchedule.objects.filter(
            equipment=equipment, scheduled_month=nxt, generation_source="signal",
        ).exists()

    def test_partial_department_completion_does_not_reschedule(self):
        self._complete(self.sA, "BNH-0001")
        # department group (2 members) is only 1/2 complete → no next schedule yet
        self.assertFalse(self._next_exists(self.eqA))
        self.assertFalse(self._next_exists(self.eqB))

    def test_full_department_completion_reschedules_whole_group(self):
        self._complete(self.sA, "BNH-0001")
        self._complete(self.sB, "BNH-0002")
        # now the whole department group is complete → both roll to next period
        self.assertTrue(self._next_exists(self.eqA))
        self.assertTrue(self._next_exists(self.eqB))
