"""Printable QR labels, one per machine (A4, 3 x 8 labels of 70 x 37 mm).

Scanning a label opens the machine's page (assets:machine): its history,
contracts and a button to start a work order on it.
"""
import io

import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from core.branding import organisation_name

COLS, ROWS = 3, 8
LABEL_W, LABEL_H = 70 * mm, 37 * mm
MARGIN_X = (A4[0] - COLS * LABEL_W) / 2
MARGIN_Y = (A4[1] - ROWS * LABEL_H) / 2


def _qr(url):
    image = qrcode.make(url, box_size=8, border=1)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


def _fit(text, limit):
    text = str(text or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def labels_pdf(machines, url_for):
    """``machines``: Equipment rows; ``url_for(machine)``: the absolute URL to encode."""
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle("Equipment QR labels")
    site = _fit(organisation_name(), 34)
    for index, machine in enumerate(machines):
        slot = index % (COLS * ROWS)
        if index and slot == 0:
            pdf.showPage()
        col, row = slot % COLS, slot // COLS
        x = MARGIN_X + col * LABEL_W
        y = A4[1] - MARGIN_Y - (row + 1) * LABEL_H
        pdf.setLineWidth(0.3)
        pdf.setStrokeGray(0.8)
        pdf.rect(x + 1 * mm, y + 1 * mm, LABEL_W - 2 * mm, LABEL_H - 2 * mm)
        size = LABEL_H - 6 * mm
        pdf.drawImage(_qr(url_for(machine)), x + 3 * mm, y + 3 * mm, size, size)
        tx = x + size + 6 * mm
        pdf.setFillGray(0)
        pdf.setFont("Helvetica-Bold", 8)
        pdf.drawString(tx, y + LABEL_H - 8 * mm, _fit(machine.description, 22))
        pdf.setFont("Helvetica", 7)
        pdf.drawString(tx, y + LABEL_H - 13 * mm, f"S/N {_fit(machine.serial_number, 18)}")
        pdf.drawString(tx, y + LABEL_H - 17 * mm, _fit(machine.model, 22))
        pdf.drawString(tx, y + LABEL_H - 21 * mm, _fit(machine.department, 22))
        pdf.setFont("Helvetica-Oblique", 6)
        pdf.drawString(tx, y + 5 * mm, site)
        pdf.drawString(tx, y + 2.5 * mm, "Scan for history or a work order")
    pdf.save()
    return buffer.getvalue()
