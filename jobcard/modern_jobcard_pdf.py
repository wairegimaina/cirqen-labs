"""
Modern PDF Generator for Job Cards
Complete with header/footer, watermark, and proper accessories display
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch, mm, cm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, Image
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.pdfgen import canvas as rl_canvas
from django.http import HttpResponse
from django.conf import settings
from io import BytesIO
import os
from datetime import datetime
import base64
from PIL import Image as PILImage
import logging
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


class ModernJobCardPDFGenerator:
    """
    Compact, professional PDF generator for job cards with header and footer
    """

    COLOR_PALETTE = {
        'primary': colors.HexColor('#0F172A'),
        'secondary': colors.HexColor('#1E40AF'),
        'border': colors.HexColor('#CBD5E1'),
        'light_bg': colors.HexColor('#F8FAFC'),
        'text_primary': colors.HexColor('#0F172A'),
        'text_secondary': colors.HexColor('#64748B'),
        'header_bg': colors.HexColor('#1E40AF'),
    }

    def __init__(self, job_card):
        self.job_card = job_card
        self.width, self.height = A4
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
        """Setup custom styles"""
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
            textColor=colors.white,
            fontName='Helvetica-Bold',
            alignment=TA_CENTER,
            leading=20
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
            "EQUIPMENT MAINTENANCE JOB CARD"
        )

        # Department name
        canvas.setFont("Helvetica", 10)
        canvas.setFillColor(self.COLOR_PALETTE['text_primary'])
        canvas.drawCentredString(
            width / 2,
            height - 1.7*cm,
            "Biomedical Engineering Department"
        )



        # Contact info
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(self.COLOR_PALETTE['text_secondary'])
        canvas.drawCentredString(
            width / 2,
            height - 2.7*cm,
            contact_line("ISO 9001:2015")
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
        canvas.setFillColor(colors.HexColor('#E5E7EB'))
        canvas.setFont("Helvetica-Oblique", 7)
        canvas.drawCentredString(
            width / 2,
            0.3*cm,
            "Confidential - Internal Use Only"
        )

        canvas.restoreState()

    def decode_signature(self, signature_data):
        """Decode base64 signature image"""
        if not signature_data or str(signature_data).strip() in ["", "N/A"]:
            return None

        try:
            if isinstance(signature_data, str) and signature_data.startswith("data:image"):
                signature_data = signature_data.split(",")[1]

            img_bytes = base64.b64decode(signature_data)
            img_buffer = BytesIO(img_bytes)

            pil_img = PILImage.open(img_buffer)
            if pil_img.mode in ("RGBA", "LA"):
                background = PILImage.new("RGB", pil_img.size, (255, 255, 255))
                background.paste(pil_img, mask=pil_img.split()[-1] if pil_img.mode == "RGBA" else None)
                pil_img = background
            elif pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")

            final_buffer = BytesIO()
            pil_img.save(final_buffer, format="PNG")
            final_buffer.seek(0)

            return final_buffer

        except Exception as e:
            logger.error(f"Error decoding signature: {e}")
            return None

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
        """Create compact title with hospital/department info"""
        elements = []

        # Card ID line - Show only first 8 characters
        short_id = str(self.job_card.id)[:8]
        card_id_para = Paragraph(f"<b>Card No:</b> {short_id}", self.styles['NormalText'])
        elements.append(card_id_para)
        elements.append(Spacer(1, 5))

        # Separator line
        line_table = Table([['']], colWidths=[7*inch], rowHeights=[2])
        line_table.setStyle(TableStyle([
            ('LINEABOVE', (0, 0), (-1, 0), 2, self.COLOR_PALETTE['primary']),
        ]))
        elements.append(line_table)
        elements.append(Spacer(1, 10))

        return elements

    def create_main_info_section(self):
        """Side-by-side info tables"""
        elements = []

        # LEFT TABLE
        left_data = [
            ['Workshop:', self.job_card.workshop.name if self.job_card.workshop else 'N/A'],
            ['Department:', self.job_card.department.name if self.job_card.department else 'N/A'],
            ['Date Issued:', self.job_card.date_issued.strftime('%Y-%m-%d') if self.job_card.date_issued else 'N/A'],
            ['Priority Level:', self.job_card.priority_level or 'N/A'],
            ['Status:', self.job_card.status or 'N/A'],
            ['Action Taken:', self.job_card.action_taken or 'N/A'],
        ]

        left_table = Table(left_data, colWidths=[1.2*inch, 2.0*inch])
        left_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))

        # RIGHT TABLE - Equipment Details
        equipment = self.job_card.equipment
        right_data = [
            ['Equipment Details'],
            ['Equipment:', str(equipment.description.name) if equipment and equipment.description else 'N/A'],
            ['Serial Number:', str(equipment.serial_number) if equipment and equipment.serial_number else 'N/A'],
            ['Model:', str(equipment.model) if equipment and equipment.model else 'N/A'],
            ['Manufacturer:', str(equipment.manufacturer.name) if equipment and equipment.manufacturer else 'N/A'],
            ['Equipment Status:', str(equipment.status) if equipment and equipment.status else 'N/A']
        ]

        right_table = Table(right_data, colWidths=[1.5*inch, 2.2*inch])
        right_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), self.COLOR_PALETTE['light_bg']),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 10),
            ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
            ('SPAN', (0, 0), (-1, 0)),
            ('FONTNAME', (0, 1), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 1), (-1, -1), 9),
            ('ALIGN', (0, 1), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))

        # Combine tables
        main_data = [[left_table, right_table]]
        main_table = Table(main_data, colWidths=[3.2*inch, 3.7*inch])
        main_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ]))

        elements.append(main_table)
        elements.append(Spacer(1, 12))

        return elements

    def create_job_details_section(self):
        """Job details section - Time Taken"""
        elements = []

        # Header
        header_table = Table([['TIME TAKEN']], colWidths=[7*inch])
        header_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), self.COLOR_PALETTE['light_bg']),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(header_table)

        # Time taken section - side by side
        time_started = self.job_card.time_started.strftime('%H:%M') if self.job_card.time_started else 'N/A'
        time_completed = self.job_card.time_completed.strftime('%H:%M') if self.job_card.time_completed else 'N/A'

        time_data = [
            ['Time Started:', time_started, 'Time Completed:', time_completed]
        ]

        time_table = Table(time_data, colWidths=[1.2*inch, 2.3*inch, 1.2*inch, 2.3*inch])
        time_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (0, 0), 'Helvetica-Bold'),
            ('FONTNAME', (2, 0), (2, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))

        elements.append(time_table)
        elements.append(Spacer(1, 12))

        return elements
    def create_parts_section(self):
        """Parts/Accessories section with proper fetching and unit costs"""
        elements = []

        # Fetch spare parts with proper relationships
        spare_parts = self.job_card.spare_parts.select_related(
            'part__name',
            'part__manufacturer',
            'part__equipment_description'
        ).all()

        # Header
        header_table = Table([['PARTS']], colWidths=[7*inch])
        header_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), self.COLOR_PALETTE['light_bg']),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(header_table)

        # Parts table with unit cost
        parts_data = [['Part Description', 'Qty', 'Unit Cost (KSh)', 'Total (KSh)', 'Remarks']]

        if not spare_parts.exists():
            parts_data.append(['No spare parts used', '0', '0.00', '0.00', 'N/A'])
        else:
            for spare_part in spare_parts:
                part = spare_part.part

                # Build part description with manufacturer
                part_desc = ''
                if part:
                    if part.name:
                        part_desc = str(part.name.name)
                    else:
                        part_desc = 'N/A'

                    if part.manufacturer:
                        part_desc += f" - {part.manufacturer.name}"
                else:
                    part_desc = 'N/A'

                # Calculate line total
                unit_cost = spare_part.unit_cost or 0
                line_total = unit_cost * spare_part.quantity

                parts_data.append([
                    part_desc,
                    str(spare_part.quantity),
                    f'{unit_cost:,.2f}',
                    f'{line_total:,.2f}',
                    str(spare_part.remarks)[:40] if spare_part.remarks else ''
                ])

        parts_table = Table(parts_data, colWidths=[2.5*inch, 0.5*inch, 1.0*inch, 1.0*inch, 2.0*inch])
        parts_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (0, -1), 'LEFT'),
            ('ALIGN', (1, 0), (1, -1), 'CENTER'),
            ('ALIGN', (2, 0), (3, -1), 'RIGHT'),
            ('ALIGN', (4, 0), (4, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))

        elements.append(parts_table)
        elements.append(Spacer(1, 12))

        return elements

    def create_cost_section(self):
        """Cost breakdown section - Ultra compact"""
        elements = []

        # Header
        header_table = Table([['COST BREAKDOWN']], colWidths=[7*inch])
        header_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), self.COLOR_PALETTE['light_bg']),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(header_table)

        # Get cost values
        labor_cost = self.job_card.labor_cost or 0
        additional_costs = self.job_card.additional_costs or 0
        total_parts_cost = self.job_card.total_parts_cost or 0
        total_cost = self.job_card.get_total_cost()

        # Single row with all costs
        if additional_costs > 0:
            additional_desc = (self.job_card.additional_costs_description or 'Other')[:15]  # Truncate if too long
            cost_data = [[
                'Labor:', f'KSh {labor_cost:,.2f}',
                'Parts:', f'KSh {total_parts_cost:,.2f}',
                f'{additional_desc}:', f'KSh {additional_costs:,.2f}',
                'TOTAL:', f'KSh {total_cost:,.2f}'
            ]]
            col_widths = [0.6*inch, 1.0*inch, 0.6*inch, 1.0*inch, 0.8*inch, 1.0*inch, 0.7*inch, 1.3*inch]
        else:
            cost_data = [[
                'Labor Cost:', f'KSh {labor_cost:,.2f}',
                'Parts Cost:', f'KSh {total_parts_cost:,.2f}',
                'TOTAL COST:', f'KSh {total_cost:,.2f}'
            ]]
            col_widths = [1.0*inch, 1.5*inch, 1.0*inch, 1.5*inch, 1.0*inch, 2.0*inch]

        cost_table = Table(cost_data, colWidths=col_widths)
        cost_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
            # Highlight TOTAL
            ('BACKGROUND', (-2, 0), (-1, 0), self.COLOR_PALETTE['light_bg']),
            ('FONTSIZE', (-2, 0), (-1, 0), 10),
            ('TEXTCOLOR', (-2, 0), (-1, 0), self.COLOR_PALETTE['secondary']),
            ('LEFTPADDING', (0, 0), (-1, -1), 4),
            ('RIGHTPADDING', (0, 0), (-1, -1), 4),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))

        elements.append(cost_table)
        elements.append(Spacer(1, 12))

        return elements
    def create_job_description_text(self):
        """Job description text box"""
        elements = []

        desc_header = Paragraph("<b>Job description</b>", self.styles['BoldText'])
        elements.append(desc_header)
        elements.append(Spacer(1, 5))

        description = self.job_card.job_description or 'No description provided'
        desc_table = Table([[description]], colWidths=[7*inch])
        desc_table.setStyle(TableStyle([
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
            ('LEFTPADDING', (0, 0), (-1, -1), 8),
            ('RIGHTPADDING', (0, 0), (-1, -1), 8),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))

        elements.append(desc_table)
        elements.append(Spacer(1, 12))

        return elements

    def create_decline_section(self):
        """Decline reason if applicable"""
        elements = []

        if self.job_card.status == 'Declined' and self.job_card.decline_reason:
            decline_header_table = Table([['DECLINE REASON']], colWidths=[7*inch])
            decline_header_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FEE2E2')),
                ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
                ('FONTSIZE', (0, 0), (-1, -1), 10),
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#DC2626')),
                ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
                ('TOPPADDING', (0, 0), (-1, -1), 6),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ]))
            elements.append(decline_header_table)

            decline_table = Table([[self.job_card.decline_reason]], colWidths=[7*inch])
            decline_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FEF2F2')),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#DC2626')),
                ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
                ('LEFTPADDING', (0, 0), (-1, -1), 8),
                ('RIGHTPADDING', (0, 0), (-1, -1), 8),
                ('TOPPADDING', (0, 0), (-1, -1), 8),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ]))
            elements.append(decline_table)
            elements.append(Spacer(1, 12))

        return elements

    def create_signatures_section(self):
        """Signatures section"""
        elements = []

        header_table = Table([['SIGNATURES']], colWidths=[7*inch])
        header_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), self.COLOR_PALETTE['light_bg']),
            ('FONTNAME', (0, 0), (-1, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 10),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(header_table)

        # Decode signatures
        tech_sig_img = self.decode_signature(self.job_card.tech_signature)
        nurse_sig_img = self.decode_signature(self.job_card.verified_signature)

        # Signatures table
        sig_data = [['Technician', 'Nurse Signature']]

        # Signature images
        sig_row = []
        if tech_sig_img:
            sig_row.append(Image(tech_sig_img, width=90, height=45))
        else:
            sig_row.append('No signature')

        if nurse_sig_img:
            sig_row.append(Image(nurse_sig_img, width=90, height=45))
        else:
            sig_row.append('Pending approval' if self.job_card.status == 'Waiting Approval' else 'No signature')

        sig_data.append(sig_row)

        # Names
        tech_name = self.job_card.performed_by.get_full_name() if self.job_card.performed_by else 'N/A'
        nurse_name = self.job_card.nurse_name if self.job_card.nurse_name else 'N/A'
        sig_data.append([tech_name, nurse_name])

        # Dates
        tech_date = self.job_card.technician_signed_date.strftime('%Y-%m-%d %H:%M') if self.job_card.technician_signed_date else 'N/A'
        nurse_date = self.job_card.nurse_signed_date.strftime('%Y-%m-%d %H:%M') if self.job_card.nurse_signed_date else 'N/A'
        sig_data.append([f"Date: {tech_date}", f"Date: {nurse_date}"])

        sig_table = Table(sig_data, colWidths=[3.5*inch, 3.5*inch], rowHeights=[25, 60, 20, 20])
        sig_table.setStyle(TableStyle([
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.5, self.COLOR_PALETTE['border']),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ]))

        elements.append(sig_table)

        return elements

    def generate_pdf(self):
        """Generate complete PDF with header/footer"""
        buffer = BytesIO()

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
        story.extend(self.create_main_info_section())
        story.extend(self.create_job_details_section())
        story.extend(self.create_parts_section())
        story.extend(self.create_cost_section())
        story.extend(self.create_job_description_text())
        story.extend(self.create_decline_section())
        story.extend(self.create_signatures_section())

        # Build with header/footer callback and watermark overlay canvas
        from functools import partial
        wm_canvas = partial(_LogoWatermarkCanvas, logo_path=self.logo_path)
        doc.build(story, onFirstPage=self.create_header_footer, onLaterPages=self.create_header_footer,
                  canvasmaker=wm_canvas)
        buffer.seek(0)
        return buffer


# Utility functions

def generate_jobcard_pdf(job_card):
    """
    Generate modern job card PDF with header/footer

    Args:
        job_card: JobCard model instance

    Returns:
        BytesIO buffer containing the PDF
    """
    generator = ModernJobCardPDFGenerator(job_card)
    return generator.generate_pdf()


def create_jobcard_pdf_response(job_card):
    """
    Create Django HTTP response with PDF content

    Args:
        job_card: JobCard model instance

    Returns:
        HttpResponse with PDF content
    """
    try:
        pdf_buffer = generate_jobcard_pdf(job_card)

        response = HttpResponse(pdf_buffer.getvalue(), content_type='application/pdf')
        filename = f"jobcard_{job_card.id}_{job_card.date_issued.strftime('%Y%m%d')}.pdf"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'

        return response

    except Exception as e:
        logger.error(f"Error generating PDF for job card {job_card.id}: {str(e)}", exc_info=True)
        raise
