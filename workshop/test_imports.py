"""Tests for the bulk workshop Excel import (:mod:`workshop.imports`).

These also cover the shared upload contract in :mod:`core.excel_import`:
file checks, HQ gating, preview rollback and commit-or-nothing.
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from core.tests.excel_helpers import ExcelImportTestMixin, read_response_workbook
from workshop.models import Workshop

HEADER = ["Workshop Name", "Category"]


class WorkshopImportTestBase(ExcelImportTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.radiology = Workshop.objects.create(name="Radiology", category="maintenance")
        self.hod = self.make_user("hod", "HOD")
        self.client.force_login(self.hod)
        self.upload_url = reverse("workshop:upload_workshops_excel")
        self.template_url = reverse("workshop:workshop_import_template")

    def upload(self, rows, **kwargs):
        return self.post_workbook(self.upload_url, {"Workshops": [HEADER, *rows]}, **kwargs)


class WorkshopTemplateTests(WorkshopImportTestBase):
    def test_hod_gets_template_listing_existing_workshops(self):
        resp = self.client.get(self.template_url)
        self.assertEqual(resp.status_code, 200)
        wb = read_response_workbook(resp)
        self.assertEqual(wb.sheetnames, ["Workshops", "Reference", "Instructions"])
        self.assertEqual([cell.value for cell in wb["Workshops"][1]], HEADER)
        self.assertIn("Radiology", [cell.value for cell in wb["Reference"]["A"]])

    def test_workshop_page_offers_the_upload(self):
        page = self.client.get(reverse("workshop:create_workshop"))
        # The manual form stays alongside the upload.
        self.assertContains(page, 'id="toggleCreateForm"')
        self.assertContains(page, 'id="createFormContainer"')
        self.assertContains(page, 'data-bs-target="#uploadWorkshopsModal"')
        self.assertContains(page, f'data-upload-url="{self.upload_url}"')
        self.assertContains(page, self.template_url)


class WorkshopPermissionTests(WorkshopImportTestBase):
    def test_non_hod_is_forbidden(self):
        tech = self.make_user("tech", "Tech", workshop=self.radiology, level="Engineer")
        self.client.force_login(tech)
        self.assertEqual(self.client.get(self.template_url).status_code, 403)
        resp = self.upload([["Dental", "Maintenance"]], commit=True)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Workshop.objects.filter(name="Dental").exists())

    def test_anonymous_is_redirected_to_login(self):
        self.client.logout()
        self.assertEqual(self.upload([["Dental", "Maintenance"]]).status_code, 302)


class WorkshopUploadTests(WorkshopImportTestBase):
    def test_preview_saves_nothing_and_commit_matches_it(self):
        rows = [["Dental", "Maintenance"], ["Cal Lab", "calibration centre"]]

        preview = self.upload(rows).json()
        self.assertFalse(preview["committed"])
        self.assertEqual(preview["counts"], {"create": 2, "skip": 0, "error": 0})
        self.assertFalse(Workshop.objects.filter(name__in=["Dental", "Cal Lab"]).exists())
        self.push_instances.assert_not_called()

        commit = self.upload(rows, commit=True).json()
        self.assertTrue(commit["committed"])
        self.assertEqual(commit["counts"], preview["counts"])
        self.assertEqual(Workshop.objects.get(name="Cal Lab").category, "calibration_center")
        self.assertEqual({w.name for w in self.pushed()}, {"Dental", "Cal Lab"})

    def test_existing_are_skipped_and_invalid_rows_reported(self):
        data = self.upload([
            ["radiology", "Maintenance"],   # row 2: exists
            ["Dental", "Kitchen"],          # row 3: bad category
            ["", "Maintenance"],            # row 4: no name
            ["ENT", "Maintenance"],         # row 5: new
            ["ent", "Maintenance"],         # row 6: duplicate of row 5
        ], commit=True).json()

        self.assertEqual(data["counts"], {"create": 1, "skip": 1, "error": 3})
        rows = self.rows_by_number(data)
        self.assertEqual(rows[2]["action"], "skip")
        self.assertIn("Category 'Kitchen' is not valid", rows[3]["messages"][0])
        self.assertIn("Workshop Name is required.", rows[4]["messages"])
        self.assertEqual(rows[5]["action"], "create")
        self.assertIn("duplicated in this file (also on row 5)", rows[6]["messages"][0])
        self.assertEqual(Workshop.objects.filter(name__iexact="ent").count(), 1)

    def test_pending_delete_name_is_an_error(self):
        Workshop.objects.create(name="Old", category="maintenance", pending_delete=True)
        data = self.upload([["Old", "Maintenance"]]).json()
        self.assertEqual(data["rows"][0]["action"], "error")
        self.assertIn("pending deletion", data["rows"][0]["messages"][0])


class SharedUploadContractTests(WorkshopImportTestBase):
    """Behaviour every import gets from core.excel_import.run_import."""

    def test_refused_while_hq_offline(self):
        self.is_hq_online.return_value = False
        resp = self.upload([["Dental", "Maintenance"]], commit=True)
        self.assertEqual(resp.status_code, 503)
        self.assertFalse(Workshop.objects.filter(name="Dental").exists())

    def test_commit_checks_hq_live(self):
        self.upload([["Dental", "Maintenance"]], commit=True)
        self.is_hq_online.assert_called_once_with(force=True)

    def test_commit_rolled_back_when_hq_rejects_it(self):
        self.push_instances.return_value = (False, "HQ said no")
        resp = self.upload([["Dental", "Maintenance"]], commit=True)
        self.assertEqual(resp.status_code, 502)
        self.assertIn("nothing was saved", resp.json()["error"])
        self.assertFalse(Workshop.objects.filter(name="Dental").exists())

    def test_commit_with_nothing_new_does_not_push(self):
        resp = self.upload([["Radiology", "Maintenance"]], commit=True)
        self.assertEqual(resp.status_code, 200)
        self.push_instances.assert_not_called()

    def test_missing_file_rejected(self):
        resp = self.client.post(self.upload_url, {"commit": "false"})
        self.assertEqual(resp.status_code, 400)

    def test_wrong_extension_rejected(self):
        resp = self.client.post(self.upload_url, {"file": SimpleUploadedFile("w.csv", b"a,b")})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Unsupported file type", resp.json()["error"])

    def test_corrupt_workbook_rejected(self):
        resp = self.client.post(self.upload_url, {"file": SimpleUploadedFile("w.xlsx", b"not a zip")})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Could not read the workbook", resp.json()["error"])

    def test_missing_header_rejected(self):
        resp = self.post_workbook(self.upload_url, {"Workshops": [["Title", "Kind"], ["A", "B"]]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Workshop Name, Category", resp.json()["error"])

    def test_empty_sheet_rejected(self):
        resp = self.upload([])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("No data rows", resp.json()["error"])
