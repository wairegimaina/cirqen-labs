"""Job card approval flow, end to end (IMPROVEMENT_PLAN.md section 6).

A technician raises a job card against a device, the department's in-charge
approves or declines it, and a PPM job card closes its PPM schedule. These are
the steps that carry compliance evidence (signatures, dates), so they are
exercised through the real views and forms.
"""
import datetime

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from Inventory.models import Department, Equipment, EquipmentDescription
from jobcard.models import jobcard
from ppms.models import PPMSchedule
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()

SIGNATURE = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class JobCardFlowTests(TestCase):
    def setUp(self):
        self.workshop = Workshop.objects.create(name="Biomed", category="maintenance")
        self.department = Department.objects.create(name="ICU", workshop=self.workshop)
        self.other_department = Department.objects.create(name="Theatre", workshop=self.workshop)
        self.equipment = Equipment.objects.create(
            description=EquipmentDescription.objects.create(name="Ventilator"),
            model="V1", serial_number="VENT-1", department=self.department,
            workshop=self.workshop, status="Working",
        )
        self.tech = self._user("jc_tech", "Tech", workshop=self.workshop, level="Engineer")
        self.nic = self._user("jc_nic", "NIC", department=self.department)
        self.other_nic = self._user("jc_nic2", "NIC", department=self.other_department)

    def _user(self, username, role, **profile):
        user = User.objects.create_user(username=username, password="pw12345!",
                                        first_name=username.title(), last_name="User")
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **profile,
        })
        return user

    def raise_job_card(self, action="Repair", **extra):
        self.client.force_login(self.tech)
        data = {
            "priority_level": "High", "department": self.department.id,
            "equipment": self.equipment.id, "job_description": "Alarm failing",
            "action_taken": action, "time_started": "09:00", "time_completed": "10:30",
            "signature_data": SIGNATURE, "spare_parts_data": "[]",
            "labor_cost": "0.00", "additional_costs": "0.00", **extra,
        }
        self.client.post(reverse("jobcard:create_job_card"), data)
        return jobcard.objects.filter(equipment=self.equipment).order_by("-date_issued").first()

    def decide(self, card, nurse, decision, **extra):
        self.client.force_login(nurse)
        return self.client.post(reverse("jobcard:create_job_card"), {
            "jobcard_id": card.id, "nurse_name": nurse.get_full_name(),
            "nurse_signature_data": SIGNATURE, decision: "1", **extra,
        })

    def test_technician_raises_a_card_that_waits_for_approval(self):
        card = self.raise_job_card()
        self.assertIsNotNone(card)
        self.assertEqual(card.status, "Waiting Approval")
        self.assertEqual(card.department, self.department)
        self.assertTrue(card.tech_signature)

    def test_in_charge_approval_records_who_and_when(self):
        card = self.raise_job_card()
        self.decide(card, self.nic, "approve")
        card.refresh_from_db()
        self.assertEqual(card.status, "Approved")
        self.assertEqual(card.verified_by_nurse, self.nic)
        self.assertIsNotNone(card.nurse_signed_date)
        self.assertTrue(card.verified_signature)

    def test_decline_keeps_the_reason(self):
        card = self.raise_job_card()
        self.decide(card, self.nic, "decline", decline_reason="Device still alarming")
        card.refresh_from_db()
        self.assertEqual(card.status, "Declined")
        self.assertEqual(card.decline_reason, "Device still alarming")

    def test_in_charge_of_another_department_cannot_approve(self):
        card = self.raise_job_card()
        self.decide(card, self.other_nic, "approve")
        card.refresh_from_db()
        self.assertEqual(card.status, "Waiting Approval")

    def test_approving_a_ppm_card_completes_its_schedule(self):
        schedule = PPMSchedule.objects.create(
            equipment=self.equipment, workshop=self.workshop,
            scheduled_month=datetime.date.today().replace(day=1),
        )
        card = self.raise_job_card(action="PPM", ppm_schedule_id=schedule.id)
        self.assertEqual(card.related_ppm_schedule, schedule)
        self.decide(card, self.nic, "approve")
        schedule.refresh_from_db()
        self.assertEqual(schedule.status, "completed")

    def test_card_cannot_name_equipment_outside_the_selected_department(self):
        foreign = Workshop.objects.create(name="Other")
        foreign_eq = Equipment.objects.create(
            description=self.equipment.description, model="X", serial_number="FOREIGN-1",
            department=Department.objects.create(name="Lab", workshop=foreign),
            workshop=foreign, status="Working",
        )
        self.client.force_login(self.tech)
        self.client.post(reverse("jobcard:create_job_card"), {
            "priority_level": "High", "department": self.department.id, "equipment": foreign_eq.id,
            "job_description": "x", "action_taken": "Repair", "time_started": "09:00",
            "signature_data": SIGNATURE, "spare_parts_data": "[]",
        })
        self.assertFalse(jobcard.objects.filter(equipment=foreign_eq).exists())
