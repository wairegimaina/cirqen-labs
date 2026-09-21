"""CalSoft.pdf_generators.results — measurement results + uncertainty tables and failure stats."""
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
from CalSoft.utils import (
    TUR_FLOOR,
    coverage_factor_for_95,
    guarded_decision,
    test_uncertainty_ratio,
)
from .watermark import _LogoWatermarkCanvas



class ResultsMixin:
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

        # Table headers. TUR and the guarded verdict are here because a bare
        # pass/fail against tolerance ignores the uncertainty this certificate
        # spends a whole table computing. TUR says whether the measurement is
        # sharp enough to judge the limit; the guarded verdict says what the
        # answer is once the uncertainty is counted.
        table_data = [
            ['Set Value', 'Mean', 'Std Dev', 'Error', 'Tolerance', 'TUR', 'Status']
        ]

        # Add readings data
        for reading in readings:
            set_value = str(reading.set_value.value) if reading.set_value else 'N/A'
            mean = f"{reading.mean:.4f}" if reading.mean is not None else 'N/A'
            std_dev = f"{reading.standard_deviation:.4f}" if getattr(reading, 'standard_deviation', None) is not None else 'N/A'
            error = f"{reading.error:.4f}" if reading.error is not None else 'N/A'

            # Determine tolerance
            tolerance = 'N/A'
            if reading.sub_parameter and reading.sub_parameter.tolerance:
                tolerance = str(reading.sub_parameter.tolerance)
            elif reading.parameter and reading.parameter.tolerance:
                tolerance = str(reading.parameter.tolerance)

            # Determine pass/fail status
            passes_tolerance = getattr(reading, 'passes_tolerance', True)
            if not hasattr(reading, 'passes_tolerance') and reading.error is not None and reading.parameter:
                tol_value = None
                if reading.sub_parameter and reading.sub_parameter.tolerance:
                    tol_value = float(reading.sub_parameter.tolerance)
                elif reading.parameter and reading.parameter.tolerance:
                    tol_value = float(reading.parameter.tolerance)

                if tol_value:
                    passes_tolerance = abs(float(reading.error)) <= tol_value

            # Guarded verdict: the uncertainty is allowed to change the answer,
            # and a reading the measurement cannot decide says so.
            tol_value = None
            if reading.sub_parameter and reading.sub_parameter.tolerance:
                tol_value = reading.sub_parameter.tolerance
            elif reading.parameter and reading.parameter.tolerance:
                tol_value = reading.parameter.tolerance

            expanded = getattr(reading, 'expanded_uncertainty', None)
            tur = test_uncertainty_ratio(tol_value, expanded)
            tur_display = f"{tur:.1f}:1" if tur is not None else 'N/A'

            if reading.error is not None and tol_value is not None:
                verdict, _, _ = guarded_decision(reading.error, tol_value, expanded)
            else:
                verdict = 'PASS' if passes_tolerance else 'FAIL'

            table_data.append([set_value, mean, std_dev, error, tolerance,
                               tur_display, verdict])

        # Create table with proper column widths
        readings_table = Table(table_data, colWidths=[1.0*inch, 1.1*inch, 0.9*inch, 0.9*inch, 0.9*inch, 0.7*inch, 1.4*inch])

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

        # Highlight by verdict. INDETERMINATE gets its own colour because it is
        # neither a pass nor a failure — it is a measurement that cannot decide.
        status_col = 6
        for i, row in enumerate(table_data[1:], 1):  # Skip header row
            verdict = row[status_col]
            if verdict == 'FAIL':
                table_style.extend([
                    ('BACKGROUND', (0, i), (-1, i), HexColor('#fef2f2')),
                    ('TEXTCOLOR', (status_col, i), (status_col, i), HexColor('#dc2626')),
                    ('FONTNAME', (status_col, i), (status_col, i), 'Helvetica-Bold'),
                ])
            elif verdict == 'INDETERMINATE':
                table_style.extend([
                    ('BACKGROUND', (0, i), (-1, i), HexColor('#fffbeb')),
                    ('TEXTCOLOR', (status_col, i), (status_col, i), HexColor('#92400e')),
                    ('FONTNAME', (status_col, i), (status_col, i), 'Helvetica-Bold'),
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

        # Table headers.
        #
        # The Reference column is not decoration: without it the printed budget
        # does not add up. Combined is the root sum of squares of Type A, Type B
        # AND Reference, so a table showing only the first two leaves an
        # assessor with an unexplained discrepancy. "n" is here for the same
        # reason — Type A depends on it, and it cannot be checked otherwise.
        uncertainty_data = [
            ['Set Value', 'n', 'Type A', 'Type B', 'Reference', 'Combined', 'Expanded', 'k']
        ]

        # Add uncertainty data
        for reading in readings:
            set_value = str(reading.set_value.value) if reading.set_value else 'N/A'
            n_readings = len(reading.get_readings_list()) if hasattr(reading, 'get_readings_list') else None
            n_display = str(n_readings) if n_readings else 'N/A'
            type_a = self._format_uncertainty(getattr(reading, 'type_a_uncertainty', None))
            type_b = self._format_uncertainty(getattr(reading, 'type_b_uncertainty', None))
            reference = self._format_uncertainty(getattr(reading, 'reference_uncertainty_component', None))
            combined = self._format_uncertainty(getattr(reading, 'combined_uncertainty', None))
            expanded = self._format_uncertainty(getattr(reading, 'expanded_uncertainty', None))
            # Print the k that was actually applied, not the one configured on
            # the parameter. Expanded uncertainty is now expanded by a coverage
            # factor derived from the effective degrees of freedom, so printing
            # the configured value would leave Expanded != Combined x k on the
            # page. Derived from the stored components through the same shared
            # function, so the two cannot drift apart.
            k_factor = self._coverage_factor_used(reading)

            uncertainty_data.append([set_value, n_display, type_a, type_b, reference,
                                     combined, expanded, k_factor])

        # Create uncertainty table
        uncertainty_table = Table(
            uncertainty_data,
            colWidths=[0.95*inch, 0.35*inch, 0.90*inch, 0.90*inch, 0.95*inch,
                       0.95*inch, 0.95*inch, 0.45*inch],
        )
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

    @staticmethod
    def _format_uncertainty(value, min_places=4, max_places=8):
        """Format an uncertainty so a small component does not print as zero.

        Fixed 4-decimal formatting rendered anything below 0.00005 as "0.0000":
        a 0.0001 resolution gives a component of 0.0000289, which vanished. This
        keeps 4 decimals for ordinary magnitudes and extends — up to the 6
        decimals actually stored, with headroom — only when the value would
        otherwise round away to nothing.
        """
        if value is None:
            return 'N/A'
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 'N/A'
        if numeric == 0:
            return '0.0000'
        # Extend the precision until at least two significant digits survive,
        # so a small component reads as a number rather than as zero.
        places = min_places
        while places < max_places and abs(numeric) * (10 ** places) < 10:
            places += 1
        return f"{numeric:.{places}f}"

    @staticmethod
    def _coverage_factor_used(reading):
        """The coverage factor actually applied to this reading's uncertainty.

        The budget expands by ``max(stated k, k derived from the effective
        degrees of freedom)``, so the configured parameter value is a floor
        rather than the answer. This recovers the applied value from the stored
        components using the same helper the budget uses.
        """
        stated = Decimal('2')
        if reading.parameter and reading.parameter.coverage_factor:
            stated = Decimal(str(reading.parameter.coverage_factor))

        type_a = getattr(reading, 'type_a_uncertainty', None)
        combined = getattr(reading, 'combined_uncertainty', None)
        count = len(reading.get_readings_list()) if hasattr(reading, 'get_readings_list') else 0

        if type_a is None or combined is None or count < 2:
            return f"{stated.normalize():f}"

        derived, _ = coverage_factor_for_95(type_a, combined, count)
        applied = max(stated, derived)
        return f"{applied.normalize():f}"

    def measurement_capability_warnings(self):
        """Parameters whose measurement is not sharp enough to judge the limit.

        TUR below 4:1 means the expanded uncertainty is a large fraction of the
        tolerance, so a bare pass on a marginal point overstates what is known.
        The ratio was already printed per point; this surfaces it as a warning
        so it is not left to the reader to notice.
        """
        offenders = {}
        try:
            readings = self.session.readings.select_related('parameter', 'sub_parameter')
        except Exception:
            # Returning [] here would print a certificate with no capability
            # warning, which reads as "every parameter is fine" rather than
            # "the check did not run". Logged loudly so the difference is
            # recoverable from the logs.
            logger.exception(
                "[TUR] Could not read readings for session %s; no measurement-"
                "capability warnings will appear on this certificate",
                getattr(self.session, 'id', '?'),
            )
            return []

        for reading in readings:
            tolerance = None
            if reading.sub_parameter and reading.sub_parameter.tolerance is not None:
                tolerance = reading.sub_parameter.tolerance
            elif reading.parameter and reading.parameter.tolerance is not None:
                tolerance = reading.parameter.tolerance

            tur = test_uncertainty_ratio(tolerance, getattr(reading, 'expanded_uncertainty', None))
            if tur is None or tur >= TUR_FLOOR:
                continue

            name = reading.parameter.name if reading.parameter else 'Unknown'
            if reading.sub_parameter:
                name = f"{name} - {reading.sub_parameter.name}"
            # Keep the worst ratio per parameter; one line per parameter reads
            # better than one per test point.
            if name not in offenders or tur < offenders[name]:
                offenders[name] = tur

        return [
            {'parameter': name, 'tur': ratio}
            for name, ratio in sorted(offenders.items(), key=lambda kv: kv[1])
        ]

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
                elif reading.error is not None and reading.parameter:
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
