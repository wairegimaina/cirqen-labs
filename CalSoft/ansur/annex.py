"""Ansur's detailed PDF as Annex A of an Ansur session's certificate.

Appended before the certificate is stored as issued, so the one SHA-256 of
the issued copy covers the annex too. An Ansur PDF whose fingerprint no
longer matches the one taken at import is not attached, and the certificate
says the annex is missing rather than attaching something unverified.
"""
from __future__ import annotations

import hashlib
import io
import logging

logger = logging.getLogger(__name__)


def _cover_page(job, session) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    page = canvas.Canvas(buf, pagesize=A4)
    width, height = A4
    page.setFont("Helvetica-Bold", 16)
    page.drawString(20 * mm, height - 30 * mm, "Annex A: Fluke Ansur detailed report")
    page.setFont("Helvetica", 10)
    lines = [
        f"Certificate: {session.certificate_number or 'pending'}",
        f"Ansur job: {job.job_number}    Template: {job.template_file}",
        f"Device serial: {session.device_serial}",
        f"Ansur record SHA-256: {job.record_sha256}",
        f"Ansur PDF SHA-256: {job.pdf_sha256}",
        "",
        "The following pages are Ansur's own report of the test, as produced by Ansur when the",
        "record was imported. The results, uncertainty and verdict on this certificate are",
        "computed by the laboratory; where Ansur's Pass/Fail differs, the certificate's verdict applies.",
    ]
    y = height - 42 * mm
    for line in lines:
        page.drawString(20 * mm, y, line)
        y -= 6 * mm
    page.showPage()
    page.save()
    return buf.getvalue()


def with_annex(session, certificate: bytes) -> bytes:
    """``certificate`` with the Ansur annex appended, when the session has one."""
    if getattr(session, "source", "manual") != "ansur":
        return certificate
    from CalSoft.models import AnsurJob

    job = AnsurJob.objects.filter(session=session).first()
    if job is None or not job.pdf_copy:
        return certificate
    try:
        with job.pdf_copy.open("rb") as handle:
            ansur_pdf = handle.read()
    except OSError:
        logger.warning("Ansur PDF for job %s is missing from storage", job.job_number)
        return certificate
    if job.pdf_sha256 and hashlib.sha256(ansur_pdf).hexdigest() != job.pdf_sha256:
        logger.error("Ansur PDF for job %s failed its fingerprint check; not attached", job.job_number)
        return certificate
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        logger.warning("pypdf is not installed; Ansur annex not attached")
        return certificate
    try:
        writer = PdfWriter()
        for part in (certificate, _cover_page(job, session), ansur_pdf):
            for page in PdfReader(io.BytesIO(part)).pages:
                writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()
    except Exception:
        logger.exception("Could not attach the Ansur annex for job %s", job.job_number)
        return certificate
