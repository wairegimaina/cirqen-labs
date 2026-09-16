# pdf_generators.py
"""
Modern PDF Generators for Tools and Accessories Reports
Matches PPM Schedule styling with professional header/footer and watermark
"""
import io
import os
import logging
from datetime import datetime
from django.conf import settings
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, cm
from reportlab.lib.colors import HexColor, white, grey
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.pdfgen import canvas as rl_canvas
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


class ModernPDFReportBase:
    """
    Base class for modern PDF reports with consistent styling
    Matches PPM schedule design
    """

    COLOR_PALETTE = {
        'primary': HexColor('#0F172A'),
        'secondary': HexColor('#1E40AF'),
        'border': HexColor('#CBD5E1'),
        'light_bg': HexColor('#F8FAFC'),
        'text_primary': HexColor('#0F172A'),
        'text_secondary': HexColor('#64748B'),
        'header_bg': HexColor('#1E40AF'),
        'table_header': HexColor('#BFDBFE'),
        'alt_row': HexColor('#F9FAFB'),
    }

    def __init__(self, title, subtitle=""):
        self.title = title
        self.subtitle = subtitle
        self.styles = getSampleStyleSheet()
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
        """Setup custom paragraph styles"""
        self.styles.add(ParagraphStyle(
            name='TitleStyle',
            fontSize=14,
            textColor=self.COLOR_PALETTE['primary'],
            fontName='Helvetica-Bold',
            leading=16
        ))

        self.styles.add(ParagraphStyle(
            name='NormalText',
            fontSize=9,
            textColor=self.COLOR_PALETTE['text_primary'],
            fontName='Helvetica',
            leading=12
        ))

        self.styles.add(ParagraphStyle(
            name='BoldText',
            fontSize=9,
            textColor=self.COLOR_PALETTE['text_primary'],
            fontName='Helvetica-Bold',
            leading=12
        ))

        self.styles.add(ParagraphStyle(
            name='HeaderText',
            fontSize=16,
            textColor=self.COLOR_PALETTE['primary'],
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
            leading=20
        ))

        self.styles.add(ParagraphStyle(
            name='SectionHeader',
            fontSize=12,
            textColor=self.COLOR_PALETTE['secondary'],
            fontName='Helvetica-Bold',
            alignment=TA_LEFT,
            leading=14,
            spaceAfter=10
        ))

        self.styles.add(ParagraphStyle(
            name='TableContent',
            fontSize=9,
            textColor=self.COLOR_PALETTE['text_primary'],
            fontName='Helvetica',
            leading=11
        ))

        self.styles.add(ParagraphStyle(
            name='TableHeader',
            fontSize=10,
            textColor=self.COLOR_PALETTE['primary'],
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
            leading=12
        ))

    def create_header_footer(self, canvas, doc):
        """Create header and footer on each page. Watermark is handled by _LogoWatermarkCanvas."""
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
            self.title.upper()
        )

        # Department name
        canvas.setFont("Helvetica", 10)
        canvas.setFillColor(self.COLOR_PALETTE['text_primary'])
        canvas.drawCentredString(
            width / 2,
            height - 1.7*cm,
            "Biomedical Engineering Department"
        )

        # Subtitle
        if self.subtitle:
            canvas.setFont("Helvetica-Oblique", 9)
            canvas.setFillColor(self.COLOR_PALETTE['text_secondary'])
            canvas.drawCentredString(
                width / 2,
                height - 2.1*cm,
                self.subtitle
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

    def create_title_section(self):
        """Create title section with report info"""
        elements = []

        # Report title
        title_para = Paragraph(f"<b>{self.title}</b>", self.styles['TitleStyle'])
        elements.append(title_para)
        elements.append(Spacer(1, 10))

        # Separator line
        line_table = Table([['']], colWidths=[7*inch], rowHeights=[2])
        line_table.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 2, self.COLOR_PALETTE['primary']),
        ]))
        elements.append(line_table)
        elements.append(Spacer(1, 15))

        return elements

    def create_summary_section(self, total_count, summary_data=None):
        """Create summary statistics section"""
        elements = []

        # Summary header
        header_para = Paragraph("<b>Report Summary</b>", self.styles['SectionHeader'])
        elements.append(header_para)
        elements.append(Spacer(1, 8))

        # Default summary data
        if summary_data is None:
            summary_data = [
                ['Total Items', str(total_count)],
            ]

        summary_table = Table(summary_data, colWidths=[3*inch, 2*inch])

        table_style = [
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.25, self.COLOR_PALETTE['border']),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]

        summary_table.setStyle(TableStyle(table_style))
        elements.append(summary_table)
        elements.append(Spacer(1, 25))

        return elements


class ToolsPDFGenerator(ModernPDFReportBase):
    """Modern PDF generator for Tools Inventory Report"""

    def __init__(self, tools_queryset, workshop_name=None):
        title = "Tools Inventory Report"
        subtitle = f"Workshop: {workshop_name}" if workshop_name else "All Workshops"
        super().__init__(title, subtitle)
        self.tools = tools_queryset

    def create_tools_table(self):
        """Create main tools table with section header"""
        elements = []

        # Add section header
        section_header = Paragraph("<b>Tools List</b>", self.styles['SectionHeader'])
        elements.append(section_header)
        elements.append(Spacer(1, 10))

        # Table headers
        table_data = [[
            'No.',
            'Tool Name',
            'Model',
            'Manufacturer',
            'Serial Number',
            'Workshop',
            'Date Added'
        ]]

        # Add tool rows
        for idx, tool in enumerate(self.tools, 1):
            tool_name = tool.name.name if tool.name else '-'
            manufacturer = tool.manufacturer.name if tool.manufacturer else '-'
            workshop = tool.workshop.name if tool.workshop else 'N/A'

            table_data.append([
                str(idx),
                tool_name[:30],  # Truncate long names
                tool.model[:20] if tool.model else '-',
                manufacturer[:20],
                tool.serial_number[:15] if tool.serial_number else '-',
                workshop[:20],
                tool.created_at.strftime("%b %d, %Y")
            ])

        # Create table with appropriate column widths
        tools_table = Table(
            table_data,
            colWidths=[0.5*inch, 1.8*inch, 1.2*inch, 1.2*inch, 1.0*inch, 1.2*inch, 1.0*inch],
            repeatRows=1  # Repeat header on each page
        )

        # Apply styles
        table_style = [
            # Header row
            ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_PALETTE['table_header']),
            ('TEXTCOLOR', (0, 0), (-1, 0), self.COLOR_PALETTE['primary']),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),

            # Data rows
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ALIGN', (0, 1), (0, -1), 'CENTER'),  # No. column
            ('ALIGN', (1, 1), (-1, -1), 'LEFT'),
            ('ALIGN', (6, 1), (6, -1), 'CENTER'),  # Date column
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),

            # Grid
            ('GRID', (0, 0), (-1, -1), 0.25, self.COLOR_PALETTE['border']),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]

        # Add alternating row colors
        for i in range(1, len(table_data)):
            if i % 2 == 0:
                table_style.append(('BACKGROUND', (0, i), (-1, i), self.COLOR_PALETTE['alt_row']))

        tools_table.setStyle(TableStyle(table_style))
        elements.append(tools_table)

        return elements

    def generate_pdf(self):
        """Generate complete PDF with header/footer"""
        buffer = io.BytesIO()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=35,
            leftMargin=35,
            topMargin=3*cm + 10,  # Space for header
            bottomMargin=1.5*cm + 10  # Space for footer
        )

        story = []

        # Build document
        story.extend(self.create_title_section())
        story.extend(self.create_summary_section(self.tools.count()))
        story.extend(self.create_tools_table())

        # Build with header/footer callback and watermark overlay canvas
        from functools import partial
        wm_canvas = partial(_LogoWatermarkCanvas, logo_path=self.logo_path)
        doc.build(story, onFirstPage=self.create_header_footer, onLaterPages=self.create_header_footer,
                  canvasmaker=wm_canvas)
        buffer.seek(0)
        return buffer


class AccessoriesPDFReport(ModernPDFReportBase):
    """Modern PDF generator for Accessories Inventory Report"""

    def __init__(self, accessories_queryset, workshop_name=None):
        title = "Accessories Inventory Report"
        subtitle = f"Workshop: {workshop_name}" if workshop_name else "All Workshops"
        super().__init__(title, subtitle)
        self.accessories = accessories_queryset

    def create_accessories_table(self):
        """Create main accessories table with section header"""
        elements = []

        # Add section header
        section_header = Paragraph("<b>Accessories List</b>", self.styles['SectionHeader'])
        elements.append(section_header)
        elements.append(Spacer(1, 10))

        # Table headers
        table_data = [[
            'No.',
            'Accessory Name',
            'Equipment',
            'Manufacturer',
            'Stock',
            'Workshop',
            'Date Added'
        ]]

        # Add accessory rows
        for idx, accessory in enumerate(self.accessories, 1):
            accessory_name = accessory.name.name if accessory.name else '-'
            equipment = accessory.equipment_description.name if accessory.equipment_description else '-'
            manufacturer = accessory.manufacturer.name if accessory.manufacturer else '-'
            workshop = accessory.workshop.name if accessory.workshop else 'N/A'

            table_data.append([
                str(idx),
                accessory_name[:30],  # Truncate long names
                equipment[:25],
                manufacturer[:20],
                str(accessory.stock_count),
                workshop[:20],
                accessory.created_at.strftime("%b %d, %Y")
            ])

        # Create table with appropriate column widths
        accessories_table = Table(
            table_data,
            colWidths=[0.5*inch, 1.8*inch, 1.5*inch, 1.2*inch, 0.6*inch, 1.2*inch, 1.0*inch],
            repeatRows=1  # Repeat header on each page
        )

        # Apply styles
        table_style = [
            # Header row
            ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_PALETTE['table_header']),
            ('TEXTCOLOR', (0, 0), (-1, 0), self.COLOR_PALETTE['primary']),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),

            # Data rows
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ALIGN', (0, 1), (0, -1), 'CENTER'),  # No. column
            ('ALIGN', (1, 1), (-1, -1), 'LEFT'),
            ('ALIGN', (4, 1), (4, -1), 'CENTER'),  # Stock column
            ('ALIGN', (6, 1), (6, -1), 'CENTER'),  # Date column
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),

            # Grid
            ('GRID', (0, 0), (-1, -1), 0.25, self.COLOR_PALETTE['border']),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]

        # Add alternating row colors
        for i in range(1, len(table_data)):
            if i % 2 == 0:
                table_style.append(('BACKGROUND', (0, i), (-1, i), self.COLOR_PALETTE['alt_row']))

        accessories_table.setStyle(TableStyle(table_style))
        elements.append(accessories_table)

        return elements

    def generate_pdf(self):
        """Generate complete PDF with header/footer"""
        buffer = io.BytesIO()

        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=35,
            leftMargin=35,
            topMargin=3*cm + 10,  # Space for header
            bottomMargin=1.5*cm + 10  # Space for footer
        )

        story = []

        # Build document
        story.extend(self.create_title_section())
        story.extend(self.create_summary_section(self.accessories.count()))
        story.extend(self.create_accessories_table())

        # Build with header/footer callback and watermark overlay canvas
        from functools import partial
        wm_canvas = partial(_LogoWatermarkCanvas, logo_path=self.logo_path)
        doc.build(story, onFirstPage=self.create_header_footer, onLaterPages=self.create_header_footer,
                  canvasmaker=wm_canvas)
        buffer.seek(0)
        return buffer
