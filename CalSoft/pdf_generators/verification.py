"""CalSoft.pdf_generators — QR verification / verification-report helpers."""
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
            # Parsing a payload is not verifying it — see verify_certificate_qr
            # below. This reports that the payload was readable, and leaves
            # authenticity to a caller that can check it against the record.
            'valid': None,
            'verification_available': False,
            'parsed': True,
            'reason': (
                'The QR payload is not signed, so authenticity cannot be '
                'established from it. Confirm this certificate number against '
                'the issuing record.'
            ),
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
            'has_more_results': any('MORE POINTS NOT IN QR' in r for r in results_data)
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

        # 'valid' reported whether the payload PARSED, never whether the
        # certificate was genuine — it returned True for any input, including a
        # forged or hand-typed one, while the certificate told the reader the QR
        # code proved authenticity.
        #
        # Until the payload is signed and checked against the issuing record,
        # the honest answer is that this function cannot establish validity. It
        # now says so, and returns the parsed fields for lookup by a caller that
        # can.
        return {
            'valid': None,
            'verification_available': False,
            'reason': (
                'The QR payload is not signed, so authenticity cannot be '
                'established from it. Confirm this certificate number against '
                'the issuing record.'
            ),
            'certificate_number': cert_data.get('CERT', 'Unknown'),
            'issue_date': cert_data.get('DATE', 'Unknown'),
            'device_serial': cert_data.get('SERIAL', 'Unknown'),
            'hospital': cert_data.get('HOSPITAL', 'Unknown'),
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
