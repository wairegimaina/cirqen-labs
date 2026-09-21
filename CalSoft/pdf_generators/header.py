"""CalSoft.pdf_generators.header — page header, logo, title and certificate-number blocks."""
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
from core.branding import contact_line, organisation_name



class HeaderMixin:
    def draw_header(self, canvas, doc):
        """Draw the header on each page."""
        canvas.saveState()

        # Header background
        canvas.setFillColor(HexColor('#f8fafc'))
        canvas.rect(0, self.page_height - 110, self.page_width, 110, fill=1, stroke=0)

        # Header border
        canvas.setStrokeColor(HexColor('#e5e7eb'))
        canvas.setLineWidth(1)
        canvas.line(0, self.page_height - 110, self.page_width, self.page_height - 110)

        # ── Logo (top-left) ──────────────────────────────────────────────────
        # The logo occupies a 80×80 pt box, vertically centred inside the
        # 110 pt header band, with a 15 pt left margin.
        LOGO_W = 80
        LOGO_H = 80
        LOGO_X = 15                                  # distance from left edge
        LOGO_Y = self.page_height - 110 + (110 - LOGO_H) / 2  # vertically centred

        logo_path = self.logo_path   # resolved once in __init__
        logo_drawn = False

        if logo_path:
            try:
                canvas.drawImage(
                    logo_path,
                    LOGO_X,
                    LOGO_Y,
                    width=LOGO_W,
                    height=LOGO_H,
                    preserveAspectRatio=True,
                    anchor='c',      # centre-anchor keeps image inside the box
                    mask='auto',
                )
                logo_drawn = True
                logger.debug(f"[HEADER] Logo rendered from {logo_path}")
            except Exception as _logo_err:
                logger.warning(f"[HEADER] Logo draw failed ({logo_path}): {_logo_err}")

        if not logo_drawn:
            # Fallback: draw a rounded rectangle containing the lab's initials
            # Derive initials from LABORATORY_INFO if available, else use 'KNH'
            # The configured organisation name, not a hardcoded one. This
            # previously fell back to a specific hospital's name without
            # logging, so a misconfigured deployment silently printed another
            # organisation's initials on its certificates.
            try:
                from .pdf_config import PDFConfiguration
                lab_name = PDFConfiguration.LABORATORY_INFO.get('name') or organisation_name()
            except Exception as exc:
                logger.warning(
                    "[HEADER] Could not read laboratory name from PDFConfiguration "
                    "(%s); using the configured organisation name instead.", exc
                )
                lab_name = organisation_name()

            initials = ''.join(w[0] for w in lab_name.split() if w[0].isupper())[:3]
            if not initials:
                initials = (lab_name.strip()[:3] or 'CAL').upper()

            # Outer rounded box
            canvas.setStrokeColor(HexColor('#1e40af'))
            canvas.setFillColor(HexColor('#dbeafe'))
            canvas.roundRect(LOGO_X, LOGO_Y, LOGO_W, LOGO_H, radius=6, fill=1, stroke=1)

            # Initials text centred inside
            canvas.setFillColor(HexColor('#1e40af'))
            font_size = 20 if len(initials) <= 3 else 16
            canvas.setFont('Helvetica-Bold', font_size)
            canvas.drawCentredString(
                LOGO_X + LOGO_W / 2,
                LOGO_Y + LOGO_H / 2 - font_size / 3,
                initials,
            )

            logger.debug("[HEADER] Fallback initials badge rendered.")

        # Hospital name and details
        canvas.setFillColor(HexColor('#1f2937'))
        canvas.setFont('Helvetica-Bold', 18)
        canvas.drawCentredString(self.page_width / 2, self.page_height - 35, ' CALIBRATION CERTIFICATE')

        canvas.setFillColor(HexColor('#374151'))
        canvas.setFont('Helvetica', 11)
        canvas.drawCentredString(self.page_width / 2, self.page_height - 50, 'Biomedical Engineering Department')
        canvas.drawCentredString(self.page_width / 2, self.page_height - 65, 'Issued By: Btwelve CALIBRATION LABORATORY')

        # Contact information
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(HexColor('#6b7280'))
        canvas.drawCentredString(self.page_width / 2, self.page_height - 80,
                               contact_line("ISO/IEC 17025:2017"))

        # QR Code in header (top right)
        qr_path = self.generate_qr_code()
        if qr_path and os.path.exists(qr_path):
            try:
                qr_size = 70
                qr_x = self.page_width - qr_size - 20
                qr_y = self.page_height - qr_size - 20
                canvas.drawImage(qr_path, qr_x, qr_y, qr_size, qr_size)

                # QR Code caption
                canvas.setFont('Helvetica', 7)
                canvas.setFillColor(HexColor('#6b7280'))
                canvas.drawCentredString(qr_x + qr_size/2, qr_y - 8, 'Scan to Verify')
                canvas.drawCentredString(qr_x + qr_size/2, qr_y - 18, 'Certificate')

            except Exception as e:
                logger.error(f"Error adding QR code to header: {str(e)}")

        canvas.restoreState()

    def _get_logo_path(self):
        """
        Locate the organisation logo by scanning every plausible directory.

        Search order (first match wins):
          1. Explicit path stored in pdf_config.LABORATORY_INFO['logo_path']
          2. STATIC_ROOT / images /
          3. Each entry in STATICFILES_DIRS / images /
          4. MEDIA_ROOT / images /  (logo uploaded via admin)
          5. BASE_DIR / static / images /
          6. Per-app static directories discovered via Django's finders
          7. Current-working-directory fallback

        Accepted file names: logo.png, logo.jpg, knh_logo.png,
        dark.png, hospital_logo.png (case-insensitive on lookup).
        """
        logo_names = [
            'logo.png', 'logo.jpg', 'logo.jpeg', 'equiper-logo.png', 'equiper-logo.jpg',
            'knh_logo.png', 'knh_logo.jpg',
            'dark.png', 'hospital_logo.png',
        ]

        def _first_existing(*parts):
            p = os.path.join(*parts)
            return p if os.path.isfile(p) else None

        candidates = []

        # ── 1. Explicit path from pdf_config ─────────────────────────────────
        try:
            from .pdf_config import PDFConfiguration  # relative import (same package)
            lab_info = PDFConfiguration.LABORATORY_INFO
            explicit = lab_info.get('logo_path') or lab_info.get('logo')
            if explicit:
                # Could be absolute or relative to BASE_DIR / STATIC_ROOT
                candidates.append(explicit)
                if hasattr(settings, 'BASE_DIR'):
                    candidates.append(os.path.join(str(settings.BASE_DIR), explicit))
                if hasattr(settings, 'STATIC_ROOT') and settings.STATIC_ROOT:
                    candidates.append(os.path.join(settings.STATIC_ROOT, explicit))
        except Exception as exc:
            # Not fatal: the next candidate source is tried below. Logged at
            # debug so a missing logo can be traced without noise in normal runs.
            logger.debug("[HEADER] Explicit logo path unusable: %s", exc)

        # ── 2. STATIC_ROOT ────────────────────────────────────────────────────
        if hasattr(settings, 'STATIC_ROOT') and settings.STATIC_ROOT:
            sr = settings.STATIC_ROOT
            for name in logo_names:
                candidates += [
                    os.path.join(sr, 'images', name),
                    os.path.join(sr, name),
                    os.path.join(sr, 'logos', name),
                    os.path.join(sr, 'img', name),
                ]

        # ── 3. STATICFILES_DIRS ───────────────────────────────────────────────
        if hasattr(settings, 'STATICFILES_DIRS') and settings.STATICFILES_DIRS:
            for sdir in settings.STATICFILES_DIRS:
                sdir = str(sdir)
                for name in logo_names:
                    candidates += [
                        os.path.join(sdir, 'images', name),
                        os.path.join(sdir, name),
                        os.path.join(sdir, 'logos', name),
                        os.path.join(sdir, 'img', name),
                    ]

        # ── 4. MEDIA_ROOT ─────────────────────────────────────────────────────
        if hasattr(settings, 'MEDIA_ROOT') and settings.MEDIA_ROOT:
            mr = settings.MEDIA_ROOT
            for name in logo_names:
                candidates += [
                    os.path.join(mr, 'images', name),
                    os.path.join(mr, 'logos', name),
                    os.path.join(mr, name),
                ]

        # ── 5. BASE_DIR / static ──────────────────────────────────────────────
        if hasattr(settings, 'BASE_DIR') and settings.BASE_DIR:
            bd = str(settings.BASE_DIR)
            for name in logo_names:
                candidates += [
                    os.path.join(bd, 'static', 'images', name),
                    os.path.join(bd, 'static', 'logos', name),
                    os.path.join(bd, 'static', 'img', name),
                    os.path.join(bd, 'staticfiles', 'images', name),
                ]

        # ── 6. Per-app static dirs via Django's finders ───────────────────────
        try:
            from django.contrib.staticfiles.finders import get_finders
            for finder in get_finders():
                for name in logo_names:
                    for sub in ('images', 'logos', 'img', ''):
                        rel = os.path.join(sub, name) if sub else name
                        try:
                            result = finder.find(rel)
                            if result:
                                # find() may return a list (AllFilesFinder) or a string
                                if isinstance(result, (list, tuple)):
                                    candidates += list(result)
                                else:
                                    candidates.append(result)
                        except Exception as exc:
                            logger.debug("[HEADER] Static finder %s failed: %s", finder, exc)
        except Exception as exc:
            logger.debug("[HEADER] Static finders unavailable: %s", exc)

        # ── 7. CWD fallback ───────────────────────────────────────────────────
        cwd = os.getcwd()
        for name in logo_names:
            candidates += [
                os.path.join(cwd, 'static', 'images', name),
                os.path.join(cwd, name),
            ]

        # Return the first candidate that actually exists on disk
        for p in candidates:
            if p and os.path.isfile(p):
                logger.debug(f"[HEADER] Logo found at: {p}")
                return p

        logger.warning("[HEADER] Logo file not found in any search location.")
        return None

    def draw_page_decoration(self, canvas, doc):
        """Draw page decorations: header and footer. Watermark handled by _LogoWatermarkCanvas."""

        # ── HEADER ───────────────────────────────────────────────────────────
        self.draw_header(canvas, doc)

        # ── FOOTER ───────────────────────────────────────────────────────────
        canvas.saveState()
        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(HexColor('#6b7280'))
        page_num = f"Page {canvas.getPageNumber()}"
        canvas.drawRightString(self.page_width - self.right_margin, 20, page_num)
        canvas.drawCentredString(self.page_width / 2, 20, f"Generated: {self._now_eat().strftime('%Y-%m-%d %H:%M EAT')}")
        canvas.restoreState()

    def build_certificate_title(self):
        """Build the certificate title section."""
        elements = []

        # ── DECLINED banner (highest priority) ──────────────────────────────
        is_declined = self.is_declined
        if is_declined:
            rejection_reason   = self.context.get('rejection_reason', 'Not specified')
            rejection_comments = self.context.get('rejection_comments', 'No comments provided')
            rejected_by        = self.context.get('rejected_by', 'Unknown')
            rejected_at        = self.context.get('rejected_at', 'Unknown')

            declined_title_table = Table(
                [[Paragraph("DECLINED CERTIFICATE", self.styles['FailedTitle'])]],
                colWidths=[6.9 * inch]
            )
            declined_title_table.setStyle(TableStyle([
                ('BACKGROUND',    (0, 0), (-1, -1), HexColor('#450a0a')),
                ('BOX',           (0, 0), (-1, -1), 2.5, HexColor('#7f1d1d')),
                ('LEFTPADDING',   (0, 0), (-1, -1), 12),
                ('RIGHTPADDING',  (0, 0), (-1, -1), 12),
                ('TOPPADDING',    (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ]))
            elements.append(declined_title_table)

            declined_detail_text = (
                f"🚫 THIS CERTIFICATE HAS BEEN DECLINED / REJECTED<br/>"
                f"<b>Reason:</b> {rejection_reason}<br/>"
                f"<b>Comments:</b> {rejection_comments}<br/>"
                f"<b>Rejected by:</b> {rejected_by} &nbsp;|&nbsp; <b>Date:</b> {rejected_at}"
            )
            declined_detail_table = Table(
                [[Paragraph(declined_detail_text, self.styles['FailureWarning'])]],
                colWidths=[6.9 * inch]
            )
            declined_detail_table.setStyle(TableStyle([
                ('BACKGROUND',    (0, 0), (-1, -1), HexColor('#fef2f2')),
                ('BOX',           (0, 0), (-1, -1), 1, HexColor('#fca5a5')),
                ('LEFTPADDING',   (0, 0), (-1, -1), 10),
                ('RIGHTPADDING',  (0, 0), (-1, -1), 10),
                ('TOPPADDING',    (0, 0), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(Spacer(1, 6))
            elements.append(declined_detail_table)
            elements.append(Spacer(1, 15))

        if self.conformity_failed:
            # The document is a failure report whenever conformity failed, not
            # only when the failure rate clears 40%. The rate still tunes the
            # wording below, but it no longer decides the verdict.
            title_table = Table([
                [Paragraph("CALIBRATION FAILURE REPORT", self.styles['FailedTitle'])]
            ], colWidths=[6.9*inch])

            title_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), HexColor('#fef2f2')),
                ('BOX', (0, 0), (-1, -1), 2, HexColor('#dc2626')),
                ('LEFTPADDING', (0, 0), (-1, -1), 12),
                ('RIGHTPADDING', (0, 0), (-1, -1), 12),
                ('TOPPADDING', (0, 0), (-1, -1), 12),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
            ]))

            elements.append(title_table)

            # Add failure warning
            failed = self.failure_stats['failed_readings']
            total = self.failure_stats['total_readings']
            if self.is_failed_report:
                warning_text = (
                    f"DEVICE FAILED CALIBRATION - {failed} of {total} readings "
                    f"outside tolerance ({self.failure_stats['overall_failure_rate']:.1%}); "
                    f"remove from service and repair"
                )
            else:
                warning_text = (
                    f"DEVICE FAILED CALIBRATION - {failed} of {total} readings "
                    f"outside tolerance ({self.failure_stats['overall_failure_rate']:.1%})"
                )
            warning_table = Table([
                [Paragraph(warning_text, self.styles['FailureWarning'])]
            ], colWidths=[6.9*inch])

            warning_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), HexColor('#fef2f2')),
                ('BOX', (0, 0), (-1, -1), 1, HexColor('#fca5a5')),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ]))

            elements.append(Spacer(1, 8))
            elements.append(warning_table)
            elements.append(Spacer(1, 15))
        else:
            # Normal certificate title (optional - can be left empty)
            pass

        return elements

    def build_certificate_number(self):
        """Build the certificate/reference number section with background color."""
        if self.is_declined:
            # Declined documents show a Reference Number, not a Certificate Number
            ref_number = self.reference_number or "N/A"
            cert_data = [
                [
                    Paragraph("Reference Number:", self.styles['CertNumberText']),
                    Paragraph(ref_number, self.styles['NormalText']),
                    Paragraph("Issue Date:", self.styles['CertNumberText']),
                    Paragraph(
                        self.session.timestamp.astimezone(EAT).strftime('%Y-%m-%d') if self.session.timestamp else 'N/A',
                        self.styles['NormalText']
                    )
                ]
            ]
            bg_color = HexColor('#fecaca')   # light red to visually distinguish
        else:
            # A pending document shows its reference, not "N/A": the number
            # is not missing, it has not been issued yet.
            cert_number = (
                str(self.certificate_number) if self.certificate_number
                else (str(self.reference_number) if getattr(self, "reference_number", None) else "N/A")
            )
            cert_data = [
                [
                    Paragraph("Certificate Number:", self.styles['CertNumberText']),
                    Paragraph(cert_number, self.styles['NormalText']),
                    Paragraph("Issue Date:", self.styles['CertNumberText']),
                    Paragraph(
                        self.session.timestamp.astimezone(EAT).strftime('%Y-%m-%d') if self.session.timestamp else 'N/A',
                        self.styles['NormalText']
                    )
                ]
            ]
            bg_color = HexColor('#d9d9d9')

        cert_table = Table(cert_data, colWidths=[1.2*inch, 2*inch, 1*inch, 1.7*inch])
        cert_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), bg_color),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, 0), 'Helvetica-Bold'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))

        return [cert_table, Spacer(1, 10)]
