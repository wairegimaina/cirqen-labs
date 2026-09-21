"""CalSoft.pdf_generators.sections — UUT / procedure / calibration info section builders."""
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
from ..models import Standard
from calSchedules.grouping import next_due_date

logger = logging.getLogger(__name__)

# sibling modules in this package
from .signatures import SignatureImageLoader
from .watermark import _LogoWatermarkCanvas



class SectionsMixin:
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

        # Due on the last day of the month the interval lands in, so the
        # workshop has that whole month to schedule the visit.
        #
        # This used `timedelta(days=365)`, which is a day short of a year
        # whenever a leap day falls in between, and assumed every device is on
        # a 12-month interval regardless of what its schedule says.
        if self.session.timestamp:
            ts_eat = self.session.timestamp.astimezone(EAT)
            interval = getattr(self.session.schedule, 'calibration_period', None) or 12
            cal_due = next_due_date(ts_eat.date(), interval).strftime('%Y-%m-%d')
        else:
            cal_due = 'N/A'

        if self.is_declined:
            doc_label  = "Reference No:"
            doc_number = self.reference_number or "N/A"
        else:
            doc_label  = "Certificate No:"
            doc_number = self.certificate_number or self.reference_number or "N/A"

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
                        standard = Standard.objects.get(serial_number=param.standard_reference)
                        # Use serial number as key to avoid duplicates
                        if standard.serial_number not in standards_dict:
                            standards_dict[standard.serial_number] = standard
                    except Standard.DoesNotExist:
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

    def build_traceability_content(self):
        """Build traceability content."""
        traceability_text = """
        The measurements reported in this certificate are traceable to the International System of Units (SI) through an
        unbroken chain of calibrations or comparisons. All measuring equipment used has been calibrated using standards
        whose accuracies are commensurate with the uncertainties reported in this certificate.
        """
        return [Paragraph(traceability_text, self.styles['NormalText'])]

    def _coverage_factor_note(self):
        """Describe the coverage factor(s) actually applied on this certificate.

        Two corrections are folded in here. The note used to assert "k=2"
        unconditionally while the k column beside it printed each parameter's
        configured value, so a parameter set to k=3 produced a certificate that
        contradicted itself on one page. And k is no longer simply the
        configured value: it is derived from the effective degrees of freedom
        and the configured value acts as a floor, so with few readings it
        exceeds 2.
        """
        default = "k=2"
        try:
            factors = sorted({
                self._coverage_factor_used(reading)
                for reading in self.session.readings.all()
                if reading.combined_uncertainty is not None
            })
        except Exception:
            # Falling back to "k=2" silently would state a coverage factor that
            # may not be the one applied — the exact defect this method was
            # written to fix. Logged so a wrong k on a certificate is traceable.
            logger.exception(
                "[NOTES] Could not determine the coverage factors used for "
                "session %s; falling back to the conventional statement",
                getattr(self.session, 'id', '?'),
            )
            return default

        if not factors:
            return default
        if len(factors) == 1:
            return (
                f"k={factors[0]}, derived from the effective degrees of freedom "
                f"(Welch-Satterthwaite) with the configured factor as a floor"
            )
        return (
            "k as stated per parameter in the uncertainty table ("
            + ", ".join(factors)
            + "), each derived from that parameter's effective degrees of freedom"
        )

    def build_notes_content(self):
        """Build notes content with failure-specific notes."""
        base_notes = [
            "1. This certificate relates only to the item(s) calibrated and the results are valid at the time and under the conditions of calibration.",
            "2. This certificate shall not be reproduced except in full without written approval.",
            "3. DECISION RULE: conformity is assessed by guard banding. A point is "
            "reported PASS when the measured deviation lies within the tolerance "
            "reduced by the expanded uncertainty, FAIL when it lies beyond the "
            "tolerance increased by the expanded uncertainty, and INDETERMINATE "
            "when it falls between the two — the measurement cannot decide. A "
            "single point failing fails the calibration.",
            f"4. The uncertainty of measurement is stated as the expanded uncertainty "
            f"calculated using a coverage factor {self._coverage_factor_note()}, "
            f"corresponding to a confidence level of approximately 95%.",
            "5. Where the test uncertainty ratio is below 4:1 the measurement is not sharp enough to judge the tolerance by simple comparison, and the guarded rule above governs."
        ]

        # Name the parameters that fall below the ratio, rather than leaving
        # the reader to compare every printed TUR against the floor.
        capability = self.measurement_capability_warnings()
        if capability:
            listed = "; ".join(
                f"{w['parameter']} ({w['tur']:.1f}:1)" for w in capability
            )
            base_notes.append(
                f"6. MEASUREMENT CAPABILITY: the following parameters were measured "
                f"with a test uncertainty ratio below the customary 4:1 floor: "
                f"{listed}. For these, the guarded decision rule in note 3 governs, "
                f"and a result reported PASS close to the tolerance should be "
                f"treated as provisional. Improving repeatability or using a "
                f"better reference standard would raise the ratio."
            )

        if self.conformity_failed:
            failed = self.failure_stats['failed_readings']
            total = self.failure_stats['total_readings']
            n = len(base_notes)
            failure_notes = [
                f"{n + 1}. THIS DEVICE HAS FAILED CALIBRATION. {failed} of {total} measured "
                f"points fell outside the permitted tolerance. A single point outside "
                f"tolerance fails the calibration.",
                f"{n + 2}. The device must not be returned to clinical service on the basis of "
                "this document.",
                f"{n + 3}. A new calibration must be performed after any adjustment or repair.",
                f"{n + 4}. Contact the Biomedical Engineering Department for repair coordination.",
            ]
            if self.is_failed_report:
                failure_notes.append(
                    f"{n + 5}. The proportion of failed points exceeds 40%, which indicates a "
                    "device fault rather than isolated drift. Remove from service "
                    "immediately."
                )
            base_notes.extend(failure_notes)
        else:
            base_notes.append(f"{len(base_notes) + 1}. Calibration interval recommendation: 12 months from calibration date.")

        notes_text = "<br/>".join(base_notes)
        return [Paragraph(notes_text, self.styles['NormalText'])]
