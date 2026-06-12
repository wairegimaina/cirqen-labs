"""
Complete Standalone Manufacturer Performance PDF Generator
Includes base template, generator logic, and all necessary components
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph,
    Table, TableStyle, Spacer, PageBreak
)
from django.http import HttpResponse
from django.utils import timezone
from django.conf import settings
import os
from reportlab.pdfgen import canvas as rl_canvas
import logging
from functools import partial
from io import BytesIO

logger = logging.getLogger(__name__)

class _LogoWatermarkCanvas(rl_canvas.Canvas):
    """
    Custom canvas that draws the logo watermark OVER all page content by
    hooking into showPage() — which fires AFTER all flowables are placed.
    This ensures the watermark is always visible above table backgrounds.
    Matches the CalSoft pdf_generators watermark implementation exactly.
    """
    def __init__(self, filename, logo_path=None, wm_alpha=0.20, wm_scale=0.52, **kwargs):
        self._wm_logo_path = logo_path
        self._wm_alpha = wm_alpha
        self._wm_scale = wm_scale
        rl_canvas.Canvas.__init__(self, filename, **kwargs)

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
        rl_canvas.Canvas.showPage(self)




# =====================================================================
# BASE TEMPLATE CLASS
# =====================================================================

class ManufacturerPDFTemplate(BaseDocTemplate):
    """
    Custom document template with configurable headers, footers, and watermark
    """

    # Color palette
    COLOR_PALETTE = {
        'primary': colors.HexColor('#0F172A'),
        'secondary': colors.HexColor('#1E40AF'),
        'border': colors.HexColor('#CBD5E1'),
        'light_bg': colors.HexColor('#F8FAFC'),
        'text_primary': colors.HexColor('#0F172A'),
        'text_secondary': colors.HexColor('#64748B'),
        'header_bg': colors.HexColor('#1E40AF'),
        'success': colors.HexColor('#10B981'),
        'warning': colors.HexColor('#F59E0B'),
        'danger': colors.HexColor('#EF4444'),
    }

    def __init__(
        self,
        filename,
        pagesize=A4,
        title="MANUFACTURER PERFORMANCE REPORT",
        subtitle=None,
        department_name="Biomedical Engineering Department",
        contact_info=None,
        logo_path=None,
        **kwargs
    ):
        """
        Initialize PDF template

        Args:
            filename: Output filename or buffer
            pagesize: Page size (default A4)
            title: Main title for header
            subtitle: Optional subtitle
            department_name: Department name for header
            contact_info: Contact information string
            logo_path: Custom logo path (optional)
        """
        BaseDocTemplate.__init__(self, filename, pagesize=pagesize, **kwargs)

        # Store configuration
        self.report_title = title
        self.report_subtitle = subtitle
        self.department_name = department_name
        self.contact_info = contact_info or "Email: biomedical@hospital.com | Phone: +254-XXX-XXXX"
        self.page_width, self.page_height = pagesize

        # Get logo path
        self.logo_path = self._find_logo(logo_path)

        # Setup page templates
        self._setup_page_templates()

    def _find_logo(self, custom_path=None):
        """
        Locate the organisation logo by scanning every plausible directory.
        Search order (first match wins):
          1. custom_path argument (if given)
          2. STATIC_ROOT / images|logos|img
          3. Each entry in STATICFILES_DIRS / images|logos|img
          4. MEDIA_ROOT / images|logos
          5. BASE_DIR / static / images|logos
          6. Per-app static directories via Django finders
          7. CWD fallback
        Accepted names: logo.png/jpg, knh_logo.png, dark.png, hospital_logo.png
        """
        logo_names = [
            'logo.png', 'logo.jpg', 'logo.jpeg', 'equiper-logo.png', 'equiper-logo.jpg',
            'knh_logo.png', 'knh_logo.jpg',
            'dark.png', 'hospital_logo.png',
        ]
        candidates = []

        # 1. Explicit custom path
        if custom_path and os.path.isfile(custom_path):
            return custom_path

        # 2. STATIC_ROOT
        if hasattr(settings, 'STATIC_ROOT') and settings.STATIC_ROOT:
            sr = settings.STATIC_ROOT
            for name in logo_names:
                candidates += [
                    os.path.join(sr, 'images', name),
                    os.path.join(sr, 'logos', name),
                    os.path.join(sr, 'img', name),
                    os.path.join(sr, name),
                ]

        # 3. STATICFILES_DIRS
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

        # 4. MEDIA_ROOT
        if hasattr(settings, 'MEDIA_ROOT') and settings.MEDIA_ROOT:
            mr = settings.MEDIA_ROOT
            for name in logo_names:
                candidates += [
                    os.path.join(mr, 'images', name),
                    os.path.join(mr, 'logos', name),
                    os.path.join(mr, name),
                ]

        # 5. BASE_DIR / static
        if hasattr(settings, 'BASE_DIR') and settings.BASE_DIR:
            bd = str(settings.BASE_DIR)
            for name in logo_names:
                candidates += [
                    os.path.join(bd, 'static', 'images', name),
                    os.path.join(bd, 'static', 'logos', name),
                    os.path.join(bd, 'static', 'img', name),
                    os.path.join(bd, 'staticfiles', 'images', name),
                ]

        # 6. Per-app static dirs via Django finders
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

        # 7. CWD fallback
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

    def _setup_page_templates(self):
        """Setup page templates with frames"""
        # Define content frame
        frame = Frame(
            x1=2*cm,
            y1=2*cm,
            width=self.page_width - 4*cm,
            height=self.page_height - 5*cm,
            leftPadding=0,
            bottomPadding=0,
            rightPadding=0,
            topPadding=0
        )

        # Create page template with header/footer callback
        template = PageTemplate(
            id='normal',
            frames=[frame],
            onPage=self._on_page
        )

        self.addPageTemplates([template])


    def build(self, flowables, **kwargs):
        """Build PDF using _LogoWatermarkCanvas so the logo watermark
        renders OVER content (fires in showPage, after layout).
        Matches the CalSoft pdf_generators watermark pattern exactly.
        """
        wm_canvas = partial(_LogoWatermarkCanvas, logo_path=self.logo_path)
        kwargs.setdefault('canvasmaker', wm_canvas)
        super().build(flowables, **kwargs)

    def _on_page(self, canvas, doc):
        """Callback for each page - draws header, footer, and watermark"""
        canvas.saveState()

        # Draw header
        self._draw_header(canvas, doc)

        # Draw footer
        self._draw_footer(canvas, doc)

        canvas.restoreState()

    def _draw_watermark(self, canvas, doc):
        """Draw watermark logo in center of page"""
        if not self.logo_path:
            return

        try:
            canvas.saveState()

            # Set transparency
            canvas.setFillAlpha(0.08)
            canvas.setStrokeAlpha(0.08)

            # Calculate center position
            watermark_size = min(self.page_width, self.page_height) * 0.5
            x_center = (self.page_width - watermark_size) / 2
            y_center = (self.page_height - watermark_size) / 2

            # Draw watermark
            canvas.drawImage(
                self.logo_path,
                x_center,
                y_center,
                width=watermark_size,
                height=watermark_size,
                preserveAspectRatio=True,
                mask='auto'
            )

            canvas.restoreState()
        except Exception as e:
            logger.warning(f"Failed to draw watermark: {str(e)}")

    def _draw_header(self, canvas, doc):
        """Draw header with logo and title"""
        # Header background
        header_height = 3*cm
        canvas.setFillColor(self.COLOR_PALETTE['light_bg'])
        canvas.rect(0, self.page_height - header_height, self.page_width, header_height, fill=1, stroke=0)

        # Top border line
        canvas.setStrokeColor(self.COLOR_PALETTE['secondary'])
        canvas.setLineWidth(2)
        canvas.line(0, self.page_height - header_height, self.page_width, self.page_height - header_height)

        # Logo
        if self.logo_path:
            try:
                logo_size = 2.5*cm
                canvas.drawImage(
                    self.logo_path,
                    1*cm,
                    self.page_height - 2.9*cm,
                    width=logo_size,
                    height=logo_size,
                    preserveAspectRatio=True,
                    mask='auto'
                )
            except Exception as e:
                logger.warning(f"Failed to load header logo: {str(e)}")
                self._draw_logo_placeholder(canvas, 1*cm, self.page_height - 2.9*cm, 2.5*cm)
        else:
            self._draw_logo_placeholder(canvas, 1*cm, self.page_height - 2.9*cm, 2.5*cm)

        # Title
        canvas.setFillColor(self.COLOR_PALETTE['primary'])
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawCentredString(
            self.page_width / 2,
            self.page_height - 1.2*cm,
            self.report_title
        )

        # Department name
        canvas.setFont("Helvetica", 10)
        canvas.setFillColor(self.COLOR_PALETTE['text_primary'])
        canvas.drawCentredString(
            self.page_width / 2,
            self.page_height - 1.7*cm,
            self.department_name
        )

        # Subtitle (if provided)
        if self.report_subtitle:
            canvas.setFont("Helvetica-Oblique", 9)
            canvas.setFillColor(self.COLOR_PALETTE['text_secondary'])
            canvas.drawCentredString(
                self.page_width / 2,
                self.page_height - 2.1*cm,
                self.report_subtitle
            )

        # Contact info
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(self.COLOR_PALETTE['text_secondary'])
        canvas.drawCentredString(
            self.page_width / 2,
            self.page_height - 2.7*cm,
            self.contact_info
        )

    def _draw_footer(self, canvas, doc):
        """Draw footer with page number and info"""
        footer_height = 1.5*cm

        # Footer background
        canvas.setFillColor(self.COLOR_PALETTE['light_bg'])
        canvas.rect(0, 0, self.page_width, footer_height, fill=1, stroke=0)

        # Top border line
        canvas.setStrokeColor(self.COLOR_PALETTE['border'])
        canvas.setLineWidth(0.5)
        canvas.line(1*cm, footer_height, self.page_width - 1*cm, footer_height)

        # Page number (right)
        canvas.setFillColor(self.COLOR_PALETTE['secondary'])
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawRightString(
            self.page_width - 1*cm,
            0.7*cm,
            f"Page {doc.page}"
        )

        # Left footer text
        canvas.setFillColor(self.COLOR_PALETTE['text_secondary'])
        canvas.setFont("Helvetica-Oblique", 8)
        canvas.drawString(1*cm, 0.7*cm, self.report_title)

        # Center watermark text
        canvas.setFillColor(colors.HexColor('#E5E7EB'))
        canvas.setFont("Helvetica-Oblique", 7)
        canvas.drawCentredString(
            self.page_width / 2,
            0.3*cm,
            "Confidential - Internal Use Only"
        )

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


# =====================================================================
# MANUFACTURER PERFORMANCE GENERATOR
# =====================================================================

class ManufacturerPerformancePDFGenerator:
    """
    Professional PDF generator for manufacturer performance reports
    """

    def __init__(
        self,
        manufacturer_performance_data,
        selected_workshop=None,
        generated_by=None
    ):
        """
        Initialize manufacturer performance report generator

        Args:
            manufacturer_performance_data: Dict of manufacturer performance metrics
            selected_workshop: Workshop object (optional)
            generated_by: User object
        """
        self.performance_data = manufacturer_performance_data
        self.selected_workshop = selected_workshop
        self.generated_by = generated_by

        # Setup styles
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

        # Color palette
        self.colors = ManufacturerPDFTemplate.COLOR_PALETTE

    def _setup_custom_styles(self):
        """Setup custom paragraph styles"""
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading2'],
            fontSize=12,
            spaceAfter=12,
            spaceBefore=20,
            textColor=colors.HexColor('#DC2626'),
            fontName='Helvetica-Bold'
        ))

        self.styles.add(ParagraphStyle(
            name='ManufacturerTitle',
            parent=self.styles['Heading3'],
            fontSize=11,
            spaceAfter=10,
            spaceBefore=15,
            textColor=colors.HexColor('#059669'),
            fontName='Helvetica-Bold',
            borderWidth=1,
            borderColor=colors.HexColor('#059669'),
            borderPadding=5,
            backColor=colors.HexColor('#D1FAE5')
        ))

    def _get_report_title(self):
        """Generate report title"""
        if self.selected_workshop:
            return f"MANUFACTURER PERFORMANCE REPORT - {self.selected_workshop.name}"
        return "MANUFACTURER PERFORMANCE REPORT"

    def _get_report_subtitle(self):
        """Generate report subtitle"""
        return "Performance Analysis & Reliability Metrics"

    def _create_executive_summary(self):
        """Create executive summary section"""
        elements = []

        elements.append(Paragraph("Executive Summary", self.styles['SectionHeader']))
        elements.append(Spacer(1, 0.3*cm))

        total_manufacturers = len(self.performance_data)
        total_equipment = sum(data['equipment_count'] for data in self.performance_data.values())
        avg_uptime = (sum(data['avg_uptime'] for data in self.performance_data.values())
                     / total_manufacturers if total_manufacturers > 0 else 0)

        summary_text = f"""
        This report analyzes the performance of <b>{total_manufacturers}</b> manufacturers
        across <b>{total_equipment}</b> pieces of equipment. The average uptime across all
        manufacturers is <b>{avg_uptime:.1f}%</b>. The analysis includes repair frequency,
        downtime, and overall reliability metrics to inform procurement decisions and
        maintenance strategies.
        """

        elements.append(Paragraph(summary_text, self.styles['Normal']))
        elements.append(Spacer(1, 0.5*cm))

        return elements

    def _create_performance_overview(self):
        """Create performance overview table"""
        elements = []

        elements.append(Paragraph("Manufacturer Performance Overview", self.styles['SectionHeader']))
        elements.append(Spacer(1, 0.3*cm))

        # Table headers
        table_data = [[
            'Manufacturer', 'Equipment\nCount', 'Uptime\n%',
            'Total\nRepairs', 'Repair\nFreq/Eq', 'Total\nDowntime (Hrs)'
        ]]

        # Sort manufacturers by uptime (descending)
        sorted_manufacturers = sorted(
            self.performance_data.items(),
            key=lambda x: x[1]['avg_uptime'],
            reverse=True
        )

        # Add manufacturer rows
        for manufacturer_name, data in sorted_manufacturers:
            table_data.append([
                manufacturer_name,
                str(data['equipment_count']),
                f"{data['avg_uptime']:.1f}%",
                str(data['total_repairs']),
                f"{data['repair_frequency']:.1f}",
                f"{data['total_downtime']:.1f}"
            ])

        # Create table
        table = Table(table_data, colWidths=[4*cm, 2.5*cm, 2.5*cm, 2.5*cm, 2.5*cm, 3*cm])

        # Style table
        table_style = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E5E7EB')),
            ('TEXTCOLOR', (0, 0), (-1, 0), self.colors['text_primary']),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),  # Left align manufacturer names
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 15),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('GRID', (0, 0), (-1, -1), 0.5, self.colors['border']),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]

        # Add alternating row colors
        for row in range(1, len(table_data)):
            bg_color = colors.white if row % 2 == 0 else colors.HexColor('#F9FAFB')
            table_style.append(('BACKGROUND', (0, row), (-1, row), bg_color))

        table.setStyle(TableStyle(table_style))
        elements.append(table)
        elements.append(PageBreak())

        return elements

    def _get_performance_rating(self, value, metric_type):
        """Generate performance rating based on metric type and value"""
        if metric_type == 'uptime':
            if value >= 95:
                return "Excellent", colors.HexColor('#059669')
            elif value >= 85:
                return "Good", colors.HexColor('#10B981')
            elif value >= 75:
                return "Fair", colors.HexColor('#F59E0B')
            else:
                return "Poor", colors.HexColor('#DC2626')

        elif metric_type == 'frequency':
            if value <= 1:
                return "Excellent", colors.HexColor('#059669')
            elif value <= 2:
                return "Good", colors.HexColor('#10B981')
            elif value <= 4:
                return "Fair", colors.HexColor('#F59E0B')
            else:
                return "Poor", colors.HexColor('#DC2626')

        return "N/A", colors.grey

    def _create_detailed_analysis(self):
        """Create detailed analysis for each manufacturer"""
        elements = []

        elements.append(Paragraph("Detailed Manufacturer Analysis", self.styles['SectionHeader']))
        elements.append(Spacer(1, 0.3*cm))

        # Sort manufacturers by uptime (descending)
        sorted_manufacturers = sorted(
            self.performance_data.items(),
            key=lambda x: x[1]['avg_uptime'],
            reverse=True
        )

        # Show top 10 manufacturers
        for i, (manufacturer_name, data) in enumerate(sorted_manufacturers[:10]):
            if i > 0 and i % 3 == 0:
                elements.append(PageBreak())

            # Manufacturer header
            elements.append(Paragraph(
                f"{i+1}. {manufacturer_name}",
                self.styles['ManufacturerTitle']
            ))

            # Get ratings
            uptime_rating, uptime_color = self._get_performance_rating(
                data['avg_uptime'], 'uptime'
            )
            freq_rating, freq_color = self._get_performance_rating(
                data['repair_frequency'], 'frequency'
            )

            # Performance metrics
            metrics_data = [
                ['Metric', 'Value', 'Performance Rating'],
                ['Equipment Count', str(data['equipment_count']), 'N/A'],
                ['Average Uptime', f"{data['avg_uptime']:.1f}%", uptime_rating],
                ['Total Repairs', str(data['total_repairs']), 'N/A'],
                ['Repair Frequency', f"{data['repair_frequency']:.1f} repairs/equipment", freq_rating],
                ['Total Downtime', f"{data['total_downtime']:.1f} hours", 'N/A'],
                ['Average Repair Cost', f"KES {data.get('avg_repair_cost', 0):,.2f}", 'N/A'],
            ]

            metrics_table = Table(metrics_data, colWidths=[5*cm, 5*cm, 5*cm])

            # Style with colored ratings
            table_style = [
                ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E5E7EB')),
                ('TEXTCOLOR', (0, 0), (-1, 0), self.colors['text_primary']),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, 0), 10),
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 0.5, self.colors['border']),
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ]

            # Add alternating row colors
            for row in range(1, len(metrics_data)):
                bg_color = colors.white if row % 2 == 0 else colors.HexColor('#F9FAFB')
                table_style.append(('BACKGROUND', (0, row), (-1, row), bg_color))

            # Color the rating cells
            table_style.append(('TEXTCOLOR', (2, 2), (2, 2), uptime_color))
            table_style.append(('FONTNAME', (2, 2), (2, 2), 'Helvetica-Bold'))
            table_style.append(('TEXTCOLOR', (2, 4), (2, 4), freq_color))
            table_style.append(('FONTNAME', (2, 4), (2, 4), 'Helvetica-Bold'))

            metrics_table.setStyle(TableStyle(table_style))
            elements.append(metrics_table)
            elements.append(Spacer(1, 0.5*cm))

        return elements

    def _create_recommendations(self):
        """Create recommendations section"""
        elements = []

        elements.append(PageBreak())
        elements.append(Paragraph("Recommendations", self.styles['SectionHeader']))
        elements.append(Spacer(1, 0.3*cm))

        recommendations = []

        if self.performance_data:
            # Find best and worst performers
            best_uptime = max(self.performance_data.items(), key=lambda x: x[1]['avg_uptime'])
            worst_uptime = min(self.performance_data.items(), key=lambda x: x[1]['avg_uptime'])

            recommendations.append(
                f"Consider prioritizing equipment from <b>{best_uptime[0]}</b> "
                f"(uptime: {best_uptime[1]['avg_uptime']:.1f}%) for future purchases."
            )

            if worst_uptime[1]['avg_uptime'] < 80:
                recommendations.append(
                    f"Review maintenance protocols for <b>{worst_uptime[0]}</b> equipment "
                    f"(uptime: {worst_uptime[1]['avg_uptime']:.1f}%)."
                )

            # High repair frequency
            high_repair_freq = [
                name for name, data in self.performance_data.items()
                if data['repair_frequency'] > 3
            ]
            if high_repair_freq:
                recommendations.append(
                    f"Investigate frequent repairs for equipment from: "
                    f"<b>{', '.join(high_repair_freq[:3])}</b>."
                )

            # Cost analysis
            high_cost_manufacturers = sorted(
                [(name, data.get('avg_repair_cost', 0)) for name, data in self.performance_data.items()],
                key=lambda x: x[1],
                reverse=True
            )[:3]

            if high_cost_manufacturers and high_cost_manufacturers[0][1] > 0:
                recommendations.append(
                    f"Monitor repair costs for <b>{high_cost_manufacturers[0][0]}</b> "
                    f"(average repair cost: KES {high_cost_manufacturers[0][1]:,.2f})."
                )

        recommendations.extend([
            "Regular performance reviews should be conducted quarterly to track improvements.",
            "Establish preferred vendor programs with top-performing manufacturers.",
            "Consider warranty and service agreements with manufacturers showing high reliability.",
            "Maintain detailed maintenance logs to improve future performance analysis.",
            "Benchmark manufacturer performance against industry standards."
        ])

        for recommendation in recommendations:
            elements.append(Paragraph(f"• {recommendation}", self.styles['Normal']))
            elements.append(Spacer(1, 0.2*cm))

        return elements

    def _create_footer_section(self):
        """Create report footer"""
        elements = []

        elements.append(Spacer(1, 1*cm))

        footer_text = f"""
        <b>Report Generated:</b> {timezone.localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p')}<br/>
        <b>Generated By:</b> {self.generated_by.get_full_name() if self.generated_by else 'System'}<br/>
        <b>Total Manufacturers Analyzed:</b> {len(self.performance_data)}<br/>
        <br/>
        <i>This manufacturer performance report provides insights into equipment reliability,
        maintenance costs, and operational efficiency. Use this data to inform procurement decisions
        and vendor management strategies. For questions, contact the Biomedical Engineering Department.</i>
        """

        elements.append(Paragraph(footer_text, self.styles['Normal']))

        return elements

    def generate(self):
        """Generate the complete PDF report"""
        buffer = BytesIO()

        # Create document with custom template
        doc = ManufacturerPDFTemplate(
            buffer,
            pagesize=A4,
            title=self._get_report_title(),
            subtitle=self._get_report_subtitle(),
            department_name="Biomedical Engineering Department",
            contact_info="Email: biomedical@hospital.com | Phone: +254-XXX-XXXX",
        )

        # Build story
        story = []

        # Add sections
        story.extend(self._create_executive_summary())
        story.extend(self._create_performance_overview())
        story.extend(self._create_detailed_analysis())
        story.extend(self._create_recommendations())
        story.extend(self._create_footer_section())

        # Build PDF
        doc.build(story)
        buffer.seek(0)

        return buffer


# =====================================================================
# PUBLIC API FUNCTIONS
# =====================================================================

def generate_manufacturer_performance_pdf(
    manufacturer_performance_data,
    selected_workshop=None,
    generated_by=None
):
    """
    Generate manufacturer performance PDF

    Args:
        manufacturer_performance_data: Dict of manufacturer performance metrics
        selected_workshop: Workshop object (optional)
        generated_by: User object

    Returns:
        BytesIO buffer containing the PDF
    """
    generator = ManufacturerPerformancePDFGenerator(
        manufacturer_performance_data=manufacturer_performance_data,
        selected_workshop=selected_workshop,
        generated_by=generated_by
    )
    return generator.generate()


def create_manufacturer_pdf_response(
    manufacturer_performance_data,
    selected_workshop=None,
    generated_by=None
):
    """
    Create Django HTTP response with manufacturer performance PDF

    Returns:
        HttpResponse with PDF content
    """
    try:
        pdf_buffer = generate_manufacturer_performance_pdf(
            manufacturer_performance_data=manufacturer_performance_data,
            selected_workshop=selected_workshop,
            generated_by=generated_by
        )

        # Create response
        response = HttpResponse(pdf_buffer.getvalue(), content_type='application/pdf')

        # Generate filename
        timestamp = timezone.now().strftime('%Y%m%d_%H%M')

        if selected_workshop:
            workshop_name = selected_workshop.name.replace(' ', '_')
            filename = f"manufacturer_performance_{workshop_name}_{timestamp}.pdf"
        else:
            filename = f"manufacturer_performance_report_{timestamp}.pdf"

        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        return response

    except Exception as e:
        logger.error(f"Error generating manufacturer performance PDF: {str(e)}", exc_info=True)
        raise
