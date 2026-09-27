"""Two-factor sign-in with an authenticator app (RFC 6238)."""
import time

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from users import two_factor
from users.models import TwoFactorDevice, UserProfile

User = get_user_model()


class TotpTests(SimpleTestCase):
    def test_rfc6238_reference_value(self):
        # RFC 6238 appendix B: SHA-1 secret "12345678901234567890", T=59s -> 94287082 (8 digits).
        import base64
        secret = base64.b32encode(b"12345678901234567890").decode()
        self.assertEqual(two_factor.code_at(secret, 59 // 30), "287082")

    def test_adjacent_steps_are_accepted_and_others_are_not(self):
        secret = two_factor.new_secret()
        now = time.time()
        step = two_factor.current_step(now)
        self.assertEqual(two_factor.matching_step(secret, two_factor.code_at(secret, step - 1), now), step - 1)
        self.assertIsNone(two_factor.matching_step(secret, two_factor.code_at(secret, step - 3), now))
        self.assertIsNone(two_factor.matching_step(secret, "12345", now))

    def test_recovery_codes_are_single_use(self):
        plain, hashed = two_factor.new_recovery_codes()
        remaining = two_factor.use_recovery_code(hashed, plain[0].upper())
        self.assertEqual(len(remaining), len(hashed) - 1)
        self.assertIsNone(two_factor.use_recovery_code(remaining, plain[0]))


class TwoFactorFlowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="tf_hod", password="pw12345!")
        UserProfile.objects.update_or_create(user=self.user, defaults={
            "role": "HOD", "must_change_password": False, "has_uploaded_signature": True})

    def _enable(self):
        self.client.force_login(self.user)
        self.client.get(reverse("two_factor_setup"))
        secret = self.client.session["2fa_new_secret"]
        response = self.client.post(reverse("two_factor_setup"),
                                    {"code": two_factor.code_at(secret, two_factor.current_step())})
        self.client.logout()
        return secret, response

    def _password(self):
        return self.client.post(reverse("custom_login"), {"username": "tf_hod", "password": "pw12345!"})

    def test_turning_on_shows_recovery_codes_once(self):
        _secret, response = self._enable()
        self.assertEqual(len(response.context["recovery_codes"]), 8)
        self.assertTrue(TwoFactorDevice.objects.filter(user=self.user).exists())

    def test_password_alone_does_not_sign_in(self):
        self._enable()
        response = self._password()
        self.assertRedirects(response, reverse("two_factor_verify"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get(reverse("dashboard:hod_dashboard")).status_code, 302)

    def test_the_right_code_signs_in_and_cannot_be_reused(self):
        secret, _ = self._enable()
        TwoFactorDevice.objects.filter(user=self.user).update(last_used_step=0)
        code = two_factor.code_at(secret, two_factor.current_step())
        self._password()
        response = self.client.post(reverse("two_factor_verify"), {"code": code})
        self.assertRedirects(response, reverse("dashboard:hod_dashboard"), fetch_redirect_response=False)
        self.assertIn("_auth_user_id", self.client.session)
        self.client.logout()
        self._password()
        self.client.post(reverse("two_factor_verify"), {"code": code})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_wrong_codes_lock_out_the_attempt(self):
        self._enable()
        self._password()
        for _ in range(5):
            self.client.post(reverse("two_factor_verify"), {"code": "000000"})
        response = self.client.post(reverse("two_factor_verify"), {"code": "000000"})
        self.assertRedirects(response, reverse("custom_login"), fetch_redirect_response=False)
        self.assertNotIn("2fa_user_id", self.client.session)

    def test_a_recovery_code_signs_in(self):
        _secret, response = self._enable()
        code = response.context["recovery_codes"][0]
        self._password()
        self.client.post(reverse("two_factor_verify"), {"code": code})
        self.assertIn("_auth_user_id", self.client.session)

    @override_settings(REQUIRE_HOD_TWO_FACTOR=True)
    def test_hods_can_be_required_to_turn_it_on(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("dashboard:hod_dashboard"))
        self.assertRedirects(response, reverse("two_factor_setup"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("two_factor_setup")).status_code, 200)


class ResetCommandTests(TestCase):
    def test_removes_the_device_and_logs_it(self):
        from io import StringIO

        from django.core.management import call_command

        from users.models import UserSecurityLog

        user = get_user_model().objects.create_user("lost_phone", password="x")
        TwoFactorDevice.objects.create(user=user, secret="JBSWY3DPEHPK3PXP")
        call_command("reset_two_factor", "lost_phone", stdout=StringIO())
        self.assertFalse(TwoFactorDevice.objects.filter(user=user).exists())
        self.assertTrue(UserSecurityLog.objects.filter(user=user, event_type="TWO_FACTOR_DISABLED").exists())

    def test_unknown_user_is_an_error(self):
        from django.core.management import CommandError, call_command

        with self.assertRaises(CommandError):
            call_command("reset_two_factor", "nobody")
