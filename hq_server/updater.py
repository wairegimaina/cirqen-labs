"""
Equiper Update Engine
=====================
Handles the full lifecycle of an update:
  1. Download (remote HQ server) OR read a local file
  2. Validate SHA-256 checksums
  3. Create a timestamped backup of every file that will change
  4. Apply files (templates / static / Python code / migrations)
  5. Run migrations — with race protection when multiple machines update at once
  6. Signal the application to reload via a sentinel file

Update package formats
----------------------
  • .zip    — built by build_package.py on Render (preferred)
  • .tar.xz — legacy streaming format

Both must contain:
  manifest.json          ← required
  files/
    <relative paths to changed files>

Migration race handling
-----------------------
When many machines apply the same update simultaneously, they all try to run
`manage.py migrate` against any shared database (e.g. HQ PostgreSQL) at the
same time. Django's advisory lock prevents data corruption but machines 2–N
hang waiting, then timeout and report failure even though nothing is wrong.

Fix: before migrating a shared DB, each machine asks the HQ server for a lock.
Only one machine migrates; the others wait and retry. After 5 failed attempts
they call showmigrations to verify the DB is already up to date and skip.

For the current SQLite-per-machine setup the lock calls are cheap (~10 ms)
no-ops. The code is wired in now so adding HQ Postgres later requires zero
changes to the updater.
"""

import hashlib
import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

BASE_DIR: Path = settings.BASE_DIR
SENTINEL_FILE = BASE_DIR / ".restart_required"

UPDATE_STAGING = BASE_DIR / "update_staging"
UPDATE_BACKUPS = BASE_DIR / "update_backups"
UPDATE_STAGING.mkdir(exist_ok=True)
UPDATE_BACKUPS.mkdir(exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _emit(q: queue.Queue, event: str, data: dict):
    q.put({"event": event, "data": data})


# ── Main updater ──────────────────────────────────────────────────────────────

class Updater:
    """
    Thread-safe update applier. Supports remote downloads and local files.

    Usage::

        progress_q = queue.Queue()

        # Remote download from Render HQ:
        updater = Updater(
            package_url="https://updates.example.com/api/updates/download/1.0.1/",
            version="1.0.1",
            progress_queue=progress_q,
            download_headers={"X-Api-Key": "your-key"},
        )

        # Local upload from the update dashboard:
        updater = Updater(
            package_url="/path/to/uploaded.zip",
            version="1.0.1",
            progress_queue=progress_q,
            is_local_file=True,
        )

        threading.Thread(target=updater.run, daemon=True).start()
    """

    def __init__(
        self,
        package_url: str,
        version: str,
        progress_queue: queue.Queue,
        expected_checksum: str = "",
        is_local_file: bool = False,
        download_headers: Optional[dict] = None,
    ):
        self.package_url = package_url
        self.version = version
        self.q = progress_queue
        self.expected_checksum = expected_checksum
        self.is_local_file = is_local_file
        self.download_headers = download_headers or {}

        self.staging_dir = UPDATE_STAGING / f"v{version}"
        self.backup_dir = (
            UPDATE_BACKUPS / f"v{version}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self):
        try:
            self._emit("started", {"message": f"Starting update to v{self.version}"})

            archive_path = self._acquire()
            manifest, extract_dir = self._validate_and_extract(archive_path)
            self._backup(manifest, extract_dir)
            self._apply_files(manifest, extract_dir)
            self._run_migrations()
            self._signal_restart()

            self._emit("complete", {
                "message": f"Update to v{self.version} applied. Restart required.",
                "restart_required": True,
            })

        except Exception as exc:
            logger.exception("Update failed")
            self._emit("error", {"message": str(exc)})
        finally:
            try:
                if self.staging_dir.exists():
                    shutil.rmtree(self.staging_dir, ignore_errors=True)
            except Exception:
                pass

    # ── Acquire (download or copy local) ──────────────────────────────────────

    def _acquire(self) -> Path:
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        if self.is_local_file:
            return self._copy_local()
        return self._download()

    def _copy_local(self) -> Path:
        src = Path(self.package_url)
        if not src.exists():
            raise FileNotFoundError(f"Uploaded package not found: {src}")

        if str(src).endswith(".tar.xz"):
            ext = ".tar.xz"
        elif str(src).endswith(".tar.gz"):
            ext = ".tar.gz"
        else:
            ext = src.suffix.lower()

        dest = self.staging_dir / f"update_v{self.version}{ext}"
        self._emit("downloading", {
            "message": f"Reading local package ({src.stat().st_size // 1024} KB)…",
            "progres": 50,
        })
        shutil.copy2(src, dest)
        self._emit("downloading", {"message": "Package ready.", "progress": 100})
        return dest

    def _download(self) -> Path:
        self._emit("downloading", {"message": "Downloading update package…", "progress": 0})

        response = requests.get(
            self.package_url,
            stream=True,
            timeout=120,
            headers=self.download_headers,  # carries X-Api-Key for Render auth
        )
        response.raise_for_status()

        url_lower = self.package_url.lower()
        if url_lower.endswith(".tar.xz"):
            ext = ".tar.xz"
        elif url_lower.endswith(".tar.gz"):
            ext = ".tar.gz"
        elif url_lower.endswith(".zip"):
            ext = ".zip"
        else:
            ext = ".zip"

        archive_path = self.staging_dir / f"update_v{self.version}{ext}"
        total = int(response.headers.get("Content-Length", 0))
        downloaded = 0

        with archive_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = int(downloaded / total * 100)
                        self._emit("downloading", {
                            "message": (
                                f"Downloading… {downloaded // 1024} KB"
                                f" / {total // 1024} KB"
                            ),
                            "progress": pct,
                        })

        self._emit("downloading", {"message": "Download complete.", "progress": 100})
        return archive_path

    # ── Validate + extract ────────────────────────────────────────────────────

    def _validate_and_extract(self, archive_path: Path):
        self._emit("validating", {"message": "Validating package checksum…"})

        if self.expected_checksum:
            actual = _sha256(archive_path)
            if actual != self.expected_checksum:
                raise ValueError(
                    f"Checksum mismatch! "
                    f"Expected {self.expected_checksum[:12]}… "
                    f"got {actual[:12]}…"
                )

        extract_dir = self.staging_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)
        self._emit("validating", {"message": "Extracting package…"})

        fname = archive_path.name.lower()
        if fname.endswith(".zip"):
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(extract_dir)
        else:
            mode = (
                "r:xz" if fname.endswith(".xz")
                else "r:gz" if fname.endswith(".gz")
                else "r:*"
            )
            with tarfile.open(archive_path, mode) as tar:
                tar.extractall(extract_dir)

        manifest_path = extract_dir / "manifest.json"
        if not manifest_path.exists():
            raise FileNotFoundError("manifest.json not found in update package")

        manifest = json.loads(manifest_path.read_text())

        # Per-file checksum verification
        files_dir = extract_dir / "files"
        bad = []
        for entry in manifest.get("files", []):
            fp = files_dir / entry["path"]
            if fp.exists() and entry.get("sha256"):
                if _sha256(fp) != entry["sha256"]:
                    bad.append(entry["path"])
        if bad:
            raise ValueError(f"Checksum mismatch for: {', '.join(bad)}")

        self._emit("validating", {
            "message": (
                f"Package valid. "
                f"{len(manifest.get('files', []))} files to update."
            ),
        })
        return manifest, extract_dir

    # ── Backup ────────────────────────────────────────────────────────────────

    def _backup(self, manifest: dict, extract_dir: Path):
        self._emit("backing_up", {"message": "Backing up files to be replaced…"})
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        backed_up = 0
        for entry in manifest.get("files", []):
            src = BASE_DIR / entry["path"]
            if src.exists():
                dest = self.backup_dir / entry["path"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                backed_up += 1

        (self.backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        self._emit("backing_up", {"message": f"Backed up {backed_up} files."})

    # ── Apply ─────────────────────────────────────────────────────────────────

    def _apply_files(self, manifest: dict, extract_dir: Path):
        files_dir = extract_dir / "files"
        file_list = manifest.get("files", [])
        total = len(file_list)

        type_labels = {
            "backend": "🐍 Backend",
            "template": "🖼  Template",
            "static": "🎨 Static",
            "migration": "🗄  Migration",
        }

        for i, entry in enumerate(file_list, 1):
            src = files_dir / entry["path"]
            dest = BASE_DIR / entry["path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            label = type_labels.get(entry.get("type", "backend"), "📄 File")
            self._emit("applying", {
                "message": f"{label}: {entry['path']}",
                "progress": int(i / total * 100),
                "current": i,
                "total": total,
            })

        self._emit("applying", {"message": f"All {total} files applied.", "progress": 100})

    # ── Migrations ────────────────────────────────────────────────────────────

    def _run_migrations(self):
        """
        Step 1 — always migrate local SQLite immediately, no lock needed.
        Step 2 — if a shared HQ PostgreSQL database is configured, acquire
                  the HQ migration lock first so only one machine migrates it.

        For the current SQLite-per-machine setup, Step 2 is skipped because
        _get_hq_db_config() returns None.  Wire in HQ Postgres credentials
        when you add a shared database and Step 2 activates automatically.
        """
        python = sys.executable
        manage_py = BASE_DIR / "manage.py"

        # ── Step 1: local SQLite ──────────────────────────────────────────────
        self._emit("migrating", {"message": "Migrating local database…"})
        result = subprocess.run(
            [python, str(manage_py), "migrate", "--database=default", "--no-input"],
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
        )
        if result.returncode != 0:
            logger.error("local migrate stderr: %s", result.stderr)
            raise RuntimeError(f"Local migration failed:\n{result.stderr[:500]}")

        self._emit("migrating", {"message": "Local database migrated ✓"})

        # ── Step 2: HQ PostgreSQL (only when configured) ──────────────────────
        hq_db = self._get_hq_db_config()
        if not hq_db:
            # No shared HQ database configured — nothing more to do
            return

        self._migrate_hq_with_lock(python, manage_py)

    def _get_hq_db_config(self) -> Optional[dict]:
        """
        Returns the HQ update server config dict if:
          a) UPDATE_SYSTEM['server_url'] is set, AND
          b) DATABASES has an 'hq' entry (shared HQ PostgreSQL)
        Otherwise returns None — migration lock is skipped entirely.
        """
        cfg = getattr(settings, "UPDATE_SYSTEM", {})
        server_url = cfg.get("server_url", "").rstrip("/")
        api_key = cfg.get("api_key", "")

        if not server_url or not api_key:
            return None

        databases = getattr(settings, "DATABASES", {})
        if "hq" not in databases:
            return None

        return {"server_url": server_url, "api_key": api_key}

    def _migrate_hq_with_lock(self, python, manage_py):
        """
        Acquire the HQ migration lock, run migrate --database=hq, release.

        Retry logic:
          - Up to 5 attempts, 30 s apart
          - If all 5 fail (another machine holds the lock the whole time),
            call showmigrations to check whether HQ is already fully migrated.
            If yes — skip gracefully. If no — raise so the update is marked failed.
        """
        cfg = self._get_hq_db_config()
        headers = {"X-Api-Key": cfg["api_key"]}
        machine_id = self._machine_id()
        hq_lock_acquired = False

        try:
            self._emit("migrating", {"message": "Requesting HQ migration lock…"})

            for attempt in range(1, 6):
                try:
                    resp = requests.post(
                        f"{cfg['server_url']}/api/migrations/acquire/",
                        params={"machine_id": machine_id, "version": self.version},
                        headers=headers,
                        timeout=15,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as exc:
                    # HQ server unreachable — warn and skip HQ migration
                    self._emit("migrating", {
                        "message": (
                            f"Cannot reach HQ server for migration lock "
                            f"({exc}). Skipping HQ migration."
                        ),
                    })
                    logger.warning("HQ migration lock request failed: %s", exc)
                    return

                if data.get("granted"):
                    hq_lock_acquired = True
                    self._emit("migrating", {
                        "message": "HQ lock acquired. Migrating HQ database…"
                    })
                    break
                else:
                    who = data.get("locked_by", "another machine")
                    self._emit("migrating", {
                        "message": (
                            f"HQ locked by {who}. "
                            f"Waiting 30 s… (attempt {attempt}/5)"
                        ),
                    })
                    time.sleep(30)

            if not hq_lock_acquired:
                # 5 attempts exhausted — check if HQ is already migrated
                self._emit("migrating", {
                    "message": "Could not acquire HQ lock. Checking HQ migration state…"
                })
                self._assert_hq_already_migrated(python, manage_py)
                return

            # Run the actual HQ migration
            result = subprocess.run(
                [python, str(manage_py), "migrate", "--database=hq", "--no-input"],
                capture_output=True,
                text=True,
                cwd=str(BASE_DIR),
            )
            if result.returncode != 0:
                logger.error("HQ migrate stderr: %s", result.stderr)
                raise RuntimeError(f"HQ migration failed:\n{result.stderr[:500]}")

            self._emit("migrating", {"message": "HQ database migrated ✓"})

        finally:
            # ALWAYS release — even if the migration itself raised
            if hq_lock_acquired:
                try:
                    requests.post(
                        f"{cfg['server_url']}/api/migrations/release/",
                        params={"machine_id": machine_id},
                        headers=headers,
                        timeout=10,
                    )
                    self._emit("migrating", {"message": "HQ migration lock released."})
                except Exception as exc:
                    logger.warning("Failed to release HQ migration lock: %s", exc)
                    # The HQ server's 5-minute watchdog will expire it automatically

    def _assert_hq_already_migrated(self, python, manage_py):
        """
        After failing to acquire the lock, verify HQ is fully migrated.
        Raises RuntimeError if there are unapplied migrations.
        """
        result = subprocess.run(
            [python, str(manage_py), "showmigrations", "--database=hq", "--plan"],
            capture_output=True,
            text=True,
            cwd=str(BASE_DIR),
        )
        unapplied = [
            line for line in result.stdout.splitlines()
            if line.strip().startswith("[ ]")
        ]
        if unapplied:
            raise RuntimeError(
                f"HQ migration lock timed out and {len(unapplied)} "
                f"migration(s) are still unapplied on HQ database."
            )
        self._emit("migrating", {
            "message": "HQ already fully migrated by another machine ✓"
        })

    # ── Restart ───────────────────────────────────────────────────────────────

    def _signal_restart(self):
        SENTINEL_FILE.write_text(self.version)
        self._emit("restarting", {"message": "Signalling application restart…"})

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _emit(self, event: str, data: dict):
        _emit(self.q, event, data)

    @staticmethod
    def _machine_id() -> str:
        try:
            from sync.device_id_generator import get_device_id
            return get_device_id()
        except Exception:
            import socket
            return socket.gethostname()


# ── Rollback helper ───────────────────────────────────────────────────────────

def rollback(version: str, progress_queue: Optional[queue.Queue] = None) -> bool:
    q = progress_queue or queue.Queue()

    candidates = sorted(
        [d for d in UPDATE_BACKUPS.iterdir() if d.name.startswith(f"v{version}_")],
        reverse=True,
    )
    if not candidates:
        _emit(q, "error", {"message": f"No backup found for v{version}"})
        return False

    backup_dir = candidates[0]
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        _emit(q, "error", {"message": "Backup manifest missing"})
        return False

    manifest = json.loads(manifest_path.read_text())
    _emit(q, "rolling_back", {"message": f"Restoring from {backup_dir.name}…"})

    for entry in manifest.get("files", []):
        src = backup_dir / entry["path"]
        dest = BASE_DIR / entry["path"]
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    _emit(q, "complete", {
        "message": "Rollback complete. Restart required.",
        "restart_required": True,
    })
    SENTINEL_FILE.write_text(f"rollback_{version}")
    return True
