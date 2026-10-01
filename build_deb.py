#!/usr/bin/env python3
"""Build cirqen_<version>_amd64.deb: Cirqen as a real Ubuntu app.

    python build_deb.py                        # build in Ubuntu 24.04 (Docker), then make the .deb
    python build_deb.py --provisioning FILE    # a hospital's .deb (its installer file from the panel)
    python build_deb.py --test                 # then install and start it in clean 24.04 and 26.04
    python build_deb.py --from-dist            # just package the existing dist/Cirqen

Install on a PC:   sudo apt install ./cirqen_1.6.2_amd64.deb
Remove:            sudo apt remove cirqen      (users' records and settings stay)

Runs on Ubuntu 24.04 and every newer release. A PyInstaller app runs only on
the glibc it was built against or newer, so the app is built inside an Ubuntu
24.04 container (Python 3.14 from uv, PostgreSQL 18 from apt.postgresql.org,
Redis 8 from packages.redis.io); the databases' own libraries go into the
app (build.py bundle_native_libs). --from-dist skips that: the .deb then runs
only on this machine's Ubuntu release and newer.

What it installs:
  /opt/cirqen/                 the app, as installed (root-owned)
  /usr/bin/cirqen              the launcher (the menu entry and the command)
  /usr/share/applications/     menu entry, with the icon
  /etc/cirqen/provisioning.json  only in a hospital's .deb

Each user runs their own copy (~/.local/share/cirqen-app/Cirqen), made from
/opt/cirqen on first start and again whenever apt installs a newer .deb.
That copy belongs to the user, so Cirqen's own updates (code packages and
full-app swaps) work without admin rights. Records and settings stay in
~/.local/share/cirqen, as with the portable build.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DIST_APP = PROJECT_ROOT / "dist" / "Cirqen"
OUT_DIR = PROJECT_ROOT / "dist"
BUILD_IMAGE = "ubuntu:24.04"
TEST_IMAGES = ("ubuntu:24.04", "ubuntu:26.04")
PYINSTALLER = "6.20.0"

PACKAGE = "cirqen"
MAINTAINER = "Cirqen Labs <support@cirqenlabs.com>"
HOMEPAGE = "https://cirqenlabs.com"
SUMMARY = "Calibration and maintenance management for hospitals"
DESCRIPTION = (" Cirqen keeps a hospital's medical equipment, calibrations, planned\n"
               " maintenance, job cards and certificates, and syncs them with the\n"
               " hospital's Cirqen HQ.")
# What the app needs from the system (the rest is inside it). Qt 6 / QtWebEngine
# and the bundled databases; t64 names are Ubuntu 24.04+, the alternatives older.
DEPENDS = [
    "libc6 (>= 2.39)", "libgl1", "libegl1", "libopengl0", "libfontconfig1", "libfreetype6", "libdbus-1-3",
    "libglib2.0-0t64 | libglib2.0-0", "libnss3", "libnspr4", "libxkbcommon0", "libxkbcommon-x11-0",
    "libxcb-cursor0", "libxcb-icccm4", "libxcb-image0", "libxcb-keysyms1", "libxcb-randr0",
    "libxcb-render-util0", "libxcb-shape0", "libxcb-xinerama0", "libxcb-xkb1", "libx11-xcb1",
    "libxcomposite1", "libxdamage1", "libxrandr2", "libxtst6", "libxshmfence1", "libgbm1", "libdrm2",
    "libasound2t64 | libasound2", "libcups2t64 | libcups2", "libxkbfile1", "libsm6", "libice6",
    "libxfixes3", "libxrender1", "libxext6", "libxi6", "libwayland-client0", "libwayland-cursor0",
    "libwayland-egl1", "libwayland-server0", "libpulse0", "libgtk-3-0t64 | libgtk-3-0",
    "tzdata",                      # Africa/Nairobi for EAT dates (zoneinfo reads the system's)
]
# Qt plugins that may stay unloadable: an old TIFF reader newer Ubuntus no longer ship.
OPTIONAL_MISSING = ("libtiff.so.5",)

LAUNCHER = r"""#!/bin/bash
# Cirqen (installed from the .deb). /opt/cirqen is the installed copy; each
# user runs their own copy so Cirqen can update itself without admin rights.
BASE=/opt/cirqen
SHARE="${XDG_DATA_HOME:-$HOME/.local/share}"
APP="$SHARE/cirqen-app/Cirqen"
DATA="$SHARE/cirqen"
version() { cat "$1/_internal/version.txt" 2>/dev/null || cat "$1/version.txt" 2>/dev/null || echo 0; }
mkdir -p "$SHARE/cirqen-app" "$DATA" || exit 1
if [ ! -x "$APP/Cirqen" ] || dpkg --compare-versions "$(version "$BASE")" gt "$(version "$APP")"; then
  rm -rf "$APP.copying"
  if ! cp -a "$BASE" "$APP.copying"; then
    rm -rf "$APP.copying"
    notify-send "Cirqen" "Not enough disk space to start Cirqen." 2>/dev/null
    echo "Cirqen: could not copy $BASE to $APP" >&2; exit 1
  fi
  rm -rf "$APP.previous"
  [ -d "$APP" ] && mv "$APP" "$APP.previous"
  mv "$APP.copying" "$APP"
fi
# A hospital's .deb carries its installer file: give it to this user's first start.
if [ -f /etc/cirqen/provisioning.json ] && [ ! -f "$DATA/config.json" ] && [ ! -f "$DATA/provisioning.json" ]; then
  install -m 600 /etc/cirqen/provisioning.json "$DATA/provisioning.json"
fi
exec "$APP/start_cirqen.sh" "$@"
"""

DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=Cirqen
GenericName=Equipment maintenance
Comment={summary}
Exec=cirqen
Icon=cirqen
Terminal=false
Categories=Office;Database;MedicalSoftware;
Keywords=calibration;maintenance;hospital;biomedical;
StartupWMClass=Cirqen
"""

POSTINST = """#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
  update-desktop-database -q /usr/share/applications 2>/dev/null || true
  gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
fi
exit 0
"""

POSTRM = """#!/bin/sh
set -e
update-desktop-database -q /usr/share/applications 2>/dev/null || true
gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
if [ "$1" = "purge" ]; then rm -rf /etc/cirqen; fi
exit 0
"""


def say(message: str) -> None:
    print(f"▶ {message}", flush=True)


def app_version(app: Path) -> str:
    for path in (app / "_internal" / "version.txt", app / "version.txt"):
        if path.is_file() and path.read_text().strip():
            return path.read_text().strip()
    raise SystemExit(f"❌ {app} has no version.txt; build it with build.py first")


def check_provisioning(path: Path) -> str:
    sys.path.insert(0, str(PROJECT_ROOT))
    import build

    try:
        _, code = build.load_panel_provisioning(path)
    except ValueError as exc:
        raise SystemExit(f"❌ {exc}") from None
    return code


# ── the .deb ────────────────────────────────────────────────────────────────

def make_deb(app: Path, out_dir: Path, provisioning: Path | None = None) -> Path:
    if not shutil.which("dpkg-deb"):
        raise SystemExit("❌ dpkg-deb not found (it comes with dpkg on Ubuntu)")
    if (app / "provisioning.json").exists():
        raise SystemExit(f"❌ {app} holds a provisioning.json; the .deb's app must be neutral")
    version = app_version(app)
    code = check_provisioning(provisioning) if provisioning else ""
    name = f"{PACKAGE}_{version}_amd64" + (f"_{code}" if code else "")
    with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
        root = Path(tmp) / name
        say(f"Laying out {name}")
        shutil.copytree(app, root / "opt" / PACKAGE, symlinks=True)
        (root / "usr" / "bin").mkdir(parents=True)
        launcher = root / "usr" / "bin" / PACKAGE
        launcher.write_text(LAUNCHER)
        launcher.chmod(0o755)
        apps = root / "usr" / "share" / "applications"
        apps.mkdir(parents=True)
        (apps / f"{PACKAGE}.desktop").write_text(DESKTOP_ENTRY.format(summary=SUMMARY))
        icon = next((p for p in (PROJECT_ROOT / "resources" / "icon.png", app / "resources" / "icon.png",
                                 app / "_internal" / "resources" / "icon.png") if p.is_file()), None)
        if icon:
            icons = root / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
            icons.mkdir(parents=True)
            shutil.copy2(icon, icons / f"{PACKAGE}.png")
        if provisioning:
            etc = root / "etc" / PACKAGE
            etc.mkdir(parents=True)
            shutil.copy2(provisioning, etc / "provisioning.json")
            (etc / "provisioning.json").chmod(0o644)
        doc = root / "usr" / "share" / "doc" / PACKAGE
        doc.mkdir(parents=True)
        (doc / "copyright").write_text("Copyright Cirqen Labs. All rights reserved.\n")

        debian = root / "DEBIAN"
        debian.mkdir()
        size_kb = sum(f.stat().st_size for f in root.rglob("*") if f.is_file() and not f.is_symlink()) // 1024
        control = [f"Package: {PACKAGE}", f"Version: {version}", "Architecture: amd64",
                   f"Maintainer: {MAINTAINER}", f"Installed-Size: {size_kb}", "Section: misc",
                   "Priority: optional", f"Homepage: {HOMEPAGE}", f"Depends: {', '.join(DEPENDS)}",
                   f"Description: {SUMMARY}", DESCRIPTION]
        (debian / "control").write_text("\n".join(control) + "\n")
        if provisioning:
            (debian / "conffiles").write_text(f"/etc/{PACKAGE}/provisioning.json\n")
        for script, body in (("postinst", POSTINST), ("postrm", POSTRM)):
            (debian / script).write_text(body)
            (debian / script).chmod(0o755)
        for path in root.rglob("*"):                     # dpkg wants 0755 dirs, no group/world write
            if path.is_dir():
                path.chmod(0o755)
            elif not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o022)
        out = out_dir / f"{name}.deb"
        say(f"dpkg-deb → {out.name}")
        subprocess.run(["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(out)], check=True,
                       stdout=subprocess.DEVNULL)
    if code:
        out.chmod(0o600)
        print("🔒 This .deb holds the hospital's enrollment token: hand it over privately")
    print(f"✅ {out} ({out.stat().st_size / 1e6:.0f} MB)")
    return out


# ── building in Ubuntu 24.04 ────────────────────────────────────────────────

CONTAINER_SCRIPT = r"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates gnupg build-essential patchelf binutils file dpkg-dev git \
  libgl1 libegl1 libopengl0 libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libdbus-1-3 libnss3 \
  libglib2.0-0t64 libxcb-cursor0 libasound2t64 libxcomposite1 libxdamage1 libxrandr2 libxtst6 libgbm1 >/dev/null
install -d /usr/share/postgresql-common/pgdg
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt noble-pgdg main" > /etc/apt/sources.list.d/pgdg.list
curl -fsSL https://packages.redis.io/gpg | gpg --dearmor --yes -o /usr/share/keyrings/redis-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/redis-archive-keyring.gpg] https://packages.redis.io/deb noble main" > /etc/apt/sources.list.d/redis.list
apt-get update -qq
apt-get install -y -qq "postgresql-$PG_MAJOR" redis-server >/dev/null
/usr/lib/postgresql/$PG_MAJOR/bin/postgres --version; redis-server --version | cut -c1-40
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
export PATH="$HOME/.local/bin:$PATH"
uv python install "$PYTHON" >/dev/null
uv venv --seed -q /venv --python "$PYTHON"
mkdir -p /work && tar xf /stage/src.tar -C /work && cd /work
uv pip install -q --python /venv/bin/python -r requirements.txt "pyinstaller==$PYINSTALLER"
CIRQEN_REQUIRE_NATIVE_BUNDLE=1 /venv/bin/python build.py
/venv/bin/python build_deb.py --from-dist --out /out $PROVISIONING_ARG
chown -R "$HOST_UID:$HOST_GID" /out
"""

EXCLUDE = {"venv", ".venv", "dist", "build", "data", "runtime", "build_logs", ".git", "__pycache__",
           "node_modules", "build_temp_data", "e2e", "review"}


def stage_source(dest: Path) -> None:
    say("Copying the source for the container")
    with tarfile.open(dest, "w") as tar:
        for path in sorted(PROJECT_ROOT.iterdir()):
            if path.name in EXCLUDE or path.name.endswith((".deb", ".tar.gz")):
                continue
            tar.add(path, arcname=path.name,
                    filter=lambda info: None if "__pycache__" in info.name else info)


def build_in_container(provisioning: Path | None) -> Path:
    if not shutil.which("docker"):
        raise SystemExit("❌ Docker is needed to build for Ubuntu 24.04 (or use --from-dist)")
    sys.path.insert(0, str(PROJECT_ROOT))
    import runtime_id

    declared = runtime_id.declared(PROJECT_ROOT)
    out = OUT_DIR / "deb-out"
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory() as stage:
        stage_dir = Path(stage)
        stage_source(stage_dir / "src.tar")
        args = ""
        if provisioning:
            shutil.copy2(provisioning, stage_dir / "provisioning.json")
            args = "--provisioning /stage/provisioning.json"
        say(f"Building in {BUILD_IMAGE} (Python {declared['python']}, PostgreSQL {declared['postgres']}, "
            f"Redis {declared['redis']}); the first time takes a while")
        subprocess.run([
            "docker", "run", "--rm", "-v", f"{stage_dir}:/stage:ro", "-v", f"{out}:/out",
            "-v", "cirqen-deb-cache:/root/.cache",
            "-e", f"PG_MAJOR={str(declared['postgres']).split('.')[0]}", "-e", f"PYTHON={declared['python']}",
            "-e", f"PYINSTALLER={PYINSTALLER}", "-e", f"PROVISIONING_ARG={args}",
            "-e", f"HOST_UID={os.getuid()}", "-e", f"HOST_GID={os.getgid()}",
            BUILD_IMAGE, "bash", "-c", CONTAINER_SCRIPT], check=True)
    debs = sorted(out.glob("*.deb"))
    if not debs:
        raise SystemExit("❌ the container build made no .deb")
    final = OUT_DIR / debs[-1].name
    shutil.move(str(debs[-1]), final)
    shutil.rmtree(out, ignore_errors=True)
    print(f"✅ {final}")
    return final


# ── testing on clean Ubuntu releases ────────────────────────────────────────

TEST_SCRIPT = r"""
set -uo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq >/dev/null
apt-get install -y -qq /deb/$DEB xvfb xauth xdotool dbus-x11 file >/dev/null 2>/tmp/apt.err || { cat /tmp/apt.err; exit 2; }
echo "installed: $(dpkg-query -W -f='${Version}' cirqen) on $(. /etc/os-release; echo $PRETTY_NAME)"
missing=$(find /opt/cirqen -type f \( -name '*.so*' -o -perm -u+x \) -exec sh -c 'file -b "$1" | grep -q ELF && LD_LIBRARY_PATH= ldd "$1" 2>/dev/null | grep "not found" | sed "s|^|$1: |"' _ {} \; | sort -u)
# *.libs/: libraries vendored inside Python wheels, which find each other at run time.
missing=$(echo "$missing" | grep -v -e "libtiff.so.5" -e "\.libs/" | grep . || true)
if [ -n "$missing" ]; then echo "MISSING LIBRARIES:"; echo "$missing" | head -30; fi
test -f /usr/share/applications/cirqen.desktop && echo "menu entry: ok"
useradd -m tester
su tester -c 'cd ~ && xvfb-run -a -s "-screen 0 1280x800x24" bash -c "cirqen >/tmp/cirqen.out 2>&1 & for i in \$(seq 1 $WAIT); do sleep 2; xdotool key Return 2>/dev/null; [ -s ~/.local/share/cirqen/full_update_ok ] && exit 0; done; exit 1"'
status=$?
if [ $status -eq 0 ]; then echo "STARTED: Cirqen $(cat /home/tester/.local/share/cirqen/full_update_ok) opened its window"
else echo "DID NOT START within $((WAIT*2))s"; echo "--- output"; tail -30 /tmp/cirqen.out
  for f in /home/tester/.local/share/cirqen/logs/launcher.log /home/tester/.local/share/cirqen/logs/django.log; do
    [ -f "$f" ] && { echo "--- $f"; grep -v "^\s*$" "$f" | tail -30; }; done
  ls -la /home/tester/.local/share/cirqen /home/tester/.local/share/cirqen-app 2>&1 | head -30; fi
exit $status
"""


def test_deb(deb: Path, images=TEST_IMAGES, wait_steps: int = 150) -> bool:
    ok = True
    for image in images:
        say(f"Installing and starting {deb.name} on a clean {image}")
        result = subprocess.run(["docker", "run", "--rm", "-v", f"{deb.parent}:/deb:ro", "-e", f"DEB={deb.name}",
                                 "-e", f"WAIT={wait_steps}", "--shm-size=1g", image, "bash", "-c", TEST_SCRIPT])
        ok = ok and result.returncode == 0
        print(("✅ " if result.returncode == 0 else "❌ ") + image)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--provisioning", type=Path, help="a hospital's installer file from the admin panel")
    parser.add_argument("--from-dist", action="store_true", help="package the existing dist/Cirqen as it is")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="where to put the .deb")
    parser.add_argument("--test", action="store_true", help="install and start it on clean Ubuntu releases")
    parser.add_argument("--test-only", type=Path, metavar="DEB", help="only test an existing .deb")
    args = parser.parse_args()
    if args.test_only:
        return 0 if test_deb(args.test_only.resolve()) else 1
    if args.provisioning:
        check_provisioning(args.provisioning)
    if args.from_dist:
        args.out.mkdir(parents=True, exist_ok=True)
        deb = make_deb(DIST_APP, args.out, args.provisioning)
    else:
        deb = build_in_container(args.provisioning)
    if args.test:
        return 0 if test_deb(deb) else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
