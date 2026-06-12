from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from users.models import UserProfile, UserSignature, UserPasswordReset
from workshop.models import Workshop
from Inventory.models import department

class UserViewsTestCase(TestCase):
    def setUp(self):
        """Setup test data before each test"""
        self.client = Client()

        # Create departments and workshops
        self.department = department.objects.create(name="Engineering")
        self.workshop = Workshop.objects.create(name="Lab1", department=self.department)

        # Create HOD user
        self.hod_user = User.objects.create_user(
            username="hoduser", password="hodpass", email="hod@test.com"
        )
        self.hod_profile = UserProfile.objects.create(
            user=self.hod_user, role="HOD", department=self.department
        )

        # Create NIC user
        self.nic_user = User.objects.create_user(
            username="nicuser", password="nicpass", email="nic@test.com"
        )
        self.nic_profile = UserProfile.objects.create(
            user=self.nic_user, role="NIC", department=self.department
        )

        # Create Tech user
        self.tech_user = User.objects.create_user(
            username="techuser", password="techpass", email="tech@test.com"
        )
        self.tech_profile = UserProfile.objects.create(
            user=self.tech_user, role="Tech", workshop=self.workshop, department=self.department
        )

    def login_hod(self):
        self.client.login(username="hoduser", password="hodpass")

    def login_nic(self):
        self.client.login(username="nicuser", password="nicpass")

    # ------------------------
    # Test Login View
    # ------------------------
    def test_custom_login_valid_user(self):
        response = self.client.post(
            reverse("custom_login"),
            {"username": "hoduser", "password": "hodpass"}
        )
        self.assertEqual(response.status_code, 302)  # redirect
        self.assertIn("/dashboard", response.url)

    def test_custom_login_invalid_password(self):
        response = self.client.post(
            reverse("custom_login"),
            {"username": "hoduser", "password": "wrongpass"}
        )
        self.assertEqual(response.status_code, 200)  # re-render login page
        self.assertContains(response, "Incorrect password")

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
        self.assertGreaterEqual(response.json()["total"], 1)

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
