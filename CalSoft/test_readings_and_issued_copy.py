"""ISO/IEC 17025 record keeping: enough readings, and issued certificates frozen."""
import shutil
import tempfile
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase, override_settings
from django.urls import reverse

from CalSoft.issued import CertificateTampered, fingerprint, issued_pdf
from CalSoft.models import (
    CalibrationParameter, CalibrationProcedure, CalibrationSession, IssuedCertificate, SetValue,
)
from Inventory.models import Department, Equipment, EquipmentDescription
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()


class MinimumReadingsTests(TestCase):
    def setUp(self):
        centre = Workshop.objects.create(name="Cal Centre", category="calibration_center")
        department = Department.objects.create(name="ICU", workshop=centre)
        self.equipment = Equipment.objects.create(
            description=EquipmentDescription.objects.create(name="Patient monitor"),
            model="PM", serial_number="PM-9", department=department, workshop=centre, status="Working")
        self.tech = User.objects.create_user(username="reading_tech", password="pw12345!")
        UserProfile.objects.update_or_create(user=self.tech, defaults={
            "role": "Tech", "workshop": centre, "level": "Engineer Incharge",
            "must_change_password": False, "has_uploaded_signature": True})
        self.procedure = CalibrationProcedure.objects.create(name="NIBP", created_by=self.tech)
        self.parameter = CalibrationParameter.objects.create(
            procedure=self.procedure, name="Systolic", unit="mmHg", tolerance=3, num_readings=5)
        self.point = SetValue.objects.create(parameter=self.parameter, value=Decimal("120"))

    def _submit(self, readings):
        data = {"equipment": str(self.equipment.pk), "procedure": str(self.procedure.pk),
                "actual_temperature": "23", "actual_humidity": "50", "actual_pressure": "101.3",
                f"resolution_{self.parameter.id}": "1"}
        for i, value in enumerate(readings, 1):
            data[f"reading_{self.parameter.id}_null_{self.point.id}_{i}"] = value
        self.client.force_login(self.tech)
        return self.client.post(reverse("calibration:perform_calibration"), data)

    def test_too_few_readings_saves_nothing(self):
        response = self._submit(["120", "121", "119"])
        self.assertRedirects(response, reverse("schedule:pending_calibrations"), fetch_redirect_response=False)
        self.assertFalse(CalibrationSession.objects.exists())
        text = " ".join(str(m) for m in get_messages(response.wsgi_request))
        self.assertIn("too few readings", text)
        self.assertIn("Systolic at 120", text)
        self.assertIn("3 of 5", text)

    def test_a_non_numeric_reading_saves_nothing(self):
        self._submit(["120", "121", "x", "119", "120"])
        self.assertFalse(CalibrationSession.objects.exists())

    def test_enough_readings_are_saved(self):
        self._submit(["120", "121", "119", "120", "120"])
        session = CalibrationSession.objects.get()
        self.assertEqual(len(session.readings.get().get_readings_list()), 5)

    def test_a_parameter_cannot_ask_for_more_readings_than_can_be_stored(self):
        from CalSoft.forms import CalibrationParameterForm
        form = CalibrationParameterForm(data={"name": "X", "unit": "V", "num_readings": 12, "tolerance": 1})
        self.assertFalse(form.is_valid())
        self.assertIn("num_readings", form.errors)


class IssuedCopyTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.user = User.objects.create_user(username="issuer", password="x")
        procedure = CalibrationProcedure.objects.create(name="NIBP", created_by=self.user)
        self.session = CalibrationSession.objects.create(
            procedure=procedure, performed_by=self.user, status="approved", certificate_number="BNH-0200")

    def test_first_issue_is_stored_and_later_builds_are_not_used(self):
        first = issued_pdf(self.session, self.user, lambda: b"%PDF version one")
        record = IssuedCertificate.objects.get()
        self.assertEqual(record.sha256, fingerprint(b"%PDF version one"))
        build = mock.Mock(return_value=b"%PDF version two")
        self.assertEqual(issued_pdf(self.session, self.user, build), first)
        build.assert_not_called()

    def test_an_altered_stored_copy_is_refused(self):
        issued_pdf(self.session, self.user, lambda: b"%PDF original")
        record = IssuedCertificate.objects.get()
        with open(record.pdf.path, "wb") as fh:
            fh.write(b"%PDF edited")
        with self.assertRaises(CertificateTampered):
            issued_pdf(self.session, self.user, lambda: b"%PDF regenerated")

    def test_documents_without_an_issued_number_are_not_stored(self):
        for status, number in (("approved_pending_certificate", None), ("rejected", "BNH-0300"),
                               ("approved", str(self.session.id))):
            self.session.status, self.session.certificate_number = status, number
            self.assertEqual(issued_pdf(self.session, self.user, lambda: b"%PDF draft"), b"%PDF draft")
        self.assertFalse(IssuedCertificate.objects.exists())

    def test_a_reissue_under_a_new_number_is_stored_separately(self):
        issued_pdf(self.session, self.user, lambda: b"%PDF as 0200")
        self.session.certificate_number = "BNH-0201"
        issued_pdf(self.session, self.user, lambda: b"%PDF as 0201")
        self.assertEqual(
            sorted(IssuedCertificate.objects.values_list("certificate_number", flat=True)),
            ["BNH-0200", "BNH-0201"])
