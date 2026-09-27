"""Suppliers and the Warranties module (Equipment -> Warranty -> Supplier)."""
import datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.tests.excel_helpers import ExcelImportTestMixin
from Inventory.models import Department, Equipment, EquipmentDescription, Supplier, Warranty
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()


class WarrantyTestBase(TestCase):
    def setUp(self):
        super().setUp()
        self.workshop = Workshop.objects.create(name="Biomed", category="maintenance")
        self.other_workshop = Workshop.objects.create(name="Annex", category="maintenance")
        self.department = Department.objects.create(name="ICU", workshop=self.workshop)
        self.other_department = Department.objects.create(name="Theatre", workshop=self.other_workshop)
        self.description = EquipmentDescription.objects.create(name="Infusion Pump")
        self.supplier = Supplier.objects.create(name="MedEquip Ltd", phone="0712 000 111",
                                                email="service@medequip.test", default_warranty_months=24)
        self.tech = self._user("w_tech", "Tech", workshop=self.workshop, level="Engineer")
        self.nurse = self._user("w_nic", "NIC", department=self.department)
        self.outsider = self._user("w_out", "Tech", workshop=self.other_workshop, level="Engineer")

    def _user(self, username, role, **profile):
        user = User.objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(user=user, defaults={
            "role": role, "must_change_password": False, "has_uploaded_signature": True, **profile})
        return user

    def _equipment(self, serial="PUMP-1", department=None, **fields):
        return Equipment.objects.create(description=self.description, model="P1", serial_number=serial,
                                        department=department or self.department, status="Working", **fields)

    def _warranty(self, equipment, **fields):
        fields.setdefault("supplier", self.supplier)
        fields.setdefault("start_date", datetime.date(2025, 1, 31))
        fields.setdefault("period_months", 24)
        return Warranty.objects.create(equipment=equipment, **fields)


class WarrantyModelTests(WarrantyTestBase):
    def test_expiry_from_start_and_period(self):
        w = self._warranty(self._equipment())
        self.assertEqual(w.expiry_date, datetime.date(2027, 1, 31))
        self.assertEqual(w.period_display, "2 years")

    def test_period_from_start_and_expiry(self):
        w = self._warranty(self._equipment(), period_months=None, start_date=datetime.date(2025, 3, 1),
                           expiry_date=datetime.date(2026, 9, 1))
        self.assertEqual(w.period_months, 18)
        self.assertEqual(w.period_display, "1 yr 6 mo")

    def test_needs_period_or_expiry(self):
        with self.assertRaises(ValidationError):
            self._warranty(self._equipment(), period_months=None)

    def test_expiry_before_start_rejected(self):
        with self.assertRaises(ValidationError):
            self._warranty(self._equipment(), period_months=None, expiry_date=datetime.date(2024, 1, 1))

    @override_settings(WARRANTY_EXPIRING_SOON_DAYS=60)
    def test_status_from_dates(self):
        w = self._warranty(self._equipment(), period_months=None, start_date=datetime.date(2025, 1, 1),
                           expiry_date=datetime.date(2026, 2, 1))
        self.assertEqual(w.status(datetime.date(2025, 6, 1)), Warranty.STATUS_ACTIVE)
        self.assertEqual(w.status(datetime.date(2026, 1, 1)), Warranty.STATUS_EXPIRING)
        self.assertEqual(w.status(datetime.date(2026, 2, 1)), Warranty.STATUS_EXPIRING)
        self.assertEqual(w.status(datetime.date(2026, 2, 2)), Warranty.STATUS_EXPIRED)

    @override_settings(WARRANTY_EXPIRING_SOON_DAYS=10)
    def test_expiring_soon_window_is_configurable(self):
        w = self._warranty(self._equipment(), period_months=None, start_date=datetime.date(2025, 1, 1),
                           expiry_date=datetime.date(2026, 2, 1))
        self.assertEqual(w.status(datetime.date(2026, 1, 1)), Warranty.STATUS_ACTIVE)

    def test_coverage_labels_keep_choice_order(self):
        w = self._warranty(self._equipment(), coverage=["labour", "parts"], coverage_other="Batteries")
        self.assertEqual(w.coverage_labels, ["Parts replacement", "Labour", "Batteries"])
        with self.assertRaises(ValidationError):
            self._warranty(self._equipment("PUMP-2"), coverage=["bogus"])

    def test_supplier_names_are_unique_case_insensitively(self):
        with self.assertRaises(ValidationError):
            Supplier.objects.create(name="medequip ltd")


class WarrantyPageTests(WarrantyTestBase):
    def test_list_counts_and_filters(self):
        today = datetime.date.today()
        active = self._warranty(self._equipment("A-1"), start_date=today, period_months=24)
        expired = self._warranty(self._equipment("E-1"), period_months=None,
                                 start_date=today - datetime.timedelta(days=800),
                                 expiry_date=today - datetime.timedelta(days=1))
        bare = self._equipment("N-1")
        self._warranty(self._equipment("X-1", department=self.other_department))  # other workshop

        self.client.force_login(self.tech)
        response = self.client.get(reverse("warranty_list"))
        self.assertEqual(response.context["counts"], {"active": 1, "expiring": 0, "expired": 1, "none": 1})
        self.assertContains(response, "0712 000 111")  # supplier phone read from the Supplier
        self.assertNotContains(response, "X-1")

        response = self.client.get(reverse("warranty_list"), {"status": "expired"})
        self.assertEqual([w.id for w in response.context["page"]], [expired.id])
        response = self.client.get(reverse("warranty_list"), {"status": "none"})
        self.assertEqual([e.id for e in response.context["page"]], [bare.id])
        response = self.client.get(reverse("warranty_list"), {"q": "A-1"})
        self.assertEqual([w.id for w in response.context["page"]], [active.id])

    def test_detail_shows_equipment_warranty_and_live_supplier(self):
        w = self._warranty(self._equipment(asset_tag="KNH-7"), coverage=["parts", "labour"], terms="Excludes misuse")
        self.supplier.phone = "0799 999 999"  # changed in Suppliers after the warranty was recorded
        self.supplier.save()
        self.client.force_login(self.nurse)
        response = self.client.get(reverse("warranty_detail", args=[w.id]))
        for text in ("KNH-7", "PUMP-1", "2 years", "Parts replacement", "Labour", "Excludes misuse",
                     "MedEquip Ltd", "0799 999 999", "service@medequip.test"):
            self.assertContains(response, text)
        self.assertFalse(response.context["can_edit"])

    def test_equipment_page_without_warranty(self):
        eq = self._equipment()
        self.client.force_login(self.tech)
        response = self.client.get(reverse("equipment_warranty", args=[eq.id]))
        self.assertContains(response, "No warranty is recorded")

    def test_scoped_to_the_users_workshop(self):
        w = self._warranty(self._equipment())
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse("warranty_detail", args=[w.id])).status_code, 404)

    def test_create_edit_and_remove(self):
        eq = self._equipment()
        self.client.force_login(self.tech)
        response = self.client.post(reverse("warranty_create"), {
            "equipment": eq.id, "warranty_supplier": self.supplier.id, "warranty_start_date": "2026-03-01",
            "warranty_period_months": "12", "warranty_coverage": ["parts", "manufacturing_defects"],
            "warranty_terms": "On-site within 48 h",
        })
        w = Warranty.objects.get(equipment=eq)
        self.assertRedirects(response, reverse("warranty_detail", args=[w.id]))
        self.assertEqual(w.expiry_date, datetime.date(2027, 3, 1))
        self.assertEqual(w.created_by, self.tech)

        self.client.post(reverse("warranty_update", args=[w.id]), {
            "warranty_supplier": self.supplier.id, "warranty_start_date": "2026-03-01",
            "warranty_period_months": "36", "warranty_coverage": ["parts"],
        })
        w.refresh_from_db()
        self.assertEqual(w.expiry_date, datetime.date(2029, 3, 1))  # period change recomputes expiry
        self.assertEqual(w.coverage, ["parts"])

        self.client.post(reverse("warranty_remove", args=[w.id]))
        w.refresh_from_db()
        self.assertFalse(w.active_status)

    def test_supplier_must_come_from_the_supplier_list(self):
        eq = self._equipment()
        self.client.force_login(self.tech)
        self.client.post(reverse("warranty_create"), {
            "equipment": eq.id, "warranty_supplier": "", "warranty_start_date": "2026-03-01",
            "warranty_period_months": "12",
        })
        self.assertFalse(Warranty.objects.exists())

    def test_in_charge_cannot_change_warranties(self):
        eq = self._equipment()
        self.client.force_login(self.nurse)
        self.client.post(reverse("warranty_create"), {
            "equipment": eq.id, "warranty_supplier": self.supplier.id, "warranty_start_date": "2026-03-01",
            "warranty_period_months": "12",
        })
        self.assertFalse(Warranty.objects.exists())

    def test_cannot_add_warranty_to_another_workshops_equipment(self):
        eq = self._equipment(department=self.other_department)
        self.client.force_login(self.tech)
        self.client.post(reverse("warranty_create"), {
            "equipment": eq.id, "warranty_supplier": self.supplier.id, "warranty_start_date": "2026-03-01",
            "warranty_period_months": "12",
        })
        self.assertFalse(Warranty.objects.exists())


class InventoryStaysCleanTests(WarrantyTestBase):
    def test_add_equipment_with_optional_warranty(self):
        self.client.force_login(self.tech)
        self.client.post(reverse("add_inventory"), {
            "description": self.description.id, "model": "P2", "serial_number": "pump-2",
            "department": self.department.id, "status": "Working", "asset_tag": "KNH-0042",
            "warranty_supplier": self.supplier.id, "warranty_start_date": "2026-03-01",
            "warranty_period_months": "24", "warranty_coverage": ["parts", "labour"],
        })
        eq = Equipment.objects.get(serial_number="PUMP-2")
        self.assertEqual(eq.asset_tag, "KNH-0042")
        w = eq.current_warranty()
        self.assertEqual((w.supplier, w.expiry_date), (self.supplier, datetime.date(2028, 3, 1)))

    def test_add_equipment_without_warranty(self):
        self.client.force_login(self.tech)
        self.client.post(reverse("add_inventory"), {
            "description": self.description.id, "model": "P2", "serial_number": "pump-3",
            "department": self.department.id, "status": "Working",
        })
        self.assertTrue(Equipment.objects.filter(serial_number="PUMP-3").exists())
        self.assertFalse(Warranty.objects.exists())

    def test_bad_warranty_keeps_the_equipment(self):
        self.client.force_login(self.tech)
        self.client.post(reverse("add_inventory"), {
            "description": self.description.id, "model": "P2", "serial_number": "pump-4",
            "department": self.department.id, "status": "Working",
            "warranty_supplier": self.supplier.id, "warranty_start_date": "",
        })
        self.assertTrue(Equipment.objects.filter(serial_number="PUMP-4").exists())
        self.assertFalse(Warranty.objects.exists())

    def test_edit_without_asset_tag_field_keeps_it(self):
        # The In-Charge edit modal has no asset number input.
        eq = self._equipment(asset_tag="A-1")
        self.client.force_login(self.tech)
        self.client.post(reverse("edit_inventory", args=[eq.id]), {
            "description": self.description.id, "model": "P1b", "serial_number": eq.serial_number,
            "department": self.department.id, "status": "Working",
        })
        eq.refresh_from_db()
        self.assertEqual((eq.model, eq.asset_tag), ("P1b", "A-1"))

    def test_inventory_table_has_no_warranty_column(self):
        self._warranty(self._equipment())
        self.client.force_login(self.tech)
        data = self.client.get(reverse("inventory"), HTTP_X_REQUESTED_WITH="XMLHttpRequest").json()
        row = data["equipments"][0]
        self.assertNotIn("warranty_expiry", row)
        self.assertNotIn("supplier", row)
        html = self.client.get(reverse("inventory")).content.decode()
        self.assertNotIn(">Warranty</th>", html)
        self.assertIn(reverse("warranty_list"), html)  # the Warranties tab

    def test_supplier_pages(self):
        self._warranty(self._equipment())
        self.client.force_login(self.tech)
        response = self.client.get(reverse("supplier_list"))
        self.assertEqual(response.context["suppliers"][0].warranty_count, 1)
        self.client.post(reverse("supplier_create"), {"name": "Philips EA", "default_warranty_months": "12"})
        self.assertEqual(Supplier.objects.get(name="Philips EA").default_warranty_months, 12)
        self.client.post(reverse("supplier_toggle_active", args=[self.supplier.id]))
        self.supplier.refresh_from_db()
        self.assertFalse(self.supplier.active_status)


class WarrantyAndSupplierImportTests(ExcelImportTestMixin, WarrantyTestBase):
    HEADERS = ["Serial Number", "Supplier", "Start Date", "Period (Months)", "Expiry Date", "Covers",
               "Other Coverage", "Warranty Reference", "Terms / Notes"]

    def setUp(self):
        super().setUp()
        self.client.force_login(self.tech)

    def test_warranty_template_download(self):
        response = self.client.get(reverse("warranty_import_template"))
        self.assertEqual(response.status_code, 200)
        from core.tests.excel_helpers import read_response_workbook
        wb = read_response_workbook(response)
        self.assertEqual([c.value for c in wb["Warranties"][1]], self.HEADERS)

    def test_preview_then_commit_warranties(self):
        self._equipment("PUMP-1")
        self._equipment("PUMP-2")
        rows = [self.HEADERS,
                ["pump-1", "MedEquip Ltd", datetime.datetime(2026, 1, 15), 24, None,
                 "Parts replacement, Labour; batteries", "", "W-100", "Standard"],
                ["PUMP-2", "New Vendor", "01/02/2026", None, "01/02/2027", "parts", "", "", ""],
                ["NOPE-9", "MedEquip Ltd", "01/02/2026", 12, None, "", "", "", ""]]
        url = reverse("warranty_import_upload")

        preview = self.post_workbook(url, {"Warranties": rows}).json()
        self.assertEqual(preview["counts"], {"create": 1, "skip": 0, "error": 2})  # New Vendor missing
        self.assertFalse(Warranty.objects.exists())

        data = self.post_workbook(url, {"Warranties": rows}, commit=True, create_missing="true").json()
        self.assertEqual(data["counts"], {"create": 2, "skip": 0, "error": 1})
        self.assertEqual(data["created"], {"Suppliers": ["New Vendor"]})
        w = Warranty.objects.get(equipment__serial_number="PUMP-1")
        self.assertEqual((w.expiry_date, w.coverage, w.coverage_other, w.reference),
                         (datetime.date(2028, 1, 15), ["parts", "labour"], "batteries", "W-100"))
        self.assertEqual(Warranty.objects.get(equipment__serial_number="PUMP-2").supplier.name, "New Vendor")

        again = self.post_workbook(url, {"Warranties": rows[:2]}).json()
        self.assertEqual(again["counts"]["skip"], 1)

    def test_warranty_import_is_scoped(self):
        self._equipment("THEATRE-1", department=self.other_department)
        rows = [self.HEADERS, ["THEATRE-1", "MedEquip Ltd", "01/02/2026", 12, None, "", "", "", ""]]
        data = self.post_workbook(reverse("warranty_import_upload"), {"Warranties": rows}, commit=True).json()
        self.assertEqual(data["counts"]["error"], 1)

    def test_nurse_cannot_import(self):
        self.client.force_login(self.nurse)
        response = self.post_workbook(reverse("warranty_import_upload"), {"Warranties": [self.HEADERS]})
        self.assertEqual(response.status_code, 302)

    def test_supplier_import(self):
        rows = [["Supplier Name", "Phone", "Email", "Default Warranty (Months)"],
                ["Acme Medical", "0700 1", "a@acme.test", 12],
                ["medequip ltd", "", "", ""],
                ["Bad Months", "", "", "two"]]
        url = reverse("supplier_import_upload")
        data = self.post_workbook(url, {"Suppliers": rows}, commit=True).json()
        self.assertEqual(data["counts"], {"create": 1, "skip": 1, "error": 1})
        self.assertEqual(Supplier.objects.get(name="Acme Medical").default_warranty_months, 12)
        self.assertEqual(self.client.get(reverse("supplier_import_template")).status_code, 200)
