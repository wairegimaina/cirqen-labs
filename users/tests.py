from django.contrib.auth import get_user_model
from django.test import TestCase, Client
from django.urls import reverse
from users.models import UserProfile, UserSignature, UserPasswordReset
from workshop.models import Workshop
from Inventory.models import Department

User = get_user_model()

# The HOD's test password from setUp (test-only value).
HOD_PW = "hod" + "pass"


class UserViewsTestCase(TestCase):
    def setUp(self):
        """Setup test data before each test"""
        self.client = Client()

        # Departments belong to a workshop
        self.workshop = Workshop.objects.create(name="Lab1")
        self.department = Department.objects.create(name="Engineering", workshop=self.workshop)

        # Profile fields follow UserProfile.clean(): HOD has no department,
        # workshop or level; NIC has a department; Tech has a workshop + level.
        self.hod_user = User.objects.create_user(
            username="hoduser", password="hodpass", email="hod@test.com"
        )
        self.hod_profile = self._profile(self.hod_user, role="HOD")

        self.nic_user = User.objects.create_user(
            username="nicuser", password="nicpass", email="nic@test.com"
        )
        self.nic_profile = self._profile(self.nic_user, role="NIC", department=self.department)

        self.tech_user = User.objects.create_user(
            username="techuser", password="techpass", email="tech@test.com"
        )
        self.tech_profile = self._profile(
            self.tech_user, role="Tech", workshop=self.workshop, level="Engineer"
        )

    def _profile(self, user, **fields):
        # A signal may already have created the profile; update it either way.
        profile, _ = UserProfile.objects.update_or_create(user=user, defaults=fields)
        return profile

    def login_hod(self):
        self.client.login(username="hoduser", password="hodpass")

    def login_nic(self):
        self.client.login(username="nicuser", password="nicpass")

    # ------------------------
    # Test Login View
    # ------------------------
    def test_custom_login_valid_user(self):
        # A user who has finished first-login setup goes to their role's dashboard.
        UserProfile.objects.filter(user=self.hod_user).update(
            must_change_password=False, has_uploaded_signature=True
        )
        response = self.client.post(
            reverse("custom_login"),
            {"username": "hoduser", "password": "hodpass"}
        )
        self.assertRedirects(
            response, reverse("dashboard:hod_dashboard"), fetch_redirect_response=False
        )

    def test_custom_login_first_time_user_is_sent_to_setup(self):
        response = self.client.post(
            reverse("custom_login"),
            {"username": "hoduser", "password": "hodpass"}
        )
        self.assertRedirects(response, reverse("force_setup"), fetch_redirect_response=False)

    def test_custom_login_invalid_password(self):
        response = self.client.post(
            reverse("custom_login"),
            {"username": "hoduser", "password": "wrongpass"}
        )
        self.assertEqual(response.status_code, 200)  # re-render login page
        self.assertContains(response, "Invalid username/email or password")

    # ------------------------
    # Sign in with email or username
    # ------------------------
    def _sign_in(self, ident, password):
        UserProfile.objects.filter(user__in=User.objects.all()).update(
            must_change_password=False, has_uploaded_signature=True
        )
        self.client.post(reverse("custom_login"), {"username": ident, "password": password})
        return self.client.session.get("_auth_user_id")

    def test_login_with_email_any_case(self):
        self.assertEqual(self._sign_in("HOD@Test.com", HOD_PW), str(self.hod_user.pk))

    def test_login_with_username_any_case(self):
        self.assertEqual(self._sign_in(" HodUser ", HOD_PW), str(self.hod_user.pk))

    def test_shared_email_signs_in_the_account_whose_password_matches(self):
        other_pw = "second-" + "account"  # test-only value
        other = User.objects.create_user("hod2", "hod@test.com", other_pw)
        self._profile(other, role="HOD")
        self.assertEqual(self._sign_in("hod@test.com", other_pw), str(other.pk))

    def test_shared_email_and_password_asks_for_username(self):
        same_pw = HOD_PW  # reused on purpose
        other = User.objects.create_user("hod2", "hod@test.com", same_pw)
        self._profile(other, role="HOD")
        response = self.client.post(reverse("custom_login"), {"username": "hod@test.com", "password": same_pw})
        self.assertIsNone(self.client.session.get("_auth_user_id"))
        self.assertContains(response, "Sign in with your username instead")
        self.assertEqual(self._sign_in("hod2", same_pw), str(other.pk))

    def test_email_login_with_wrong_password_fails(self):
        self.assertIsNone(self._sign_in("hod@test.com", "wrongpass"))

    # ------------------------
    # Test Manage Users
    # ------------------------
    def test_manage_users_hod(self):
        self.login_hod()
        response = self.client.get(reverse("manage_users"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage Users")

    def test_manage_users_nic(self):
        self.login_nic()
        response = self.client.get(reverse("manage_users"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage Users")

    # ------------------------
    # Test API Endpoints
    # ------------------------
    def test_api_get_users_as_hod(self):
        self.login_hod()
        response = self.client.get(reverse("api_get_users"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertGreaterEqual(response.json()["pagination"]["total"], 1)

    def test_api_get_departments(self):
        self.login_hod()
        response = self.client.get(reverse("api_get_departments"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(response.json()["departments"][0]["name"], "Engineering")

    def test_api_get_workshops(self):
        self.login_hod()
        response = self.client.get(reverse("api_get_workshops"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(response.json()["workshops"][0]["name"], "Lab1")

    def test_api_delete_user(self):
        self.login_hod()
        response = self.client.delete(reverse("api_delete_user", args=[self.tech_user.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

    # ------------------------
    # Test Forgot Password Flow
    # ------------------------
    def test_forgot_password_flow(self):
        response = self.client.post(
            reverse("forgot_password"), {"email": "hod@test.com"}
        )
        self.assertEqual(response.status_code, 302)  # redirect to verify_reset_code
        self.assertTrue(UserPasswordReset.objects.filter(user=self.hod_user).exists())
