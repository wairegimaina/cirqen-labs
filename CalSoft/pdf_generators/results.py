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
