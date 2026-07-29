"""Integration tests for a representative slice of the Inventory views.

These run against an in-memory SQLite DB (see ``Equiper.test_settings``) and
verify the behaviour that survived the package split: role-based permissions,
create/duplicate handling, equipment creation, and a lookup API. They exercise
the views through their real URLs so the ``urls.py`` -> package wiring is covered
too.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from Inventory.models import Department, Equipment, EquipmentDescription, Manufacturer
from workshop.models import Workshop
from users.models import UserProfile

User = get_user_model()


class InventoryViewTestBase(TestCase):
    def setUp(self):
        self.workshop = Workshop.objects.create(name="Radiology")
        self.department = Department.objects.create(name="CT", workshop=self.workshop)
        self.description = EquipmentDescription.objects.create(name="Scanner")
        self.manufacturer = Manufacturer.objects.create(name="Acme")

    def _make_user(self, username, role, *, workshop=None, department=None, level=None):
        """Create a user with an explicitly configured profile for a role."""
        user = User.objects.create_user(username=username, password="pw12345!")
        # Create the profile directly (don't depend on signal wiring being active).
        UserProfile.objects.update_or_create(
            user=user,
            defaults={
                "role": role,
                "workshop": workshop,
                "department": department,
                "level": level,
            },
        )
        return user


class CreateEquipmentDescriptionTests(InventoryViewTestBase):
    def test_tech_can_create_description(self):
        tech = self._make_user("tech1", "Tech", workshop=self.workshop, level="Engineer")
        self.client.force_login(tech)

        resp = self.client.post(
            reverse("create_equipment_description"),
            {"description_name": "Ventilator"},
        )

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        self.assertTrue(EquipmentDescription.objects.filter(name="Ventilator").exists())

    def test_non_tech_is_forbidden(self):
        hod = self._make_user("hod1", "HOD")
        self.client.force_login(hod)

        resp = self.client.post(
            reverse("create_equipment_description"),
            {"description_name": "Ventilator"},
        )

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(EquipmentDescription.objects.filter(name="Ventilator").exists())

    def test_blank_name_rejected(self):
        tech = self._make_user("tech2", "Tech", workshop=self.workshop, level="Engineer")
        self.client.force_login(tech)
        resp = self.client.post(reverse("create_equipment_description"), {"description_name": "  "})
        self.assertEqual(resp.status_code, 400)

    def test_duplicate_is_conflict(self):
        tech = self._make_user("tech3", "Tech", workshop=self.workshop, level="Engineer")
        self.client.force_login(tech)
        # 'Scanner' already exists from setUp (case-insensitive check)
        resp = self.client.post(reverse("create_equipment_description"), {"description_name": "scanner"})
        self.assertEqual(resp.status_code, 409)


class CreateManufacturerTests(InventoryViewTestBase):
    def test_create_and_titlecase(self):
        tech = self._make_user("tech4", "Tech", workshop=self.workshop, level="Engineer")
        self.client.force_login(tech)
        resp = self.client.post(reverse("create_manufacturer"), {"manufacturer_name": "philips"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        # stored title-cased
        self.assertTrue(Manufacturer.objects.filter(name="Philips").exists())

    def test_duplicate_conflict(self):
        tech = self._make_user("tech5", "Tech", workshop=self.workshop, level="Engineer")
        self.client.force_login(tech)
        resp = self.client.post(reverse("create_manufacturer"), {"manufacturer_name": "acme"})
        self.assertEqual(resp.status_code, 409)


class AddInventoryTests(InventoryViewTestBase):
    def test_add_equipment_creates_active_record(self):
        tech = self._make_user("tech6", "Tech", workshop=self.workshop, level="Engineer")
        self.client.force_login(tech)

        resp = self.client.post(
            reverse("add_inventory"),
            {
                "description": str(self.description.id),
                "manufacturer": str(self.manufacturer.id),
                "model": "X100",
                "serial_number": "SN-ADD-1",
                "department": str(self.department.id),
                "status": "Working",
            },
        )

        self.assertEqual(resp.status_code, 302)  # redirects to inventory
        eq = Equipment.objects.get(serial_number="SN-ADD-1")
        self.assertTrue(eq.active_status)
        self.assertFalse(eq.pending_delete)
        self.assertEqual(eq.department_id, self.department.id)


class TransferEquipmentPermissionTests(InventoryViewTestBase):
    def test_non_tech_cannot_transfer(self):
        equipment = Equipment.objects.create(
            description=self.description,
            manufacturer=self.manufacturer,
            model="X100",
            serial_number="SN-TR-1",
            department=self.department,
            status="Working",
            active_status=True,
        )
        hod = self._make_user("hod2", "HOD")
        self.client.force_login(hod)

        resp = self.client.post(
            reverse("transfer_equipment", args=[equipment.id]),
            {"target_department_id": str(self.department.id)},
        )
        self.assertEqual(resp.status_code, 403)


class GetModelsByDescriptionTests(InventoryViewTestBase):
    def test_returns_distinct_models(self):
        Equipment.objects.create(
            description=self.description, manufacturer=self.manufacturer,
            model="X100", serial_number="SN-M-1", department=self.department,
            status="Working", active_status=True,
        )
        Equipment.objects.create(
            description=self.description, manufacturer=self.manufacturer,
            model="X100", serial_number="SN-M-2", department=self.department,
            status="Working", active_status=True,
        )
        Equipment.objects.create(
            description=self.description, manufacturer=self.manufacturer,
            model="Z9", serial_number="SN-M-3", department=self.department,
            status="Working", active_status=True,
        )
        tech = self._make_user("tech7", "Tech", workshop=self.workshop, level="Engineer")
        self.client.force_login(tech)

        resp = self.client.get(reverse("get_models_by_description", args=[self.description.id]))
        self.assertEqual(resp.status_code, 200)
        payload = resp.json()
        self.assertTrue(payload["success"])
        self.assertEqual(sorted(payload["models"]), ["X100", "Z9"])
        self.assertEqual(payload["count"], 2)


class DeleteDepartmentTests(InventoryViewTestBase):
    def test_empty_department_marked_pending_delete(self):
        # A department with no dependencies can be marked for deletion.
        empty_dept = Department.objects.create(name="Empty Store", workshop=self.workshop)
        tech = self._make_user("tech8", "Tech", workshop=self.workshop, level="Engineer")
        self.client.force_login(tech)

        resp = self.client.post(reverse("delete_department", args=[empty_dept.id]))
        self.assertEqual(resp.status_code, 302)

        empty_dept.refresh_from_db()
        self.assertTrue(empty_dept.pending_delete)

    def test_department_with_equipment_is_blocked(self):
        # 'self.department' has equipment -> the effective delete_department must refuse.
        Equipment.objects.create(
            description=self.description, manufacturer=self.manufacturer,
            model="X100", serial_number="SN-DEL-1", department=self.department,
            status="Working", active_status=True,
        )
        tech = self._make_user("tech9", "Tech", workshop=self.workshop, level="Engineer")
        self.client.force_login(tech)

        resp = self.client.post(reverse("delete_department", args=[self.department.id]))
        self.assertEqual(resp.status_code, 302)

        self.department.refresh_from_db()
        self.assertFalse(self.department.pending_delete)
