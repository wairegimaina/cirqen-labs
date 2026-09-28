"""Reviewing an Ansur session: what the reviewer sees, the acknowledgement
when Ansur's verdict differs, and Ansur's report as Annex A."""
import hashlib
import io

from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from CalSoft.ansur.annex import with_annex
from CalSoft.ansur.importer import import_record
from CalSoft.ansur.testing import AnsurFixture, record_xml
from CalSoft.models import CalibrationAuditLog


def one_page_pdf(text):
    buf = io.BytesIO()
    page = canvas.Canvas(buf)
    page.drawString(100, 700, text)
    page.showPage()
    page.save()
    return buf.getvalue()


class AnsurReviewTests(AnsurFixture, TestCase):
    def setUp(self):
        self.make_ansur_site()
        self.job = self.make_job()

    def _session(self, **record):
        return import_record(self.job, record_xml(job=self.job.job_number, **record))

    def _approve(self, session, **data):
        self.client.force_login(self.hod)
        return self.client.post(reverse("calibration:approve_calibration_session_ajax", args=[session.pk]), data)

    def test_details_show_the_source_and_both_verdicts(self):
        session = self._session(leakage="497")
        self.client.force_login(self.hod)
        data = self.client.get(reverse("calibration:api_session_details", args=[session.pk])).json()
        self.assertEqual(data["source"], "ansur")
        self.assertEqual(data["ansur"]["job_number"], self.job.job_number)
        self.assertEqual(data["ansur"]["disagreements"], 1)
        self.assertEqual(data["ansur"]["checks"], [{"name": "Visual inspection", "status": "Pass"}])
        leakage = next(r for r in data["readings"] if r["parameter"] == "Earth leakage")
        self.assertEqual((leakage["verdict"], leakage["ansur_status"], leakage["limit_type"]),
                         ("INDETERMINATE", "Pass", "upper"))

    def test_a_disagreement_must_be_acknowledged(self):
        session = self._session(leakage="497")
        response = self._approve(session)
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()["needs_ansur_acknowledgement"])
        session.refresh_from_db()
        self.assertEqual(session.status, "pending_review")

        self.assertTrue(self._approve(session, acknowledge_ansur="1").json()["success"])
        session.refresh_from_db()
        self.assertEqual(session.status, "approved_pending_certificate")
        self.assertTrue(CalibrationAuditLog.objects.filter(
            session=session, description__contains="acknowledged by the reviewer").exists())

    def test_no_acknowledgement_needed_when_they_agree(self):
        self.assertTrue(self._approve(self._session()).json()["success"])

    def test_the_performer_still_cannot_approve(self):
        session = self._session()
        self.client.force_login(self.tech)
        response = self.client.post(reverse("calibration:approve_calibration_session_ajax", args=[session.pk]))
        self.assertEqual(response.status_code, 403)

    def _with_ansur_pdf(self, session, data=b""):
        data = data or one_page_pdf("Ansur report")
        self.job.refresh_from_db()
        self.job.pdf_copy.save("a.pdf", ContentFile(data), save=False)
        self.job.pdf_sha256 = hashlib.sha256(data).hexdigest()
        self.job.save()
        return data

    def test_annex_is_appended_with_a_cover_page(self):
        session = self._session()
        self._with_ansur_pdf(session)
        merged = with_annex(session, one_page_pdf("Certificate"))
        pages = PdfReader(io.BytesIO(merged)).pages
        self.assertEqual(len(pages), 3)
        self.assertIn("Annex A", pages[1].extract_text())
        self.assertIn(self.job.job_number, pages[1].extract_text())
        self.assertIn("Ansur report", pages[2].extract_text())

    def test_an_altered_ansur_pdf_is_not_attached(self):
        session = self._session()
        self._with_ansur_pdf(session)
        self.job.pdf_sha256 = "0" * 64
        self.job.save()
        certificate = one_page_pdf("Certificate")
        self.assertEqual(with_annex(session, certificate), certificate)

    def test_manual_sessions_are_unchanged(self):
        session = self._session()
        session.source = "manual"
        certificate = one_page_pdf("Certificate")
        self.assertEqual(with_annex(session, certificate), certificate)

    def test_ansur_pdf_download_checks_its_fingerprint(self):
        session = self._session()
        data = self._with_ansur_pdf(session)
        self.client.force_login(self.hod)
        url = reverse("calibration:ansur_session_pdf", args=[session.pk])
        response = self.client.get(url)
        self.assertEqual(b"".join(response.streaming_content), data)
        self.job.pdf_sha256 = "0" * 64
        self.job.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_certificate_pdf_carries_the_note_and_annex(self):
        session = self._session()
        self._with_ansur_pdf(session)
        self.client.force_login(self.hod)
        response = self.client.get(reverse("calibration:generate_comprehensive_certificate", args=[session.pk]))
        self.assertEqual(response.status_code, 200)
        content = response.content if hasattr(response, "content") else b"".join(response.streaming_content)
        reader = PdfReader(io.BytesIO(content))
        text = " ".join(page.extract_text() for page in reader.pages)
        self.assertIn("MEASURED WITH FLUKE ANSUR", text)
        self.assertIn("CHECKS RECORDED IN ANSUR", text)
        self.assertIn("Visual inspection", text)
        self.assertIn("Annex A", text)
        self.assertIn("Ansur report", text)
