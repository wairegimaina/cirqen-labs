"""A calibration against an out-of-date reference standard is refused.

ISO/IEC 17025 requires the reference to be within its own calibration when it
is used. Before this check a session traced to an expired standard was saved,
approved and certified.
"""
import datetime

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from CalSoft.models import CalibrationParameter, CalibrationProcedure, CalibrationSession, Standard
from CalSoft.view_modules.calibration_helpers import _reference_standard_status
from Inventory.models import Department, Equipment, EquipmentDescription
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()


class ReferenceStandardDueTests(TestCase):
    def setUp(self):
        cal_centre = Workshop.objects.create(name="Cal Centre", category="calibration_center")
        department = Department.objects.create(name="Ward 7", workshop=cal_centre)
        self.equipment = Equipment.objects.create(
            description=EquipmentDescription.objects.create(name="Patient monitor"),
            model="PM", serial_number="PM-1", department=department, workshop=cal_centre, status="Working",
        )
        self.tech = User.objects.create_user(username="cal_tech", password="pw12345!")
        UserProfile.objects.update_or_create(user=self.tech, defaults={
            "role": "Tech", "workshop": cal_centre, "level": "Engineer Incharge",
            "must_change_password": False, "has_uploaded_signature": True,
        })
        self.procedure = CalibrationProcedure.objects.create(name="NIBP", created_by=self.tech)
        CalibrationParameter.objects.create(procedure=self.procedure, name="NIBP", unit="mmHg",
                                            standard_reference="SIM-1", tolerance=3)
        self.today = timezone.localdate()

    def _standard(self, due):
        return Standard.objects.create(
            name="NIBP simulator", model_number="S1", serial_number="SIM-1", manufacturer="Acme",
            certificate_number="CAL-1", calibration_date=due - datetime.timedelta(days=365),
            calibration_due_date=due, created_by=self.tech,
        )

    def _submit(self):
        self.client.force_login(self.tech)
        return self.client.post(reverse("calibration:perform_calibration"), {
            "equipment": str(self.equipment.pk), "procedure": str(self.procedure.pk),
            "actual_temperature": "23", "actual_humidity": "50", "actual_pressure": "101.3",
        })

    def test_expired_standard_blocks_the_session(self):
        self._standard(self.today - datetime.timedelta(days=1))
        response = self._submit()

        self.assertRedirects(response, reverse("schedule:pending_calibrations"), fetch_redirect_response=False)
        self.assertFalse(CalibrationSession.objects.exists())
        text = " ".join(str(m) for m in get_messages(response.wsgi_request))
        self.assertIn("past its calibration due date", text)
        self.assertIn("SIM-1", text)

    def test_status_splits_expired_and_due_soon(self):
        self._standard(self.today + datetime.timedelta(days=10))
        expired, due_soon = _reference_standard_status(self.procedure, self.today)
        self.assertEqual((len(expired), len(due_soon)), (0, 1))

        expired, due_soon = _reference_standard_status(self.procedure, self.today + datetime.timedelta(days=11))
        self.assertEqual((len(expired), len(due_soon)), (1, 0))

    def test_in_date_standard_is_not_flagged(self):
        self._standard(self.today + datetime.timedelta(days=200))
        self.assertEqual(_reference_standard_status(self.procedure, self.today), ([], []))

    def test_unknown_serial_is_not_flagged(self):
        """A parameter naming a standard that is not on record keeps today's behaviour."""
        self.assertEqual(_reference_standard_status(self.procedure, self.today), ([], []))
