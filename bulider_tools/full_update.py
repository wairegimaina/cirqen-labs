"""Install the full app when a release needs a different runtime.

A code update replaces Cirqen's .py files; it can't replace the Python, the
libraries or the embedded databases inside the app folder (runtime_id.py).
When Control answers "full_required" (hq_server desktop_releases.py), the PC:

  1. prepare(): downloads the full app Control vouched for (signed offer,
     checked against its sha256), unpacks it next to the app folder
     (<app>.new) and copies over this install's own files (provisioning.json).
  2. start_swap(): when the user restarts, writes a small script that waits
     for Cirqen to close, renames <app> to <app>.previous and <app>.new to
     <app>, and starts the new app.
  3. The new app calls confirm_started() once its window is up. If that
     doesn't happen within SWAP_WAIT seconds, the script stops it, puts the
     previous folder back, records the failure (so the same version isn't
     offered again in a loop) and starts the previous app.

The data folder (database, settings, keys) is never moved: only the app
folder is swapped. Linux only for now: elsewhere the user is told to install
the full app by hand.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)

CONTEXT = b"cirqen-full-app-v1\n"           # hq_server/desktop_releases.py
OK_FILE = "full_update_ok"
SWAP_WAIT = 300
KEEP_FILES = ("provisioning.json",)          # this install's own files, carried over


class FullUpdateError(Exception):
    pass


def supported() -> bool:
    return sys.platform.startswith("linux")


def verify_offer(offer: dict, public_key) -> dict:
    """The package Control described, if Control signed it. public_key: the
    update signing key, as base64 or as a key object (endpoint_sync._public_key)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not public_key:
        raise FullUpdateError("no update signing key on this PC; can't trust a full app")
    try:
        key = (Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key))
               if isinstance(public_key, str) else public_key)
        key.verify(base64.b64decode(offer["signature"] or ""), CONTEXT + offer["document"].encode())
        package = json.loads(offer["document"])
    except Exception as exc:  # noqa: BLE001
        raise FullUpdateError("the full app offer is not signed by Cirqen Control") from exc
    return package


def new_dir_for(app_path: Path) -> Path:
    return Path(app_path).with_name(Path(app_path).name + ".new")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unpack(archive: Path, target: Path) -> None:
    """Extract the archive's single top folder (Cirqen/) as target."""
    tmp = target.with_name(target.name + ".unpacking")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        if archive.name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(tmp)
        else:
            with tarfile.open(archive) as tar:
                tar.extractall(tmp, filter="tar")     # keeps the executables' modes
        tops = [p for p in tmp.iterdir()]
        source = tops[0] if len(tops) == 1 and tops[0].is_dir() else tmp
        shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(source), str(target))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def prepare(offer: dict, *, app_path: Path, staging: Path, public_key,
            download, progress=None) -> dict:
    """Download, check and unpack the full app next to app_path.

    download(url, dest_path, progress) fetches the archive (the caller adds
    its API key). Returns the verified package description, with "new_dir".
    """
    package = verify_offer(offer, public_key)
    app_path, staging = Path(app_path), Path(staging)
    staging.mkdir(parents=True, exist_ok=True)
    archive = staging / package["file"]
    if not (archive.is_file() and _sha256(archive) == package["sha256"]):
        part = archive.with_suffix(archive.suffix + ".part")
        download(offer["download_url"], part, progress)
        if _sha256(part) != package["sha256"]:
            part.unlink(missing_ok=True)
            raise FullUpdateError("the full app download doesn't match Control's checksum")
        part.replace(archive)
    new_dir = new_dir_for(app_path)
    _unpack(archive, new_dir)
    stamped = (new_dir / "_internal" / "runtime_id.txt")
    if not stamped.is_file() or stamped.read_text().strip() != package["runtime_id"]:
        shutil.rmtree(new_dir, ignore_errors=True)
        raise FullUpdateError("the full app's runtime isn't the one Control described")
    for name in KEEP_FILES:
        if (app_path / name).is_file() and not (new_dir / name).exists():
            shutil.copy2(app_path / name, new_dir / name)
    archive.unlink(missing_ok=True)       # unpacked; the space is better left free
    return {**package, "new_dir": str(new_dir)}


SWAP_SCRIPT = r"""#!/bin/bash
# Written by Cirqen (bulider_tools/full_update.py): swap in the full app.
PID="$1"; APP="$2"; NEW="$3"; VER="$4"; DATA="$5"; WAIT="$6"
mkdir -p "$DATA/logs"; exec >>"$DATA/logs/full_update.log" 2>&1
echo "$(date '+%F %T') installing the full app $VER"
for i in $(seq 1 120); do kill -0 "$PID" 2>/dev/null || break; sleep 1; done
if kill -0 "$PID" 2>/dev/null; then echo "Cirqen did not close; nothing changed"; exit 1; fi
start() { setsid "$1/start_cirqen.sh" </dev/null >/dev/null 2>&1 & }
rm -rf "$APP.previous"
if ! mv "$APP" "$APP.previous"; then echo "could not move the app folder; nothing changed"; start "$APP"; exit 1; fi
if ! mv "$NEW" "$APP"; then echo "could not put the new app in place; back"; mv "$APP.previous" "$APP"; start "$APP"; exit 1; fi
rm -f "$DATA/full_update_ok"
start "$APP"
for i in $(seq 1 "$WAIT"); do
  if [ "$(cat "$DATA/full_update_ok" 2>/dev/null)" = "$VER" ]; then echo "$VER started; done"; exit 0; fi
  sleep 1
done
echo "$VER did not start within ${WAIT}s; going back to the previous app"
pkill -f "$APP/Cirqen" ; sleep 5 ; pkill -9 -f "$APP/Cirqen"
rm -rf "$APP.failed"; mv "$APP" "$APP.failed"; mv "$APP.previous" "$APP"
mkdir -p "$DATA/sync_state"
printf '{"version": "%s", "checksum": "", "error": "the full app did not start; the previous app was put back"}' "$VER" > "$DATA/sync_state/update_failed.json"
start "$APP"
echo "back on the previous app"
exit 1
"""


def start_swap(*, app_path: Path, new_dir: Path, version: str, data_path: Path,
               pid: int | None = None, wait: int = SWAP_WAIT, launcher=subprocess.Popen) -> Path:
    """Start the swap script in the background; Cirqen should then quit."""
    if not supported():
        raise FullUpdateError("installing the full app by itself is only available on Linux")
    app_path, new_dir, data_path = Path(app_path), Path(new_dir), Path(data_path)
    if not (new_dir / "start_cirqen.sh").is_file():
        raise FullUpdateError(f"{new_dir} isn't a prepared app")
    if not os.access(app_path.parent, os.W_OK):
        raise FullUpdateError(f"{app_path.parent} isn't writable: ask IT to install the full app")
    script = data_path / "full_update_swap.sh"
    script.write_text(SWAP_SCRIPT)
    script.chmod(0o755)
    launcher(["setsid", "/bin/bash", str(script), str(pid or os.getpid()), str(app_path), str(new_dir),
              version, str(data_path), str(wait)],
             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
             start_new_session=True)
    return script


def confirm_started(data_path: Path, version: str) -> None:
    """Called by the app once its window is up: the swap script stops waiting."""
    try:
        (Path(data_path) / OK_FILE).write_text(version)
    except OSError as exc:
        logger.warning("Could not confirm the app started: %s", exc)
