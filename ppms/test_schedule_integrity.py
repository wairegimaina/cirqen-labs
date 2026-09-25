"""PPM schedule integrity (scheduling redesign, phase 0).

Pins the fixes that stop history being lost and equipment going unscheduled:
groups with several members advance, completed schedules are immutable,
overdue schedules stay in their due month, cleanup retires rather than
deletes, and the daily auto-scheduler picks up equipment whose chain broke.
"""
from datetime import date

from dateutil.relativedelta import relativedelta
from django.core.exceptions import ValidationError
from django.test import TestCase

from Inventory.models import Department, Equipment, EquipmentDescription
from ppms.models import PPMSchedule
from ppms.tasks import (
    auto_schedule_unscheduled_equipment,
    check_and_push_overdue_ppms,
    periodic_cleanup_inactive_schedules,
    validate_ppm_schedules,
)
from workshop.models import Workshop

THIS_MONTH = date.today().replace(day=1)


class PPMIntegrityBase(TestCase):
    def setUp(self):
        self.workshop = Workshop.objects.create(name="Biomed")
        self.icu = Department.objects.create(name="ICU", workshop=self.workshop)
        self.desc = EquipmentDescription.objects.create(name="Infusion Pump")
        self.serial = 0

    def equipment(self, **extra):
        self.serial += 1
        eq = Equipment.objects.create(
            description=self.desc, model="M", serial_number=f"PPM-{self.serial}",
            department=self.icu, workshop=self.workshop, status="Working", **extra,
        )
        # New equipment gets a next-month schedule from a signal; tests build
        # their own history, so start clean.
        PPMSchedule.objects.filter(equipment=eq).delete()
        return eq

    def schedule(self, eq, month, status="pending", **extra):
        return PPMSchedule.objects.create(
            equipment=eq, workshop=self.workshop, scheduled_month=month, status=status,
            maintenance_period=6, planning_logic="department", **extra,
        )

    def open_months(self, eq):
        return list(
            PPMSchedule.open_schedules().filter(equipment=eq)
            .order_by("scheduled_month").values_list("scheduled_month", flat=True)
        )


class GroupAdvanceTests(PPMIntegrityBase):
    def test_every_member_of_a_group_gets_its_next_schedule(self):
        month = THIS_MONTH
        devices = [self.equipment() for _ in range(3)]
        schedules = [self.schedule(eq, month) for eq in devices]

        # Completed one at a time, as job cards do. The first completion used
        # to mark the group as processed, so the last one never advanced it.
        for s in schedules:
            s.status = "completed"
            s.save()

        nxt = month + relativedelta(months=6)
        for eq in devices:
            self.assertEqual(self.open_months(eq), [nxt])

    def test_group_waits_until_all_members_are_done(self):
        a, b = self.equipment(), self.equipment()
        sa, _ = self.schedule(a, THIS_MONTH), self.schedule(b, THIS_MONTH)
        sa.status = "completed"
        sa.save()
        self.assertEqual(self.open_months(a), [])

    def test_member_already_scheduled_elsewhere_is_not_given_a_second(self):
        eq = self.equipment()
        s = self.schedule(eq, THIS_MONTH)
        s.status = "completed"
        s.save()
        # Complete again (a re-save): still exactly one open schedule.
        s.save()
        self.assertEqual(len(self.open_months(eq)), 1)


class CompletedIsImmutableTests(PPMIntegrityBase):
    def test_completed_month_cannot_change(self):
        s = self.schedule(self.equipment(), THIS_MONTH, status="completed")
        s.scheduled_month = THIS_MONTH + relativedelta(months=1)
        with self.assertRaises(ValidationError):
            s.save()

    def test_completed_status_cannot_regress(self):
        s = self.schedule(self.equipment(), THIS_MONTH, status="completed")
        s.status = "pending"
        with self.assertRaises(ValidationError):
            s.save()

    def test_completed_can_still_be_locked(self):
        s = self.schedule(self.equipment(), THIS_MONTH, status="completed")
        s.is_locked = True
        s.save(update_fields=["is_locked", "needs_sync"])


class OverdueTests(PPMIntegrityBase):
    def test_overdue_schedule_stays_in_its_due_month(self):
        due = THIS_MONTH - relativedelta(months=2)
        s = self.schedule(self.equipment(), due)
        check_and_push_overdue_ppms()
        s.refresh_from_db()
        self.assertEqual(s.scheduled_month, due)
        self.assertEqual(s.status, "pending")
        self.assertTrue(s.is_overdue)


class CleanupKeepsHistoryTests(PPMIntegrityBase):
    def test_inactive_equipment_keeps_completed_history(self):
        eq = self.equipment()
        done = self.schedule(eq, THIS_MONTH - relativedelta(months=6), status="completed")
        todo = self.schedule(eq, THIS_MONTH)
        Equipment.objects.filter(pk=eq.pk).update(active_status=False)

        periodic_cleanup_inactive_schedules()

        done.refresh_from_db()
        todo.refresh_from_db()
        self.assertTrue(done.active_status)
        self.assertEqual(done.status, "completed")
        self.assertFalse(todo.active_status)

    def test_validate_retires_duplicate_open_schedules_only(self):
        eq = self.equipment()
        done = self.schedule(eq, THIS_MONTH - relativedelta(months=6), status="completed")
        first = self.schedule(eq, THIS_MONTH)
        second = self.schedule(eq, THIS_MONTH + relativedelta(months=1))

        validate_ppm_schedules(str(self.workshop.id))

        self.assertTrue(PPMSchedule.objects.filter(pk=done.pk, active_status=True).exists())
        self.assertEqual(self.open_months(eq), [first.scheduled_month])
        self.assertTrue(PPMSchedule.objects.filter(pk=second.pk).exists())


class AutoScheduleTests(PPMIntegrityBase):
    def test_broken_chain_resumes_in_its_own_month_slot(self):
        eq = self.equipment()
        # Last done 13 months ago on a 6-month cycle: 7 months ago was missed,
        # so the chain resumes at 13 - 12 = 1 month from now.
        last = THIS_MONTH - relativedelta(months=13)
        self.schedule(eq, last, status="completed")

        auto_schedule_unscheduled_equipment()

        self.assertEqual(self.open_months(eq), [last + relativedelta(months=18)])
        self.assertEqual(self.open_months(eq), [THIS_MONTH + relativedelta(months=5)])

    def test_never_scheduled_equipment_is_scheduled(self):
        eq = self.equipment()
        result = auto_schedule_unscheduled_equipment()
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(len(self.open_months(eq)), 1, result)

    def test_equipment_with_only_retired_rows_is_scheduled(self):
        eq = self.equipment()
        self.schedule(eq, THIS_MONTH - relativedelta(months=3), active_status=False)
        auto_schedule_unscheduled_equipment()
        self.assertEqual(len(self.open_months(eq)), 1)

    def test_running_repeatedly_creates_nothing_new(self):
        broken, fresh = self.equipment(), self.equipment()
        self.schedule(broken, THIS_MONTH - relativedelta(months=6), status="completed")
        auto_schedule_unscheduled_equipment()
        before = PPMSchedule.objects.count()
        for _ in range(3):
            auto_schedule_unscheduled_equipment()
        self.assertEqual(PPMSchedule.objects.count(), before)
        self.assertEqual(len(self.open_months(broken)), 1)
        self.assertEqual(len(self.open_months(fresh)), 1)


class SyncInitializationViewTests(PPMIntegrityBase):
    def test_sync_initialization_schedules_equipment(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from users.models import UserProfile

        eq = self.equipment()
        user = get_user_model().objects.create_user(
            username="ppm_staff", password="pw12345!", is_staff=True
        )
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": "Tech", "workshop": self.workshop, "level": "Engineer Incharge",
            "must_change_password": False, "has_uploaded_signature": True,
        })
        self.client.force_login(user)
        self.client.post(reverse("trigger_sync_initialization"), {
            "planning_logic": "department", "maintenance_period": "6",
            "base_month": str(THIS_MONTH.month), "base_year": str(THIS_MONTH.year),
            "preserve_existing": "true",
        })
        self.assertEqual(len(self.open_months(eq)), 1)
