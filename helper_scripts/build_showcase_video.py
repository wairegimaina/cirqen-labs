#!/usr/bin/env python3
"""Compose the captured module screens into a product showcase video.

Reads the PNGs written by helper_scripts/capture_screens.py, frames each one
with a caption, inserts section title cards, and encodes a 1080p MP4 with
crossfades.

    python helper_scripts/capture_screens.py      # shoot the screens first
    python helper_scripts/build_showcase_video.py

Needs ffmpeg on PATH. The video is silent by design — narrate over it, or
leave it as a captioned walkthrough.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
FRAMES = ROOT / "data" / "demo_frames"
BUILD = ROOT / "data" / "showcase_build"
OUT = ROOT / "data" / "Equiper_Showcase.mp4"

W, H = 1920, 1080
FPS = 30
XFADE = 0.6          # seconds of crossfade between slides
HOLD_SCREEN = 5.2    # seconds a module screen is held
HOLD_CARD = 3.0      # seconds a section card is held
HOLD_TITLE = 4.0

# palette carried over from the product explainer
INK = (16, 30, 29)
INK_DEEP = (9, 18, 17)
PAPER = (241, 244, 242)
MUTED = (140, 158, 154)
ACCENT = (92, 198, 210)
ACCENT_DEEP = (10, 92, 104)
WHITE = (255, 255, 255)

F = "/usr/share/fonts/truetype/lato/Lato-%s.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def font(weight, size):
    return ImageFont.truetype(F % weight, size)


def mono(size):
    return ImageFont.truetype(MONO, size)


def text_w(draw, s, f):
    return draw.textbbox((0, 0), s, font=f)[2]


def tracked(draw, xy, s, f, fill, spacing=6, center_in=None):
    """Draw letter-spaced text; returns total width."""
    total = sum(text_w(draw, ch, f) + spacing for ch in s) - spacing
    x, y = xy
    if center_in is not None:
        x = (center_in - total) // 2
    for ch in s:
        draw.text((x, y), ch, font=f, fill=fill)
        x += text_w(draw, ch, f) + spacing
    return total


# ---- the running order -------------------------------------------------

TITLE = {
    "kind": "title",
    "eyebrow": "CIRQEN LABS",
    "title": "Equiper",
    "sub": "Hospital biomedical engineering, in one system",
}

CLOSING = {
    "kind": "title",
    "eyebrow": "CIRQEN LABS",
    "title": "Equiper",
    "sub": "Inventory · Job cards · PPM · Calibration · Parts · Reports",
}

RUNNING_ORDER = [
    TITLE,
    {"kind": "card", "n": "01", "title": "The department at a glance",
     "sub": "One view across every workshop"},
    {"kind": "screen", "file": "01-hod-dashboard", "title": "Head of Department dashboard",
     "sub": "Every workshop, live counts, job card status and PPM completions"},

    {"kind": "card", "n": "02", "title": "The register",
     "sub": "Every machine, by department and workshop"},
    {"kind": "screen", "file": "02-inventory", "title": "Inventory",
     "sub": "201 devices with unique serials, status and criticality class"},

    {"kind": "card", "n": "03", "title": "The work",
     "sub": "Raised, signed, verified, approved"},
    {"kind": "screen", "file": "03-jobcard-new", "title": "Raise a job card",
     "sub": "Fault, priority, times, parts drawn and labour — against a serial number"},
    {"kind": "screen", "file": "04-jobcard-waiting", "title": "Awaiting approval",
     "sub": "Signed by the technician, verified by the ward, waiting on the HOD"},
    {"kind": "screen", "file": "05-jobcard-approved", "title": "Approved and costed",
     "sub": "Stock deducted, labour and parts booked to the machine's history"},

    {"kind": "card", "n": "04", "title": "Planned maintenance",
     "sub": "Schedules that roll forward on completion"},
    {"kind": "screen", "file": "06-ppm", "title": "PPM dashboard",
     "sub": "The month's work queue, by workshop and department"},
    {"kind": "screen", "file": "07-cal-schedules", "title": "Calibration schedules",
     "sub": "What is due, what is overdue, what is already booked"},

    {"kind": "card", "n": "05", "title": "Calibration and certificates",
     "sub": "Measurement, uncertainty, verdict, certificate"},
    {"kind": "screen", "file": "08-cal-dashboard", "title": "Calibration dashboard",
     "sub": "Sessions in progress, pending approval and completed"},
    {"kind": "screen", "file": "09-cal-procedures", "title": "Procedures",
     "sub": "Parameters, set points, tolerances and the reference standard"},
    {"kind": "screen", "file": "10-cal-standards", "title": "Reference standards",
     "sub": "The standards the readings are traceable to, with their uncertainty"},
    {"kind": "screen", "file": "11-cal-approvals", "title": "Session approval",
     "sub": "Approved by an In-Charge or HOD — or rejected with a reason"},
    {"kind": "screen", "file": "12-cal-certificates", "title": "Certificate register",
     "sub": "Numbered, QR-verifiable, with the next calibration due date"},

    {"kind": "card", "n": "06", "title": "Stores",
     "sub": "Spare parts and test equipment"},
    {"kind": "screen", "file": "13-accessories", "title": "Spare parts",
     "sub": "Stock levels and unit costs, drawn down by approved job cards"},
    {"kind": "screen", "file": "14-tools", "title": "Tools",
     "sub": "The workshop's test equipment, by workshop"},

    {"kind": "card", "n": "07", "title": "Evidence",
     "sub": "The numbers management asks for"},
    {"kind": "screen", "file": "15-machine-reports", "title": "Machine reports",
     "sub": "Repairs, downtime hours and total cost across every workshop"},
    {"kind": "screen", "file": "16-manufacturers", "title": "Manufacturer performance",
     "sub": "Repairs by brand — the report that answers whether to buy it again"},
    {"kind": "screen", "file": "17-report-hub", "title": "Report hub",
     "sub": "Weekly, monthly, quarterly and annual workshop returns"},
    {"kind": "screen", "file": "18-audit-log", "title": "Audit log",
     "sub": "Every change, with the user and the values before and after"},

    CLOSING,
]


# ---- slide rendering ---------------------------------------------------

def bg(deep=False):
    img = Image.new("RGB", (W, H), INK_DEEP if deep else INK)
    d = ImageDraw.Draw(img)
    # a soft accent wash along the bottom edge, so the frame is not flat
    for i in range(140):
        a = i / 140
        y = H - 140 + i
        d.line([(0, y), (W, y)],
               fill=tuple(int(c + (ACCENT_DEEP[j] - c) * a * 0.30)
                          for j, c in enumerate(INK_DEEP if deep else INK)))
    return img, d


def render_title(spec, path):
    img, d = bg(deep=True)
    f_title = font("Black", 132)
    f_sub = font("Light", 40)
    f_eye = mono(22)

    tracked(d, (0, H // 2 - 170), spec["eyebrow"], f_eye, ACCENT, 10, center_in=W)

    tw = text_w(d, spec["title"], f_title)
    d.text(((W - tw) // 2, H // 2 - 120), spec["title"], font=f_title, fill=WHITE)

    d.line([(W // 2 - 60, H // 2 + 66), (W // 2 + 60, H // 2 + 66)], fill=ACCENT, width=3)

    sw = text_w(d, spec["sub"], f_sub)
    d.text(((W - sw) // 2, H // 2 + 104), spec["sub"], font=f_sub, fill=MUTED)

    img.save(path)


def render_card(spec, path):
    img, d = bg()
    f_n = mono(26)
    f_title = font("Black", 86)
    f_sub = font("Light", 38)

    x = 190
    tracked(d, (x, H // 2 - 150), spec["n"], f_n, ACCENT, 8)
    d.line([(x, H // 2 - 104), (x + 96, H // 2 - 104)], fill=ACCENT, width=3)
    d.text((x, H // 2 - 70), spec["title"], font=f_title, fill=WHITE)
    d.text((x, H // 2 + 50), spec["sub"], font=f_sub, fill=MUTED)

    img.save(path)


def render_screen(spec, src, path):
    img, d = bg()
    shot = Image.open(src).convert("RGB")

    # frame the screenshot above a caption band, scaled to keep UI text legible
    band = 128
    max_w, max_h = W - 176, H - band - 96
    ratio = min(max_w / shot.width, max_h / shot.height)
    sw, sh = int(shot.width * ratio), int(shot.height * ratio)
    shot = shot.resize((sw, sh), Image.LANCZOS)

    sx, sy = (W - sw) // 2, 54
    d.rectangle([sx - 2, sy - 2, sx + sw + 1, sy + sh + 1], outline=(46, 62, 60), width=2)
    img.paste(shot, (sx, sy))

    f_title = font("Bold", 42)
    f_sub = font("Light", 29)
    ty = sy + sh + 30
    d.text((sx, ty), spec["title"], font=f_title, fill=WHITE)
    d.text((sx, ty + 54), spec["sub"], font=f_sub, fill=MUTED)

    img.save(path)


# ---- build -------------------------------------------------------------

def main():
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg not found on PATH. Install it: sudo apt install -y ffmpeg")
    if not FRAMES.exists():
        sys.exit(f"No captures in {FRAMES}. Run helper_scripts/capture_screens.py first.")

    if BUILD.exists():
        shutil.rmtree(BUILD)
    BUILD.mkdir(parents=True)

    slides = []
    print("composing slides")
    for i, spec in enumerate(RUNNING_ORDER):
        path = BUILD / f"slide_{i:03d}.png"
        if spec["kind"] == "title":
            render_title(spec, path)
            hold = HOLD_TITLE
        elif spec["kind"] == "card":
            render_card(spec, path)
            hold = HOLD_CARD
        else:
            src = FRAMES / f"{spec['file']}.png"
            if not src.exists():
                print(f"  ! missing capture {src.name} — skipping")
                continue
            render_screen(spec, src, path)
            hold = HOLD_SCREEN
        slides.append((path, hold))
    print(f"  {len(slides)} slides")

    # each slide becomes a clip, then the clips are crossfaded together
    print("encoding clips")
    clips = []
    for i, (path, hold) in enumerate(slides):
        clip = BUILD / f"clip_{i:03d}.mp4"
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-loop", "1", "-framerate", str(FPS), "-i", str(path),
            "-t", f"{hold:.2f}",
            "-vf", f"format=yuv420p,scale={W}:{H}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-r", str(FPS), str(clip),
        ], check=True)
        clips.append((clip, hold))

    print("crossfading")
    inputs, filters = [], []
    for clip, _ in clips:
        inputs += ["-i", str(clip)]

    prev, offset = "[0:v]", clips[0][1] - XFADE
    for i in range(1, len(clips)):
        label = f"[x{i}]"
        filters.append(
            f"{prev}[{i}:v]xfade=transition=fade:duration={XFADE}:offset={offset:.2f}{label}"
        )
        prev = label
        offset += clips[i][1] - XFADE

    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", *inputs,
        "-filter_complex", ";".join(filters),
        "-map", prev,
        "-c:v", "libx264", "-preset", "slow", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-r", str(FPS), str(OUT),
    ], check=True)

    dur = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(OUT)],
        capture_output=True, text=True).stdout.strip()
    size_mb = OUT.stat().st_size / 1e6
    print(f"\n{OUT}")
    print(f"  {float(dur):.1f}s · {size_mb:.1f} MB · {W}x{H} · {FPS}fps")
    shutil.rmtree(BUILD)


if __name__ == "__main__":
    main()
