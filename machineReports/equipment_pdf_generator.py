"""
Complete Standalone Equipment Report PDF Generator
Includes base template, generator logic, and all necessary components
"""
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageTemplate, Paragraph,
    Table, TableStyle, Spacer, PageBreak
)
from reportlab.pdfgen import canvas as rl_canvas  # FIX: was missing entirely
from django.http import HttpResponse
from django.utils import timezone
from django.conf import settings
import os
import logging
from functools import partial
from io import BytesIO
from core.branding import contact_line

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

class EquipmentPDFTemplate(BaseDocTemplate):
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
        pagesize=landscape(A4),
        title="EQUIPMENT REPORT",
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
            pagesize: Page size (default landscape A4)
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
        self.contact_info = contact_info or contact_line("ISO/IEC 17025:2017")
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
        """Callback for each page - draws header, footer, then watermark on top"""
        canvas.saveState()

        # Draw header
        self._draw_header(canvas, doc)

        # Draw footer
        self._draw_footer(canvas, doc)

        canvas.restoreState()

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
        # FIX: was bare HexColor(...) — must be colors.HexColor(...)
        canvas.setStrokeColor(colors.HexColor('#1e40af'))
        canvas.setFillColor(colors.HexColor('#dbeafe'))
        canvas.roundRect(x, y, size, size, radius=4, fill=1, stroke=1)
        canvas.setFillColor(colors.HexColor('#1e40af'))
        font_size = size * 0.28
        canvas.setFont('Helvetica-Bold', font_size)
        canvas.drawCentredString(x + size / 2, y + size / 2 - font_size * 0.35, initials)


# =====================================================================
# EQUIPMENT REPORT GENERATOR
# =====================================================================

class EquipmentReportPDFGenerator:
    """
    Professional PDF generator for equipment reports
    """

    def __init__(
        self,
        equipment_queryset,
        selected_workshop=None,
        selected_category=None,
        search_query=None,
        report_type='detailed',
        generated_by=None
    ):
        """
        Initialize equipment report generator

        Args:
            equipment_queryset: Filtered Equipment queryset
            selected_workshop: Workshop object (optional)
            selected_category: EquipmentCategory object (optional)
            search_query: Search string (optional)
            report_type: 'detailed', 'summary', 'category'
            generated_by: User object
        """
        self.equipment_qs = equipment_queryset
        self.selected_workshop = selected_workshop
        self.selected_category = selected_category
        self.search_query = search_query
        self.report_type = report_type
        self.generated_by = generated_by

        # Setup styles
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

        # Color palette
        self.colors = EquipmentPDFTemplate.COLOR_PALETTE

    def _setup_custom_styles(self):
        """Setup custom paragraph styles"""
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading2'],
            fontSize=13,
            spaceAfter=12,
            spaceBefore=20,
            textColor=colors.HexColor('#1E40AF'),
            fontName='Helvetica-Bold'
        ))

        self.styles.add(ParagraphStyle(
            name='SubsectionHeader',
            parent=self.styles['Heading3'],
            fontSize=11,
            spaceAfter=10,
            spaceBefore=15,
            textColor=colors.HexColor('#059669'),
            fontName='Helvetica-Bold',
            borderWidth=1,
            borderColor=colors.HexColor('#059669'),
            borderPadding=5,
            backColor=colors.HexColor('#ECFDF5')
        ))

        self.styles.add(ParagraphStyle(
            name='StatsText',
            parent=self.styles['Normal'],
            fontSize=10,
            textColor=colors.HexColor('#374151'),
            fontName='Helvetica'
        ))

    def _get_report_title(self):
        """Generate report title based on filters"""
        parts = ["EQUIPMENT REPORT"]

        if self.selected_workshop:
            parts.append(f"Workshop: {self.selected_workshop.name}")

        if self.selected_category:
            parts.append(f"Category: {self.selected_category.name}")
        elif self.report_type == 'category':
            parts.append("All Categories")

        if self.search_query:
            parts.append(f"Search: '{self.search_query}'")

        return " | ".join(parts)

    def _get_report_subtitle(self):
        """Generate report subtitle"""
        count = self.equipment_qs.count()
        return f"Total Equipment: {count} items"

    def _create_equipment_table(self, equipment_list, category_name=None):
        """Create equipment table for a category or all equipment"""
        elements = []

        if category_name:
            elements.append(Paragraph(
                f"Category: {category_name} ({len(equipment_list)} items)",
                self.styles['SubsectionHeader']
            ))

            # Category statistics
            working = sum(1 for eq in equipment_list if eq.status == "Working")
            repair = sum(1 for eq in equipment_list if eq.status == "Under repair")
            not_working = sum(1 for eq in equipment_list if eq.status == "Not working")
            elements.append(Spacer(1, 0.3*cm))

        # Table headers
        table_data = [[
            'NO:', 'Equipment Name', 'Serial Number',
            'Model', 'Manufacturer', 'Status'
        ]]

        # Add equipment rows
        for i, equipment in enumerate(equipment_list, 1):
            table_data.append([
                str(i),
                equipment.description.name if equipment.description else 'N/A',
                equipment.serial_number or 'Unknown',
                equipment.model or 'N/A',
                equipment.manufacturer.name if equipment.manufacturer else 'Unknown',
                equipment.status or 'Unknown',
            ])

        # Create table
        col_widths = [2*cm, 7*cm, 5*cm, 5*cm, 5*cm, 4*cm]
        table = Table(table_data, colWidths=col_widths, repeatRows=1)

        # Style table
        table_style = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#6366F1')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 12),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 13),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 0.5, self.colors['border']),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]

        # Add alternating row colors and status-based coloring
        for row in range(1, len(table_data)):
            # Alternating rows
            bg_color = colors.HexColor('#F9FAFB') if row % 2 == 0 else colors.white
            table_style.append(('BACKGROUND', (0, row), (-1, row), bg_color))

            # Status-based coloring
            status = table_data[row][5]
            status_colors_map = {
                'Working': (colors.HexColor('#059669'), colors.white),
                'Under repair': (colors.HexColor('#D97706'), colors.white),
                'Not working': (colors.HexColor('#DC2626'), colors.white),
                'Due calibration': (colors.HexColor('#2563EB'), colors.white),
            }

            if status in status_colors_map:
                text_color, font = status_colors_map[status]
                table_style.append(('TEXTCOLOR', (5, row), (5, row), text_color))
                table_style.append(('FONTNAME', (5, row), (5, row), 'Helvetica-Bold'))

        table.setStyle(TableStyle(table_style))
        elements.append(table)

        return elements

    def _create_category_breakdown(self):
        """Create equipment breakdown by category"""
        elements = []

        # Import here to avoid circular imports
        from .models import EquipmentCategory

        categories = EquipmentCategory.objects.all()
        equipment_by_category = {}

        # Group equipment by category
        for category in categories:
            cat_equipment = list(self.equipment_qs.filter(category=category))
            if cat_equipment:
                equipment_by_category[category] = cat_equipment

        # Add uncategorized equipment
        uncategorized = list(self.equipment_qs.filter(category__isnull=True))
        if uncategorized:
            equipment_by_category['Uncategorized'] = uncategorized

        # Create sections for each category
        for idx, (category, equipment_list) in enumerate(equipment_by_category.items()):
            category_name = category.name if hasattr(category, 'name') else str(category)

            elements.extend(self._create_equipment_table(
                equipment_list,
                category_name
            ))

            # Add page break between categories (except last)
            if idx < len(equipment_by_category) - 1:
                elements.append(PageBreak())
            else:
                elements.append(Spacer(1, 1*cm))

        return elements

    def _create_category_summary_table(self):
        """Create category summary table"""
        elements = []

        # Import here to avoid circular imports
        from .models import EquipmentCategory

        elements.append(PageBreak())
        elements.append(Paragraph("Category Summary Breakdown", self.styles['SectionHeader']))

        categories = EquipmentCategory.objects.all()
        summary_data = [['Category', 'Total', 'Working', 'Under Repair', 'Not Working']]

        for category in categories:
            cat_equipment = self.equipment_qs.filter(category=category)
            total = cat_equipment.count()

            if total > 0:
                working = cat_equipment.filter(status="Working").count()
                repair = cat_equipment.filter(status="Under repair").count()
                not_working = cat_equipment.filter(status="Not working").count()

                summary_data.append([
                    category.name,
                    str(total),
                    f"{working} ({(working/total*100):.1f}%)",
                    f"{repair} ({(repair/total*100):.1f}%)",
                    f"{not_working} ({(not_working*100/total):.1f}%)"
                ])

        # Add uncategorized
        uncategorized = self.equipment_qs.filter(category__isnull=True)
        if uncategorized.exists():
            total = uncategorized.count()
            working = uncategorized.filter(status="Working").count()
            repair = uncategorized.filter(status="Under repair").count()
            not_working = uncategorized.filter(status="Not working").count()

            summary_data.append([
                'Uncategorized',
                str(total),
                f"{working} ({(working/total*100):.1f}%)",
                f"{repair} ({(repair/total*100):.1f}%)",
                f"{not_working} ({(not_working/total*100):.1f}%)"
            ])

        # Create table
        category_table = Table(summary_data, colWidths=[6*cm, 5*cm, 5*cm, 5*cm, 5*cm])
        category_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#059669')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 11),
            ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 10),
            ('GRID', (0, 0), (-1, -1), 0.5, self.colors['border']),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#F3F4F6')]),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))

        elements.append(category_table)
        elements.append(Spacer(1, 1*cm))

        return elements

    def _create_footer_section(self):
        """Create report footer with metadata"""
        elements = []

        elements.append(Paragraph("Report Information", self.styles['SubsectionHeader']))

        footer_text = f"""
        <b>Report Generated:</b> {timezone.localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p')}<br/>
        <b>Generated By:</b> {self.generated_by.get_full_name() if self.generated_by else 'System'}<br/>
        <b>Total Equipment Processed:</b> {self.equipment_qs.count()}<br/>
        <br/>
        <i>For questions or updates to equipment information, please contact the Biomedical Engineering Department.<br/>
        This report contains detailed equipment information including status indicators and summary statistics.</i>
        """

        elements.append(Paragraph(footer_text, self.styles['Normal']))

        return elements

    def _create_summary_section(self):
        """Create overall summary statistics"""
        elements = []

        elements.append(Paragraph("Overall Summary Statistics", self.styles['SectionHeader']))

        # Calculate statistics
        total = self.equipment_qs.count()

        if total == 0:
            elements.append(Paragraph(
                "No equipment found matching the selected criteria.",
                self.styles['Normal']
            ))
            return elements

        working = self.equipment_qs.filter(status="Working").count()
        under_repair = self.equipment_qs.filter(status="Under repair").count()
        not_working = self.equipment_qs.filter(status="Not working").count()

        # Summary table
        summary_data = [
            ['Status', 'Count', 'Percentage'],
            ['Total Equipment', str(total), '100.0%'],
            ['Working', str(working), f'{(working/total*100):.1f}%'],
            ['Under Repair', str(under_repair), f'{(under_repair/total*100):.1f}%'],
            ['Not Working', str(not_working), f'{(not_working/total*100):.1f}%'],
        ]

        summary_table = Table(summary_data, colWidths=[7*cm, 7*cm, 7*cm])

        # Style summary table
        table_style = [
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#E5E7EB')),
            ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#DBEAFE')),
            ('TEXTCOLOR', (0, 0), (-1, 0), self.colors['text_primary']),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 13),
            ('FONTSIZE', (0, 1), (-1, -1), 12),
            ('GRID', (0, 0), (-1, -1), 0.5, self.colors['border']),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]

        # Color status rows
        status_colors = {
            2: ('Working', colors.HexColor('#D1FAE5')),
            3: ('Under Repair', colors.HexColor('#FEF3C7')),
            4: ('Not Working', colors.HexColor('#FEE2E2')),
        }

        for row_idx, (status, color) in status_colors.items():
            table_style.append(('BACKGROUND', (0, row_idx), (-1, row_idx), color))

        summary_table.setStyle(TableStyle(table_style))
        elements.append(summary_table)
        elements.append(Spacer(1, 1*cm))

        return elements

    def generate(self):
        """Generate the complete PDF report"""
        buffer = BytesIO()

        # Create document with custom template
        doc = EquipmentPDFTemplate(
            buffer,
            pagesize=landscape(A4),
            title=self._get_report_title(),
            subtitle=self._get_report_subtitle(),
            department_name="Biomedical Engineering Department",
            contact_info=contact_line("ISO/IEC 17025:2017"),
        )

        # Build story
        story = []

        # Add summary section
        story.extend(self._create_summary_section())

        # Add equipment tables based on report type
        if self.report_type == 'category' or not self.selected_category:
            story.extend(self._create_category_breakdown())
            if self.equipment_qs.exists():
                story.extend(self._create_category_summary_table())
        else:
            story.extend(self._create_equipment_table(
                list(self.equipment_qs.order_by('description__name'))
            ))

        # Add footer
        story.extend(self._create_footer_section())

        # Build PDF
        doc.build(story)
        buffer.seek(0)

        return buffer


# =====================================================================
# PUBLIC API FUNCTIONS
# =====================================================================

def generate_equipment_report_pdf(
    equipment_queryset,
    selected_workshop=None,
    selected_category=None,
    search_query=None,
    report_type='detailed',
    generated_by=None
):
    """
    Generate equipment report PDF

    Args:
        equipment_queryset: Filtered Equipment queryset
        selected_workshop: Workshop object (optional)
        selected_category: EquipmentCategory object (optional)
        search_query: Search string (optional)
        report_type: 'detailed', 'summary', 'category'
        generated_by: User object

    Returns:
        BytesIO buffer containing the PDF
    """
    generator = EquipmentReportPDFGenerator(
        equipment_queryset=equipment_queryset,
        selected_workshop=selected_workshop,
        selected_category=selected_category,
        search_query=search_query,
        report_type=report_type,
        generated_by=generated_by
    )
    return generator.generate()


def create_equipment_pdf_response(
    equipment_queryset,
    selected_workshop=None,
    selected_category=None,
    search_query=None,
    report_type='detailed',
    generated_by=None
):
    """
    Create Django HTTP response with equipment PDF

    Returns:
        HttpResponse with PDF content
    """
    try:
        pdf_buffer = generate_equipment_report_pdf(
            equipment_queryset=equipment_queryset,
            selected_workshop=selected_workshop,
            selected_category=selected_category,
            search_query=search_query,
            report_type=report_type,
            generated_by=generated_by
        )

        # Create response
        response = HttpResponse(pdf_buffer.getvalue(), content_type='application/pdf')

        # Generate filename
        timestamp = timezone.now().strftime('%Y%m%d_%H%M')

        if selected_category:
            category_name = selected_category.name.replace(' ', '_')
        else:
            category_name = 'All_Categories'

        workshop_name = selected_workshop.name.replace(' ', '_') if selected_workshop else 'All_Workshops'

        filename = f"equipment_{category_name}_{workshop_name}_{timestamp}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        return response

    except Exception as e:
        logger.error(f"Error generating equipment PDF: {str(e)}", exc_info=True)
        raise
