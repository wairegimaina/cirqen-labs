"""Free text must not break out of data embedded in <script> (IMPROVEMENT_PLAN.md 4.1)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()

PAYLOAD = "</script><script>window.__xss=1</script>"


class ScriptDataEscapingTest(TestCase):
    def test_hod_dashboard_escapes_workshop_names_in_chart_data(self):
        Workshop.objects.create(name=PAYLOAD)
        hod = User.objects.create_user(username="hod_xss", password="pw12345!")
        UserProfile.objects.update_or_create(
            user=hod,
            defaults={"role": "HOD", "must_change_password": False, "has_uploaded_signature": True},
        )
        self.client.force_login(hod)

        response = self.client.get(reverse("dashboard:hod_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, PAYLOAD)
        # json_script writes the name with <, > and & as \\u escapes
        self.assertContains(response, "\\u003C/script\\u003E\\u003Cscript\\u003Ewindow.__xss=1")
