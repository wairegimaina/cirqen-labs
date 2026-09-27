"""Sign-out after inactivity counts people, not background refreshes."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from users.models import UserProfile

User = get_user_model()


@override_settings(SESSION_IDLE_SECONDS=1800)
class IdleTimeoutTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="idle_hod", password="pw12345!")
        UserProfile.objects.update_or_create(user=self.user, defaults={
            "role": "HOD", "must_change_password": False, "has_uploaded_signature": True})
        self.client.force_login(self.user)
        self.page = reverse("dashboard:hod_dashboard")
        self.now = timezone.now()

    def at(self, minutes):
        return mock.patch("users.session_middleware.timezone.now",
                          return_value=self.now + timezone.timedelta(minutes=minutes))

    def test_a_page_after_the_idle_limit_signs_out(self):
        with self.at(0):
            self.client.get(self.page)
        with self.at(31):
            response = self.client.get(self.page)
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_background_refreshes_do_not_keep_a_session_alive(self):
        with self.at(0):
            self.client.get(self.page)
        for minute in (10, 20, 29):
            with self.at(minute):
                self.client.get(self.page, HTTP_SEC_FETCH_MODE="cors")
        with self.at(31):
            response = self.client.get(self.page, HTTP_SEC_FETCH_MODE="cors")
        self.assertEqual(response.status_code, 401)

    def test_typing_pings_keep_a_session_alive(self):
        with self.at(0):
            self.client.get(self.page)
        for minute in (10, 20, 30, 40):
            with self.at(minute):
                self.assertEqual(self.client.post(reverse("activity_ping")).status_code, 204)
        with self.at(45):
            self.assertEqual(self.client.get(self.page).status_code, 200)


class SessionExpiryTests(TestCase):
    def test_an_expired_session_is_signed_out_with_a_message(self):
        user = User.objects.create_user(username="exp_hod", password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": "HOD", "must_change_password": False, "has_uploaded_signature": True})
        self.client.force_login(user)
        with mock.patch("django.contrib.sessions.backends.base.SessionBase.get_expiry_age", return_value=0):
            page = self.client.get(reverse("dashboard:hod_dashboard"))
            ajax = self.client.get(reverse("dashboard:hod_dashboard"), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(page.status_code, 302)
        self.assertIn("/users/login/", page["Location"])
        self.assertIn(ajax.status_code, (302, 401))
