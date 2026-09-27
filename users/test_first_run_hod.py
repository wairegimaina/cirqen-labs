"""The desktop's first-run setup makes a per-install HOD, never a fixed login."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from users.models import UserProfile


class FirstRunHodTests(TestCase):
    def setUp(self):
        try:
            import bulider_tools.database as setup_module
        except Exception as exc:  # PySide6 missing on this machine
            self.skipTest(f"desktop setup module unavailable: {exc}")
        self.module = setup_module
        self.tmp = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(setup_module, "DATA_PATH", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_setup(self):
        stub = SimpleNamespace()
        assert self.module.FirstRunSetup._create_hod_user(stub)
        return stub

    def test_each_install_gets_its_own_one_time_password(self):
        first = self.run_setup().first_login
        user = get_user_model().objects.get(username="hod")
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password(first["password"]))
        self.assertFalse(user.check_password("ChangeMe123!"))
        self.assertTrue(UserProfile.objects.get(user=user).must_change_password)
        note = (self.tmp / "first_login.txt").read_text()
        self.assertIn(first["password"], note)
        self.assertEqual((self.tmp / "first_login.txt").stat().st_mode & 0o777, 0o600)

    def test_an_existing_hod_is_left_alone(self):
        self.run_setup()
        again = self.run_setup()
        self.assertIsNone(again.first_login)
        self.assertEqual(UserProfile.objects.filter(role="HOD").count(), 1)

    def test_passwords_differ_between_installs(self):
        first = self.run_setup().first_login["password"]
        get_user_model().objects.all().delete()
        second = self.run_setup().first_login["password"]
        self.assertNotEqual(first, second)
