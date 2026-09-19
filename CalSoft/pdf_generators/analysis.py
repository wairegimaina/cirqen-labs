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
from .signatures import SignatureImageLoader
from .watermark import _LogoWatermarkCanvas


class AnalysisMixin:
    def compute_linearity(self):
        """Least-squares linearity per parameter, from the shared calculator.

        Returns a list of dicts, one per parameter or sub-parameter with at
        least two usable points.

        The certificate previously showed only a scatter plot of set value
        against mean — no fitted line, no residuals and no computed linearity
        error — so the section was labelled analysis but contained none. The
        arithmetic lives in ``CalibrationCalculator.calculate_linearity``, the
        same implementation the session detail page uses.
        """
        from CalSoft.utils import CalibrationCalculator

        calculator = CalibrationCalculator()
        results = []

        def series(label, readings):
            pairs = [
                (float(r.set_value.value), float(r.mean))
                for r in readings
                if r.set_value is not None and r.mean is not None
            ]
            if len(pairs) < 2:
                return
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            try:
                fit = calculator.calculate_linearity(xs, ys)
            except (ValueError, ZeroDivisionError) as exc:
                logger.warning("Linearity unavailable for %s: %s", label, exc)
                return
            results.append({
                "label": label,
                "points": len(pairs),
                "slope": float(fit["slope"]),
                "intercept": float(fit["intercept"]),
                "max_error": abs(float(fit["max_linearity_error_units"])),
                "max_error_percent": abs(float(fit["max_linearity_error_percent"])),
                "set_values": xs,
                "means": ys,
            })

        for param_name, param_data in self.group_readings_by_parameter().items():
            if param_data["sub_parameters"]:
                for sub_name, sub_data in param_data["sub_parameters"].items():
                    series(f"{param_name} - {sub_name}", sub_data["readings"])
            elif param_data["readings"]:
                series(param_name, param_data["readings"])

        return results

    def build_linearity_content(self):
        """Linearity: the fitted line, the residual, and what each one means."""
        elements = []

        if not (hasattr(self.session, "readings") and self.session.readings.exists()):
            return [Paragraph("No readings available for linearity analysis.",
                              self.styles["NormalText"])]

        analyses = self.compute_linearity()

        if not analyses:
            return [Paragraph(
                "Linearity analysis requires at least two test points per parameter. "
                "Not enough points were measured on this calibration.",
                self.styles["NormalText"],
            )]

        # Slope, intercept and residual diagnose three different faults, so
        # they are reported separately rather than rolled into one number.
        head = ["Parameter", "Points", "Slope", "Intercept",
                "Max deviation from fit", "% of span"]
        rows = [head]
        for a in analyses:
            rows.append([
                a["label"],
                str(a["points"]),
                f"{a['slope']:.6f}",
                f"{a['intercept']:+.6f}",
                f"{a['max_error']:.6f}",
                f"{a['max_error_percent']:.3f}%",
            ])

        table = Table(rows, colWidths=[2.1 * inch, 0.5 * inch, 0.9 * inch,
                                       0.9 * inch, 1.4 * inch, 0.8 * inch])
        table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("BACKGROUND", (0, 0), (-1, 0), HexColor("#e5e7eb")),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("BOX", (0, 0), (-1, -1), 0.5, HexColor("#d1d5db")),
            ("INNERGRID", (0, 0), (-1, -1), 0.25, HexColor("#d1d5db")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, HexColor("#f9fafb")]),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(table)
        elements.append(Spacer(1, 4))

        elements.append(Paragraph(
            "Slope near 1.000 and intercept near 0 indicate a device needing no "
            "span or zero adjustment. An intercept away from zero is an offset "
            "and is usually zero-adjustable; a slope away from 1.000 is a gain "
            "error and is usually span-adjustable. The maximum deviation from "
            "the fitted line is true non-linearity, which is not adjustable and "
            "limits how far the device can be trusted between the points tested.",
            self.styles["NormalText"],
        ))
        elements.append(Spacer(1, 6))

        chart_path = self.generate_linearity_chart()
        if chart_path and os.path.exists(chart_path):
            try:
                elements.append(Image(chart_path, width=5.6 * inch, height=2.4 * inch))
            except Exception as e:
                logger.error(f"Error adding linearity chart: {e}")

        return elements

    def generate_linearity_chart(self):
        """Plot each parameter against its fitted line, plus the residuals.

        The previous chart drew set value against mean and nothing else, so the
        one thing it was supposed to show — how far the device departs from a
        straight line — was invisible: at certificate scale a 1% non-linearity
        on a 45-degree line cannot be seen. The fitted line and a residual
        panel make the deviation legible.
        """
        try:
            analyses = self.compute_linearity()
            if not analyses:
                return None

            fig, (ax, ax_res) = plt.subplots(
                2, 1, figsize=(10, 5), sharex=True,
                gridspec_kw={"height_ratios": [2, 1]},
            )

            for a in analyses:
                xs, ys = a["set_values"], a["means"]
                line = ax.plot(xs, ys, "o", markersize=5, label=a["label"])[0]
                colour = line.get_color()

                fitted = [a["slope"] * x + a["intercept"] for x in xs]
                ax.plot(xs, fitted, "-", linewidth=1.2, color=colour, alpha=0.85)

                residuals = [y - f for y, f in zip(ys, fitted)]
                ax_res.plot(xs, residuals, "o-", markersize=4, linewidth=1,
                            color=colour, label=a["label"])

            ax.set_ylabel("Measured value", fontsize=10)
            ax.set_title("Linearity: measured values against the fitted line", fontsize=12)
            ax.grid(True, alpha=0.3)
            if ax.get_legend_handles_labels()[0]:
                ax.legend(fontsize=8)

            ax_res.axhline(0, color="#374151", linewidth=0.8)
            ax_res.set_xlabel("Set value", fontsize=10)
            ax_res.set_ylabel("Deviation\nfrom fit", fontsize=9)
            ax_res.grid(True, alpha=0.3)

            chart_path = os.path.join(
                settings.MEDIA_ROOT, f"charts/linearity_{self.session.id}.png"
            )
            os.makedirs(os.path.dirname(chart_path), exist_ok=True)
            plt.tight_layout()
            plt.savefig(chart_path, dpi=200, bbox_inches="tight")
            plt.close(fig)

            return chart_path

        except Exception as e:
            logger.error(f"Error generating linearity chart: {str(e)}")
            plt.close("all")
            return None

    def build_failure_analysis(self):
        """Build failure analysis section for failed calibrations."""
        elements = []

        if not self.failure_stats["failed_parameters"] and not self.conformity_failed:
            return [Paragraph("No significant failures detected.", self.styles["NormalText"])]

        # Overall failure summary. The verdict is conformity; the rate is a
        # triage signal about how widespread the failure is, and is labelled as
        # such so it cannot be read as the verdict.
        if not self.conformity_failed:
            status = "PASSED - all points within tolerance"
        elif self.is_failed_report:
            status = "FAILED - widespread failure, device requires repair"
        else:
            status = "FAILED - one or more points outside tolerance"

        summary_text = f"""
        <b>Status:</b> {status}<br/>
        <b>Points outside tolerance:</b>
        {self.failure_stats['failed_readings']} of {self.failure_stats['total_readings']}
        ({self.failure_stats['overall_failure_rate']:.1%})<br/>
        <b>Conformity rule:</b> strict - a single point outside tolerance fails the calibration<br/>
        <b>Repair threshold:</b> above 40% of points indicates a device fault rather than drift
        """
        elements.append(Paragraph(summary_text, self.styles["FailureText"]))
        elements.append(Spacer(1, 8))

        # Parameter-specific failure analysis
        if self.failure_stats["failed_parameters"]:
            elements.append(
                Paragraph("<b>Failed Parameters Analysis:</b>", self.styles["FailureText"])
            )

            failure_data = [["Parameter", "Failed/Total", "Failure Rate", "Recommendation"]]

            for param_failure in self.failure_stats["failed_parameters"]:
                failure_rate = f"{param_failure['failure_rate']:.1%}"
                ratio = f"{param_failure['failed_count']}/{param_failure['total_count']}"

                # Generate recommendation based on failure rate
                if param_failure["failure_rate"] >= 0.80:
                    recommendation = "Critical - Immediate repair required"
                elif param_failure["failure_rate"] >= 0.60:
                    recommendation = "Major issue - Service needed"
                else:
                    recommendation = "Minor issue - Monitor closely"

                failure_data.append([param_failure["name"], ratio, failure_rate, recommendation])

            failure_table = Table(
                failure_data, colWidths=[2 * inch, 1 * inch, 1 * inch, 2.9 * inch]
            )
            failure_table.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
                        ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#fecaca")),
                        ("BACKGROUND", (0, 1), (-1, -1), HexColor("#fef2f2")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ("TEXTCOLOR", (0, 0), (-1, -1), HexColor("#991b1b")),
                    ]
                )
            )

            elements.append(failure_table)

        return elements
