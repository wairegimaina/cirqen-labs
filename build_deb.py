#!/usr/bin/env python3
"""
=====================================================================
  CIRQEN DESKTOP - Debian Package (.deb) Builder
  Wraps the PyInstaller dist into a proper dpkg-installable .deb

USAGE:
    # AppImage already built:
    python build_deb.py

    # Full build from scratch (runs bulid_backup.py first):
    python build_deb.py --full-build

    # Custom dist path or version:
    python build_deb.py --dist-path /path/to/dist/Cirqen --version 1.0.1

OUTPUT:
    cirqen_1.0.0_amd64.deb  (in project root)

INSTALL THE .deb:
    sudo dpkg -i cirqen_1.0.0_amd64.deb
    sudo apt-get install -f        # fix any missing deps

UNINSTALL:
    sudo dpkg -r cirqen

REQUIREMENTS:
    - Linux x86_64
    - dpkg-deb  (comes with dpkg, pre-installed on Ubuntu/Debian)
    - Python 3.10+
=====================================================================
"""

import os
import sys
import stat
import shutil
import logging
import argparse
import platform
import subprocess
from pathlib import Path
from datetime import datetime
from textwrap import dedent

# ──────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────
LOG_DIR = Path(__file__).parent / "build_logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"deb_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("CirqenDebBuilder")

# ──────────────────────────────────────────────────────────────────
# Config — edit these when you bump a release
# ──────────────────────────────────────────────────────────────────
APP_NAME      = "cirqen"            # must be lowercase for dpkg
APP_PRETTY    = "Cirqen"            # display name
APP_VERSION   = "1.0.0"
APP_ARCH      = "amd64"
MAINTAINER    = "Cirqen Technologies <support@cirqen.com>"
DESCRIPTION   = "Cirqen Desktop - Business Management System"
LONG_DESC     = (
    "Cirqen Desktop is a full-featured business management platform\n"
    " for hospitals, schools, and enterprises across East Africa.\n"
    " Built Smarter. Scale Faster."
)
HOMEPAGE      = "https://cirqen.com"
# Runtime deps that must exist on the target machine.
# Qt/WebEngine needs these; add more if your app needs them.
DEPENDS = ", ".join([
    "libc6 (>= 2.17)",
    "libstdc++6",
    "libglib2.0-0",
    "libgl1",
    "libx11-6",
    "libxcb1",
    "libxext6",
    "libxi6",
    "libxrender1",
    "libxrandr2",
    "libxfixes3",
    "libxcursor1",
    "libxinerama1",
    "libasound2",
    "libdbus-1-3",
    "libfontconfig1",
    "libfreetype6",
    "libpulse0",
    "libnss3",
    "libnspr4",
    "libatk1.0-0",
    "libatk-bridge2.0-0",
    "libcups2",
    "libdrm2",
    "libxkbcommon0",
    "libxcomposite1",
    "libxdamage1",
])

# ──────────────────────────────────────────────────────────────────
# Paths
# ──────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.resolve()
DIST_DIR     = PROJECT_ROOT / "dist"
DIST_APP_DIR = DIST_DIR / "Cirqen"
BUILD_DIR    = PROJECT_ROOT / "build"
PKG_ROOT     = BUILD_DIR / f"{APP_NAME}_{APP_VERSION}_{APP_ARCH}"  # staging

# Standard Linux install locations inside the package
PKG_OPT      = PKG_ROOT / "opt" / APP_NAME          # binary lives here
PKG_APPS     = PKG_ROOT / "usr/share/applications"  # .desktop
PKG_ICONS_HI = PKG_ROOT / "usr/share/icons/hicolor/256x256/apps"
PKG_ICONS_SC = PKG_ROOT / "usr/share/icons/hicolor/scalable/apps"
PKG_DOC      = PKG_ROOT / f"usr/share/doc/{APP_NAME}"
PKG_DEBIAN   = PKG_ROOT / "DEBIAN"                  # control files


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────

def banner(text):
    logger.info("\n" + "=" * 70)
    logger.info(f"  {text}")
    logger.info("=" * 70)


def run(cmd, cwd=None, timeout=3600):
    logger.info("CMD: " + " ".join(str(c) for c in cmd))
    subprocess.run(
        [str(c) for c in cmd],
        cwd=str(cwd) if cwd else None,
        timeout=timeout,
        check=True,
    )


def make_exec(path: Path):
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def write(path: Path, content: str, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dedent(content).lstrip())
    path.chmod(mode)


# ──────────────────────────────────────────────────────────────────
# Step 0 — Platform & tool check
# ──────────────────────────────────────────────────────────────────

def check_platform():
    banner("Platform & Tool Check")
    if platform.system() != "Linux":
        logger.error("❌ .deb packages can only be built on Linux.")
        return False
    logger.info(f"✅ Linux: {platform.release()}")

    if not shutil.which("dpkg-deb"):
        logger.error("❌ dpkg-deb not found. Install with:  sudo apt install dpkg")
        return False
    logger.info("✅ dpkg-deb found")
    return True


# ──────────────────────────────────────────────────────────────────
# Step 1 — Optional PyInstaller build
# ──────────────────────────────────────────────────────────────────

def run_pyinstaller():
    banner("PyInstaller Build (bulid_backup.py)")
    script = PROJECT_ROOT / "bulid_backup.py"
    if not script.exists():
        logger.error(f"❌ {script} not found")
        return False
    try:
        run([sys.executable, str(script)], cwd=PROJECT_ROOT)
        logger.info("✅ PyInstaller build done")
        return True
    except subprocess.CalledProcessError:
        logger.error("❌ PyInstaller build failed")
        return False


# ──────────────────────────────────────────────────────────────────
# Step 1b — Docker-based PyInstaller build  (--docker-build)
# ──────────────────────────────────────────────────────────────────

def run_docker_build():
    banner("Docker PyInstaller Build")

    if not shutil.which("docker"):
        logger.error("❌ docker not found — install Docker and ensure the daemon is running")
        return False

    image = "python:3.11-slim"
    container_name = f"cirqen_build_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    cmd = [
        "docker", "run", "--rm",
        "--name", container_name,
        "-v", f"{PROJECT_ROOT}:/app",
        "-w", "/app",
        image,
        "bash", "-c",
        (
            "pip install --quiet pyinstaller && "
            f"python bulid_backup.py"
        ),
    ]

    logger.info(f"Docker image : {image}")
    logger.info(f"Mount        : {PROJECT_ROOT} → /app")
    try:
        run(cmd, cwd=PROJECT_ROOT)
        logger.info("✅ Docker PyInstaller build done")
        return True
    except subprocess.CalledProcessError:
        logger.error("❌ Docker build failed — check the log above")
        return False


# ──────────────────────────────────────────────────────────────────
# Step 2 — Verify dist
# ──────────────────────────────────────────────────────────────────

def verify_dist(dist_path: Path):
    banner(f"Verify dist: {dist_path}")
    if not dist_path.exists():
        logger.error(f"❌ Not found: {dist_path}")
        logger.info("   Run with --full-build or check --dist-path")
        return False
    exe = dist_path / "Cirqen"
    if not exe.exists():
        # try lowercase
        exe = dist_path / "cirqen"
    if not exe.exists():
        logger.error("❌ Main executable not found in dist folder")
        return False
    logger.info(f"✅ Executable: {exe}")
    return True


# ──────────────────────────────────────────────────────────────────
# Step 3 — Build package directory tree
# ──────────────────────────────────────────────────────────────────

DESKTOP_ENTRY = f"""\
[Desktop Entry]
Type=Application
Name={APP_PRETTY}
GenericName=Business Management System
Comment=Build Smarter. Scale Faster.
Exec=/opt/{APP_NAME}/Cirqen --no-sandbox
Icon={APP_NAME}
Categories=Office;ProjectManagement;Finance;
Terminal=false
StartupNotify=true
StartupWMClass=Cirqen
Keywords=cirqen;hospital;equipment;management;erp;
"""

FALLBACK_SVG = """\
<?xml version="1.0" encoding="UTF-8"?>
<svg width="256" height="256" viewBox="0 0 256 256"
     xmlns="http://www.w3.org/2000/svg">
  <rect width="256" height="256" rx="40" fill="#1A7F5A"/>
  <text x="50%" y="55%" dominant-baseline="middle" text-anchor="middle"
        font-family="sans-serif" font-size="160" font-weight="bold"
        fill="white">C</text>
</svg>
"""

# postinst — runs after dpkg unpacks the files
# NOTE: use a raw f-string (rf"...") so that \; inside the bash find
# command is passed through literally without triggering Python's
# DeprecationWarning about invalid escape sequences in f-strings.
POSTINST = rf"""#!/bin/bash
set -e

# Make the main binary executable
chmod +x /opt/{APP_NAME}/Cirqen 2>/dev/null || true

# Symlink for terminal use
ln -sf /opt/{APP_NAME}/Cirqen /usr/local/bin/{APP_NAME} 2>/dev/null || true

# Create media / upload directory and ensure correct ownership
MEDIA_DIR="/var/lib/{APP_NAME}/media"
mkdir -p "$MEDIA_DIR"
# Recursively set ownership to root (no setuid risk) and make writable
find "$MEDIA_DIR" -maxdepth 0 -type d -exec chmod 0755 {{}} \;

# Refresh icon & desktop caches
gtk-update-icon-cache /usr/share/icons/hicolor/ 2>/dev/null || true
update-desktop-database /usr/share/applications/        2>/dev/null || true

echo "✅ Cirqen installed. Launch from Applications menu or run: {APP_NAME}"
exit 0
"""

# prerm — runs before dpkg removes the package
PRERM = f"""\
#!/bin/bash
set -e

# Kill any running instance
pkill -f "Cirqen" 2>/dev/null || true
sleep 1

# Remove symlink
rm -f /usr/local/bin/{APP_NAME}

exit 0
"""

# postrm — runs after dpkg removes files
POSTRM = f"""\
#!/bin/bash
set -e

# Refresh caches after removal
gtk-update-icon-cache /usr/share/icons/hicolor/ 2>/dev/null || true
update-desktop-database /usr/share/applications/        2>/dev/null || true

exit 0
"""


def build_package_tree(dist_path: Path):
    banner("Build Package Directory Tree")

    # Clean slate — PKG_ROOT may be root-owned from a previous
    # dpkg-deb --root-owner-group run, so shutil.rmtree can fail with
    # PermissionError.  Fall back to `sudo rm -rf` in that case.
    if PKG_ROOT.exists():
        try:
            shutil.rmtree(PKG_ROOT)
        except PermissionError:
            logger.warning(
                f"\u26a0\ufe0f  {PKG_ROOT} is root-owned (leftover from dpkg-deb). "
                "Re-running removal with sudo \u2026"
            )
            try:
                subprocess.run(
                    ["sudo", "rm", "-rf", str(PKG_ROOT)],
                    check=True, timeout=60,
                )
            except subprocess.CalledProcessError:
                logger.error(
                    f"\u274c Could not remove {PKG_ROOT}. "
                    "Run:  sudo rm -rf build/  and retry."
                )
                return False

    # ── 3a. Copy PyInstaller output → /opt/cirqen/ ──────────────────
    logger.info(f"Copying dist → {PKG_OPT} …")
    shutil.copytree(dist_path, PKG_OPT, symlinks=True)

    # Make sure the main executable is +x
    main_exe = PKG_OPT / "Cirqen"
    if main_exe.exists():
        make_exec(main_exe)
    logger.info("✅ App files copied")

    # ── 3b. .desktop entry ──────────────────────────────────────────
    write(PKG_APPS / f"{APP_NAME}.desktop", DESKTOP_ENTRY)
    logger.info("✅ .desktop entry written")

    # ── 3c. Icons — map pre-generated sizes from static/images/ ────────
    #
    # Expected files in PROJECT_ROOT/static/images/:
    #   logo-16x16.png, logo-32x32.png, logo-48x48.png, logo-57x57.png,
    #   logo-60x60.png, logo-72x72.png, logo-96x96.png, logo-114x114.png,
    #   logo-120x120.png, logo-128x128.png, logo-144x144.png, logo-152x152.png,
    #   logo-180x180.png, logo-192x192.png, logo-256x256.png, logo-310x310.png,
    #   logo-512x512.png, logo-1024x1024.png
    #
    # hicolor standard sizes we care about and their source filename:
    ICON_MAP = {
        16:   "logo-16x16.png",
        32:   "logo-32x32.png",
        48:   "logo-48x48.png",
        64:   "logo-64x64.png",       # fallback to 72 if missing
        72:   "logo-72x72.png",
        96:   "logo-96x96.png",
        114:  "logo-114x114.png",
        120:  "logo-120x120.png",
        128:  "logo-128x128.png",
        144:  "logo-144x144.png",
        152:  "logo-152x152.png",
        180:  "logo-180x180.png",
        192:  "logo-192x192.png",
        256:  "logo-256x256.png",
        310:  "logo-310x310.png",
        512:  "logo-512x512.png",
    }

    # hicolor standard sizes that desktops actually use
    HICOLOR_SIZES = [16, 24, 32, 48, 64, 96, 128, 256, 512]

    STATIC_IMAGES = PROJECT_ROOT / "static" / "images"

    def find_icon(size: int) -> Path:
        """Find best matching pre-generated icon for a given size."""
        # Exact match first
        exact = ICON_MAP.get(size)
        if exact:
            p = STATIC_IMAGES / exact
            if p.exists():
                return p
        # Nearest larger size as fallback
        for s in sorted(ICON_MAP.keys()):
            if s >= size:
                p = STATIC_IMAGES / ICON_MAP[s]
                if p.exists():
                    return p
        return None

    installed_count = 0
    for size in HICOLOR_SIZES:
        src = find_icon(size)
        if src:
            icon_dir = PKG_ROOT / f"usr/share/icons/hicolor/{size}x{size}/apps"
            icon_dir.mkdir(parents=True, exist_ok=True)
            dest = icon_dir / f"{APP_NAME}.png"
            if src.stat().st_size > 0 and size not in [s for s in ICON_MAP if ICON_MAP[s] == src.name]:
                # Size doesn't have exact match — resize with Pillow
                try:
                    from PIL import Image
                    Image.open(src).convert("RGBA").resize(
                        (size, size), Image.LANCZOS
                    ).save(dest)
                except ImportError:
                    shutil.copy2(src, dest)
            else:
                shutil.copy2(src, dest)
            installed_count += 1
            logger.info(f"  ✅ {size}x{size} ← {src.name}")
        else:
            logger.warning(f"  ⚠️  No icon found for {size}x{size} — skipping")

    # Scalable slot — use the largest available
    scalable = PKG_ROOT / "usr/share/icons/hicolor/scalable/apps"
    scalable.mkdir(parents=True, exist_ok=True)
    # Prefer SVG if present, else 1024 PNG, else 512 PNG
    for svg_name in ["white.svg", "dark.svg"]:
        svg_src = STATIC_IMAGES / svg_name
        if svg_src.exists():
            shutil.copy2(svg_src, scalable / f"{APP_NAME}.svg")
            logger.info(f"  ✅ scalable ← {svg_name}")
            break
    else:
        for fallback in ["logo-1024x1024.png", "logo-512x512.png"]:
            p = STATIC_IMAGES / fallback
            if p.exists():
                shutil.copy2(p, scalable / f"{APP_NAME}.png")
                logger.info(f"  ✅ scalable ← {fallback}")
                break
        else:
            (PKG_ICONS_SC.parent / "scalable" / "apps").mkdir(parents=True, exist_ok=True)
            (scalable / f"{APP_NAME}.svg").write_text(FALLBACK_SVG)
            logger.warning("  ⚠️  No scalable icon found — using SVG placeholder")

    if installed_count == 0:
        logger.error("❌ No icons installed — check static/images/ path and filenames")
        return False
    logger.info(f"✅ {installed_count} icon sizes installed from static/images/")

    # ── 3d. Docs / changelog ────────────────────────────────────────
    PKG_DOC.mkdir(parents=True, exist_ok=True)
    changelog = f"""\
{APP_NAME} ({APP_VERSION}) stable; urgency=low

  * Initial release.

 -- {MAINTAINER}  {datetime.now().strftime('%a, %d %b %Y %H:%M:%S +0300')}
"""
    (PKG_DOC / "changelog.Debian").write_text(changelog)
    run(["gzip", "--best", "--force", str(PKG_DOC / "changelog.Debian")])

    copyright_txt = f"""\
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: {APP_PRETTY}
Upstream-Contact: {MAINTAINER}
Source: {HOMEPAGE}

Files: *
Copyright: {datetime.now().year} Cirqen Technologies
License: Proprietary
 All rights reserved. Unauthorised copying, redistribution or modification
 of this software is strictly prohibited.
"""
    (PKG_DOC / "copyright").write_text(copyright_txt)
    logger.info("✅ Docs written")

    # ── 3e. DEBIAN control files ────────────────────────────────────
    PKG_DEBIAN.mkdir(parents=True, exist_ok=True)

    # Calculate installed size (in KB) for the control file
    total_bytes = sum(
        f.stat().st_size for f in PKG_ROOT.rglob("*") if f.is_file()
    )
    installed_size_kb = max(1, total_bytes // 1024)

    # IMPORTANT: control file must have NO leading spaces on any line
    control_lines = [
        f"Package: {APP_NAME}",
        f"Version: {APP_VERSION}",
        f"Architecture: {APP_ARCH}",
        f"Maintainer: {MAINTAINER}",
        f"Installed-Size: {installed_size_kb}",
        f"Depends: {DEPENDS}",
        f"Section: misc",
        f"Priority: optional",
        f"Homepage: {HOMEPAGE}",
        f"Description: {DESCRIPTION}",
    ]
    # Long description: first line of each continuation must start with " " (one space)
    for line in LONG_DESC.splitlines():
        control_lines.append(f" {line}")
    control_lines.append("")  # trailing newline required
    control = "\n".join(control_lines)
    (PKG_DEBIAN / "control").write_text(control)
    (PKG_DEBIAN / "control").chmod(0o644)

    write(PKG_DEBIAN / "postinst", POSTINST, mode=0o755)
    write(PKG_DEBIAN / "prerm",    PRERM,    mode=0o755)
    write(PKG_DEBIAN / "postrm",   POSTRM,   mode=0o755)

    logger.info("✅ DEBIAN control files written")

    # ── 3f. Static assets & project images → /opt/cirqen/static/ ───
    #
    # Copies the entire static/ tree (CSS, JS, fonts, images, etc.) so
    # the installed app can serve or reference its bundled assets at
    # /opt/cirqen/static/.  Project-level images (e.g. sample logos,
    # report headers) stored under project_images/ are placed alongside.
    #
    STATIC_SRC = PROJECT_ROOT / "static"
    if STATIC_SRC.exists():
        static_dest = PKG_OPT / "static"
        shutil.copytree(STATIC_SRC, static_dest, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        n_static = sum(1 for _ in static_dest.rglob("*") if _.is_file())
        logger.info(f"✅ static/ copied → {static_dest}  ({n_static} files)")
    else:
        logger.warning("⚠️  static/ not found — skipping static asset copy")

    PROJECT_IMAGES_SRC = PROJECT_ROOT / "project_images"
    if PROJECT_IMAGES_SRC.exists():
        images_dest = PKG_OPT / "project_images"
        shutil.copytree(PROJECT_IMAGES_SRC, images_dest, symlinks=True)
        n_imgs = sum(1 for _ in images_dest.rglob("*") if _.is_file())
        logger.info(f"✅ project_images/ copied → {images_dest}  ({n_imgs} files)")
    else:
        logger.info("ℹ️  project_images/ not found — nothing to copy")

    return True


# ──────────────────────────────────────────────────────────────────
# Step 4 — Fix permissions (dpkg-deb is strict)
# ──────────────────────────────────────────────────────────────────

def fix_permissions():
    banner("Fix Permissions")

    # All dirs 755, all files 644 by default
    for path in PKG_ROOT.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            # Keep exec bit on scripts and the app binary
            if path.suffix in (".sh", ".py", "") and path.stat().st_mode & 0o100:
                path.chmod(0o755)
            else:
                path.chmod(0o644)

    # DEBIAN scripts must be 755
    for script in ["postinst", "prerm", "postrm", "preinst"]:
        s = PKG_DEBIAN / script
        if s.exists():
            s.chmod(0o755)

    # Main executable must be 755
    exe = PKG_OPT / "Cirqen"
    if exe.exists():
        exe.chmod(0o755)

    logger.info("✅ Permissions fixed")
    return True


# ──────────────────────────────────────────────────────────────────
# Step 5 — Run dpkg-deb
# ──────────────────────────────────────────────────────────────────

def run_dpkg_deb():
    banner("Run dpkg-deb")

    deb_name  = f"{APP_NAME}_{APP_VERSION}_{APP_ARCH}.deb"
    deb_path  = PROJECT_ROOT / deb_name

    if deb_path.exists():
        deb_path.unlink()

    try:
        run([
            "dpkg-deb",
            "--build",
            "--root-owner-group",   # avoids needing root to build
            str(PKG_ROOT),
            str(deb_path),
        ])
    except subprocess.CalledProcessError:
        logger.error("❌ dpkg-deb failed")
        return False, None

    if not deb_path.exists():
        logger.error(f"❌ Expected output not found: {deb_path}")
        return False, None

    size_mb = deb_path.stat().st_size / (1024 * 1024)
    logger.info(f"✅ .deb built: {deb_path}  ({size_mb:.1f} MB)")
    return True, deb_path


# ──────────────────────────────────────────────────────────────────
# Step 6 — Verify with dpkg-deb --info
# ──────────────────────────────────────────────────────────────────

def verify_deb(deb_path: Path):
    banner("Verify .deb Package")
    try:
        result = subprocess.run(
            ["dpkg-deb", "--info", str(deb_path)],
            capture_output=True, text=True, check=True
        )
        logger.info(result.stdout)

        result2 = subprocess.run(
            ["dpkg-deb", "--contents", str(deb_path)],
            capture_output=True, text=True, check=True
        )
        # Just show first 30 lines so log isn't huge
        lines = result2.stdout.splitlines()
        logger.info("\n".join(lines[:30]))
        if len(lines) > 30:
            logger.info(f"  ... and {len(lines)-30} more files")

        logger.info("✅ Package verified")
        return True
    except subprocess.CalledProcessError as e:
        logger.warning(f"⚠️  Verification warning: {e}")
        return True   # non-fatal


# ──────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────

def main():
    global APP_VERSION, PKG_ROOT, PKG_OPT, PKG_APPS
    global PKG_ICONS_HI, PKG_ICONS_SC, PKG_DOC, PKG_DEBIAN

    parser = argparse.ArgumentParser(
        description="Build Cirqen Desktop .deb package"
    )
    parser.add_argument(
        "--full-build", action="store_true",
        help="Run bulid_backup.py (PyInstaller) before packaging"
    )
    parser.add_argument(
        "--docker-build", action="store_true",
        help="Run the PyInstaller build inside Docker for a clean, reproducible environment"
    )
    parser.add_argument(
        "--dist-path", default=str(DIST_APP_DIR),
        help=f"Path to PyInstaller dist folder (default: {DIST_APP_DIR})"
    )
    parser.add_argument(
        "--version", default=APP_VERSION,
        help=f"Package version (default: {APP_VERSION})"
    )
    args = parser.parse_args()

    APP_VERSION  = args.version
    dist_path    = Path(args.dist_path)

    # Recompute paths if version changed
    PKG_ROOT     = BUILD_DIR / f"{APP_NAME}_{APP_VERSION}_{APP_ARCH}"
    PKG_OPT      = PKG_ROOT / "opt" / APP_NAME
    PKG_APPS     = PKG_ROOT / "usr/share/applications"
    PKG_ICONS_HI = PKG_ROOT / "usr/share/icons/hicolor/256x256/apps"
    PKG_ICONS_SC = PKG_ROOT / "usr/share/icons/hicolor/scalable/apps"
    PKG_DOC      = PKG_ROOT / f"usr/share/doc/{APP_NAME}"
    PKG_DEBIAN   = PKG_ROOT / "DEBIAN"

    # Determine active build mode for the header
    if args.docker_build:
        build_mode = "Docker build (reproducible)"
    elif args.full_build:
        build_mode = "Full build (local PyInstaller)"
    else:
        build_mode = "Package only (use existing dist)"

    print("\n" + "=" * 70)
    print("  CIRQEN DESKTOP — Debian Package Builder")
    print(f"  Version : {APP_VERSION}")
    print(f"  Arch    : {APP_ARCH}")
    print(f"  Mode    : {build_mode}")
    print(f"  Dist    : {dist_path}")
    print(f"  Log     : {LOG_FILE}")
    print("=" * 70 + "\n")

    steps = [
        ("Platform & tool check",   check_platform),
    ]

    if args.docker_build:
        steps.append(("Docker PyInstaller build", run_docker_build))
    elif args.full_build:
        steps.append(("PyInstaller build", run_pyinstaller))

    steps += [
        ("Verify dist",             lambda: verify_dist(dist_path)),
        ("Build package tree",      lambda: build_package_tree(dist_path)),
        ("Fix permissions",         fix_permissions),
        ("Run dpkg-deb",            run_dpkg_deb),
    ]

    deb_path = None

    for name, fn in steps:
        logger.info(f"\n▶  {name} …")
        result = fn()

        if isinstance(result, tuple):
            ok, deb_path = result
        else:
            ok = result

        if not ok:
            print(f"\n❌  BUILD FAILED at: {name}")
            print(f"    See log: {LOG_FILE}")
            return 1

    if deb_path:
        verify_deb(deb_path)

    deb_name = deb_path.name if deb_path else f"{APP_NAME}_{APP_VERSION}_{APP_ARCH}.deb"

    print("\n" + "=" * 70)
    print("  🎉  .deb build SUCCESSFUL!")
    print("=" * 70)
    print(f"""
  Package : {deb_path}
  Size    : {deb_path.stat().st_size / (1024*1024):.1f} MB

  ── INSTALL ──────────────────────────────────────────────────
  sudo dpkg -i {deb_name}
  sudo apt-get install -f          # pull in any missing deps

  ── VERIFY install ───────────────────────────────────────────
  dpkg -l | grep cirqen
  dpkg -L cirqen                   # list installed files

  ── LAUNCH ───────────────────────────────────────────────────
  cirqen                           # terminal
  # or open Applications menu and search "Cirqen"

  ── UNINSTALL ────────────────────────────────────────────────
  sudo dpkg -r cirqen              # remove (keep config)
  sudo dpkg -P cirqen              # purge (remove everything)
""")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
