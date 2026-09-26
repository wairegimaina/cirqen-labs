"""Machine reports show each role only its own equipment (core.scoping).

An in-charge (NIC) has a department but no workshop. The exports only filtered
``if selected_workshop``, so a NIC's PDF held every department in the hospital;
these tests pin the fix for each view.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.http import HttpResponse
from django.test import TestCase
from django.urls import reverse

from Inventory.models import Department, Equipment, EquipmentDescription, Manufacturer
from users.models import UserProfile
from workshop.models import Workshop

User = get_user_model()


class MachineReportScopingTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ws_a = Workshop.objects.create(name="Renal Workshop")
        cls.ws_b = Workshop.objects.create(name="ICU Workshop")
        cls.dept_a = Department.objects.create(name="Renal Unit", workshop=cls.ws_a)
        cls.dept_b = Department.objects.create(name="ICU", workshop=cls.ws_b)
        desc = EquipmentDescription.objects.create(name="Monitor")
        mfr = Manufacturer.objects.create(name="Acme")
        cls.eq_a = Equipment.objects.create(description=desc, manufacturer=mfr, model="M", serial_number="A-1",
                                            department=cls.dept_a, workshop=cls.ws_a, status="Working")
        cls.eq_b = Equipment.objects.create(description=desc, manufacturer=mfr, model="M", serial_number="B-1",
                                            department=cls.dept_b, workshop=cls.ws_b, status="Working")
        cls.hod = cls._user("mr_hod", "HOD")
        cls.nic_a = cls._user("mr_nic_a", "NIC", department=cls.dept_a)
        cls.nic_b = cls._user("mr_nic_b", "NIC", department=cls.dept_b)
        cls.tech_a = cls._user("mr_tech_a", "Tech", workshop=cls.ws_a, level="Engineer Incharge")

    @classmethod
    def _user(cls, username, role, **profile):
        user = User.objects.create_user(username=username, password="pw12345!")
        UserProfile.objects.update_or_create(
            user=user, defaults={"role": role, "must_change_password": False,
                                 "has_uploaded_signature": True, **profile})
        return user

    def setUp(self):
        caches["default"].clear()

    def _category_pdf_serials(self, user):
        seen = {}

        def capture(equipment_queryset, **kwargs):
            seen["serials"] = sorted(equipment_queryset.values_list("serial_number", flat=True))
            return HttpResponse(b"pdf")

        self.client.force_login(user)
        with mock.patch("machineReports.views.exports.create_equipment_pdf_response", side_effect=capture):
            self.client.get(reverse("export_equipment_category_detailed_pdf"))
        return seen["serials"]

    def _manufacturer_pdf_serials(self, user):
        seen = {}

        def capture(equipment_qs):
            seen["serials"] = sorted(equipment_qs.values_list("serial_number", flat=True))
            return {}

        self.client.force_login(user)
        with mock.patch("machineReports.views.exports.calculate_manufacturer_performance", side_effect=capture), \
                mock.patch("machineReports.views.exports.create_manufacturer_pdf_response",
                           return_value=HttpResponse(b"pdf")):
            self.client.get(reverse("export_manufacturer_pdf"))
        return seen["serials"]

    def test_nic_category_pdf_holds_only_their_department(self):
        self.assertEqual(self._category_pdf_serials(self.nic_a), ["A-1"])

    def test_nic_manufacturer_pdf_holds_only_their_department(self):
        self.assertEqual(self._manufacturer_pdf_serials(self.nic_a), ["A-1"])

    def test_tech_sees_their_workshop_and_hod_sees_everything(self):
        self.assertEqual(self._category_pdf_serials(self.tech_a), ["A-1"])
        self.assertEqual(self._category_pdf_serials(self.hod), ["A-1", "B-1"])

    def test_nic_cannot_export_another_departments_history(self):
        self.client.force_login(self.nic_a)
        denied = self.client.get(reverse("export_equipment_history", args=[self.eq_b.id]))
        self.assertEqual(denied.status_code, 302)
        allowed = self.client.get(reverse("export_equipment_history", args=[self.eq_a.id]))
        self.assertEqual(allowed.status_code, 200)

    def test_nic_dashboard_lists_only_their_department(self):
        self.client.force_login(self.nic_a)
        response = self.client.get(reverse("equipment_dashboard"))
        serials = [e.serial_number for e in response.context["equipment_page"]]
        self.assertEqual(serials, ["A-1"])

    def test_nics_do_not_share_cached_manufacturer_figures(self):
        seen = []

        def capture(equipment_qs):
            seen.append(sorted(equipment_qs.values_list("serial_number", flat=True)))
            return {}

        with mock.patch("machineReports.views.dashboard.calculate_manufacturer_performance", side_effect=capture):
            for user in (self.nic_a, self.nic_b):
                self.client.force_login(user)
                self.client.get(reverse("equipment_dashboard"))
        self.assertEqual(seen, [["A-1"], ["B-1"]])

    def test_only_hod_can_change_equipment_categories(self):
        from machineReports.models import EquipmentCategory

        critical, _ = EquipmentCategory.objects.get_or_create(name="Critical", defaults={"is_critical": True})
        desc = self.eq_a.description
        form = {"assign_categories": "1", f"category_{desc.id}": str(critical.id)}

        self.client.force_login(self.nic_a)
        self.client.post(reverse("equipment_dashboard"), form)
        desc.refresh_from_db()
        self.assertIsNone(desc.category)

        self.client.force_login(self.hod)
        self.client.post(reverse("equipment_dashboard"), form)
        desc.refresh_from_db()
        self.assertEqual(desc.category, critical)
