"""CalSoft.pdf_generators.qr — QR-code generation for certificate verification."""

"""CalSoft.pdf_generators — the B-12 hospital certificate generator + thin wrapper."""
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

matplotlib.use("Agg")
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

# sibling modules in this package
from core.branding import organisation_slug
from .signatures import SignatureImageLoader
from .watermark import _LogoWatermarkCanvas


class QrMixin:
    def generate_qr_code(self):
        """Generate QR code for certificate verification including results table."""
        try:
            # Use reference number for declined docs, certificate number otherwise
            # Declined and awaiting-issue documents both carry a reference
            # number rather than a certificate number. Without this, a session
            # whose number HQ has not yet allocated rendered the string "None"
            # into the QR payload.
            cert_number = (
                self.reference_number
                if (self.is_declined or getattr(self, 'is_pending_number', False))
                else (str(self.certificate_number) if self.certificate_number else "N/A")
            )

            # Get results data
            results_data = []
            grouped_readings = self.group_readings_by_parameter()

            for param_name, param_data in grouped_readings.items():
                unit = param_data["parameter"].unit if param_data["parameter"].unit else ""

                if param_data["sub_parameters"]:
                    for sub_param_name, sub_param_data in param_data["sub_parameters"].items():
                        for reading in sub_param_data["readings"]:
                            set_value = str(reading.set_value.value) if reading.set_value else "N/A"
                            mean = f"{reading.mean:.4f}" if reading.mean is not None else "N/A"
                            error = f"{reading.error:.4f}" if reading.error is not None else "N/A"

                            # Determine pass/fail status
                            passes_tolerance = getattr(reading, "passes_tolerance", True)
                            status = "PASS" if passes_tolerance else "FAIL"

                            # Create compact result entry
                            # "mean err=..." rather than "mean±...": a ± after a
                            # mean conventionally introduces an uncertainty, and
                            # printing the deviation there invites it to be read
                            # as one.
                            result_entry = f"{param_name}-{sub_param_name}:{set_value}{unit}>{mean} err={error}({status})"
                            results_data.append(result_entry)
                elif param_data["readings"]:
                    for reading in param_data["readings"]:
                        set_value = str(reading.set_value.value) if reading.set_value else "N/A"
                        mean = f"{reading.mean:.4f}" if reading.mean is not None else "N/A"
                        error = f"{reading.error:.4f}" if reading.error is not None else "N/A"

                        # Determine pass/fail status
                        passes_tolerance = getattr(reading, "passes_tolerance", True)
                        status = "PASS" if passes_tolerance else "FAIL"

                        # Create compact result entry
                        result_entry = f"{param_name}:{set_value}{unit}>{mean} err={error}({status})"
                        results_data.append(result_entry)

            # Create comprehensive verification data
            verification_data = {
                "certificate_number": cert_number,
                "issued_date": (
                    self.session.timestamp.astimezone(EAT).strftime("%Y-%m-%d")
                    if self.session.timestamp
                    else self._now_eat().strftime("%Y-%m-%d")
                ),
                "device_serial": getattr(self.session, "device_serial", "N/A"),
                "device_model": getattr(self.session, "device_model", "N/A"),
                "hospital": organisation_slug(),
                "overall_status": "FAILED" if self.conformity_failed else "PASSED",
                "failure_rate": f"{self.failure_stats['overall_failure_rate']:.1%}",
                "total_readings": str(self.failure_stats["total_readings"]),
                "failed_readings": str(self.failure_stats["failed_readings"]),
                # A QR code has a finite capacity, so long result sets are
                # truncated — but silently dropping points makes the payload
                # look complete when it is not. The marker says what is missing.
                "results": (
                    results_data[:15] + [f"...{len(results_data) - 15} MORE POINTS NOT IN QR"]
                    if len(results_data) > 15 else results_data
                ),
                "results_total": str(len(results_data)),
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
            if verification_data["results"]:
                verification_components.append("RESULTS:")
                verification_components.extend(verification_data["results"])

            verification_string = "|".join(verification_components)

            # Check string length and adjust if necessary
            max_qr_length = 2500
            if len(verification_string) > max_qr_length:
                truncated_results = []
                current_length = len("|".join(verification_components[:8]))

                for result in verification_data["results"]:
                    if current_length + len(result) + 1 < max_qr_length:
                        truncated_results.append(result)
                        current_length += len(result) + 1
                    else:
                        break

                verification_components = verification_components[:8]
                if truncated_results:
                    verification_components.append("RESULTS:")
                    verification_components.extend(truncated_results)
                    if len(truncated_results) < len(verification_data["results"]):
                        verification_components.append(
                            f"...+{len(verification_data['results']) - len(truncated_results)}more"
                        )

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
            qr_filename = f"qrcodes/cert_{self.session.id}_qr.png"
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
                basic_verification = f"CERT:{self.certificate_number or self.reference_number}|DATE:{self.session.timestamp.astimezone(EAT).strftime('%Y-%m-%d') if self.session.timestamp else self._now_eat().strftime('%Y-%m-%d')}|SERIAL:{getattr(self.session, 'device_serial', 'N/A')}|HOSPITAL:{organisation_slug()}|STATUS:{'FAILED' if self.conformity_failed else 'PASSED'}"

                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_M,
                    box_size=10,
                    border=4,
                )
                qr.add_data(basic_verification)
                qr.make(fit=True)

                qr_img = qr.make_image(fill_color="black", back_color="white")
                qr_filename = f"qrcodes/cert_{self.session.id}_qr_basic.png"
                qr_path = os.path.join(settings.MEDIA_ROOT, qr_filename)
                os.makedirs(os.path.dirname(qr_path), exist_ok=True)
                qr_img.save(qr_path)

                logger.info(f"Fallback basic QR code generated")
                return qr_path

            except Exception as fallback_error:
                logger.error(f"Error generating fallback QR code: {str(fallback_error)}")
                return None
