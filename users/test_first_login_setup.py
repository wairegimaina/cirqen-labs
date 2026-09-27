"""An account with a temporary password can reach only the setup page."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from users.models import UserProfile

User = get_user_model()


class FirstLoginSetupMiddlewareTests(TestCase):
    def _hod(self, username, must_change_password):
        user = User.objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": "HOD", "must_change_password": must_change_password, "has_uploaded_signature": True})
        return user

    def test_temporary_password_is_sent_to_setup(self):
        self.client.force_login(self._hod("new_hod", True))
        response = self.client.get(reverse("dashboard:hod_dashboard"))
        self.assertRedirects(response, reverse("force_setup"), fetch_redirect_response=False)

    def test_temporary_password_gets_403_on_json_requests(self):
        self.client.force_login(self._hod("new_hod", True))
        response = self.client.get(reverse("dashboard:hod_dashboard"), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 403)

    def test_setup_page_health_and_static_stay_reachable(self):
        self.client.force_login(self._hod("new_hod", True))
        self.assertEqual(self.client.get(reverse("force_setup")).status_code, 200)
        self.assertEqual(self.client.get(reverse("health_check")).status_code, 200)

    def test_completed_setup_passes_through(self):
        self.client.force_login(self._hod("old_hod", False))
        response = self.client.get(reverse("dashboard:hod_dashboard"))
        self.assertNotEqual(response.get("Location"), reverse("force_setup"))

    def test_finishing_setup_unlocks_the_account(self):
        user = self._hod("new_hod", True)
        self.client.force_login(user)
        self.client.post(reverse("force_setup"), {
            "new_password": "N3w-strong-pass", "confirm_password": "N3w-strong-pass",
            "signature_data": ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
                               "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="),
        })
        user.refresh_from_db()
        self.assertTrue(user.check_password("N3w-strong-pass"))
        self.assertFalse(user.userprofile.must_change_password)
        response = self.client.get(reverse("dashboard:hod_dashboard"))
        self.assertNotEqual(response.get("Location"), reverse("force_setup"))
