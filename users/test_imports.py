"""Bulk user import from Excel (users.imports)."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.tests.excel_helpers import ExcelImportTestMixin, read_response_workbook
from Inventory.models import Department
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()
HEADER = ["First Name", "Last Name", "Email", "Role", "Workshop", "Level", "Department", "Phone"]


@override_settings(EMAIL_HOST_USER="")
class UserImportTests(ExcelImportTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.biomed = Workshop.objects.create(name="Biomed", category="maintenance")
        self.renal = Department.objects.create(name="Renal", workshop=self.biomed)
        self.hod = self.make_user("imp_hod", "HOD")
        self.client.force_login(self.hod)
        self.url = reverse("upload_users_excel")
        self.sheet = {"Users": [HEADER,
                                ["Amina", "Otieno", "amina@hospital.example", "Tech", "Biomed", "Engineer", "", "0700000001"],
                                ["Brian", "Kamau", "brian@hospital.example", "NIC", "", "", "Renal", ""],
                                ["Cara", "", "not-an-email", "Doctor", "", "", "", ""]]}

    def test_preview_saves_nothing_and_reports_every_row(self):
        with mock.patch("users.utils.UserManagementUtils.send_welcome_email") as email:
            data = self.post_workbook(self.url, self.sheet).json()
        self.assertEqual(data["counts"], {"create": 2, "skip": 0, "error": 1})
        self.assertFalse(User.objects.filter(email="amina@hospital.example").exists())
        email.assert_not_called()
        errors = " ".join(self.rows_by_number(data)[4]["messages"])
        for text in ("Last Name is required", "not a valid email", "Role 'Doctor'"):
            self.assertIn(text, errors)

    def test_commit_creates_users_with_first_login_setup_and_emails_them(self):
        with mock.patch("users.utils.UserManagementUtils.send_welcome_email") as email, \
                self.captureOnCommitCallbacks(execute=True):
            self.post_workbook(self.url, self.sheet, commit=True)
        amina = UserProfile.objects.get(user__email="amina@hospital.example")
        self.assertEqual((amina.role, amina.workshop, amina.level, amina.phone_number),
                         ("Tech", self.biomed, "Engineer", "0700000001"))
        self.assertTrue(amina.must_change_password and not amina.has_uploaded_signature)
        self.assertEqual(UserProfile.objects.get(user__email="brian@hospital.example").department, self.renal)
        self.assertEqual(email.call_count, 2)
        self.assertTrue(amina.user.check_password(email.call_args_list[0].args[1]))

    def test_registered_emails_are_skipped(self):
        User.objects.create_user(username="existing", email="AMINA@hospital.example", password="x")
        data = self.post_workbook(self.url, self.sheet).json()
        self.assertEqual(data["counts"]["skip"], 1)

    def test_only_the_hod_may_import(self):
        self.client.force_login(self.make_user("imp_nic", "NIC", department=self.renal))
        self.assertEqual(self.post_workbook(self.url, self.sheet).status_code, 403)

    def test_template_offers_the_roles_and_workshops(self):
        wb = read_response_workbook(self.client.get(reverse("user_import_template")))
        self.assertEqual([c.value for c in wb["Users"][1]], HEADER)
        reference = [c.value for row in wb["Reference"].iter_rows() for c in row]
        self.assertIn("Biomed", reference)
        self.assertIn("Engineer Incharge", reference)
