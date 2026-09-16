"""The shared role gate keeps each view's refusal style (IMPROVEMENT_PLAN.md 4.2)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()


class RoleRequiredTests(TestCase):
    def setUp(self):
        ws = Workshop.objects.create(name="Main")
        self.tech = User.objects.create_user(username="rr_tech", password="pw12345!")
        UserProfile.objects.update_or_create(user=self.tech, defaults={
            "role": "Tech", "workshop": ws, "level": "Engineer",
            "must_change_password": False, "has_uploaded_signature": True,
        })
        self.no_profile = User.objects.create_user(username="rr_bare", password="pw12345!")
        UserProfile.objects.filter(user=self.no_profile).delete()
        self.ws = ws

    def test_json_endpoint_refuses_with_json_403(self):
        self.client.force_login(self.tech)
        response = self.client.post(reverse("partstools:delete_accessory_name", args=[self.ws.id]))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "Only HOD can delete accessory names.")

    def test_page_refuses_with_message_and_redirect(self):
        self.client.force_login(self.tech)
        response = self.client.get(reverse("inventory_for_hod", args=[self.ws.id]))
        self.assertRedirects(response, reverse("inventory"), fetch_redirect_response=False)

    def test_default_is_the_403_page(self):
        self.client.force_login(self.tech)
        self.assertEqual(self.client.get(reverse("dashboard:nic_dashboard")).status_code, 403)

    def test_user_without_a_profile_is_refused_not_crashed(self):
        self.client.force_login(self.no_profile)
        response = self.client.get(reverse("jobcard:hod_workshop_jobcards", args=[self.ws.id]))
        self.assertEqual(response.status_code, 302)
