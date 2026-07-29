"""CalSoft.pdf_generators.styles — ReportLab paragraph/table style setup."""
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



class StylesMixin:
    def setup_custom_styles(self):
        """Setup custom paragraph styles for the certificate."""

        # Hospital name style for header
        self.styles.add(ParagraphStyle(
            name='HeaderHospitalName',
            fontSize=18,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
            spaceAfter=3,
            textColor=HexColor('#1f2937')
        ))

        # Header subtitle
        self.styles.add(ParagraphStyle(
            name='HeaderSubtitle',
            fontSize=12,
            fontName='Helvetica',
            alignment=TA_CENTER,
            spaceAfter=2,
            textColor=HexColor('#374151')
        ))

        # Certificate title style
        self.styles.add(ParagraphStyle(
            name='CertificateTitle',
            fontSize=16,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
            spaceAfter=10,
        ))

        # Section headers with background
        self.styles.add(ParagraphStyle(
            name='HeaderText',
            fontSize=9,
            fontName='Helvetica-Bold',
            alignment=TA_LEFT,
            textColor=HexColor('#000000')
        ))

        # Parameter title style
        self.styles.add(ParagraphStyle(
            name='ParameterTitle',
            fontSize=11,
            fontName='Helvetica-Bold',
            alignment=TA_LEFT,
            textColor=HexColor('#1f2937'),
            spaceAfter=4,
            backColor=HexColor('#f3f4f6')
        ))

        # Certificate number text style
        self.styles.add(ParagraphStyle(
            name='CertNumberText',
            fontSize=9,
            fontName='Helvetica-Bold',
            alignment=TA_LEFT,
            textColor=HexColor('#000000')
        ))

        # Normal text
        self.styles.add(ParagraphStyle(
            name='NormalText',
            fontSize=9,
            alignment=TA_LEFT,
            spaceAfter=3,
            fontName='Helvetica'
        ))

        # Centered text
        self.styles.add(ParagraphStyle(
            name='CenteredText',
            parent=self.styles['NormalText'],
            alignment=TA_CENTER
        ))

        # QR Code caption style
        self.styles.add(ParagraphStyle(
            name='QRCaption',
            fontSize=8,
            fontName='Helvetica',
            alignment=TA_CENTER,
            textColor=HexColor('#6b7280')
        ))

        # Failed report styles
        self.styles.add(ParagraphStyle(
            name='FailedTitle',
            fontSize=16,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
            spaceAfter=10,
            textColor=HexColor('#dc2626'),
            borderWidth=2,
            borderColor=HexColor('#dc2626'),
            borderPadding=8
        ))

        self.styles.add(ParagraphStyle(
            name='FailureWarning',
            fontSize=11,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
            spaceAfter=8,
            textColor=HexColor('#dc2626'),
            backColor=HexColor('#fef2f2')
        ))

        self.styles.add(ParagraphStyle(
            name='FailureText',
            fontSize=9,
            fontName='Helvetica',
            alignment=TA_LEFT,
            spaceAfter=4,
            textColor=HexColor('#b91c1c')
        ))

        # Enhanced signature styles
        self.styles.add(ParagraphStyle(
            name='SignatureTitle',
            fontSize=10,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
            spaceAfter=4,
            textColor=HexColor('#1f2937')
        ))

        self.styles.add(ParagraphStyle(
            name='SignatureDetails',
            fontSize=8,
            fontName='Helvetica',
            alignment=TA_CENTER,
            spaceAfter=2,
            textColor=HexColor('#4b5563')
        ))

        # Blurred name style — simulates blur via stacked grey layers
        self.styles.add(ParagraphStyle(
            name='BlurredName',
            fontSize=9,
            fontName='Helvetica',
            alignment=TA_LEFT,
            spaceAfter=3,
            textColor=HexColor('#cccccc'),
            backColor=HexColor('#cccccc'),  # text hidden behind matching bg
        ))

        # Drift analysis styles
        self.styles.add(ParagraphStyle(
            name='DriftNormal',
            fontSize=9,
            fontName='Helvetica',
            alignment=TA_LEFT,
            spaceAfter=3,
            textColor=HexColor('#1f2937')
        ))
        self.styles.add(ParagraphStyle(
            name='DriftWarning',
            fontSize=9,
            fontName='Helvetica-Bold',
            alignment=TA_LEFT,
            spaceAfter=3,
            textColor=HexColor('#b45309'),
            backColor=HexColor('#fffbeb')
        ))
        self.styles.add(ParagraphStyle(
            name='DriftGood',
            fontSize=9,
            fontName='Helvetica-Bold',
            alignment=TA_LEFT,
            spaceAfter=3,
            textColor=HexColor('#065f46'),
            backColor=HexColor('#d1fae5')
        ))
        self.styles.add(ParagraphStyle(
            name='DriftBad',
            fontSize=9,
            fontName='Helvetica-Bold',
            alignment=TA_LEFT,
            spaceAfter=3,
            textColor=HexColor('#991b1b'),
            backColor=HexColor('#fee2e2')
        ))

    def styled_header_table(self, title):
        """Create a header table with background color but no borders."""
        table = Table([[Paragraph(title, self.styles['HeaderText'])]], colWidths=[2.3*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), HexColor('#d9d9d9')),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        return table
