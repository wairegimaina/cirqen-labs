#!/usr/bin/env python3
"""Render the five-minute Cirqen platform tour: every module, narrated.

A longer companion to build_marketing_video.py, sharing its drawing kit, voice
and music. It walks through dashboards, inventory, work orders and corrective
maintenance, PPM, calibration schedules, procedures, the verdict, certificates,
reference standards, drift predictions, parts and tools, the performance
overview, reports, security and offline working. Every claim in the script was
checked against the code; figures on screen are marked as examples.

    python helper_scripts/build_overview_video.py              # full video
    python helper_scripts/build_overview_video.py --still 12   # PNG of scene 12

Output: data/Cirqen_Platform_Tour.mp4. Same requirements as the marketing video.
"""
import argparse
import hashlib
import math
import pickle
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_marketing_video as mk  # noqa: E402
from build_marketing_video import (  # noqa: E402
    AMBER, BG, BG_SOFT, CARD, CARD_HI, CORAL, GREEN, GREYDOC, INKDOC, LILAC, LINE, LOGO_ICON, MUTED, PAPER, SKY,
    SR, TEAL, WHITE, Canvas, appear, badge, check, clamp, computer, cross, ease, headline, icon, lerp, logo,
    mix, person, pill, qr, signature, wide_logo,
)

ROOT = mk.ROOT
BUILD = ROOT / "data" / "tour_build"
OUT = ROOT / "data" / "Cirqen_Platform_Tour.mp4"
FPS = mk.FPS
W, H, S = mk.W, mk.H, mk.S
SUB = (232, 236, 233)
DOCGREY = (104, 118, 122)

N = "Sirken"   # how the brand is spoken

NARRATION = {
    "intro": [
        ("Cirqen Labs. Engineering healthcare.", f"{N} Labs. Engineering healthcare."),
        ("A complete tour of Cirqen, module by module.", f"A complete tour of {N}, module by module."),
    ],
    "modules": [
        ("Cirqen brings every part of the department into one place.", f"{N} brings every part of the department into one place."),
        ("Dashboards, inventory, work orders, preventive maintenance, calibration and its schedules, reference standards, parts and tools, reports, machine performance, users and the audit trail.", None),
        ("Because they share one record, a work order knows its machine, its parts, its costs and its schedule.", None),
    ],
    "dashboard": [
        ("Each person starts on a dashboard built for their role.", None),
        ("At a glance: which machines are working, under repair or down; which work orders are waiting, approved or declined; what maintenance is done, upcoming or overdue; and which devices are certified.", None),
        ("A global search finds any machine or work order in seconds.", None),
    ],
    "inventory": [
        ("The inventory holds every machine, in every department and workshop.", None),
        ("Each one has its equipment type, manufacturer, model, serial number and category, and critical equipment is flagged.", None),
        ("Its status is always clear: working, under repair, or not working. Existing registers can be imported straight from Excel.", None),
    ],
    "workorder": [
        ("Every job is recorded as a work order.", None),
        ("The technician picks the department and the machine, sets the priority from low to urgent, and chooses the type of work: repair, preventive maintenance, calibration, or other.", None),
        ("They describe the job, record when it started and finished, add the spare parts used from stock, the labour and any extra costs, and their remarks.", None),
    ],
    "corrective": [
        ("Corrective maintenance follows one clear path.", None),
        ("A ventilator in the ICU stops working. An urgent repair work order is opened, and the technician signs it with their saved signature.",
         "A ventilator in the I C U stops working. An urgent repair work order is opened, and the technician signs it with their saved signature."),
        ("The in-charge reviews it, and approves it, or declines it with a reason.", None),
        ("On approval, spare parts come out of stock, costs are totalled, and the repair and its downtime join the machine's history. Without enough stock, approval stops and says why.", None),
    ],
    "ppm": [
        ("Preventive maintenance is planned automatically.", None),
        ("Each machine has a maintenance interval, and schedules can be planned by equipment type or by department.", None),
        ("Every month shows what is pending, what is done, and what was carried forward.", None),
        ("When the maintenance work order is approved, the task completes itself and the next one is created.", None),
    ],
    "calschedule": [
        ("Calibration has its own schedules.", None),
        ("Each device is linked to its procedure, with an interval of six or twelve months.", None),
        ("Devices are grouped by department or by equipment type, so a whole group is calibrated in the same month, due by the end of that month.", None),
        ("A schedule moves from pending, to in progress, to awaiting approval, to completed, then the next cycle opens. Anything missed shows as overdue.", None),
    ],
    "procedure": [
        ("A calibration procedure defines exactly what to measure.", None),
        ("Parameters, sub-parameters such as systolic and diastolic pressure, the set points, their tolerances, and the reference standard.", None),
        ("In the session, the technician records the room conditions, the device's resolution, and several readings at each point. Cirqen suggests the procedures used before on the same type of equipment.",
         f"In the session, the technician records the room conditions, the device's resolution, and several readings at each point. {N} suggests the procedures used before on the same type of equipment."),
    ],
    "verdict": [
        ("Cirqen then does the maths: the mean, the error and the measurement uncertainty at every point.",
         f"{N} then does the maths: the mean, the error and the measurement uncertainty at every point."),
        ("The verdict is strict. If one point is out of tolerance, the device fails.", None),
    ],
    "certificate": [
        ("A reviewer approves the session, or rejects it with a reason, such as data quality, procedure not followed, or environmental conditions.", None),
        ("Once approved, the certificate number is issued centrally. If the workshop is offline, the number follows as soon as it reconnects.", None),
        ("The certificate carries the results, the uncertainty, the reference standards, the drift history, signatures, and a QR code for verification.", None),
    ],
    "standards": [
        ("Traceability starts with the reference standards register.", None),
        ("Every standard has its serial number, its own certificate, calibration and due dates, the calibrating agency, and the uncertainty of each parameter.", None),
    ],
    "predict": [
        ("Cirqen also looks ahead.", f"{N} also looks ahead."),
        ("From each device's calibration history, it measures how fast it is drifting, compared with its tolerance.", None),
        ("Very stable devices can have their interval extended, and drifting ones shortened. If a device will breach tolerance within two years, it is flagged as urgent, with an estimate of the time it has left.", None),
    ],
    "parts": [
        ("Parts and tools are managed per workshop.", None),
        ("Tools are registered with their serial numbers. Spare parts carry a stock count and a unit cost, and are linked to the equipment they fit.", None),
        ("A workshop requests new parts or a restock. The head of department approves it, setting quantity and cost. The workshop confirms receipt, stock goes up, and every step stays in the request history.", None),
    ],
    "performance": [
        ("The performance overview shows how your equipment is really doing.", None),
        ("Machines by category and status, critical equipment, the number of repairs, total downtime, and the cost of labour and parts.", None),
        ("Manufacturers are compared on uptime, repair frequency and average repair cost, rated from excellent to poor, with recommendations, such as which to prefer for future purchases.", None),
        ("Every view exports to Excel or PDF.", None),
    ],
    "reports": [
        ("The report hub builds weekly and monthly reports, with filters you can save, and a report for each workshop for the head of department.", None),
    ],
    "security": [
        ("Access follows your structure: technicians, in-charges and the head of department each see what their role allows.", None),
        ("Repeated failed logins are blocked, every change is kept in one audit timeline, and updates are verified before they install.", None),
    ],
    "offline": [
        ("And every workshop runs Cirqen on its own computer, so work carries on when the network drops, and catches up when it returns.",
         f"And every workshop runs {N} on its own computer, so work carries on when the network drops, and catches up when it returns."),
    ],
    "close": [
        ("Cirqen Labs. Every machine ready, every record signed.", f"{N} Labs. Every machine ready, every record signed."),
        ("Book a demo today.", None),
    ],
}
ORDER = list(NARRATION)
SPEED, LEAD_IN, GAP, TAIL = 1.12, 0.5, 0.3, 0.8


# ---- extra glyphs -----------------------------------------------------------

def glyph(c, kind, xy, s, col, bg=CARD, a=1.0):
    x, y = xy
    if kind == "bars":
        for k, h in enumerate((18, 34, 26, 44)):
            c.rect((x - 34 * s + k * 18 * s, y + 24 * s - h * s, x - 22 * s + k * 18 * s, y + 24 * s), 3 * s, fill=col,
                   a=a, bg=bg)
    elif kind == "doc":
        c.rect((x - 26 * s, y - 34 * s, x + 26 * s, y + 34 * s), 6 * s, fill=mix(bg, col, 0.2), outline=col, w=4 * s,
               a=a, bg=bg)
        for k in range(4):
            c.line([(x - 14 * s, y - 16 * s + k * 12 * s), (x + 14 * s - (k % 2) * 10 * s, y - 16 * s + k * 12 * s)],
                   col, 3 * s, a, bg)
    elif kind == "calendar":
        c.rect((x - 34 * s, y - 28 * s, x + 34 * s, y + 32 * s), 6 * s, fill=mix(bg, col, 0.2), outline=col, w=4 * s,
               a=a, bg=bg)
        c.rect((x - 34 * s, y - 28 * s, x + 34 * s, y - 12 * s), 4 * s, fill=col, a=a, bg=bg)
        for i in range(3):
            for j in range(2):
                c.rect((x - 22 * s + i * 17 * s, y - 2 * s + j * 15 * s, x - 12 * s + i * 17 * s, y + 8 * s + j * 15 * s),
                       2 * s, fill=col, a=a, bg=bg)
    elif kind == "gauge":
        c.arc((x - 34 * s, y - 30 * s, x + 34 * s, y + 38 * s), 180, 360, col, 6 * s, a, bg)
        c.line([(x, y + 4 * s), (x + 20 * s, y - 18 * s)], col, 4 * s, a, bg)
        c.circle((x, y + 4 * s), 6 * s, fill=col, a=a, bg=bg)
    elif kind == "list":
        for k in range(3):
            c.circle((x - 24 * s, y - 20 * s + k * 20 * s), 5 * s, fill=col, a=a, bg=bg)
            c.line([(x - 12 * s, y - 20 * s + k * 20 * s), (x + 28 * s, y - 20 * s + k * 20 * s)], col, 4 * s, a, bg)
    elif kind == "person":
        person(c, (x, y + 4 * s), 0.7 * s, col, a, bg)
    elif kind == "shield":
        c.poly([(x, y - 34 * s), (x + 30 * s, y - 22 * s), (x + 26 * s, y + 10 * s), (x, y + 34 * s),
                (x - 26 * s, y + 10 * s), (x - 30 * s, y - 22 * s)], col, a, bg)
        check(c, (x, y), 26 * s, bg, 5 * s, a, col)
    elif kind == "sync":
        c.arc((x - 30 * s, y - 30 * s, x + 30 * s, y + 30 * s), 200, 340, col, 5 * s, a, bg)
        c.arc((x - 30 * s, y - 30 * s, x + 30 * s, y + 30 * s), 20, 160, col, 5 * s, a, bg)
        c.poly([(x + 30 * s, y - 14 * s), (x + 20 * s, y + 2 * s), (x + 38 * s, y + 2 * s)], col, a, bg)
        c.poly([(x - 30 * s, y + 14 * s), (x - 20 * s, y - 2 * s), (x - 38 * s, y - 2 * s)], col, a, bg)
    else:
        icon(c, kind, xy, s * 0.8, col, a, bg)


def panel(c, xy, a, fill=CARD, r=22, outline=LINE):
    c.rect(xy, r, fill=fill, outline=outline, a=a)


def kpi(c, xy, w, h, label, value, col, a, bg=BG):
    x, y = xy
    c.rect((x, y, x + w, y + h), 18, fill=CARD, outline=LINE, a=a, bg=bg)
    c.rect((x, y + 18, x + 6, y + h - 18), 3, fill=col, a=a, bg=CARD)
    c.text((x + 28, y + h * 0.36), label, 21, MUTED, "SemiBold", "lm", a, CARD)
    c.text((x + 28, y + h * 0.7), value, 40, WHITE, "Bold", "lm", a, CARD)


def ticklist(c, x, y, items, starts, t, gap=54, size=25, bg=BG, col=TEAL):
    for k, (it, st) in enumerate(zip(items, starts)):
        ia = appear(t, st, 0.4)
        c.circle((x, y + k * gap), 15, outline=LINE, w=2, a=1 if t > st - 1.5 else ia, bg=bg)
        if t >= st:
            badge(c, (x, y + k * gap), 15, col, "check", ia, bg)
        c.text((x + 30, y + k * gap), it, size, WHITE if t >= st else MUTED, "Regular", "lm",
               max(ia, 0.55 if t > st - 1.5 else 0), bg)


def example_tag(c, t, x=1800, y=108):
    c.text((x, y), "Example figures", 18, MUTED, "SemiBold", "ra", appear(t, 0.6, 0.6))


def spread(b, i, n, start_off=0.3, end=None, dur=None):
    """Evenly spaced times across sentence i for n items."""
    s0 = b[i] + start_off
    s1 = (b[i + 1] - 0.4) if i + 1 < len(b) else (dur - 1.2 if dur else s0 + n)
    step = (s1 - s0) / max(1, n)
    return [s0 + k * step for k in range(n)]


# ---- scenes -------------------------------------------------------------------

def s_intro(c, t, dur, b):
    a = appear(t, 0.3, 1.2)
    logo(c, wide_logo(), (960, 440 + 20 * (1 - a)), 330, a)
    ta = appear(t, b[1], 0.8)
    c.text((960, 740), "The platform tour", 40, MUTED, "Regular", "mm", ta)


MODULES = [("Dashboards", "bars", TEAL), ("Inventory", "monitor", SKY), ("Work Orders", "doc", AMBER),
           ("Preventive Maintenance", "calendar", GREEN), ("Calibration", "gauge", LILAC),
           ("Calibration Schedules", "calendar", LILAC), ("Reference Standards", "analyser", AMBER),
           ("Parts & Tools", "wrench", CORAL), ("Report Hub", "doc", SKY), ("Machine Performance", "bars", GREEN),
           ("Users & Signatures", "person", TEAL), ("Audit Trail", "list", CORAL)]


def s_modules(c, t, dur, b):
    headline(c, t, "The platform", "Twelve modules, one record")
    cw, ch, gx, gy = 390, 170, 26, 26
    x0 = (W - (4 * cw + 3 * gx)) / 2
    starts = spread(b, 1, 12, 0.2)
    focus = t > b[2]
    linked = ("Inventory", "Parts & Tools", "Preventive Maintenance", "Machine Performance")
    pos = {}
    for i, (name, kind, col) in enumerate(MODULES):
        r, k = divmod(i, 4)
        pos[name] = (x0 + k * (cw + gx) + cw / 2, 240 + r * (ch + gy) + ch / 2)
    if focus:
        # drawn first, so the tiles cover them and only the joins between tiles show
        la = appear(t, b[2] + 0.3, 0.6)
        wo = pos["Work Orders"]
        for other in linked:
            c.line([wo, lerp(wo, pos[other], la)], AMBER, 5, la)
    for i, (name, kind, col) in enumerate(MODULES):
        r, k = divmod(i, 4)
        x, y = x0 + k * (cw + gx), 240 + r * (ch + gy)
        a = appear(t, starts[i], 0.45)
        on = name == "Work Orders" or name in linked
        dim = 0.35 if focus and not on else 1.0
        edge = AMBER if focus and on else LINE
        c.rect((x, y, x + cw, y + ch), 20, fill=CARD, outline=edge, w=3 if focus and on else 2, a=a * dim)
        glyph(c, kind, (x + 58, y + ch / 2), 0.9, col, CARD, a * dim)
        lines = c.wrap(name, 26, cw - 130, "SemiBold")
        for j, ln in enumerate(lines):
            c.text((x + 108, y + ch / 2 + (j - (len(lines) - 1) / 2) * 32), ln, 26, WHITE, "SemiBold", "lm", a * dim, CARD)


def s_dashboard(c, t, dur, b):
    headline(c, t, "Dashboards", "Everything that matters, at a glance")
    example_tag(c, t)
    rows = [
        [("Working", "412", GREEN), ("Under repair", "23", AMBER), ("Not working", "9", CORAL)],
        [("Work orders waiting", "14", AMBER), ("Approved", "286", GREEN), ("Declined", "6", CORAL)],
        [("PPM done", "188", GREEN), ("Upcoming", "41", SKY), ("Overdue", "5", CORAL)],
        [("Certified", "97", LILAC), ("Uncertified", "12", MUTED), ("Standards", "8", AMBER)],
    ]
    starts = spread(b, 1, 4, 0.2)
    for r, row in enumerate(rows):
        for k, (lab, val, col) in enumerate(row):
            a = appear(t, starts[r] + k * 0.15, 0.4)
            kpi(c, (120 + k * 330, 230 + r * 150), 310, 130, lab, val, col, a)
    # role cards
    ra = appear(t, b[0] + 0.3, 0.5)
    c.rect((1150, 230, 1800, 500), 22, fill=CARD, outline=LINE, a=ra)
    c.text((1180, 270), "A dashboard for each role", 26, WHITE, "SemiBold", "lm", ra, CARD)
    for k, (role, col) in enumerate((("Technician", TEAL), ("In-Charge", AMBER), ("Head of Dept", GREEN))):
        person(c, (1250 + k * 200, 380), 0.8, col, ra, CARD)
        c.text((1250 + k * 200, 460), role, 22, MUTED, "SemiBold", "mm", ra, CARD)
    # search
    sa = appear(t, b[2], 0.5)
    c.rect((1150, 540, 1800, 830), 22, fill=CARD, outline=LINE, a=sa)
    c.rect((1180, 570, 1770, 630), 30, fill=BG_SOFT, outline=TEAL, w=2, a=sa, bg=CARD)
    q = "Ventilator ICU"
    typed = q[: int(len(q) * clamp((t - b[2] - 0.3) / 1.0))]
    c.text((1215, 600), typed or "Search", 24, WHITE if typed else MUTED, "Regular", "lm", sa, BG_SOFT)
    for k, (res, kind) in enumerate((("Ventilator · ICU · Bay 2", "Machine"), ("Work order · Ventilator · ICU", "Work order"))):
        ra2 = appear(t, b[2] + 1.4 + k * 0.3, 0.4)
        c.text((1190, 680 + k * 60), res, 23, WHITE, "Regular", "lm", ra2, CARD)
        c.text((1770, 680 + k * 60), kind, 20, MUTED, "SemiBold", "rm", ra2, CARD)


def s_inventory(c, t, dur, b):
    headline(c, t, "Inventory", "Every machine, every department")
    example_tag(c, t)
    a = appear(t, b[0] + 0.3, 0.5)
    x0, y0, x1 = 120, 230, 1800
    c.rect((x0, y0, x1, 760), 22, fill=CARD, outline=LINE, a=a)
    cols = [("Equipment type", 150), ("Manufacturer", 470), ("Model", 710), ("Serial no.", 880),
            ("Department", 1090), ("Category", 1330), ("Status", 1600)]
    for lab, x in cols:
        c.text((x, y0 + 45), lab, 21, MUTED, "SemiBold", "lm", a, CARD)
    rows = [("Ventilator", "Manufacturer A", "V-300", "SN 40318", "ICU", "Critical", "Working", GREEN),
            ("Dialysis machine", "Manufacturer B", "D-5", "SN 22871", "Renal Unit", "Critical", "Under repair", AMBER),
            ("Infusion pump", "Manufacturer C", "P-20", "SN 77102", "Ward 7", "General", "Working", GREEN),
            ("Anaesthesia machine", "Manufacturer A", "A-9", "SN 10554", "Operating Theatre", "Critical", "Working", GREEN),
            ("Incubator", "Manufacturer D", "I-2", "SN 63390", "Maternity", "Critical", "Not working", CORAL),
            ("X-ray unit", "Manufacturer E", "X-1", "SN 90021", "Radiology", "General", "Working", GREEN)]
    starts = spread(b, 1, len(rows), 0.1)
    for r, row in enumerate(rows):
        ra = appear(t, starts[r], 0.4)
        y = y0 + 110 + r * 72
        c.line([(x0 + 20, y - 36), (x1 - 20, y - 36)], LINE, 1, ra, CARD)
        for k, (lab, x) in enumerate(cols[:5]):
            c.text((x, y), row[k], 23, WHITE if k == 0 else (200, 214, 216), "SemiBold" if k == 0 else "Regular", "lm",
                   ra, CARD)
        if row[5] == "Critical":
            pill(c, (1390, y), "Critical", CORAL, ra, 17, bg=CARD)
        else:
            c.text((1330, y), "General", 23, (200, 214, 216), "Regular", "lm", ra, CARD)
        sa = appear(t, b[2] + 0.2 + r * 0.12, 0.4) if t > b[2] else 0
        pill(c, (1660, y), row[6], row[7], max(sa, 0.0) if t > b[2] else ra * 0.35, 18, solid=t > b[2], bg=CARD)
    ea = appear(t, b[2] + 2.8, 0.5)
    if ea > 0:
        c.rect((120, 800, 700, 880), 18, fill=CARD, outline=GREEN, w=2, a=ea)
        glyph(c, "doc", (170, 840), 0.7, GREEN, CARD, ea)
        c.text((215, 840), "Import from Excel", 26, WHITE, "SemiBold", "lm", ea, CARD)
        c.text((680, 840), "→ inventory", 22, MUTED, "Regular", "rm", ea, CARD)


def workorder_form(c, x, y, t, b, a, bg=BG, sig_at=None):
    w, h = 820, 660
    c.rect((x, y, x + w, y + h), 18, fill=PAPER, a=a, bg=bg)
    if a <= 0:
        return
    c.rect((x, y, x + w, y + 64), 18, fill=TEAL, a=a, bg=PAPER)
    c.rect((x, y + 44, x + w, y + 64), 0, fill=TEAL, a=a, bg=PAPER)
    c.text((x + 30, y + 33), "NEW WORK ORDER", 24, WHITE, "Bold", "lm", a, TEAL)
    c.text((x + w - 30, y + 33), "Waiting Approval", 20, WHITE, "SemiBold", "rm", a, TEAL)
    s1 = spread(b, 1, 4, 0.3)
    s2 = spread(b, 2, 5, 0.3)

    def field(lx, ly, lab, val, st, width=360):
        fa = appear(t, st, 0.4)
        c.text((lx, ly), lab, 18, DOCGREY, "SemiBold", "la", fa * a, PAPER)
        typed = val[: int(len(val) * clamp((t - st) / 0.6))]
        c.text((lx, ly + 24), typed, 24, INKDOC, "SemiBold", "la", fa * a, PAPER)

    field(x + 30, y + 90, "Department", "ICU", s1[0])
    field(x + 300, y + 90, "Machine", "Ventilator · SN 40318", s1[0] + 0.4)
    # priority
    pa = appear(t, s1[1], 0.4)
    c.text((x + 30, y + 170), "Priority", 18, DOCGREY, "SemiBold", "la", pa * a, PAPER)
    for k, (lab, col) in enumerate((("Low", GREEN), ("Medium", SKY), ("High", AMBER), ("Urgent", CORAL))):
        sel = k == 3 and t > s1[1] + 0.8
        pill(c, (x + 75 + k * 125, y + 218), lab, col, pa * a, 18, solid=sel, bg=PAPER)
    ta = appear(t, s1[2], 0.4)
    c.text((x + 30, y + 262), "Type of work", 18, DOCGREY, "SemiBold", "la", ta * a, PAPER)
    for k, (lab, col) in enumerate((("Repair", CORAL), ("PPM", GREEN), ("Calibration", LILAC), ("Other", SKY))):
        sel = k == 0 and t > s1[2] + 0.8
        pill(c, (x + 80 + k * 150, y + 310), lab, col, ta * a, 18, solid=sel, bg=PAPER)
    field(x + 30, y + 350, "Job description", "No ventilation alarm; replaced flow sensor", s2[0])
    field(x + 30, y + 420, "Started", "09:10", s2[1])
    field(x + 180, y + 420, "Finished", "11:45", s2[1] + 0.3)
    field(x + 330, y + 420, "Spare parts from stock", "1 × flow sensor", s2[2])
    field(x + 30, y + 490, "Labour", "KSh 1,500", s2[3])
    field(x + 180, y + 490, "Extra costs", "KSh 0", s2[3] + 0.3)
    field(x + 330, y + 490, "Remarks", "Tested, back in service", s2[4])
    c.line([(x + 30, y + 610), (x + 330, y + 610)], GREYDOC, 2, a, PAPER)
    c.line([(x + 450, y + 610), (x + 790, y + 610)], GREYDOC, 2, a, PAPER)
    c.text((x + 30, y + 626), "Technician signature", 16, DOCGREY, "Regular", "la", a, PAPER)
    c.text((x + 450, y + 626), "In-charge signature", 16, DOCGREY, "Regular", "la", a, PAPER)
    if sig_at is not None:
        signature(c, x + 40, y + 590, 200, INKDOC, (t - sig_at) / 0.7, 3, a, PAPER, 2)


def s_workorder(c, t, dur, b):
    headline(c, t, "Work orders", "One complete record for every job")
    items = ["Department and machine", "Priority", "Type of work", "Job description", "Start and finish time",
             "Spare parts used", "Labour and extra costs", "Remarks", "Signatures"]
    s1 = spread(b, 1, 4, 0.3)
    s2 = spread(b, 2, 5, 0.3)
    starts = [s1[0], s1[1], s1[2], s2[0], s2[1], s2[2], s2[3], s2[4], dur - 1.4]
    c.text((120, 250), "Every work order records", 24, MUTED, "SemiBold", "lm", appear(t, b[0] + 0.3))
    ticklist(c, 140, 310, items, starts, t, gap=60, size=27)
    workorder_form(c, 960, 220, t, b, appear(t, b[0] + 0.5, 0.6), sig_at=starts[-1])


def s_corrective(c, t, dur, b):
    headline(c, t, "Corrective maintenance", "From breakdown to back in service")
    steps = [("ICU ventilator", "not working", "vent", CORAL, b[1]),
             ("Urgent repair", "work order opened", "doc", AMBER, b[1] + 2.4),
             ("Technician", "signs with saved signature", "person", TEAL, b[1] + 4.6),
             ("In-Charge", "approves or declines", "person", AMBER, b[2]),
             ("Approved", "stock, costs, history", "doc", GREEN, b[3])]
    for k, (ti, su, kind, col, st) in enumerate(steps):
        a = appear(t, st, 0.5)
        x = 120 + k * 344
        c.rect((x, 240, x + 310, 520), 22, fill=CARD, outline=col if a > 0.9 else LINE, w=3, a=a)
        glyph(c, kind, (x + 155, 330), 1.1, col, CARD, a)
        c.text((x + 155, 430), ti, 29, WHITE, "Bold", "mm", a, CARD)
        c.text((x + 155, 470), su, 21, MUTED, "Regular", "mm", a, CARD)
        if k:
            aa = appear(t, st - 0.2, 0.4)
            c.line([(x - 30, 380), (x - 30 + 26 * aa, 380)], LINE, 4, aa)
    if t > b[1] + 4.9:
        signature(c, 850, 500, 130, TEAL, (t - b[1] - 4.9) / 0.8, 3, 1, CARD, 4)
    da = appear(t, b[2] + 2.0, 0.5)
    if da > 0:
        pill(c, (1170, 570), "Declined → back with a reason", CORAL, da, 20)
    # outcomes
    outs = [("Spare parts deducted", "Flow sensor stock 6 → 5", TEAL), ("Costs totalled", "Labour + parts + extras", AMBER),
            ("History updated", "Repair and downtime recorded", LILAC)]
    for k, (ti, su, col) in enumerate(outs):
        oa = appear(t, b[3] + 0.8 + k * 1.4, 0.5)
        x = 120 + k * 470
        c.rect((x, 640, x + 440, 780), 20, fill=CARD, outline=LINE, a=oa)
        badge(c, (x + 50, 710), 20, col, "check", oa, CARD)
        c.text((x + 90, 690), ti, 27, WHITE, "SemiBold", "lm", oa, CARD)
        c.text((x + 90, 728), su, 21, MUTED, "Regular", "lm", oa, CARD)
    wa = appear(t, b[3] + 5.6, 0.5)
    if wa > 0:
        c.rect((1540, 640, 1800, 780), 20, fill=mix(BG, CORAL, 0.18), outline=CORAL, w=2, a=wa)
        c.text((1670, 690), "Not enough stock", 22, CORAL, "Bold", "mm", wa, mix(BG, CORAL, 0.18))
        c.text((1670, 728), "approval stops", 20, WHITE, "Regular", "mm", wa, mix(BG, CORAL, 0.18))


def s_ppm(c, t, dur, b):
    headline(c, t, "Preventive maintenance", "Planned automatically, closed automatically")
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    rows = [("Ventilator", "ICU", 3, 0, CORAL, "vent"), ("Dialysis machine", "Renal Unit", 3, 1, TEAL, "dialysis"),
            ("Infusion pump", "Ward 7", 4, 2, SKY, "pump"), ("Anaesthesia machine", "Theatre", 6, 1, AMBER, "lamp"),
            ("Incubator", "Maternity", 6, 3, LILAC, "incubator")]
    gx0, gy0, cw, rh = 640, 340, 96, 90
    a = appear(t, b[0] + 0.3, 0.6)
    c.rect((110, 230, 1810, 830), 26, fill=BG_SOFT, a=a)
    for j, m in enumerate(months):
        c.text((gx0 + j * cw + cw / 2, gy0 - 62), m, 21, MUTED, "SemiBold", "mm", a, BG_SOFT)
    cursor_month = 6
    for i, (name, dept, every, off, col, kind) in enumerate(rows):
        y = gy0 + 30 + i * rh
        ra = appear(t, b[1] + i * 0.25, 0.5)
        icon(c, kind, (175, y), 0.45, col, ra, BG_SOFT)
        c.text((230, y - 13), name, 25, WHITE, "SemiBold", "lm", ra, BG_SOFT)
        c.text((230, y + 17), f"{dept} · every {every} months", 19, MUTED, "Regular", "lm", ra, BG_SOFT)
        c.line([(gx0, y), (gx0 + 12 * cw, y)], LINE, 2, ra, BG_SOFT)
        for j in range(off, 12, every):
            x = gx0 + j * cw + cw / 2
            pa = appear(t, b[1] + 0.8 + i * 0.25 + j * 0.03, 0.4)
            carried = (i == 2 and j == 2)
            if carried and t > b[2] + 1.6:
                x_to = gx0 + 3 * cw + cw / 2
                u = ease((t - b[2] - 1.6) / 0.8)
                c.circle((x, y), 20, outline=AMBER, w=3, a=pa * (1 - u) + 0.3 * u, bg=BG_SOFT)
                badge(c, (x + (x_to - x) * u, y), 20, AMBER, "!", pa, BG_SOFT)
                continue
            if j < cursor_month and t > b[2]:
                badge(c, (x, y), 20, GREEN, "check", appear(t, b[2] + 0.2 + j * 0.08, 0.3), BG_SOFT)
            else:
                c.circle((x, y), 19, fill=mix(BG_SOFT, col, 0.15), outline=col, w=3, a=pa, bg=BG_SOFT)
    # planning logic toggle
    la = appear(t, b[1] + 2.5, 0.5)
    pill(c, (820, 790), "Plan by equipment type", TEAL, la, 20, solid=True, bg=BG_SOFT)
    pill(c, (1130, 790), "Plan by department", TEAL, la, 20, bg=BG_SOFT)
    # legend
    lg = appear(t, b[2], 0.5)
    for k, (lab, col, kind) in enumerate((("Done", GREEN, "check"), ("Pending", SKY, "o"), ("Carried forward", AMBER, "!"))):
        x = 1350 + k * 150
        if kind == "o":
            c.circle((x, 790), 12, outline=col, w=3, a=lg, bg=BG_SOFT)
        else:
            badge(c, (x, 790), 12, col, kind, lg, BG_SOFT)
        c.text((x + 20, 790), lab, 18, MUTED, "SemiBold", "lm", lg, BG_SOFT)
    # approval creates next
    na = appear(t, b[3] + 1.0, 0.5)
    if na > 0:
        y = gy0 + 30
        x = gx0 + 6 * cw + cw / 2
        badge(c, (x, y), 20, GREEN, "check", na, BG_SOFT)
        pill(c, (x, y - 44), "Work order approved", GREEN, na, 17, solid=True, bg=BG_SOFT)
        nb = appear(t, b[3] + 2.4, 0.5)
        x2 = gx0 + 9 * cw + cw / 2
        c.circle((x2, y), 24, outline=WHITE, w=3, a=nb, bg=BG_SOFT)
        pill(c, (x2, y + 44), "Next one created", WHITE, nb, 17, bg=BG_SOFT)


def s_calschedule(c, t, dur, b):
    headline(c, t, "Calibration schedules", "Grouped, timed and always accounted for")
    # procedure link + interval
    a1 = appear(t, b[1], 0.5)
    c.rect((120, 230, 660, 420), 22, fill=CARD, outline=LINE, a=a1)
    icon(c, "monitor", (200, 325), 0.8, CORAL, a1, CARD)
    c.text((270, 300), "Patient monitor", 27, WHITE, "SemiBold", "lm", a1, CARD)
    c.text((270, 338), "linked to: Monitor procedure", 21, MUTED, "Regular", "lm", a1, CARD)
    ia = appear(t, b[1] + 2.4, 0.5)
    pill(c, (330, 385), "6 months", LILAC, ia, 19, bg=CARD)
    pill(c, (500, 385), "12 months", LILAC, ia, 19, solid=True, bg=CARD)
    # groups on a timeline
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    g = appear(t, b[2], 0.5)
    tx0, ty, mw = 1000, 300, 66
    c.rect((700, 230, 1800, 520), 22, fill=CARD, outline=LINE, a=g)
    for j, m in enumerate(months):
        c.text((tx0 + j * mw + mw / 2, ty - 30), m, 19, MUTED, "SemiBold", "mm", g, CARD)
    groups = [("ICU · patient monitors", 2, 6, CORAL, 0.6), ("Ward 7 · infusion pumps", 5, 8, SKY, 1.6),
              ("Theatre · anaesthesia", 9, 4, AMBER, 2.6)]
    for k, (name, mo, n, col, off) in enumerate(groups):
        ga = appear(t, b[2] + off, 0.5)
        y = ty + 20 + k * 62
        x = tx0 + mo * mw
        c.rect((x + 4, y, x + mw - 4, y + 44), 10, fill=col, a=ga, bg=CARD)
        for d in range(min(n, 4)):
            c.circle((x + 16 + d * 11, y + 22), 4, fill=WHITE, a=ga, bg=col)
        c.text((x - 12, y + 22), name, 21, WHITE, "SemiBold", "rm", ga, CARD)
    da = appear(t, b[2] + 3.4, 0.5)
    if da > 0:
        c.text((tx0 + 2 * mw + mw / 2, ty + 200), "due by 31 March", 20, CORAL, "SemiBold", "mm", da, CARD)
    # status pipeline
    stages = ["Pending", "In progress", "Awaiting approval", "Completed"]
    st = spread(b, 3, 5, 0.2)
    for k, lab in enumerate(stages):
        a = appear(t, b[3] - 0.3, 0.5)
        x = 230 + k * 380
        active = t > st[k]
        col = GREEN if k == 3 else LILAC
        pill(c, (x, 620), lab, col, a, 24, solid=active)
        if k < 3:
            c.line([(x + 120, 620), (x + 250, 620)], LINE, 4, a)
    na = appear(t, st[4], 0.5)
    if na > 0:
        pill(c, (1520, 710), "Next cycle opens", GREEN, na, 22)
        c.line([(1370, 650), (1440, 690)], GREEN, 3, na)
    oa = appear(t, st[4] + 1.6, 0.5)
    pill(c, (230, 740), "Overdue", CORAL, oa, 24, solid=True)
    c.text((330, 740), "anything missed stands out", 22, MUTED, "Regular", "lm", oa)


def s_procedure(c, t, dur, b):
    headline(c, t, "Calibration procedures", "Exactly what to measure, and how")
    a = appear(t, b[0] + 0.3, 0.5)
    c.rect((120, 230, 900, 820), 22, fill=CARD, outline=LINE, a=a)
    c.text((150, 270), "Procedure · Patient monitor", 26, WHITE, "SemiBold", "lm", a, CARD)
    s = spread(b, 1, 5, 0.2)
    pa = appear(t, s[0], 0.4)
    c.rect((150, 310, 870, 370), 12, fill=CARD_HI, a=pa, bg=CARD)
    c.text((175, 340), "Parameter: NIBP (mmHg)", 24, WHITE, "SemiBold", "lm", pa, CARD_HI)
    for k, (sub, pts) in enumerate((("Systolic", "80 · 120 · 200"), ("Diastolic", "40 · 80 · 120"))):
        sa = appear(t, s[1] + k * 0.3, 0.4)
        y = 410 + k * 130
        c.line([(190, 370), (190, y + 30)], LINE, 3, sa, CARD)
        c.rect((210, y, 870, y + 110), 12, fill=BG_SOFT, a=sa, bg=CARD)
        c.text((235, y + 28), f"Sub-parameter: {sub}", 23, WHITE, "SemiBold", "lm", sa, BG_SOFT)
        c.text((235, y + 68), "Set points", 19, MUTED, "Regular", "lm", appear(t, s[2], 0.4) * sa, BG_SOFT)
        c.text((350, y + 68), pts, 22, WHITE, "SemiBold", "lm", appear(t, s[2], 0.4) * sa, BG_SOFT)
        c.text((850, y + 68), "tolerance ±3", 19, AMBER, "SemiBold", "rm", appear(t, s[3], 0.4) * sa, BG_SOFT)
    ra = appear(t, s[4], 0.4)
    c.rect((150, 700, 870, 790), 12, fill=BG_SOFT, a=ra, bg=CARD)
    icon(c, "analyser", (210, 745), 0.5, AMBER, ra, BG_SOFT)
    c.text((260, 745), "Reference standard: NIBP simulator", 23, WHITE, "SemiBold", "lm", ra, BG_SOFT)
    # session
    sa = appear(t, b[2], 0.5)
    c.rect((960, 230, 1800, 820), 22, fill=PAPER, a=sa)
    s2 = spread(b, 2, 4, 0.3)
    ea = appear(t, s2[0], 0.4)
    c.text((990, 270), "Session", 26, INKDOC, "Bold", "lm", sa, PAPER)
    for k, (lab, val) in enumerate((("Temperature", "23.1 °C"), ("Humidity", "48 %RH"), ("Pressure", "101.3 kPa"))):
        c.rect((990 + k * 265, 305, 1235 + k * 265, 385), 12, fill=SUB, a=ea * sa, bg=PAPER)
        c.text((1010 + k * 265, 330), lab, 18, DOCGREY, "SemiBold", "lm", ea * sa, SUB)
        c.text((1010 + k * 265, 362), val, 24, INKDOC, "Bold", "lm", ea * sa, SUB)
    ra2 = appear(t, s2[1], 0.4)
    c.text((990, 420), "Resolution: 1 mmHg", 22, INKDOC, "SemiBold", "lm", ra2 * sa, PAPER)
    c.text((990, 470), "Set point", 18, DOCGREY, "SemiBold", "lm", ra2 * sa, PAPER)
    for k in range(3):
        c.text((1200 + k * 150, 470), f"Reading {k + 1}", 18, DOCGREY, "SemiBold", "lm", ra2 * sa, PAPER)
    vals = [("80", "81", "80", "81"), ("120", "121", "122", "121"), ("200", "202", "201", "202")]
    for r, row in enumerate(vals):
        y = 520 + r * 56
        for k, v in enumerate(row):
            va = appear(t, s2[2] + r * 0.4 + k * 0.12, 0.3)
            c.text((1000 if k == 0 else 1200 + (k - 1) * 150, y), v, 24, INKDOC, "SemiBold" if k == 0 else "Regular",
                   "lm", va * sa, PAPER)
    rc = appear(t, s2[3], 0.4)
    pill(c, (1180, 760), "Suggested: used before on this type", GREEN, rc * sa, 19, solid=True, bg=PAPER)


def s_verdict(c, t, dur, b):
    headline(c, t, "The verdict", "Calculated for you, and strict")
    a = appear(t, 0.4, 0.5)
    c.rect((120, 230, 1400, 800), 22, fill=PAPER, a=a)
    heads = [("Set point", 160), ("Mean", 380), ("Error", 580), ("Uncertainty", 780), ("Tolerance", 1010), ("Result", 1250)]
    s = spread(b, 0, 3, 1.2)
    for k, (lab, x) in enumerate(heads):
        ha = a if k in (0, 4, 5) else appear(t, s[min(k - 1, 2)], 0.4)
        c.text((x, 280), lab, 21, DOCGREY, "SemiBold", "lm", ha, PAPER)
    rows = [("Systolic 80", "80.7", "+0.7", "±0.8", "±3"), ("Systolic 120", "121.3", "+1.3", "±0.9", "±3"),
            ("Systolic 200", "201.7", "+1.7", "±0.9", "±3"), ("Diastolic 40", "40.3", "+0.3", "±0.8", "±3"),
            ("Diastolic 80", "80.7", "+0.7", "±0.8", "±3"), ("Diastolic 120", "124.1", "+4.1", "±1.0", "±3")]
    for r, row in enumerate(rows):
        y = 340 + r * 72
        ra = appear(t, 0.8 + r * 0.25, 0.4)
        c.rect((140, y - 26, 1380, y + 26), 10, fill=SUB, a=ra * a, bg=PAPER)
        for k, v in enumerate(row):
            vis = ra if k in (0, 4) else appear(t, s[min(k - 1, 2)] + r * 0.1, 0.3)
            col = CORAL if (r == 5 and k == 2 and t > b[1]) else INKDOC
            c.text((heads[k][1], y), v, 23, col, "SemiBold" if k == 0 else "Regular", "lm", vis * a, SUB)
        if t > s[2] + r * 0.1:
            bad = r == 5 and t > b[1]
            okc = CORAL if bad else GREEN
            badge(c, (1290, y), 18, okc, "cross" if bad else "check", appear(t, s[2] + r * 0.1, 0.3), SUB)
    va = appear(t, b[1] + 0.4, 0.5)
    c.rect((1450, 230, 1800, 520), 22, fill=CARD, outline=LINE, a=va)
    c.text((1625, 290), "Verdict", 26, MUTED, "SemiBold", "mm", va, CARD)
    if va > 0:
        badge(c, (1625, 380), 44, CORAL, "cross", va, CARD)
        c.text((1625, 470), "Fails", 34, CORAL, "Bold", "mm", va, CARD)
    ra = appear(t, b[1] + 1.8, 0.5)
    c.rect((1450, 560, 1800, 800), 22, fill=CARD, outline=LINE, a=ra)
    c.para((1480, 610), "One point outside tolerance is enough to fail the device.", 24, WHITE, 300, "Regular",
           a=ra, bg=CARD)


def s_certificate(c, t, dur, b):
    headline(c, t, "Review and certificates", "Approved, numbered, generated")
    ra = appear(t, b[0], 0.5)
    person(c, (220, 320), 1.0, GREEN, ra)
    c.text((220, 420), "Reviewer", 26, WHITE, "SemiBold", "mm", ra)
    reasons = ["Data quality", "Procedure not followed", "Equipment error", "Environmental conditions"]
    for k, rs in enumerate(reasons):
        pa = appear(t, b[0] + 2.2 + k * 0.4, 0.4)
        pill(c, (220, 490 + k * 58), rs, CORAL, pa, 18)
    c.text((220, 730), "reasons to reject", 20, MUTED, "Regular", "mm", appear(t, b[0] + 2.2, 0.4))
    # numbering
    na = appear(t, b[1], 0.5)
    c.rect((440, 250, 820, 520), 22, fill=CARD, outline=LINE, a=na)
    glyph(c, "sync", (630, 320), 1.0, TEAL, CARD, na)
    c.text((630, 400), "Number issued", 26, WHITE, "SemiBold", "mm", na, CARD)
    c.text((630, 436), "centrally", 22, MUTED, "Regular", "mm", na, CARD)
    off = appear(t, b[1] + 3.0, 0.5)
    got = t > b[1] + 5.6
    pill(c, (630, 480), "Number received" if got else "Offline: number to follow", GREEN if got else AMBER, off, 17,
         solid=got, bg=CARD)
    # certificate
    da = appear(t, b[0] + 1.2, 0.7)
    x, y, w, h = 900, 200, 860, 700
    c.rect((x + 14, y + 14, x + w + 14, y + h + 14), 16, fill=BG_SOFT, a=da)
    c.rect((x, y, x + w, y + h), 16, fill=PAPER, a=da)
    if da <= 0:
        return
    logo(c, LOGO_ICON, (x + 70, y + 76), 64, da)
    c.text((x + 120, y + 62), "CALIBRATION CERTIFICATE", 30, INKDOC, "Bold", "lm", da, PAPER)
    c.text((x + 120, y + 98), "Patient monitor · ICU", 22, DOCGREY, "Regular", "lm", da, PAPER)
    if got:
        pill(c, (x + w - 150, y + 78), "Cert. No. issued", LILAC, appear(t, b[1] + 5.6, 0.4), 18, solid=True, bg=PAPER)
    s = spread(b, 2, 6, 0.2)
    labels = ["Results", "Uncertainty", "Reference standards", "Drift history", "Signatures", "QR code"]
    for k, lab in enumerate(labels):
        sa = appear(t, s[k], 0.4)
        col_x = x + 50 + (k % 2) * 400
        row_y = y + 170 + (k // 2) * 160
        c.text((col_x, row_y), lab, 22, DOCGREY, "SemiBold", "la", sa, PAPER)
        if k == 0:
            for j, hgt in enumerate((50, 64, 44, 58, 52)):
                c.rect((col_x + j * 50, row_y + 110 - hgt * sa, col_x + 32 + j * 50, row_y + 110), 4, fill=TEAL, a=sa,
                       bg=PAPER)
        elif k == 1:
            for j in range(5):
                c.line([(col_x + 16 + j * 50, row_y + 50), (col_x + 16 + j * 50, row_y + 100)], INKDOC, 3, sa, PAPER)
                c.line([(col_x + 6 + j * 50, row_y + 50), (col_x + 26 + j * 50, row_y + 50)], INKDOC, 3, sa, PAPER)
                c.line([(col_x + 6 + j * 50, row_y + 100), (col_x + 26 + j * 50, row_y + 100)], INKDOC, 3, sa, PAPER)
        elif k == 2:
            for j in range(3):
                c.rect((col_x, row_y + 44 + j * 28, col_x + 300 - j * 50, row_y + 58 + j * 28), 4, fill=GREYDOC, a=sa,
                       bg=PAPER)
        elif k == 3:
            pts = [(col_x + 10 + j * 70, row_y + 110 - v) for j, v in enumerate((20, 30, 26, 44, 40))]
            c.line(pts, LILAC, 4, sa, PAPER)
            for p in pts:
                c.circle(p, 6, fill=LILAC, a=sa, bg=PAPER)
        elif k == 4:
            signature(c, col_x, row_y + 76, 130, INKDOC, (t - s[k]) / 0.8, 3, sa, PAPER, 3)
            signature(c, col_x + 170, row_y + 76, 130, INKDOC, (t - s[k] - 0.5) / 0.8, 3, sa, PAPER, 8)
        else:
            qr(c, (col_x, row_y + 36), 120, INKDOC, sa, PAPER, clamp((t - s[k]) / 1.0))


def s_standards(c, t, dur, b):
    headline(c, t, "Reference standards", "Traceability you can show an auditor")
    example_tag(c, t)
    stds = [("NIBP simulator", "SN 5521-A", "CAL-4471", "Mar 2026", "Mar 2027", "Accredited lab", "±0.5 mmHg"),
            ("Electrical safety analyser", "SN 3390-K", "CAL-4419", "Jan 2026", "Jan 2027", "Accredited lab", "±1 %"),
            ("Infusion device analyser", "SN 7710-C", "CAL-4502", "Jun 2026", "Jun 2027", "Accredited lab", "±1 %")]
    fields = ["Serial no.", "Certificate", "Calibrated", "Due", "Agency", "Uncertainty"]
    st = spread(b, 1, 6, 0.2)
    for r, row in enumerate(stds):
        ra = appear(t, b[0] + 0.4 + r * 0.4, 0.5)
        y = 240 + r * 200
        c.rect((120, y, 1800, y + 176), 22, fill=CARD, outline=LINE, a=ra)
        icon(c, "analyser", (210, y + 88), 0.9, AMBER, ra, CARD)
        c.text((300, y + 50), row[0], 30, WHITE, "Bold", "lm", ra, CARD)
        for k, lab in enumerate(fields):
            fa = appear(t, st[k] + r * 0.1, 0.4)
            x = 300 + k * 250
            c.text((x, y + 104), lab, 19, MUTED, "SemiBold", "lm", fa, CARD)
            c.text((x, y + 138), row[k + 1], 24, WHITE, "Regular", "lm", fa, CARD)


def s_predict(c, t, dur, b):
    headline(c, t, "Predictions", "Drift tells you what is coming")
    a = appear(t, b[1], 0.5)
    x0, y0, x1, y1 = 200, 260, 1150, 760
    c.rect((120, 230, 1200, 840), 22, fill=CARD, outline=LINE, a=a)
    if a > 0:
        # tolerance band
        def ymap(v):
            return y0 + (3.6 - v) / 7.2 * (y1 - y0)
        c.rect((x0, ymap(3), x1, ymap(-3)), 0, fill=mix(CARD, GREEN, 0.10), a=a, bg=CARD)
        c.line([(x0, ymap(3)), (x1, ymap(3))], CORAL, 3, a, CARD)
        c.line([(x0, ymap(-3)), (x1, ymap(-3))], CORAL, 3, a, CARD)
        c.text((x1 - 10, ymap(3) - 18), "tolerance +3", 19, CORAL, "SemiBold", "rm", a, CARD)
        c.line([(x0, ymap(0)), (x1, ymap(0))], LINE, 2, a, CARD)
        years = [2022, 2023, 2024, 2025, 2026, 2027, 2028]
        xs = [x0 + 40 + k * (x1 - x0 - 80) / 6 for k in range(7)]
        for k, yr in enumerate(years):
            c.text((xs[k], y1 + 30), str(yr), 19, MUTED, "SemiBold", "mm", a, CARD)
        # 0.9 mmHg a year against a 3 mmHg tolerance is 30% of tolerance per year: "Drifting" in
        # CalSoft.utils.DRIFT_BANDS; at 2.4 mmHg now it reaches 3 in (3 - 2.4) / 0.9, about 8 months.
        errs = [-1.2, -0.3, 0.6, 1.5, 2.4]
        n = int(clamp((t - b[1] - 0.4) / 2.0) * 5 + 0.999)
        pts = [(xs[k], ymap(v)) for k, v in enumerate(errs)][:n]
        if len(pts) > 1:
            c.line(pts, SKY, 4, a, CARD)
        for p in pts:
            c.circle(p, 9, fill=SKY, a=a, bg=CARD)
        ta = appear(t, b[2], 0.6)
        if ta > 0:
            p0 = (xs[4], ymap(2.4))
            p1 = (xs[4] + (xs[5] - xs[4]) * (0.6 / 0.9), ymap(3.0))
            for k in range(8):
                if k % 2 == 0:
                    c.line([lerp(p0, p1, k / 8 * ta), lerp(p0, p1, (k + 1) / 8 * ta)], AMBER, 4, 1, CARD)
            if ta > 0.95:
                la = appear(t, b[2] + 0.8, 0.4)
                c.circle(p1, 12, fill=CORAL, a=la, bg=CARD)
                c.text((p1[0] - 24, ymap(3) - 30), "≈ 8 months to tolerance", 24, CORAL, "Bold", "rm", la, CARD)
        c.text((x0, y0 - 6), "Error at 120 mmHg, by year", 20, MUTED, "SemiBold", "lm", a, CARD)
    bands = [("Very stable", "extend the interval", GREEN), ("Normal", "keep the interval", SKY),
             ("Drifting", "shorten the interval", AMBER), ("Urgent", "breach within two years", CORAL)]
    for k, (lab, rec, col) in enumerate(bands):
        ba = appear(t, b[2] + k * 0.9, 0.4)
        y = 250 + k * 145
        hi = k == 2 and t > b[2] + 3.4
        c.rect((1250, y, 1800, y + 125), 20, fill=mix(BG, col, 0.16) if hi else CARD, outline=col if hi else LINE,
               w=3 if hi else 2, a=ba)
        fillbg = mix(BG, col, 0.16) if hi else CARD
        c.circle((1300, y + 62), 14, fill=col, a=ba, bg=fillbg)
        c.text((1335, y + 44), lab, 28, WHITE, "Bold", "lm", ba, fillbg)
        c.text((1335, y + 84), rec, 22, MUTED, "Regular", "lm", ba, fillbg)


def s_parts(c, t, dur, b):
    headline(c, t, "Parts and tools", "Stock you can trust, requests you can trace")
    example_tag(c, t)
    s = spread(b, 1, 2, 0.2)
    ta = appear(t, s[0], 0.5)
    c.rect((120, 230, 620, 560), 22, fill=CARD, outline=LINE, a=ta)
    c.text((150, 270), "Tools", 28, WHITE, "Bold", "lm", ta, CARD)
    for k, (tool, sn) in enumerate((("Multimeter", "SN 7712"), ("Torque screwdriver", "SN 2209"),
                                    ("Safety analyser", "SN 3390"))):
        y = 330 + k * 72
        icon(c, "wrench", (175, y), 0.4, CORAL, ta, CARD)
        c.text((215, y - 12), tool, 23, WHITE, "SemiBold", "lm", ta, CARD)
        c.text((215, y + 16), sn, 19, MUTED, "Regular", "lm", ta, CARD)
    pa = appear(t, s[1], 0.5)
    c.rect((660, 230, 1200, 560), 22, fill=CARD, outline=LINE, a=pa)
    c.text((690, 270), "Spare parts", 28, WHITE, "Bold", "lm", pa, CARD)
    stock_boost = t > b[2] + 7.0
    for k, (part, fits, stock, cost) in enumerate((("Flow sensor", "Ventilator", 5, "KSh 4,200"),
                                                  ("Pump roller", "Dialysis machine", 3, "KSh 2,400"),
                                                  ("SpO2 probe", "Patient monitor", 12, "KSh 3,100"))):
        y = 330 + k * 72
        val = stock + (10 if (k == 1 and stock_boost) else 0)
        c.text((690, y - 12), part, 23, WHITE, "SemiBold", "lm", pa, CARD)
        c.text((690, y + 16), f"fits {fits} · {cost}", 19, MUTED, "Regular", "lm", pa, CARD)
        c.text((1170, y), f"{val} in stock", 21, GREEN if k == 1 and stock_boost else WHITE, "SemiBold", "rm", pa,
               CARD)
    # request flow
    steps = [("Request", "workshop asks for a restock", "person", TEAL), ("Approve", "HOD sets 10 × KSh 2,400", "person", GREEN),
             ("Receive", "workshop confirms receipt", "doc", AMBER), ("Stock up", "pump roller 3 → 13", "bars", SKY)]
    st = spread(b, 2, 4, 0.2)
    for k, (ti, su, kind, col) in enumerate(steps):
        a = appear(t, st[k], 0.5)
        x = 120 + k * 420
        c.rect((x, 610, x + 390, 790), 20, fill=CARD, outline=col if a > 0.9 else LINE, w=2, a=a)
        glyph(c, kind, (x + 60, 700), 0.8, col, CARD, a)
        c.text((x + 115, 680), ti, 27, WHITE, "Bold", "lm", a, CARD)
        c.text((x + 115, 718), su, 20, MUTED, "Regular", "lm", a, CARD)
    ha = appear(t, st[3] + 1.2, 0.5)
    c.rect((1240, 230, 1800, 560), 22, fill=CARD, outline=LINE, a=ha)
    c.text((1270, 270), "Request history", 28, WHITE, "Bold", "lm", ha, CARD)
    for k, (ev, col) in enumerate((("Created", TEAL), ("Approved by HOD", GREEN), ("Accepted by workshop", AMBER))):
        y = 340 + k * 64
        c.circle((1290, y), 10, fill=col, a=ha, bg=CARD)
        c.text((1315, y), ev, 23, WHITE, "Regular", "lm", ha, CARD)


def s_performance(c, t, dur, b):
    headline(c, t, "Performance overview", "How your equipment is really doing")
    example_tag(c, t)
    a = appear(t, b[1], 0.5)
    # status by category
    c.rect((120, 230, 760, 560), 22, fill=CARD, outline=LINE, a=a)
    c.text((150, 268), "Status by category", 24, WHITE, "SemiBold", "lm", a, CARD)
    cats = [("Critical", 160, 12, 5), ("General", 252, 11, 4)]
    for k, (cat, wk, ur, nw) in enumerate(cats):
        y = 330 + k * 90
        tot = wk + ur + nw
        g = ease((t - b[1] - 0.3) / 0.8)
        x = 280
        span = 440 * g
        for v, col in ((wk, GREEN), (ur, AMBER), (nw, CORAL)):
            wv = span * v / tot
            c.rect((x, y, x + wv, y + 40), 6, fill=col, a=a, bg=CARD)
            x += wv
        c.text((150, y + 20), cat, 22, WHITE, "SemiBold", "lm", a, CARD)
    for k, (lab, col) in enumerate((("Working", GREEN), ("Under repair", AMBER), ("Not working", CORAL))):
        c.circle((170 + k * 180, 520), 8, fill=col, a=a, bg=CARD)
        c.text((186 + k * 180, 520), lab, 18, MUTED, "SemiBold", "lm", a, CARD)
    s = spread(b, 1, 3, 1.5)
    for k, (lab, val, col) in enumerate((("Repairs this month", "42", AMBER), ("Downtime", "318 h", CORAL),
                                         ("Labour + parts", "KSh 486k", SKY))):
        kpi(c, (800 + k * 340, 230), 320, 150, lab, val, col, appear(t, s[k], 0.4))
    # manufacturers
    ma = appear(t, b[2], 0.5)
    c.rect((800, 410, 1800, 800), 22, fill=CARD, outline=LINE, a=ma)
    heads = [("Manufacturer", 830), ("Uptime", 1110), ("Repairs / machine", 1270), ("Avg repair cost", 1480),
             ("Rating", 1680)]
    for lab, x in heads:
        c.text((x, 450), lab, 19, MUTED, "SemiBold", "lm", ma, CARD)
    mrows = [("Manufacturer A", "97%", "0.8", "KSh 3.1k", "Excellent", GREEN),
             ("Manufacturer B", "88%", "1.9", "KSh 5.4k", "Good", SKY),
             ("Manufacturer C", "79%", "3.2", "KSh 8.9k", "Fair", AMBER),
             ("Manufacturer D", "71%", "4.6", "KSh 11.2k", "Poor", CORAL)]
    for r, row in enumerate(mrows):
        ra = appear(t, b[2] + 1.0 + r * 0.5, 0.4)
        y = 510 + r * 62
        for k, (lab, x) in enumerate(heads[:4]):
            c.text((x, y), row[k], 22, WHITE if k == 0 else (205, 218, 220), "SemiBold" if k == 0 else "Regular", "lm",
                   ra, CARD)
        pill(c, (1720, y), row[4], row[5], ra, 16, solid=True, bg=CARD)
    # recommendations
    rc = appear(t, b[2] + 5.0, 0.5)
    c.rect((120, 600, 760, 800), 22, fill=CARD, outline=GREEN, w=2, a=rc)
    c.text((150, 638), "Recommendations", 24, GREEN, "SemiBold", "lm", rc, CARD)
    c.para((150, 675), "Prefer Manufacturer A (97% uptime) for future purchases. Review maintenance for Manufacturer D.",
           21, WHITE, 580, "Regular", a=rc, bg=CARD)
    ex = appear(t, b[3], 0.4)
    pill(c, (1500, 850), "Export to Excel", GREEN, ex, 20, solid=True)
    pill(c, (1700, 850), "Export PDF", CORAL, ex, 20, solid=True)


def s_reports(c, t, dur, b):
    headline(c, t, "Report hub", "Weekly, monthly, per workshop")
    a = appear(t, b[0] + 0.2, 0.5)
    pill(c, (220, 260), "Weekly", SKY, a, 22)
    pill(c, (370, 260), "Monthly", SKY, a, 22, solid=True)
    fa = appear(t, b[0] + 2.2, 0.5)
    for k, lab in enumerate(("ICU · Repairs", "All workshops · PPM", "Critical equipment")):
        pill(c, (700 + k * 330, 260), "★ " + lab, AMBER, fa, 19)
    c.text((1680, 260), "saved filters", 19, MUTED, "Regular", "mm", fa)
    for k in range(4):
        da = appear(t, b[0] + 0.8 + k * 0.4, 0.5)
        x = 140 + k * 420
        c.rect((x, 330, x + 380, 820), 14, fill=PAPER, a=da)
        c.rect((x, 330, x + 380, 380), 14, fill=TEAL, a=da, bg=PAPER)
        c.rect((x, 360, x + 380, 380), 0, fill=TEAL, a=da, bg=PAPER)
        c.text((x + 24, 356), ["Renal Workshop", "ICU Workshop", "Theatre Workshop", "Calibration Centre"][k], 21, WHITE,
               "SemiBold", "lm", da, TEAL)
        for j, hgt in enumerate((80, 120, 96, 140, 110)):
            c.rect((x + 40 + j * 62, 560 - hgt, x + 80 + j * 62, 560), 5, fill=[TEAL, SKY][j % 2], a=da, bg=PAPER)
        for j in range(5):
            c.rect((x + 30, 600 + j * 36, x + 350 - (j % 3) * 60, 614 + j * 36), 4, fill=GREYDOC, a=da, bg=PAPER)
    ha = appear(t, b[0] + 4.5, 0.5)
    c.text((960, 870), "The head of department gets a report for each workshop", 24, WHITE, "SemiBold", "mm", ha)


def s_security(c, t, dur, b):
    headline(c, t, "Security and accountability", "The right access, a full record")
    for k, (role, sees, col) in enumerate((("Technician", "their workshop's work", TEAL),
                                           ("In-Charge", "approvals for the department", AMBER),
                                           ("Head of Dept", "every workshop and user", GREEN))):
        a = appear(t, b[0] + 0.4 + k * 0.6, 0.5)
        x = 120 + k * 400
        c.rect((x, 240, x + 370, 470), 22, fill=CARD, outline=LINE, a=a)
        person(c, (x + 185, 320), 1.0, col, a, CARD)
        c.text((x + 185, 410), role, 27, WHITE, "Bold", "mm", a, CARD)
        c.text((x + 185, 444), sees, 20, MUTED, "Regular", "mm", a, CARD)
    s = spread(b, 1, 3, 0.2)
    la = appear(t, s[0], 0.5)
    c.rect((1340, 240, 1800, 470), 22, fill=CARD, outline=CORAL, w=2, a=la)
    c.rect((1530, 300, 1610, 370), 10, fill=CORAL, a=la, bg=CARD)
    c.arc((1540, 262, 1600, 330), 180, 360, CORAL, 8, la, CARD)
    c.text((1570, 410), "Too many attempts", 24, WHITE, "SemiBold", "mm", la, CARD)
    c.text((1570, 442), "login blocked for a while", 20, MUTED, "Regular", "mm", la, CARD)
    aa = appear(t, s[1], 0.5)
    c.rect((120, 510, 1160, 840), 22, fill=CARD, outline=LINE, a=aa)
    c.text((150, 550), "Audit timeline", 26, WHITE, "SemiBold", "lm", aa, CARD)
    evs = [("Reviewer approved a calibration session", GREEN), ("In-charge approved a work order", AMBER),
           ("Technician edited a procedure", TEAL), ("HOD approved a parts request", LILAC)]
    for k, (ev, col) in enumerate(evs):
        ea = appear(t, s[1] + 0.3 + k * 0.35, 0.4)
        y = 610 + k * 54
        c.circle((170, y), 10, fill=col, a=ea, bg=CARD)
        c.text((195, y), ev, 23, WHITE, "Regular", "lm", ea, CARD)
    ua = appear(t, s[2], 0.5)
    c.rect((1200, 510, 1800, 840), 22, fill=CARD, outline=LINE, a=ua)
    glyph(c, "shield", (1500, 640), 1.8, TEAL, CARD, ua)
    c.text((1500, 780), "Updates verified before install", 23, WHITE, "SemiBold", "mm", ua, CARD)


def s_offline(c, t, dur, b):
    headline(c, t, "Works offline", "Every workshop keeps going")
    hub = (960, 330)
    ca = appear(t, 0.3, 0.5)
    mk.cloud(c, hub, 170, CARD_HI, TEAL, ca)
    logo(c, LOGO_ICON, (hub[0], hub[1] + 6), 70, ca)
    names = ["Renal Workshop", "ICU Workshop", "Theatre Workshop", "Main Workshop", "Calibration Centre"]
    down = 2.5 < t < dur - 3.5
    for i, name in enumerate(names):
        a = appear(t, 0.5 + i * 0.25, 0.5)
        x, y = 230 + i * 365, 690
        p, q = (x, y - 70), (hub[0] + (x - hub[0]) * 0.18, hub[1] + 70)
        off = down and i == 2
        if off:
            for k in range(14):
                if k % 2 == 0:
                    c.line([lerp(p, q, k / 14), lerp(p, q, (k + 1) / 14)], CORAL, 3, a)
            badge(c, lerp(p, q, 0.5), 18, CORAL, "cross", a)
        else:
            c.line([p, q], LINE, 3, a)
            u = ((t * 0.5) + i * 0.23) % 1.0
            c.circle(lerp(p, q, u), 7, fill=WHITE, a=a)
        c.rect((x - 165, y - 70, x + 165, y + 150), 22, fill=CARD, outline=CORAL if off else LINE, w=3 if off else 2,
               a=a)
        computer(c, (x, y + 5), 0.75, TEAL, a, CARD)
        c.text((x, y + 82), name, 24, WHITE, "SemiBold", "mm", a, CARD)
        status = ("Offline · still working", CORAL) if off else ("Up to date", GREEN)
        c.text((x, y + 118), status[0], 20, status[1], "SemiBold", "mm", a, CARD)


def s_close(c, t, dur, b):
    logo(c, wide_logo(), (960, 360), 300, appear(t, 0.2, 1.0))
    c.text((960, 640), "Every machine ready. Every record signed.", 40, WHITE, "SemiBold", "mm", appear(t, 1.2, 0.7))
    pill(c, (960, 780), "Book a demo", TEAL, appear(t, b[1] - 0.2, 0.6), 36, solid=True)


SCENES = {n: globals()[f"s_{n}"] for n in ORDER}
NO_SUBTITLES = {"intro", "close"}


# ---- build --------------------------------------------------------------------

def synth():
    from kokoro_onnx import Kokoro

    k = Kokoro(str(mk.TTS_DIR / "kokoro-v1.0.onnx"), str(mk.TTS_DIR / "voices-v1.0.bin"))
    out = {}
    for name in ORDER:
        parts, beats, spans = [np.zeros(int(LEAD_IN * SR), dtype=np.float32)], [], []
        pos = LEAD_IN
        for display, spoken in NARRATION[name]:
            audio, sr = k.create(spoken or display, voice=mk.VOICE, speed=SPEED, lang="en-us")
            audio = audio.astype(np.float32)
            beats.append(pos)
            spans.append((pos, pos + len(audio) / SR, display))
            parts += [audio, np.zeros(int(GAP * SR), dtype=np.float32)]
            pos += len(audio) / SR + GAP
        parts.append(np.zeros(int(TAIL * SR), dtype=np.float32))
        out[name] = (np.concatenate(parts), beats, spans)
        print(f"voice: {name} {pos + TAIL:.1f}s", flush=True)
    return out


def render(name, t, dur, beats, spans):
    img = Image.new("RGB", (W * S, H * S), BG)
    c = Canvas(img)
    SCENES[name](c, t, dur, beats)
    if name not in NO_SUBTITLES:
        mk.subtitle(c, t, spans)
    img = img.resize((W, H), Image.LANCZOS)
    a = min(clamp(t / 0.5), clamp((dur - t) / 0.5))
    if a < 1:
        img = Image.blend(Image.new("RGB", (W, H), BG), img, a)
    return img


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--still", type=int, help="write a PNG of scene N (1-based) instead of the video")
    ap.add_argument("--at", type=float, default=None, help="seconds into the scene for --still (default: near end)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    import soundfile as sf

    BUILD.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(repr((NARRATION, mk.VOICE, SPEED, LEAD_IN, GAP, TAIL)).encode()).hexdigest()[:12]
    cache = BUILD / f"voice_{key}.pkl"
    if cache.exists():
        voice = pickle.loads(cache.read_bytes())
    else:
        voice = synth()
        cache.write_bytes(pickle.dumps(voice))

    if args.still:
        name = ORDER[args.still - 1]
        audio, beats, spans = voice[name]
        dur = len(audio) / SR
        t = args.at if args.at is not None else dur - 0.6
        path = BUILD / f"scene{args.still:02d}_{name}.png"
        render(name, t, dur, beats, spans).save(path)
        print(path)
        return

    track = np.concatenate([voice[n][0] for n in ORDER])
    total = len(track) / SR
    music = mk.fit(mk.music_bed(total), len(track))
    mixed = track / (np.max(np.abs(track)) + 1e-9) * 0.9 + music * 0.10
    mixed /= max(1.0, np.max(np.abs(mixed)) / 0.98)
    wav = BUILD / "soundtrack.wav"
    sf.write(wav, mixed.astype(np.float32), SR)

    ff = mk.ffmpeg_exe()
    silent = BUILD / "video.mp4"
    cmd = [ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", str(silent)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    done = 0
    for name in ORDER:
        audio, beats, spans = voice[name]
        dur = len(audio) / SR
        nframes = int(round(dur * FPS))
        for f in range(nframes):
            proc.stdin.write(render(name, f / FPS, dur, beats, spans).tobytes())
        done += nframes
        print(f"video: {name} done ({done / FPS:.0f}s of {total:.0f}s)", flush=True)
    proc.stdin.close()
    if proc.wait():
        sys.exit("ffmpeg failed while encoding video")
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(silent), "-i", str(wav), "-c:v", "copy", "-c:a", "aac",
                    "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(args.out)], check=True)
    print(args.out)


if __name__ == "__main__":
    main()
