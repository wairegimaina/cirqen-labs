#!/usr/bin/env python3
"""Render the Cirqen marketing video: narrated, illustrated, for the website.

Every scene is drawn from scratch (no screenshots, no technical detail) and is
timed to a synthesised voice-over, sentence by sentence, with a soft music bed
and burned-in subtitles so it also works when a site autoplays it muted.

    python helper_scripts/build_marketing_video.py              # full video
    python helper_scripts/build_marketing_video.py --still 8    # PNG of scene 8

Output: data/Cirqen_Marketing.mp4 (1080p, 30 fps, AAC audio).

Needs Pillow, numpy, soundfile, kokoro-onnx and ffmpeg (on PATH or through
imageio-ffmpeg). The Kokoro voice model is two files, kokoro-v1.0.onnx and
voices-v1.0.bin, from the kokoro-onnx GitHub releases (model-files-v1.0); point
CIRQEN_TTS_DIR at the folder holding them. CIRQEN_FONT_DIR may point at a
folder with Inter-*.ttf; Liberation Sans is used otherwise.
"""
import argparse
import hashlib
import math
import os
import pickle
import random
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "data" / "marketing_build"
OUT = ROOT / "data" / "Cirqen_Marketing.mp4"
TTS_DIR = Path(os.environ.get("CIRQEN_TTS_DIR", "/tmp/claude-0/tts"))
LOGO_WIDE = ROOT / "static" / "images" / "white.png"          # full logo, for dark backgrounds
LOGO_DARK = ROOT / "static" / "images" / "equiper-logo.png"   # full logo, for light backgrounds
LOGO_ICON = ROOT / "static" / "images" / "logo-1024x1024.png"  # the CQ app icon
FONT_DIR = Path(os.environ.get("CIRQEN_FONT_DIR", "/tmp/claude-0/tts/fonts/inter/extras/ttf"))

W, H = 1920, 1080
S = 2                      # supersampling factor; frames are drawn at 2x and scaled down
FPS = 30
SR = 24000
VOICE = "af_heart"
LEAD_IN, GAP, TAIL = 0.7, 0.35, 1.0

BG = (10, 28, 38)
BG_SOFT = (16, 40, 52)
CARD = (22, 50, 63)
CARD_HI = (30, 64, 79)
LINE = (54, 90, 104)
WHITE = (247, 250, 250)
PAPER = (244, 246, 243)
INKDOC = (28, 42, 48)
GREYDOC = (196, 204, 204)
MUTED = (150, 172, 178)
TEAL = (20, 184, 80)           # Cirqen brand green, sampled from the logo
SKY = (96, 176, 240)
AMBER = (246, 184, 76)
CORAL = (240, 108, 96)
GREEN = (98, 208, 132)
LILAC = (174, 150, 240)


# ---- the script ----------------------------------------------------------
# (display text, spoken text or None). The brand is spoken as "Sirken".

NARRATION = {
    "hook": [
        ("In a busy hospital, every infusion pump, monitor and ventilator has to be ready the moment a patient needs it.", None),
        ("Tracking all of it on paper is slow, and things get missed.", None),
    ],
    "brand": [
        ("Meet Cirqen: one system for your whole biomedical engineering department.", "Meet Sirken: one system for your whole biomedical engineering department."),
    ],
    "hospital": [
        ("Cirqen knows your hospital the way you do.", "Sirken knows your hospital the way you do."),
        ("The Renal Unit, the ICU, Ward 7, the Operating Theatre, Maternity and Radiology, each with every machine registered to it.", "The Renal Unit, the I C U, Ward Seven, the Operating Theatre, Maternity and Radiology, each with every machine registered to it."),
        ("Every machine keeps its manufacturer, its location and its complete service history.", None),
    ],
    "people": [
        ("Your workshops and your people are in it too.", None),
        ("Technicians work from their own workshop: Renal, ICU, Theatre, the Main Workshop, and the Calibration Centre.", "Technicians work from their own workshop: Renal, I C U, Theatre, the Main Workshop, and the Calibration Centre."),
        ("In-charges approve the work for their departments, and the head of department sees everything.", None),
    ],
    "workorder": [
        ("When a dialysis machine fails, the technician opens a work order in seconds.", None),
        ("The fault, the work done, the spare parts used, and any remarks.", None),
        ("They sign it on screen, and it goes straight to the in-charge, who approves it or sends it back.", None),
        ("The signed work order is ready to print, and the machine's history updates itself.", None),
    ],
    "ppm": [
        ("Preventive maintenance runs on schedule.", None),
        ("Cirqen plans every machine's service for the year, and shows your team exactly what is due each month.", "Sirken plans every machine's service for the year, and shows your team exactly what is due each month."),
        ("Each task closes itself when its work order is approved, and nothing overdue slips by unnoticed.", None),
    ],
    "calibration": [
        ("Calibration is built in.", None),
        ("The technician follows the procedure for the device and records each reading against a traceable reference standard.", None),
        ("Cirqen works out the error, the uncertainty and the verdict. If even one point is out of tolerance, the device does not pass.", "Sirken works out the error, the uncertainty and the verdict. If even one point is out of tolerance, the device does not pass."),
        ("Adjust it, test again, and only then is it cleared for use.", None),
    ],
    "certificate": [
        ("Once a reviewer approves the session, the calibration certificate is generated for you.", None),
        ("The results and their uncertainty, the reference standards used, drift compared with earlier calibrations, the signatures, and a QR code for quick verification.", None),
        ("Every certificate receives its own unique number, issued centrally, so no two can ever clash.", None),
    ],
    "offline": [
        ("And Cirqen keeps working when the network does not.", "And Sirken keeps working when the network does not."),
        ("Each workshop runs Cirqen on its own computer.", "Each workshop runs Sirken on its own computer."),
        ("If the connection drops in the Theatre Workshop, the team carries on: opening work orders, recording calibrations, signing off jobs.", None),
        ("When the connection returns, everything catches up on its own. Certificates approved offline receive their numbers, and every workshop sees the same picture.", None),
    ],
    "trust": [
        ("Reports for management are a click away.", None),
        ("Every change is recorded in an audit trail, so you always know who did what, and when.", None),
        ("And updates arrive securely, checked before they are ever installed.", None),
    ],
    "close": [
        ("Cirqen. Every machine ready, every record signed, across your whole hospital.", "Sirken. Every machine ready, every record signed, across your whole hospital."),
        ("Book a demo today.", None),
    ],
}
ORDER = ["hook", "brand", "hospital", "people", "workorder", "ppm", "calibration", "certificate", "offline",
         "trust", "close"]


# ---- fonts and drawing ---------------------------------------------------

_fonts = {}


def font(size, weight="Regular"):
    key = (size, weight)
    if key not in _fonts:
        inter = FONT_DIR / f"Inter-{weight}.ttf"
        if inter.exists():
            _fonts[key] = ImageFont.truetype(str(inter), int(size * S))
        else:
            lib = "Bold" if weight in ("Bold", "SemiBold", "ExtraBold") else "Regular"
            _fonts[key] = ImageFont.truetype(f"/usr/share/fonts/truetype/liberation/LiberationSans-{lib}.ttf",
                                             int(size * S))
    return _fonts[key]


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def ease(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def ease_io(x):
    x = clamp(x)
    return 4 * x * x * x if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def appear(t, start, length=0.6):
    return ease((t - start) / length)


def mix(c1, c2, a):
    a = clamp(a)
    return tuple(int(c1[i] + (c2[i] - c1[i]) * a) for i in range(3))


def lerp(p, q, a):
    return (p[0] + (q[0] - p[0]) * a, p[1] + (q[1] - p[1]) * a)


class Canvas:
    """ImageDraw in 1920x1080 coordinates, drawn at S times the size.

    Every colour takes an opacity `a` that blends it into `bg` (the surface it
    sits on), which keeps fades cheap without RGBA compositing.
    """

    def __init__(self, img):
        self.img = img
        self.d = ImageDraw.Draw(img)

    @staticmethod
    def _xy(xy):
        return [v * S for v in xy]

    def rect(self, xy, r=0, fill=None, outline=None, w=2, a=1.0, bg=BG):
        if a <= 0:
            return
        self.d.rounded_rectangle(self._xy(xy), r * S, fill=mix(bg, fill, a) if fill else None,
                                 outline=mix(bg, outline, a) if outline else None, width=int(w * S))

    def ellipse(self, xy, fill=None, outline=None, w=2, a=1.0, bg=BG):
        if a <= 0:
            return
        self.d.ellipse(self._xy(xy), fill=mix(bg, fill, a) if fill else None,
                       outline=mix(bg, outline, a) if outline else None, width=int(w * S))

    def circle(self, c, r, fill=None, outline=None, w=2, a=1.0, bg=BG):
        self.ellipse((c[0] - r, c[1] - r, c[0] + r, c[1] + r), fill, outline, w, a, bg)

    def line(self, pts, fill, w=3, a=1.0, bg=BG):
        if a <= 0 or len(pts) < 2:
            return
        self.d.line([(x * S, y * S) for x, y in pts], fill=mix(bg, fill, a), width=int(w * S), joint="curve")

    def poly(self, pts, fill, a=1.0, bg=BG):
        if a <= 0:
            return
        self.d.polygon([(x * S, y * S) for x, y in pts], fill=mix(bg, fill, a))

    def arc(self, xy, start, end, fill, w=4, a=1.0, bg=BG):
        if a <= 0:
            return
        self.d.arc(self._xy(xy), start, end, fill=mix(bg, fill, a), width=int(w * S))

    def text(self, xy, s, size, fill, weight="Regular", anchor="la", a=1.0, bg=BG):
        if a <= 0:
            return
        self.d.text((xy[0] * S, xy[1] * S), s, font=font(size, weight), fill=mix(bg, fill, a), anchor=anchor)

    def textlen(self, s, size, weight="Regular"):
        return self.d.textlength(s, font=font(size, weight)) / S

    def wrap(self, s, size, width, weight="Regular"):
        lines, cur = [], ""
        for word in s.split():
            trial = (cur + " " + word).strip()
            if self.textlen(trial, size, weight) <= width:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        if cur:
            lines.append(cur)
        return lines

    def para(self, xy, s, size, fill, width, weight="Regular", gap=1.3, anchor="la", a=1.0, bg=BG):
        x, y = xy
        for ln in self.wrap(s, size, width, weight):
            self.text((x, y), ln, size, fill, weight, anchor, a, bg)
            y += size * gap
        return y


# ---- illustration kit ----------------------------------------------------

def check(c, xy, size, col, w=6, a=1.0, bg=BG, prog=1.0):
    x, y = xy
    p1, p2, p3 = (x - size * 0.5, y), (x - size * 0.12, y + size * 0.4), (x + size * 0.55, y - size * 0.45)
    prog = clamp(prog)
    if prog <= 0:
        return
    if prog < 0.4:
        c.line([p1, lerp(p1, p2, prog / 0.4)], col, w, a, bg)
    else:
        c.line([p1, p2, lerp(p2, p3, (prog - 0.4) / 0.6)], col, w, a, bg)


def cross(c, xy, size, col, w=6, a=1.0, bg=BG):
    x, y = xy
    s = size / 2
    c.line([(x - s, y - s), (x + s, y + s)], col, w, a, bg)
    c.line([(x - s, y + s), (x + s, y - s)], col, w, a, bg)


def badge(c, xy, r, col, kind="check", a=1.0, bg=BG):
    c.circle(xy, r, fill=col, a=a, bg=bg)
    if kind == "check":
        check(c, (xy[0], xy[1] + r * 0.05), r * 1.0, WHITE, max(3, r * 0.2), a, bg)
    elif kind == "cross":
        cross(c, xy, r * 0.8, WHITE, max(3, r * 0.2), a, bg)
    else:
        c.text(xy, "!", r * 1.3, WHITE, "Bold", "mm", a, bg)


def pill(c, xy, label, col, a=1.0, size=24, solid=False, bg=BG):
    if a <= 0:
        return
    w = c.textlen(label, size, "SemiBold") + size * 1.6
    x, y = xy
    h = size * 0.95
    if solid:
        c.rect((x - w / 2, y - h, x + w / 2, y + h), h, fill=col, a=a, bg=bg)
        c.text((x, y), label, size, WHITE, "SemiBold", "mm", a, bg)
    else:
        c.rect((x - w / 2, y - h, x + w / 2, y + h), h, fill=mix(bg, col, 0.18), outline=col, w=2, a=a, bg=bg)
        c.text((x, y), label, size, col, "SemiBold", "mm", a, bg)


def person(c, xy, s, col, a=1.0, bg=BG):
    x, y = xy
    c.circle((x, y - 26 * s), 20 * s, fill=col, a=a, bg=bg)
    c.rect((x - 34 * s, y, x + 34 * s, y + 52 * s), 24 * s, fill=col, a=a, bg=bg)


def ecg(c, x0, y, width, col, w=4, a=1.0, bg=BG, prog=1.0, amp=1.0):
    pts = []
    n = 48
    for k in range(int(n * clamp(prog)) + 1):
        u = k / n
        ph = (u * 3) % 1.0
        dy = 0
        if 0.40 < ph < 0.45:
            dy = -34 * amp
        elif 0.45 <= ph < 0.50:
            dy = 26 * amp
        elif 0.60 < ph < 0.70:
            dy = -8 * amp
        pts.append((x0 + u * width, y + dy))
    c.line(pts, col, w, a, bg)


def icon(c, kind, xy, s, col, a=1.0, bg=BG):
    """Simple medical-equipment glyphs, roughly 100*s wide."""
    x, y = xy
    dark = mix(bg, col, 0.22)
    if kind == "dialysis":
        c.rect((x - 32 * s, y - 48 * s, x + 32 * s, y + 48 * s), 10 * s, fill=dark, outline=col, w=4 * s, a=a, bg=bg)
        c.rect((x - 22 * s, y - 38 * s, x + 22 * s, y - 12 * s), 4 * s, fill=col, a=a, bg=bg)
        c.circle((x - 10 * s, y + 12 * s), 9 * s, outline=col, w=4 * s, a=a, bg=bg)
        c.circle((x + 12 * s, y + 12 * s), 9 * s, outline=col, w=4 * s, a=a, bg=bg)
        c.line([(x + 32 * s, y - 20 * s), (x + 52 * s, y - 20 * s), (x + 52 * s, y + 40 * s)], col, 4 * s, a, bg)
    elif kind == "monitor":
        c.rect((x - 50 * s, y - 38 * s, x + 50 * s, y + 30 * s), 10 * s, fill=dark, outline=col, w=4 * s, a=a, bg=bg)
        ecg(c, x - 40 * s, y - 4 * s, 80 * s, col, 4 * s, a, bg, amp=0.7 * s)
        c.line([(x, y + 30 * s), (x, y + 46 * s)], col, 4 * s, a, bg)
        c.line([(x - 22 * s, y + 46 * s), (x + 22 * s, y + 46 * s)], col, 4 * s, a, bg)
    elif kind == "bed":
        c.line([(x - 52 * s, y - 30 * s), (x - 52 * s, y + 36 * s)], col, 5 * s, a, bg)
        c.line([(x + 52 * s, y + 2 * s), (x + 52 * s, y + 36 * s)], col, 5 * s, a, bg)
        c.rect((x - 52 * s, y + 2 * s, x + 52 * s, y + 18 * s), 4 * s, fill=col, a=a, bg=bg)
        c.circle((x - 30 * s, y - 10 * s), 11 * s, fill=col, a=a, bg=bg)
        c.rect((x - 14 * s, y - 16 * s, x + 50 * s, y + 2 * s), 8 * s, fill=dark, outline=col, w=3 * s, a=a, bg=bg)
    elif kind == "lamp":
        c.line([(x, y - 50 * s), (x, y - 22 * s)], col, 5 * s, a, bg)
        c.poly([(x - 48 * s, y + 8 * s), (x - 24 * s, y - 24 * s), (x + 24 * s, y - 24 * s), (x + 48 * s, y + 8 * s)],
               col, a, bg)
        for k in (-1, 0, 1):
            c.line([(x + k * 22 * s, y + 18 * s), (x + k * 30 * s, y + 44 * s)], mix(bg, col, 0.6), 3 * s, a, bg)
    elif kind == "incubator":
        c.rect((x - 50 * s, y + 10 * s, x + 50 * s, y + 30 * s), 6 * s, fill=col, a=a, bg=bg)
        c.arc((x - 46 * s, y - 40 * s, x + 46 * s, y + 50 * s), 180, 360, col, 4 * s, a, bg)
        c.line([(x - 46 * s, y + 5 * s), (x + 46 * s, y + 5 * s)], col, 4 * s, a, bg)
        c.circle((x - 8 * s, y - 6 * s), 9 * s, fill=col, a=a, bg=bg)
        c.line([(x - 30 * s, y + 30 * s), (x - 30 * s, y + 48 * s)], col, 4 * s, a, bg)
        c.line([(x + 30 * s, y + 30 * s), (x + 30 * s, y + 48 * s)], col, 4 * s, a, bg)
    elif kind == "xray":
        c.rect((x - 40 * s, y - 48 * s, x + 40 * s, y + 48 * s), 8 * s, fill=dark, outline=col, w=4 * s, a=a, bg=bg)
        c.line([(x, y - 34 * s), (x, y + 34 * s)], col, 4 * s, a, bg)
        for k in range(4):
            yy = y - 24 * s + k * 16 * s
            c.arc((x - 28 * s, yy - 8 * s, x - 2 * s, yy + 14 * s), 180, 300, col, 3 * s, a, bg)
            c.arc((x + 2 * s, yy - 8 * s, x + 28 * s, yy + 14 * s), 240, 360, col, 3 * s, a, bg)
    elif kind == "pump":
        c.rect((x - 34 * s, y - 40 * s, x + 34 * s, y + 44 * s), 10 * s, fill=dark, outline=col, w=4 * s, a=a, bg=bg)
        c.rect((x - 24 * s, y - 30 * s, x + 24 * s, y - 8 * s), 4 * s, fill=col, a=a, bg=bg)
        for k in range(3):
            c.circle((x - 16 * s + k * 16 * s, y + 12 * s), 5 * s, fill=col, a=a, bg=bg)
        c.line([(x, y - 40 * s), (x, y - 56 * s), (x + 30 * s, y - 56 * s)], col, 3 * s, a, bg)
    elif kind == "vent":
        c.rect((x - 40 * s, y - 30 * s, x + 40 * s, y + 44 * s), 10 * s, fill=dark, outline=col, w=4 * s, a=a, bg=bg)
        c.circle((x, y + 8 * s), 18 * s, outline=col, w=4 * s, a=a, bg=bg)
        c.line([(x + 40 * s, y - 10 * s), (x + 58 * s, y - 10 * s), (x + 58 * s, y - 44 * s), (x + 30 * s, y - 44 * s)],
               col, 4 * s, a, bg)
    elif kind == "wrench":
        c.line([(x - 30 * s, y + 30 * s), (x + 16 * s, y - 16 * s)], col, 12 * s, a, bg)
        c.circle((x + 22 * s, y - 22 * s), 20 * s, fill=col, a=a, bg=bg)
        c.circle((x + 30 * s, y - 30 * s), 9 * s, fill=bg, a=1.0, bg=bg)
    elif kind == "analyser":
        c.rect((x - 46 * s, y - 34 * s, x + 46 * s, y + 34 * s), 10 * s, fill=dark, outline=col, w=4 * s, a=a, bg=bg)
        c.rect((x - 36 * s, y - 24 * s, x + 8 * s, y + 4 * s), 4 * s, fill=col, a=a, bg=bg)
        c.circle((x + 26 * s, y - 10 * s), 9 * s, outline=col, w=4 * s, a=a, bg=bg)
        c.line([(x - 36 * s, y + 20 * s), (x + 36 * s, y + 20 * s)], col, 4 * s, a, bg)


def computer(c, xy, s, col, a=1.0, bg=BG, screen=None):
    x, y = xy
    c.rect((x - 60 * s, y - 42 * s, x + 60 * s, y + 30 * s), 8 * s, fill=screen or mix(bg, col, 0.2), outline=col,
           w=4 * s, a=a, bg=bg)
    c.poly([(x - 16 * s, y + 30 * s), (x + 16 * s, y + 30 * s), (x + 24 * s, y + 46 * s), (x - 24 * s, y + 46 * s)],
           col, a, bg)


def cloud(c, xy, s, fill, outline, a=1.0, bg=BG):
    x, y = xy
    parts = ((-0.55, 0.12, 0.40), (-0.12, -0.18, 0.52), (0.42, 0.02, 0.44), (0.0, 0.26, 0.42))
    for cx, cy, r in parts:
        c.ellipse((x + (cx - r) * s, y + (cy - r) * s, x + (cx + r) * s, y + (cy + r) * s), fill=outline, a=a, bg=bg)
    for cx, cy, r in parts:
        r2 = r - 0.035
        c.ellipse((x + (cx - r2) * s, y + (cy - r2) * s, x + (cx + r2) * s, y + (cy + r2) * s), fill=fill, a=a, bg=bg)


def signature(c, x0, y, width, col, prog, w=3, a=1.0, bg=BG, seed=1):
    n = 36
    rnd = random.Random(seed)
    amps = [rnd.uniform(6, 16) for _ in range(n + 1)]
    pts = []
    for k in range(int(n * clamp(prog)) + 1):
        u = k / n
        pts.append((x0 + u * width, y + math.sin(u * 17 + seed) * amps[k] * (1 - u * 0.5)))
    c.line(pts, col, w, a, bg)


def qr(c, xy, size, col, a=1.0, bg=PAPER, prog=1.0, seed=7):
    n = 21
    cell = size / n
    x0, y0 = xy
    rnd = random.Random(seed)
    cells = [(i, j) for i in range(n) for j in range(n)]
    rnd.shuffle(cells)
    shown = set(cells[: int(len(cells) * clamp(prog))])
    for i in range(n):
        for j in range(n):
            finder = (i < 7 and j < 7) or (i < 7 and j >= n - 7) or (i >= n - 7 and j < 7)
            if finder:
                continue
            if (i, j) in shown and rnd.random() < 0.5:
                c.rect((x0 + i * cell, y0 + j * cell, x0 + (i + 1) * cell, y0 + (j + 1) * cell), 0, fill=col, a=a,
                       bg=bg)
    if prog > 0:
        for fi, fj in ((0, 0), (n - 7, 0), (0, n - 7)):
            fx, fy = x0 + fi * cell, y0 + fj * cell
            c.rect((fx, fy, fx + 7 * cell, fy + 7 * cell), 0, fill=col, a=a, bg=bg)
            c.rect((fx + cell, fy + cell, fx + 6 * cell, fy + 6 * cell), 0, fill=bg, a=1, bg=bg)
            c.rect((fx + 2 * cell, fy + 2 * cell, fx + 5 * cell, fy + 5 * cell), 0, fill=col, a=a, bg=bg)


_logo_cache = {}


def _brand(path, height):
    key = (path, height)
    if key not in _logo_cache:
        im = Image.open(path).convert("RGBA")
        im = im.crop(im.getbbox())
        w = round(im.width * height * S / im.height)
        _logo_cache[key] = im.resize((w, round(height * S)), Image.LANCZOS)
    return _logo_cache[key]


def logo(c, path, xy, height, a=1.0):
    """Paste one of the Cirqen logo files centred on xy, faded to opacity a."""
    if a <= 0:
        return
    im = _brand(path, height)
    if a < 1:
        im = im.copy()
        im.putalpha(im.getchannel("A").point(lambda v: int(v * a)))
    c.img.paste(im, (round(xy[0] * S - im.width / 2), round(xy[1] * S - im.height / 2)), im)


# ---- scene frame ----------------------------------------------------------

def headline(c, t, eyebrow, title, start=0.2):
    a = appear(t, start, 0.7)
    c.text((120, 86), eyebrow.upper(), 22, TEAL, "SemiBold", a=a)
    c.text((120 - 20 * (1 - a), 118), title, 54, WHITE, "Bold", a=a)


def dept_tile(c, xy, w, h, name, kind, col, a, bg=BG, alert=0.0, t=0.0):
    x, y = xy
    fill = CARD
    c.rect((x, y, x + w, y + h), 22, fill=fill, outline=mix(LINE, CORAL, alert), w=2 + 3 * alert, a=a, bg=bg)
    icon(c, kind, (x + w / 2, y + h * 0.43), min(w, h) / 230, col, a, CARD)
    c.text((x + w / 2, y + h - 34), name, 26, WHITE, "SemiBold", "mm", a, CARD)
    if alert > 0:
        pulse = 0.5 + 0.5 * math.sin(t * 8)
        badge(c, (x + w - 30, y + 30), 16 + 3 * pulse, CORAL, "!", alert, CARD)


DEPTS = [("Renal Unit", "dialysis", TEAL), ("ICU", "monitor", CORAL), ("Ward 7", "bed", SKY),
         ("Operating Theatre", "lamp", AMBER), ("Maternity", "incubator", LILAC), ("Radiology", "xray", GREEN)]
WORKSHOPS = [("Renal Workshop", TEAL), ("ICU Workshop", CORAL), ("Theatre Workshop", AMBER),
             ("Main Workshop", SKY), ("Calibration Centre", LILAC)]


# ---- scenes ---------------------------------------------------------------
# Each scene function gets (canvas, t, dur, beats) where beats[i] is the time
# the i-th sentence starts, so drawing stays in step with the voice.

def s_hook(c, t, dur, b):
    # the hospital
    a = appear(t, 0.2, 0.9)
    hx, hy = 960, 560
    c.rect((hx - 230, hy - 170, hx + 230, hy + 210), 18, fill=CARD, outline=LINE, a=a)
    c.rect((hx - 110, hy - 250, hx + 110, hy - 150), 14, fill=CARD_HI, outline=LINE, a=a)
    c.rect((hx - 16, hy - 236, hx + 16, hy - 164), 4, fill=CORAL, a=a, bg=CARD_HI)
    c.rect((hx - 36, hy - 216, hx + 36, hy - 184), 4, fill=CORAL, a=a, bg=CARD_HI)
    for r in range(3):
        for k in range(5):
            if r == 2 and k == 2:
                continue
            lit = (r * 5 + k) % 3 != 0
            c.rect((hx - 190 + k * 78, hy - 130 + r * 90, hx - 140 + k * 78, hy - 80 + r * 90), 6,
                   fill=mix(CARD, AMBER, 0.55 if lit else 0.12), a=a, bg=CARD)
    c.rect((hx - 44, hy + 110, hx + 44, hy + 210), 8, fill=CARD_HI, a=a, bg=CARD)
    # equipment orbit
    kinds = [("pump", TEAL), ("monitor", CORAL), ("vent", SKY), ("dialysis", GREEN), ("incubator", LILAC),
             ("lamp", AMBER)]
    for i, (k, col) in enumerate(kinds):
        ea = appear(t, b[0] + 0.8 + i * 0.35, 0.5)
        side, row = (-1 if i < 3 else 1), i % 3
        p = (hx + side * (430 + (row == 1) * 60), hy - 170 + row * 230)
        bob = math.sin(t * 1.6 + i) * 6
        c.circle((p[0], p[1] + bob), 78, fill=BG_SOFT, outline=LINE, a=ea)
        icon(c, k, (p[0], p[1] + bob), 0.85, col, ea, BG_SOFT)
        missed = i in (1, 4) and t > b[1] + 1.4
        if missed:
            badge(c, (p[0] + 52, p[1] - 52 + bob), 20, AMBER, "!", appear(t, b[1] + 1.4 + (i == 4) * 0.5, 0.4))
    # paper pile
    pa = appear(t, b[1], 0.6)
    for k in range(6):
        off = k * 12
        tilt = (k % 2) * 8
        c.rect((1640 - tilt, 800 - off, 1820 - tilt, 862 - off), 6, fill=mix(BG, PAPER, 0.85), outline=GREYDOC, w=2,
               a=pa * appear(t, b[1] + k * 0.12, 0.3))


def s_brand(c, t, dur, b):
    a = appear(t, 0.3, 1.2)
    logo(c, LOGO_WIDE, (960, 470 + 30 * (1 - a)), 300, a)


def s_hospital(c, t, dur, b):
    headline(c, t, "Your hospital", "Every department. Every machine.")
    tw, th, gx, gy = 330, 250, 28, 28
    x0, y0 = 120, 240
    for i, (name, kind, col) in enumerate(DEPTS):
        r, k = divmod(i, 3)
        a = appear(t, b[1] + 0.3 + i * 0.75, 0.5)
        dept_tile(c, (x0 + k * (tw + gx), y0 + r * (th + gy)), tw, th, name, kind, col, a)
        # machine dots registering into the department
        for m in range(5):
            ma = appear(t, b[1] + 0.8 + i * 0.75 + m * 0.12, 0.3)
            c.circle((x0 + k * (tw + gx) + 34 + m * 22, y0 + r * (th + gy) + 30), 7, fill=col, a=ma, bg=CARD)
    # the machine card
    a = appear(t, b[2], 0.7)
    cx0, cy0, cx1, cy1 = 1200, 240, 1800, 770
    c.rect((cx0 + 40 * (1 - a), cy0, cx1 + 40 * (1 - a), cy1), 24, fill=PAPER, a=a)
    if a > 0:
        ox = 40 * (1 - a)
        icon(c, "dialysis", (cx0 + 90 + ox, cy0 + 100), 0.9, TEAL, a, PAPER)
        c.text((cx0 + 160 + ox, cy0 + 70), "Dialysis machine", 34, INKDOC, "Bold", a=a, bg=PAPER)
        c.text((cx0 + 160 + ox, cy0 + 116), "Renal Unit  ·  Bay 3", 24, (90, 110, 116), a=a, bg=PAPER)
        rows = [("Manufacturer", "registered"), ("Location", "Renal Unit"), ("Service history", "")]
        for k, (lab, val) in enumerate(rows):
            ra = appear(t, b[2] + 0.4 + k * 0.4, 0.4)
            c.text((cx0 + 50 + ox, cy0 + 190 + k * 50), lab, 24, (100, 116, 120), a=ra * a, bg=PAPER)
            c.text((cx1 - 50 + ox, cy0 + 190 + k * 50), val, 24, INKDOC, "SemiBold", "ra", ra * a, PAPER)
        events = [("Installed", SKY), ("Preventive service", GREEN), ("Repair: pump fault", CORAL),
                  ("Calibrated: passed", LILAC)]
        ty = cy0 + 360
        c.line([(cx0 + 70 + ox, ty), (cx0 + 70 + ox, ty + 3 * 42)], GREYDOC, 3, a, PAPER)
        for k, (ev, col) in enumerate(events):
            ea = appear(t, b[2] + 1.4 + k * 0.45, 0.4)
            c.circle((cx0 + 70 + ox, ty + k * 42), 10, fill=col, a=ea * a, bg=PAPER)
            c.text((cx0 + 100 + ox, ty + k * 42), ev, 24, INKDOC, "Regular", "lm", ea * a, PAPER)


def s_people(c, t, dur, b):
    headline(c, t, "Your team", "Workshops, technicians and approvals")
    cw, gap = 316, 20
    x0 = (W - (5 * cw + 4 * gap)) / 2
    for i, (name, col) in enumerate(WORKSHOPS):
        a = appear(t, b[1] + 0.2 + i * 0.6, 0.5)
        x = x0 + i * (cw + gap)
        c.rect((x, 250, x + cw, 520), 22, fill=CARD, outline=LINE, a=a)
        c.rect((x, 250, x + cw, 258), 4, fill=col, a=a, bg=CARD)
        icon(c, "analyser" if i == 4 else "wrench", (x + cw / 2, 330), 0.8, col, a, CARD)
        c.text((x + cw / 2, 410), name, 27, WHITE, "SemiBold", "mm", a, CARD)
        for p in range(2 + (i % 2)):
            pa = appear(t, b[1] + 0.6 + i * 0.6 + p * 0.2, 0.4)
            person(c, (x + cw / 2 - 40 * (1 + i % 2) / 2 * 1 + p * 40 - (0 if i % 2 else 0), 462), 0.5, mix(CARD, col, 0.9),
                   pa, CARD)
    # approval chain
    roles = [("Technician", "does and signs the work", TEAL), ("In-Charge", "approves for the department", AMBER),
             ("Head of Department", "sees everything", GREEN)]
    for k, (role, sub, col) in enumerate(roles):
        a = appear(t, b[2] + k * 0.7, 0.5)
        x = 330 + k * 630
        person(c, (x - 110, 660), 0.9, col, a)
        c.text((x - 50, 650), role, 32, WHITE, "Bold", "lm", a)
        c.text((x - 50, 692), sub, 24, MUTED, "Regular", "lm", a)
        if k < 2:
            aa = appear(t, b[2] + k * 0.7 + 0.5, 0.5)
            c.line([(x + 250, 670), (x + 250 + 150 * aa, 670)], LINE, 4, aa)
            if aa > 0.9:
                c.poly([(x + 410, 670), (x + 394, 660), (x + 394, 680)], LINE, aa)


def workorder_doc(c, xy, t, b, a=1.0):
    x, y = xy
    w, h = 520, 600
    c.rect((x, y, x + w, y + h), 18, fill=PAPER, a=a)
    if a <= 0:
        return
    c.rect((x, y, x + w, y + 70), 18, fill=TEAL, a=a, bg=PAPER)
    c.rect((x, y + 50, x + w, y + 70), 0, fill=TEAL, a=a, bg=PAPER)
    c.text((x + 30, y + 36), "WORK ORDER", 26, WHITE, "Bold", "lm", a, TEAL)
    c.text((x + w - 30, y + 36), "Renal Unit", 22, WHITE, "SemiBold", "rm", a, TEAL)
    fields = [("Equipment", "Dialysis machine, Bay 3"), ("Fault", "Blood pump alarm"),
              ("Work done", "Replaced pump roller, tested"), ("Spare parts", "1 × pump roller"),
              ("Remarks", "Back in service")]
    for k, (lab, val) in enumerate(fields):
        st = b[0] + 1.6 + k * 0.45 if k == 0 else b[1] + (k - 1) * 0.75
        fa = appear(t, st, 0.4)
        yy = y + 100 + k * 72
        c.text((x + 30, yy), lab, 20, (110, 124, 128), a=fa * a, bg=PAPER)
        typed = val[: int(len(val) * clamp((t - st) / 0.6))]
        c.text((x + 30, yy + 26), typed, 26, INKDOC, "SemiBold", a=fa * a, bg=PAPER)
    sy = y + 480
    c.line([(x + 30, sy + 40), (x + 240, sy + 40)], GREYDOC, 2, a, PAPER)
    c.line([(x + 280, sy + 40), (x + 490, sy + 40)], GREYDOC, 2, a, PAPER)
    c.text((x + 30, sy + 56), "Technician", 18, (110, 124, 128), a=a, bg=PAPER)
    c.text((x + 280, sy + 56), "In-Charge", 18, (110, 124, 128), a=a, bg=PAPER)
    signature(c, x + 40, sy + 18, 180, INKDOC, (t - b[2] - 0.3) / 1.0, 3, a, PAPER, seed=2)
    signature(c, x + 290, sy + 18, 170, INKDOC, (t - b[2] - 2.6) / 0.9, 3, a, PAPER, seed=5)


def s_workorder(c, t, dur, b):
    headline(c, t, "Work orders", "From breakdown to signed off, fast")
    # the failing machine
    alert = 1.0 if t < b[2] + 3.4 else 1 - appear(t, b[2] + 3.4, 0.6)
    a = appear(t, 0.4, 0.6)
    dept_tile(c, (120, 300), 340, 300, "Renal Unit", "dialysis", TEAL, a, alert=alert * a, t=t)
    if t > b[2] + 3.6:
        badge(c, (430, 330), 18, GREEN, "check", appear(t, b[2] + 3.6, 0.4))
    # technician
    pa = appear(t, b[0] + 1.0, 0.5)
    person(c, (290, 700), 0.9, TEAL, pa)
    c.text((290, 790), "Technician", 24, MUTED, "SemiBold", "mm", pa)
    # document
    da = appear(t, b[0] + 1.2, 0.6)
    workorder_doc(c, (620 + 30 * (1 - da), 250), t, b, da)
    # in-charge
    ia = appear(t, b[2] + 1.6, 0.5)
    person(c, (1320, 470), 1.1, AMBER, ia)
    c.text((1320, 580), "In-Charge", 26, WHITE, "SemiBold", "mm", ia)
    if ia > 0:
        c.line([(1150, 480), (1150 + 80 * ia, 480)], LINE, 4, ia)
    ap = appear(t, b[2] + 3.3, 0.4)
    if ap > 0:
        pill(c, (1320, 650), "Approved", GREEN, ap, 26, solid=True)
        pill(c, (1320, 730), "or sent back with remarks", CORAL, ap * 0.9, 20)
    # printout + history
    pr = appear(t, b[3], 0.6)
    if pr > 0:
        px, py = 1560, 280 + 30 * (1 - pr)
        c.rect((px, py, px + 230, py + 300), 10, fill=PAPER, a=pr)
        c.rect((px, py, px + 230, py + 36), 10, fill=TEAL, a=pr, bg=PAPER)
        for k in range(6):
            c.rect((px + 20, py + 60 + k * 30, px + 210 - (k % 3) * 40, py + 72 + k * 30), 4, fill=GREYDOC, a=pr,
                   bg=PAPER)
        signature(c, px + 30, py + 262, 80, INKDOC, 1, 2, pr, PAPER, 2)
        signature(c, px + 130, py + 262, 70, INKDOC, 1, 2, pr, PAPER, 5)
        c.text((px + 115, py + 330), "Ready to print", 22, MUTED, "SemiBold", "mm", pr)
    ha = appear(t, b[3] + 1.4, 0.6)
    if ha > 0:
        c.rect((1480, 690, 1830, 800), 16, fill=CARD, outline=LINE, a=ha)
        c.text((1510, 722), "Service history", 20, MUTED, "SemiBold", a=ha, bg=CARD)
        c.circle((1522, 768), 9, fill=CORAL, a=ha, bg=CARD)
        c.text((1545, 768), "Repair: pump roller", 24, WHITE, "Regular", "lm", ha, CARD)


def s_ppm(c, t, dur, b):
    headline(c, t, "Preventive maintenance", "A plan for every machine, all year")
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    rows = [("Dialysis machine", "Renal Unit", 3, 0, TEAL), ("Ventilator", "ICU", 2, 1, CORAL),
            ("Infusion pump", "Ward 7", 4, 2, SKY), ("Anaesthesia machine", "Operating Theatre", 3, 1, AMBER),
            ("Incubator", "Maternity", 6, 0, LILAC)]
    gx0, gy0, cw, rh = 640, 290, 96, 104
    a = appear(t, b[1] - 0.2, 0.6)
    c.rect((110, 230, 1810, 850), 26, fill=BG_SOFT, a=a)
    for j, m in enumerate(months):
        c.text((gx0 + j * cw + cw / 2, gy0 - 22), m, 22, MUTED, "SemiBold", "mm", a, BG_SOFT)
    sweep_start, sweep_len = b[1] + 1.8, max(3.0, b[2] - b[1] + 1.0)
    cursor = gx0 + 12 * cw * clamp((t - sweep_start) / sweep_len)
    for i, (name, dept, every, off, col) in enumerate(rows):
        y = gy0 + 40 + i * rh
        ra = appear(t, b[1] + 0.2 + i * 0.2, 0.5)
        icon(c, ["dialysis", "vent", "pump", "lamp", "incubator"][i], (180, y), 0.5, col, ra, BG_SOFT)
        c.text((240, y - 14), name, 27, WHITE, "SemiBold", "lm", ra, BG_SOFT)
        c.text((240, y + 18), dept, 21, MUTED, "Regular", "lm", ra, BG_SOFT)
        c.line([(gx0, y), (gx0 + 12 * cw, y)], LINE, 2, ra, BG_SOFT)
        for j in range(off, 12, every):
            x = gx0 + j * cw + cw / 2
            pa = appear(t, b[1] + 0.6 + i * 0.2 + j * 0.04, 0.4)
            late = (i == 2 and j == 6)
            if x < cursor:
                if late:
                    fixed = t > b[2] + 3.4
                    badge(c, (x, y), 22, GREEN if fixed else AMBER, "check" if fixed else "!", pa, BG_SOFT)
                else:
                    badge(c, (x, y), 22, GREEN, "check", pa, BG_SOFT)
            else:
                c.circle((x, y), 20, fill=mix(BG_SOFT, col, 0.15), outline=col, w=3, a=pa, bg=BG_SOFT)
    if t > sweep_start:
        c.line([(cursor, gy0 - 44), (cursor, gy0 + 5 * rh + 10)], WHITE, 3, 1, BG_SOFT)
        c.text((cursor, gy0 + 5 * rh + 30), "Today", 20, WHITE, "SemiBold", "mm", 1, BG_SOFT)
    la = appear(t, b[2] + 0.8, 0.5)
    if la > 0 and t < b[2] + 3.4:
        pill(c, (gx0 + 6 * cw + cw / 2, gy0 + 40 + 2 * rh - 56), "Overdue", AMBER, la, 20, solid=True, bg=BG_SOFT)


def s_calibration(c, t, dur, b):
    headline(c, t, "Calibration", "Measured, checked and cleared")
    a1 = appear(t, b[1], 0.6)
    c.rect((120, 250, 520, 480), 22, fill=CARD, outline=LINE, a=a1)
    icon(c, "pump", (220, 365), 1.0, SKY, a1, CARD)
    c.text((300, 340), "Infusion pump", 28, WHITE, "SemiBold", a=a1, bg=CARD)
    c.text((300, 378), "device under test", 22, MUTED, a=a1, bg=CARD)
    a2 = appear(t, b[1] + 1.4, 0.6)
    c.rect((120, 540, 520, 770), 22, fill=CARD, outline=LINE, a=a2)
    icon(c, "analyser", (220, 655), 1.0, AMBER, a2, CARD)
    c.text((300, 630), "Reference", 28, WHITE, "SemiBold", a=a2, bg=CARD)
    c.text((300, 668), "traceable standard", 22, MUTED, a=a2, bg=CARD)
    la = appear(t, b[1] + 2.0, 0.6)
    c.line([(520, 365), (640, 510)], SKY, 4, la)
    c.line([(520, 655), (640, 520)], AMBER, 4, la)
    # the readings
    a3 = appear(t, b[1] + 1.0, 0.6)
    c.rect((650, 250, 1300, 790), 24, fill=PAPER, a=a3)
    c.text((690, 300), "Test points", 28, INKDOC, "Bold", a=a3, bg=PAPER)
    c.text((1260, 300), "Result", 22, (110, 124, 128), "SemiBold", "ra", a3, PAPER)
    retest = t > b[3] + 0.8
    for k in range(5):
        ra = appear(t, b[1] + 2.2 + k * 0.55, 0.4)
        y = 380 + k * 78
        c.rect((690, y - 26, 1260, y + 26), 12, fill=(232, 236, 233), a=ra * a3, bg=PAPER)
        c.text((720, y), f"Point {k + 1}", 24, INKDOC, "SemiBold", "lm", ra * a3, (232, 236, 233))
        # a small bar showing how close the reading sits to the set value
        cx = 1000
        c.line([(cx - 120, y), (cx + 120, y)], GREYDOC, 4, ra * a3, (232, 236, 233))
        c.line([(cx, y - 14), (cx, y + 14)], (140, 150, 150), 3, ra * a3, (232, 236, 233))
        bad = k == 3 and t > b[2] + 2.2 and not retest
        dev = [18, -26, 10, 150, -14][k] if not (k == 3 and retest) else 22
        if k == 3 and not bad and not retest:
            dev = 22
        col = CORAL if bad else GREEN
        c.circle((cx + dev * 0.7, y), 10, fill=col, a=ra * a3, bg=(232, 236, 233))
        if ra > 0.5:
            badge(c, (1225, y), 18, col, "cross" if bad else "check", ra * a3, (232, 236, 233))
    # the verdict
    va = appear(t, b[2] + 0.6, 0.5)
    vx, vy = 1570, 420
    if va > 0:
        c.rect((1370, 250, 1790, 600), 24, fill=CARD, outline=LINE, a=va)
        c.text((vx + 10, 300), "Cirqen works out", 22, MUTED, "SemiBold", "mm", va, CARD)
        for k, lab in enumerate(("Error", "Uncertainty", "Verdict")):
            ka = appear(t, b[2] + 0.8 + k * 0.5, 0.4)
            c.text((1420, 360 + k * 56), lab, 28, WHITE, "SemiBold", "lm", ka, CARD)
            check(c, (1740, 362 + k * 56), 26, TEAL, 5, ka, CARD, prog=(t - b[2] - 1.0 - k * 0.5) / 0.4)
        fail = t > b[2] + 2.2 and not retest
        passed = retest and t > b[3] + 1.6
        if fail:
            pill(c, (vx, 540), "Does not pass", CORAL, appear(t, b[2] + 2.3, 0.4), 26, solid=True, bg=CARD)
        elif passed:
            pill(c, (vx, 540), "Cleared for use", GREEN, appear(t, b[3] + 1.6, 0.4), 26, solid=True, bg=CARD)
    ada = appear(t, b[3], 0.5)
    if ada > 0:
        c.rect((1370, 640, 1790, 770), 24, fill=CARD, outline=LINE, a=ada)
        icon(c, "wrench", (1450, 705), 0.7, AMBER, ada, CARD)
        c.text((1510, 690), "Adjusted", 28, WHITE, "SemiBold", "lm", ada, CARD)
        c.text((1510, 726), "and tested again", 22, MUTED, "Regular", "lm", ada, CARD)


def s_certificate(c, t, dur, b):
    headline(c, t, "Calibration certificates", "Generated for you, ready to issue")
    # reviewer approval
    ra = appear(t, 0.5, 0.5)
    person(c, (240, 360), 1.0, GREEN, ra)
    c.text((240, 460), "Reviewer", 26, WHITE, "SemiBold", "mm", ra)
    pill(c, (240, 530), "Session approved", GREEN, appear(t, b[0] + 1.6, 0.4), 22, solid=True)
    # checklist of what goes on the certificate
    items = ["Results and uncertainty", "Reference standards used", "Drift against earlier calibrations",
             "Signatures", "QR code for verification"]
    starts = [b[1] + 0.2, b[1] + 2.3, b[1] + 4.2, b[1] + 6.4, b[1] + 7.6]
    for k, it in enumerate(items):
        ia = appear(t, starts[k], 0.4)
        y = 620 + k * 52
        badge(c, (140, y), 15, TEAL, "check", ia)
        c.text((170, y), it, 26, WHITE, "Regular", "lm", ia)
    # the certificate itself
    da = appear(t, b[0] + 0.8, 0.7)
    x, y, w, h = 900, 200, 820, 690
    c.rect((x + 14, y + 14, x + w + 14, y + h + 14), 16, fill=BG_SOFT, a=da)
    c.rect((x, y, x + w, y + h), 16, fill=PAPER, a=da)
    if da <= 0:
        return
    c.rect((x + 18, y + 18, x + w - 18, y + h - 18), 10, outline=(210, 216, 214), w=2, a=da, bg=PAPER)
    logo(c, LOGO_ICON, (x + 72, y + 80), 64, da)
    c.text((x + 120, y + 64), "CALIBRATION CERTIFICATE", 30, INKDOC, "Bold", a=da, bg=PAPER)
    c.text((x + 120, y + 100), "Infusion pump  ·  Ward 7", 22, (100, 114, 118), a=da, bg=PAPER)
    na = appear(t, b[2] + 0.4, 0.5)
    if na > 0:
        pill(c, (x + w - 150, y + 80), "Cert. No. issued", LILAC, na, 20, solid=True, bg=PAPER)
    # results + uncertainty
    s1 = appear(t, starts[0], 0.5)
    c.text((x + 50, y + 170), "Results", 22, (100, 114, 118), "SemiBold", a=s1 * da, bg=PAPER)
    for k in range(5):
        hgt = [60, 74, 52, 68, 58][k]
        bx = x + 60 + k * 58
        c.rect((bx, y + 300 - hgt * s1, bx + 36, y + 300), 4, fill=TEAL, a=s1 * da, bg=PAPER)
        c.line([(bx + 18, y + 300 - hgt * s1 - 14), (bx + 18, y + 300 - hgt * s1 + 14)], INKDOC, 2, s1 * da, PAPER)
    c.text((x + 50, y + 322), "with uncertainty", 18, (120, 132, 134), a=s1 * da, bg=PAPER)
    # standards
    s2 = appear(t, starts[1], 0.5)
    c.text((x + 440, y + 170), "Reference standards", 22, (100, 114, 118), "SemiBold", a=s2 * da, bg=PAPER)
    for k in range(3):
        c.rect((x + 440, y + 206 + k * 36, x + 760 - k * 50, y + 222 + k * 36), 4, fill=GREYDOC, a=s2 * da,
               bg=PAPER)
    # drift chart
    s3 = appear(t, starts[2], 0.5)
    c.text((x + 50, y + 380), "Drift history", 22, (100, 114, 118), "SemiBold", a=s3 * da, bg=PAPER)
    pts = [(x + 60 + k * 70, y + 500 - v) for k, v in enumerate((20, 34, 28, 46, 40))]
    n = max(2, int(len(pts) * clamp((t - starts[2]) / 1.0)) + 1)
    c.line(pts[:n], LILAC, 4, s3 * da, PAPER)
    for p in pts[:n]:
        c.circle(p, 7, fill=LILAC, a=s3 * da, bg=PAPER)
    # signatures
    sg = t - starts[3]
    c.text((x + 440, y + 330), "Signatures", 22, (100, 114, 118), "SemiBold", a=da, bg=PAPER)
    c.line([(x + 440, y + 420), (x + 590, y + 420)], GREYDOC, 2, da, PAPER)
    c.line([(x + 610, y + 420), (x + 760, y + 420)], GREYDOC, 2, da, PAPER)
    signature(c, x + 450, y + 398, 120, INKDOC, sg / 0.8, 3, da, PAPER, seed=3)
    signature(c, x + 620, y + 398, 120, INKDOC, (sg - 0.6) / 0.8, 3, da, PAPER, seed=8)
    c.text((x + 440, y + 434), "Calibrated by", 16, (120, 132, 134), a=da, bg=PAPER)
    c.text((x + 610, y + 434), "Approved by", 16, (120, 132, 134), a=da, bg=PAPER)
    # QR + verdict
    qa = clamp((t - starts[4]) / 1.0)
    qr(c, (x + w - 190, y + h - 190), 140, INKDOC, da, PAPER, qa)
    pa = appear(t, starts[4] + 0.6, 0.4)
    if pa > 0:
        pill(c, (x + 180, y + h - 90), "PASSED", GREEN, pa, 26, solid=True, bg=PAPER)
    # unique number stamp
    if na > 0:
        c.text((x + 272, y + h - 90), "Unique number, issued centrally", 19, (100, 114, 118), "SemiBold",
               "lm", na * da, PAPER)


def s_offline(c, t, dur, b):
    headline(c, t, "Works offline", "The network drops. Your team doesn't stop.")
    hub = (960, 330)
    ca = appear(t, 0.4, 0.6)
    cloud(c, hub, 180, CARD_HI, TEAL, ca)
    logo(c, LOGO_ICON, (hub[0] - 62, hub[1] + 6), 54, ca)
    c.text((hub[0] - 22, hub[1] + 6), "Cirqen", 34, WHITE, "Bold", "lm", ca, CARD_HI)
    off_start, off_end = b[2], b[3] + 0.3
    offline = off_start <= t < off_end
    for i, (name, col) in enumerate(WORKSHOPS):
        a = appear(t, b[1] + 0.2 + i * 0.35, 0.5)
        x = 230 + i * 365
        y = 690
        is_theatre = i == 2
        down = is_theatre and offline
        # connection
        p, q = (x, y - 70), (hub[0] + (x - hub[0]) * 0.18, hub[1] + 70)
        if a > 0:
            if down:
                n = 14
                for k in range(n):
                    if k % 2 == 0:
                        c.line([lerp(p, q, k / n), lerp(p, q, (k + 1) / n)], CORAL, 3, a)
                mid = lerp(p, q, 0.5)
                badge(c, mid, 20, CORAL, "cross", a)
            else:
                c.line([p, q], LINE, 3, a)
                if t > b[1] + 1.5:
                    u = ((t * 0.55) + i * 0.23) % 1.0
                    c.circle(lerp(p, q, u), 7, fill=WHITE, a=a)
                    u2 = ((t * 0.45) + i * 0.41) % 1.0
                    c.circle(lerp(q, p, u2), 7, fill=TEAL, a=a)
            # catch-up burst for the theatre workshop
            if is_theatre and off_end <= t < off_end + 2.6:
                for k in range(8):
                    u = clamp((t - off_end) * 1.2 - k * 0.14)
                    if 0 < u < 1:
                        c.circle(lerp(p, q, u), 9, fill=AMBER, a=a)
                    u2 = clamp((t - off_end - 0.8) * 1.2 - k * 0.2)
                    if 0 < u2 < 1 and k < 3:
                        c.circle(lerp(q, p, u2), 9, fill=LILAC, a=a)
        c.rect((x - 165, y - 70, x + 165, y + 170), 22, fill=CARD, outline=CORAL if down else LINE, w=3 if down else 2,
               a=a)
        computer(c, (x, y + 10), 0.8, col, a, CARD)
        c.text((x, y + 92), name, 25, WHITE, "SemiBold", "mm", a, CARD)
        if is_theatre and t >= off_start:
            saved = int(clamp((t - off_start - 0.8) / max(1.0, off_end - off_start - 1.2)) * 9)
            if offline:
                status, scol = f"Offline · {saved} jobs saved here", CORAL
            else:
                left = int(9 * (1 - clamp((t - off_end - 0.3) / 1.8)))
                status, scol = ("Catching up…", AMBER) if left else ("Up to date", GREEN)
            c.text((x, y + 132), status, 21, scol, "SemiBold", "mm", a, CARD)
            if offline:
                for k in range(min(saved, 9)):
                    c.rect((x - 108 + k * 24, y - 44, x - 90 + k * 24, y - 22), 3, fill=AMBER, a=a, bg=CARD)
        else:
            c.text((x, y + 132), "Up to date", 21, GREEN, "SemiBold", "mm", a, CARD)
    # certificate number arriving
    na = appear(t, b[3] + 3.4, 0.5)
    if na > 0:
        pill(c, (960, 530), "Certificate numbers received", LILAC, na, 22, solid=True)


def s_trust(c, t, dur, b):
    headline(c, t, "Peace of mind", "Reports, accountability, secure updates")
    panels = [(120, "Reports", b[0]), (700, "Audit trail", b[1]), (1280, "Secure updates", b[2])]
    for x, title, st in panels:
        a = appear(t, st - 0.2, 0.6)
        c.rect((x, 250, x + 520, 800), 26, fill=CARD, outline=LINE, a=a)
        c.text((x + 40, 300), title, 34, WHITE, "Bold", a=a, bg=CARD)
    # reports: bars growing
    a = appear(t, b[0], 0.6)
    months = ["J", "F", "M", "A", "M", "J"]
    for k, v in enumerate((180, 240, 200, 290, 260, 330)):
        g = ease((t - b[0] - 0.3 - k * 0.12) / 0.7)
        bx = 180 + k * 70
        c.rect((bx, 730 - v * g, bx + 44, 730), 8, fill=[TEAL, SKY][k % 2], a=a, bg=CARD)
        c.text((bx + 22, 760), months[k], 20, MUTED, "SemiBold", "mm", a, CARD)
    # audit trail: entries ticking in
    entries = [("Technician", "signed a work order", TEAL), ("In-Charge", "approved it", AMBER),
               ("Reviewer", "approved a calibration", GREEN), ("Technician", "updated a machine", SKY),
               ("Head of Dept", "added a user", LILAC)]
    for k, (who, what, col) in enumerate(entries):
        ea = appear(t, b[1] + 0.3 + k * 0.45, 0.4)
        y = 390 + k * 78
        c.circle((750, y), 20, fill=col, a=ea, bg=CARD)
        c.text((784, y - 12), who, 24, WHITE, "SemiBold", "lm", ea, CARD)
        c.text((784, y + 16), what, 20, MUTED, "Regular", "lm", ea, CARD)
    # updates: shield with check
    ua = appear(t, b[2], 0.6)
    sx, sy = 1540, 520
    if ua > 0:
        c.poly([(sx, sy - 130), (sx + 110, sy - 90), (sx + 100, sy + 30), (sx, sy + 130), (sx - 100, sy + 30),
                (sx - 110, sy - 90)], TEAL, ua, CARD)
        c.poly([(sx, sy - 108), (sx + 88, sy - 76), (sx + 80, sy + 24), (sx, sy + 106), (sx - 80, sy + 24),
                (sx - 88, sy - 76)], mix(CARD, TEAL, 0.3), ua, CARD)
        check(c, (sx, sy + 4), 90, WHITE, 12, ua, mix(CARD, TEAL, 0.3), prog=(t - b[2] - 0.5) / 0.6)
        c.text((sx, 720), "Verified before install", 24, MUTED, "SemiBold", "mm", appear(t, b[2] + 1.0, 0.5), CARD)


def s_close(c, t, dur, b):
    logo(c, LOGO_WIDE, (960, 330), 250, appear(t, 0.2, 1.0))
    lines = ["Every machine ready.", "Every record signed.", "Across your whole hospital."]
    for k, ln in enumerate(lines):
        c.text((960, 560 + k * 56), ln, 40, MUTED if k < 2 else WHITE, "Regular" if k < 2 else "SemiBold", "mm",
               appear(t, 1.6 + k * 0.7, 0.6))
    ba = appear(t, b[1] - 0.2, 0.6)
    if ba > 0:
        pill(c, (960, 830), "Book a demo", TEAL, ba, 36, solid=True)


SCENES = {"hook": s_hook, "brand": s_brand, "hospital": s_hospital, "people": s_people, "workorder": s_workorder,
          "ppm": s_ppm, "calibration": s_calibration, "certificate": s_certificate, "offline": s_offline,
          "trust": s_trust, "close": s_close}
NO_SUBTITLES = {"brand", "close"}


# ---- audio -----------------------------------------------------------------

def synth_voice():
    """Synthesise each sentence; return per-scene (audio, beats, sentence spans)."""
    from kokoro_onnx import Kokoro

    k = Kokoro(str(TTS_DIR / "kokoro-v1.0.onnx"), str(TTS_DIR / "voices-v1.0.bin"))
    scenes = {}
    for name in ORDER:
        parts, beats, spans = [np.zeros(int(LEAD_IN * SR), dtype=np.float32)], [], []
        pos = LEAD_IN
        for display, spoken in NARRATION[name]:
            audio, sr = k.create(spoken or display, voice=VOICE, speed=0.98, lang="en-us")
            assert sr == SR
            audio = audio.astype(np.float32)
            beats.append(pos)
            spans.append((pos, pos + len(audio) / SR, display))
            parts += [audio, np.zeros(int(GAP * SR), dtype=np.float32)]
            pos += len(audio) / SR + GAP
        parts.append(np.zeros(int(TAIL * SR), dtype=np.float32))
        scenes[name] = (np.concatenate(parts), beats, spans)
        print(f"voice: {name} {pos + TAIL:.1f}s", flush=True)
    return scenes


def music_bed(seconds):
    """A soft, slow chord pad with a gentle arpeggio, synthesised."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    out = np.zeros(n, dtype=np.float64)
    midi = lambda m: 440.0 * 2 ** ((m - 69) / 12)  # noqa: E731
    chords = [[48, 55, 64, 71], [45, 52, 60, 67], [41, 48, 57, 64], [43, 50, 59, 62]]  # Cmaj7 Am7 Fmaj7 G
    bar = 4.0
    for i in range(int(seconds / bar) + 2):
        chord = chords[i % 4]
        start = i * bar - 0.5
        s0, s1 = max(0, int(start * SR)), min(n, int((start + bar + 1.5) * SR))
        if s0 >= s1:
            continue
        tt = t[s0:s1] - start
        env = np.clip(tt / 1.2, 0, 1) * np.clip((bar + 1.5 - tt) / 1.5, 0, 1)
        for m in chord:
            f = midi(m)
            out[s0:s1] += env * (np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(4 * np.pi * f * tt)) * 0.12
        # arpeggio: soft plucks an octave up
        for k in range(8):
            ps = start + 0.5 + k * 0.5
            p0, p1 = int(ps * SR), min(n, int((ps + 1.2) * SR))
            if p0 < 0 or p0 >= n:
                continue
            pt = t[p0:p1] - ps
            f = midi(chord[k % 4] + 12)
            out[p0:p1] += np.exp(-pt * 4.5) * np.sin(2 * np.pi * f * pt) * 0.05
    fade_n = int(3 * SR)
    out[:fade_n] *= np.linspace(0, 1, fade_n)
    out[-fade_n:] *= np.linspace(1, 0, fade_n)
    return out / (np.max(np.abs(out)) + 1e-9)


# ---- render ------------------------------------------------------------------

_bg_cache = {}


def background():
    if "bg" not in _bg_cache:
        img = Image.new("RGB", (W * S, H * S), BG)
        _bg_cache["bg"] = img
    return _bg_cache["bg"].copy()


def subtitle(c, t, spans):
    for start, end, s in spans:
        if start - 0.1 <= t <= end + 0.25:
            a = min(clamp((t - start + 0.1) / 0.25), clamp((end + 0.25 - t) / 0.25))
            lines = c.wrap(s, 30, 1400, "Medium")
            h = len(lines) * 42 + 30
            wmax = max(c.textlen(ln, 30, "Medium") for ln in lines) + 60
            y0 = 1040 - h
            c.rect((W / 2 - wmax / 2, y0, W / 2 + wmax / 2, 1040), 18, fill=(6, 18, 24), a=0.85 * a)
            for k, ln in enumerate(lines):
                c.text((W / 2, y0 + 15 + 21 + k * 42), ln, 30, WHITE, "Medium", "mm", a, (6, 18, 24))
            return


def render(name, t, dur, beats, spans):
    img = background()
    c = Canvas(img)
    SCENES[name](c, t, dur, beats)
    if name not in NO_SUBTITLES:
        subtitle(c, t, spans)
    img = img.resize((W, H), Image.LANCZOS)
    a = min(clamp(t / 0.5), clamp((dur - t) / 0.5))
    if a < 1:
        img = Image.blend(Image.new("RGB", (W, H), BG), img, a)
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
    ap.add_argument("--at", type=float, default=None, help="seconds into the scene for --still (default: near end)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    import soundfile as sf

    BUILD.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(repr((NARRATION, ORDER, VOICE, LEAD_IN, GAP, TAIL)).encode()).hexdigest()[:12]
    cache = BUILD / f"voice_{key}.pkl"
    if cache.exists():
        voice = pickle.loads(cache.read_bytes())
    else:
        voice = synth_voice()
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
    music = music_bed(total)
    mixed = track / (np.max(np.abs(track)) + 1e-9) * 0.9 + music * 0.11
    mixed /= max(1.0, np.max(np.abs(mixed)) / 0.98)
    wav = BUILD / "soundtrack.wav"
    sf.write(wav, mixed.astype(np.float32), SR)

    ff = ffmpeg_exe()
    silent = BUILD / "video.mp4"
    cmd = [ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", str(silent)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    frames_done = 0
    for name in ORDER:
        audio, beats, spans = voice[name]
        dur = len(audio) / SR
        nframes = int(round(dur * FPS))
        for f in range(nframes):
            proc.stdin.write(render(name, f / FPS, dur, beats, spans).tobytes())
        frames_done += nframes
        print(f"video: {name} done ({frames_done / FPS:.0f}s of {total:.0f}s)", flush=True)
    proc.stdin.close()
    if proc.wait():
        sys.exit("ffmpeg failed while encoding video")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ff, "-y", "-loglevel", "error", "-i", str(silent), "-i", str(wav), "-c:v", "copy", "-c:a", "aac",
                    "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(args.out)], check=True)
    print(args.out)


if __name__ == "__main__":
    main()
