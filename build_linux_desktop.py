#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║           CIRQEN  –  Linux Desktop Application Build System                 ║
║           Works on: Ubuntu · Debian · Fedora · RHEL · Arch · openSUSE      ║
║                     Mint · Pop!_OS · Tails · Kali · EndeavourOS             ║
╚══════════════════════════════════════════════════════════════════════════════╝

HOW TO USE
──────────
  python3 build_linux_desktop.py

  Options:
    --skip-pyinstaller    Skip the PyInstaller build (use existing dist/Cirqen)
    --skip-appdir         Skip creating the AppDir / AppImage structure
    --version X.Y.Z       Override the app version string (default: 1.0.0)

WHAT THIS SCRIPT DOES
──────────────────────
  Step 1   Detect distro and check all required system dependencies
  Step 2   Run build.py  (full PyInstaller build)
  Step 3   Locate and copy the app icon from static/images/
  Step 4   Create start_cirqen.sh  (cross-distro launcher with all env flags)
  Step 5   Create the .desktop entry file  (app menu integration)
  Step 6   Create install.sh   (system-wide install to /opt/Cirqen)
  Step 7   Create uninstall.sh (clean removal)
  Step 8   Create AppDir       (ready for appimagetool → .AppImage)
  Step 9   Create README_LINUX.txt  (per-distro install instructions)
  Step 10  Package everything into Cirqen_linux_<version>.tar.gz

OUTPUT
──────
  dist/Cirqen/                              ← runnable app folder
    Cirqen                                  ← PyInstaller executable
    start_cirqen.sh                         ← launcher  (run this)
    com.b12technologies.cirqen.desktop      ← system install desktop entry
    Cirqen.desktop                          ← local/test desktop entry
    install.sh                              ← system-wide installer
    uninstall.sh                            ← clean removal
    resources/icon.png                      ← best icon found in static/images/
    README_LINUX.txt                        ← per-distro instructions
  dist/Cirqen.AppDir/                       ← AppImage source folder
  dist/Cirqen_linux_1.0.0.tar.gz           ← final distributable archive
"""

# ─────────────────────────────────────────────────────────────────────────────
# Standard-library imports only – no third-party deps needed at build time
# ─────────────────────────────────────────────────────────────────────────────
import sys
import os
import platform
import subprocess
import shutil
import logging
import stat
import tarfile
import textwrap
import argparse
from pathlib import Path
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# Linux-only guard  (checked before anything else so error is crystal-clear)
# ─────────────────────────────────────────────────────────────────────────────
if platform.system() != "Linux":
    print("❌  This script is for Linux only.")
    print("    On Windows run:  python build.py")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────────────────────
# CLI arguments
# ─────────────────────────────────────────────────────────────────────────────
_parser = argparse.ArgumentParser(description="Cirqen Linux Desktop Build")
_parser.add_argument("--skip-pyinstaller", action="store_true",
                     help="Skip PyInstaller build – use an existing dist/Cirqen")
_parser.add_argument("--skip-appdir", action="store_true",
                     help="Skip AppDir / AppImage structure creation")
_parser.add_argument("--version", default="1.0.0",
                     help="App version string  (default: 1.0.0)")
ARGS = _parser.parse_args()

# ─────────────────────────────────────────────────────────────────────────────
# Paths  (all derived from the location of this script)
# ─────────────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.resolve()
LOG_DIR      = PROJECT_ROOT / "build_logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE     = LOG_DIR / f"linux_build_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

# ─────────────────────────────────────────────────────────────────────────────
# Logging  –  file + stdout, identical format
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("cirqen_build")

# ─────────────────────────────────────────────────────────────────────────────
# Application constants
# ─────────────────────────────────────────────────────────────────────────────
APP_NAME        = "Cirqen"
APP_ID          = "com.b12technologies.cirqen"
APP_VERSION     = ARGS.version
APP_DESCRIPTION = "Calibration & Maintenance Management System"
APP_CATEGORIES  = "Office;Science;MedicalSoftware;"
AUTHOR          = "B12 Technologies"
INSTALL_DIR     = Path("/opt") / APP_NAME        # system-wide target

DIST_DIR        = PROJECT_ROOT / "dist"
DIST_APP        = DIST_DIR / APP_NAME            # dist/Cirqen
BUILD_BACKUP    = PROJECT_ROOT / "build.py"

# ─────────────────────────────────────────────────────────────────────────────
# Icon search order
#
# Your project uses logo-<WxH>.png naming inside static/images/.
# The list below is ordered from BEST (largest, highest-quality) to FALLBACK.
#
# Actual files observed in static/images/:
#   logo-1024x1024.png  ← best quality, use for AppImage / hicolor 256 (scaled)
#   logo-512x512.png    ← great; used when 1024 is absent
#   logo-256x256.png    ← ideal native size for desktop icons
#   logo-192x192.png
#   logo-180x180.png
#   logo-152x152.png
#   logo-128x128.png
#   logo-120x120.png
#   logo-114x114.png
#   logo-96x96.png
#   logo-76x76.png
#   logo-72x72.png
#   logo-60x60.png
#   logo-57x57.png
#   logo-48x48.png
#   logo-32x32.png
#   logo-16x16.png
#   dark.png            ← project-branded dark variant
#   equiper-logo.png    ← alternate brand logo
#   equiper-logo2.png   ← alternate brand logo variant
#
# staticfiles/ mirrors static/ after collectstatic.
# Legacy/alternate root locations are tried last.
# ─────────────────────────────────────────────────────────────────────────────

_STATIC_IMG      = PROJECT_ROOT / "static"      / "images"
_STATICFILES_IMG = PROJECT_ROOT / "staticfiles"  / "images"
_DIST_STATIC_IMG = DIST_APP     / "static"       / "images"
_DIST_SF_IMG     = DIST_APP     / "staticfiles"  / "images"

ICON_SEARCH_PATHS = [
    # ── Highest-resolution logos from static/images/ ──────────────────────────
    _STATIC_IMG      / "logo-1024x1024.png",
    _STATIC_IMG      / "logo-512x512.png",
    _STATIC_IMG      / "logo-256x256.png",
    _STATIC_IMG      / "logo-192x192.png",
    _STATIC_IMG      / "logo-180x180.png",
    _STATIC_IMG      / "logo-152x152.png",
    _STATIC_IMG      / "logo-128x128.png",
    _STATIC_IMG      / "logo-120x120.png",
    _STATIC_IMG      / "logo-114x114.png",
    _STATIC_IMG      / "logo-96x96.png",
    _STATIC_IMG      / "logo-76x76.png",
    _STATIC_IMG      / "logo-72x72.png",
    _STATIC_IMG      / "logo-60x60.png",
    _STATIC_IMG      / "logo-57x57.png",
    _STATIC_IMG      / "logo-48x48.png",
    _STATIC_IMG      / "logo-32x32.png",
    _STATIC_IMG      / "logo-16x16.png",
    # ── Brand logos (dark variant + equiper logos) ────────────────────────────
    _STATIC_IMG      / "dark.png",
    _STATIC_IMG      / "equiper-logo.png",
    _STATIC_IMG      / "equiper-logo2.png",
    # ── Legacy generic names (kept for backward compatibility) ────────────────
    _STATIC_IMG      / "icon.png",
    _STATIC_IMG      / "white.png",
    _STATIC_IMG      / "logo.png",
    # ── Same set mirrored into staticfiles/ (after collectstatic) ────────────
    _STATICFILES_IMG / "logo-1024x1024.png",
    _STATICFILES_IMG / "logo-512x512.png",
    _STATICFILES_IMG / "logo-256x256.png",
    _STATICFILES_IMG / "logo-192x192.png",
    _STATICFILES_IMG / "logo-128x128.png",
    _STATICFILES_IMG / "logo-96x96.png",
    _STATICFILES_IMG / "dark.png",
    _STATICFILES_IMG / "equiper-logo.png",
    _STATICFILES_IMG / "icon.png",
    _STATICFILES_IMG / "logo.png",
    # ── Already in dist/ (re-running the script) ─────────────────────────────
    _DIST_STATIC_IMG / "logo-1024x1024.png",
    _DIST_STATIC_IMG / "logo-512x512.png",
    _DIST_STATIC_IMG / "logo-256x256.png",
    _DIST_SF_IMG     / "logo-1024x1024.png",
    _DIST_SF_IMG     / "logo-256x256.png",
    DIST_APP / "resources"   / "icon.png",
    # ── Project-root legacy fallbacks ────────────────────────────────────────
    PROJECT_ROOT / "resources" / "icon.png",
    PROJECT_ROOT / "assets"    / "icon.png",
    PROJECT_ROOT / "assets"    / "icons" / "app_icon.png",
]

# ─────────────────────────────────────────────────────────────────────────────
# Icon size constants – used when scaling / installing into hicolor theme
# ─────────────────────────────────────────────────────────────────────────────
HICOLOR_SIZES = [16, 22, 24, 32, 48, 64, 96, 128, 256, 512]

# ─────────────────────────────────────────────────────────────────────────────
# Helper utilities
# ─────────────────────────────────────────────────────────────────────────────

def section(title: str):
    """Print a clearly-visible section header to the log."""
    bar = "=" * 70
    logger.info(f"\n{bar}\n  {title}\n{bar}")


def ok(msg: str):   logger.info(f"  ✅  {msg}")
def warn(msg: str): logger.warning(f"  ⚠️   {msg}")
def fail(msg: str): logger.error(f"  ❌  {msg}")


def run_cmd(cmd, cwd=None, timeout=5400, env=None) -> bool:
    """
    Run *cmd* as a subprocess, streaming all output line-by-line to the log.
    Returns True on success, False on failure / timeout.
    Default timeout: 90 minutes (generous for large Django + PySide6 builds).
    """
    logger.info(f"  $ {' '.join(str(c) for c in cmd)}")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env or os.environ.copy(),
        )
        for line in proc.stdout:
            logger.info("    " + line.rstrip())
        proc.wait(timeout=timeout)
        if proc.returncode != 0:
            fail(f"Command exited with code {proc.returncode}")
            return False
        return True
    except subprocess.TimeoutExpired:
        proc.kill()
        fail(f"Command timed out after {timeout}s")
        return False
    except FileNotFoundError:
        fail(f"Executable not found: {cmd[0]}")
        return False
    except Exception as exc:
        fail(f"Unexpected error running command: {exc}")
        return False


def detect_distro() -> dict:
    """
    Parse /etc/os-release and return a dict:
      id          – e.g. "ubuntu", "arch", "fedora"
      id_like     – e.g. "debian", "rhel fedora"
      version     – e.g. "22.04"
      pkg_manager – "apt" | "dnf" | "pacman" | "zypper" | "emerge" |
                    "xbps" | "apk" | "unknown"
    """
    info = {"id": "unknown", "id_like": "", "version": "", "pkg_manager": "unknown"}
    os_release = Path("/etc/os-release")
    if os_release.exists():
        for line in os_release.read_text().splitlines():
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip('"')
            if   k == "ID":         info["id"]      = v.lower()
            elif k == "ID_LIKE":    info["id_like"] = v.lower()
            elif k == "VERSION_ID": info["version"] = v

    family = info["id"] + " " + info["id_like"]

    if any(x in family for x in ("ubuntu", "debian", "mint", "pop", "kali",
                                  "tails", "elementary", "raspbian", "linuxmint")):
        info["pkg_manager"] = "apt"
    elif any(x in family for x in ("fedora", "rhel", "centos", "rocky", "alma",
                                    "ol", "scientific", "oracle")):
        info["pkg_manager"] = "dnf"
    elif any(x in family for x in ("arch", "manjaro", "endeavour", "garuda",
                                    "artix", "blackarch", "parabola")):
        info["pkg_manager"] = "pacman"
    elif any(x in family for x in ("opensuse", "suse", "sles")):
        info["pkg_manager"] = "zypper"
    elif "gentoo" in family:
        info["pkg_manager"] = "emerge"
    elif "void" in family:
        info["pkg_manager"] = "xbps"
    elif "alpine" in family:
        info["pkg_manager"] = "apk"

    return info


# ═════════════════════════════════════════════════════════════════════════════
# BUILD STEPS
# ═════════════════════════════════════════════════════════════════════════════

# ─────────────────────────────────────────────────────────────────────────────
# Step 1 – Detect distro & verify environment
# ─────────────────────────────────────────────────────────────────────────────

def step_check_environment() -> bool:
    section("Step 1 – Detect distro & check environment")

    distro = detect_distro()
    logger.info(f"  Distro       : {distro['id']} {distro['version']}")
    logger.info(f"  ID_LIKE      : {distro['id_like'] or '(none)'}")
    logger.info(f"  Pkg manager  : {distro['pkg_manager']}")
    logger.info(f"  Python       : {sys.version.split()[0]}")
    logger.info(f"  Architecture : {platform.machine()}")
    logger.info(f"  Kernel       : {platform.release()}")
    logger.info(f"  Project root : {PROJECT_ROOT}")
    logger.info(f"  Build log    : {LOG_FILE}")

    # Python version gate
    if sys.version_info < (3, 8):
        fail("Python 3.8+ is required")
        return False
    ok(f"Python {sys.version.split()[0]}")

    # Required project files
    required_files = ["main.py", "manage.py", "build.py", "requirements.txt"]
    missing = [f for f in required_files if not (PROJECT_ROOT / f).exists()]
    if missing:
        fail(f"Missing required project files: {', '.join(missing)}")
        fail("Make sure build_linux_desktop.py lives in the project root.")
        return False
    ok("All required project files present")

    # PyInstaller (just a warning – build.py will install it if absent)
    try:
        import PyInstaller as _pi
        ok(f"PyInstaller {_pi.__version__} already installed")
    except ImportError:
        warn("PyInstaller not installed – build.py will install it automatically")

    # Optional system tools
    for tool in ("upx", "appimagetool", "gtk-update-icon-cache", "convert", "rsvg-convert"):
        if shutil.which(tool):
            ok(f"Optional tool found: {tool}")
        else:
            warn(f"Optional tool not found: {tool} (build will work without it)")

    # Root warning
    if os.geteuid() == 0:
        warn("Running as root – PyInstaller works best as a normal user")

    # Stash distro on the function so main() can retrieve it
    step_check_environment._distro = distro
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Step 2 – Run the full PyInstaller build (build.py)
# ─────────────────────────────────────────────────────────────────────────────

def step_run_pyinstaller_build() -> bool:
    section("Step 2 – Running PyInstaller build  (build.py)")

    if ARGS.skip_pyinstaller:
        warn("--skip-pyinstaller: skipping PyInstaller build")
        if not DIST_APP.exists():
            fail(f"dist/Cirqen not found at {DIST_APP}.")
            fail("Run without --skip-pyinstaller first to create the build.")
            return False
        files  = list(DIST_APP.rglob("*"))
        size   = sum(f.stat().st_size for f in files if f.is_file()) / (1024*1024)
        ok(f"Using existing build: {len(files)} files, {size:.1f} MB")
        return True

    if not BUILD_BACKUP.exists():
        fail(f"build.py not found at {BUILD_BACKUP}")
        return False

    logger.info("  This can take 15–40 minutes for a large Django + PySide6 project.")
    logger.info("  All output is streamed here and saved to build_logs/")
    logger.info("")

    if not run_cmd([sys.executable, str(BUILD_BACKUP)],
                   cwd=str(PROJECT_ROOT),
                   timeout=5400):       # 90-minute cap
        fail("build.py reported an error. Fix the errors above then re-run.")
        return False

    if not DIST_APP.exists():
        fail(f"Expected dist output not found: {DIST_APP}")
        fail("build.py finished without creating the Cirqen folder.")
        return False

    files  = list(DIST_APP.rglob("*"))
    size   = sum(f.stat().st_size for f in files if f.is_file()) / (1024*1024)
    ok(f"PyInstaller build complete: {len(files)} files, {size:.1f} MB")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Step 3 – Locate / copy / generate the application icon
#
# Priority:
#   1. Best available PNG from static/images/logo-<WxH>.png  (largest first)
#   2. Brand logos: dark.png, equiper-logo.png, equiper-logo2.png
#   3. Legacy generic names: icon.png, white.png, logo.png
#   4. Same paths mirrored inside staticfiles/ and dist/
#   5. Generated PIL fallback
#
# After selection the chosen file is:
#   • Copied to  dist/Cirqen/resources/icon.png  (primary runtime location)
#   • Optionally scaled and installed into the hicolor theme tree inside dist
#     so that the install.sh script can populate /usr/share/icons/ correctly
#     on every distro.
# ─────────────────────────────────────────────────────────────────────────────

def _generate_fallback_icon(dest: Path):
    """
    Try to generate a 256×256 Cirqen icon with PIL.
    If PIL is not installed, write a minimal but valid 1×1 PNG so that the
    rest of the build never fails due to a missing icon file.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont

        img  = Image.new("RGB", (256, 256), "#080A0F")
        draw = ImageDraw.Draw(img)
        draw.ellipse([10, 10, 246, 246], outline="#1a2a3a", width=3)
        draw.ellipse([20, 20, 236, 236], outline="#4FC3FF", width=6, fill="#111827")
        draw.ellipse([50, 50, 206, 206], outline="#4FC3FF", width=2)

        font = None
        for font_name in ("DejaVuSans-Bold.ttf", "FreeSansBold.ttf",
                          "LiberationSans-Bold.ttf", "NotoSans-Bold.ttf"):
            try:
                font = ImageFont.truetype(font_name, 100)
                break
            except Exception:
                pass
        if font is None:
            font = ImageFont.load_default()

        draw.text((128, 128), "C", font=font, fill="#4FC3FF", anchor="mm")
        dest.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest, format="PNG")
        ok(f"Generated fallback icon via PIL: {dest.name}")

    except ImportError:
        # Minimum valid PNG (1×1 white pixel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
            b'\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00'
            b'\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18'
            b'\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        warn("PIL not available – a 1×1 placeholder icon was written.")
        warn("Install Pillow (pip install Pillow) for a proper icon.")


def _scale_icon_with_pillow(src: Path, dest: Path, size: int) -> bool:
    """
    Scale *src* to a square *size*×*size* PNG and write to *dest*.
    Returns True if PIL is available and scaling succeeded, False otherwise.
    LANCZOS resampling gives crisp results at every size.
    """
    try:
        from PIL import Image
        with Image.open(src) as im:
            im = im.convert("RGBA")          # preserve transparency
            im = im.resize((size, size), Image.LANCZOS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, format="PNG", optimize=True)
        return True
    except Exception:
        return False


def _build_hicolor_tree(src_icon: Path, hicolor_root: Path):
    """
    Populate a hicolor icon theme tree at *hicolor_root* from *src_icon*.

    Structure created:
        <hicolor_root>/
            hicolor/
                16x16/apps/<APP_ID>.png
                22x22/apps/<APP_ID>.png
                24x24/apps/<APP_ID>.png
                32x32/apps/<APP_ID>.png
                48x48/apps/<APP_ID>.png
                64x64/apps/<APP_ID>.png
                96x96/apps/<APP_ID>.png
                128x128/apps/<APP_ID>.png
                256x256/apps/<APP_ID>.png    ← primary (used by most DEs)
                512x512/apps/<APP_ID>.png
                scalable/apps/<APP_ID>.png   ← copy of best source for SVG slot

    If PIL/Pillow is present: every size is properly scaled.
    If PIL is absent: we try to find a matching logo-<WxH>.png from the
    project's static/images/ for that exact size, otherwise copy the source
    as-is (DE will scale on its own, which is good enough).
    """
    pil_available = True
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        pil_available = False
        warn("Pillow not installed – hicolor tree will use size-matched source files "
             "where possible; DEs will scale the rest automatically.")

    hicolor_dir = hicolor_root / "hicolor"

    for size in HICOLOR_SIZES:
        size_dir = hicolor_dir / f"{size}x{size}" / "apps"
        size_dir.mkdir(parents=True, exist_ok=True)
        dest = size_dir / f"{APP_ID}.png"

        if pil_available:
            if _scale_icon_with_pillow(src_icon, dest, size):
                continue
            # PIL failed for this size – fall through to copy logic

        # No PIL: look for an exact-match logo-<WxH>.png in the project
        exact_match = _STATIC_IMG / f"logo-{size}x{size}.png"
        if not exact_match.exists():
            exact_match = _STATICFILES_IMG / f"logo-{size}x{size}.png"

        if exact_match.exists() and exact_match.stat().st_size > 100:
            shutil.copy2(exact_match, dest)
        else:
            # Last resort: copy best source; DE scales it (acceptable quality)
            shutil.copy2(src_icon, dest)

    # scalable/ slot – copy our best PNG there too (not a true SVG, but the
    # slot is still useful for DEs that prefer the scalable category)
    scalable_dir = hicolor_dir / "scalable" / "apps"
    scalable_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_icon, scalable_dir / f"{APP_ID}.png")

    ok(f"hicolor theme tree built at resources/hicolor/  ({len(HICOLOR_SIZES)} sizes)")


def step_ensure_icon() -> Path:
    """
    Search for the best icon starting with static/images/logo-1024x1024.png.
    Copy the first valid file found to dist/Cirqen/resources/icon.png.
    Then build a full hicolor theme tree under dist/Cirqen/resources/hicolor/
    so that install.sh can populate /usr/share/icons/ on every distro.
    Returns the destination path (dist/Cirqen/resources/icon.png).
    """
    section("Step 3 – Locating application icon")

    dest = DIST_APP / "resources" / "icon.png"
    dest.parent.mkdir(parents=True, exist_ok=True)

    logger.info("  Icon search order (first valid file wins):")
    for p in ICON_SEARCH_PATHS:
        exists = p.exists() and p.stat().st_size > 100
        marker = "✓" if exists else "·"
        logger.info(f"    {marker}  {p}")

    selected: Path | None = None
    for candidate in ICON_SEARCH_PATHS:
        if candidate.exists() and candidate.stat().st_size > 100:
            selected = candidate
            break

    if selected is None:
        logger.info("")
        warn("No icon found in any search path. Generating a fallback icon …")
        _generate_fallback_icon(dest)
    else:
        logger.info(f"\n  Selected  : {selected}")
        logger.info(f"  File size : {selected.stat().st_size:,} bytes")
        if selected.resolve() != dest.resolve():
            shutil.copy2(selected, dest)
            logger.info(f"  Copied to : {dest}")
        else:
            logger.info(f"  Already at target location.")
        ok(f"Icon ready → resources/icon.png  ({dest.stat().st_size:,} bytes)")

    # Build the full hicolor multi-size tree for install.sh
    _build_hicolor_tree(dest, DIST_APP / "resources")

    return dest


# ─────────────────────────────────────────────────────────────────────────────
# Step 4 – start_cirqen.sh   (cross-distro launcher)
# ─────────────────────────────────────────────────────────────────────────────

def step_create_launcher() -> bool:
    section("Step 4 – Creating cross-distro launcher  (start_cirqen.sh)")

    launcher = DIST_APP / "start_cirqen.sh"

    launcher.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        # ══════════════════════════════════════════════════════════════════════
        # {APP_NAME} {APP_VERSION} – Linux Launcher
        # Compatible with:
        #   Debian/Ubuntu family  : Ubuntu, Debian, Mint, Pop!_OS, Kali, Tails,
        #                           Elementary, Raspbian
        #   Red Hat / Fedora family: Fedora, RHEL, CentOS, Rocky, AlmaLinux
        #   Arch family           : Arch, Manjaro, EndeavourOS, Garuda
        #   SUSE family           : openSUSE Leap, Tumbleweed
        #   Other                 : Void Linux, Alpine, Gentoo
        # ══════════════════════════════════════════════════════════════════════

        # Resolve the real directory of this script even through symlinks
        SCRIPT="$(readlink -f "${{BASH_SOURCE[0]}}")"
        SCRIPT_DIR="$(dirname "$SCRIPT")"
        EXECUTABLE="$SCRIPT_DIR/{APP_NAME}"

        # ── Sanity checks ─────────────────────────────────────────────────────
        if [ ! -f "$EXECUTABLE" ]; then
            echo "❌  Executable not found: $EXECUTABLE"
            echo "    Make sure you run this script from inside the Cirqen folder."
            exit 1
        fi

        if [ ! -x "$EXECUTABLE" ]; then
            echo "⚠️   Fixing permissions on: $EXECUTABLE"
            chmod +x "$EXECUTABLE"
        fi

        # ── Display server detection ──────────────────────────────────────────
        # Prefer X11/XWayland for QtWebEngine stability.
        # Fall back to native Wayland only when X11 is truly absent.
        if [ -n "${{WAYLAND_DISPLAY:-}}" ] && [ -z "${{DISPLAY:-}}" ]; then
            # Pure Wayland session (no XWayland)
            export QT_QPA_PLATFORM="${{QT_QPA_PLATFORM:-wayland}}"
        else
            # X11 or XWayland (default – best compatibility with QtWebEngine)
            export QT_QPA_PLATFORM="${{QT_QPA_PLATFORM:-xcb}}"
        fi

        # ── QtWebEngine / Chromium sandbox flags ──────────────────────────────
        # Required on all Linux distros to prevent GPU-process sandbox crashes.
        # These flags are safe and do not reduce real functionality.
        export QTWEBENGINE_CHROMIUM_FLAGS="\\
            --no-sandbox \\
            --disable-gpu \\
            --disable-software-rasterizer \\
            --disable-gpu-sandbox \\
            --single-process \\
            --disable-dev-shm-usage \\
            --disable-setuid-sandbox \\
            --no-first-run \\
            --no-zygote"

        export QTWEBENGINE_DISABLE_SANDBOX=1

        # Suppress verbose Qt / Chromium debug noise in the terminal
        export QT_LOGGING_RULES="*.debug=false;qt.webenginecontext.info=false"

        # ── Embedded runtime library path ─────────────────────────────────────
        # Ensures the bundled PostgreSQL .so files are found before any
        # system-installed libraries, avoiding version mismatches.
        PG_LIB="$SCRIPT_DIR/runtime/postgresql/lib"
        if [ -d "$PG_LIB" ]; then
            export LD_LIBRARY_PATH="$PG_LIB:${{LD_LIBRARY_PATH:-}}"
        fi

        # ── Locale ────────────────────────────────────────────────────────────
        # Prevents "could not determine locale" errors on minimal distros
        # (Tails, Alpine, Docker-based systems).
        export LANG="${{LANG:-en_US.UTF-8}}"
        export LC_ALL="${{LC_ALL:-en_US.UTF-8}}"

        # ── XDG base directories ──────────────────────────────────────────────
        # Provides explicit paths for the app's data, config and cache.
        # Defaults to the standard XDG locations if not already set.
        export XDG_DATA_HOME="${{XDG_DATA_HOME:-$HOME/.local/share}}"
        export XDG_CONFIG_HOME="${{XDG_CONFIG_HOME:-$HOME/.config}}"
        export XDG_CACHE_HOME="${{XDG_CACHE_HOME:-$HOME/.cache}}"

        # ── Tails / live-system note ──────────────────────────────────────────
        # On Tails, $HOME is /home/amnesia. Data is lost on reboot unless
        # Persistent Storage is enabled and includes ~/.local/share/cirqen
        if [ "${{USER:-}}" = "amnesia" ] || [ "${{HOME:-}}" = "/home/amnesia" ]; then
            echo "ℹ️   Running on Tails. Enable Persistent Storage to keep data across reboots."
        fi

        # ── PostgreSQL run-directory permission fix ───────────────────────────
        # /var/run/postgresql is owned by the system 'postgres' user (mode 2775).
        # Our embedded PostgreSQL runs as the logged-in user and cannot write its
        # lock file there → FATAL crash before the app even opens.
        # We fix this once per machine using a flag file so the user is only
        # ever asked for their password a single time.
        _PG_RUN_DIR="/var/run/postgresql"
        _PG_FLAG="$XDG_DATA_HOME/cirqen/.pg_perms_ok"

        _fix_pg_permissions() {{
            # Already fixed on this machine?
            [ -f "$_PG_FLAG" ] && return 0
            # Already writable (e.g. root or group postgres already set)?
            [ -w "$_PG_RUN_DIR" ] && {{ mkdir -p "$(dirname "$_PG_FLAG")"; touch "$_PG_FLAG"; return 0; }}

            echo ""
            echo "┌──────────────────────────────────────────────────────────┐"
            echo "│  {APP_NAME} – One-time setup  (requires administrator)   │"
            echo "│                                                           │"
            echo "│  {APP_NAME} needs a small system tweak so its built-in   │"
            echo "│  database can start. This will only happen once.         │"
            echo "└──────────────────────────────────────────────────────────┘"
            echo ""

            # Write the fix to a temp script so we can pass it cleanly to
            # pkexec / sudo without shell-quoting nightmares.
            local _HELPER
            _HELPER=$(mktemp /tmp/cirqen_setup_XXXXXX.sh)
            cat > "$_HELPER" <<'__HELPER__'
#!/bin/bash
chmod 1777 /var/run/postgresql
echo "d /var/run/postgresql 1777 root root -" > /etc/tmpfiles.d/cirqen-postgresql.conf
__HELPER__
            chmod +x "$_HELPER"

            local _RC=0
            if command -v pkexec &>/dev/null; then
                # pkexec shows a native GUI password dialog — ideal for non-technical users
                pkexec bash "$_HELPER" || _RC=$?
            elif command -v sudo &>/dev/null; then
                echo "  Please enter your system password to complete setup:"
                sudo bash "$_HELPER" || _RC=$?
            else
                echo "  ❌  Could not apply setup (pkexec and sudo not found)."
                echo "     Ask your administrator to run once:"
                echo "       sudo chmod 1777 /var/run/postgresql"
                rm -f "$_HELPER"
                return 1
            fi
            rm -f "$_HELPER"

            if [ "$_RC" -eq 0 ]; then
                mkdir -p "$(dirname "$_PG_FLAG")"
                touch "$_PG_FLAG"
                echo "  ✅  Setup complete — you will not be asked again."
            else
                echo "  ⚠️   Setup was cancelled or failed. {APP_NAME} may not start correctly."
                echo "      If the database fails to start, run install.sh as administrator."
            fi
        }}

        _fix_pg_permissions

        # ── Launch ────────────────────────────────────────────────────────────
        echo "🚀  Starting {APP_NAME} {APP_VERSION} …"
        echo "    Data : $XDG_DATA_HOME/cirqen"
        echo "    Logs : $XDG_DATA_HOME/cirqen/logs"
        echo ""

        exec "$EXECUTABLE" "$@"
    """))

    launcher.chmod(0o755)
    ok(f"Created: {launcher.name}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Step 5 – .desktop entry files
# ─────────────────────────────────────────────────────────────────────────────

def step_create_desktop_entry(icon_path: Path) -> bool:
    section("Step 5 – Creating .desktop entry files")

    # Shared key=value lines (same for both variants)
    shared = textwrap.dedent(f"""\
        [Desktop Entry]
        Version=1.0
        Type=Application
        Name={APP_NAME}
        GenericName={APP_DESCRIPTION}
        Comment={APP_DESCRIPTION}
        Terminal=false
        Categories={APP_CATEGORIES}
        StartupNotify=true
        StartupWMClass={APP_NAME}
        Keywords=calibration;maintenance;medical;physics;equipment;cmms;
        MimeType=
    """)

    # ── 1. Installed .desktop  (used after sudo ./install.sh) ────────────────
    #    Exec and Icon point to /opt/Cirqen/ – valid only once installed.
    installed_exec = INSTALL_DIR / "start_cirqen.sh"
    # Icon references the APP_ID name; the hicolor cache resolves the size.
    installed_desktop = DIST_APP / f"{APP_ID}.desktop"
    installed_desktop.write_text(
        shared
        + f"Exec={installed_exec}\n"
        + f"Icon={APP_ID}\n"          # ← theme-name reference (distro-agnostic)
    )
    installed_desktop.chmod(installed_desktop.stat().st_mode | stat.S_IEXEC)
    ok(f"Created installed desktop entry  : {installed_desktop.name}")

    # ── 2. Local / portable .desktop  (works without installing) ─────────────
    #    Exec and Icon use absolute paths to dist/Cirqen so it works immediately.
    local_exec = (DIST_APP / "start_cirqen.sh").absolute()
    local_icon = icon_path.absolute()

    local_desktop = DIST_APP / f"{APP_NAME}.desktop"
    local_desktop.write_text(
        shared
        + f"Exec={local_exec}\n"
        + f"Icon={local_icon}\n"      # ← absolute path for portable use
    )
    local_desktop.chmod(local_desktop.stat().st_mode | stat.S_IEXEC)
    ok(f"Created local/portable desktop entry: {local_desktop.name}")

    logger.info("")
    logger.info("  To add Cirqen to your personal app menu WITHOUT installing:")
    logger.info(f"    cp \"{local_desktop}\" ~/.local/share/applications/")
    logger.info(f"    update-desktop-database ~/.local/share/applications/")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Step 6 – install.sh  (system-wide, works on all major distros)
#
# Now installs the full hicolor tree from resources/hicolor/ so every
# desktop environment (GNOME, KDE, XFCE, LXDE, Cinnamon …) gets the
# correctly-sized icon without any extra tooling.
# ─────────────────────────────────────────────────────────────────────────────

def step_create_install_script() -> bool:
    section("Step 6 – Creating install.sh  (system-wide installer)")

    install_script = DIST_APP / "install.sh"

    install_script.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        # ══════════════════════════════════════════════════════════════════════
        # {APP_NAME} {APP_VERSION} – System-Wide Installer
        #
        # Compatible with:
        #   Ubuntu / Debian / Mint / Pop!_OS / Kali / Tails / Elementary
        #   Fedora / RHEL / CentOS / Rocky / AlmaLinux
        #   Arch Linux / Manjaro / EndeavourOS
        #   openSUSE Leap / Tumbleweed
        #   Void Linux  ·  Alpine Linux  ·  Gentoo
        #
        # Usage:  sudo ./install.sh
        # ══════════════════════════════════════════════════════════════════════
        set -euo pipefail

        # ── Constants ─────────────────────────────────────────────────────────
        APP_NAME="{APP_NAME}"
        APP_VERSION="{APP_VERSION}"
        APP_ID="{APP_ID}"
        INSTALL_DIR="/opt/$APP_NAME"
        DESKTOP_DEST="/usr/share/applications/$APP_ID.desktop"
        HICOLOR_ROOT="/usr/share/icons/hicolor"
        PIXMAPS_DIR="/usr/share/pixmaps"
        BIN_LINK="/usr/local/bin/cirqen"
        SCRIPT_DIR="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"

        # ── Colour helpers ────────────────────────────────────────────────────
        RED='\\033[0;31m'; GREEN='\\033[0;32m'
        YELLOW='\\033[1;33m'; CYAN='\\033[0;36m'; BOLD='\\033[1m'; NC='\\033[0m'
        ok()   {{ echo -e "${{GREEN}}  ✅  $1${{NC}}"; }}
        warn() {{ echo -e "${{YELLOW}}  ⚠️   $1${{NC}}"; }}
        info() {{ echo -e "${{CYAN}}  ℹ️   $1${{NC}}"; }}
        err()  {{ echo -e "${{RED}}  ❌  $1${{NC}}" >&2; exit 1; }}

        # ── Root check ────────────────────────────────────────────────────────
        [ "$EUID" -eq 0 ] || err "Please run as root:  sudo ./install.sh"

        echo ""
        echo -e "${{BOLD}}══════════════════════════════════════════════════════════════${{NC}}"
        echo -e "${{BOLD}}  Installing $APP_NAME $APP_VERSION${{NC}}"
        echo -e "${{BOLD}}══════════════════════════════════════════════════════════════${{NC}}"
        echo ""

        # ── Detect distro for conditional steps ───────────────────────────────
        DISTRO_ID="unknown"
        [ -f /etc/os-release ] && DISTRO_ID=$(. /etc/os-release; echo "${{ID:-unknown}}")

        # ── STEP 1: Copy application files ───────────────────────────────────
        echo "📦  Copying application files → $INSTALL_DIR …"
        rm -rf "$INSTALL_DIR"
        cp -r "$SCRIPT_DIR" "$INSTALL_DIR"

        # Reset all permissions cleanly
        find "$INSTALL_DIR" -type f -exec chmod 644 {{}} \\;
        find "$INSTALL_DIR" -type d -exec chmod 755 {{}} \\;

        # Re-apply execute bit where needed
        for f in \\
            "$INSTALL_DIR/{APP_NAME}" \\
            "$INSTALL_DIR/start_cirqen.sh" \\
            "$INSTALL_DIR/install.sh" \\
            "$INSTALL_DIR/uninstall.sh" \\
            "$INSTALL_DIR/launch_cirqen.py" \\
            "$INSTALL_DIR/cleanup_cirqen.py"
        do
            [ -f "$f" ] && chmod +x "$f"
        done

        # Make embedded runtime binaries executable (PostgreSQL, Redis)
        if [ -d "$INSTALL_DIR/runtime" ]; then
            find "$INSTALL_DIR/runtime" -type f | while read -r BIN; do
                head -c 4 "$BIN" 2>/dev/null | grep -qP "^\\x7fELF" && chmod +x "$BIN" || true
            done
        fi

        chown -R root:root "$INSTALL_DIR"
        ok "Files installed to $INSTALL_DIR"

        # ── STEP 2: Install icons (full hicolor multi-size tree) ──────────────
        # The build system pre-generated correctly-sized PNGs for every standard
        # hicolor size inside resources/hicolor/.  We copy them all so that
        # GNOME, KDE, XFCE, LXDE, Cinnamon and others each pick the right size.
        echo "🎨  Installing icons …"

        HICOLOR_SRC="$INSTALL_DIR/resources/hicolor"
        ICON_256="$HICOLOR_SRC/256x256/apps/$APP_ID.png"

        if [ -d "$HICOLOR_SRC" ]; then
            # Walk every size directory and copy into system hicolor tree
            for SIZE_DIR in "$HICOLOR_SRC"/*/; do
                SIZE="$(basename "$SIZE_DIR")"            # e.g. "256x256"
                SRC_PNG="$SIZE_DIR/apps/$APP_ID.png"
                if [ -f "$SRC_PNG" ]; then
                    DEST_DIR="$HICOLOR_ROOT/$SIZE/apps"
                    mkdir -p "$DEST_DIR"
                    cp "$SRC_PNG" "$DEST_DIR/$APP_ID.png"
                fi
            done
            ok "hicolor icon tree installed ($HICOLOR_ROOT)"
        else
            # Fallback: install the single icon.png at 256x256
            SINGLE_ICON="$INSTALL_DIR/resources/icon.png"
            if [ -f "$SINGLE_ICON" ]; then
                mkdir -p "$HICOLOR_ROOT/256x256/apps"
                cp "$SINGLE_ICON" "$HICOLOR_ROOT/256x256/apps/$APP_ID.png"
                ICON_256="$HICOLOR_ROOT/256x256/apps/$APP_ID.png"
                warn "hicolor source tree not found – installed single icon at 256x256"
            else
                warn "No icon found – application will launch without a taskbar icon"
            fi
        fi

        # Also copy to /usr/share/pixmaps/ for older DEs (LXDE, openbox, etc.)
        SINGLE_ICON="$INSTALL_DIR/resources/icon.png"
        if [ -f "$SINGLE_ICON" ]; then
            mkdir -p "$PIXMAPS_DIR"
            cp "$SINGLE_ICON" "$PIXMAPS_DIR/$APP_ID.png"
            ok "Pixmap icon installed ($PIXMAPS_DIR/$APP_ID.png)"
        fi

        # Refresh the icon cache (guards against gtk-update-icon-cache absence)
        if command -v gtk-update-icon-cache &>/dev/null; then
            gtk-update-icon-cache -f -t "$HICOLOR_ROOT" 2>/dev/null || true
            ok "GTK icon cache refreshed"
        elif command -v gtk4-update-icon-cache &>/dev/null; then
            gtk4-update-icon-cache -f "$HICOLOR_ROOT" 2>/dev/null || true
            ok "GTK4 icon cache refreshed"
        else
            warn "gtk-update-icon-cache not found – icon may not appear until next login"
            info "On Arch: sudo pacman -S gtk-update-icon-cache"
            info "On Fedora/RHEL: sudo dnf install gtk3"
            info "On Alpine: sudo apk add gtk+3.0"
        fi

        # ── STEP 3: Fix PostgreSQL run-directory permissions ─────────────────
        # The embedded PostgreSQL starts as the logged-in user, not the system
        # 'postgres' account. On a fresh Linux install /var/run/postgresql is
        # mode 2775 (group postgres), so the lock-file write fails instantly:
        #   FATAL: could not create lock file "...": Permission denied
        # chmod 1777 (world-writable + sticky) fixes this, and the tmpfiles.d
        # rule re-applies it automatically on every boot.
        echo "🗄️   Configuring database run directory …"
        if [ -d "/var/run/postgresql" ]; then
            chmod 1777 /var/run/postgresql
            echo "d /var/run/postgresql 1777 root root -" > /etc/tmpfiles.d/cirqen-postgresql.conf
            ok "PostgreSQL run directory configured (permissions will survive reboots)"
        else
            warn "/var/run/postgresql not found — it may be created automatically on first run"
        fi

        # Mark as done so start_cirqen.sh never shows the permission dialog again.
        # We write into each existing user's home directory.
        for _U_HOME in /home/*/; do
            _FLAG="$_U_HOME/.local/share/cirqen/.pg_perms_ok"
            _U="$(basename "$_U_HOME")"
            mkdir -p "$(dirname "$_FLAG")"
            touch "$_FLAG"
            chown "$_U:$_U" "$_FLAG" 2>/dev/null || true
        done
        # Also mark for root, just in case
        mkdir -p /root/.local/share/cirqen
        touch /root/.local/share/cirqen/.pg_perms_ok

        # ── STEP 4: Install .desktop entry ───────────────────────────────────
        echo "📋  Installing desktop entry …"
        DESKTOP_SRC="$INSTALL_DIR/{APP_ID}.desktop"
        if [ -f "$DESKTOP_SRC" ]; then
            mkdir -p "$(dirname "$DESKTOP_DEST")"
            cp "$DESKTOP_SRC" "$DESKTOP_DEST"
            chmod 644 "$DESKTOP_DEST"
            # Rewrite Exec to point to the installed launcher;
            # Icon stays as the APP_ID theme-name so every DE resolves it.
            sed -i "s|^Exec=.*|Exec=$INSTALL_DIR/start_cirqen.sh|" "$DESKTOP_DEST"
            sed -i "s|^Icon=.*|Icon=$APP_ID|"                       "$DESKTOP_DEST"

            # Refresh the desktop database so the app appears in menus immediately
            if command -v update-desktop-database &>/dev/null; then
                update-desktop-database /usr/share/applications/ 2>/dev/null || true
            fi
            ok "Desktop entry installed: $DESKTOP_DEST"
        else
            warn "Desktop file not found – app menu entry will be skipped"
        fi

        # ── STEP 5: /usr/local/bin symlink ───────────────────────────────────
        echo "🔗  Creating command-line shortcut (cirqen) …"
        ln -sf "$INSTALL_DIR/start_cirqen.sh" "$BIN_LINK"
        ok "Shortcut created: cirqen → $INSTALL_DIR/start_cirqen.sh"

        # ── STEP 6: Distro-specific notes ────────────────────────────────────
        case "$DISTRO_ID" in
            fedora|rhel|centos|rocky|alma)
                echo ""
                info "Fedora/RHEL note: if SELinux blocks the app on first run, run:"
                info "  sudo chcon -R -t bin_t $INSTALL_DIR/{APP_NAME}"
                ;;
            opensuse*|suse*)
                echo ""
                info "openSUSE note: if AppArmor blocks the app, add it to allowed binaries."
                ;;
            arch|manjaro|endeavouros|garuda)
                echo ""
                info "Arch note: if the icon is missing from the menu, run:"
                info "  gtk-update-icon-cache -f -t /usr/share/icons/hicolor"
                ;;
        esac

        # ── Done ──────────────────────────────────────────────────────────────
        echo ""
        echo -e "${{BOLD}}══════════════════════════════════════════════════════════════${{NC}}"
        echo -e "${{GREEN}}${{BOLD}}  ✅  $APP_NAME $APP_VERSION installed successfully!${{NC}}"
        echo -e "${{BOLD}}══════════════════════════════════════════════════════════════${{NC}}"
        echo ""
        echo "  Launch options:"
        echo "    • Application menu  →  $APP_NAME"
        echo "    • Terminal          →  cirqen"
        echo "    • Direct            →  $INSTALL_DIR/start_cirqen.sh"
        echo ""
        echo "  First-run notes:"
        echo "    • User data is stored per-user in  ~/.local/share/cirqen/"
        echo "    • On first launch the database initialises (takes ~1 minute)"
        echo "    • Default login:  maina.wairegi  /  ChangeMe123!"
        echo "    • ⚠️   Change the password immediately after first login!"
        echo ""
    """))

    install_script.chmod(0o755)
    ok(f"Created: {install_script.name}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Step 7 – uninstall.sh
# ─────────────────────────────────────────────────────────────────────────────

def step_create_uninstall_script() -> bool:
    section("Step 7 – Creating uninstall.sh")

    uninstall = DIST_APP / "uninstall.sh"

    uninstall.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        # ══════════════════════════════════════════════════════════════════════
        # {APP_NAME} – Uninstaller
        # Usage:  sudo ./uninstall.sh
        # ══════════════════════════════════════════════════════════════════════
        set -euo pipefail

        APP_NAME="{APP_NAME}"
        APP_ID="{APP_ID}"
        INSTALL_DIR="/opt/$APP_NAME"
        BIN_LINK="/usr/local/bin/cirqen"
        HICOLOR_ROOT="/usr/share/icons/hicolor"

        RED='\\033[0;31m'; GREEN='\\033[0;32m'; YELLOW='\\033[1;33m'; NC='\\033[0m'
        ok()   {{ echo -e "${{GREEN}}  ✅  $1${{NC}}"; }}
        warn() {{ echo -e "${{YELLOW}}  ⚠️   $1${{NC}}"; }}
        err()  {{ echo -e "${{RED}}  ❌  $1${{NC}}" >&2; exit 1; }}

        [ "$EUID" -eq 0 ] || err "Please run as root:  sudo ./uninstall.sh"

        echo ""
        echo "══════════════════════════════════════════════════════════════"
        echo "  Uninstalling $APP_NAME …"
        echo "══════════════════════════════════════════════════════════════"
        echo ""

        # Stop any running Cirqen processes first
        echo "  Stopping running Cirqen processes …"
        pkill -f "{APP_NAME}"          2>/dev/null || true
        pkill -f "cirqen"              2>/dev/null || true
        pkill -f "start_cirqen.sh"     2>/dev/null || true
        sleep 1
        ok "Processes stopped"

        # Remove application files
        if [ -d "$INSTALL_DIR" ]; then
            rm -rf "$INSTALL_DIR"
            ok "Removed $INSTALL_DIR"
        else
            warn "$INSTALL_DIR not found – already uninstalled?"
        fi

        # Remove all hicolor icon sizes
        echo "  Removing hicolor icons …"
        for SIZE in 16x16 22x22 24x24 32x32 48x48 64x64 96x96 128x128 256x256 512x512 scalable; do
            rm -f "$HICOLOR_ROOT/$SIZE/apps/$APP_ID.png"
        done
        ok "Removed hicolor icons"

        # Remove desktop integration files
        rm -f "/usr/share/applications/$APP_ID.desktop"
        rm -f "/usr/share/pixmaps/$APP_ID.png"
        rm -f "$BIN_LINK"
        ok "Removed desktop entry, pixmap and symlink"

        # Refresh caches
        command -v update-desktop-database &>/dev/null && \\
            update-desktop-database /usr/share/applications/ 2>/dev/null || true
        command -v gtk-update-icon-cache &>/dev/null && \\
            gtk-update-icon-cache -f -t "$HICOLOR_ROOT" 2>/dev/null || true
        command -v gtk4-update-icon-cache &>/dev/null && \\
            gtk4-update-icon-cache -f "$HICOLOR_ROOT" 2>/dev/null || true

        echo ""
        ok "$APP_NAME uninstalled."
        echo ""
        echo "  ℹ️   User data was NOT removed."
        echo "      To remove it for the current user:"
        echo "        rm -rf ~/.local/share/cirqen"
        echo "      To remove it for ALL users:"
        echo "        for d in /home/*; do rm -rf \"\\$d/.local/share/cirqen\"; done"
        echo ""
    """))

    uninstall.chmod(0o755)
    ok(f"Created: {uninstall.name}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Step 8 – AppDir  (foundation for an AppImage)
# ─────────────────────────────────────────────────────────────────────────────

def step_create_appdir() -> bool:
    section("Step 8 – Creating AppDir structure  (AppImage-ready)")

    if ARGS.skip_appdir:
        warn("--skip-appdir: skipping AppDir creation")
        return True

    appdir = DIST_DIR / f"{APP_NAME}.AppDir"
    if appdir.exists():
        logger.info(f"  Removing existing AppDir …")
        shutil.rmtree(appdir)
    appdir.mkdir(parents=True)

    # Standard FHS layout inside AppDir
    usr_bin = appdir / "usr" / "bin"
    usr_bin.mkdir(parents=True)
    (appdir / "usr" / "lib").mkdir(parents=True)

    # Copy entire dist/Cirqen into AppDir/usr/bin/Cirqen/
    app_dest = usr_bin / APP_NAME
    logger.info(f"  Copying Cirqen → AppDir/usr/bin/Cirqen/  (may take a minute) …")
    shutil.copytree(DIST_APP, app_dest, symlinks=True)
    ok(f"App files copied to AppDir")

    # ── AppRun ────────────────────────────────────────────────────────────────
    apprun = appdir / "AppRun"
    apprun.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        # AppImage entry point – executed by the AppImage runtime on launch
        SELF="$(readlink -f "$0")"
        HERE="$(dirname "$SELF")"

        # QtWebEngine flags (same as start_cirqen.sh)
        export QTWEBENGINE_CHROMIUM_FLAGS="\\
            --no-sandbox --disable-gpu --disable-software-rasterizer \\
            --disable-gpu-sandbox --single-process --disable-dev-shm-usage \\
            --disable-setuid-sandbox --no-first-run --no-zygote"
        export QTWEBENGINE_DISABLE_SANDBOX=1
        export QT_LOGGING_RULES="*.debug=false;qt.webenginecontext.info=false"

        # Embedded PostgreSQL library path
        PG_LIB="$HERE/usr/bin/{APP_NAME}/runtime/postgresql/lib"
        [ -d "$PG_LIB" ] && export LD_LIBRARY_PATH="$PG_LIB:${{LD_LIBRARY_PATH:-}}"

        export LANG="${{LANG:-en_US.UTF-8}}"
        export LC_ALL="${{LC_ALL:-en_US.UTF-8}}"
        export XDG_DATA_HOME="${{XDG_DATA_HOME:-$HOME/.local/share}}"

        exec "$HERE/usr/bin/{APP_NAME}/{APP_NAME}" "$@"
    """))
    apprun.chmod(0o755)
    ok("Created AppRun")

    # ── .desktop in AppDir root  (required by appimagetool) ──────────────────
    desktop_src = DIST_APP / f"{APP_ID}.desktop"
    if desktop_src.exists():
        dst = appdir / f"{APP_ID}.desktop"
        content = desktop_src.read_text()
        # AppImage context: Exec = app binary name, Icon = app-id (no path/extension)
        lines = []
        for line in content.splitlines():
            if line.startswith("Exec="):
                lines.append(f"Exec={APP_NAME}")
            elif line.startswith("Icon="):
                lines.append(f"Icon={APP_ID}")
            else:
                lines.append(line)
        dst.write_text("\n".join(lines) + "\n")
        ok("Adjusted .desktop entry placed in AppDir root")

    # ── Icons in AppDir root  (multiple names required by the AppImage spec) ──
    # appimagetool expects both <APP_ID>.png and .DirIcon in the root.
    # We use the best available size (prefer 256x256 from the hicolor tree).
    hicolor_256 = DIST_APP / "resources" / "hicolor" / "256x256" / "apps" / f"{APP_ID}.png"
    fallback_icon = DIST_APP / "resources" / "icon.png"
    appimage_icon = hicolor_256 if hicolor_256.exists() else fallback_icon

    if appimage_icon.exists():
        for name in (f"{APP_ID}.png", f"{APP_NAME}.png", ".DirIcon"):
            shutil.copy2(appimage_icon, appdir / name)
        ok(f"Icons placed in AppDir root (source: {appimage_icon.name})")

    ok(f"AppDir ready: dist/{APP_NAME}.AppDir/")
    logger.info("")
    logger.info("  ── To build an AppImage ──────────────────────────────────────")
    logger.info("  # Download appimagetool (once):")
    logger.info("  wget https://github.com/AppImage/AppImageKit/releases/latest/download/appimagetool-x86_64.AppImage")
    logger.info("  chmod +x appimagetool-x86_64.AppImage")
    logger.info("")
    logger.info("  # Build:")
    logger.info(f"  ARCH=x86_64 ./appimagetool-x86_64.AppImage \\")
    logger.info(f"      {appdir} \\")
    logger.info(f"      {DIST_DIR}/{APP_NAME}-{APP_VERSION}-x86_64.AppImage")
    logger.info("  ─────────────────────────────────────────────────────────────")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Step 9 – README_LINUX.txt  (per-distro install instructions)
# ─────────────────────────────────────────────────────────────────────────────

def step_create_readme(distro: dict) -> bool:
    section("Step 9 – Writing README_LINUX.txt")

    pkg     = distro.get("pkg_manager", "unknown")
    dist_id = distro.get("id", "linux")

    # Distro-specific missing-library install command
    if pkg == "apt":
        dep_cmd = (
            "sudo apt-get install -y \\\n"
            "    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \\\n"
            "    libxcb-randr0 libxcb-render-util0 libxcb-xinerama0 libxcb-xkb1 \\\n"
            "    libxkbcommon-x11-0 libegl1 libgles2 libgl1 libdbus-1-3"
        )
    elif pkg == "dnf":
        dep_cmd = (
            "sudo dnf install -y \\\n"
            "    xcb-util-cursor xcb-util-icccm xcb-util-image xcb-util-keysyms \\\n"
            "    xcb-util-renderutil libxkbcommon-x11 mesa-libGL mesa-libEGL dbus-libs"
        )
    elif pkg == "pacman":
        dep_cmd = (
            "sudo pacman -S --needed \\\n"
            "    xcb-util-cursor xcb-util-icccm xcb-util-image xcb-util-keysyms \\\n"
            "    xcb-util-renderutil libxkbcommon-x11 mesa libdbus"
        )
    elif pkg == "zypper":
        dep_cmd = (
            "sudo zypper install -y \\\n"
            "    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \\\n"
            "    libxcb-renderutil0 libxkbcommon-x11-0 libdbus-1-3"
        )
    elif pkg == "xbps":
        dep_cmd = "sudo xbps-install -y xcb-util-cursor xcb-util-image mesa libxkbcommon"
    elif pkg == "apk":
        dep_cmd = "sudo apk add xcb-util-cursor mesa-gl libxkbcommon"
    else:
        dep_cmd = (
            "# Install xcb-util, xkbcommon, mesa/egl using your distro's package manager.\n"
            "# Look for packages named: xcb-util-cursor, libxkbcommon-x11, mesa"
        )

    readme = textwrap.dedent(f"""\
        ╔══════════════════════════════════════════════════════════════════════╗
        ║  {APP_NAME} {APP_VERSION} – Linux Installation Guide                          ║
        ╚══════════════════════════════════════════════════════════════════════╝

          Built on  : {dist_id} ({platform.machine()})
          Date      : {datetime.now().strftime('%Y-%m-%d')}
          Author    : {AUTHOR}

        ──────────────────────────────────────────────────────────────────────
        OPTION 1 – SYSTEM-WIDE INSTALL  (recommended)
        ──────────────────────────────────────────────────────────────────────

          Installs to /opt/Cirqen.  Adds 'cirqen' to PATH.  Registers
          the app in your desktop application menu.

          1.  Open a terminal in this folder
          2.  Run:
                sudo ./install.sh

          3.  Launch:
                • Application menu → {APP_NAME}
                • Terminal         → cirqen

          4.  To uninstall:
                sudo ./uninstall.sh

        ──────────────────────────────────────────────────────────────────────
        OPTION 2 – RUN WITHOUT INSTALLING  (portable / no root needed)
        ──────────────────────────────────────────────────────────────────────

          Run directly from this folder:
            ./start_cirqen.sh

          Add to personal app menu (no root required):
            mkdir -p ~/.local/share/applications
            cp {APP_NAME}.desktop ~/.local/share/applications/
            update-desktop-database ~/.local/share/applications/

        ──────────────────────────────────────────────────────────────────────
        SYSTEM DEPENDENCIES
        ──────────────────────────────────────────────────────────────────────

          Most libraries are bundled inside the app.
          If the app fails to start with missing xcb or GL library errors,
          install the following:

            {dep_cmd}

        ──────────────────────────────────────────────────────────────────────
        DISTRO-SPECIFIC NOTES
        ──────────────────────────────────────────────────────────────────────

          Ubuntu / Debian / Mint / Pop!_OS
            Works out of the box on Ubuntu 20.04 LTS and later.
            Headless / no display:
              sudo apt-get install xvfb
              xvfb-run ./start_cirqen.sh

          Fedora / RHEL / Rocky / AlmaLinux
            SELinux may block the executable on first run. Fix with:
              sudo chcon -R -t bin_t /opt/{APP_NAME}/{APP_NAME}
            Or temporarily:
              sudo setenforce 0   (remember to re-enable: sudo setenforce 1)

          Arch Linux / Manjaro / EndeavourOS
            All dependencies are in the standard repos; no AUR needed.
            If the taskbar icon is missing after install:
              gtk-update-icon-cache -f -t /usr/share/icons/hicolor

          openSUSE Leap / Tumbleweed
            AppArmor profiles may need updating. Add {APP_NAME} to allowed binaries
            or disable AppArmor for the executable temporarily.

          Tails OS
            Run as the regular user (amnesia), NOT root.
            Data is lost on reboot unless Persistent Storage is enabled.
            Enable persistence for:  ~/.local/share/cirqen

          Kali Linux
            Works the same as Debian. Recommended: run as a non-root user.

          Raspberry Pi / ARM64
            This build is x86_64 only.
            For ARM, rebuild on the device:
              python3 build_linux_desktop.py

          Pure Wayland  (no XWayland)
            If you run a pure Wayland session without XWayland, force it:
              QT_QPA_PLATFORM=wayland ./start_cirqen.sh

        ──────────────────────────────────────────────────────────────────────
        DATA STORAGE  (~/.local/share/cirqen/)
        ──────────────────────────────────────────────────────────────────────

          postgres/          PostgreSQL database files
          redis/             Redis cache files
          logs/              Application log files
          media/             User-uploaded files
          sync_state/        Sync agent tracking files
          session.json       Port allocation for the current session

        ──────────────────────────────────────────────────────────────────────
        FIRST-RUN LOGIN
        ──────────────────────────────────────────────────────────────────────

          Username : maina.wairegi
          Password : ChangeMe123!
          ⚠️   Change the password immediately after first login!

        ──────────────────────────────────────────────────────────────────────
        LOG FILES  (~/.local/share/cirqen/logs/)
        ──────────────────────────────────────────────────────────────────────

          cirqen_app.log      Main application log
          postgres.log        PostgreSQL (embedded)
          redis.log           Redis (embedded)
          django.log          Django web server
          celery.log          Celery background worker
          celery_beat.log     Celery Beat scheduler
          sync_agent.log      HQ sync agent
          update_manager.log  Auto-update system

        ──────────────────────────────────────────────────────────────────────
        TROUBLESHOOTING
        ──────────────────────────────────────────────────────────────────────

          App won't close cleanly:
            python3 cleanup_cirqen.py

          PostgreSQL port conflict  (port 2215 already in use):
            ss -tlnp | grep 2215
            # Kill the conflicting process, or Cirqen will pick an alternative port

          Redis port conflict  (port 7788 already in use):
            ss -tlnp | grep 7788

          Black / blank window on Wayland:
            QT_QPA_PLATFORM=xcb ./start_cirqen.sh

          "FATAL: could not determine locale":
            export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8
            ./start_cirqen.sh

          Permission denied on the executable:
            chmod +x {APP_NAME}
            chmod +x start_cirqen.sh

          Icon missing from app menu after install:
            sudo gtk-update-icon-cache -f -t /usr/share/icons/hicolor
            update-desktop-database /usr/share/applications/

        ──────────────────────────────────────────────────────────────────────
        SUPPORT
        ──────────────────────────────────────────────────────────────────────

          Email : support@b12technologies.com
          © {datetime.now().year} {AUTHOR} – All Rights Reserved
    """)

    readme_path = DIST_APP / "README_LINUX.txt"
    readme_path.write_text(readme)
    ok(f"Created: {readme_path.name}")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Step 10 – Package into a distributable .tar.gz
# ─────────────────────────────────────────────────────────────────────────────

def step_package_distributable() -> bool:
    section("Step 10 – Packaging final distributable  (.tar.gz)")

    archive_stem = f"{APP_NAME}_linux_{APP_VERSION}"
    archive_path = DIST_DIR / f"{archive_stem}.tar.gz"

    logger.info(f"  Packaging dist/Cirqen → {archive_path.name} …")

    shutil.make_archive(
        base_name=str(DIST_DIR / archive_stem),
        format="gztar",
        root_dir=str(DIST_DIR),
        base_dir=APP_NAME,
    )

    size_mb = archive_path.stat().st_size / (1024 * 1024)

    with tarfile.open(archive_path) as tf:
        entry_count = len(tf.getmembers())

    ok(f"Created: {archive_path.name}")
    ok(f"Size   : {size_mb:.1f} MB  |  {entry_count:,} entries")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Final summary
# ─────────────────────────────────────────────────────────────────────────────

def print_final_summary(icon_path: Path):
    archive = DIST_DIR / f"{APP_NAME}_linux_{APP_VERSION}.tar.gz"
    appdir  = DIST_DIR / f"{APP_NAME}.AppDir"

    section("Build complete 🎉")

    # Calculate total build size
    files    = list(DIST_APP.rglob("*"))
    total_mb = sum(f.stat().st_size for f in files if f.is_file()) / (1024 * 1024)

    logger.info(f"""
  ┌────────────────────────────────────────────────────────────────────┐
  │  OUTPUT SUMMARY                                                    │
  ├────────────────────────────────────────────────────────────────────┤
  │  App folder        dist/Cirqen/                                    │
  │  Executable        dist/Cirqen/{APP_NAME:<37}  │
  │  Launcher          dist/Cirqen/start_cirqen.sh                     │
  │  Desktop (system)  dist/Cirqen/{APP_ID}.desktop         │
  │  Desktop (local)   dist/Cirqen/{APP_NAME}.desktop                  │
  │  Installer         dist/Cirqen/install.sh                          │
  │  Uninstaller       dist/Cirqen/uninstall.sh                        │
  │  Icon (primary)    resources/icon.png                              │
  │  Icon (hicolor)    resources/hicolor/<size>/apps/{APP_ID}.png      │
  │  Readme            dist/Cirqen/README_LINUX.txt                    │
  │  AppDir            dist/{APP_NAME}.AppDir/                         │
  │  Archive           dist/{APP_NAME}_linux_{APP_VERSION}.tar.gz      │
  │                                                                    │
  │  Total build size  {total_mb:.1f} MB                              │
  │  Build log         {str(LOG_FILE.relative_to(PROJECT_ROOT)):<52}  │
  └────────────────────────────────────────────────────────────────────┘

  ── QUICK TEST (no install) ──────────────────────────────────────────
    cd {DIST_APP}
    ./start_cirqen.sh

  ── SYSTEM-WIDE INSTALL ──────────────────────────────────────────────
    cd {DIST_APP}
    sudo ./install.sh
    # → App appears in menu  AND  'cirqen' works in terminal

  ── DISTRIBUTE TO OTHER MACHINES ────────────────────────────────────
    Share:  {archive.name}
    Recipient runs:
      tar -xzf {APP_NAME}_linux_{APP_VERSION}.tar.gz
      cd Cirqen
      sudo ./install.sh     # or just:  ./start_cirqen.sh

  ── BUILD AN AppImage (optional, needs appimagetool) ─────────────────
    wget https://github.com/AppImage/AppImageKit/releases/latest/download/appimagetool-x86_64.AppImage
    chmod +x appimagetool-x86_64.AppImage
    ARCH=x86_64 ./appimagetool-x86_64.AppImage \\
        {appdir} \\
        {DIST_DIR}/{APP_NAME}-{APP_VERSION}-x86_64.AppImage
""")


# ─────────────────────────────────────────────────────────────────────────────
# Main orchestrator
# ─────────────────────────────────────────────────────────────────────────────

def main() -> int:
    logger.info("=" * 70)
    logger.info(f"  {APP_NAME} Linux Desktop Build System  –  v{APP_VERSION}")
    logger.info(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"  Log     : {LOG_FILE}")
    logger.info("=" * 70)

    # ── Step 1: Environment & distro detection ────────────────────────────────
    if not step_check_environment():
        return 1
    distro = getattr(step_check_environment, "_distro",
                     {"pkg_manager": "unknown", "id": "linux"})

    # ── Step 2: PyInstaller build ─────────────────────────────────────────────
    if not step_run_pyinstaller_build():
        return 1

    # ── Step 3: Icon (must succeed before desktop entry) ─────────────────────
    icon_path = step_ensure_icon()

    # ── Steps 4-10: Linux packaging (all steps run in order) ─────────────────
    packaging_steps = [
        ("Create launcher  (start_cirqen.sh)",   step_create_launcher),
        ("Create .desktop entry files",          lambda: step_create_desktop_entry(icon_path)),
        ("Create install.sh",                    step_create_install_script),
        ("Create uninstall.sh",                  step_create_uninstall_script),
        ("Create AppDir",                        step_create_appdir),
        ("Create README_LINUX.txt",              lambda: step_create_readme(distro)),
        ("Package distributable (.tar.gz)",      step_package_distributable),
    ]

    for name, fn in packaging_steps:
        logger.info(f"\n▶  {name}")
        try:
            if not fn():
                fail(f"Step failed: {name}")
                fail("Fix the error above and re-run the build.")
                return 1
        except Exception as exc:
            fail(f"Step '{name}' raised an exception: {exc}")
            logger.debug("Full traceback:", exc_info=True)
            return 1

    print_final_summary(icon_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
