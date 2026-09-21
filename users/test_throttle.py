"""Brute-force protection on sign-in and password reset (IMPROVEMENT_PLAN.md 4.3)."""
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.test import TestCase
from django.urls import reverse

from users import throttle
from users.models import UserPasswordReset, UserProfile

User = get_user_model()


class ThrottleTestBase(TestCase):
    def setUp(self):
        caches["throttle"].clear()
        self.user = User.objects.create_user(
            username="alice", password="right-password-1", email="alice@example.com"
        )
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={"role": "HOD", "must_change_password": False, "has_uploaded_signature": True},
        )

    def _login(self, password):
        return self.client.post(
            reverse("custom_login"), {"username": "alice", "password": password}
        )


class LoginThrottleTests(ThrottleTestBase):
    def test_account_locks_after_repeated_failures(self):
        for _ in range(throttle.LOGIN_PER_USER.limit):
            self.assertEqual(self._login("wrong").status_code, 200)

        response = self._login("right-password-1")  # even the right password
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many failed sign-in attempts", status_code=429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_lock_is_per_username_and_case_insensitive(self):
        for _ in range(throttle.LOGIN_PER_USER.limit):
            self._login("wrong")
        response = self.client.post(
            reverse("custom_login"), {"username": "ALICE", "password": "right-password-1"}
        )
        self.assertEqual(response.status_code, 429)

    def test_success_clears_the_failure_count(self):
        for _ in range(throttle.LOGIN_PER_USER.limit - 1):
            self._login("wrong")
        self.assertEqual(self._login("right-password-1").status_code, 302)
        self.client.logout()
        self.assertEqual(throttle.LOGIN_PER_USER.blocked_for("alice"), 0)
        self._login("wrong")  # count restarted, so one more failure does not lock
        self.assertEqual(throttle.LOGIN_PER_USER.blocked_for("alice"), 0)


class ResetCodeTests(ThrottleTestBase):
    def _start_reset(self):
        UserPasswordReset.objects.filter(user=self.user).delete()  # one per user, as the view does
        reset = UserPasswordReset.objects.create(user=self.user)
        session = self.client.session
        session["reset_email"] = self.user.email
        session.save()
        return reset

    def _verify(self, code):
        return self.client.post(reverse("verify_reset_code"), {"reset_code": code})

    @staticmethod
    def _wrong(code):
        return f"{(int(code) + 1) % 1_000_000:06d}"

    def test_wrong_guesses_burn_the_code(self):
        reset = self._start_reset()
        for _ in range(reset.max_attempts):
            self._verify(self._wrong(reset.reset_code))

        response = self._verify(reset.reset_code)  # correct code, but too late
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("reset_user_id", self.client.session)
        reset.refresh_from_db()
        self.assertTrue(reset.is_used)

    def test_correct_code_still_works_within_the_limit(self):
        reset = self._start_reset()
        self._verify(self._wrong(reset.reset_code))
        response = self._verify(reset.reset_code)
        self.assertRedirects(response, reverse("reset_password"), fetch_redirect_response=False)

    def test_verify_endpoint_is_rate_limited_per_client(self):
        for _ in range(throttle.RESET_VERIFY_PER_IP.limit):
            reset = self._start_reset()  # a fresh code each time, as an attacker would
            self._verify(self._wrong(reset.reset_code))
        reset = self._start_reset()
        self.assertEqual(self._verify(reset.reset_code).status_code, 429)


class CsrfEnforcedTests(ThrottleTestBase):
    """User-management APIs must reject cross-site POSTs (IMPROVEMENT_PLAN.md 4.4)."""

    def test_create_user_api_requires_csrf_token(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        url = reverse("api_create_user")

        response = client.post(url, data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 403)

        # The manage-users page reads the token from base.html's meta tag and
        # sends it as X-CSRFToken; that path must keep working.
        page = client.get(reverse("manage_users"))
        token = page.context["csrf_token"]
        response = client.post(url, data="{}", content_type="application/json",
                               HTTP_X_CSRFTOKEN=str(token))
        self.assertNotEqual(response.status_code, 403)
