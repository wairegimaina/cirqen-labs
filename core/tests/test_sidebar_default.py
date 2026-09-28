"""Every signed-in page has the sidebar unless it is a sign-in page or the
user is held on first-login setup (Equiper.context_processors.nav_context)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from users.models import UserProfile

User = get_user_model()
SIDEBAR = 'class="equiper-sidebar'


class SidebarDefaultTests(TestCase):
    def _user(self, name, role="HOD", **extra):
        user = User.objects.create_user(username=name, password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **extra})
        return user

    def test_pages_whose_view_does_not_ask_for_it_still_have_it(self):
        self.client.force_login(self._user("sb_hod"))
        for name in ("core:site_profile", "audit_log:list", "dashboard:hod_dashboard",
                     "calibration:ansur_settings"):
            with self.subTest(name):
                self.assertContains(self.client.get(reverse(name)), SIDEBAR)

    def test_sign_in_pages_have_none(self):
        for name in ("custom_login", "forgot_password"):
            with self.subTest(name):
                self.assertNotContains(self.client.get(reverse(name)), SIDEBAR)

    def test_none_while_held_on_first_login_setup(self):
        user = self._user("sb_new")
        user.userprofile.must_change_password = True
        user.userprofile.save()
        self.client.force_login(user)
        self.assertNotContains(self.client.get(reverse("force_setup")), SIDEBAR)

    def test_two_factor_setup_opened_from_the_menu_keeps_it(self):
        self.client.force_login(self._user("sb_2fa"))
        self.assertContains(self.client.get(reverse("two_factor_setup")), SIDEBAR)
