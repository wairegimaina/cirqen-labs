from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase

from users.models import UserProfile


class CreateHodCommandTests(TestCase):
    def test_creates_hod_with_one_time_password(self):
        out = StringIO()
        call_command("create_hod", username="jdoe", email="jdoe@hospital.example", stdout=out)
        user = get_user_model().objects.get(username="jdoe")
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.role, "HOD")
        self.assertTrue(profile.must_change_password)
        self.assertFalse(user.is_superuser)
        password = out.getvalue().split("One-time password: ")[1].split()[0]
        self.assertTrue(user.check_password(password))
        self.assertGreaterEqual(len(password), 12)

    def test_refuses_an_existing_username(self):
        get_user_model().objects.create_user(username="jdoe", password="x")
        with self.assertRaises(CommandError):
            call_command("create_hod", username="jdoe", email="j@h.example", stdout=StringIO())
