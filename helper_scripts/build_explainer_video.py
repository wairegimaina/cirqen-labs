#!/usr/bin/env python3
"""Render an animated, illustrated explainer of the whole Equiper system.

Unlike build_showcase_video.py (which frames real screenshots), this draws
every scene from scratch, so it needs no running app, database or demo data:

    python helper_scripts/build_explainer_video.py            # full video
    python helper_scripts/build_explainer_video.py --still 7  # PNG of scene 7

Output: data/Equiper_Explainer.mp4 (1080p, 30 fps, silent, captioned).
Needs Pillow and ffmpeg (on PATH, or via the imageio-ffmpeg package).
"""
import argparse
import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "Equiper_Explainer.mp4"

W, H = 1920, 1080
FPS = 30
FADE = 0.45

INK = (16, 30, 29)
PANEL = (27, 48, 46)
PANEL_HI = (37, 64, 61)
LINE = (58, 88, 84)
PAPER = (241, 244, 242)
MUTED = (140, 158, 154)
ACCENT = (92, 198, 210)
ACCENT_DEEP = (10, 92, 104)
AMBER = (240, 182, 84)
RED = (232, 104, 92)
GREEN = (112, 204, 142)
VIOLET = (170, 146, 236)

SANS = "/usr/share/fonts/truetype/liberation/LiberationSans-%s.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
_fonts = {}


def font(size, bold=False):
    key = (size, bold)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(SANS % ("Bold" if bold else "Regular"), size)
    return _fonts[key]


def mono(size):
    key = ("mono", size)
    if key not in _fonts:
        _fonts[key] = ImageFont.truetype(MONO, size)
    return _fonts[key]


# ---- animation helpers ---------------------------------------------------

def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ease(x):
    x = clamp(x)
    return 4 * x * x * x if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def appear(t, start, length=0.5):
    return ease((t - start) / length)


def mix(c1, c2, a):
    return tuple(int(c1[i] + (c2[i] - c1[i]) * a) for i in range(3))


def fade(c, a, bg=INK):
    """Colour c at opacity a over the background."""
    return mix(bg, c, clamp(a))


def lerp(p, q, a):
    return (p[0] + (q[0] - p[0]) * a, p[1] + (q[1] - p[1]) * a)


# ---- drawing helpers -----------------------------------------------------

def text(d, xy, s, f, fill, anchor="la"):
    d.text(xy, s, font=f, fill=fill, anchor=anchor)


def wrap(d, s, f, width):
    lines, cur = [], ""
    for word in s.split():
        trial = (cur + " " + word).strip()
        if d.textlength(trial, font=f) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def para(d, xy, s, f, fill, width, gap=8, anchor="la"):
    x, y = xy
    size = f.size
    for line in wrap(d, s, f, width):
        text(d, (x, y), line, f, fill, anchor)
        y += size + gap
    return y


def box(d, xy, a=1.0, fill=PANEL, outline=LINE, r=18, w=2):
    d.rounded_rectangle(xy, r, fill=fade(fill, a), outline=fade(outline, a) if outline else None, width=w)


def card(d, xy, title, sub, a, color=ACCENT, title_size=30, sub_size=22, fill=PANEL):
    """A panel with a coloured top rule, a title and a sub line."""
    if a <= 0:
        return
    x0, y0, x1, y1 = xy
    box(d, xy, a, fill=fill)
    d.rounded_rectangle((x0, y0, x1, y0 + 8), 4, fill=fade(color, a))
    cx = (x0 + x1) / 2
    has_sub = bool(sub)
    ty = (y0 + y1) / 2 - (18 if has_sub else 0)
    text(d, (cx, ty), title, font(title_size, True), fade(PAPER, a), "mm")
    if has_sub:
        para(d, (cx, ty + title_size - 4), sub, font(sub_size), fade(MUTED, a), x1 - x0 - 36, gap=4, anchor="ma")


def arrow(d, p, q, prog=1.0, color=ACCENT, w=4, head=16, a=1.0):
    if prog <= 0 or a <= 0:
        return
    end = lerp(p, q, clamp(prog))
    col = fade(color, a)
    d.line([p, end], fill=col, width=w)
    ang = math.atan2(end[1] - p[1], end[0] - p[0])
    left = (end[0] - head * math.cos(ang - 0.45), end[1] - head * math.sin(ang - 0.45))
    right = (end[0] - head * math.cos(ang + 0.45), end[1] - head * math.sin(ang + 0.45))
    d.polygon([end, left, right], fill=col)


def dashed(d, p, q, color, w=3, dash=14, gap=10, offset=0.0):
    length = math.dist(p, q)
    if length == 0:
        return
    step = dash + gap
    s = -(offset % step)
    while s < length:
        a0, a1 = max(s, 0) / length, min(s + dash, length) / length
        if a1 > a0:
            d.line([lerp(p, q, a0), lerp(p, q, a1)], fill=color, width=w)
        s += step


def dot(d, c, r, color):
    d.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill=color)


def packet(d, p, q, t, period, color, r=9, phase=0.0):
    """A dot travelling p -> q repeatedly."""
    u = ((t / period) + phase) % 1.0
    dot(d, lerp(p, q, u), r, color)


def check(d, c, size, color, w=6):
    x, y = c
    d.line([(x - size * 0.5, y), (x - size * 0.12, y + size * 0.4), (x + size * 0.55, y - size * 0.45)],
           fill=color, width=w, joint="curve")


def cross(d, c, size, color, w=6):
    x, y = c
    s = size / 2
    d.line([(x - s, y - s), (x + s, y + s)], fill=color, width=w)
    d.line([(x - s, y + s), (x + s, y - s)], fill=color, width=w)


def person(d, c, scale, color):
    x, y = c
    dot(d, (x, y - 30 * scale), 22 * scale, color)
    d.rounded_rectangle((x - 38 * scale, y, x + 38 * scale, y + 60 * scale), int(26 * scale), fill=color)


def chip(d, xy, label, color, a=1.0, size=22):
    if a <= 0:
        return
    f = font(size, True)
    w = d.textlength(label, font=f) + 34
    x, y = xy
    d.rounded_rectangle((x - w / 2, y - size, x + w / 2, y + size), size, fill=fade(mix(INK, color, 0.25), a),
                        outline=fade(color, a), width=2)
    text(d, (x, y), label, f, fade(color, a), "mm")


def cloud(d, c, s, fill, outline):
    x, y = c
    for cx, cy, r in ((-0.55, 0.1, 0.42), (-0.1, -0.2, 0.55), (0.45, 0.0, 0.45), (0.0, 0.25, 0.45)):
        d.ellipse((x + (cx - r) * s, y + (cy - r) * s, x + (cx + r) * s, y + (cy + r) * s), fill=outline)
    for cx, cy, r in ((-0.55, 0.1, 0.39), (-0.1, -0.2, 0.52), (0.45, 0.0, 0.42), (0.0, 0.25, 0.42)):
        d.ellipse((x + (cx - r) * s, y + (cy - r) * s, x + (cx + r) * s, y + (cy + r) * s), fill=fill)


def database(d, c, w, h, color, a=1.0):
    x, y = c
    col, top = fade(mix(INK, color, 0.35), a), fade(color, a)
    d.rectangle((x - w / 2, y - h / 2, x + w / 2, y + h / 2), fill=col)
    d.ellipse((x - w / 2, y + h / 2 - 16, x + w / 2, y + h / 2 + 16), fill=col)
    d.ellipse((x - w / 2, y - h / 2 - 16, x + w / 2, y - h / 2 + 16), fill=top)


def header(d, t, number, eyebrow, title):
    a = appear(t, 0.1, 0.6)
    text(d, (120, 84), f"{number:02d}  ·  {eyebrow}", font(24, True), fade(ACCENT, a))
    text(d, (120, 118), title, font(56, True), fade(PAPER, a))


def caption(d, t, s, start=0.6):
    a = appear(t, start, 0.7)
    para(d, (W / 2, 972), s, font(32), fade(MUTED, a), 1500, anchor="ma")


# ---- scenes ----------------------------------------------------------------
# Each scene is (duration seconds, function(draw, t, duration)).

def s_title(d, t, dur):
    a = appear(t, 0.2, 0.8)
    # an orbit of small nodes around the title: the sites around HQ
    for i in range(7):
        ang = i / 7 * 2 * math.pi + t * 0.25
        c = (W / 2 + math.cos(ang) * 620, H / 2 + math.sin(ang) * 330)
        dashed(d, (W / 2, H / 2), c, fade(LINE, a * 0.8), w=2, offset=t * 40)
        dot(d, c, 14, fade(ACCENT, a))
        packet(d, c, (W / 2, H / 2), t, 2.2, fade(PAPER, a * 0.8), r=5, phase=i / 7)
    d.rounded_rectangle((W / 2 - 460, H / 2 - 170, W / 2 + 460, H / 2 + 170), 36, fill=INK)
    text(d, (W / 2, H / 2 - 110), "C I R Q E N   L A B S", font(28, True), fade(ACCENT, a), "mm")
    text(d, (W / 2, H / 2 - 10), "Equiper", font(150, True), fade(PAPER, a), "mm")
    b = appear(t, 1.0, 0.8)
    text(d, (W / 2, H / 2 + 105), "How the whole system works", font(40), fade(MUTED, b), "mm")


def s_people(d, t, dur):
    header(d, t, 1, "THE PROBLEM", "A hospital runs on equipment that must be kept safe")
    roles = [
        ("Equipment", "Pumps, monitors, ventilators… each with a history", AMBER, "eq"),
        ("Technician", "Repairs, services and calibrates; signs the work", ACCENT, "p"),
        ("In-Charge", "Reviews and approves work for a department", VIOLET, "p"),
        ("Head of Dept", "Manages users, workshops and oversight", GREEN, "p"),
    ]
    x0, cw, gap = 150, 375, 40
    for i, (name, sub, col, kind) in enumerate(roles):
        a = appear(t, 0.6 + i * 0.45, 0.6)
        if a <= 0:
            continue
        lift = (1 - a) * 40
        x = x0 + i * (cw + gap)
        y0, y1 = 300 + lift, 800 + lift
        box(d, (x, y0, x + cw, y1), a)
        cx = x + cw / 2
        if kind == "eq":
            d.rounded_rectangle((cx - 80, y0 + 90, cx + 80, y0 + 220), 16, fill=fade(mix(INK, col, 0.3), a),
                                outline=fade(col, a), width=4)
            pts = [(cx - 62 + k * 8, y0 + 155 - (40 if k % 5 == 2 else 0) + (25 if k % 5 == 3 else 0))
                   for k in range(16)]
            d.line(pts, fill=fade(col, a), width=4)
        else:
            person(d, (cx, y0 + 170), 1.3, fade(col, a))
        text(d, (cx, y0 + 300), name, font(38, True), fade(PAPER, a), "mm")
        para(d, (cx, y0 + 345), sub, font(25), fade(MUTED, a), cw - 50, anchor="ma")
    caption(d, t, "Equiper is a maintenance system (CMMS) built for hospital biomedical engineering departments.", 2.6)


def s_stack(d, t, dur):
    header(d, t, 2, "ONE SITE", "Every hospital runs its own complete copy")
    # the machine
    a = appear(t, 0.5)
    box(d, (150, 250, 1150, 900), a, fill=(20, 38, 36), outline=MUTED, r=26, w=3)
    text(d, (180, 272), "Desktop build at the site", font(24, True), fade(MUTED, a))
    layers = [
        (1.0, (190, 320, 1110, 420), "Desktop shell", "PySide6 window  ·  main.py, bulider_tools/", ACCENT),
        (1.8, (190, 445, 1110, 575), "Django app", "Inventory · Work Orders · PPM · Calibration · Parts · Reports · Users", VIOLET),
        (2.6, (190, 600, 640, 730), "PostgreSQL", "embedded local database", AMBER),
        (3.0, (660, 600, 1110, 730), "Redis", "cache and task queues", RED),
        (3.6, (190, 755, 1110, 870), "Sync agent", "sync/  ·  exchanges changes with HQ", GREEN),
    ]
    for start, xy, title, sub, col in layers:
        card(d, xy, title, sub, appear(t, start), col, title_size=30, sub_size=22)
    b = appear(t, 4.4)
    cloud(d, (1560, 560), 260, fade(PANEL, b), fade(ACCENT, b))
    text(d, (1560, 540), "HQ server", font(40, True), fade(PAPER, b), "mm")
    text(d, (1560, 590), "hq_server/", font(24), fade(MUTED, b), "mm")
    arrow(d, (1110, 812), (1380, 650), appear(t, 4.8), GREEN)
    if t > 5.4:
        packet(d, (1110, 812), (1380, 650), t, 1.4, PAPER, r=7)
        packet(d, (1380, 650), (1110, 812), t, 1.4, ACCENT, r=7, phase=0.5)
    c = appear(t, 5.6)
    text(d, (1560, 790), "Works fully offline.", font(30, True), fade(GREEN, c), "mm")
    text(d, (1560, 832), "Syncs when a connection exists.", font(26), fade(MUTED, c), "mm")
    caption(d, t, "A desktop window around a Django web app, with its own database — so the site never waits for the network.", 6.2)


def s_modules(d, t, dur):
    header(d, t, 3, "WHAT IT DOES", "Everything the department tracks, in one place")
    mods = [
        ("Inventory", "Departments, equipment, manufacturers", AMBER),
        ("Work Orders", "Repairs with signatures and remarks", ACCENT),
        ("PPM", "Planned preventive maintenance", GREEN),
        ("Calibration", "Sessions, readings, ISO/IEC 17025 certificates", VIOLET),
        ("Parts & Tools", "Spares used, accessories, requests", RED),
        ("Reports", "Report hub and machine reports", AMBER),
        ("Users & Roles", "Tech, In-Charge, HOD; signatures", ACCENT),
        ("Audit Log", "Who changed what, and when", GREEN),
    ]
    cw, ch, gx, gy = 390, 250, 36, 36
    x0 = (W - (4 * cw + 3 * gx)) / 2
    for i, (name, sub, col) in enumerate(mods):
        a = appear(t, 0.6 + i * 0.25, 0.5)
        if a <= 0:
            continue
        r, c = divmod(i, 4)
        x, y = x0 + c * (cw + gx), 300 + r * (ch + gy)
        s = (1 - a) * 20
        card(d, (x + s, y + s, x + cw - s, y + ch - s), name, sub, a, col, title_size=34, sub_size=24)
    caption(d, t, "Thirteen Django apps share one database, so a work order knows its equipment, its PPM and its parts.", 3.2)


def s_work_order(d, t, dur):
    header(d, t, 4, "WORK ORDERS", "From a fault report to a signed, approved record")
    nodes = [
        ((260, 430), "Fault", "equipment fails"),
        ((620, 430), "Work Order", "created by technician"),
        ((980, 430), "Signed", "technician signature"),
        ((1340, 430), "Review", "In-Charge decides"),
        ((1700, 430), "Approved", "record is final"),
    ]
    starts = [0.6, 1.6, 3.0, 4.4, 6.2]
    for i, ((x, y), title, sub) in enumerate(nodes):
        a = appear(t, starts[i])
        col = GREEN if i == 4 else ACCENT
        box(d, (x - 150, y - 90, x + 150, y + 90), a, fill=PANEL, outline=col if a > 0.9 else LINE, w=3)
        text(d, (x, y - 22), title, font(34, True), fade(PAPER, a), "mm")
        text(d, (x, y + 24), sub, font(23), fade(MUTED, a), "mm")
        if i < 4:
            arrow(d, (x + 152, y), (nodes[i + 1][0][0] - 158, y), appear(t, starts[i + 1] - 0.4, 0.5))
    # status chip that follows the work order
    if t > 1.9:
        if t < 6.2:
            chip(d, (620 + 360 * clamp((t - 2.2) / 3.4) ** 1.0 * (1 if t > 2.2 else 0), 560),
                 "Waiting Approval", AMBER, appear(t, 1.9))
        else:
            chip(d, (1700, 560), "Approved", GREEN, appear(t, 6.2))
    # signature scribble
    sa = clamp((t - 3.0) / 1.0)
    if sa > 0:
        pts = [(915 + k * 6.5, 482 + 10 * math.sin(k * 0.9)) for k in range(int(20 * sa) + 1)]
        if len(pts) > 1:
            d.line(pts, fill=ACCENT, width=3)
    # declined branch
    b = appear(t, 5.2)
    arrow(d, (1340, 522), (1340, 660), b, RED, a=b)
    box(d, (1200, 668, 1480, 770), b, outline=RED)
    text(d, (1340, 700), "Declined", font(30, True), fade(RED, b), "mm")
    text(d, (1340, 740), "back with remarks", font(22), fade(MUTED, b), "mm")
    if t > 6.4:
        check(d, (1700, 340), 44, GREEN)
    # consequences of approval
    c = appear(t, 7.2)
    arrow(d, (1700, 600), (1700, 660), c, GREEN, a=c)
    box(d, (1560, 668, 1840, 770), c, outline=GREEN)
    text(d, (1700, 700), "PDF + linked PPM", font(28, True), fade(PAPER, c), "mm")
    text(d, (1700, 740), "marked completed", font(22), fade(MUTED, c), "mm")
    e = appear(t, 8.2)
    para(d, (150, 680), "Spare parts used are logged against the work order; the equipment keeps a complete service history.",
         font(28), fade(PAPER, e), 950)
    caption(d, t, "Every repair is a Work Order: Waiting Approval → Approved or Declined, with signatures on the PDF.", 8.8)


def s_ppm(d, t, dur):
    header(d, t, 5, "PREVENTIVE MAINTENANCE", "Service before it breaks")
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    rows = [("Infusion pump", 3, 0), ("Patient monitor", 6, 1), ("Ventilator", 1, 0), ("Defibrillator", 4, 2),
            ("Autoclave", 2, 1)]
    gx0, gy0, cw, rh = 520, 300, 100, 92
    a = appear(t, 0.5)
    for j, m in enumerate(months):
        text(d, (gx0 + j * cw + cw / 2, gy0 - 30), m, font(24, True), fade(MUTED, a), "mm")
    for i, (name, every, off) in enumerate(rows):
        y = gy0 + i * rh + rh / 2
        ra = appear(t, 0.8 + i * 0.2)
        text(d, (gx0 - 30, y), name, font(28, True), fade(PAPER, ra), "rm")
        text(d, (gx0 - 30, y + 28), f"every {every} mo", font(20), fade(MUTED, ra), "rm")
        d.line([(gx0, y), (gx0 + 12 * cw, y)], fill=fade(LINE, ra), width=2)
    cursor = gx0 + 12 * cw * clamp((t - 2.5) / 6.5)
    for i, (name, every, off) in enumerate(rows):
        y = gy0 + i * rh + rh / 2
        for j in range(off, 12, every):
            x = gx0 + j * cw + cw / 2
            pa = appear(t, 1.4 + i * 0.2 + j * 0.03)
            if pa <= 0:
                continue
            if x < cursor:
                late = (i == 3 and j == 6)
                col = AMBER if late else GREEN
                dot(d, (x, y), 20, fade(col, pa))
                if late:
                    text(d, (x, y), "!", font(26, True), INK, "mm")
                else:
                    check(d, (x, y + 2), 20, INK, w=4)
            else:
                d.ellipse((x - 20, y - 20, x + 20, y + 20), outline=fade(ACCENT, pa), width=4)
    if t > 2.5:
        d.line([(cursor, gy0 - 55), (cursor, gy0 + 5 * rh)], fill=PAPER, width=3)
        text(d, (cursor, gy0 + 5 * rh + 26), "today", font(22, True), PAPER, "mm")
    b = appear(t, 5.0)
    chip(d, (1030, 850), "due → work order → approved → PPM completed", GREEN, b, size=24)
    caption(d, t, "Schedules are generated from each machine's interval; approving the linked work order closes the PPM.", 6.0)


def s_calibration(d, t, dur):
    header(d, t, 6, "CALIBRATION", "Measured against a standard, certified to ISO/IEC 17025")
    card(d, (120, 300, 480, 520), "Procedure", "parameters, set values, tolerances", appear(t, 0.6), VIOLET)
    card(d, (120, 560, 480, 780), "Reference standard", "traceable instrument with its own certificate",
         appear(t, 1.0), AMBER)
    arrow(d, (484, 410), (570, 470), appear(t, 1.4), VIOLET)
    arrow(d, (484, 670), (570, 600), appear(t, 1.4), AMBER)
    # session readings table
    a = appear(t, 1.6)
    box(d, (580, 280, 1180, 800), a)
    text(d, (610, 300), "Calibration session", font(30, True), fade(PAPER, a))
    cols = ("Set", "Reading", "Error", "")
    for k, c in enumerate(cols):
        text(d, (620 + k * 140, 360), c, font(22, True), fade(MUTED, a))
    readings = [("10.0", "10.1", "+1.0%", True), ("50.0", "49.8", "-0.4%", True), ("100", "100.3", "+0.3%", True),
                ("250", "256.0", "+2.4%", False), ("250", "250.4", "+0.2%", True)]
    for r, (sv, rd, err, ok) in enumerate(readings):
        ra = appear(t, 2.2 + r * 0.55, 0.3)
        y = 410 + r * 72
        if ra <= 0:
            continue
        for k, v in enumerate((sv, rd, err)):
            text(d, (620 + k * 140, y), v, mono(26), fade(PAPER, ra))
        (check if ok else cross)(d, (1060, y + 14), 26, fade(GREEN if ok else RED, ra), w=5)
        if not ok:
            text(d, (1090, y + 3), "adjust", font(20), fade(RED, ra))
    # certificate
    b = appear(t, 5.4)
    arrow(d, (1184, 540), (1300, 540), b, VIOLET)
    box(d, (1310, 300, 1780, 800), b, fill=(236, 240, 238), outline=VIOLET, r=10, w=3)
    if b > 0:
        ink = fade((30, 40, 40), b, bg=(236, 240, 238))
        text(d, (1545, 350), "CALIBRATION CERTIFICATE", font(26, True), ink, "mm")
        text(d, (1545, 385), "ISO/IEC 17025", font(22), fade((90, 100, 100), b, (236, 240, 238)), "mm")
        for k in range(6):
            d.line([(1360, 450 + k * 36), (1730 - (k % 3) * 60, 450 + k * 36)],
                   fill=fade((190, 198, 196), b, (236, 240, 238)), width=6)
    c = appear(t, 6.4)
    chip(d, (1545, 700), "No. issued by HQ", ACCENT, c, size=24)
    if t > 6.4:
        packet(d, (1545, 250), (1545, 660), t, 1.2, ACCENT, r=8)
    text(d, (1545, 230), "HQ", font(28, True), fade(ACCENT, c), "mm")
    caption(d, t, "Readings are checked against tolerances; HQ allocates certificate numbers so two sites never collide.", 7.0)


SITES = [("Referral Hospital", -150), ("County Hospital A", -90), ("County Hospital B", -30),
         ("Sub-County Hospital", 30), ("Mission Hospital", 90), ("Calibration Centre", 150)]


def s_network(d, t, dur):
    header(d, t, 7, "OFFLINE-FIRST SYNC", "Many sites, one picture at HQ")
    hq = (W / 2, 580)
    off = 4.5 <= t < 10.0
    for i, (name, deg) in enumerate(SITES):
        a = appear(t, 0.5 + i * 0.2)
        ang = math.radians(deg)
        c = (hq[0] + math.cos(ang) * 640, hq[1] + math.sin(ang) * 290)
        offline = off and i == 1
        if a > 0:
            if offline:
                dashed(d, c, hq, fade(RED, a), w=3)
                cross(d, lerp(c, hq, 0.5), 30, RED, w=6)
            else:
                d.line([c, hq], fill=fade(LINE, a), width=3)
                if t > 1.8:
                    packet(d, c, hq, t, 1.8, fade(PAPER, a), r=7, phase=i * 0.37)
                    packet(d, hq, c, t, 2.3, fade(ACCENT, a), r=7, phase=i * 0.21)
            # reconnect burst
            if i == 1 and 10.0 <= t < 12.5:
                for k in range(6):
                    u = clamp((t - 10.0) * 1.3 - k * 0.18)
                    if 0 < u < 1:
                        dot(d, lerp(c, hq, u), 8, AMBER)
            col = RED if offline else ACCENT
            box(d, (c[0] - 150, c[1] - 48, c[0] + 150, c[1] + 48), a, outline=col, w=3)
            text(d, (c[0], c[1] - 12), name, font(26, True), fade(PAPER, a), "mm")
            if i == 1 and t >= 4.5:
                queued = int(clamp((t - 4.8) / 5.0) * 14) if t < 10.0 else int(14 * (1 - clamp((t - 10.3) / 1.8)))
                label = f"offline · {queued} changes queued" if off else (
                    f"syncing · {queued} left" if queued else "back in sync")
                text(d, (c[0], c[1] + 20), label, font(20), fade(RED if off else (AMBER if queued else GREEN), a),
                     "mm")
            else:
                text(d, (c[0], c[1] + 20), "online", font(20), fade(GREEN, a), "mm")
    b = appear(t, 0.3)
    cloud(d, hq, 230, fade(PANEL_HI, b), fade(ACCENT, b))
    text(d, (hq[0], hq[1] - 10), "HQ", font(52, True), fade(PAPER, b), "mm")
    text(d, (hq[0], hq[1] + 38), "/api/sync", font(22), fade(MUTED, b), "mm")
    phases = [(0.6, 4.5, "Each site uploads its own changes and downloads everyone else's."),
              (4.5, 10.0, "A site loses its connection — work goes on locally, changes wait their turn."),
              (10.0, dur, "When it comes back, the queue drains and every site converges on the same data.")]
    for start, end, s in phases:
        if start <= t < end:
            a2 = min(appear(t, start, 0.5), clamp((end - t) / 0.3) if end < dur else 1)
            para(d, (W / 2, 972), s, font(32), fade(MUTED, a2), 1500, anchor="ma")


def s_engine(d, t, dur):
    header(d, t, 8, "INSIDE THE SYNC AGENT", "Upload, download, and what happens on a conflict")
    text(d, (120, 250), "UPLOAD", font(24, True), fade(PAPER, appear(t, 0.4)))
    up = [
        ((150, 280, 470, 420), "Local DB", "PostgreSQL", AMBER),
        ((540, 280, 920, 420), "Change poller", "discover_recent_changes", ACCENT),
        ((990, 280, 1370, 420), "upload_batch", "idempotent · backpressure", ACCENT),
        ((1440, 280, 1780, 420), "HQ", "/api/sync", GREEN),
    ]
    down = [
        ((1440, 520, 1780, 660), "HQ", "other sites' changes", GREEN),
        ((990, 520, 1370, 660), "Conflict resolver", "last-write-wins, deterministic", VIOLET),
        ((540, 520, 920, 660), "Apply locally", "apply_remote_update_locally", ACCENT),
        ((150, 520, 470, 660), "Local DB", "converged", AMBER),
    ]
    for k, (xy, ti, su, col) in enumerate(up):
        card(d, xy, ti, su, appear(t, 0.6 + k * 0.4), col, title_size=30, sub_size=21)
        if k:
            p, q = (up[k - 1][0][2] + 4, 350), (xy[0] - 6, 350)
            arrow(d, p, q, appear(t, 0.4 + k * 0.4))
            if t > 2.4:
                packet(d, p, q, t, 1.0, PAPER, r=6, phase=k * 0.3)
    text(d, (120, 490), "DOWNLOAD", font(24, True), fade(PAPER, appear(t, 2.4)))
    for k, (xy, ti, su, col) in enumerate(down):
        card(d, xy, ti, su, appear(t, 2.6 + k * 0.4), col, title_size=30, sub_size=21)
        if k:
            p, q = (down[k - 1][0][0] - 4, 590), (xy[2] + 6, 590)
            arrow(d, p, q, appear(t, 2.4 + k * 0.4))
            if t > 4.4:
                packet(d, p, q, t, 1.0, ACCENT, r=6, phase=k * 0.3)
    # side modules
    side = [
        ((990, 740, 1370, 860), "Quarantine", "losers kept in sync_conflicts, never dropped", RED, (1180, 664), 5.2),
        ((540, 740, 920, 860), "Parent recovery", "fetch missing FK parents from HQ", AMBER, (730, 664), 6.2),
        ((150, 740, 470, 860), "Schema guard", "records drift in sync_schema_drift", VIOLET, (310, 664), 7.0),
    ]
    for xy, ti, su, col, anchor, st in side:
        a = appear(t, st)
        arrow(d, anchor, ((xy[0] + xy[2]) / 2, xy[1] - 6), a, col, a=a)
        card(d, xy, ti, su, a, col, title_size=28, sub_size=20)
    caption(d, t, "Changes are found by timestamp, sent in idempotent batches, and every site resolves conflicts the same way.", 7.8)


def s_updates(d, t, dur):
    header(d, t, 9, "SOFTWARE UPDATES", "Only packages signed by HQ are installed")
    card(d, (120, 330, 480, 560), "Update package", "published on the HQ update server", appear(t, 0.5), ACCENT)
    a = appear(t, 1.0)
    chip(d, (300, 610), "Ed25519 signature", VIOLET, a, size=24)
    steps = [("Verify", "signature checked", GREEN, 2.4), ("Snapshot", "database backed up", AMBER, 3.4),
             ("Apply", "new version installed", ACCENT, 4.4), ("Reload", "app restarts itself", GREEN, 5.4)]
    x0 = 640
    for k, (ti, su, col, st) in enumerate(steps):
        x = x0 + k * 300
        card(d, (x, 360, x + 250, 530), ti, su, appear(t, st), col, title_size=32, sub_size=21)
        arrow(d, ((x - 44) if k else 486, 445), (x - 6, 445), appear(t, st - 0.4), col)
    if 1.8 < t < 5.8:
        u = clamp((t - 1.8) / 4.0)
        dot(d, (486 + (x0 + 3 * 300 + 125 - 486) * u, 445), 12, PAPER)
    if t > 2.8:
        check(d, (765, 590), 40, GREEN)
    # forged package rejected
    b = appear(t, 6.2)
    card(d, (120, 700, 480, 860), "Forged package", "no valid signature", b, RED)
    arrow(d, (484, 780), (700, 780), b, RED, a=b)
    if b > 0:
        cross(d, (765, 780), 60, fade(RED, b), w=10)
        text(d, (830, 780), "refused — nothing is changed", font(32, True), fade(RED, b), "lm")
    caption(d, t, "Updates are verified before anything is touched, and the database is snapshotted first.", 7.0)


def s_config(d, t, dur):
    header(d, t, 10, "CONFIGURATION", "Where does a site find HQ?")
    layers = [("Environment variable", "SYNC_API_URL, HQ_SERVER_URL, POSTGRES_HQ_*", False),
              ("config.json", "in the data directory, created on first run", True),
              ("provisioning.json", "shipped with the installer for this site", True),
              ("Built-in default", "HQ_ENDPOINT_DEFAULTS in config.py — the one place a host is named", True)]
    winner = 1
    scan = clamp((t - 2.6) / 2.4) * (winner + 0.99)
    for k, (ti, su, is_set) in enumerate(layers):
        a = appear(t, 0.6 + k * 0.35)
        y = 280 + k * 150
        active = t > 2.6 and int(scan) == k
        chosen = t > 5.0 and k == winner
        outline = GREEN if chosen else (PAPER if active else LINE)
        box(d, (360, y, 1560, y + 120), a, outline=outline, w=4 if (active or chosen) else 2)
        text(d, (400, y + 40), ti, font(34, True), fade(PAPER, a), "lm")
        text(d, (400, y + 84), su, font(24), fade(MUTED, a), "lm")
        text(d, (300, y + 60), str(k + 1), font(44, True), fade(ACCENT, a), "mm")
        if t > 2.6 and k <= min(int(scan), winner) and k < winner and not is_set:
            text(d, (1520, y + 60), "not set", font(24, True), fade(MUTED, a), "rm")
        if chosen:
            chip(d, (1440, y + 60), "used", GREEN, appear(t, 5.0), size=24)
    caption(d, t, "Each setting is resolved env → config.json → provisioning → default, and validate_config() rejects bad addresses.", 5.6)


def s_recap(d, t, dur):
    header(d, t, 11, "IN SHORT", "Equiper, end to end")
    items = [("Local first", "each hospital runs a full desktop build with its own PostgreSQL", ACCENT),
             ("Complete workflow", "inventory, work orders, PPM, calibration, parts, reports", VIOLET),
             ("Accountable", "roles, signatures, approvals and an audit log", AMBER),
             ("Converges at HQ", "offline sync, deterministic conflicts, nothing silently lost", GREEN),
             ("Safe to update", "signed packages, snapshot before apply", RED)]
    for k, (ti, su, col) in enumerate(items):
        a = appear(t, 0.6 + k * 0.45)
        y = 280 + k * 120
        dot(d, (230, y + 40), 16, fade(col, a))
        text(d, (280, y + 22), ti, font(40, True), fade(PAPER, a))
        text(d, (720, y + 30), su, font(30), fade(MUTED, a))
    b = appear(t, 3.8)
    text(d, (W / 2, 950), "Cirqen Labs  ·  Equiper", font(34, True), fade(ACCENT, b), "mm")


SCENES = [
    (5.0, s_title), (8.0, s_people), (11.0, s_stack), (8.5, s_modules), (13.5, s_work_order),
    (11.0, s_ppm), (12.0, s_calibration), (15.0, s_network), (13.0, s_engine), (10.0, s_updates),
    (9.0, s_config), (7.5, s_recap),
]


def render(idx, t):
    dur, fn = SCENES[idx]
    img = Image.new("RGB", (W, H), INK)
    d = ImageDraw.Draw(img)
    fn(d, t, dur)
    a = min(clamp(t / FADE), clamp((dur - t) / FADE))
    if a < 1:
        img = Image.blend(Image.new("RGB", (W, H), INK), img, a)
    return img


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found: install it or `pip install imageio-ffmpeg`")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--still", type=int, help="write a PNG of scene N (1-based) instead of the video")
    ap.add_argument("--at", type=float, default=None, help="time within the scene for --still (default: near end)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.still:
        dur = SCENES[args.still - 1][0]
        t = args.at if args.at is not None else dur - FADE - 0.05
        path = args.out.with_name(f"explainer_scene{args.still:02d}.png")
        render(args.still - 1, t).save(path)
        print(path)
        return

    total = sum(s[0] for s in SCENES)
    cmd = [ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium",
           "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(args.out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    done = 0
    for idx, (dur, _) in enumerate(SCENES):
        for f in range(int(round(dur * FPS))):
            proc.stdin.write(render(idx, f / FPS).tobytes())
            done += 1
        print(f"scene {idx + 1}/{len(SCENES)} done ({done / FPS:.0f}s of {total:.0f}s)", flush=True)
    proc.stdin.close()
    if proc.wait():
        sys.exit("ffmpeg failed")
    print(args.out)


if __name__ == "__main__":
    main()
