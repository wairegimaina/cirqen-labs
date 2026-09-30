"""Machine Reports > Category Assignment: creating a category before
assigning it (HOD only)."""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from Inventory.models import Department, Equipment, EquipmentDescription
from machineReports.models import EquipmentCategory
from users.models import UserProfile
from workshop.models import Workshop

URL = reverse("equipment_dashboard")
BACK = URL + "#assignments"


class CreateCategoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        workshop = Workshop.objects.create(name="Renal Workshop")
        dept = Department.objects.create(name="Renal Unit", workshop=workshop)
        cls.eq_a = Equipment.objects.create(description=EquipmentDescription.objects.create(name="Monitor"),
                                            model="M", serial_number="A-1", department=dept, workshop=workshop,
                                            status="Working")
        cls.hod = cls._user("cat_hod", "HOD")
        cls.nic_a = cls._user("cat_nic", "NIC", department=dept)
        cls.tech_a = cls._user("cat_tech", "Tech", workshop=workshop, level="Engineer Incharge")

    @classmethod
    def _user(cls, username, role, **profile):
        user = get_user_model().objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **profile})
        return user

    def _create(self, user, name, **extra):
        self.client.force_login(user)
        return self.client.post(URL, {"create_category": "1", "category_name": name, **extra})

    def test_the_hod_creates_one_and_can_then_assign_it(self):
        response = self._create(self.hod, "  Life   support ", is_critical="1", category_description="Ventilators")
        self.assertRedirects(response, BACK, fetch_redirect_response=False)
        category = EquipmentCategory.objects.get(name="Life support")
        self.assertTrue(category.is_critical)
        self.assertEqual(category.description, "Ventilators")

        page = self.client.get(URL)
        self.assertIn(category, page.context["categories"])
        self.assertContains(page, f'value="{category.id}"')

        desc = self.eq_a.description
        self.client.post(URL, {"assign_categories": "1", f"category_{desc.id}": str(category.id)})
        desc.refresh_from_db()
        self.assertEqual(desc.category, category)

    def test_only_the_hod_can_create(self):
        for user in (self.tech_a, self.nic_a):
            self._create(user, "Imaging")
        self.assertFalse(EquipmentCategory.objects.filter(name="Imaging").exists())
        self.client.force_login(self.tech_a)
        self.assertNotContains(self.client.get(URL), 'name="create_category"')

    def test_a_name_in_any_capitals_is_not_created_twice(self):
        self._create(self.hod, "Imaging")
        self._create(self.hod, "IMAGING")
        self.assertEqual(EquipmentCategory.objects.filter(name__iexact="imaging").count(), 1)

    def test_a_deleted_category_is_restored_not_duplicated(self):
        old = EquipmentCategory.objects.create(name="Dialysis", active_status=False, pending_delete=True)
        self._create(self.hod, "dialysis", is_critical="1")
        old.refresh_from_db()
        self.assertTrue(old.active_status and not old.pending_delete and old.is_critical)
        self.assertEqual(EquipmentCategory.objects.filter(name__iexact="dialysis").count(), 1)

    def test_deleted_categories_are_not_offered(self):
        gone = EquipmentCategory.objects.create(name="Retired", active_status=False)
        self.client.force_login(self.hod)
        self.assertNotIn(gone, self.client.get(URL).context["categories"])

    def test_a_blank_name_is_refused(self):
        before = EquipmentCategory.objects.count()
        self._create(self.hod, "   ")
        self.assertEqual(EquipmentCategory.objects.count(), before)

    def test_saving_assignments_returns_to_the_category_section(self):
        self.client.force_login(self.hod)
        response = self.client.post(URL, {"assign_categories": "1"})
        self.assertRedirects(response, BACK, fetch_redirect_response=False)
