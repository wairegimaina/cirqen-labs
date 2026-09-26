"""The Site details page sets the name, contacts and logo used everywhere."""
import io
import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from core import branding
from core.models import SiteProfile
from users.models import UserProfile

User = get_user_model()


def _png():
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), "navy").save(buf, "PNG")
    return SimpleUploadedFile("logo.png", buf.getvalue(), content_type="image/png")


class SiteProfileTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media, SITE_NAME="Configured Name",
                                     REPORT_CONTACT={"email": "cfg@example.test"})
        override.enable()
        self.addCleanup(override.disable)
        branding.clear_cache()
        self.addCleanup(branding.clear_cache)

    def _user(self, username, role, **extra):
        user = User.objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **extra})
        return user

    def test_config_values_are_used_until_the_page_is_filled_in(self):
        self.assertEqual(branding.organisation_name(), "Configured Name")
        self.assertIn("cfg@example.test", branding.contact_line())
        self.assertIsNone(branding.logo_path())

    def test_hod_saves_details_and_logo(self):
        self.client.force_login(self._user("site_hod", "HOD"))
        response = self.client.post(reverse("core:site_profile"), {
            "name": "Kenyatta Level 6 Hospital", "address": "Hospital Road\nNairobi",
            "phone": "+254 700 000000", "email": "biomed@hospital.example", "logo": _png()})
        self.assertRedirects(response, reverse("core:site_profile"))
        self.assertEqual(branding.organisation_name(), "Kenyatta Level 6 Hospital")
        self.assertIn("biomed@hospital.example", branding.contact_line())
        self.assertEqual(branding.organisation_address(), "Hospital Road\nNairobi")
        self.assertTrue(branding.logo_path().endswith(".png"))
        page = self.client.get(reverse("core:site_profile"))
        self.assertContains(page, "<title>Site details — Kenyatta Level 6 Hospital</title>", html=False)
        self.assertEqual(self.client.get(reverse("core:site_logo")).status_code, 200)

    def test_only_the_hod_can_change_details(self):
        from Inventory.models import Department
        from workshop.models import Workshop

        ward = Department.objects.create(name="Ward 7", workshop=Workshop.objects.create(name="Biomed", category="maintenance"))
        self.client.force_login(self._user("site_nic", "NIC", department=ward))
        response = self.client.post(reverse("core:site_profile"), {"name": "Someone else"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(SiteProfile.objects.count(), 0)

    def test_certificates_use_the_uploaded_logo(self):
        from CalSoft.models import CalibrationProcedure, CalibrationSession
        from CalSoft.pdf_generators.certificate import BtwelveHospitalCertificateGenerator

        profile = SiteProfile.load()
        profile.logo = _png()
        profile.save()
        user = self._user("cert_tech", "HOD")
        session = CalibrationSession.objects.create(
            procedure=CalibrationProcedure.objects.create(name="NIBP", created_by=user), performed_by=user)
        self.assertEqual(BtwelveHospitalCertificateGenerator(session).logo_path, SiteProfile.load().logo.path)
