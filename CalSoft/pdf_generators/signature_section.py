"""CalSoft.pdf_generators.signature_section — technician/approver signature section."""
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



class SignatureMixin:
    def build_signature_section(self):
        """Build the official signature section with both performed_by and approved_by signatures"""
        elements = []

        # Get the users
        performed_by = getattr(self.session, 'performed_by', None)
        approved_by = getattr(self.session, 'approved_by', None)

        # Create a two-column layout for signatures
        signature_data = []

        # Performed By column
        performed_by_content = self._build_signature_column(
            performed_by,
            "PERFORMED BY",
            self.session.timestamp if self.session.timestamp else None
        )

        # Approved By column (only if approved)
        approved_by_content = self._build_signature_column(
            approved_by if self.session.status == "approved" else None,
            "APPROVED BY",
            self.session.approved_at if self.session.approved_at else None
        )

        # Add both columns to the table
        signature_data.append([performed_by_content, approved_by_content])

        signature_table = Table(signature_data, colWidths=[3.45*inch, 3.45*inch])
        signature_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ]))

        elements.append(Spacer(1, 12))
        elements.append(signature_table)

        return elements

    def _build_signature_column(self, user, title, timestamp):
        """
        Build a signature column for a specific user - FIXED VERSION

        This uses the new SignatureImageLoader class for reliable signature loading.
        """
        column_elements = []

        # Column title
        column_elements.append(Paragraph(f"<b>{title}</b>", self.styles['HeaderText']))
        column_elements.append(Spacer(1, 8))

        if user:
            logger.info(f"[SIGNATURE] Building signature column for: {user.username}")

            # Try to load signature image using the new loader
            sig_img, status = SignatureImageLoader.load_signature(
                user,
                width=2.5*inch,
                height=0.8*inch
            )

            if sig_img:
                # Signature loaded successfully
                logger.info(f"[SIGNATURE] ✓ Signature loaded: {status}")
                column_elements.append(sig_img)
                column_elements.append(Spacer(1, 4))
            else:
                # Signature loading failed - use text fallback
                logger.warning(f"[SIGNATURE] ✗ Failed to load signature: {status}")
                logger.info(f"[SIGNATURE] Using text signature fallback")

                # Add text signature
                self._add_text_signature(column_elements, user)

            # Add user details
            user_details = []

            # Get user's full name
            full_name = SignatureImageLoader.get_user_full_name(user)
            user_details.append(f"<b>Eng:</b> {full_name}")

            # Add timestamp if available
            if timestamp:
                user_details.append(f"<b>Date:</b> {timestamp.astimezone(EAT).strftime('%Y-%m-%d %H:%M EAT')}")

            column_elements.append(Paragraph("<br/>".join(user_details), self.styles['NormalText']))

        else:
            # No user specified - show empty signature area
            if title == "PERFORMED BY":
                column_elements.append(Paragraph("Not specified", self.styles['NormalText']))
            else:
                column_elements.append(Paragraph("Not approved", self.styles['NormalText']))

        return column_elements

    def _add_text_signature(self, elements, user):
        """Add a text-based signature representation"""
        try:
            # Get user's full name
            full_name = SignatureImageLoader.get_user_full_name(user)

            elements.append(Paragraph(
                f"<i>Digitally signed by:</i><br/><b>{full_name}</b>",
                self.styles['NormalText']
            ))
            elements.append(Spacer(1, 4))
        except Exception as e:
            logger.error(f"Error getting user signature text: {str(e)}")
            elements.append(Paragraph(
                f"<i>Digitally signed by:</i><br/><b>{user.username}</b>",
                self.styles['NormalText']
            ))
            elements.append(Spacer(1, 4))
