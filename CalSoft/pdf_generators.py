import base64
import calendar
import io
import os
from datetime import datetime, timedelta
from decimal import Decimal

try:
    from zoneinfo import ZoneInfo
    EAT = ZoneInfo("Africa/Nairobi")
except ImportError:
    import pytz
    EAT = pytz.timezone("Africa/Nairobi")
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
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    Image,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

logger = logging.getLogger(__name__)


class _LogoWatermarkCanvas(canvas.Canvas):
    """
    Custom canvas that draws the logo watermark OVER all page content by
    hooking into showPage() — which fires AFTER all flowables are placed.
    This ensures the watermark is fully visible and never hidden behind
    solid table backgrounds or white cell fills.
    """
    def __init__(self, filename, logo_path=None, wm_alpha=0.20, wm_scale=0.52, **kwargs):
        self._wm_logo_path = logo_path
        self._wm_alpha = wm_alpha
        self._wm_scale = wm_scale
        canvas.Canvas.__init__(self, filename, **kwargs)

    def showPage(self):
        """Draw watermark over the completed page, then advance."""
        if self._wm_logo_path:
            try:
                self.saveState()
                w, h = self._pagesize
                self.setFillAlpha(self._wm_alpha)
                self.setStrokeAlpha(self._wm_alpha)
                sz = min(w, h) * self._wm_scale
                self.drawImage(
                    self._wm_logo_path,
                    (w - sz) / 2,
                    (h - sz) / 2,
                    width=sz,
                    height=sz,
                    preserveAspectRatio=True,
                    mask='auto',
                )
                self.restoreState()
            except Exception as _wm_err:
                logger.warning(f"[WATERMARK] Overlay draw failed: {_wm_err}")
        canvas.Canvas.showPage(self)


# ==============================================================================
# SIGNATURE IMAGE LOADER - Handles Django ImageField signatures
# ==============================================================================

class SignatureImageLoader:
    """
    Handles loading signature images from Django's ImageField storage.
    Supports multiple fallback methods for maximum compatibility.

    The UserSignature model stores signatures as:
        signature_image = models.ImageField(upload_to='signatures/', null=True, blank=True)

    This class properly loads these images for PDF generation.
    """

    @staticmethod
    def load_signature(user, width=2.5*inch, height=0.8*inch):
        """
        Load signature image for a user with multiple fallback methods.
        PRIORITY ORDER:
        1. Base64 data from database (BEST for PyInstaller apps)
        2. ImageField file storage (fallback)

        Args:
            user: Django User object with signature relationship
            width: Desired width for signature image
            height: Desired height for signature image

        Returns:
            tuple: (Image object or None, status_message)
        """
        if not user:
            return None, "No user provided"

        logger.info(f"[SIGNATURE] Loading signature for: {user.username}")

        # Check if user has signature
        if not hasattr(user, 'signature'):
            logger.warning(f"[SIGNATURE] User {user.username} has no 'signature' attribute")
            return None, "User has no signature object"

        try:
            signature = user.signature
        except Exception as e:
            logger.error(f"[SIGNATURE] Error accessing signature: {e}")
            return None, f"Error accessing signature: {e}"

        # PRIORITY 1: Try base64 signature data (PREFERRED for PyInstaller apps)
        if hasattr(signature, 'signature_data') and signature.signature_data:
            logger.info(f"[SIGNATURE] Found base64 signature data")
            try:
                img = SignatureImageLoader._load_from_base64(signature.signature_data, width, height)
                if img:
                    logger.info(f"[SIGNATURE] ✓ Successfully loaded from base64 database")
                    return img, "Loaded from base64 database field"
            except Exception as e:
                logger.warning(f"[SIGNATURE] Base64 loading failed: {e}")

        # PRIORITY 2: Fall back to ImageField file storage
        if signature.signature_image:
            logger.info(f"[SIGNATURE] Signature image path: {signature.signature_image.name}")

            # Try multiple file loading methods
            loaders = [
                SignatureImageLoader._load_via_storage,
                SignatureImageLoader._load_via_file_path,
                SignatureImageLoader._load_via_media_root,
                SignatureImageLoader._load_via_pil,
            ]

            for loader_func in loaders:
                try:
                    img = loader_func(signature.signature_image, width, height)
                    if img:
                        method_name = loader_func.__name__
                        logger.info(f"[SIGNATURE] ✓ Successfully loaded via {method_name}")
                        return img, f"Loaded via {method_name}"
                except Exception as e:
                    logger.debug(f"[SIGNATURE] {loader_func.__name__} failed: {e}")
                    continue

        logger.error(f"[SIGNATURE] All loading methods failed for {user.username}")
        return None, "No signature data available"

    @staticmethod
    def _load_via_storage(image_field, width, height):
        """Method 1: Load signature using Django's storage backend."""
        logger.debug(f"[SIGNATURE] Method 1: Storage backend")

        if not default_storage.exists(image_field.name):
            raise FileNotFoundError(f"File not in storage: {image_field.name}")

        with default_storage.open(image_field.name, 'rb') as img_file:
            img_data = img_file.read()

        if len(img_data) == 0:
            raise ValueError("Image file is empty")

        logger.debug(f"[SIGNATURE] Read {len(img_data)} bytes from storage")

        pil_img = PILImage.open(io.BytesIO(img_data))
        pil_img = SignatureImageLoader._remove_background(pil_img)
        return SignatureImageLoader._pil_to_reportlab(pil_img, width, height)

    @staticmethod
    def _remove_background(pil_img, tolerance=30):
        """
        Remove the background from a signature image, making it transparent.

        Strategy:
          1. Convert to RGBA so we always have an alpha channel.
          2. Sample the four corner pixels to determine the background colour
             (signatures are almost always drawn on a white or light-grey canvas).
          3. Any pixel whose R, G, and B channels are all within *tolerance*
             of the sampled background colour is made fully transparent.
          4. The signature strokes, which are much darker, are left opaque.

        Args:
            pil_img:   A PIL.Image object (any mode).
            tolerance: Maximum per-channel distance from the background colour
                       that is still considered background (0-255). Default 30.

        Returns:
            A PIL.Image in RGBA mode with background pixels set to alpha=0.
        """
        img = pil_img.convert("RGBA")
        data = img.load()
        width, height = img.size

        # --- sample background colour from the four corners -----------------
        corner_pixels = [
            data[0, 0],
            data[width - 1, 0],
            data[0, height - 1],
            data[width - 1, height - 1],
        ]
        # Average the RGB of the corners (ignore any existing alpha)
        bg_r = sum(p[0] for p in corner_pixels) // 4
        bg_g = sum(p[1] for p in corner_pixels) // 4
        bg_b = sum(p[2] for p in corner_pixels) // 4

        # --- replace background pixels with transparent ----------------------
        for y in range(height):
            for x in range(width):
                r, g, b, a = data[x, y]
                if (abs(r - bg_r) <= tolerance and
                        abs(g - bg_g) <= tolerance and
                        abs(b - bg_b) <= tolerance):
                    data[x, y] = (r, g, b, 0)  # fully transparent

        return img

    @staticmethod
    def _pil_to_reportlab(pil_img, width, height):
        """Convert a PIL image (RGBA) to a ReportLab Image flowable."""
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        buf.seek(0)
        return Image(ImageReader(buf), width=width, height=height)

    @staticmethod
    def _load_from_base64(base64_data, width, height):
        """Method 0: Load signature from base64 string (PREFERRED for PyInstaller)."""
        logger.debug(f"[SIGNATURE] Method 0: Base64 decoding")

        # Remove data URI prefix if present
        if 'base64,' in base64_data:
            base64_str = base64_data.split('base64,')[1]
        else:
            base64_str = base64_data

        # Decode base64
        try:
            image_data = base64.b64decode(base64_str)
        except Exception as e:
            raise ValueError(f"Invalid base64 data: {e}")

        if len(image_data) == 0:
            raise ValueError("Decoded image data is empty")

        logger.debug(f"[SIGNATURE] Decoded {len(image_data)} bytes from base64")

        # Open with PIL, remove background, return transparent PNG
        pil_img = PILImage.open(io.BytesIO(image_data))
        pil_img = SignatureImageLoader._remove_background(pil_img)
        return SignatureImageLoader._pil_to_reportlab(pil_img, width, height)

    @staticmethod
    def _load_via_file_path(image_field, width, height):
        """Method 2: Load signature using direct file path (FileSystemStorage)."""
        logger.debug(f"[SIGNATURE] Method 2: Direct file path")

        if not hasattr(image_field, 'path'):
            raise AttributeError("Image field has no 'path' attribute")

        file_path = image_field.path

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")

        file_size = os.path.getsize(file_path)
        if file_size == 0:
            raise ValueError("Image file is empty")

        logger.debug(f"[SIGNATURE] Loading from path: {file_path} ({file_size} bytes)")

        pil_img = PILImage.open(file_path)
        pil_img = SignatureImageLoader._remove_background(pil_img)
        return SignatureImageLoader._pil_to_reportlab(pil_img, width, height)

    @staticmethod
    def _load_via_media_root(image_field, width, height):
        """Method 3: Load signature using MEDIA_ROOT path construction."""
        logger.debug(f"[SIGNATURE] Method 3: MEDIA_ROOT path")

        media_root = getattr(settings, 'MEDIA_ROOT', None)
        if not media_root:
            raise ValueError("MEDIA_ROOT not configured")

        full_path = os.path.join(media_root, image_field.name)

        if not os.path.exists(full_path):
            raise FileNotFoundError(f"File not found: {full_path}")

        file_size = os.path.getsize(full_path)
        if file_size == 0:
            raise ValueError("Image file is empty")

        logger.debug(f"[SIGNATURE] Loading from MEDIA_ROOT: {full_path} ({file_size} bytes)")

        pil_img = PILImage.open(full_path)
        pil_img = SignatureImageLoader._remove_background(pil_img)
        return SignatureImageLoader._pil_to_reportlab(pil_img, width, height)

    @staticmethod
    def _load_via_pil(image_field, width, height):
        """Method 4: Load signature via PIL (handles corrupt or special format images)."""
        logger.debug(f"[SIGNATURE] Method 4: PIL conversion")

        # Get image data
        img_data = None

        if default_storage.exists(image_field.name):
            with default_storage.open(image_field.name, 'rb') as f:
                img_data = f.read()
        elif hasattr(image_field, 'path') and os.path.exists(image_field.path):
            with open(image_field.path, 'rb') as f:
                img_data = f.read()
        else:
            raise FileNotFoundError("Image file not accessible")

        if not img_data or len(img_data) == 0:
            raise ValueError("Image data is empty")

        pil_img = PILImage.open(io.BytesIO(img_data))
        logger.debug(f"[SIGNATURE] PIL: format={pil_img.format}, mode={pil_img.mode}, size={pil_img.size}")

        pil_img = SignatureImageLoader._remove_background(pil_img)
        return SignatureImageLoader._pil_to_reportlab(pil_img, width, height)

    @staticmethod
    def get_user_full_name(user):
        """Get user's full name with title."""
        try:
            # Try userprofile.get_full_name() first
            if hasattr(user, 'userprofile') and hasattr(user.userprofile, 'get_full_name'):
                full_name = user.userprofile.get_full_name()
                if full_name:
                    return full_name

            # Try user.get_full_name()
            if hasattr(user, 'get_full_name'):
                full_name = user.get_full_name()
                if full_name:
                    return full_name

            # Try first_name + last_name
            if user.first_name or user.last_name:
                return f"{user.first_name} {user.last_name}".strip()

            # Fallback to username
            return user.username

        except Exception as e:
            logger.error(f"[SIGNATURE] Error getting user name: {e}")
            return user.username


class BtwelveHospitalCertificateGenerator:
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
        if self.is_declined:
            ref = (
                self.context.get('certificate_number')  # view passes DECLINED-<pk> here
                or (f"REF-{self.session.id}" if hasattr(self.session, 'id') else "REF-000")
            )
            self.reference_number = str(ref)
            self.certificate_number = None          # explicitly absent for declined docs
        else:
            self.reference_number = None
            self.certificate_number = self._get_certificate_number_from_session()

        # Calculate failure statistics
        self.failure_stats = self.calculate_failure_statistics()
        self.is_failed_report = self.failure_stats['overall_failure_rate'] >= 0.40

        # Resolve logo once; reused by both the header and the watermark canvas
        self.logo_path = self._get_logo_path()

    def _get_certificate_number_from_session(self):
        """
        Get certificate number from the session object.
        The certificate number is generated by the backend (CalibrationSession model).
        This method just retrieves it safely.
        """
        try:
            # First priority: Get from session's certificate_number field
            if hasattr(self.session, 'certificate_number') and self.session.certificate_number:
                cert_num = str(self.session.certificate_number).strip()
                if cert_num and cert_num.lower() != 'none' and cert_num.lower() != 'null':
                    return cert_num

            # Second priority: Get from context (passed from view)
            if 'certificate_number' in self.context and self.context['certificate_number']:
                cert_num = str(self.context['certificate_number']).strip()
                if cert_num and cert_num.lower() != 'none':
                    return cert_num

            # Third priority: Check for certificate_number in related objects
            if hasattr(self.session, 'procedure') and self.session.procedure:
                if hasattr(self.session.procedure, 'certificate_number') and self.session.procedure.certificate_number:
                    cert_num = str(self.session.procedure.certificate_number).strip()
                    if cert_num and cert_num.lower() != 'none':
                        return cert_num

            # Fourth priority: Try to generate one if session ID exists
            if hasattr(self.session, 'id') and self.session.id:
                logger.warning(f"Certificate number not found for session {self.session.id}. Using fallback.")
                try:
                    # Try to import the model and generate
                    from CalSoft.models import CalibrationSession
                    cert_num = CalibrationSession.generate_certificate_number()
                    # Save it to the session
                    self.session.certificate_number = cert_num
                    self.session.save(update_fields=['certificate_number'])
                    logger.info(f"Generated and saved certificate number: {cert_num}")
                    return cert_num
                except ImportError as e:
                    logger.error(f"Cannot import CalibrationSession: {e}")
                except Exception as e:
                    logger.error(f"Failed to generate certificate number: {e}")

                # Alternative fallback
                year_prefix = datetime.now().strftime('%y')
                return f"BNH-CAL-{year_prefix}-{self.session.id:06d}"

            # Final fallback
            logger.error("No valid certificate number or session ID found")
            return "BNH-TEMP-0000"

        except Exception as e:
            logger.error(f"Error getting certificate number: {str(e)}")
            return "BNH-ERROR-0000"

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

    def group_readings_by_parameter(self):
        """Group readings by parameter and order by backend order."""
        if not hasattr(self.session, 'readings') or not self.session.readings.exists():
            return {}

        readings = self.session.readings.all().order_by('parameter__order', 'sub_parameter__order', 'id')
        grouped = {}

        for reading in readings:
            if reading.parameter:
                param_key = reading.parameter.name
                if param_key not in grouped:
                    grouped[param_key] = {
                        'parameter': reading.parameter,
                        'readings': [],
                        'sub_parameters': {}
                    }

                # Group by sub-parameter if it exists
                if reading.sub_parameter:
                    sub_param_key = reading.sub_parameter.name
                    if sub_param_key not in grouped[param_key]['sub_parameters']:
                        grouped[param_key]['sub_parameters'][sub_param_key] = {
                            'sub_parameter': reading.sub_parameter,
                            'readings': []
                        }
                    grouped[param_key]['sub_parameters'][sub_param_key]['readings'].append(reading)
                else:
                    grouped[param_key]['readings'].append(reading)

        return grouped

    def build_results_table(self):
        """Build calibration results and statistics table with proper grouping."""
        grouped_readings = self.group_readings_by_parameter()

        if not grouped_readings:
            return [Paragraph("No calibration results available", self.styles['NormalText'])]

        elements = []

        for param_name, param_data in grouped_readings.items():
            parameter = param_data['parameter']
            unit = parameter.unit if parameter.unit else ''

            # Create parameter title
            param_title = f"Parameter: {param_name}"
            if unit:
                param_title += f" | Unit: {unit}"

            param_title_table = Table([[Paragraph(param_title, self.styles['ParameterTitle'])]],
                                    colWidths=[6.9*inch])
            param_title_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), HexColor('#f3f4f6')),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('BOX', (0, 0), (-1, -1), 1, HexColor('#d1d5db')),
            ]))

            elements.append(param_title_table)
            elements.append(Spacer(1, 4))

            # Check if this parameter has sub-parameters or direct readings
            if param_data['sub_parameters']:
                # Handle sub-parameters
                self._add_sub_parameter_tables(elements, param_data['sub_parameters'])
            elif param_data['readings']:
                # Handle direct parameter readings
                self._add_parameter_readings_table(elements, param_data['readings'])

            elements.append(Spacer(1, 12))

        return elements

    def _add_sub_parameter_tables(self, elements, sub_parameters):
        """Add tables for sub-parameters in order."""
        for sub_param_name, sub_param_data in sub_parameters.items():
            if sub_param_data['readings']:
                # Sub-parameter header
                sub_param_header = Table([[f"  └─ {sub_param_name}"]], colWidths=[6.9*inch])
                sub_param_header.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), HexColor('#f9fafb')),
                    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 9),
                    ('LEFTPADDING', (0, 0), (-1, -1), 16),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ]))

                elements.append(sub_param_header)

                # Sub-parameter readings table
                self._add_parameter_readings_table(elements, sub_param_data['readings'])
                elements.append(Spacer(1, 6))

    def _add_parameter_readings_table(self, elements, readings):
        """Create a table for parameter readings."""
        if not readings:
            return

        # Table headers
        table_data = [
            ['Set Value', 'Mean', 'Std Dev', 'Error', 'Tolerance', 'Status']
        ]

        # Add readings data
        for reading in readings:
            set_value = str(reading.set_value.value) if reading.set_value else 'N/A'
            mean = f"{reading.mean:.4f}" if reading.mean else 'N/A'
            std_dev = f"{reading.standard_deviation:.4f}" if getattr(reading, 'standard_deviation', None) else 'N/A'
            error = f"{reading.error:.4f}" if reading.error else 'N/A'

            # Determine tolerance
            tolerance = 'N/A'
            if reading.sub_parameter and reading.sub_parameter.tolerance:
                tolerance = str(reading.sub_parameter.tolerance)
            elif reading.parameter and reading.parameter.tolerance:
                tolerance = str(reading.parameter.tolerance)

            # Determine pass/fail status
            passes_tolerance = getattr(reading, 'passes_tolerance', True)
            if not hasattr(reading, 'passes_tolerance') and reading.error and reading.parameter:
                tol_value = None
                if reading.sub_parameter and reading.sub_parameter.tolerance:
                    tol_value = float(reading.sub_parameter.tolerance)
                elif reading.parameter and reading.parameter.tolerance:
                    tol_value = float(reading.parameter.tolerance)

                if tol_value:
                    passes_tolerance = abs(float(reading.error)) <= tol_value

            status = 'PASS' if passes_tolerance else 'FAIL'

            table_data.append([set_value, mean, std_dev, error, tolerance, status])

        # Create table with proper column widths
        readings_table = Table(table_data, colWidths=[1.0*inch, 1.2*inch, 1.0*inch, 1.0*inch, 1.0*inch, 0.7*inch])

        # Style the table
        table_style = [
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, 0), HexColor('#e5e7eb')),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('BOX', (0, 0), (-1, -1), 0.5, HexColor('#d1d5db')),
            ('INNERGRID', (0, 0), (-1, -1), 0.25, HexColor('#d1d5db')),
        ]

        # Add highlighting for failed rows
        for i, row in enumerate(table_data[1:], 1):  # Skip header row
            if row[5] == 'FAIL':  # Status column
                table_style.extend([
                    ('BACKGROUND', (0, i), (-1, i), HexColor('#fef2f2')),
                    ('TEXTCOLOR', (5, i), (5, i), HexColor('#dc2626')),
                    ('FONTNAME', (5, i), (5, i), 'Helvetica-Bold'),
                ])
            else:
                table_style.append(('BACKGROUND', (0, i), (-1, i), HexColor('#ffffff')))

        readings_table.setStyle(TableStyle(table_style))
        elements.append(readings_table)

    def build_uncertainty_table(self):
        """Build uncertainty budget table with proper grouping."""
        grouped_readings = self.group_readings_by_parameter()

        if not grouped_readings:
            return [Paragraph("No uncertainty data available", self.styles['NormalText'])]

        elements = []

        for param_name, param_data in grouped_readings.items():
            parameter = param_data['parameter']
            unit = parameter.unit if parameter.unit else ''

            # Create parameter title for uncertainty
            param_title = f"Parameter: {param_name}"
            if unit:
                param_title += f" | Unit: {unit}"

            param_title_table = Table([[Paragraph(param_title, self.styles['ParameterTitle'])]],
                                    colWidths=[6.9*inch])
            param_title_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), HexColor('#f3f4f6')),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('BOX', (0, 0), (-1, -1), 1, HexColor('#d1d5db')),
            ]))

            elements.append(param_title_table)
            elements.append(Spacer(1, 4))

            # Check if this parameter has sub-parameters or direct readings
            if param_data['sub_parameters']:
                # Handle sub-parameters
                self._add_sub_parameter_uncertainty_tables(elements, param_data['sub_parameters'])
            elif param_data['readings']:
                # Handle direct parameter readings
                self._add_parameter_uncertainty_table(elements, param_data['readings'])

            elements.append(Spacer(1, 12))

        return elements

    def _add_sub_parameter_uncertainty_tables(self, elements, sub_parameters):
        """Add uncertainty tables for sub-parameters in order."""
        for sub_param_name, sub_param_data in sub_parameters.items():
            if sub_param_data['readings']:
                # Sub-parameter header
                sub_param_header = Table([[f"  └─ {sub_param_name}"]], colWidths=[6.9*inch])
                sub_param_header.setStyle(TableStyle([
                    ('BACKGROUND', (0, 0), (-1, -1), HexColor('#f9fafb')),
                    ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
                    ('FONTSIZE', (0, 0), (-1, -1), 9),
                    ('LEFTPADDING', (0, 0), (-1, -1), 16),
                    ('TOPPADDING', (0, 0), (-1, -1), 4),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ]))

                elements.append(sub_param_header)

                # Sub-parameter uncertainty table
                self._add_parameter_uncertainty_table(elements, sub_param_data['readings'])
                elements.append(Spacer(1, 6))

    def _add_parameter_uncertainty_table(self, elements, readings):
        """Create an uncertainty table for parameter readings."""
        if not readings:
            return

        # Table headers
        uncertainty_data = [
            ['Set Value', 'Type A', 'Type B', 'Combined', 'Expanded', 'k-factor']
        ]

        # Add uncertainty data
        for reading in readings:
            set_value = str(reading.set_value.value) if reading.set_value else 'N/A'
            type_a = f"{reading.type_a_uncertainty:.4f}" if getattr(reading, 'type_a_uncertainty', None) else 'N/A'
            type_b = f"{reading.type_b_uncertainty:.4f}" if getattr(reading, 'type_b_uncertainty', None) else 'N/A'
            combined = f"{reading.combined_uncertainty:.4f}" if getattr(reading, 'combined_uncertainty', None) else 'N/A'
            expanded = f"{reading.expanded_uncertainty:.4f}" if getattr(reading, 'expanded_uncertainty', None) else 'N/A'
            k_factor = str(reading.parameter.coverage_factor) if reading.parameter and reading.parameter.coverage_factor else '2.0'

            uncertainty_data.append([set_value, type_a, type_b, combined, expanded, k_factor])

        # Create uncertainty table
        uncertainty_table = Table(uncertainty_data, colWidths=[1.15*inch] * 6)
        uncertainty_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, -1), 8),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('BACKGROUND', (0, 0), (-1, 0), HexColor('#e5e7eb')),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
            ('BOX', (0, 0), (-1, -1), 0.5, HexColor('#d1d5db')),
            ('INNERGRID', (0, 0), (-1, -1), 0.25, HexColor('#d1d5db')),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [HexColor('#ffffff'), HexColor('#f9fafb')]),
        ]))

        elements.append(uncertainty_table)

    def generate_qr_code(self):
        """Generate QR code for certificate verification including results table."""
        try:
            # Use reference number for declined docs, certificate number otherwise
            cert_number = self.reference_number if self.is_declined else (str(self.certificate_number) if self.certificate_number else "N/A")

            # Get results data
            results_data = []
            grouped_readings = self.group_readings_by_parameter()

            for param_name, param_data in grouped_readings.items():
                unit = param_data['parameter'].unit if param_data['parameter'].unit else ''

                if param_data['sub_parameters']:
                    for sub_param_name, sub_param_data in param_data['sub_parameters'].items():
                        for reading in sub_param_data['readings']:
                            set_value = str(reading.set_value.value) if reading.set_value else 'N/A'
                            mean = f"{reading.mean:.4f}" if reading.mean else 'N/A'
                            error = f"{reading.error:.4f}" if reading.error else 'N/A'

                            # Determine pass/fail status
                            passes_tolerance = getattr(reading, 'passes_tolerance', True)
                            status = 'PASS' if passes_tolerance else 'FAIL'

                            # Create compact result entry
                            result_entry = f"{param_name}-{sub_param_name}:{set_value}{unit}>{mean}±{error}({status})"
                            results_data.append(result_entry)
                elif param_data['readings']:
                    for reading in param_data['readings']:
                        set_value = str(reading.set_value.value) if reading.set_value else 'N/A'
                        mean = f"{reading.mean:.4f}" if reading.mean else 'N/A'
                        error = f"{reading.error:.4f}" if reading.error else 'N/A'

                        # Determine pass/fail status
                        passes_tolerance = getattr(reading, 'passes_tolerance', True)
                        status = 'PASS' if passes_tolerance else 'FAIL'

                        # Create compact result entry
                        result_entry = f"{param_name}:{set_value}{unit}>{mean}±{error}({status})"
                        results_data.append(result_entry)

            # Create comprehensive verification data
            verification_data = {
                'certificate_number': cert_number,
                'issued_date': self.session.timestamp.astimezone(EAT).strftime('%Y-%m-%d') if self.session.timestamp else self._now_eat().strftime('%Y-%m-%d'),
                'device_serial': getattr(self.session, 'device_serial', 'N/A'),
                'device_model': getattr(self.session, 'device_model', 'N/A'),
                'hospital': 'BTWELVE_NATIONAL_HOSPITAL',
                'overall_status': 'FAILED' if self.is_failed_report else 'PASSED',
                'failure_rate': f"{self.failure_stats['overall_failure_rate']:.1%}",
                'total_readings': str(self.failure_stats['total_readings']),
                'failed_readings': str(self.failure_stats['failed_readings']),
                'results': results_data[:15]  # Limit to first 15 results
            }

            # Create verification string
            verification_components = [
                f"CERT:{verification_data['certificate_number']}",
                f"DATE:{verification_data['issued_date']}",
                f"SERIAL:{verification_data['device_serial']}",
                f"MODEL:{verification_data['device_model']}",
                f"HOSPITAL:{verification_data['hospital']}",
                f"STATUS:{verification_data['overall_status']}",
                f"FAIL_RATE:{verification_data['failure_rate']}",
                f"READINGS:{verification_data['total_readings']}/{verification_data['failed_readings']}",
            ]

            # Add results if available
            if verification_data['results']:
                verification_components.append("RESULTS:")
                verification_components.extend(verification_data['results'])

            verification_string = "|".join(verification_components)

            # Check string length and adjust if necessary
            max_qr_length = 2500
            if len(verification_string) > max_qr_length:
                truncated_results = []
                current_length = len("|".join(verification_components[:8]))

                for result in verification_data['results']:
                    if current_length + len(result) + 1 < max_qr_length:
                        truncated_results.append(result)
                        current_length += len(result) + 1
                    else:
                        break

                verification_components = verification_components[:8]
                if truncated_results:
                    verification_components.append("RESULTS:")
                    verification_components.extend(truncated_results)
                    if len(truncated_results) < len(verification_data['results']):
                        verification_components.append(f"...+{len(verification_data['results']) - len(truncated_results)}more")

                verification_string = "|".join(verification_components)

            # Generate QR code
            qr = qrcode.QRCode(
                version=None,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=8,
                border=2,
            )

            qr.add_data(verification_string)
            qr.make(fit=True)

            qr_img = qr.make_image(fill_color="black", back_color="white")

            # Save QR code to storage
            qr_filename = f'qrcodes/cert_{self.session.id}_qr.png'
            qr_path = os.path.join(settings.MEDIA_ROOT, qr_filename)
            os.makedirs(os.path.dirname(qr_path), exist_ok=True)
            qr_img.save(qr_path)

            logger.info(f"QR Code generated for certificate {cert_number}")
            logger.debug(f"QR Code data length: {len(verification_string)} characters")

            return qr_path

        except Exception as e:
            logger.error(f"Error generating QR code: {str(e)}")

            # Fallback to basic QR code
            try:
                basic_verification = f"CERT:{self.certificate_number}|DATE:{self.session.timestamp.astimezone(EAT).strftime('%Y-%m-%d') if self.session.timestamp else self._now_eat().strftime('%Y-%m-%d')}|SERIAL:{getattr(self.session, 'device_serial', 'N/A')}|HOSPITAL:BTWELVE_NATIONAL_HOSPITAL|STATUS:{'FAILED' if self.is_failed_report else 'PASSED'}"

                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_M,
                    box_size=10,
                    border=4,
                )
                qr.add_data(basic_verification)
                qr.make(fit=True)

                qr_img = qr.make_image(fill_color="black", back_color="white")
                qr_filename = f'qrcodes/cert_{self.session.id}_qr_basic.png'
                qr_path = os.path.join(settings.MEDIA_ROOT, qr_filename)
                os.makedirs(os.path.dirname(qr_path), exist_ok=True)
                qr_img.save(qr_path)

                logger.info(f"Fallback basic QR code generated")
                return qr_path

            except Exception as fallback_error:
                logger.error(f"Error generating fallback QR code: {str(fallback_error)}")
                return None

    def calculate_failure_statistics(self):
        """Calculate failure statistics for the calibration session."""
        try:
            if not hasattr(self.session, 'readings') or not self.session.readings.exists():
                return {
                    'total_readings': 0,
                    'failed_readings': 0,
                    'overall_failure_rate': 0,
                    'parameter_failures': {},
                    'failed_parameters': []
                }

            readings = self.session.readings.all()
            total_readings = readings.count()
            failed_readings = 0
            parameter_failures = {}

            for reading in readings:
                # Determine if reading passes tolerance
                passes_tolerance = True

                if hasattr(reading, 'passes_tolerance'):
                    passes_tolerance = reading.passes_tolerance
                elif reading.error and reading.parameter:
                    # Calculate based on error and tolerance
                    tolerance = None
                    if reading.sub_parameter and reading.sub_parameter.tolerance:
                        try:
                            tolerance = float(reading.sub_parameter.tolerance)
                        except (ValueError, TypeError):
                            tolerance = None
                    elif reading.parameter and reading.parameter.tolerance:
                        try:
                            tolerance = float(reading.parameter.tolerance)
                        except (ValueError, TypeError):
                            tolerance = None

                    if tolerance:
                        try:
                            passes_tolerance = abs(float(reading.error)) <= tolerance
                        except (ValueError, TypeError):
                            passes_tolerance = True

                # Track parameter-level failures
                param_name = reading.parameter.name if reading.parameter else 'Unknown'
                if param_name not in parameter_failures:
                    parameter_failures[param_name] = {'total': 0, 'failed': 0}

                parameter_failures[param_name]['total'] += 1

                if not passes_tolerance:
                    failed_readings += 1
                    parameter_failures[param_name]['failed'] += 1

            # Calculate overall failure rate
            overall_failure_rate = failed_readings / total_readings if total_readings > 0 else 0

            # Identify parameters with >= 40% failure rate
            failed_parameters = []
            for param, stats in parameter_failures.items():
                failure_rate = stats['failed'] / stats['total'] if stats['total'] > 0 else 0
                if failure_rate >= 0.40:
                    failed_parameters.append({
                        'name': param,
                        'failure_rate': failure_rate,
                        'failed_count': stats['failed'],
                        'total_count': stats['total']
                    })

            return {
                'total_readings': total_readings,
                'failed_readings': failed_readings,
                'overall_failure_rate': overall_failure_rate,
                'parameter_failures': parameter_failures,
                'failed_parameters': failed_parameters
            }

        except Exception as e:
            logger.error(f"Error calculating failure statistics: {str(e)}")
            return {
                'total_readings': 0,
                'failed_readings': 0,
                'overall_failure_rate': 0,
                'parameter_failures': {},
                'failed_parameters': []
            }

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
            try:
                from .pdf_config import PDFConfiguration
                lab_name = PDFConfiguration.LABORATORY_INFO.get('name', 'KNH CALIBRATION LABORATORY')
            except Exception:
                lab_name = 'KNH CALIBRATION LABORATORY'

            initials = ''.join(w[0] for w in lab_name.split() if w[0].isupper())[:3] or 'KNH'

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
                               'Email: calibration@btwelve.hospital | Phone: +254-XXX-XXXX | ISO/IEC 17025:2017')

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
        except Exception:
            pass

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
                        except Exception:
                            pass
        except Exception:
            pass

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

    def _now_eat(self):
        """Return current datetime in East Africa Time (UTC+3)."""
        from django.utils import timezone as dj_tz
        return dj_tz.now().astimezone(EAT)

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

        if self.is_failed_report:
            # Failed report title
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
            warning_text = f"⚠️ DEVICE FAILED CALIBRATION - {self.failure_stats['overall_failure_rate']:.1%} failure rate exceeds acceptable limits"
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
            cert_number = str(self.certificate_number) if self.certificate_number else "N/A"
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

    def build_three_section_table(self):
        """Build the three-section table with header backgrounds."""
        headers = [
            self.styled_header_table("U.U.T Information"),
            self.styled_header_table("Procedure Information"),
            self.styled_header_table("Calibration Information")
        ]
        contents = [
            self.get_uut_info_content(),
            self.get_procedure_info_content(),
            self.get_calibration_info_content()
        ]
        table_data = [
            headers,
            contents
        ]
        table = Table(table_data, colWidths=[2.3*inch, 2.3*inch, 2.3*inch])
        table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ]))
        return [table, Spacer(1, 12)]

    def get_uut_info_content(self):
        """Get UUT Information content."""
        location = self.context.get('location', 'N/A')
        department = self.context.get('department', 'N/A')

        # Get device information with better fallbacks
        device_desc = getattr(self.session, 'device_description', 'N/A')
        device_model = getattr(self.session, 'device_model', 'N/A')
        device_serial = getattr(self.session, 'device_serial', 'N/A')

        # If session has a device field, use that
        if hasattr(self.session, 'device') and self.session.device:
            device = self.session.device
            if device_desc == 'N/A' and hasattr(device, 'description'):
                device_desc = device.description or 'N/A'
            if device_model == 'N/A' and hasattr(device, 'model'):
                device_model = device.model or 'N/A'
            if device_serial == 'N/A' and hasattr(device, 'serial_number'):
                device_serial = device.serial_number or 'N/A'

        content = f"""
        <b>Description:</b> {device_desc}<br/>
        <b>Model:</b> {device_model}<br/>
        <b>Serial Number:</b> {device_serial}<br/>
        <b>Department:</b> {department}<br/>
        <b>Location:</b> {location}<br/>
        """
        return Paragraph(content, self.styles['NormalText'])

    def get_procedure_info_content(self):
        """Get Procedure Information content."""
        procedure = self.session.procedure if hasattr(self.session, 'procedure') and self.session.procedure else None

        procedure_name = procedure.name if procedure else 'N/A'
        input_range = getattr(procedure, 'input_range', 'N/A') if procedure else 'N/A'

        content = f"""
        <b>Procedure:</b> {procedure_name}<br/>
        <b>Input Range:</b> {input_range}<br/>
        <b>Output Range:</b> 0 to 0<br/>
        <b>Reject Error ></b> 0.00% of Range<br/>
        """
        return Paragraph(content, self.styles['NormalText'])

    def get_calibration_info_content(self):
        """Get Calibration Information content."""
        cal_date = self.session.timestamp.astimezone(EAT).strftime('%Y-%m-%d') if self.session.timestamp else 'N/A'

        # Calculate due date: last day of the month, one year from calibration date (EAT)
        if self.session.timestamp:
            ts_eat = self.session.timestamp.astimezone(EAT)
            due_dt = ts_eat + timedelta(days=365)
            last_day = calendar.monthrange(due_dt.year, due_dt.month)[1]
            cal_due = due_dt.replace(day=last_day).strftime('%Y-%m-%d')
        else:
            cal_due = 'N/A'

        if self.is_declined:
            doc_label  = "Reference No:"
            doc_number = self.reference_number or "N/A"
        else:
            doc_label  = "Certificate No:"
            doc_number = self.certificate_number or "N/A"

        content = f"""
        <b>Calibration Date:</b> {cal_date}<br/>
        <b>Due Date:</b> {cal_due}<br/>
        <b>{doc_label}</b> {doc_number}<br/>
        """
        return Paragraph(content, self.styles['NormalText'])

    def build_section_with_header(self, title, content_flowables):
        """Build a section with a colored header background but no borders."""
        table = Table([[Paragraph(title, self.styles['HeaderText'])]], colWidths=[6.9*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), HexColor('#d9d9d9')),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))

        elements = [table, Spacer(1, 4)]
        elements.extend(content_flowables)
        elements.append(Spacer(1, 4))

        return elements

    def build_calibrator_info_content(self):
        """Build calibrator and standard information content in table format with deduplication."""
        elements = []

        # Get standards from procedure parameters
        standards_dict = {}  # Use dict to track by serial number

        if hasattr(self.session, 'procedure') and self.session.procedure:
            for param in self.session.procedure.parameters.all():
                if param.standard_reference:
                    try:
                        from .models import Standard  # Adjust import as needed
                        standard = Standard.objects.get(serial_number=param.standard_reference)
                        # Use serial number as key to avoid duplicates
                        if standard.serial_number not in standards_dict:
                            standards_dict[standard.serial_number] = standard
                    except (Standard.DoesNotExist, ImportError):
                        # Standard not found or import error
                        pass

        if standards_dict:
            # Create table header
            standard_data = [
                ['Description', 'Serial No.', 'Model', 'Manufacturer', 'Cal Date', 'Due Date']
            ]

            # Add standard rows from the deduplicated dictionary
            for serial_number, standard in standards_dict.items():
                cal_date = standard.calibration_date.strftime('%Y-%m-%d') if standard.calibration_date else 'N/A'
                due_date = standard.calibration_due_date.strftime('%Y-%m-%d') if standard.calibration_due_date else 'N/A'

                standard_data.append([
                    standard.name or 'N/A',
                    standard.serial_number or 'N/A',
                    standard.model_number or 'N/A',
                    standard.manufacturer or 'N/A',
                    cal_date,
                    due_date
                ])

            # Create standards table with adjusted column widths
            standards_table = Table(standard_data, colWidths=[1.4*inch, 1.5*inch, 1.1*inch, 1.3*inch, 0.9*inch, 0.9*inch])
            standards_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('BACKGROUND', (0, 0), (-1, 0), HexColor('#e5e7eb')),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [HexColor('#ffffff'), HexColor('#f9fafb')]),
                ('WORDWRAP', (0, 0), (-1, -1), 'CJK'),
            ]))

            elements.append(standards_table)

        # Add calibrators information if available
        if hasattr(self.session, 'calibrators') and self.session.calibrators.exists():
            if elements:  # Add spacing if standards table exists
                elements.append(Spacer(1, 10))

            calibrator_data = [
                ['Calibrator Description', 'Serial No.', 'Certificate No.']
            ]

            for calibrator in self.session.calibrators.all():
                calibrator_data.append([
                    calibrator.description or 'N/A',
                    calibrator.serial_no or 'N/A',
                    calibrator.certificate_no or 'N/A'
                ])

            # Adjusted calibrator table column widths
            calibrators_table = Table(calibrator_data, colWidths=[3.2*inch, 1.8*inch, 1.9*inch])
            calibrators_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 8),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('BACKGROUND', (0, 0), (-1, 0), HexColor('#e5e7eb')),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [HexColor('#ffffff'), HexColor('#f9fafb')]),
                ('WORDWRAP', (0, 0), (-1, -1), 'CJK'),
            ]))

            elements.append(calibrators_table)

        if not elements:
            elements.append(Paragraph("No calibrator or standard information available", self.styles['NormalText']))

        return elements

    def build_linearity_content(self):
        """Build linearity analysis content with chart."""
        elements = []

        if hasattr(self.session, 'readings') and self.session.readings.exists():
            chart_path = self.generate_linearity_chart()
            if chart_path and os.path.exists(chart_path):
                try:
                    chart_img = Image(chart_path, width=3.5*inch, height=1.5*inch)
                    elements.append(chart_img)
                except Exception as e:
                    logger.error(f"Error adding linearity chart: {str(e)}")
                    elements.append(Paragraph("Linearity chart generation error", self.styles['NormalText']))
            else:
                elements.append(Paragraph("Linearity chart not available", self.styles['NormalText']))
        else:
            elements.append(Paragraph("Linearity chart not available", self.styles['NormalText']))

        return elements

    def generate_linearity_chart(self):
        """Generate linearity analysis chart."""
        try:
            grouped_readings = self.group_readings_by_parameter()
            if not grouped_readings:
                return None

            # Create the chart
            fig, ax = plt.subplots(figsize=(10, 5))

            for param_name, param_data in grouped_readings.items():
                if param_data['sub_parameters']:
                    for sub_param_name, sub_param_data in param_data['sub_parameters'].items():
                        set_values = []
                        means = []
                        for reading in sub_param_data['readings']:
                            if reading.set_value and reading.mean:
                                try:
                                    set_values.append(float(reading.set_value.value))
                                    means.append(float(reading.mean))
                                except (ValueError, TypeError):
                                    continue

                        if set_values and means:
                            ax.plot(set_values, means, 'o-', label=f"{param_name} - {sub_param_name}")
                elif param_data['readings']:
                    set_values = []
                    means = []
                    for reading in param_data['readings']:
                        if reading.set_value and reading.mean:
                            try:
                                set_values.append(float(reading.set_value.value))
                                means.append(float(reading.mean))
                            except (ValueError, TypeError):
                                continue

                    if set_values and means:
                        ax.plot(set_values, means, 'o-', label=param_name)

            ax.set_xlabel('Set Values', fontsize=12)
            ax.set_ylabel('Measured Values', fontsize=12)
            ax.set_title('Linearity Analysis', fontsize=14)
            if ax.get_legend_handles_labels()[0]:  # Check if there are labels
                ax.legend()
            ax.grid(True, alpha=0.3)

            chart_path = os.path.join(settings.MEDIA_ROOT, f'charts/linearity_{self.session.id}.png')
            os.makedirs(os.path.dirname(chart_path), exist_ok=True)
            plt.tight_layout()
            plt.savefig(chart_path, dpi=300, bbox_inches='tight')
            plt.close()

            return chart_path

        except Exception as e:
            logger.error(f"Error generating linearity chart: {str(e)}")
            return None

    def build_failure_analysis(self):
        """Build failure analysis section for failed calibrations."""
        elements = []

        if not self.failure_stats['failed_parameters'] and not self.is_failed_report:
            return [Paragraph("No significant failures detected.", self.styles['NormalText'])]

        # Overall failure summary
        summary_text = f"""
        <b>Overall Failure Rate:</b> {self.failure_stats['overall_failure_rate']:.1%}
        ({self.failure_stats['failed_readings']}/{self.failure_stats['total_readings']} readings failed)<br/>
        <b>Threshold:</b> 40% failure rate triggers failure report<br/>
        <b>Status:</b> {'FAILED - Device requires maintenance/repair' if self.is_failed_report else 'WARNING - Some parameters exceeded limits'}
        """
        elements.append(Paragraph(summary_text, self.styles['FailureText']))
        elements.append(Spacer(1, 8))

        # Parameter-specific failure analysis
        if self.failure_stats['failed_parameters']:
            elements.append(Paragraph("<b>Failed Parameters Analysis:</b>", self.styles['FailureText']))

            failure_data = [['Parameter', 'Failed/Total', 'Failure Rate', 'Recommendation']]

            for param_failure in self.failure_stats['failed_parameters']:
                failure_rate = f"{param_failure['failure_rate']:.1%}"
                ratio = f"{param_failure['failed_count']}/{param_failure['total_count']}"

                # Generate recommendation based on failure rate
                if param_failure['failure_rate'] >= 0.80:
                    recommendation = "Critical - Immediate repair required"
                elif param_failure['failure_rate'] >= 0.60:
                    recommendation = "Major issue - Service needed"
                else:
                    recommendation = "Minor issue - Monitor closely"

                failure_data.append([
                    param_failure['name'],
                    ratio,
                    failure_rate,
                    recommendation
                ])

            failure_table = Table(failure_data, colWidths=[2*inch, 1*inch, 1*inch, 2.9*inch])
            failure_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('BACKGROUND', (0, 0), (-1, 0), HexColor('#fecaca')),
                ('BACKGROUND', (0, 1), (-1, -1), HexColor('#fef2f2')),
                ('LEFTPADDING', (0, 0), (-1, -1), 4),
                ('RIGHTPADDING', (0, 0), (-1, -1), 4),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('TEXTCOLOR', (0, 0), (-1, -1), HexColor('#991b1b')),
            ]))

            elements.append(failure_table)

        return elements

    def build_traceability_content(self):
        """Build traceability content."""
        traceability_text = """
        The measurements reported in this certificate are traceable to the International System of Units (SI) through an
        unbroken chain of calibrations or comparisons. All measuring equipment used has been calibrated using standards
        whose accuracies are commensurate with the uncertainties reported in this certificate.
        """
        return [Paragraph(traceability_text, self.styles['NormalText'])]

    def build_notes_content(self):
        """Build notes content with failure-specific notes."""
        base_notes = [
            "1. This certificate relates only to the item(s) calibrated and the results are valid at the time and under the conditions of calibration.",
            "2. This certificate shall not be reproduced except in full without written approval.",
            "3. The uncertainty of measurement is stated as the expanded uncertainty calculated using a coverage factor k=2.",
            "4. Certificate authenticity can be verified by scanning the QR code."
        ]

        if self.is_failed_report:
            failure_notes = [
                "5. CRITICAL: This device has FAILED calibration with a failure rate exceeding 40%.",
                "6. The device must be taken out of service immediately and repaired before use.",
                "7. This failure report serves as documentation for maintenance/repair requirements.",
                "8. A new calibration must be performed after repairs are completed.",
                "9. Contact the Biomedical Engineering Department for repair coordination."
            ]
            base_notes.extend(failure_notes)
        elif self.failure_stats['failed_parameters']:
            warning_notes = [
                "5. WARNING: Some parameters failed calibration but overall failure rate is below 40%.",
                "6. Monitor device performance closely and consider maintenance scheduling.",
                "7. Calibration interval recommendation may be shortened based on failure analysis."
            ]
            base_notes.extend(warning_notes)
        else:
            base_notes.append("5. Calibration interval recommendation: 12 months from calibration date.")

        notes_text = "<br/>".join(base_notes)
        return [Paragraph(notes_text, self.styles['NormalText'])]

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

    def _fetch_historical_drift_data(self):
        """
        Fetch drift data from ALL previous CalibrationSession records for the same
        device serial, grouped by (parameter_name, sub_parameter_name).

        Each entry carries per-reading metadata including pass/fail and the
        overall session-level pass flag so we can surface last-session outcomes.

        Returns:
            dict: {(param_name, sub_param_name): [reading_dict, ...]}
        """
        try:
            from CalSoft.models import CalibrationReading, CalibrationSession

            device_serial = getattr(self.session, 'device_serial', None)
            if not device_serial:
                return {}

            previous_sessions = (
                CalibrationSession.objects
                .filter(
                    device_serial=device_serial,
                    timestamp__lt=self.session.timestamp,
                    active_status=True,
                )
                .exclude(id=self.session.id)
                .order_by('timestamp')
            )

            if not previous_sessions.exists():
                return {}

            grouped = {}

            for sess in previous_sessions:
                cert_num = getattr(sess, 'certificate_number', None) or '\u2014'
                sess_overall_pass = getattr(sess, 'overall_pass', None)

                readings = (
                    CalibrationReading.objects
                    .filter(session=sess, active_status=True)
                    .select_related('parameter', 'sub_parameter', 'set_value')
                )
                for r in readings:
                    if r.error is None:
                        continue
                    param_name = r.parameter.name if r.parameter else 'Unknown'
                    sub_name   = r.sub_parameter.name if r.sub_parameter else ''
                    key = (param_name, sub_name)
                    if key not in grouped:
                        grouped[key] = []
                    grouped[key].append({
                        'session_id':           str(sess.id),
                        'session_date':         sess.timestamp,
                        'session_cert':         cert_num,
                        'session_overall_pass': sess_overall_pass,
                        'error':                float(r.error),
                        'mean':                 float(r.mean) if r.mean is not None else None,
                        'uncertainty':          float(r.expanded_uncertainty) if r.expanded_uncertainty is not None else 0.0,
                        'passes':               bool(r.passes_tolerance),
                    })

            for key in grouped:
                grouped[key].sort(key=lambda x: x['session_date'])

            return grouped

        except Exception as e:
            logger.error(f"[DRIFT] Error fetching historical drift data: {e}")
            return {}

    @staticmethod
    def _compute_drift_metrics(readings):
        """
        Linear-regression drift analysis over a list of reading dicts.
        Returns a rich metrics dict including last-session pass/fail details.
        """
        n = len(readings)
        if n < 2:
            return None

        errors    = [r['error'] for r in readings]
        base_date = readings[0]['session_date']
        days      = [(r['session_date'] - base_date).days for r in readings]

        sum_x  = sum(days)
        sum_y  = sum(errors)
        sum_xy = sum(x * y for x, y in zip(days, errors))
        sum_xx = sum(x * x for x in days)
        denom  = n * sum_xx - sum_x ** 2

        drift_rate_per_day  = (n * sum_xy - sum_x * sum_y) / denom if denom != 0 else 0.0
        drift_rate_per_year = drift_rate_per_day * 365
        intercept           = (sum_y - drift_rate_per_day * sum_x) / n
        total_drift         = errors[-1] - errors[0]

        if abs(drift_rate_per_day) < 1e-8:
            direction = 'Stable'
        elif drift_rate_per_day > 0:
            direction = 'Increasing \u25b2'
        else:
            direction = 'Decreasing \u25bc'

        yearly = abs(drift_rate_per_year)
        if yearly < 0.1:
            stability = 'Excellent'
        elif yearly < 0.5:
            stability = 'Good'
        elif yearly < 1.0:
            stability = 'Fair'
        else:
            stability = 'Poor'

        mean_y = sum_y / n
        ss_tot = sum((y - mean_y) ** 2 for y in errors)
        if ss_tot > 0:
            ss_res    = sum((errors[i] - (drift_rate_per_day * days[i] + intercept)) ** 2 for i in range(n))
            r_squared = max(0.0, 1 - ss_res / ss_tot)
        else:
            r_squared = 1.0

        uncertainties = [r['uncertainty'] for r in readings]
        avg_unc    = sum(uncertainties) / n
        half       = max(n // 2, 1)
        first_avg  = sum(uncertainties[:half]) / half
        second_avg = sum(uncertainties[half:]) / max(n - half, 1)
        if second_avg > first_avg * 1.2:
            unc_trend = 'Increasing'
        elif second_avg < first_avg * 0.8:
            unc_trend = 'Decreasing'
        else:
            unc_trend = 'Stable'

        fail_count = sum(1 for r in readings if not r['passes'])
        pass_count = n - fail_count
        fail_rate  = fail_count / n

        last                = readings[-1]
        last_reading_pass   = last['passes']
        last_session_pass   = last['session_overall_pass']
        last_cert           = last['session_cert']
        last_date           = last['session_date']
        last_error          = last['error']
        last_uncertainty    = last['uncertainty']

        return {
            'drift_rate_per_day':  drift_rate_per_day,
            'drift_rate_per_year': drift_rate_per_year,
            'total_drift':         total_drift,
            'direction':           direction,
            'stability':           stability,
            'r_squared':           r_squared,
            'uncertainty_trend':   unc_trend,
            'avg_uncertainty':     avg_unc,
            'n_sessions':          n,
            'fail_count':          fail_count,
            'pass_count':          pass_count,
            'fail_rate':           fail_rate,
            'first_date':          readings[0]['session_date'],
            'last_date':           last_date,
            'last_cert':           last_cert,
            'last_reading_pass':   last_reading_pass,
            'last_session_pass':   last_session_pass,
            'last_error':          last_error,
            'last_uncertainty':    last_uncertainty,
        }

    # ── colour palette for drift tables ──────────────────────────────────────
    _DRIFT_COLORS = {
        'pass_bg':   HexColor('#d1fae5'),
        'pass_text': HexColor('#065f46'),
        'fail_bg':   HexColor('#fee2e2'),
        'fail_text': HexColor('#991b1b'),
        'warn_bg':   HexColor('#fef3c7'),
        'warn_text': HexColor('#92400e'),
        'hdr_bg':    HexColor('#0f172a'),
        'row_alt':   HexColor('#f8fafc'),
        'border':    HexColor('#cbd5e1'),
        'label_bg':  HexColor('#f1f5f9'),
    }

    def build_drift_analysis_content(self):
        """
        Build a single comprehensive drift analysis table that includes:
          - Device-level last-session PASS / FAIL banner
          - Six-cell summary strip (sessions, params, failures, drift rate, stability, date range)
          - Colour-coded recommendation bar
          - One unified per-parameter table with all metrics and last-session status
          - Legend footnote
        """
        elements = []
        DC = self._DRIFT_COLORS

        historical = self._fetch_historical_drift_data()

        if not historical:
            empty = Table(
                [[Paragraph(
                    "No previous calibration sessions found for this device serial. "
                    "Drift analysis becomes available once a second session is recorded.",
                    self.styles['DriftNormal']
                )]],
                colWidths=[6.9 * inch]
            )
            empty.setStyle(TableStyle([
                ('BACKGROUND',    (0, 0), (-1, -1), DC['label_bg']),
                ('BOX',           (0, 0), (-1, -1), 0.5, DC['border']),
                ('LEFTPADDING',   (0, 0), (-1, -1), 10),
                ('RIGHTPADDING',  (0, 0), (-1, -1), 10),
                ('TOPPADDING',    (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
            ]))
            elements.append(empty)
            return elements

        all_metrics = []
        for key, readings in historical.items():
            m = self._compute_drift_metrics(readings)
            if m:
                m['param_name'] = key[0]
                m['sub_param']  = key[1]
                all_metrics.append(m)

        if not all_metrics:
            elements.append(Paragraph(
                "Insufficient repeated readings across sessions to compute drift "
                "(need \u2265 2 sessions with matching parameters).",
                self.styles['DriftNormal']
            ))
            return elements

        # ── BANNER: last-session outcome ──────────────────────────────────────
        any_last_fail     = any(not m['last_reading_pass'] for m in all_metrics)
        overall_last_pass = all_metrics[0]['last_session_pass']

        if overall_last_pass is False or any_last_fail:
            b_bg, b_tc  = DC['fail_bg'], DC['fail_text']
            b_label     = "\u26a0  LAST CALIBRATION SESSION: FAILED"
            b_sub       = ("One or more parameters did not pass tolerance in the most recent "
                           "historical session. Refer to per-parameter details below.")
        elif overall_last_pass is True:
            b_bg, b_tc  = DC['pass_bg'], DC['pass_text']
            b_label     = "\u2714  LAST CALIBRATION SESSION: PASSED"
            b_sub       = "All parameters were within tolerance in the most recent historical session."
        else:
            b_bg, b_tc  = DC['warn_bg'], DC['warn_text']
            b_label     = "\u25cf  LAST SESSION STATUS: UNKNOWN"
            b_sub       = "Pass/fail outcome was not recorded on the most recent historical session."

        last_cert_shown = all_metrics[0]['last_cert']
        last_date_shown = all_metrics[0]['last_date'].strftime('%d %b %Y')

        b_title_s = ParagraphStyle('_bt', fontSize=10, fontName='Helvetica-Bold',
                                   textColor=b_tc, leading=14)
        b_sub_s   = ParagraphStyle('_bs', fontSize=7.5, fontName='Helvetica',
                                   textColor=b_tc, leading=11)
        b_meta_s  = ParagraphStyle('_bm', fontSize=8, fontName='Helvetica',
                                   textColor=b_tc, leading=12, alignment=TA_RIGHT)

        banner = Table(
            [[Paragraph(f"{b_label}<br/><font size=\'7.5\'>{b_sub}</font>", b_title_s),
              Paragraph(f"<b>Cert:</b> {last_cert_shown}<br/><b>Date:</b> {last_date_shown}",
                        b_meta_s)]],
            colWidths=[5.0 * inch, 1.9 * inch]
        )
        banner.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), b_bg),
            ('BOX',           (0, 0), (-1, -1), 1.5, b_tc),
            ('LEFTPADDING',   (0, 0), (-1, -1), 8),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
            ('TOPPADDING',    (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(banner)
        elements.append(Spacer(1, 5))

        # ── SUMMARY STRIP ─────────────────────────────────────────────────────
        total_sessions = max(m['n_sessions'] for m in all_metrics)
        earliest       = min(m['first_date'] for m in all_metrics)
        latest         = max(m['last_date']  for m in all_metrics)
        avg_yearly     = sum(abs(m['drift_rate_per_year']) for m in all_metrics) / len(all_metrics)
        total_fails    = sum(m['fail_count']  for m in all_metrics)
        total_readings = sum(m['n_sessions']  for m in all_metrics)

        stab_scores = {'Excellent': 4, 'Good': 3, 'Fair': 2, 'Poor': 1}
        avg_score   = sum(stab_scores.get(m['stability'], 2) for m in all_metrics) / len(all_metrics)
        if avg_score >= 3.5:
            ovr_label, ovr_bg, ovr_tc = 'Excellent', DC['pass_bg'], DC['pass_text']
        elif avg_score >= 2.5:
            ovr_label, ovr_bg, ovr_tc = 'Good',      DC['pass_bg'], DC['pass_text']
        elif avg_score >= 1.5:
            ovr_label, ovr_bg, ovr_tc = 'Fair',       DC['warn_bg'], DC['warn_text']
        else:
            ovr_label, ovr_bg, ovr_tc = 'Poor',       DC['fail_bg'], DC['fail_text']

        recommendation = (
            "Calibration interval is appropriate. Continue standard schedule."
            if avg_yearly < 0.5 else
            "Consider shortening calibration interval — drift rate is elevated."
            if avg_yearly < 1.0 else
            "Shorten calibration interval urgently — high drift rate detected."
        )

        lbl_s  = ParagraphStyle('_sl', fontSize=7, fontName='Helvetica-Bold',
                                textColor=HexColor('#64748b'), alignment=TA_CENTER)
        val_s  = ParagraphStyle('_sv', fontSize=9.5, fontName='Helvetica-Bold',
                                textColor=HexColor('#0f172a'), alignment=TA_CENTER)
        fval_s = ParagraphStyle('_sf', fontSize=9.5, fontName='Helvetica-Bold',
                                textColor=DC['fail_text'] if total_fails else DC['pass_text'],
                                alignment=TA_CENTER)
        oval_s = ParagraphStyle('_so', fontSize=9.5, fontName='Helvetica-Bold',
                                textColor=ovr_tc, alignment=TA_CENTER)

        strip_w = 6.9 * inch / 6
        strip_top = [
            Paragraph('SESSIONS', lbl_s), Paragraph('PARAMETERS', lbl_s),
            Paragraph('FAIL HISTORY', lbl_s), Paragraph('AVG DRIFT/YEAR', lbl_s),
            Paragraph('OVERALL STABILITY', lbl_s), Paragraph('DATE RANGE', lbl_s),
        ]
        strip_bot = [
            Paragraph(str(total_sessions), val_s),
            Paragraph(str(len(all_metrics)), val_s),
            Paragraph(f"{total_fails} / {total_readings}", fval_s),
            Paragraph(f"{avg_yearly:.5f}", val_s),
            Paragraph(ovr_label, oval_s),
            Paragraph(f"{earliest.strftime('%d %b %y')} \u2192 {latest.strftime('%d %b %y')}", val_s),
        ]

        strip = Table([strip_top, strip_bot], colWidths=[strip_w] * 6)
        strip.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), DC['label_bg']),
            ('BACKGROUND',    (2, 0), (2, 1),
             DC['fail_bg'] if total_fails else DC['pass_bg']),
            ('BACKGROUND',    (4, 0), (4, 1), ovr_bg),
            ('BOX',           (0, 0), (-1, -1), 0.5, DC['border']),
            ('INNERGRID',     (0, 0), (-1, -1), 0.5, DC['border']),
            ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(strip)
        elements.append(Spacer(1, 3))

        # Recommendation bar
        rec_bg = (DC['fail_bg'] if avg_yearly >= 1.0
                  else DC['warn_bg'] if avg_yearly >= 0.5
                  else DC['pass_bg'])
        rec_tc = (DC['fail_text'] if avg_yearly >= 1.0
                  else DC['warn_text'] if avg_yearly >= 0.5
                  else DC['pass_text'])
        rec_s  = ParagraphStyle('_rec', fontSize=8, fontName='Helvetica-Bold',
                                textColor=rec_tc, alignment=TA_LEFT)
        rec_tbl = Table(
            [[Paragraph(f"Recommendation: {recommendation}", rec_s)]],
            colWidths=[6.9 * inch]
        )
        rec_tbl.setStyle(TableStyle([
            ('BACKGROUND',    (0, 0), (-1, -1), rec_bg),
            ('BOX',           (0, 0), (-1, -1), 0.5, rec_tc),
            ('LEFTPADDING',   (0, 0), (-1, -1), 8),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
            ('TOPPADDING',    (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))
        elements.append(rec_tbl)
        elements.append(Spacer(1, 8))

        # ── MAIN UNIFIED PARAMETER TABLE ──────────────────────────────────────
        # Columns: Parameter | Sub-Param | Sessions | Last Status | Fail History |
        #          Last Error ±Unc | Drift/Year | Direction | Stability | R²
        COL_W = [1.10*inch, 0.82*inch, 0.50*inch, 0.68*inch, 0.72*inch,
                 0.88*inch, 0.72*inch, 0.88*inch, 0.72*inch, 0.54*inch]

        def hdr(txt):
            return Paragraph(txt, ParagraphStyle(
                '_h', fontSize=7, fontName='Helvetica-Bold',
                textColor=colors.white, alignment=TA_CENTER, leading=9))

        def cell(txt, align=TA_CENTER, bold=False, tc=HexColor('#1e293b')):
            fn = 'Helvetica-Bold' if bold else 'Helvetica'
            return Paragraph(txt, ParagraphStyle(
                '_c', fontSize=7.5, fontName=fn,
                textColor=tc, alignment=align, leading=10))

        def badge(txt, tc, bg):
            inner = Table([[Paragraph(txt, ParagraphStyle(
                '_ib', fontSize=7.5, fontName='Helvetica-Bold',
                textColor=tc, alignment=TA_CENTER, leading=10))]],
                colWidths=[0.60 * inch])
            inner.setStyle(TableStyle([
                ('BACKGROUND',    (0, 0), (-1, -1), bg),
                ('LEFTPADDING',   (0, 0), (-1, -1), 2),
                ('RIGHTPADDING',  (0, 0), (-1, -1), 2),
                ('TOPPADDING',    (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
            ]))
            return inner

        tbl_rows   = [[hdr('Parameter'), hdr('Sub-\nParam'), hdr('Sess.'),
                       hdr('Last\nStatus'), hdr('Fail\nHistory'),
                       hdr('Last Err\n\u00b1Unc'), hdr('Drift\n/Year'),
                       hdr('Direction'), hdr('Stability'), hdr('R\u00b2')]]
        row_styles = []

        dir_colors = {
            'Increasing \u25b2': (DC['fail_text'], HexColor('#fff1f2')),
            'Decreasing \u25bc': (DC['warn_text'], HexColor('#fffbeb')),
            'Stable':             (DC['pass_text'], HexColor('#f0fdf4')),
        }
        stab_colors = {
            'Excellent': (DC['pass_text'], DC['pass_bg']),
            'Good':      (DC['pass_text'], DC['pass_bg']),
            'Fair':      (DC['warn_text'], DC['warn_bg']),
            'Poor':      (DC['fail_text'], DC['fail_bg']),
        }

        for i, m in enumerate(all_metrics, start=1):
            row_bg = colors.white if i % 2 == 0 else DC['row_alt']

            # Last status badge
            if m['last_reading_pass']:
                lst_tc, lst_bg = DC['pass_text'], DC['pass_bg']
                lst_lbl = 'PASS'
            else:
                lst_tc, lst_bg = DC['fail_text'], DC['fail_bg']
                lst_lbl = 'FAIL'

            # Fail history badge
            fp = m['fail_rate'] * 100
            if fp == 0:
                fh_tc, fh_bg = DC['pass_text'], DC['pass_bg']
            elif fp < 40:
                fh_tc, fh_bg = DC['warn_text'], DC['warn_bg']
            else:
                fh_tc, fh_bg = DC['fail_text'], DC['fail_bg']
            fh_lbl = (f"{m['fail_count']}/{m['n_sessions']}"
                      if m['fail_count'] == 0
                      else f"{m['fail_count']}/{m['n_sessions']}\n({fp:.0f}%)")

            d_tc, d_bg = dir_colors.get(m['direction'], (HexColor('#0f172a'), colors.white))
            s_tc, s_bg = stab_colors.get(m['stability'], (HexColor('#0f172a'), colors.white))

            tbl_rows.append([
                cell(m['param_name'], align=TA_LEFT),
                cell(m['sub_param'] or '\u2014'),
                cell(str(m['n_sessions'])),
                badge(lst_lbl,        lst_tc, lst_bg),
                badge(fh_lbl,         fh_tc,  fh_bg),
                cell(f"{m['last_error']:+.5f}\n\u00b1{m['last_uncertainty']:.5f}"),
                cell(f"{m['drift_rate_per_year']:+.5f}"),
                badge(m['direction'],  d_tc,   d_bg),
                badge(m['stability'],  s_tc,   s_bg),
                cell(f"{m['r_squared']:.3f}"),
            ])
            row_styles.append(('BACKGROUND', (0, i), (-1, i), row_bg))

        base_style = [
            ('BACKGROUND',    (0, 0), (-1, 0), DC['hdr_bg']),
            ('TEXTCOLOR',     (0, 0), (-1, 0), colors.white),
            ('ALIGN',         (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
            ('LINEBELOW',     (0, 0), (-1, 0), 1.0, HexColor('#334155')),
            ('GRID',          (0, 0), (-1, -1), 0.4, DC['border']),
            ('LEFTPADDING',   (0, 0), (-1, -1), 3),
            ('RIGHTPADDING',  (0, 0), (-1, -1), 3),
            ('TOPPADDING',    (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ] + row_styles

        main_tbl = Table(tbl_rows, colWidths=COL_W)
        main_tbl.setStyle(TableStyle(base_style))
        elements.append(main_tbl)

        # Legend footnote
        elements.append(Spacer(1, 3))
        elements.append(Paragraph(
            "Last Status = most recent reading pass/fail per parameter  |  "
            "Fail History = failed readings across all sessions  |  "
            "Drift/Year = linear regression rate  |  "
            "R\u00b2 = regression fit confidence (1.000 = perfect)",
            ParagraphStyle('_leg', fontSize=6.5, fontName='Helvetica',
                           textColor=HexColor('#94a3b8'), leading=9)
        ))

        return elements

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

        doc_id_label  = "Reference Number" if self.is_declined else "Certificate Number"
        doc_id_value  = self.reference_number if self.is_declined else self.certificate_number

        footer_text = f"""
        {doc_id_label}: {doc_id_value} | Generated on: {self._now_eat().strftime('%Y-%m-%d %H:%M EAT')}<br/>
        Calibrated by: {calibrated_by}<br/>
        <i>Certificate authenticity verified through QR code scanning and digital signature</i>
        """

        return [Spacer(1, 6), Paragraph(footer_text, footer_style)]


# =============================================================================
# NEW FUNCTIONS ADDED FOR SIGNATURE DIAGNOSTICS AND VERIFICATION
# =============================================================================

def verify_user_signature_before_pdf(user):
    """
    Verify that user has a valid signature before generating PDF.
    Call this before calling generate_certificate().

    Returns:
        tuple: (has_signature: bool, error_message: str or None)
    """
    if not user:
        return False, "No user provided"

    # Check if user has signature
    if not hasattr(user, 'signature'):
        return False, f"User {user.username} does not have a signature object"

    try:
        signature = user.signature
    except Exception as e:
        return False, f"Error accessing signature: {str(e)}"

    # Check if signature has image
    if not hasattr(signature, 'signature_image') or not signature.signature_image:
        return False, f"User {user.username} has no signature image uploaded"

    # Check if file exists
    try:
        if not default_storage.exists(signature.signature_image.name):
            return False, f"Signature file does not exist in storage: {signature.signature_image.name}"
    except Exception as e:
        return False, f"Error checking signature file: {str(e)}"

    return True, None


def diagnose_signature_issue(user):
    """
    Diagnostic function to check why signature isn't loading.
    Run this in Django shell to debug signature issues.

    Usage:
        from CalSoft.pdf_generators import diagnose_signature_issue
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.get(username='your_username')
        diagnose_signature_issue(user)
    """
    print(f"\n{'='*60}")
    print(f"SIGNATURE DIAGNOSTIC FOR USER: {user.username}")
    print(f"{'='*60}\n")

    # Check 1: Does user have signature attribute?
    print("1. Checking signature attribute...")
    if hasattr(user, 'signature'):
        print("   ✓ User has 'signature' attribute")
        try:
            sig = user.signature
            print(f"   ✓ Signature object: {sig}")
            print(f"   - Signature ID: {sig.id}")
            print(f"   - Is active: {sig.is_active}")
            print(f"   - Created at: {sig.created_at}")
        except Exception as e:
            print(f"   ✗ Error accessing signature: {e}")
            return
    else:
        print("   ✗ User does NOT have 'signature' attribute")
        print("   → Need to create UserSignature object for this user")
        return

    # Check 2: Does signature have image field?
    print("\n2. Checking signature_image field...")
    if hasattr(sig, 'signature_image'):
        print("   ✓ Signature has 'signature_image' field")
        if sig.signature_image:
            print(f"   ✓ signature_image is not empty")
            print(f"   - Field name: {sig.signature_image.name}")
            print(f"   - Field size: {sig.signature_image.size} bytes")
            if hasattr(sig.signature_image, 'path'):
                print(f"   - Field path: {sig.signature_image.path}")
            if hasattr(sig.signature_image, 'url'):
                try:
                    print(f"   - Field URL: {sig.signature_image.url}")
                except:
                    print(f"   - Field URL: (unable to generate)")
        else:
            print("   ✗ signature_image field is EMPTY")
            print("   → Need to upload signature image")
            return
    else:
        print("   ✗ Signature does NOT have 'signature_image' field")
        return

    # Check 3: Does file exist in storage?
    print("\n3. Checking file in storage...")
    try:
        from django.core.files.storage import default_storage
        if default_storage.exists(sig.signature_image.name):
            print(f"   ✓ File EXISTS in storage: {sig.signature_image.name}")
            print(f"   - Storage location: {default_storage.location}")

            # Try to read file
            try:
                with default_storage.open(sig.signature_image.name, 'rb') as f:
                    data = f.read()
                    print(f"   ✓ File is READABLE: {len(data)} bytes")
            except Exception as e:
                print(f"   ✗ File exists but CANNOT BE READ: {e}")
        else:
            print(f"   ✗ File DOES NOT EXIST in storage: {sig.signature_image.name}")
            print(f"   - Storage location: {default_storage.location}")

            # Check if file exists in filesystem
            if hasattr(sig.signature_image, 'path'):
                full_path = sig.signature_image.path
                print(f"   - Checking filesystem path: {full_path}")
                if os.path.exists(full_path):
                    print(f"   ⚠ File EXISTS in filesystem but NOT in storage!")
                else:
                    print(f"   ✗ File DOES NOT EXIST in filesystem either")
    except Exception as e:
        print(f"   ✗ Error checking storage: {e}")

    # Check 4: Try to create PIL Image
    print("\n4. Testing image loading with PIL...")
    try:
        from PIL import Image as PILImage
        if hasattr(sig.signature_image, 'path') and os.path.exists(sig.signature_image.path):
            img = PILImage.open(sig.signature_image.path)
            print(f"   ✓ PIL can open image")
            print(f"   - Format: {img.format}")
            print(f"   - Size: {img.size}")
            print(f"   - Mode: {img.mode}")
        else:
            print("   ⚠ Cannot test PIL - no valid path")
    except Exception as e:
        print(f"   ✗ PIL cannot open image: {e}")

    # Check 5: Test ImageReader
    print("\n5. Testing ReportLab ImageReader...")
    try:
        from reportlab.lib.utils import ImageReader
        if default_storage.exists(sig.signature_image.name):
            with default_storage.open(sig.signature_image.name, 'rb') as img_file:
                img_data = io.BytesIO(img_file.read())
                img_data.seek(0)
                img_reader = ImageReader(img_data)
                print(f"   ✓ ImageReader can load image")
                print(f"   - Image size: {img_reader.getSize()}")
        else:
            print("   ⚠ Cannot test ImageReader - file not in storage")
    except Exception as e:
        print(f"   ✗ ImageReader failed: {e}")

    print(f"\n{'='*60}")
    print("DIAGNOSTIC COMPLETE")
    print(f"{'='*60}\n")


def generate_btwelve_certificate(session, context=None):
    """Generate a certificate using Department location from context."""
    generator = BtwelveHospitalCertificateGenerator(session, context)
    return generator.generate_certificate()


def verify_certificate_qr_with_results(qr_data):
    """
    Enhanced verification function to handle QR codes with results table data.
    """
    try:
        # Parse QR data
        parts = qr_data.split('|')
        cert_data = {}
        results_data = []
        collecting_results = False

        for part in parts:
            if part == "RESULTS:":
                collecting_results = True
                continue

            if collecting_results:
                # This is a result entry
                if ':' in part and '>' in part and not part.startswith('...'):
                    results_data.append(part)
                else:
                    # Handle additional info like "...+5more"
                    results_data.append(part)
            else:
                # This is metadata
                if ':' in part:
                    key, value = part.split(':', 1)
                    cert_data[key] = value

        # Parse individual results
        parsed_results = []
        for result in results_data:
            if ':' in result and '>' in result and not result.startswith('...'):
                try:
                    # Parse: "Parameter:SetValue>Mean±Error(Status)" or "Parameter-SubParam:SetValue>Mean±Error(Status)"
                    param_part, rest = result.split(':', 1)
                    set_part, measurement_part = rest.split('>', 1)

                    # Extract mean, error, and status
                    if '±' in measurement_part and '(' in measurement_part:
                        mean_error, status_part = measurement_part.split('(', 1)
                        status = status_part.rstrip(')')
                        mean, error = mean_error.split('±', 1)

                        # Check if it's a sub-parameter
                        if '-' in param_part:
                            main_param, sub_param = param_part.split('-', 1)
                            parsed_results.append({
                                'parameter': main_param,
                                'sub_parameter': sub_param,
                                'set_value': set_part,
                                'mean': mean,
                                'error': error,
                                'status': status
                            })
                        else:
                            parsed_results.append({
                                'parameter': param_part,
                                'sub_parameter': None,
                                'set_value': set_part,
                                'mean': mean,
                                'error': error,
                                'status': status
                            })
                except Exception as parse_error:
                    logger.warning(f"Could not parse result entry: {result} - {parse_error}")

        verification_result = {
            'valid': True,
            'certificate_number': cert_data.get('CERT', 'Unknown'),
            'issue_date': cert_data.get('DATE', 'Unknown'),
            'device_serial': cert_data.get('SERIAL', 'Unknown'),
            'device_model': cert_data.get('MODEL', 'Unknown'),
            'hospital': cert_data.get('HOSPITAL', 'Unknown'),
            'overall_status': cert_data.get('STATUS', 'Unknown'),
            'failure_rate': cert_data.get('FAIL_RATE', 'Unknown'),
            'readings_info': cert_data.get('READINGS', 'Unknown'),
            'results_count': len(parsed_results),
            'results': parsed_results,
            'has_more_results': any(r.startswith('...+') for r in results_data)
        }

        return verification_result

    except Exception as e:
        logger.error(f"Error verifying QR code with results: {str(e)}")
        return {
            'valid': False,
            'error': f'Invalid QR code format: {str(e)}'
        }


def generate_verification_report(verification_data):
    """
    Generate a human-readable verification report from QR code data.
    """
    if not verification_data.get('valid'):
        return f"INVALID CERTIFICATE\nError: {verification_data.get('error', 'Unknown error')}"

    report_lines = [
        "CERTIFICATE VERIFICATION REPORT",
        "=" * 40,
        f"Certificate Number: {verification_data['certificate_number']}",
        f"Issue Date: {verification_data['issue_date']}",
        f"Hospital: {verification_data['hospital']}",
        "",
        "DEVICE INFORMATION:",
        f"  Serial Number: {verification_data['device_serial']}",
        f"  Model: {verification_data['device_model']}",
        "",
        "CALIBRATION STATUS:",
        f"  Overall Status: {verification_data['overall_status']}",
        f"  Failure Rate: {verification_data['failure_rate']}",
        f"  Readings: {verification_data['readings_info']}",
    ]

    if verification_data.get('results'):
        report_lines.extend([
            "",
            f"CALIBRATION RESULTS ({verification_data['results_count']} shown):",
            "-" * 40
        ])

        current_param = None
        for i, result in enumerate(verification_data['results'], 1):
            # Group by parameter
            if result['parameter'] != current_param:
                current_param = result['parameter']
                report_lines.append(f"\n{current_param}:")

            status_symbol = "PASS" if result['status'] == 'PASS' else "FAIL"
            if result['sub_parameter']:
                report_lines.append(
                    f"  └─ {result['sub_parameter']}: {result['set_value']} → "
                    f"{result['mean']} (±{result['error']}) [{status_symbol}]"
                )
            else:
                report_lines.append(
                    f"  {result['set_value']} → "
                    f"{result['mean']} (±{result['error']}) [{status_symbol}]"
                )

        if verification_data.get('has_more_results'):
            report_lines.append("\n    ... additional results available in full certificate")

    report_lines.extend([
        "",
        "This verification is based on QR code data only.",
        "For complete validation, verify against laboratory database."
    ])

    return "\n".join(report_lines)


def verify_certificate_qr(qr_data):
    """
    Basic verification function for backward compatibility.
    """
    try:
        # Parse QR data
        parts = qr_data.split('|')
        cert_data = {}

        for part in parts:
            if ':' in part and not part.startswith('RESULTS'):
                key, value = part.split(':', 1)
                cert_data[key] = value

        return {
            'valid': True,  # This would be determined by database lookup
            'certificate_number': cert_data.get('CERT', 'Unknown'),
            'issue_date': cert_data.get('DATE', 'Unknown'),
            'device_serial': cert_data.get('SERIAL', 'Unknown'),
            'hospital': cert_data.get('HOSPITAL', 'Unknown')
        }

    except Exception as e:
        logger.error(f"Error verifying QR code: {str(e)}")
        return {
            'valid': False,
            'error': 'Invalid QR code format'
        }


def generate_verification_url(certificate_number, session):
    """
    Generate a verification URL for the certificate.
    This could be used as an alternative to QR codes or in addition to them.
    """
    try:
        base_url = getattr(settings, 'CERTIFICATE_VERIFICATION_URL', 'https://verify.btwelve.hospital')
        verification_token = base64.urlsafe_b64encode(
            f"{certificate_number}:{session.id}:{session.timestamp.timestamp()}".encode()
        ).decode()

        return f"{base_url}/verify/{verification_token}"

    except Exception as e:
        logger.error(f"Error generating verification URL: {str(e)}")
        return None


# =============================================================================
# USAGE INSTRUCTIONS AT BOTTOM OF FILE
# =============================================================================

"""
TO FIX YOUR SIGNATURE DISPLAY ISSUE:

1. THIS FILE NOW CONTAINS THE ENHANCED _build_signature_column method
   with detailed logging and multiple fallback methods.

2. TO DIAGNOSE SIGNATURE ISSUES, run in Django shell:

   python manage.py shell

   >>> from CalSoft.pdf_generators import diagnose_signature_issue
   >>> from django.contrib.auth import get_user_model
   >>> User = get_user_model()
   >>> user = User.objects.get(username='your_username')
   >>> diagnose_signature_issue(user)

3. TO VERIFY SIGNATURE BEFORE GENERATING PDF:

   >>> from CalSoft.pdf_generators import verify_user_signature_before_pdf
   >>> has_sig, error = verify_user_signature_before_pdf(user)
   >>> if not has_sig:
   >>>     print(f"Error: {error}")

4. COMMON ISSUES AND SOLUTIONS:

   Issue: "signature_image field is EMPTY"
   Solution: User needs to upload their signature in admin or user profile

   Issue: "File DOES NOT EXIST in storage"
   Solution: Signature file was deleted or never uploaded properly

   Issue: "User does NOT have 'signature' attribute"
   Solution: UserSignature object not created - create it:

   >>> from users.models import UserSignature
   >>> UserSignature.objects.create(user=user)
"""


# ==============================================================================
# ENHANCED DIAGNOSTIC FUNCTION
# ==============================================================================

def diagnose_signature(user):
    """
    Comprehensive signature diagnostic tool.

    Usage:
        python manage.py shell

        from CalSoft.pdf_generators import diagnose_signature
        from django.contrib.auth import get_user_model

        User = get_user_model()
        user = User.objects.get(username='wairegi1906')
        diagnose_signature(user)
    """
    print(f"\n{'='*70}")
    print(f"SIGNATURE DIAGNOSTIC FOR: {user.username}")
    print(f"{'='*70}\n")

    # Check signature object
    if not hasattr(user, 'signature'):
        print("❌ User has no 'signature' attribute")
        print("   Fix: Create UserSignature object")
        print("\n   from users.models import UserSignature")
        print(f"   UserSignature.objects.create(user=user)")
        return

    try:
        sig = user.signature
        print(f"✓ Signature object exists (ID: {sig.id})")
        print(f"  - Active: {getattr(sig, 'is_active', 'N/A')}")
        print(f"  - User drawn: {getattr(sig, 'is_user_drawn', 'N/A')}")
    except Exception as e:
        print(f"❌ Error accessing signature: {e}")
        return

    # Check signature_image field
    if not sig.signature_image:
        print("❌ signature_image field is empty")
        print("   Fix: Upload signature image via admin")
        print("\n   1. Go to /admin/users/usersignature/")
        print(f"   2. Find signature for {user.username}")
        print("   3. Upload signature image file")
        print("   4. Save")
        return

    print(f"✓ signature_image field: {sig.signature_image.name}")

    # Check file existence
    file_exists = False
    if default_storage.exists(sig.signature_image.name):
        print(f"✓ File exists in storage")
        file_exists = True
        try:
            with default_storage.open(sig.signature_image.name, 'rb') as f:
                size = len(f.read())
                print(f"  - Size: {size} bytes")
        except Exception as e:
            print(f"  - Error reading: {e}")
    else:
        print(f"❌ File NOT in storage: {sig.signature_image.name}")

    # Check file path
    if hasattr(sig.signature_image, 'path'):
        path = sig.signature_image.path
        if os.path.exists(path):
            print(f"✓ File exists at path: {path}")
            print(f"  - Size: {os.path.getsize(path)} bytes")
            file_exists = True
        else:
            print(f"❌ File NOT at path: {path}")

    # Try to load signature
    print(f"\n  Testing signature loading...")
    sig_img, status = SignatureImageLoader.load_signature(user)

    if sig_img:
        print(f"✓ Signature loaded successfully!")
        print(f"   Method: {status}")
        print("\n✅ DIAGNOSIS: Signatures will work in PDFs")
    else:
        print(f"❌ Signature loading failed")
        print(f"   Reason: {status}")

        if not file_exists:
            print("\n  RECOMMENDED ACTION:")
            print("  Re-upload signature file:")
            print("\n  python manage.py shell")
            print("  >>> from django.core.files import File")
            print(f"  >>> user = User.objects.get(username='{user.username}')")
            print("  >>> with open('/path/to/signature.png', 'rb') as f:")
            print(f"  >>>     user.signature.signature_image.save('signature_{user.username}.png', File(f), save=True)")
        else:
            print("\n  RECOMMENDED ACTION:")
            print("  1. Check file permissions (should be readable)")
            print("  2. Verify MEDIA_ROOT setting")
            print("  3. Try re-uploading signature via admin")

    print(f"\n{'='*70}\n")
