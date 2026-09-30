"""Near-duplicate names and the Add Equipment modal's editable lists."""
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from core.names import find_same, name_key
from Inventory.models import Equipment, EquipmentDescription, Manufacturer
from parts_tools.models import Accessoriesname

from .test_views import InventoryViewTestBase


class NameKeyTests(TestCase):
    def test_case_spaces_and_punctuation_are_ignored(self):
        self.assertEqual(name_key("PatienT MONITOR"), name_key("patient- Monitor"))
        self.assertEqual(name_key("Patient  Monitor"), "patientmonitor")
        self.assertNotEqual(name_key("Patient Monitor"), name_key("Patient Monitors"))

    def test_description_near_duplicate_refused(self):
        EquipmentDescription.objects.create(name="Patient Monitor")
        with self.assertRaises(ValidationError):
            EquipmentDescription.objects.create(name="PatienT- MONITOR")

    def test_existing_near_duplicates_still_save_for_other_edits(self):
        a = EquipmentDescription.objects.create(name="Infusion Pump")
        # Written before the rule (e.g. by sync), bypassing clean().
        EquipmentDescription.objects.bulk_create([EquipmentDescription(name="infusion-pump")])
        a.save()  # its own name did not change, so no complaint

    def test_part_names_use_the_same_rule(self):
        Accessoriesname.objects.create(name="Spo2 Probe")
        with self.assertRaises(ValidationError):
            Accessoriesname.objects.create(name="SPO2-probe")
        self.assertEqual(find_same(Accessoriesname.objects.all(), "spo2 probe").name, "Spo2 Probe")


class EquipmentModelSpellingTests(InventoryViewTestBase):
    def test_model_joins_the_existing_spelling(self):
        Equipment.objects.create(description=self.description, model="MX 450", serial_number="A1",
                                 department=self.department, status="Working")
        e = Equipment.objects.create(description=self.description, model="mx-450", serial_number="A2",
                                     department=self.department, status="Working")
        self.assertEqual(e.model, "MX 450")


class CreateNearDuplicateTests(InventoryViewTestBase):
    def test_near_duplicate_description_returns_existing(self):
        tech = self._make_user("t1", "Tech", workshop=self.workshop, level="Engineer")
        self.client.force_login(tech)
        resp = self.client.post(reverse("create_equipment_description"), {"description_name": "scan-ner "})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["description_id"], str(self.description.id))
        self.assertTrue(resp.json()["existing"])


class NameListsTests(InventoryViewTestBase):
    def setUp(self):
        super().setUp()
        self.tech = self._make_user("t2", "Tech", workshop=self.workshop, level="Engineer")
        self.client.force_login(self.tech)

    def test_list_shows_usage(self):
        Equipment.objects.create(description=self.description, manufacturer=self.manufacturer, model="X",
                                 serial_number="S1", department=self.department, status="Working")
        data = self.client.get(reverse("equipment_name_lists")).json()
        scanner = {d["name"]: d for d in data["descriptions"]}["Scanner"]
        self.assertEqual(scanner["devices"], 1)
        self.assertGreaterEqual(scanner["uses"], 1)
        self.assertEqual({m["name"]: m["devices"] for m in data["manufacturers"]}["Acme"], 1)

    def test_rename_description(self):
        url = reverse("equipment_name_rename", args=["description", self.description.id])
        resp = self.client.post(url, {"name": "CT Scanner"})
        self.assertTrue(resp.json()["success"])
        self.description.refresh_from_db()
        self.assertEqual(self.description.name, "CT Scanner")

    def test_rename_to_existing_name_refused(self):
        other = EquipmentDescription.objects.create(name="Patient Monitor")
        url = reverse("equipment_name_rename", args=["description", other.id])
        resp = self.client.post(url, {"name": "SCANNER"})
        self.assertEqual(resp.status_code, 400)

    def test_delete_only_when_unused(self):
        Equipment.objects.create(description=self.description, manufacturer=self.manufacturer, model="X",
                                 serial_number="S1", department=self.department, status="Working")
        resp = self.client.post(reverse("equipment_name_delete", args=["manufacturer", self.manufacturer.id]))
        self.assertEqual(resp.status_code, 400)

        unused = Manufacturer.objects.create(name="Unused Co")
        resp = self.client.post(reverse("equipment_name_delete", args=["manufacturer", unused.id]))
        self.assertTrue(resp.json()["success"])
        unused.refresh_from_db()
        self.assertTrue(unused.pending_delete)
        self.assertFalse(unused.active_status)

    def test_deleted_name_comes_back_when_added_again(self):
        unused = Manufacturer.objects.create(name="Unused Co")
        self.client.post(reverse("equipment_name_delete", args=["manufacturer", unused.id]))
        resp = self.client.post(reverse("create_manufacturer"), {"manufacturer_name": "unused-co"})
        self.assertTrue(resp.json()["success"])
        unused.refresh_from_db()
        self.assertTrue(unused.active_status)
        self.assertFalse(unused.pending_delete)

    def test_rename_model_merges(self):
        for serial, model in (("S1", "MX 450"), ("S2", "mx450"), ("S3", "Other")):
            Equipment.objects.bulk_create([Equipment(description=self.description, model=model, serial_number=serial,
                                                     department=self.department, status="Working")])
        url = reverse("equipment_model_rename", args=[self.description.id])
        resp = self.client.post(url, {"old": "mx450", "name": "MX-450"})
        self.assertEqual(resp.json()["name"], "MX 450")
        self.assertEqual(Equipment.objects.filter(model="MX 450").count(), 2)

    def test_nurse_cannot_edit_lists(self):
        nurse = self._make_user("n1", "NIC", department=self.department)
        self.client.force_login(nurse)
        resp = self.client.post(reverse("equipment_name_rename", args=["description", self.description.id]),
                                {"name": "Hack"})
        self.assertEqual(resp.status_code, 403)
