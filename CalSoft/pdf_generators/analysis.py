"""CalSoft.pdf_generators.analysis — linearity chart and failure-analysis builders."""
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



class AnalysisMixin:
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
