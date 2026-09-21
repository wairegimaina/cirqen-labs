import io
import os
import logging
from datetime import datetime

from django.conf import settings
from reportlab.lib.pagesizes import letter, A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.lib.colors import HexColor, white, grey, black
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image, PageBreak, KeepTogether
from reportlab.graphics.shapes import Drawing, Rect, Circle, String
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.barcharts import VerticalBarChart, HorizontalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.pdfgen import canvas as rl_canvas
from collections import Counter
from django.db.models import Count, Q
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
                    mask='auto',
                )
                self.restoreState()
            except Exception as _wm_err:
                logger.warning(f"[WATERMARK] Overlay draw failed: {_wm_err}")
        rl_canvas.Canvas.showPage(self)

class EquipmentPDFGenerator:
    """
    Professional PDF generator for equipment inventory reports with modern design
    Supports multiple report types: detailed, summary, department-wise, and status reports
    Enhanced with contemporary color schemes and improved visual analytics
    """

    # Modern color palette - Cool professional tones
    COLOR_PALETTE = {
        'primary': HexColor('#0F172A'),        # Deep slate
        'secondary': HexColor('#1E40AF'),      # Royal blue
        'accent': HexColor('#06B6D4'),         # Cyan
        'success': HexColor('#10B981'),        # Emerald
        'warning': HexColor('#F59E0B'),        # Amber
        'danger': HexColor('#EF4444'),         # Red
        'info': HexColor('#3B82F6'),           # Blue
        'light': HexColor('#F8FAFC'),          # Slate 50
        'medium': HexColor('#E2E8F0'),         # Slate 200
        'dark': HexColor('#1E293B'),           # Slate 800
        'text_primary': HexColor('#0F172A'),   # Deep slate
        'text_secondary': HexColor('#64748B'), # Slate 600
        'border': HexColor('#CBD5E1'),         # Border color
        'light_bg': HexColor('#F8FAFC'),       # Light background
        'table_header': HexColor('#BFDBFE'),   # Light blue for table headers

        # Chart colors - Modern vibrant palette
        'chart_1': HexColor('#3B82F6'),  # Blue
        'chart_2': HexColor('#10B981'),  # Emerald
        'chart_3': HexColor('#F59E0B'),  # Amber
        'chart_4': HexColor('#EF4444'),  # Red
        'chart_5': HexColor('#8B5CF6'),  # Purple
        'chart_6': HexColor('#06B6D4'),  # Cyan
        'chart_7': HexColor('#EC4899'),  # Pink
        'chart_8': HexColor('#14B8A6'),  # Teal
        'chart_9': HexColor('#F97316'),  # Orange
        'chart_10': HexColor('#6366F1'), # Indigo
    }

    def __init__(self, workshop, equipment_queryset, report_type='detailed', context=None):
        self.workshop = workshop
        self.equipment_queryset = equipment_queryset
        self.report_type = report_type
        self.context = context or {}
        self.styles = getSampleStyleSheet()
        self.setup_custom_styles()

        # Determine page orientation based on report type
        self.pagesize = A4  # Always use A4
        self.is_landscape = False  # No landscape mode

        # Pagination settings for large datasets
        self.items_per_page = 35 if report_type == 'detailed' else 50

        # Find logo
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
        """Setup modern custom styles for the PDF"""
        # Main title style - Bold and modern
        self.styles.add(ParagraphStyle(
            name='MainTitle',
            parent=self.styles['Title'],
            fontSize=26,
            textColor=self.COLOR_PALETTE['primary'],
            alignment=TA_CENTER,
            spaceAfter=30,
            spaceBefore=10,
            fontName='Helvetica-Bold',
            leading=30
        ))

        # Section header style - Clean and professional
        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            parent=self.styles['Heading2'],
            fontSize=18,
            textColor=self.COLOR_PALETTE['secondary'],
            alignment=TA_LEFT,
            spaceAfter=20,
            spaceBefore=15,
            fontName='Helvetica-Bold',
            borderPadding=5,
            leading=22
        ))

        # Subtitle style - Modern and subtle
        self.styles.add(ParagraphStyle(
            name='Subtitle',
            parent=self.styles['Normal'],
            fontSize=12,
            textColor=self.COLOR_PALETTE['text_secondary'],
            alignment=TA_CENTER,
            spaceAfter=25,
            leading=18
        ))

        # Info text style
        self.styles.add(ParagraphStyle(
            name='InfoText',
            parent=self.styles['Normal'],
            fontSize=11,
            textColor=self.COLOR_PALETTE['text_secondary'],
            alignment=TA_LEFT,
            spaceAfter=10,
            leading=16
        ))

        # Warning text style
        self.styles.add(ParagraphStyle(
            name='WarningText',
            parent=self.styles['Normal'],
            fontSize=11,
            textColor=self.COLOR_PALETTE['danger'],
            alignment=TA_LEFT,
            spaceAfter=10,
            fontName='Helvetica-Bold'
        ))

        # Metric card style
        self.styles.add(ParagraphStyle(
            name='MetricCard',
            parent=self.styles['Normal'],
            fontSize=13,
            textColor=self.COLOR_PALETTE['primary'],
            alignment=TA_CENTER,
            spaceAfter=5,
            fontName='Helvetica-Bold'
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
        """Create modern header and footer matching PPM style. Watermark handled by _LogoWatermarkCanvas."""
        width, height = self.pagesize
        canvas.saveState()

        # HEADER - Clean design with light background (no colored background)
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

        # Title - Determine based on report type
        title_mapping = {
            'detailed': 'EQUIPMENT INVENTORY REPORT',
            'summary': 'EQUIPMENT INVENTORY SUMMARY',
            'department': 'DEPARTMENT-WISE ANALYSIS',
            'status': 'EQUIPMENT STATUS REPORT'
        }
        title_text = title_mapping.get(self.report_type, 'EQUIPMENT INVENTORY REPORT')

        canvas.setFillColor(self.COLOR_PALETTE['primary'])
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawCentredString(
            width / 2,
            height - 1.2*cm,
            title_text
        )

        # Department name
        canvas.setFont("Helvetica", 10)
        canvas.setFillColor(self.COLOR_PALETTE['text_primary'])
        canvas.drawCentredString(
            width / 2,
            height - 1.7*cm,
            "Biomedical Engineering Department"
        )

        # Subtitle with workshop/department
        canvas.setFont("Helvetica-Oblique", 9)
        canvas.setFillColor(self.COLOR_PALETTE['text_secondary'])

        subtitle_parts = [f"Workshop: {self.workshop.name}"]
        if self.context.get('department'):
            subtitle_parts.append(f"Department: {self.context['department'].name}")
        if self.context.get('status_filter'):
            subtitle_parts.append(f"Status: {self.context['status_filter']}")

        subtitle = " | ".join(subtitle_parts)
        canvas.drawCentredString(
            width / 2,
            height - 2.1*cm,
            subtitle
        )

        # Contact info
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(self.COLOR_PALETTE['text_secondary'])
        canvas.drawCentredString(
            width / 2,
            height - 2.7*cm,
            contact_line("ISO/IEC 17025:2017")
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
        """Create modern title section with metrics cards"""
        elements = []

        # Main title based on report type
        title_mapping = {
            'detailed': 'DETAILED EQUIPMENT INVENTORY',
            'summary': 'EQUIPMENT INVENTORY SUMMARY',
            'department': 'DEPARTMENT-WISE ANALYSIS',
            'status': 'EQUIPMENT STATUS REPORT'
        }

        title_text = title_mapping.get(self.report_type, 'EQUIPMENT INVENTORY REPORT')
        title = Paragraph(title_text, self.styles['MainTitle'])
        elements.append(title)

        # Workshop and filter information
        subtitle_parts = [f"<b>Workshop:</b> {self.workshop.name}"]

        if self.context.get('department'):
            subtitle_parts.append(f"<b>Department:</b> {self.context['department'].name}")

        if self.context.get('status_filter'):
            subtitle_parts.append(f"<b>Status:</b> {self.context['status_filter']}")

        total_count = self.equipment_queryset.count()
        subtitle_parts.append(f"<b>Total Equipment:</b> {total_count:,}")

        subtitle_text = " • ".join(subtitle_parts)
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

    def create_overview_section(self):
        """Create executive overview with modern metrics cards"""
        elements = []
        header = Paragraph("EXECUTIVE OVERVIEW", self.styles['SectionHeader'])
        elements.append(header)

        # Calculate statistics
        equipments = list(self.equipment_queryset.select_related(
            'description', 'department', 'manufacturer'
        ))
        total_count = len(equipments)

        if total_count == 0:
            no_data = Paragraph("No equipment found matching the selected criteria.",
                              self.styles['WarningText'])
            elements.append(no_data)
            return elements

        # Status breakdown
        status_counts = Counter(eq.status for eq in equipments)

        # Manufacturer breakdown
        manufacturer_counts = Counter(
            eq.manufacturer.name if eq.manufacturer else 'No Manufacturer'
            for eq in equipments
        )

        # Department breakdown
        dept_counts = Counter(eq.department.name for eq in equipments)

        # Create modern overview table with auto-fit columns
        overview_data = [
            ['Metric', 'Count', 'Percentage', 'Status']
        ]

        # Add status rows with indicators
        working_count = status_counts.get('Working', 0)
        working_pct = (working_count/total_count*100) if total_count > 0 else 0
        overview_data.append([
            'Working Equipment',
            f"{working_count:,}",
            f"{working_pct:.1f}%",
            '✓ Operational'
        ])

        not_working_count = status_counts.get('Not working', 0)
        not_working_pct = (not_working_count/total_count*100) if total_count > 0 else 0
        overview_data.append([
            'Not Working',
            f"{not_working_count:,}",
            f"{not_working_pct:.1f}%",
            '✗ Critical'
        ])

        repair_count = status_counts.get('Under repair', 0)
        repair_pct = (repair_count/total_count*100) if total_count > 0 else 0
        overview_data.append([
            'Under Repair',
            f"{repair_count:,}",
            f"{repair_pct:.1f}%",
            '⚠ In Progress'
        ])

        # Add manufacturer breakdown (top 3)
        top_manufacturers = manufacturer_counts.most_common(3)
        for manufacturer, count in top_manufacturers:
            percentage = (count/total_count*100) if total_count > 0 else 0
            # Truncate long manufacturer names
            mfr_name = manufacturer[:25] + '...' if len(manufacturer) > 25 else manufacturer
            overview_data.append([
                f'{mfr_name}',
                f"{count:,}",
                f"{percentage:.1f}%",
                'Manufacturer'
            ])

        # Add summary rows
        overview_data.extend([
            ['Active Departments', f"{len(dept_counts):,}", '-', 'Info'],
            ['Total Inventory', f"{total_count:,}", '100.0%', 'Total']
        ])

        # Smaller, more compact column widths
        col_widths = [2.6*inch, 1.2*inch, 1.1*inch, 1.4*inch]
        overview_table = Table(overview_data, colWidths=col_widths)
        overview_table.setStyle(TableStyle([
            # Header styling - medium weight and better font size
            ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_PALETTE['table_header']),
            ('TEXTCOLOR', (0, 0), (-1, 0), self.COLOR_PALETTE['primary']),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (0, 1), (0, -1), 'LEFT'),  # Metric column left aligned
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),  # Bold for headers
            ('FONTSIZE', (0, 0), (-1, 0), 10),  # Increased from 9
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 6),

            # Data rows styling
            ('BACKGROUND', (0, 1), (-1, -2), white),
            ('FONTNAME', (0, 1), (0, -1), 'Helvetica'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),  # Increased from 8
            ('GRID', (0, 0), (-1, -1), 0.15, self.COLOR_PALETTE['border']),  # Thinner border
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [white, HexColor('#F9FAFB')]),

            # Status-specific modern coloring
            ('BACKGROUND', (0, 1), (-1, 1), HexColor('#ECFDF5')),
            ('TEXTCOLOR', (3, 1), (3, 1), self.COLOR_PALETTE['success']),
            ('BACKGROUND', (0, 2), (-1, 2), HexColor('#FEF2F2')),
            ('TEXTCOLOR', (3, 2), (3, 2), self.COLOR_PALETTE['danger']),
            ('BACKGROUND', (0, 3), (-1, 3), HexColor('#FFFBEB')),
            ('TEXTCOLOR', (3, 3), (3, 3), self.COLOR_PALETTE['warning']),

            # Total row emphasis
            ('BACKGROUND', (0, -1), (-1, -1), self.COLOR_PALETTE['primary']),
            ('TEXTCOLOR', (0, -1), (-1, -1), white),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, -1), (-1, -1), 9),

            # Modern padding - reduced
            ('LEFTPADDING', (0, 0), (-1, -1), 5),
            ('RIGHTPADDING', (0, 0), (-1, -1), 5),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))

        elements.append(overview_table)
        elements.append(Spacer(1, 25))

        # Add page break after overview section
        elements.append(PageBreak())

        return elements

    def create_detailed_equipment_table_paginated(self):
        """Create detailed equipment listing with modern design"""
        elements = []

        # Start on new page
        elements.append(PageBreak())

        equipments = list(self.equipment_queryset.select_related(
            'description', 'department', 'manufacturer'
        ).order_by('department__name', 'description__name', 'serial_number'))

        if not equipments:
            header = Paragraph("DETAILED EQUIPMENT INVENTORY", self.styles['SectionHeader'])
            elements.append(header)
            no_data = Paragraph("No equipment found matching the selected criteria.",
                              self.styles['WarningText'])
            elements.append(no_data)
            return elements

        # Table headers with adjusted text
        headers = ['No.', 'Description', 'Model', 'Serial No.', 'Department', 'Status']
        col_widths = [0.4*inch, 2.0*inch, 1.2*inch, 1.3*inch, 1.3*inch, 0.9*inch]

        # Split equipment into pages
        total_items = len(equipments)
        pages = [equipments[i:i + self.items_per_page]
                for i in range(0, total_items, self.items_per_page)]

        for page_num, page_equipments in enumerate(pages, 1):
            # Add section header only on first page
            if page_num == 1:
                header = Paragraph("DETAILED EQUIPMENT INVENTORY", self.styles['SectionHeader'])
                elements.append(header)

            # Page info for multi-page reports
            if len(pages) > 1:
                page_info = Paragraph(
                    f"<b>Page {page_num} of {len(pages)}</b> • Items {(page_num-1)*self.items_per_page + 1}-{min(page_num*self.items_per_page, total_items)} of {total_items}",
                    self.styles['InfoText']
                )
                elements.append(page_info)
                elements.append(Spacer(1, 10))

            # Create table data for this page
            data = [headers]

            for idx, equipment in enumerate(page_equipments, start=(page_num-1)*self.items_per_page + 1):
                row = [
                    str(idx),
                    equipment.description.name[:35],
                    equipment.model[:20] if equipment.model else 'N/A',
                    equipment.serial_number[:20] if equipment.serial_number else 'N/A',
                    equipment.department.name[:20],
                    equipment.status
                ]
                data.append(row)

            # Create and style table with modern design
            table = Table(data, colWidths=col_widths, repeatRows=1)

            # Modern table styling - better visibility with bold headers
            table_style = [
                # Header styling - bold and readable
                ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_PALETTE['table_header']),
                ('TEXTCOLOR', (0, 0), (-1, 0), self.COLOR_PALETTE['primary']),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('ALIGN', (0, 0), (0, -1), 'CENTER'),  # No. column centered
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),  # Bold for headers
                ('FONTSIZE', (0, 0), (-1, 0), 9),  # Good readable size
                ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
                ('TOPPADDING', (0, 0), (-1, 0), 6),

                # Data rows
                ('FONTSIZE', (0, 1), (-1, -1), 9),  # Increased from 8
                ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
                ('GRID', (0, 0), (-1, -1), 0.15, self.COLOR_PALETTE['border']),  # Thinner border
                ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),

                # Modern padding - reduced
                ('LEFTPADDING', (0, 0), (-1, -1), 3),
                ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ]

            # Add alternating row colors and status-based coloring
            status_col = 5
            for i, equipment in enumerate(page_equipments, 1):
                # Alternating rows
                if i % 2 == 0:
                    table_style.append(('BACKGROUND', (0, i), (-1, i), HexColor('#F9FAFB')))

                # Status-based coloring
                if equipment.status == 'Working':
                    table_style.append(('BACKGROUND', (status_col, i), (status_col, i), HexColor('#ECFDF5')))
                    table_style.append(('TEXTCOLOR', (status_col, i), (status_col, i), self.COLOR_PALETTE['success']))
                elif equipment.status == 'Not working':
                    table_style.append(('BACKGROUND', (status_col, i), (status_col, i), HexColor('#FEF2F2')))
                    table_style.append(('TEXTCOLOR', (status_col, i), (status_col, i), self.COLOR_PALETTE['danger']))
                elif equipment.status == 'Under repair':
                    table_style.append(('BACKGROUND', (status_col, i), (status_col, i), HexColor('#FFFBEB')))
                    table_style.append(('TEXTCOLOR', (status_col, i), (status_col, i), self.COLOR_PALETTE['warning']))

            table.setStyle(TableStyle(table_style))

            # Wrap table to keep it together when possible
            elements.append(KeepTogether(table))

            # Add page break between pages (except for last page)
            if page_num < len(pages):
                elements.append(PageBreak())

        elements.append(Spacer(1, 25))
        return elements

    def create_summary_table(self):
        """Create equipment summary by description"""
        elements = []

        # Start on new page
        elements.append(PageBreak())

        header = Paragraph("EQUIPMENT SUMMARY BY TYPE", self.styles['SectionHeader'])
        elements.append(header)

        # Aggregate data by equipment description
        summary_data = self.equipment_queryset.values(
            'description__name'
        ).annotate(
            total_count=Count('id'),
            working_count=Count('id', filter=Q(status='Working')),
            not_working_count=Count('id', filter=Q(status='Not working')),
            under_repair_count=Count('id', filter=Q(status='Under repair'))
        ).order_by('description__name')

        if not summary_data:
            no_data = Paragraph("No equipment found for summary.", self.styles['WarningText'])
            elements.append(no_data)
            return elements

        # Table headers
        headers = ['No.', 'Equipment Type', 'Working', 'Not Working', 'Repair', 'Total']
        col_widths = [0.4*inch, 3.0*inch, 0.95*inch, 1.05*inch, 0.85*inch, 0.9*inch]

        data = [headers]

        # Add summary data
        totals = {'working': 0, 'not_working': 0, 'under_repair': 0, 'total': 0}

        for idx, item in enumerate(summary_data, 1):
            row = [
                str(idx),
                item['description__name'][:45],
                str(item['working_count']),
                str(item['not_working_count']),
                str(item['under_repair_count']),
                str(item['total_count'])
            ]
            data.append(row)

            # Update totals
            totals['working'] += item['working_count']
            totals['not_working'] += item['not_working_count']
            totals['under_repair'] += item['under_repair_count']
            totals['total'] += item['total_count']

        # Add totals row
        data.append([
            '',
            'TOTAL',
            str(totals['working']),
            str(totals['not_working']),
            str(totals['under_repair']),
            str(totals['total'])
        ])

        # Create table with pagination if needed
        if len(data) > 50:
            self._create_paginated_summary_table(elements, data, headers, col_widths)
        else:
            table = Table(data, colWidths=col_widths, repeatRows=1)
            table.setStyle(self._get_summary_table_style())
            elements.append(table)

        elements.append(Spacer(1, 25))
        return elements

    def _create_paginated_summary_table(self, elements, data, headers, col_widths):
        """Helper method to create paginated summary tables"""
        # Separate totals row
        totals_row = data[-1]
        data_rows = data[1:-1]  # Exclude headers and totals

        # Split into pages (45 rows per page to leave room for totals)
        page_size = 45
        pages = [data_rows[i:i + page_size] for i in range(0, len(data_rows), page_size)]

        for page_num, page_data in enumerate(pages, 1):
            # Add page info for multi-page tables
            if len(pages) > 1:
                page_info = Paragraph(
                    f"<b>Summary Table - Page {page_num} of {len(pages)}</b>",
                    self.styles['InfoText']
                )
                elements.append(page_info)
                elements.append(Spacer(1, 5))

            # Create page data with headers
            page_table_data = [headers] + page_data

            # Add totals on last page
            if page_num == len(pages):
                page_table_data.append(totals_row)

            table = Table(page_table_data, colWidths=col_widths, repeatRows=1)
            table.setStyle(self._get_summary_table_style())
            elements.append(KeepTogether(table))

            # Add page break between pages
            if page_num < len(pages):
                elements.append(PageBreak())

    def _get_summary_table_style(self):
        """Get modern table style for summary tables"""
        return TableStyle([
            # Header styling - bold and readable
            ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_PALETTE['table_header']),
            ('TEXTCOLOR', (0, 0), (-1, 0), self.COLOR_PALETTE['primary']),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),  # No. column centered
            ('ALIGN', (1, 1), (1, -2), 'LEFT'),  # Equipment Type left aligned
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),  # Bold for headers
            ('FONTSIZE', (0, 0), (-1, 0), 9),  # Good readable size
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 6),

            # Data rows
            ('FONTSIZE', (0, 1), (-1, -2), 9),  # Increased from 8
            ('FONTNAME', (1, 1), (1, -2), 'Helvetica-Bold'),  # Equipment Type bold
            ('GRID', (0, 0), (-1, -1), 0.15, self.COLOR_PALETTE['border']),  # Thinner border
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [white, HexColor('#F9FAFB')]),

            # Status columns modern coloring
            ('BACKGROUND', (2, 1), (2, -2), HexColor('#ECFDF5')),  # Working
            ('TEXTCOLOR', (2, 1), (2, -2), self.COLOR_PALETTE['success']),
            ('BACKGROUND', (3, 1), (3, -2), HexColor('#FEF2F2')),  # Not working
            ('TEXTCOLOR', (3, 1), (3, -2), self.COLOR_PALETTE['danger']),
            ('BACKGROUND', (4, 1), (4, -2), HexColor('#FFFBEB')),  # Under repair
            ('TEXTCOLOR', (4, 1), (4, -2), self.COLOR_PALETTE['warning']),

            # Totals row
            ('BACKGROUND', (0, -1), (-1, -1), self.COLOR_PALETTE['primary']),
            ('TEXTCOLOR', (0, -1), (-1, -1), white),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, -1), (-1, -1), 9),

            # Modern padding - reduced
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ])

    def create_equipment_description_analysis(self):
        """Create equipment description analysis - shows which equipment types are most common"""
        elements = []
        header = Paragraph("EQUIPMENT TYPE ANALYSIS", self.styles['SectionHeader'])
        elements.append(header)

        # Aggregate data by equipment description
        description_data = self.equipment_queryset.values(
            'description__name'
        ).annotate(
            total_count=Count('id'),
            working_count=Count('id', filter=Q(status='Working')),
            not_working_count=Count('id', filter=Q(status='Not working')),
            under_repair_count=Count('id', filter=Q(status='Under repair')),
            dept_count=Count('department', distinct=True),
            manufacturer_count=Count('manufacturer', distinct=True)
        ).order_by('-total_count')  # Order by most common first

        if not description_data:
            no_data = Paragraph("No equipment descriptions found for analysis.",
                              self.styles['WarningText'])
            elements.append(no_data)
            return elements

        # Add summary paragraph
        total_descriptions = description_data.count()
        summary_text = (
            f"Found <b>{total_descriptions}</b> different equipment types across the inventory. "
            f"This analysis shows equipment distribution, operational status, and deployment across departments."
        )
        summary_para = Paragraph(summary_text, self.styles['InfoText'])
        elements.append(summary_para)
        elements.append(Spacer(1, 10))

        # Table headers
        headers = ['No.', 'Equipment Type', 'Total', 'Working', 'Not Working', 'Repair', 'Depts', 'Mfrs']
        col_widths = [0.4*inch, 2.2*inch, 0.6*inch, 0.7*inch, 0.85*inch, 0.7*inch, 0.65*inch, 0.65*inch]

        data = [headers]

        # Add description data (limit to top 20 for readability, or show all if less)
        display_limit = min(20, len(description_data))
        totals = {
            'total': 0,
            'working': 0,
            'not_working': 0,
            'under_repair': 0,
            'departments': set(),
            'manufacturers': set()
        }

        for i, item in enumerate(description_data[:display_limit], 1):
            # Calculate health percentage
            health_pct = (item['working_count'] / item['total_count'] * 100) if item['total_count'] > 0 else 0

            row = [
                str(i),
                item['description__name'][:35],
                str(item['total_count']),
                str(item['working_count']),
                str(item['not_working_count']),
                str(item['under_repair_count']),
                str(item['dept_count']),
                str(item['manufacturer_count'])
            ]
            data.append(row)

            # Update totals
            totals['total'] += item['total_count']
            totals['working'] += item['working_count']
            totals['not_working'] += item['not_working_count']
            totals['under_repair'] += item['under_repair_count']

        # Add totals row
        data.append([
            '',
            f'TOP {display_limit} TOTAL',
            str(totals['total']),
            str(totals['working']),
            str(totals['not_working']),
            str(totals['under_repair']),
            '-',
            '-'
        ])

        # Create modern table
        table = Table(data, colWidths=col_widths, repeatRows=1)
        table.setStyle(TableStyle([
            # Header styling - bold and readable
            ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_PALETTE['table_header']),
            ('TEXTCOLOR', (0, 0), (-1, 0), self.COLOR_PALETTE['primary']),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),  # Bold for headers
            ('FONTSIZE', (0, 0), (-1, 0), 9),  # Good readable size
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 6),

            # Data rows
            ('FONTSIZE', (0, 1), (-1, -2), 9),  # Increased from 8
            ('FONTNAME', (1, 1), (1, -2), 'Helvetica-Bold'),
            ('ALIGN', (1, 1), (1, -2), 'LEFT'),
            ('GRID', (0, 0), (-1, -1), 0.15, self.COLOR_PALETTE['border']),  # Thinner border
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [white, HexColor('#F9FAFB')]),

            # Status columns modern coloring
            ('BACKGROUND', (3, 1), (3, -2), HexColor('#ECFDF5')),  # Working
            ('TEXTCOLOR', (3, 1), (3, -2), self.COLOR_PALETTE['success']),
            ('BACKGROUND', (4, 1), (4, -2), HexColor('#FEF2F2')),  # Not working
            ('TEXTCOLOR', (4, 1), (4, -2), self.COLOR_PALETTE['danger']),
            ('BACKGROUND', (5, 1), (5, -2), HexColor('#FFFBEB')),  # Under repair
            ('TEXTCOLOR', (5, 1), (5, -2), self.COLOR_PALETTE['warning']),

            # Info columns
            ('BACKGROUND', (6, 1), (6, -2), HexColor('#F0F9FF')),  # Departments
            ('TEXTCOLOR', (6, 1), (6, -2), self.COLOR_PALETTE['info']),
            ('BACKGROUND', (7, 1), (7, -2), HexColor('#F5F3FF')),  # Manufacturers
            ('TEXTCOLOR', (7, 1), (7, -2), self.COLOR_PALETTE['chart_5']),

            # Totals row
            ('BACKGROUND', (0, -1), (-1, -1), self.COLOR_PALETTE['primary']),
            ('TEXTCOLOR', (0, -1), (-1, -1), white),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, -1), (-1, -1), 9),

            # Modern padding - reduced
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))

        elements.append(table)

        # Add note if there are more items
        if len(description_data) > display_limit:
            remaining = len(description_data) - display_limit
            note_text = f"<i>Note: Showing top {display_limit} equipment types. {remaining} additional types not displayed.</i>"
            note_para = Paragraph(note_text, self.styles['InfoText'])
            elements.append(Spacer(1, 10))
            elements.append(note_para)

        elements.append(Spacer(1, 25))

        # Page break is handled by next section, no need to add here

        return elements

    def create_department_analysis(self):
        """Create department-wise equipment analysis with modern design"""
        elements = []

        # Start on new page
        elements.append(PageBreak())

        header = Paragraph("DEPARTMENT-WISE EQUIPMENT ANALYSIS", self.styles['SectionHeader'])
        elements.append(header)

        # Aggregate data by department
        dept_data = self.equipment_queryset.values(
            'department__name'
        ).annotate(
            total_count=Count('id'),
            working_count=Count('id', filter=Q(status='Working')),
            not_working_count=Count('id', filter=Q(status='Not working')),
            under_repair_count=Count('id', filter=Q(status='Under repair')),
            with_manufacturer=Count('id', filter=Q(manufacturer__isnull=False))
        ).order_by('department__name')

        if not dept_data:
            no_data = Paragraph("No departments found for analysis.", self.styles['WarningText'])
            elements.append(no_data)
            return elements

        # Table headers
        headers = ['No.', 'Department', 'Working', 'Not Working', 'Repair', 'With Mfr', 'Total']
        col_widths = [0.4*inch, 2.2*inch, 0.9*inch, 1.0*inch, 0.75*inch, 0.9*inch, 0.9*inch]

        data = [headers]

        # Add department data
        totals = {'working': 0, 'not_working': 0, 'under_repair': 0, 'with_manufacturer': 0, 'total': 0}

        for idx, item in enumerate(dept_data, 1):
            row = [
                str(idx),
                item['department__name'][:30],
                str(item['working_count']),
                str(item['not_working_count']),
                str(item['under_repair_count']),
                str(item['with_manufacturer']),
                str(item['total_count'])
            ]
            data.append(row)

            # Update totals
            totals['working'] += item['working_count']
            totals['not_working'] += item['not_working_count']
            totals['under_repair'] += item['under_repair_count']
            totals['with_manufacturer'] += item['with_manufacturer']
            totals['total'] += item['total_count']

        # Add totals row
        data.append([
            '',
            'TOTAL',
            str(totals['working']),
            str(totals['not_working']),
            str(totals['under_repair']),
            str(totals['with_manufacturer']),
            str(totals['total'])
        ])

        # Create modern table
        table = Table(data, colWidths=col_widths)
        table.setStyle(TableStyle([
            # Header styling - bold and readable
            ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_PALETTE['table_header']),
            ('TEXTCOLOR', (0, 0), (-1, 0), self.COLOR_PALETTE['primary']),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (0, 0), (0, -1), 'CENTER'),  # No. column
            ('ALIGN', (1, 1), (1, -2), 'LEFT'),  # Department left aligned
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),  # Bold for headers
            ('FONTSIZE', (0, 0), (-1, 0), 9),  # Good readable size
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 6),

            # Data rows
            ('FONTSIZE', (0, 1), (-1, -2), 9),  # Increased from 8
            ('FONTNAME', (1, 1), (1, -2), 'Helvetica-Bold'),  # Department bold
            ('GRID', (0, 0), (-1, -1), 0.15, self.COLOR_PALETTE['border']),  # Thinner border
            ('ROWBACKGROUNDS', (0, 1), (-1, -2), [white, HexColor('#F9FAFB')]),

            # Modern column-specific coloring
            ('BACKGROUND', (2, 1), (2, -2), HexColor('#ECFDF5')),  # Working
            ('TEXTCOLOR', (2, 1), (2, -2), self.COLOR_PALETTE['success']),
            ('BACKGROUND', (3, 1), (3, -2), HexColor('#FEF2F2')),  # Not working
            ('TEXTCOLOR', (3, 1), (3, -2), self.COLOR_PALETTE['danger']),
            ('BACKGROUND', (4, 1), (4, -2), HexColor('#FFFBEB')),  # Under repair
            ('TEXTCOLOR', (4, 1), (4, -2), self.COLOR_PALETTE['warning']),
            ('BACKGROUND', (5, 1), (5, -2), HexColor('#EFF6FF')),  # With Manufacturer
            ('TEXTCOLOR', (5, 1), (5, -2), self.COLOR_PALETTE['info']),

            # Totals row
            ('BACKGROUND', (0, -1), (-1, -1), self.COLOR_PALETTE['primary']),
            ('TEXTCOLOR', (0, -1), (-1, -1), white),
            ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, -1), (-1, -1), 9),

            # Modern padding - reduced
            ('LEFTPADDING', (0, 0), (-1, -1), 3),
            ('RIGHTPADDING', (0, 0), (-1, -1), 3),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ]))

        elements.append(table)
        elements.append(Spacer(1, 25))

        return elements

    def create_charts_section(self):
        """Create modern visual charts section"""
        elements = []

        # Start on new page
        elements.append(PageBreak())

        header = Paragraph("VISUAL ANALYTICS", self.styles['SectionHeader'])
        elements.append(header)

        equipments = list(self.equipment_queryset.select_related(
            'description', 'department', 'manufacturer'
        ))

        if not equipments:
            no_data = Paragraph("No data available for charts.", self.styles['WarningText'])
            elements.append(no_data)
            return elements

        # Status pie chart with modern design
        status_counts = Counter(eq.status for eq in equipments)
        if status_counts:
            elements.extend(self.create_modern_status_chart(status_counts))

        # Department bar chart (if multiple departments)
        dept_counts = Counter(eq.department.name for eq in equipments)
        if len(dept_counts) > 1:
            elements.extend(self.create_modern_department_chart(dept_counts))

        # Manufacturer distribution chart
        manufacturer_counts = Counter(
            eq.manufacturer.name if eq.manufacturer else 'No Manufacturer'
            for eq in equipments
        )
        if len(manufacturer_counts) > 1:
            elements.extend(self.create_modern_manufacturer_chart(manufacturer_counts))

        # Equipment description distribution chart (top 10)
        description_counts = Counter(eq.description.name for eq in equipments)
        if len(description_counts) > 1:
            elements.extend(self.create_modern_description_chart(description_counts))

        return elements

    def create_modern_description_chart(self, description_counts):
        """Create modern chart for equipment description distribution"""
        elements = []

        chart_header = Paragraph(
            "<b>Top Equipment Types Distribution</b>",
            self.styles['InfoText']
        )
        elements.append(chart_header)
        elements.append(Spacer(1, 10))

        # Limit to top 10 for clarity
        top_descriptions = dict(description_counts.most_common(10))

        if not top_descriptions:
            return elements

        drawing = Drawing(550, 300)
        chart = VerticalBarChart()
        chart.x = 50
        chart.y = 80
        chart.width = 450
        chart.height = 180

        descriptions = [desc[:25] + '...' if len(desc) > 25 else desc
                       for desc in top_descriptions.keys()]
        values = list(top_descriptions.values())

        chart.data = [values]
        chart.categoryAxis.categoryNames = descriptions

        # Modern styling - use single color for all bars
        chart.bars[0].fillColor = self.COLOR_PALETTE['accent']
        chart.bars[0].strokeColor = self.COLOR_PALETTE['secondary']
        chart.bars.strokeWidth = 2

        # Style axes
        chart.valueAxis.valueMin = 0
        chart.valueAxis.valueMax = max(values) * 1.15 if values else 10
        chart.valueAxis.valueStep = max(1, max(values) // 5) if values else 1
        chart.categoryAxis.labels.fontName = 'Helvetica'
        chart.categoryAxis.labels.fontSize = 7
        chart.categoryAxis.labels.angle = 45
        chart.categoryAxis.labels.boxAnchor = 'e'
        chart.valueAxis.labels.fontName = 'Helvetica'
        chart.valueAxis.labels.fontSize = 9

        chart.barWidth = 20
        chart.barSpacing = 2

        drawing.add(chart)
        elements.append(drawing)
        elements.append(Spacer(1, 20))

        return elements

    def create_modern_status_chart(self, status_counts):
        """Create modern donut-style status distribution chart"""
        elements = []

        chart_header = Paragraph(
            "<b>Equipment Status Distribution</b>",
            self.styles['InfoText']
        )
        elements.append(chart_header)
        elements.append(Spacer(1, 10))

        drawing = Drawing(500, 250)

        # Modern pie chart with better styling
        pie = Pie()
        pie.x = 120
        pie.y = 50
        pie.width = 150
        pie.height = 150

        # Data and modern colors
        statuses = list(status_counts.keys())
        values = list(status_counts.values())

        # Modern status colors
        color_map = {
            'Working': self.COLOR_PALETTE['success'],
            'Not working': self.COLOR_PALETTE['danger'],
            'Under repair': self.COLOR_PALETTE['warning']
        }
        colors = [color_map.get(status, self.COLOR_PALETTE['info']) for status in statuses]

        pie.data = values
        pie.labels = [f'{v}' for v in values]  # Just numbers on slices
        pie.slices.strokeWidth = 2
        pie.slices.strokeColor = white
        pie.slices.fontName = 'Helvetica-Bold'
        pie.slices.fontSize = 12

        # Create donut effect
        pie.innerRadiusFraction = 0.5

        # Assign colors
        for i, color in enumerate(colors):
            pie.slices[i].fillColor = color
            pie.slices[i].labelRadius = 1.2
            pie.slices[i].fontColor = color

        # Modern legend with better positioning
        legend = Legend()
        legend.x = 320
        legend.y = 150
        legend.dx = 10
        legend.dy = 10
        legend.fontName = 'Helvetica'
        legend.fontSize = 11
        legend.boxAnchor = 'w'
        legend.columnMaximum = 10
        legend.strokeWidth = 0
        legend.deltay = 15

        # Calculate percentages for legend
        total = sum(values)
        legend.colorNamePairs = [
            (colors[i], f'{status}: {count} ({count/total*100:.1f}%)')
            for i, (status, count) in enumerate(status_counts.items())
        ]

        drawing.add(pie)
        drawing.add(legend)
        elements.append(drawing)
        elements.append(Spacer(1, 20))

        return elements

    def create_modern_department_chart(self, dept_counts):
        """Create modern horizontal bar chart for department distribution"""
        elements = []

        chart_header = Paragraph(
            "<b>Equipment Distribution by Department</b>",
            self.styles['InfoText']
        )
        elements.append(chart_header)
        elements.append(Spacer(1, 10))

        drawing = Drawing(500, 300)
        chart = HorizontalBarChart()
        chart.x = 150
        chart.y = 50
        chart.width = 300
        chart.height = 200

        departments = list(dept_counts.keys())
        values = list(dept_counts.values())

        chart.data = [values]
        chart.categoryAxis.categoryNames = departments

        # Modern gradient-like colors
        chart.bars[0].fillColor = self.COLOR_PALETTE['secondary']
        chart.bars[0].strokeColor = self.COLOR_PALETTE['accent']
        chart.bars.strokeWidth = 2

        # Style axes
        chart.valueAxis.valueMin = 0
        chart.valueAxis.valueMax = max(values) * 1.1 if values else 10
        chart.valueAxis.valueStep = max(1, max(values) // 5) if values else 1
        chart.categoryAxis.labels.fontName = 'Helvetica'
        chart.categoryAxis.labels.fontSize = 9
        chart.valueAxis.labels.fontName = 'Helvetica'
        chart.valueAxis.labels.fontSize = 9

        chart.barWidth = 15
        chart.barSpacing = 3

        drawing.add(chart)
        elements.append(drawing)
        elements.append(Spacer(1, 20))

        return elements

    def create_modern_manufacturer_chart(self, manufacturer_counts):
        """Create modern manufacturer distribution chart"""
        elements = []

        chart_header = Paragraph(
            "<b>Equipment Distribution by Manufacturer</b>",
            self.styles['InfoText']
        )
        elements.append(chart_header)
        elements.append(Spacer(1, 10))

        # Limit to top 8 manufacturers for clarity
        top_manufacturers = dict(manufacturer_counts.most_common(8))

        drawing = Drawing(500, 250)
        pie = Pie()
        pie.x = 120
        pie.y = 50
        pie.width = 150
        pie.height = 150

        # Data and modern color palette
        manufacturers = list(top_manufacturers.keys())
        values = list(top_manufacturers.values())

        colors = [
            self.COLOR_PALETTE['chart_1'],
            self.COLOR_PALETTE['chart_2'],
            self.COLOR_PALETTE['chart_3'],
            self.COLOR_PALETTE['chart_4'],
            self.COLOR_PALETTE['chart_5'],
            self.COLOR_PALETTE['chart_6'],
            self.COLOR_PALETTE['chart_7'],
            self.COLOR_PALETTE['chart_8'],
        ]

        pie.data = values
        pie.labels = [f'{v}' for v in values]
        pie.slices.strokeWidth = 2
        pie.slices.strokeColor = white
        pie.slices.fontName = 'Helvetica-Bold'
        pie.slices.fontSize = 10

        # Donut style
        pie.innerRadiusFraction = 0.5

        # Assign colors
        for i, color in enumerate(colors[:len(values)]):
            pie.slices[i].fillColor = color
            pie.slices[i].labelRadius = 1.2

        # Modern legend
        legend = Legend()
        legend.x = 320
        legend.y = 150
        legend.dx = 10
        legend.dy = 10
        legend.fontName = 'Helvetica'
        legend.fontSize = 10
        legend.boxAnchor = 'w'
        legend.columnMaximum = 10
        legend.strokeWidth = 0
        legend.deltay = 12

        # Truncate long manufacturer names for legend
        total = sum(values)
        legend.colorNamePairs = [
            (colors[i], f'{mfr[:20]}: {count} ({count/total*100:.1f}%)')
            for i, (mfr, count) in enumerate(top_manufacturers.items())
        ]

        drawing.add(pie)
        drawing.add(legend)
        elements.append(drawing)
        elements.append(Spacer(1, 20))

        return elements

    def create_notes_section(self):
        """Create notes and remarks section with modern insights"""
        elements = []

        # Start on new page
        elements.append(PageBreak())

        header = Paragraph("INSIGHTS & RECOMMENDATIONS", self.styles['SectionHeader'])
        elements.append(header)

        # Add any custom notes from context
        notes = self.context.get('notes', '')
        if notes:
            notes_para = Paragraph(notes, self.styles['Normal'])
            elements.append(notes_para)
        else:
            # Generate intelligent insights
            equipments = list(self.equipment_queryset.select_related(
                'description', 'manufacturer', 'department'
            ))
            total = len(equipments)

            if total > 0:
                insights = self._generate_insights(equipments, total)
                insights_text = "<br/><br/>".join(insights)
            else:
                insights_text = "• No equipment found in the current selection criteria."

            notes_para = Paragraph(insights_text, self.styles['Normal'])
            elements.append(notes_para)

        elements.append(Spacer(1, 25))
        return elements

    def _generate_insights(self, equipments, total):
        """Generate intelligent insights from equipment data"""
        insights = []

        # Status analysis
        not_working = sum(1 for eq in equipments if eq.status == 'Not working')
        under_repair = sum(1 for eq in equipments if eq.status == 'Under repair')
        working = sum(1 for eq in equipments if eq.status == 'Working')

        if not_working > 0:
            percentage = (not_working / total) * 100
            severity = "Critical" if percentage > 20 else "Moderate" if percentage > 10 else "Minor"
            insights.append(
                f"<b>Status Alert ({severity}):</b> {not_working} equipment items "
                f"({percentage:.1f}%) are currently not working and require immediate attention."
            )

        if under_repair > 0:
            percentage = (under_repair / total) * 100
            insights.append(
                f"<b>Maintenance In Progress:</b> {under_repair} equipment items "
                f"({percentage:.1f}%) are currently under repair. Monitor completion timelines."
            )

        if working == total:
            insights.append(
                "<b>Excellent Status:</b> All equipment items are currently in working condition. "
                "Continue regular maintenance to preserve this status."
            )
        elif working / total >= 0.95:
            percentage = (working / total) * 100
            insights.append(
                f"<b>Outstanding Performance:</b> {percentage:.1f}% of equipment is operational. "
                "Maintain current maintenance protocols."
            )
        elif working / total >= 0.85:
            percentage = (working / total) * 100
            insights.append(
                f"<b>Good Performance:</b> {percentage:.1f}% operational rate. "
                "Review non-working items for optimization opportunities."
            )
        else:
            percentage = (working / total) * 100
            insights.append(
                f"<b>Performance Review Needed:</b> Only {percentage:.1f}% operational. "
                "Immediate review of maintenance procedures recommended."
            )

        # Manufacturer data completeness
        no_manufacturer = sum(1 for eq in equipments if not eq.manufacturer)
        if no_manufacturer > 0:
            percentage = (no_manufacturer / total) * 100
            if percentage > 20:
                insights.append(
                    f"<b>Data Quality Issue:</b> {no_manufacturer} items ({percentage:.1f}%) "
                    "lack manufacturer information. This impacts warranty tracking and part sourcing."
                )

        # Age analysis for preventive maintenance
        old_equipment = [
            eq for eq in equipments
            if eq.created_at and (datetime.now().date() - eq.created_at.date()).days > 1095
        ]
        if old_equipment:
            insights.append(
                f"<b>Preventive Maintenance Review:</b> {len(old_equipment)} equipment items "
                "are 3+ years old. Schedule comprehensive maintenance assessment."
            )

        # Department distribution analysis
        dept_counts = Counter(eq.department.name for eq in equipments)
        if len(dept_counts) > 1:
            max_dept = max(dept_counts.items(), key=lambda x: x[1])
            min_dept = min(dept_counts.items(), key=lambda x: x[1])
            if max_dept[1] > min_dept[1] * 3:
                insights.append(
                    f"<b>Distribution Imbalance:</b> {max_dept[0]} has significantly more equipment "
                    f"({max_dept[1]} items) compared to {min_dept[0]} ({min_dept[1]} items). "
                    "Review resource allocation if needed."
                )

        # Manufacturer diversity
        manufacturer_counts = Counter(
            eq.manufacturer.name if eq.manufacturer else None
            for eq in equipments
        )
        manufacturer_counts.pop(None, None)  # Remove None entries

        if len(manufacturer_counts) == 1:
            insights.append(
                "<b>Single Manufacturer Risk:</b> All equipment from one manufacturer. "
                "Consider diversifying suppliers to reduce dependency risk."
            )
        elif len(manufacturer_counts) >= 5:
            insights.append(
                f"<b>Diverse Supply Chain:</b> Equipment sourced from {len(manufacturer_counts)} "
                "manufacturers, providing good supply chain resilience."
            )

        if not insights:
            insights = [
                "<b>No Critical Issues Identified:</b> Current equipment inventory shows no major concerns."
            ]

        return insights

    def create_performance_metrics_section(self):
        """Create modern performance metrics dashboard"""
        elements = []
        total_equipment = self.equipment_queryset.count()

        if total_equipment > 1000:
            # Start on new page
            elements.append(PageBreak())

            header = Paragraph("PERFORMANCE METRICS DASHBOARD", self.styles['SectionHeader'])
            elements.append(header)

            # Calculate KPIs
            equipments = list(self.equipment_queryset.select_related(
                'description', 'department', 'manufacturer'
            ))

            # Operational efficiency
            working_count = sum(1 for eq in equipments if eq.status == 'Working')
            operational_efficiency = (working_count / total_equipment * 100) if total_equipment > 0 else 0

            # Data completeness
            manufacturer_count = sum(1 for eq in equipments if eq.manufacturer)
            manufacturer_rate = (manufacturer_count / total_equipment * 100) if total_equipment > 0 else 0

            # Department distribution balance
            dept_counts = Counter(eq.department.name for eq in equipments)
            dept_values = list(dept_counts.values())
            avg_per_dept = sum(dept_values) / len(dept_values) if dept_values else 0

            # Calculate coefficient of variation for balance
            if avg_per_dept > 0:
                std_dev = (sum((v - avg_per_dept) ** 2 for v in dept_values) / len(dept_values)) ** 0.5
                cv = (std_dev / avg_per_dept) * 100
                dept_balance = max(0, min(100, 100 - cv))
            else:
                dept_balance = 0

            # Maintenance load
            under_repair = sum(1 for eq in equipments if eq.status == 'Under repair')
            maintenance_load = (under_repair / total_equipment * 100) if total_equipment > 0 else 0

            metrics_data = [
                ['Performance Metric', 'Value', 'Rating', 'Trend']
            ]

            # Add metrics with modern status indicators
            metrics_data.extend([
                ['Operational Efficiency',
                 f'{operational_efficiency:.1f}%',
                 '★★★★★' if operational_efficiency >= 95 else '★★★★☆' if operational_efficiency >= 85 else '★★★☆☆' if operational_efficiency >= 75 else '★★☆☆☆',
                 '↑ Excellent' if operational_efficiency >= 90 else '→ Good' if operational_efficiency >= 80 else '↓ Needs Attention'],

                ['Data Completeness',
                 f'{manufacturer_rate:.1f}%',
                 '★★★★★' if manufacturer_rate >= 95 else '★★★★☆' if manufacturer_rate >= 85 else '★★★☆☆' if manufacturer_rate >= 75 else '★★☆☆☆',
                 'Complete' if manufacturer_rate >= 95 else 'Good' if manufacturer_rate >= 85 else 'Incomplete'],

                ['Distribution Balance',
                 f'{dept_balance:.1f}%',
                 '★★★★★' if dept_balance >= 80 else '★★★★☆' if dept_balance >= 65 else '★★★☆☆' if dept_balance >= 50 else '★★☆☆☆',
                 'Balanced' if dept_balance >= 70 else 'Moderate' if dept_balance >= 50 else 'Unbalanced'],

                ['Maintenance Load',
                 f'{maintenance_load:.1f}%',
                 '★★★★★' if maintenance_load <= 5 else '★★★★☆' if maintenance_load <= 10 else '★★★☆☆' if maintenance_load <= 15 else '★★☆☆☆',
                 'Low' if maintenance_load <= 5 else 'Moderate' if maintenance_load <= 10 else 'High'],

                ['Total Assets',
                 f'{total_equipment:,}',
                 'Large Dataset',
                 f'{len(dept_counts)} Departments']
            ])

            metrics_table = Table(metrics_data, colWidths=[2.5*inch, 1.5*inch, 1.5*inch, 1.7*inch])
            metrics_table.setStyle(TableStyle([
                # Header styling - bold and readable
                ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_PALETTE['table_header']),
                ('TEXTCOLOR', (0, 0), (-1, 0), self.COLOR_PALETTE['primary']),
                ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),  # Bold for headers
                ('FONTSIZE', (0, 0), (-1, 0), 10),  # Good readable size
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('BOTTOMPADDING', (0, 0), (-1, 0), 7),
                ('TOPPADDING', (0, 0), (-1, 0), 7),

                # Data styling
                ('FONTSIZE', (0, 1), (-1, -1), 9),
                ('GRID', (0, 0), (-1, -1), 0.15, self.COLOR_PALETTE['border']),  # Thinner border
                ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, HexColor('#F9FAFB')]),
                ('ALIGN', (0, 1), (0, -1), 'LEFT'),

                # Color coding for metrics
                ('TEXTCOLOR', (2, 1), (2, 1),
                 self.COLOR_PALETTE['success'] if operational_efficiency >= 85 else self.COLOR_PALETTE['warning']),
                ('TEXTCOLOR', (2, 2), (2, 2),
                 self.COLOR_PALETTE['success'] if manufacturer_rate >= 85 else self.COLOR_PALETTE['danger']),
                ('TEXTCOLOR', (2, 3), (2, 3),
                 self.COLOR_PALETTE['success'] if dept_balance >= 70 else self.COLOR_PALETTE['warning']),
                ('TEXTCOLOR', (2, 4), (2, 4),
                 self.COLOR_PALETTE['success'] if maintenance_load <= 10 else self.COLOR_PALETTE['danger']),

                # Modern padding
                ('LEFTPADDING', (0, 0), (-1, -1), 5),
                ('RIGHTPADDING', (0, 0), (-1, -1), 5),
                ('TOPPADDING', (0, 0), (-1, -1), 5),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ]))

            elements.append(metrics_table)
            elements.append(Spacer(1, 25))

        return elements

    def generate_pdf(self):
        """Generate the complete modern PDF report"""
        buffer = io.BytesIO()

        # Create document with appropriate margins for header/footer
        doc = SimpleDocTemplate(
            buffer,
            pagesize=self.pagesize,
            rightMargin=35,
            leftMargin=35,
            topMargin=3*cm + 10,  # Space for header
            bottomMargin=1.5*cm + 10  # Space for footer
        )

        elements = []
        total_equipment = self.equipment_queryset.count()

        # Add title section
        elements.extend(self.create_title_section())

        # Add performance metrics for large datasets
        if total_equipment > 1000:
            elements.extend(self.create_performance_metrics_section())

        # Add content based on report type
        if self.report_type == 'detailed':
            elements.extend(self.create_overview_section())
            elements.extend(self.create_equipment_description_analysis())
            elements.extend(self.create_detailed_equipment_table_paginated())

        elif self.report_type == 'summary':
            elements.extend(self.create_overview_section())
            elements.extend(self.create_equipment_description_analysis())
            elements.extend(self.create_summary_table())
            if total_equipment <= 2000:
                elements.extend(self.create_charts_section())

        elif self.report_type == 'department':
            elements.extend(self.create_overview_section())
            elements.extend(self.create_equipment_description_analysis())
            elements.extend(self.create_department_analysis())
            if total_equipment <= 2000:
                elements.extend(self.create_charts_section())

        elif self.report_type == 'status':
            elements.extend(self.create_overview_section())
            elements.extend(self.create_equipment_description_analysis())
            elements.extend(self.create_summary_table())
            elements.extend(self.create_department_analysis())
            if total_equipment <= 2000:
                elements.extend(self.create_charts_section())

        # Add insights section
        elements.extend(self.create_notes_section())

        # Build PDF with header/footer
        try:
            if total_equipment > 2000:
                logger.info(f"Generating modern PDF for large dataset: {total_equipment} items")

            from functools import partial
            wm_canvas = partial(_LogoWatermarkCanvas, logo_path=self.logo_path)
            doc.build(elements, onFirstPage=self.create_header_footer,
                     onLaterPages=self.create_header_footer,
                     canvasmaker=wm_canvas)
            buffer.seek(0)

            if total_equipment > 2000:
                logger.info(f"Successfully generated modern PDF for {total_equipment} items")

            return buffer
        except Exception as e:
            logger.error(f"Error generating PDF for {total_equipment} items: {str(e)}")
            raise


# Utility functions

def generate_equipment_pdf_report(workshop, equipment_queryset, report_type='detailed', context=None):
    """
    Generate modern equipment PDF reports with enhanced design

    Args:
        workshop: Workshop model instance
        equipment_queryset: Django QuerySet of Equipment objects
        report_type: Type of report ('detailed', 'summary', 'department', 'status')
        context: Additional context data for the report

    Returns:
        BytesIO buffer containing the PDF
    """
    if context is None:
        context = {}

    # Add default context
    context.setdefault('generated_by', 'System Administrator')

    # Log large dataset generation
    count = equipment_queryset.count()
    if count > 2000:
        logger.info(f"Starting modern PDF generation for large dataset: {count} items")

    generator = EquipmentPDFGenerator(workshop, equipment_queryset, report_type, context)
    return generator.generate_pdf()


def get_pdf_filename(workshop, report_type, department=None, status_filter=None, equipment_count=None):
    """
    Generate appropriate filename for the PDF report

    Args:
        workshop: Workshop model instance
        report_type: Type of report
        department: Optional department filter
        status_filter: Optional status filter
        equipment_count: Optional equipment count for filename

    Returns:
        String filename for the PDF
    """
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # Safely get workshop name
    try:
        workshop_name = str(workshop.name).lower().replace(' ', '_')
    except (AttributeError, TypeError):
        workshop_name = 'unknown'

    base_name = f"equipment_{report_type}_{workshop_name}"

    if department:
        try:
            dept_name = str(department.name).lower().replace(' ', '_')
            base_name += f"_{dept_name}"
        except (AttributeError, TypeError):
            pass

    if status_filter:
        try:
            status_name = str(status_filter).lower().replace(' ', '_')
            base_name += f"_{status_name}"
        except (AttributeError, TypeError):
            pass

    if equipment_count and equipment_count > 1000:
        base_name += f"_{equipment_count}items"

    return f"{base_name}_{timestamp}.pdf"


def create_equipment_pdf_response(workshop, equipment_queryset, report_type='detailed',
                                 context=None, request=None):
    """
    Create Django HTTP response with modern PDF content

    Usage in views:
        return create_equipment_pdf_response(workshop, equipments, 'summary', context, request)
    """
    from django.http import HttpResponse

    if context is None:
        context = {}

    # Add user information if available
    if request and request.user.is_authenticated:
        user_name = f"{request.user.first_name} {request.user.last_name}".strip() or request.user.username
        context['generated_by'] = user_name

    equipment_count = equipment_queryset.count()
    pdf_buffer = None

    try:
        # Generate modern PDF
        pdf_buffer = generate_equipment_pdf_report(workshop, equipment_queryset, report_type, context)

        # Create response
        response = HttpResponse(pdf_buffer.getvalue(), content_type='application/pdf')

        # Generate filename
        filename = get_pdf_filename(
            workshop,
            report_type,
            context.get('department'),
            context.get('status_filter'),
            equipment_count
        )

        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        # Add headers for large files
        if equipment_count > 2000:
            response['Cache-Control'] = 'no-cache, must-revalidate'
            response['Expires'] = '0'

        return response

    except Exception as e:
        logger.error(f"Error creating modern PDF response for {equipment_count} items: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

        # Return error response
        from django.http import JsonResponse
        return JsonResponse({
            'error': 'Failed to generate PDF report',
            'details': str(e),
            'equipment_count': equipment_count
        }, status=500)
    finally:
        if pdf_buffer is not None:
            try:
                pdf_buffer.close()
            except Exception:
                pass
