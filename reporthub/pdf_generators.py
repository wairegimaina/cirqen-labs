import io
import os
import logging
from datetime import datetime
from django.conf import settings
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.lib.colors import HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.graphics.shapes import Drawing, String, Rect
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.charts.legends import Legend

from reporthub.utils import get_report_data
from core.branding import contact_line

logger = logging.getLogger(__name__)

class _LogoWatermarkCanvas(rl_canvas.Canvas):
    """Draws logo watermark OVER page content via showPage() hook."""
    def __init__(self, filename, logo_path=None, wm_alpha=0.20, wm_scale=0.52, **kwargs):
        self._wm_logo_path = logo_path
        self._wm_alpha = wm_alpha
        self._wm_scale = wm_scale
        rl_canvas.Canvas.__init__(self, filename, **kwargs)

    def showPage(self):
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
                    mask="auto",
                )
                self.restoreState()
            except Exception as _wm_err:
                import logging
                logging.getLogger(__name__).warning(f"[WATERMARK] Overlay draw failed: {_wm_err}")
        rl_canvas.Canvas.showPage(self)

class PDFReportGenerator:
    """
    Professional PDF report generator for technician job card reports
    Enhanced with modern styling, watermarks, and annual reporting capabilities
    """

    COLOR_PALETTE = {
        'primary': HexColor('#0F172A'),
        'secondary': HexColor('#1E40AF'),
        'border': HexColor('#CBD5E1'),
        'light_bg': HexColor('#F8FAFC'),
        'text_primary': HexColor('#0F172A'),
        'text_secondary': HexColor('#64748B'),
        'header_bg': HexColor('#1E40AF'),
        'waiting_highlight': HexColor('#FEF3C7'),  # Light yellow
        'approved_highlight': HexColor('#D1FAE5'),  # Light green
        'declined_highlight': HexColor('#FEE2E2'),  # Light red
        'waiting_status': HexColor('#dc2626'),  # Red for charts
        'approved_status': HexColor('#16a34a'),  # Green for charts
        'declined_status': HexColor('#ef4444'),  # Red for declined
    }

    def __init__(self, workshop, periods, report_type, context):
        self.workshop = workshop
        self.periods = periods  # List of (period_name, start_date, end_date)
        self.report_type = report_type
        self.context = context
        self.styles = getSampleStyleSheet()
        self.width, self.height = A4
        self.setup_custom_styles()
        self.logo_path = self._find_logo()

    def _find_logo(self):
        """
        Locate the organisation logo by scanning every plausible directory.
        Search order (first match wins):
          1. STATIC_ROOT / images|logos|img
          2. Each entry in STATICFILES_DIRS / images|logos|img
          3. MEDIA_ROOT / images|logos
          4. BASE_DIR / static / images|logos
          5. Per-app static directories via Django finders
          6. CWD fallback
        Accepted names: logo.png/jpg, knh_logo.png, dark.png, hospital_logo.png
        """
        logo_names = [
            'logo.png', 'logo.jpg', 'logo.jpeg', 'equiper-logo.png', 'equiper-logo.jpg',
            'knh_logo.png', 'knh_logo.jpg',
            'dark.png', 'hospital_logo.png',
        ]
        candidates = []

        # 1. STATIC_ROOT
        if hasattr(settings, 'STATIC_ROOT') and settings.STATIC_ROOT:
            sr = settings.STATIC_ROOT
            for name in logo_names:
                candidates += [
                    os.path.join(sr, 'images', name),
                    os.path.join(sr, 'logos', name),
                    os.path.join(sr, 'img', name),
                    os.path.join(sr, name),
                ]

        # 2. STATICFILES_DIRS
        if hasattr(settings, 'STATICFILES_DIRS') and settings.STATICFILES_DIRS:
            for sdir in settings.STATICFILES_DIRS:
                sdir = str(sdir)
                for name in logo_names:
                    candidates += [
                        os.path.join(sdir, 'images', name),
                        os.path.join(sdir, 'logos', name),
                        os.path.join(sdir, 'img', name),
                        os.path.join(sdir, name),
                    ]

        # 3. MEDIA_ROOT
        if hasattr(settings, 'MEDIA_ROOT') and settings.MEDIA_ROOT:
            mr = settings.MEDIA_ROOT
            for name in logo_names:
                candidates += [
                    os.path.join(mr, 'images', name),
                    os.path.join(mr, 'logos', name),
                    os.path.join(mr, name),
                ]

        # 4. BASE_DIR / static
        if hasattr(settings, 'BASE_DIR') and settings.BASE_DIR:
            bd = str(settings.BASE_DIR)
            for name in logo_names:
                candidates += [
                    os.path.join(bd, 'static', 'images', name),
                    os.path.join(bd, 'static', 'logos', name),
                    os.path.join(bd, 'static', 'img', name),
                    os.path.join(bd, 'staticfiles', 'images', name),
                ]

        # 5. Per-app static dirs via Django finders
        try:
            from django.contrib.staticfiles.finders import get_finders
            for finder in get_finders():
                for name in logo_names:
                    for sub in ('images', 'logos', 'img', ''):
                        rel = os.path.join(sub, name) if sub else name
                        try:
                            result = finder.find(rel)
                            if result:
                                if isinstance(result, (list, tuple)):
                                    candidates += list(result)
                                else:
                                    candidates.append(result)
                        except Exception:
                            pass
        except Exception:
            pass

        # 6. CWD fallback
        cwd = os.getcwd()
        for name in logo_names:
            candidates += [
                os.path.join(cwd, 'static', 'images', name),
                os.path.join(cwd, name),
            ]

        for p in candidates:
            if p and os.path.isfile(p):
                logger.debug(f"[HEADER] Logo found at: {p}")
                return p

        logger.warning("[HEADER] Logo file not found in any search location.")
        return None

    def setup_custom_styles(self):
        """Setup custom styles for the PDF"""
        self.styles.add(ParagraphStyle(
            name='MainTitle',
            parent=self.styles['Title'],
            fontSize=16,
            textColor=self.COLOR_PALETTE['primary'],
            alignment=TA_CENTER,
            spaceAfter=15,
            fontName='Helvetica-Bold'
        ))
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading2'],
            fontSize=12,
            textColor=self.COLOR_PALETTE['secondary'],
            alignment=TA_LEFT,
            spaceAfter=10,
            fontName='Helvetica-Bold'
        ))
        self.styles.add(ParagraphStyle(
            name='SubSectionHeader',
            parent=self.styles['Heading3'],
            fontSize=11,
            textColor=self.COLOR_PALETTE['text_primary'],
            alignment=TA_LEFT,
            spaceAfter=8,
            fontName='Helvetica-Bold'
        ))
        self.styles.add(ParagraphStyle(
            name='Subtitle',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=self.COLOR_PALETTE['text_secondary'],
            alignment=TA_CENTER,
            spaceAfter=12
        ))
        self.styles.add(ParagraphStyle(
            name='InfoText',
            parent=self.styles['Normal'],
            fontSize=9,
            textColor=self.COLOR_PALETTE['text_secondary'],
            alignment=TA_LEFT,
            spaceAfter=6
        ))
        self.styles.add(ParagraphStyle(
            name='ExecutiveText',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=self.COLOR_PALETTE['text_primary'],
            alignment=TA_LEFT,
            spaceAfter=8,
            leftIndent=10,
            rightIndent=10
        ))
        self.styles.add(ParagraphStyle(
            name='RemarksText',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=self.COLOR_PALETTE['text_primary'],
            alignment=TA_LEFT,
            spaceAfter=12,
            spaceBefore=8,
            leftIndent=15,
            rightIndent=15,
            leading=14
        ))

    def _draw_logo_placeholder(self, canvas, x, y, size):
        """Draw a styled initials badge when the logo file is not available."""
        initials = 'KNH'
        # Rounded badge
        canvas.setStrokeColor(HexColor('#1e40af'))
        canvas.setFillColor(HexColor('#dbeafe'))
        canvas.roundRect(x, y, size, size, radius=4, fill=1, stroke=1)
        # Initials centred inside
        canvas.setFillColor(HexColor('#1e40af'))
        font_size = size * 0.28
        canvas.setFont('Helvetica-Bold', font_size)
        canvas.drawCentredString(x + size / 2, y + size / 2 - font_size * 0.35, initials)

    def create_header_footer(self, canvas, doc):
        """Create consistent header and footer for all pages. Watermark handled by _LogoWatermarkCanvas."""
        canvas.saveState()
        width, height = A4

        # HEADER - Clean design with light background
        header_height = 3*cm
        canvas.setFillColor(self.COLOR_PALETTE['light_bg'])
        canvas.rect(0, height - header_height, width, header_height, fill=1, stroke=0)

        # Top border line
        canvas.setStrokeColor(self.COLOR_PALETTE['secondary'])
        canvas.setLineWidth(2)
        canvas.line(0, height - header_height, width, height - header_height)

        # Header logo
        if self.logo_path:
            try:
                logo_size = 2.5*cm
                canvas.drawImage(
                    self.logo_path,
                    1*cm,
                    height - 2.9*cm,
                    width=logo_size,
                    height=logo_size,
                    preserveAspectRatio=True,
                    mask='auto'
                )
            except Exception as e:
                logger.warning(f"Failed to load header logo: {str(e)}")
                self._draw_logo_placeholder(canvas, 1*cm, height - 2.9*cm, 2.5*cm)
        else:
            self._draw_logo_placeholder(canvas, 1*cm, height - 2.9*cm, 2.5*cm)

        # Title
        canvas.setFillColor(self.COLOR_PALETTE['primary'])
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawCentredString(
            width / 2,
            height - 1.2*cm,
            "BIOMEDICAL ENGINEERING DEPARTMENT"
        )

        # Subtitle
        canvas.setFont("Helvetica", 10)
        canvas.setFillColor(self.COLOR_PALETTE['text_primary'])
        canvas.drawCentredString(
            width / 2,
            height - 1.7*cm,
            "Equipment Management System - Job Card Report"
        )

        # Workshop info
        if self.workshop:
            canvas.setFont("Helvetica-Oblique", 9)
            canvas.setFillColor(self.COLOR_PALETTE['text_secondary'])
            canvas.drawCentredString(
                width / 2,
                height - 2.1*cm,
                f"Workshop: {self.workshop.name}"
            )

        # Contact info
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(self.COLOR_PALETTE['text_secondary'])
        canvas.drawCentredString(
            width / 2,
            height - 2.7*cm,
            contact_line("ISO 9001:2015 Certified")
        )

        # FOOTER
        footer_height = 1.5*cm
        canvas.setFillColor(self.COLOR_PALETTE['light_bg'])
        canvas.rect(0, 0, width, footer_height, fill=1, stroke=0)

        # Top border line
        canvas.setStrokeColor(self.COLOR_PALETTE['border'])
        canvas.setLineWidth(0.5)
        canvas.line(1*cm, footer_height, width - 1*cm, footer_height)

        # Page number (right)
        canvas.setFillColor(self.COLOR_PALETTE['secondary'])
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawRightString(
            width - 1*cm,
            0.7*cm,
            f"Page {doc.page}"
        )

        # Left footer text - Generated timestamp
        canvas.setFillColor(self.COLOR_PALETTE['text_secondary'])
        canvas.setFont("Helvetica-Oblique", 8)
        timestamp = datetime.now().strftime('%B %d, %Y at %I:%M %p')
        canvas.drawString(1*cm, 0.7*cm, f"Generated: {timestamp}")

        # Generated by
        canvas.setFont("Helvetica", 7)
        canvas.drawString(1*cm, 0.4*cm, f"Generated by: {self.context.get('generated_by', 'System')}")

        # Center watermark text
        canvas.setFillColor(HexColor('#E5E7EB'))
        canvas.setFont("Helvetica-Oblique", 7)
        canvas.drawCentredString(
            width / 2,
            0.3*cm,
            "Confidential - Internal Use Only"
        )

        canvas.restoreState()

    def create_title_section(self):
        """Create the main title and subtitle section"""
        elements = []

        title_text = f"{self.report_type.upper()} TECHNICIAN JOB CARD REPORT"
        title = Paragraph(title_text, self.styles['MainTitle'])
        elements.append(title)

        subtitle_text = f"Report Period: {self.context['period_label']}"
        subtitle = Paragraph(subtitle_text, self.styles['Subtitle'])
        elements.append(subtitle)

        # Separator line
        line_table = Table([['']], colWidths=[7*inch], rowHeights=[2])
        line_table.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 2, self.COLOR_PALETTE['primary']),
        ]))
        elements.append(line_table)
        elements.append(Spacer(1, 15))

        return elements

    def create_executive_summary(self, annual_data=None):
        """Create executive summary section for annual reports"""
        elements = []
        header = Paragraph("EXECUTIVE SUMMARY", self.styles['SectionHeader'])
        elements.append(header)

        if self.report_type == 'annual' and annual_data:
            total_jobs = annual_data['total_waiting'] + annual_data['total_approved'] + annual_data.get('total_declined', 0)
            efficiency_rate = (annual_data['total_approved'] / total_jobs * 100) if total_jobs > 0 else 0

            summary_text = f"""
            <b>Performance Overview for {annual_data['year']}:</b><br/><br/>
            • Total job cards processed: <b>{total_jobs:,}</b><br/>
            • Jobs approved: <b>{annual_data['total_approved']:,}</b> ({efficiency_rate:.1f}% completion rate)<br/>
            • Jobs awaiting approval: <b>{annual_data['total_waiting']:,}</b><br/>
            • Jobs declined: <b>{annual_data.get('total_declined', 0):,}</b><br/>
            • Active technicians: <b>{annual_data['active_technicians']:,}</b><br/>
            • Peak performance month: <b>{annual_data.get('peak_month', 'N/A')}</b><br/><br/>

            <b>Key Insights:</b><br/>
            • {annual_data.get('insights', 'Performance data indicates steady workflow throughout the year.')}<br/>
            • Workshop efficiency maintained at {efficiency_rate:.1f}% approval rate.<br/>
            • {annual_data.get('recommendations', 'Continue current operational procedures.')}
            """

            summary_para = Paragraph(summary_text, self.styles['ExecutiveText'])
            elements.append(summary_para)

        elements.append(Spacer(1, 20))
        return elements

    def create_summary_section(self, users_data, total_waiting, total_approved, total_declined, period_name=""):
        """Create summary section for a period"""
        elements = []
        header_text = f"SUMMARY - {period_name}" if period_name else "EXECUTIVE SUMMARY"
        header = Paragraph(header_text, self.styles['SectionHeader'])
        elements.append(header)

        total_jobs = total_waiting + total_approved + total_declined
        summary_data = [
            ['Metric', 'Count', 'Percentage'],
            ['Jobs Waiting Approval', f'{total_waiting:,}', f'{(total_waiting/total_jobs*100) if total_jobs > 0 else 0:.1f}%'],
            ['Jobs Approved', f'{total_approved:,}', f'{(total_approved/total_jobs*100) if total_jobs > 0 else 0:.1f}%'],
            ['Jobs Declined', f'{total_declined:,}', f'{(total_declined/total_jobs*100) if total_jobs > 0 else 0:.1f}%'],
            ['Total Job Cards', f'{total_jobs:,}', '100.0%'],
            ['Active Technicians', f'{len(users_data):,}', '-']
        ]

        summary_table = Table(summary_data, colWidths=[3*inch, 2*inch, 2*inch])
        summary_table.setStyle(TableStyle([
            # Header row
            ('BACKGROUND', (0, 0), (-1, 0), HexColor('#BFDBFE')),
            ('TEXTCOLOR', (0, 0), (-1, 0), self.COLOR_PALETTE['primary']),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('ALIGN', (0, 1), (0, -1), 'LEFT'),
            ('GRID', (0, 0), (-1, -1), 0.25, self.COLOR_PALETTE['border']),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, self.COLOR_PALETTE['light_bg']]),
            ('BACKGROUND', (0, 4), (-1, 4), HexColor('#E0F2FE')),
            ('FONTNAME', (0, 4), (-1, 4), 'Helvetica-Bold'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 20))
        return elements

    def create_annual_trend_chart(self, monthly_data):
        """Create annual performance trend visualization"""
        elements = []
        header = Paragraph("ANNUAL PERFORMANCE TRENDS", self.styles['SectionHeader'])
        elements.append(header)

        drawing = Drawing(500, 250)

        # Create line chart for trends
        chart = HorizontalLineChart()
        chart.x = 50
        chart.y = 50
        chart.width = 380
        chart.height = 150

        # Prepare data
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        waiting_data = []
        approved_data = []
        declined_data = []

        for month in range(1, 13):
            month_data = monthly_data.get(month, {'waiting': 0, 'approved': 0, 'declined': 0})
            waiting_data.append(month_data['waiting'])
            approved_data.append(month_data['approved'])
            declined_data.append(month_data.get('declined', 0))

        chart.data = [waiting_data, approved_data, declined_data]
        chart.categoryAxis.categoryNames = months
        chart.categoryAxis.labels.boxAnchor = 'n'
        chart.categoryAxis.labels.angle = 30
        chart.categoryAxis.labels.fontSize = 8
        chart.valueAxis.valueMin = 0
        max_value = max(waiting_data + approved_data + declined_data) if (waiting_data + approved_data + declined_data) else 1
        chart.valueAxis.valueStep = max(1, int(max_value / 5))
        chart.valueAxis.labels.fontSize = 8

        # Line styles with smooth curves
        chart.lines[0].strokeColor = self.COLOR_PALETTE['waiting_status']
        chart.lines[1].strokeColor = self.COLOR_PALETTE['approved_status']
        chart.lines[2].strokeColor = self.COLOR_PALETTE['declined_status']
        chart.lines[0].strokeWidth = 2.5
        chart.lines[1].strokeWidth = 2.5
        chart.lines[2].strokeWidth = 2.5

        # Add symbols to lines for better visibility using makeMarker
        from reportlab.graphics.widgets.markers import makeMarker
        chart.lines[0].symbol = makeMarker('Circle')
        chart.lines[1].symbol = makeMarker('Circle')
        chart.lines[2].symbol = makeMarker('Circle')
        chart.lines[0].symbol.size = 4
        chart.lines[1].symbol.size = 4
        chart.lines[2].symbol.size = 4

        # Add legend
        legend = Legend()
        legend.x = 450
        legend.y = 120
        legend.dx = 8
        legend.dy = 8
        legend.fontName = 'Helvetica'
        legend.fontSize = 9
        legend.boxAnchor = 'w'
        legend.columnMaximum = 2
        legend.colorNamePairs = [
            (self.COLOR_PALETTE['waiting_status'], 'Waiting Approval'),
            (self.COLOR_PALETTE['approved_status'], 'Approved'),
            (self.COLOR_PALETTE['declined_status'], 'Declined')
        ]

        # Add axis labels
        drawing.add(String(240, 20, "Month", fontName="Helvetica-Bold", fontSize=10))
        drawing.add(String(15, 150, "Job Cards", fontName="Helvetica-Bold", fontSize=10, angle=90))

        drawing.add(chart)
        drawing.add(legend)
        elements.append(drawing)
        elements.append(Spacer(1, 20))
        return elements

    def create_detailed_table(self, users_data, period_name=""):
        """Create technician performance table with declined status"""
        elements = []
        header_text = f"DETAILED TECHNICIAN PERFORMANCE - {period_name}" if period_name else "DETAILED TECHNICIAN PERFORMANCE"
        header = Paragraph(header_text, self.styles['SectionHeader'])
        elements.append(header)

        if not users_data:
            no_data = Paragraph("No data available for the selected period.", self.styles['InfoText'])
            elements.append(no_data)
            return elements

        # Headers with Declined column
        data = [
            ['Technician', 'Waiting Approval', '', '', '', '', 'Approved', '', '', '', '', 'Declined', '', '', '', ''],
            ['', 'PPM', 'Calib.', 'Repair', 'Others', 'Total', 'PPM', 'Calib.', 'Repair', 'Others', 'Total', 'PPM', 'Calib.', 'Repair', 'Others', 'Total']
        ]

        # Sort and populate
        sorted_users = sorted(
            users_data.items(),
            key=lambda x: x[1]['Waiting_Approval']['total'] + x[1]['Approved']['total'] + x[1].get('Declined', {}).get('total', 0),
            reverse=True
        )

        for username, user_data in sorted_users:
            row = [
                username,
                str(user_data['Waiting_Approval']['PPM']),
                str(user_data['Waiting_Approval']['Calibration']),
                str(user_data['Waiting_Approval']['Repair']),
                str(user_data['Waiting_Approval']['Others']),
                str(user_data['Waiting_Approval']['total']),
                str(user_data['Approved']['PPM']),
                str(user_data['Approved']['Calibration']),
                str(user_data['Approved']['Repair']),
                str(user_data['Approved']['Others']),
                str(user_data['Approved']['total']),
                str(user_data.get('Declined', {}).get('PPM', 0)),
                str(user_data.get('Declined', {}).get('Calibration', 0)),
                str(user_data.get('Declined', {}).get('Repair', 0)),
                str(user_data.get('Declined', {}).get('Others', 0)),
                str(user_data.get('Declined', {}).get('total', 0)),
            ]
            data.append(row)

        # Total row
        total_row = ['TOTAL']
        waiting_totals = [0, 0, 0, 0, 0]
        approved_totals = [0, 0, 0, 0, 0]
        declined_totals = [0, 0, 0, 0, 0]

        for user_data in users_data.values():
            waiting_totals[0] += user_data['Waiting_Approval']['PPM']
            waiting_totals[1] += user_data['Waiting_Approval']['Calibration']
            waiting_totals[2] += user_data['Waiting_Approval']['Repair']
            waiting_totals[3] += user_data['Waiting_Approval']['Others']
            waiting_totals[4] += user_data['Waiting_Approval']['total']
            approved_totals[0] += user_data['Approved']['PPM']
            approved_totals[1] += user_data['Approved']['Calibration']
            approved_totals[2] += user_data['Approved']['Repair']
            approved_totals[3] += user_data['Approved']['Others']
            approved_totals[4] += user_data['Approved']['total']
            declined_totals[0] += user_data.get('Declined', {}).get('PPM', 0)
            declined_totals[1] += user_data.get('Declined', {}).get('Calibration', 0)
            declined_totals[2] += user_data.get('Declined', {}).get('Repair', 0)
            declined_totals[3] += user_data.get('Declined', {}).get('Others', 0)
            declined_totals[4] += user_data.get('Declined', {}).get('total', 0)

        total_row.extend([str(x) for x in waiting_totals])
        total_row.extend([str(x) for x in approved_totals])
        total_row.extend([str(x) for x in declined_totals])
        data.append(total_row)

        # Optimized column widths for A4 with declined column (slightly narrower)
        col_widths = [1.2*inch, 0.4*inch, 0.4*inch, 0.4*inch, 0.4*inch, 0.45*inch,
                      0.4*inch, 0.4*inch, 0.4*inch, 0.4*inch, 0.45*inch,
                      0.4*inch, 0.4*inch, 0.4*inch, 0.4*inch, 0.45*inch]

        table = Table(data, colWidths=col_widths, repeatRows=2)
        table.setStyle(TableStyle([
            # Spans
            ('SPAN', (1, 0), (5, 0)),
            ('SPAN', (6, 0), (10, 0)),
            ('SPAN', (11, 0), (15, 0)),

            # Header styling
            ('BACKGROUND', (1, 0), (5, 0), HexColor('#BFDBFE')),
            ('BACKGROUND', (6, 0), (10, 0), HexColor('#BFDBFE')),
            ('BACKGROUND', (11, 0), (15, 0), HexColor('#FED7AA')),  # Orange for declined
            ('TEXTCOLOR', (1, 0), (15, 0), self.COLOR_PALETTE['primary']),
            ('ALIGN', (1, 0), (15, 0), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 7),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),

            # Technician column
            ('BACKGROUND', (0, 0), (0, 1), self.COLOR_PALETTE['primary']),
            ('TEXTCOLOR', (0, 0), (0, 1), white),

            # Sub-headers
            ('BACKGROUND', (0, 1), (-1, 1), self.COLOR_PALETTE['light_bg']),
            ('FONTSIZE', (0, 1), (-1, 1), 6),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),

            # Data rows
            ('FONTNAME', (0, 2), (0, -2), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 2), (-1, -2), 6.5),
            ('ALIGN', (0, 2), (0, -2), 'LEFT'),
            ('ROWBACKGROUNDS', (0, 2), (-1, -2), [white, self.COLOR_PALETTE['light_bg']]),

            # Highlight total columns
            ('BACKGROUND', (5, 2), (5, -2), self.COLOR_PALETTE['waiting_highlight']),
            ('BACKGROUND', (10, 2), (10, -2), self.COLOR_PALETTE['approved_highlight']),
            ('BACKGROUND', (15, 2), (15, -2), self.COLOR_PALETTE['declined_highlight']),
            ('FONTNAME', (5, 2), (5, -2), 'Helvetica-Bold'),
            ('FONTNAME', (10, 2), (10, -2), 'Helvetica-Bold'),
            ('FONTNAME', (15, 2), (15, -2), 'Helvetica-Bold'),

            # Total row
            ('BACKGROUND', (0, -1), (-1, -1), self.COLOR_PALETTE['primary']),
            ('TEXTCOLOR', (0, -1), (-1, -1), white),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, -1), (-1, -1), 8),

            # Borders
            ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
            ('BOX', (0, 0), (-1, -1), 2, self.COLOR_PALETTE['primary']),
            ('LINEAFTER', (0, 0), (0, -1), 2, self.COLOR_PALETTE['border']),
            ('LINEAFTER', (5, 0), (5, -1), 2, self.COLOR_PALETTE['border']),
            ('LINEAFTER', (10, 0), (10, -1), 2, self.COLOR_PALETTE['border']),

            # Padding
            ('LEFTPADDING', (0, 0), (-1, -1), 2),
            ('RIGHTPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING', (0, 0), (-1, -1), 3),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ]))

        elements.append(Spacer(1, 8))
        elements.append(table)
        elements.append(Spacer(1, 15))
        return elements

    def create_chart_section(self, users_data, period_name=""):
        """Create visual bar chart section - always on new page"""
        elements = []

        # Add page break before chart
        elements.append(PageBreak())

        header_text = f"JOB CARD STATUS VISUALIZATION - {period_name}" if period_name else "JOB CARD STATUS VISUALIZATION"
        header = Paragraph(header_text, self.styles['SectionHeader'])
        elements.append(header)
        elements.append(Spacer(1, 15))

        drawing = Drawing(500, 250)

        total_waiting = sum(user_data['Waiting_Approval']['total'] for user_data in users_data.values())
        total_approved = sum(user_data['Approved']['total'] for user_data in users_data.values())
        total_declined = sum(user_data.get('Declined', {}).get('total', 0) for user_data in users_data.values())

        # Create stylish bar chart with better positioning
        chart = VerticalBarChart()
        chart.x = 180
        chart.y = 70
        chart.width = 180
        chart.height = 130
        chart.data = [[total_waiting], [total_approved], [total_declined]]
        chart.categoryAxis.categoryNames = ['Waiting', 'Approved', 'Declined']
        chart.categoryAxis.labels.boxAnchor = 'n'
        chart.categoryAxis.labels.dy = -8
        chart.categoryAxis.labels.fontSize = 9
        chart.categoryAxis.labels.fontName = 'Helvetica-Bold'

        # Enhanced bar styling
        chart.bars[0].fillColor = self.COLOR_PALETTE['waiting_status']
        chart.bars[1].fillColor = self.COLOR_PALETTE['approved_status']
        chart.bars[2].fillColor = self.COLOR_PALETTE['declined_status']
        chart.bars[0].strokeColor = white
        chart.bars[1].strokeColor = white
        chart.bars[2].strokeColor = white
        chart.bars.strokeWidth = 2
        chart.barWidth = 30
        chart.groupSpacing = 20
        chart.valueAxis.valueMin = 0
        chart.valueAxis.strokeWidth = 1.5
        chart.categoryAxis.strokeWidth = 1.5
        chart.valueAxis.labels.fontSize = 8

        # Add grid lines
        chart.valueAxis.visibleGrid = 1
        chart.valueAxis.gridStrokeColor = HexColor('#E5E7EB')
        chart.valueAxis.gridStrokeWidth = 0.5

        # Add value labels on top of bars
        max_value = max(total_waiting, total_approved, total_declined, 1)
        values = [total_waiting, total_approved, total_declined]

        for i, value in enumerate(values):
            if value > 0:
                x_pos = chart.x + i * (chart.barWidth + chart.groupSpacing) + chart.barWidth / 2
                y_pos = chart.y + (value / max_value) * chart.height + 8
                label = String(x_pos, y_pos, str(value),
                             fontName='Helvetica-Bold', fontSize=10, textAnchor='middle')
                label.fillColor = self.COLOR_PALETTE['primary']
                drawing.add(label)

        # Add chart
        drawing.add(chart)

        # Add statistics box - centered below chart
        total_jobs = total_waiting + total_approved + total_declined
        if total_jobs > 0:
            waiting_pct = (total_waiting / total_jobs) * 100
            approved_pct = (total_approved / total_jobs) * 100
            declined_pct = (total_declined / total_jobs) * 100

            stats_text = f"Total: {total_jobs:,} | Waiting: {waiting_pct:.1f}% | Approved: {approved_pct:.1f}% | Declined: {declined_pct:.1f}%"
            stats_label = String(250, 30, stats_text,
                              fontName='Helvetica', fontSize=9, textAnchor='middle')
            stats_label.fillColor = self.COLOR_PALETTE['text_secondary']
            drawing.add(stats_label)
        else:
            # Show message when no data
            no_data_text = String(250, 30,
                                "No job card data available for this period",
                                fontName='Helvetica-Oblique', fontSize=9, textAnchor='middle')
            no_data_text.fillColor = self.COLOR_PALETTE['text_secondary']
            drawing.add(no_data_text)

        elements.append(drawing)
        elements.append(Spacer(1, 20))
        return elements

    def create_remarks_section(self):
        """Create well-spaced remarks section with improved formatting"""
        elements = []

        # Add extra spacing before section
        elements.append(Spacer(1, 30))

        # Header with decorative line
        header = Paragraph("REMARKS &amp; OBSERVATIONS", self.styles['SectionHeader'])
        elements.append(header)

        # Decorative separator line
        line_table = Table([['']], colWidths=[7*inch], rowHeights=[1])
        line_table.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 1.5, self.COLOR_PALETTE['secondary']),
        ]))
        elements.append(line_table)
        elements.append(Spacer(1, 15))

        remarks = self.context.get('remarks', '')
        if remarks:
            # Create a styled box for remarks
            remarks_box_data = [[Paragraph(remarks, self.styles['RemarksText'])]]
            remarks_box = Table(remarks_box_data, colWidths=[6.5*inch])
            remarks_box.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), self.COLOR_PALETTE['light_bg']),
                ('BOX', (0, 0), (-1, -1), 1, self.COLOR_PALETTE['border']),
                ('LEFTPADDING', (0, 0), (-1, -1), 20),
                ('RIGHTPADDING', (0, 0), (-1, -1), 20),
                ('TOPPADDING', (0, 0), (-1, -1), 15),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 15),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            elements.append(remarks_box)
        else:
            # Placeholder text in styled box
            no_remarks_text = "No specific remarks or observations recorded for this reporting period."
            no_remarks_para = Paragraph(f"<i>{no_remarks_text}</i>", self.styles['RemarksText'])

            no_remarks_box_data = [[no_remarks_para]]
            no_remarks_box = Table(no_remarks_box_data, colWidths=[6.5*inch])
            no_remarks_box.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), HexColor('#FFFBEB')),
                ('BOX', (0, 0), (-1, -1), 1, self.COLOR_PALETTE['border']),
                ('LEFTPADDING', (0, 0), (-1, -1), 20),
                ('RIGHTPADDING', (0, 0), (-1, -1), 20),
                ('TOPPADDING', (0, 0), (-1, -1), 15),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 15),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ]))
            elements.append(no_remarks_box)

        # Add spacing after remarks
        elements.append(Spacer(1, 30))

        # Submitted by section - check if report has been submitted
        report = self.context.get('report')

        if report is not None and hasattr(report, 'submitted_by') and report.submitted_by is not None:
            # Report exists and has been submitted
            submitted_by = report.submitted_by.get_full_name() or report.submitted_by.username
            submit_date = report.submitted_at.strftime('%B %d, %Y at %I:%M %p') if hasattr(report, 'submitted_at') and report.submitted_at else 'N/A'

            submitted_data = [
                ['Submitted By:', submitted_by],
                ['Submission Date:', submit_date],
                ['Position:', 'Biomedical Technician'],
            ]

            submitted_table = Table(submitted_data, colWidths=[1.8*inch, 4.7*inch])
            submitted_table.setStyle(TableStyle([
                ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('FONTNAME', (1, 0), (1, -1), 'Helvetica'),
                ('TEXTCOLOR', (0, 0), (-1, -1), self.COLOR_PALETTE['text_primary']),
                ('ALIGN', (0, 0), (0, -1), 'LEFT'),
                ('ALIGN', (1, 0), (1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
                ('LINEBELOW', (0, -1), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
            ]))
            elements.append(submitted_table)
        else:
            # Report not submitted - show not submitted message
            not_submitted_text = Paragraph(
                "<i>This report has not been submitted yet.</i>",
                self.styles['InfoText']
            )
            not_submitted_box = Table([[not_submitted_text]], colWidths=[6.5*inch])
            not_submitted_box.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), HexColor('#FEF3C7')),
                ('BOX', (0, 0), (-1, -1), 1, HexColor('#FCD34D')),
                ('LEFTPADDING', (0, 0), (-1, -1), 15),
                ('RIGHTPADDING', (0, 0), (-1, -1), 15),
                ('TOPPADDING', (0, 0), (-1, -1), 10),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ]))
            elements.append(not_submitted_box)

        elements.append(Spacer(1, 20))

        return elements

    def generate_pdf(self):
        """Generate the complete PDF report"""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=35,
            leftMargin=35,
            topMargin=3*cm + 10,
            bottomMargin=1.5*cm + 10
        )
        elements = []

        # Title section
        elements.extend(self.create_title_section())

        if self.report_type == 'annual':
            # Annual report handling
            annual_data = self.context.get('annual_data', {})
            elements.extend(self.create_executive_summary(annual_data))

            monthly_data = annual_data.get('monthly_breakdown', {})
            if monthly_data:
                elements.extend(self.create_annual_trend_chart(monthly_data))

            # Process each period
            for period_name, start_date, end_date in self.periods:
                users_data = get_report_data(self.workshop, start_date, end_date)
                total_waiting = sum(user_data['Waiting_Approval']['total'] for user_data in users_data.values())
                total_approved = sum(user_data['Approved']['total'] for user_data in users_data.values())
                total_declined = sum(user_data.get('Declined', {}).get('total', 0) for user_data in users_data.values())

                # Add page break between months (except first)
                if period_name != self.periods[0][0]:
                    elements.append(PageBreak())

                elements.extend(self.create_summary_section(users_data, total_waiting, total_approved, total_declined, period_name))
                elements.extend(self.create_detailed_table(users_data, period_name))
                elements.extend(self.create_chart_section(users_data, period_name))

        else:
            # Standard processing for other report types
            for period_name, start_date, end_date in self.periods:
                users_data = get_report_data(self.workshop, start_date, end_date)
                total_waiting = sum(user_data['Waiting_Approval']['total'] for user_data in users_data.values())
                total_approved = sum(user_data['Approved']['total'] for user_data in users_data.values())
                total_declined = sum(user_data.get('Declined', {}).get('total', 0) for user_data in users_data.values())

                self.context['total_waiting_approval'] = total_waiting
                self.context['total_approved'] = total_approved
                self.context['total_declined'] = total_declined

                elements.extend(self.create_summary_section(users_data, total_waiting, total_approved, total_declined))
                elements.extend(self.create_detailed_table(users_data))
                elements.extend(self.create_chart_section(users_data))

        # Add remarks section at the end
        elements.extend(self.create_remarks_section())

        # Build with header/footer callback
        from functools import partial
        wm_canvas = partial(_LogoWatermarkCanvas, logo_path=self.logo_path)
        doc.build(elements, onFirstPage=self.create_header_footer, onLaterPages=self.create_header_footer,
                  canvasmaker=wm_canvas)
        return buffer
