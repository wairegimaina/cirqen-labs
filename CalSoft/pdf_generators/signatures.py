"""CalSoft.pdf_generators — signature image loading and signature diagnostics."""
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
