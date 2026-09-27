"""The monthly maintenance report (PDF) for heads of department."""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.branding import contact_line, logo_path, organisation_name

from .kpis import compute


def _fmt(value, suffix=""):
    return "—" if value is None else f"{value:g}{suffix}" if isinstance(value, float) else f"{value}{suffix}"


def _table(rows, widths):
    table = Table(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def monthly_report_pdf(equipment_qs, month_end):
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small", fontSize=8.5, leading=11)
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Maintenance report {month_end:%B %Y}")
    story = []
    logo = logo_path()
    if logo:
        story.append(Image(logo, width=40 * mm, height=16 * mm, kind="proportional"))
    story += [Paragraph(f"{organisation_name()} — equipment maintenance report", styles["Title"]),
              Paragraph(f"{month_end:%B %Y}. KPIs cover the 12 months to {month_end:%d %B %Y}.", small),
              Paragraph(contact_line(), small), Spacer(0, 6 * mm)]

    k = compute(equipment_qs, months=12, today=month_end)
    story.append(Paragraph("Key figures", styles["Heading2"]))
    story.append(_table([
        ["Measure", "Value", "Meaning"],
        ["Machines", _fmt(k.machines), "Active machines in scope"],
        ["Failures", _fmt(k.failures), "Repair work orders, not declined"],
        ["MTBF", _fmt(k.mtbf_days, " days"), "Machine-days per failure"],
        ["MTTR", _fmt(k.mttr_hours, " h"), "Average recorded repair time"],
        ["Uptime", _fmt(k.uptime_percent, "%"), "From recorded repair hours (upper bound)"],
        ["PPM completion", _fmt(k.ppm_completion_percent, "%"), f"{k.ppm_completed} of {k.ppm_due} due"],
        ["Awaiting approval", _fmt(k.backlog), f"Average {_fmt(k.backlog_avg_days, ' days')}, oldest "
                                               f"{_fmt(k.backlog_oldest_days, ' days')}"],
    ], [40 * mm, 30 * mm, 108 * mm]))

    if k.by_type:
        story += [Spacer(0, 5 * mm), Paragraph("Failures by equipment type", styles["Heading2"])]
        story.append(_table([["Type", "Machines", "Failures", "MTBF (days)"]] + [
            [Paragraph(r["type"] or "—", small), r["machines"], r["failures"], _fmt(r["mtbf_days"])]
            for r in k.by_type], [88 * mm, 28 * mm, 28 * mm, 34 * mm]))

    from machineReports.prediction import predict

    risky = [p for p in predict(equipment_qs.filter(status="Working"), horizon_days=90, today=month_end)
             if p.risk_level != "Low"][:15]
    story += [Spacer(0, 5 * mm), Paragraph("Machines most likely to need repair (next 90 days)", styles["Heading2"])]
    if risky:
        story.append(_table([["Risk", "Machine", "Department", "Why"]] + [
            [f"{p.risk_percent}%", Paragraph(f"{p.equipment.description} · S/N {p.equipment.serial_number}", small),
             Paragraph(str(p.equipment.department), small), Paragraph("; ".join(p.reasons) or "—", small)]
            for p in risky], [16 * mm, 62 * mm, 40 * mm, 60 * mm]))
    else:
        story.append(Paragraph("No machine at medium or high risk.", small))
    doc.build(story)
    return buffer.getvalue()
