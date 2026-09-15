"""Tests for the bulk department Excel import (:mod:`Inventory.views.department_imports`)."""
from django.test import TestCase
from django.urls import reverse

from core.tests.excel_helpers import ExcelImportTestMixin, read_response_workbook
from Inventory.models import Department
from workshop.models import Workshop

HEADER = ["Department Name"]


class DepartmentImportTestBase(ExcelImportTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.radiology = Workshop.objects.create(name="Radiology")
        self.dental = Workshop.objects.create(name="Dental")
        self.ct = Department.objects.create(name="CT", workshop=self.radiology)
        Department.objects.create(name="Ortho", workshop=self.dental)
        self.tech = self.make_user("tech", "Tech", workshop=self.radiology, level="Engineer")
        self.hod = self.make_user("hod", "HOD")

    def upload(self, workshop, names, **kwargs):
        url = reverse("upload_departments_excel", args=[workshop.id])
        return self.post_workbook(url, {"Departments": [HEADER, *[[name] for name in names]]}, **kwargs)


class DepartmentPermissionTests(DepartmentImportTestBase):
    def test_tech_cannot_target_another_workshop(self):
        self.client.force_login(self.tech)
        template = self.client.get(reverse("download_department_import_template", args=[self.dental.id]))
        self.assertEqual(template.status_code, 403)
        resp = self.upload(self.dental, ["Implants"], commit=True)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Department.objects.filter(name="Implants").exists())

    def test_hod_can_target_any_workshop(self):
        self.client.force_login(self.hod)
        resp = self.upload(self.dental, ["Implants"], commit=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Department.objects.get(name="Implants").workshop, self.dental)

    def test_in_charge_is_forbidden(self):
        self.client.force_login(self.make_user("nic", "NIC", department=self.ct))
        self.assertEqual(self.upload(self.radiology, ["MRI"]).status_code, 403)

    def test_unknown_workshop_is_not_found(self):
        self.client.force_login(self.hod)
        self.radiology.pending_delete = True
        self.radiology.save()
        self.assertEqual(self.upload(self.radiology, ["MRI"]).status_code, 404)


class DepartmentUploadTests(DepartmentImportTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.tech)

    def test_department_page_offers_the_upload_for_its_workshop(self):
        page = self.client.get(reverse("create_department"))
        # The manual form stays alongside the upload.
        self.assertContains(page, 'id="toggleFormBtn"')
        self.assertContains(page, 'id="addDepartmentForm"')
        self.assertContains(page, 'data-bs-target="#uploadDepartmentsModal"')
        self.assertContains(page, "Bulk Upload Departments to Radiology")
        self.assertContains(page, reverse("upload_departments_excel", args=[self.radiology.id]))

    def test_template_lists_only_this_workshops_departments(self):
        resp = self.client.get(reverse("download_department_import_template", args=[self.radiology.id]))
        self.assertEqual(resp.status_code, 200)
        reference = [cell.value for cell in read_response_workbook(resp)["Reference"]["A"]]
        self.assertIn("CT", reference)
        self.assertNotIn("Ortho", reference)

    def test_preview_saves_nothing(self):
        data = self.upload(self.radiology, ["MRI"]).json()
        self.assertEqual(data["counts"]["create"], 1)
        self.assertFalse(Department.objects.filter(name="MRI").exists())
        self.push_instances.assert_not_called()

    def test_commit_creates_skips_and_reports_conflicts(self):
        data = self.upload(self.radiology, ["MRI", "ct", "Ortho", "mri"], commit=True).json()

        self.assertEqual(data["counts"], {"create": 1, "skip": 1, "error": 2})
        rows = self.rows_by_number(data)
        self.assertEqual(rows[2]["action"], "create")
        self.assertEqual(rows[3]["action"], "skip")
        self.assertIn("already exists in another workshop (Dental)", rows[4]["messages"][0])
        self.assertIn("duplicated in this file", rows[5]["messages"][0])

        mri = Department.objects.get(name="MRI")
        self.assertEqual(mri.workshop, self.radiology)
        self.assertEqual(self.pushed(), [mri])

    def test_overlong_name_rejected(self):
        data = self.upload(self.radiology, ["X" * 101]).json()
        self.assertIn("exceeds 100 characters", data["rows"][0]["messages"][0])
