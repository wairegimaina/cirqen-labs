"""CalSoft.pdf_generators.certificate — the B-12 certificate generator (composed from mixins)."""
"""CalSoft.pdf_generators — the B-12 hospital certificate generator + thin wrapper."""
import base64
import calendar
import io
import os
from datetime import datetime, timedelta
from decimal import Decimal
try:
    from zoneinfo import ZoneInfo
    EAT = ZoneInfo('Africa/Nairobi')
except ImportError:
    import pytz
    EAT = pytz.timezone('Africa/Nairobi')
import matplotlib
matplotlib.use('Agg')
import logging
import matplotlib.pyplot as plt
import numpy as np
import qrcode
from django.conf import settings
from django.core.files.storage import default_storage
from django.utils import timezone
from PIL import Image as PILImage
from reportlab.graphics import renderPDF
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing, Line, Rect
from reportlab.lib import colors
from reportlab.lib.colors import HexColor, black, blue, gray, green, red, white
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, inch, mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether, PageBreak, PageTemplate, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
logger = logging.getLogger(__name__)

# sibling modules in this package
from .signatures import SignatureImageLoader
from .watermark import _LogoWatermarkCanvas

# concern mixins (methods composed onto the generator below)
from .styles import StylesMixin
from .results import ResultsMixin
from .qr import QrMixin
from .header import HeaderMixin
from .sections import SectionsMixin
from .analysis import AnalysisMixin
from .signature_section import SignatureMixin
from .drift import DriftMixin


class BtwelveHospitalCertificateGenerator(
    StylesMixin, ResultsMixin, QrMixin, HeaderMixin, SectionsMixin, AnalysisMixin, SignatureMixin, DriftMixin,
):
    """
    PDF certificate generator with styled headers, background colors, and QR code for authenticity.
    """

    def __init__(self, session, context=None):
        """Initialize the certificate generator with proper certificate number handling."""
        self.session = session
        self.context = context or {}
        self.styles = getSampleStyleSheet()
        self.setup_custom_styles()
        self.page_width, self.page_height = A4
        self.left_margin = 40
        self.right_margin = 40
        self.top_margin = 120
        self.bottom_margin = 40
        self.content_width = self.page_width - self.left_margin - self.right_margin

        # Declined flag — set before number resolution so helpers can branch on it
        self.is_declined = bool(self.context.get('is_declined', False))

        # For declined sessions use a reference number, not a certificate number.
        # The reference number comes from context (set by the view) or falls back
        # to a "REF-<pk>" pattern so the document is still traceable.
        # A document has exactly one identity, and which one depends on state:
        #   declined        -> a reference number; no certificate exists
        #   awaiting issue  -> a reference number; HQ has not allocated yet
        #   issued          -> the certificate number HQ allocated
        self.is_pending_number = False

        if self.is_declined:
            ref = (
                self.context.get('certificate_number')  # view passes DECLINED-<pk> here
                or (f"REF-{self.session.id}" if hasattr(self.session, 'id') else "REF-000")
            )
            self.reference_number = str(ref)
            self.certificate_number = None          # explicitly absent for declined docs
        else:
            self.certificate_number = self._get_certificate_number_from_session()
            if self.certificate_number:
                self.reference_number = None
            else:
                # Not an error: approval queues the request and HQ allocates.
                # The document stays traceable by session id, and is labelled
                # so nobody mistakes it for an issued certificate.
                self.is_pending_number = True
                self.reference_number = (
                    f"PENDING-{self.session.id}" if getattr(self.session, 'id', None)
                    else "PENDING"
                )

        # Calculate failure statistics
        self.failure_stats = self.calculate_failure_statistics()

        # Two distinct questions, previously conflated into is_failed_report:
        #
        #   conformity_failed — did the device fail calibration? Strict
        #       conformity: ONE reading outside tolerance fails the session.
        #       This is the verdict. It drives the banner, the QR status and
        #       the certificate notes.
        #
        #   is_failed_report  — is the failure widespread enough to warrant a
        #       maintenance/repair report rather than a certificate? A triage
        #       signal for the workshop. It must never decide conformity.
        #
        # Before this split, the 40% rate decided the verdict, so a device
        # failing up to 39% of its test points was certified PASSED while the
        # session record said it had failed.
        self.conformity_failed = self._determine_conformity_failure()
        self.is_failed_report = self.failure_stats['overall_failure_rate'] >= 0.40

        # Resolve logo once; reused by both the header and the watermark canvas
        self.logo_path = self._get_logo_path()

    def _determine_conformity_failure(self):
        """Strict conformity: any reading outside tolerance fails the session.

        Two sources are consulted because each catches something the other
        misses:

        * ``failed_readings`` counts the rows on this certificate, so the
          verdict always agrees with the table printed beneath it.
        * ``session.overall_pass`` additionally covers a test point that was
          left blank. No reading row is created for a missing point, so it is
          invisible to ``failed_readings``, but the session was still marked
          failed when it was submitted.

        A session whose ``overall_pass`` is None (never computed) is treated as
        failing only if a reading actually failed — absence of a flag is not
        evidence of conformity, but nor is it evidence of failure.
        """
        if self.failure_stats.get('failed_readings', 0) > 0:
            return True

        overall_pass = getattr(self.session, 'overall_pass', None)
        if overall_pass is False:
            return True

        return False

    def _get_certificate_number_from_session(self):
        """Return this session's certificate number, or None if none is issued.

        Certificate numbers are allocated by the HQ server only, so this
        retrieves and never invents. It previously had four fallbacks, three of
        which minted an identifier:

        * ``generate_certificate_number()`` allocated from the LOCAL sequence
          and saved it to the session — a second local allocation path, with
          the collision consequences described in
          ``view_modules/pending_sessions.py``;
        * a ``BNH-CAL-<yy>-<id>`` pattern formatted a UUID with ``:06d``, which
          raises ``TypeError``;
        * ``BNH-TEMP-0000`` and ``BNH-ERROR-0000`` printed a placeholder in the
          certificate-number field of a real document.

        A session with no number has not had one issued yet. Returning None
        lets the caller label the document as awaiting issue, which is true,
        instead of printing a number that means nothing.
        """
        try:
            if getattr(self.session, 'certificate_number', None):
                cert_num = str(self.session.certificate_number).strip()
                if cert_num and cert_num.lower() not in ('none', 'null'):
                    return cert_num

            context_number = self.context.get('certificate_number')
            if context_number:
                cert_num = str(context_number).strip()
                if cert_num and cert_num.lower() not in ('none', 'null'):
                    return cert_num

            logger.info(
                "Session %s has no certificate number yet; HQ has not issued one",
                getattr(self.session, 'id', '?'),
            )
            return None

        except Exception as e:
            logger.error(f"Error reading certificate number: {e}")
            return None

    def generate_certificate(self):
        """Generate the calibration certificate PDF with background colors and QR code."""
        buffer = io.BytesIO()

        doc = BaseDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=self.left_margin,
            rightMargin=self.right_margin,
            topMargin=self.top_margin,
            bottomMargin=self.bottom_margin,
        )

        main_frame = Frame(
            self.left_margin,
            self.bottom_margin,
            self.content_width,
            self.page_height - self.top_margin - self.bottom_margin,
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
            id='main_frame'
        )

        template = PageTemplate(
            id='certificate_template',
            frames=[main_frame],
            onPage=self.draw_page_decoration
        )

        doc.addPageTemplates([template])

        elements = []
        elements.extend(self.build_certificate_title())
        elements.extend(self.build_certificate_number())

        elements.extend(self.build_three_section_table())
        elements.extend(self.build_section_with_header("CALIBRATOR & STANDARD INFORMATION", self.build_calibrator_info_content()))
        elements.extend(self.build_section_with_header("LINEARITY ANALYSIS", self.build_linearity_content()))
        elements.extend(self.build_section_with_header("DRIFT ANALYSIS", self.build_drift_analysis_content()))
        elements.extend(self.build_section_with_header("CALIBRATION RESULTS & STATISTICS", self.build_results_table()))

        # Add failure analysis section if there are failures
        if self.is_failed_report or self.failure_stats['failed_parameters']:
            elements.extend(self.build_section_with_header("FAILURE ANALYSIS", self.build_failure_analysis()))

        elements.extend(self.build_section_with_header("UNCERTAINTY BUDGET", self.build_uncertainty_table()))
        elements.extend(self.build_section_with_header("TRACEABILITY", self.build_traceability_content()))
        elements.extend(self.build_section_with_header("NOTES", self.build_notes_content()))
        elements.extend(self.build_signature_section())
        elements.extend(self.build_footer())

        from functools import partial
        wm_canvas = partial(_LogoWatermarkCanvas, logo_path=self.logo_path)
        doc.build(elements, canvasmaker=wm_canvas)
        buffer.seek(0)
        return buffer

    def _now_eat(self):
        """Return current datetime in East Africa Time (UTC+3)."""
        from django.utils import timezone as dj_tz
        return dj_tz.now().astimezone(EAT)

    def build_footer(self):
        """Build certificate footer with verification information."""

        footer_style = ParagraphStyle(
            name='LightFooter',
            fontSize=9,
            fontName='Helvetica',
            alignment=TA_CENTER,
            textColor=HexColor('#6b7280'),
            spaceAfter=3,
        )

        # Get calibrated by information
        calibrated_by = 'Biomedical Engineering Team'
        if hasattr(self.session, 'performed_by') and self.session.performed_by:
            user = self.session.performed_by
            if hasattr(user, 'get_full_name') and user.get_full_name():
                calibrated_by = user.get_full_name()
            elif user.first_name or user.last_name:
                calibrated_by = f"{user.first_name} {user.last_name}".strip()
            else:
                calibrated_by = user.username

        # Three document states, three labels. A pending document has no
        # certificate number because HQ has not allocated one yet, and saying
        # so is better than printing an empty field.
        if self.is_declined:
            doc_id_label, doc_id_value = "Reference Number", self.reference_number
        elif self.is_pending_number:
            doc_id_label, doc_id_value = "Reference (certificate pending)", self.reference_number
        else:
            doc_id_label, doc_id_value = "Certificate Number", self.certificate_number

        # The footer claimed authenticity was "verified through QR code scanning
        # and digital signature". Nothing is signed — PDFConfig records
        # digital_signature as False — and the QR verifier reported valid for
        # any input. A certificate that overstates its own assurances is worse
        # than one that makes none, so this states what the QR code is for.
        footer_text = f"""
        {doc_id_label}: {doc_id_value} | Generated on: {self._now_eat().strftime('%Y-%m-%d %H:%M EAT')}<br/>
        Calibrated by: {calibrated_by}<br/>
        <i>The QR code carries a summary of this certificate for reference. Confirm
        the certificate number against the issuing record.</i>
        """

        return [Spacer(1, 6), Paragraph(footer_text, footer_style)]


def generate_btwelve_certificate(session, context=None):
    """Generate a certificate using Department location from context."""
    generator = BtwelveHospitalCertificateGenerator(session, context)
    return generator.generate_certificate()
