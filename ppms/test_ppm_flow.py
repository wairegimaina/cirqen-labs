"""PPM scheduling flow (IMPROVEMENT_PLAN.md section 6): schedule a device,
push a month, complete; in-charges can view but not change schedules, and a
technician cannot touch another workshop's schedules."""
import datetime

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from Inventory.models import Department, Equipment, EquipmentDescription
from ppms.models import PPMSchedule
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()


class PPMFlowTests(TestCase):
    def setUp(self):
        self.workshop = Workshop.objects.create(name="Biomed")
        self.department = Department.objects.create(name="ICU", workshop=self.workshop)
        self.equipment = Equipment.objects.create(
            description=EquipmentDescription.objects.create(name="Defibrillator"),
            model="D1", serial_number="DEF-1", department=self.department,
            workshop=self.workshop, status="Working",
        )
        self.tech = self._user("ppm_tech", "Tech", workshop=self.workshop, level="Engineer Incharge")
        self.nic = self._user("ppm_nic", "NIC", department=self.department)
        other = Workshop.objects.create(name="Other")
        self.other_tech = self._user("ppm_other", "Tech", workshop=other, level="Engineer Incharge")

    def _user(self, username, role, **profile):
        user = User.objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **profile,
        })
        return user

    def _post(self, user, name, obj_id):
        self.client.force_login(user)
        return self.client.post(reverse(name, args=[obj_id]))

    def test_schedule_push_complete(self):
        # New equipment is scheduled by its workshop's plan straight away.
        schedule = PPMSchedule.open_schedules().get(equipment=self.equipment)
        first_month = schedule.scheduled_month
        self.assertGreater(first_month, datetime.date.today().replace(day=1))
        self.assertEqual(schedule.status, "pending")
        self.assertEqual(schedule.generation_source, "plan")

        self._post(self.tech, "push_schedule", schedule.id)
        schedule.refresh_from_db()
        self.assertEqual(schedule.scheduled_month, first_month + relativedelta(months=1))
        self.assertEqual(schedule.status, "pushed")

        self._post(self.tech, "mark_completed", schedule.id)
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, "completed")
        # ...and the next visit follows from the plan.
        self.assertEqual(PPMSchedule.open_schedules().filter(equipment=self.equipment).count(), 1)

    def test_device_is_not_scheduled_twice(self):
        self._post(self.tech, "schedule_equipment", self.equipment.id)
        self._post(self.tech, "schedule_equipment", self.equipment.id)
        self.assertEqual(PPMSchedule.objects.filter(equipment=self.equipment).count(), 1)

    def test_in_charge_cannot_change_schedules(self):
        schedule = PPMSchedule.objects.create(
            equipment=self.equipment, workshop=self.workshop,
            scheduled_month=datetime.date.today().replace(day=1),
        )
        self._post(self.nic, "mark_completed", schedule.id)
        self._post(self.nic, "push_schedule", schedule.id)
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, "pending")

    def test_other_workshop_cannot_change_schedules(self):
        schedule = PPMSchedule.objects.create(
            equipment=self.equipment, workshop=self.workshop,
            scheduled_month=datetime.date.today().replace(day=1),
        )
        self._post(self.other_tech, "mark_completed", schedule.id)
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, "pending")
