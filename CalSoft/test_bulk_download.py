"""Bulk certificate download: a real zip, one PDF per approved session."""
import io
import zipfile
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from core.tests.excel_helpers import ExcelImportTestMixin
from workshop.models import Workshop

MODULE = "CalSoft.view_modules.certificates"


class Sessions(list):
    def exists(self):
        return bool(self)

    def count(self):
        return len(self)


def _session(number):
    return SimpleNamespace(id=number, pk=number, certificate_number=number, Department=None, device_serial="",
                           procedure=None, device_model="", device_description="")


class BulkDownloadTests(ExcelImportTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        workshop = Workshop.objects.create(name="Cal Lab", category="calibration_center")
        self.client.force_login(self.make_user("tech", "Tech", workshop=workshop, level="Engineer"))
        self.url = reverse("calibration:bulk_certificates_download")

    def _get(self, sessions, **params):
        with mock.patch(f"{MODULE}.get_accessible_sessions", return_value=(Sessions(sessions), None)), \
             mock.patch(f"{MODULE}.issued_pdf", side_effect=lambda s, u, build: f"%PDF {s.id}".encode()):
            return self.client.get(self.url, params)

    def test_zip_holds_one_pdf_per_session(self):
        response = self._get([_session("BNH-0001"), _session("BNH-0002")],
                             date_from="2026-09-01", date_to="2026-09-07")
        self.assertEqual(response["Content-Type"], "application/zip")
        self.assertIn("certificates_2026-09-01_to_2026-09-07.zip", response["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(b"".join(response.streaming_content))) as z:
            self.assertEqual(sorted(z.namelist()), ["certificate_BNH-0001.pdf", "certificate_BNH-0002.pdf"])
            self.assertEqual(z.read("certificate_BNH-0002.pdf"), b"%PDF BNH-0002")

    def test_check_only_counts(self):
        self.assertEqual(self._get([_session("BNH-0001")], check_only="1").json(), {"count": 1})

    def test_nothing_in_range(self):
        self.assertIn("error", self._get([]).json())
