"""
app_updates.py — Cirqen Application Update Service
====================================================
A self-contained service that handles the full update lifecycle:

  1. Periodic version polling  (every CHECK_INTERVAL_SECS)
  2. Background download + SHA-256 verification
  3. Status broadcast via Qt signals AND update_status.json
  4. Manual "Check Now" trigger from the UI
  5. apply_and_restart() — applies the staged package then relaunches the app

Change-type handling
--------------------
Updates are classified by what they touch (from manifest.json):

  "backend"   — Python source files  (.py)
                Requires a full app restart.  The Updater copies files,
                runs `manage.py migrate`, writes .restart_required, and
                calls QApplication.quit() so the launcher relaunches.

  "frontend"  — Django templates / static assets  (.html, .js, .css, …)
                No restart needed.  After files are copied the service
                calls `manage.py collectstatic --no-input` and emits
                frontend_applied so the MainWindow can do a web-view reload.

  "migration" — standalone Django migrations  (.py inside migrations/)
                Runs `manage.py migrate` only.  No restart unless other
                backend files changed in the same package.

  "mixed"     — package contains both backend + frontend files.
                Restart path wins.

The Updater (hq_server/updater.py) already classifies every file with a
"type" field in manifest.json ("backend", "template", "static", "migration").
This service reads that manifest before applying and picks the right path.

Usage (ServiceManager)::

    from bulider_tools.app_updates import AppUpdateService

    update_service = AppUpdateService(
        data_path=DATA_PATH,
        app_path=APPLICATION_PATH,
    )
    update_service.update_available.connect(ui_slot)
    update_service.update_ready.connect(ui_slot)
    update_service.frontend_applied.connect(main_window.refresh_page)
    update_service.status_changed.connect(status_bar_slot)
    update_service.start()
    ...
    update_service.stop()

Trigger restart from UI::

    update_service.apply_and_restart(new_version)
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import subprocess
import sys
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger("AppUpdateService")

# ── Tunable constants ─────────────────────────────────────────────────────────

STARTUP_DELAY_SECS: int   = 20
CHECK_INTERVAL_SECS: int  = 6 * 60 * 60   # 6 hours
REQUEST_TIMEOUT_SECS: int = 20
DOWNLOAD_TIMEOUT_SECS: int = 120

_DEFAULT_HQ_URL  = "https://cirqen-hq.onrender.com"
_DEFAULT_API_KEY = ""

# File extensions that classify as frontend-only (no restart needed)
_FRONTEND_EXTS = {
    ".html", ".htm", ".css", ".js", ".ts", ".jsx", ".tsx",
    ".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff",
    ".woff2", ".ttf", ".eot", ".map",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _classify_package(manifest: dict) -> str:
    """
    Returns 'frontend', 'backend', 'migration', or 'mixed'.
    'backend' and 'mixed' both require a restart; 'frontend' does not.
    """
    has_backend   = False
    has_frontend  = False
    has_migration = False

    for entry in manifest.get("files", []):
        ftype = entry.get("type", "")
        path  = entry.get("path", "")
        ext   = Path(path).suffix.lower()

        if ftype == "migration" or "migrations" in path:
            has_migration = True
        elif ftype in ("template", "static") or ext in _FRONTEND_EXTS:
            has_frontend = True
        else:
            has_backend = True

    if has_backend and has_frontend:
        return "mixed"
    if has_backend:
        return "backend"
    if has_migration and not has_frontend:
        return "migration"
    if has_frontend:
        return "frontend"
    return "backend"   # conservative default


# ── Service class ─────────────────────────────────────────────────────────────

class AppUpdateService(QObject):
    """
    Long-running update service with Qt signal support.

    Signals
    -------
    update_available(version, changes, critical)
        New version detected on HQ — not yet downloaded.

    update_ready(version, staged_path, change_type)
        Package downloaded, verified, and staged.
        change_type is one of: 'frontend', 'backend', 'migration', 'mixed'.

    frontend_applied(version)
        Frontend-only update applied without restart.
        MainWindow should call web_view.reload() on this signal.

    status_changed(status_dict)
        Emitted on every status transition.

    error_occurred(message)
        Network / file-system error in a check cycle.
    """

    update_available = Signal(str, str, bool)    # version, changes, critical
    update_ready     = Signal(str, str, str)     # version, staged_path, change_type
    frontend_applied = Signal(str)               # version
    status_changed   = Signal(dict)
    error_occurred   = Signal(str)

    # ------------------------------------------------------------------
    def __init__(self, data_path: Path, app_path: Path, parent: QObject = None):
        super().__init__(parent)

        self._data_path  = Path(data_path)
        self._app_path   = Path(app_path)
        self._status_dir = self._data_path / "sync_state"
        self._staging    = self._data_path / "update_staging"
        self._status_dir.mkdir(parents=True, exist_ok=True)
        self._staging.mkdir(parents=True, exist_ok=True)

        self._hq_url  = _DEFAULT_HQ_URL
        self._api_key = _DEFAULT_API_KEY

        self._stop_event  = threading.Event()
        self._check_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._status: dict = {
            "checking": False, "downloading": False,
            "update_available": False, "update_ready": False,
            "new_version": None, "changes": "", "critical": False,
            "change_type": None,
            "download_progress": 0, "server_available": None,
            "last_check": None, "staged_path": None, "error": None,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._check_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="AppUpdateService", daemon=True
        )
        self._thread.start()
        logger.info("AppUpdateService started (interval=%ds)", CHECK_INTERVAL_SECS)

    def stop(self, timeout: int = 5):
        self._stop_event.set()
        self._check_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        logger.info("AppUpdateService stopped")

    def check_now(self):
        """Trigger an immediate check (call from UI 'Check Now' button)."""
        logger.info("AppUpdateService: manual check triggered")
        self._check_event.set()

    def get_status(self) -> dict:
        return dict(self._status)

    def apply_and_restart(self, version: str):
        """
        Apply the staged package for *version* and restart the application.

        Runs in a background thread so the Qt event loop stays responsive
        while the Updater copies files and runs migrations.

        For frontend-only packages: applies files + collectstatic, then emits
        frontend_applied (no restart).  For everything else: applies files,
        runs migrations, writes the sentinel, and quits Qt.
        """
        staged_zip = self._staging / f"cirqen_update_v{version}.zip"

        # Also check status for an explicit staged_path
        if not staged_zip.exists():
            alt = self._status.get("staged_path", "")
            if alt and Path(alt).exists():
                staged_zip = Path(alt)

        if not staged_zip.exists():
            logger.error("apply_and_restart: package not found for v%s", version)
            self.error_occurred.emit(f"Update package for v{version} not found.")
            return

        threading.Thread(
            target=self._apply_thread,
            args=(version, staged_zip),
            name="AppUpdateApply",
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # Apply thread
    # ------------------------------------------------------------------

    def _apply_thread(self, version: str, staged_zip: Path):
        """Runs in a background thread — copies files, migrates, restarts."""
        import queue as _q

        logger.info("AppUpdateService._apply_thread: applying v%s", version)

        # Peek at the manifest to decide restart vs. hot-reload
        try:
            with zipfile.ZipFile(staged_zip, "r") as zf:
                manifest = json.loads(zf.read("manifest.json"))
        except Exception as exc:
            logger.error("Cannot read manifest: %s", exc)
            self.error_occurred.emit(f"Cannot read update manifest: {exc}")
            return

        change_type = _classify_package(manifest)
        logger.info("AppUpdateService: change_type=%s for v%s", change_type, version)

        # Import Updater (lives in updates/updater.py or hq_server/updater.py)
        try:
            try:
                from updates.updater import Updater
            except ImportError:
                from hq_server.updater import Updater
        except ImportError as exc:
            logger.error("Cannot import Updater: %s", exc)
            self.error_occurred.emit(f"Updater not found: {exc}")
            return

        progress_q = _q.Queue()
        updater = Updater(
            package_url=str(staged_zip),
            version=version,
            progress_queue=progress_q,
            is_local_file=True,
        )

        # Run the Updater synchronously in this thread
        updater.run()

        # Drain events from the queue (for logging)
        while not progress_q.empty():
            try:
                evt = progress_q.get_nowait()
                logger.info("Updater event: %s — %s", evt.get("event"), evt.get("data", {}).get("message", ""))
            except Exception:
                break

        if change_type == "frontend":
            # Hot-reload: run collectstatic then signal the web view to refresh
            self._run_collectstatic()
            logger.info("AppUpdateService: frontend update applied — reloading web view")
            self.frontend_applied.emit(version)
            self._write_status(
                update_available=False, update_ready=False,
                new_version=None, staged_path=None,
            )

        elif change_type == "migration":
            # Migrations only — already run by Updater; no restart needed
            logger.info("AppUpdateService: migration-only update applied")
            self._write_status(
                update_available=False, update_ready=False,
                new_version=None, staged_path=None,
            )
            self.frontend_applied.emit(version)   # signal UI to show success

        else:
            # backend or mixed — full restart required
            logger.info("AppUpdateService: backend update applied — restarting app")
            self._trigger_app_restart(version)

    def _run_collectstatic(self):
        """Run Django's collectstatic to publish new static files."""
        try:
            python   = sys.executable
            manage   = self._app_path / "manage.py"
            if not manage.exists():
                manage = self._app_path / "_internal" / "manage.py"
            if not manage.exists():
                logger.warning("manage.py not found — skipping collectstatic")
                return

            result = subprocess.run(
                [python, str(manage), "collectstatic", "--no-input", "--clear"],
                capture_output=True, text=True,
                cwd=str(self._app_path),
            )
            if result.returncode == 0:
                logger.info("AppUpdateService: collectstatic OK")
            else:
                logger.warning("collectstatic returned %d: %s", result.returncode, result.stderr[:300])
        except Exception as exc:
            logger.warning("collectstatic error: %s", exc)

    def _trigger_app_restart(self, version: str):
        """Write restart sentinel and quit Qt — launcher will relaunch."""
        from bulider_tools.runtime import get_restart_command

        # Write sentinel so the app.py on_ready block knows to relaunch
        # Prefer DATA_PATH first (always writable and consistent across modes)
        for candidate in (
            self._data_path / ".restart_required",
            self._app_path / ".restart_required",
            self._app_path / "_internal" / ".restart_required",
        ):
            try:
                candidate.write_text(version)
                logger.info("Restart sentinel written: %s", candidate)
                break
            except Exception:
                continue

        # Clear staged status so the restart doesn't re-prompt
        self._write_status(
            update_available=False, update_ready=False,
            new_version=None, staged_path=None,
        )

        # Quit the Qt event loop from the main thread
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QTimer
        QTimer.singleShot(500, QApplication.instance().quit)

    # ------------------------------------------------------------------
    # Service loop
    # ------------------------------------------------------------------

    def _run(self):
        interrupted = self._stop_event.wait(timeout=STARTUP_DELAY_SECS)
        if interrupted:
            return

        while not self._stop_event.is_set():
            self._check_event.clear()
            self._reload_config()
            self._run_check_cycle()
            logger.info("AppUpdateService: next check in %ds", CHECK_INTERVAL_SECS)
            self._check_event.wait(timeout=CHECK_INTERVAL_SECS)

        logger.info("AppUpdateService: loop exiting")

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _reload_config(self):
        cfg_file = self._data_path / "config.json"
        if not cfg_file.exists():
            return
        try:
            cfg = json.loads(cfg_file.read_text())
            self._hq_url = (
                cfg.get("hq_server_url") or cfg.get("server_url") or
                cfg.get("hq_url") or cfg.get("update", {}).get("server_url") or
                _DEFAULT_HQ_URL
            ).rstrip("/")
            self._api_key = (
                cfg.get("hq_api_key") or cfg.get("api_key") or
                cfg.get("update", {}).get("api_key") or _DEFAULT_API_KEY
            )
        except Exception as exc:
            logger.warning("Cannot read config.json: %s", exc)

    # ------------------------------------------------------------------
    # Check cycle
    # ------------------------------------------------------------------

    def _run_check_cycle(self):
        # Honour config enabled flag
        cfg_file = self._data_path / "config.json"
        if cfg_file.exists():
            try:
                cfg = json.loads(cfg_file.read_text())
                if not cfg.get("update", {}).get("enabled", True):
                    self._write_status(checking=False, server_available=False,
                                       error="Update checking disabled")
                    return
            except Exception:
                pass

        if not self._api_key:
            self._write_status(checking=False, server_available=False,
                               error="No API key configured")
            return

        current_version = self._read_current_version()
        self._write_status(checking=True, error=None)

        # Step 1 — version check
        try:
            resp = requests.get(
                f"{self._hq_url}/api/updates/latest/",
                params={"current_version": current_version},
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT_SECS,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            msg = str(exc)
            logger.warning("AppUpdateService: version check failed: %s", msg)
            self._write_status(checking=False, server_available=False, error=msg)
            self.error_occurred.emit(msg)
            return

        if not data.get("update_available"):
            self._write_status(checking=False, server_available=True,
                               update_available=False)
            return

        new_version  = data["version"]
        download_url = data["download_url"]
        checksum     = data.get("checksum", "")
        changes      = data.get("changes", "")
        critical     = data.get("critical", False)

        self._write_status(checking=False, server_available=True,
                           update_available=True, new_version=new_version,
                           changes=changes, critical=critical)
        self.update_available.emit(new_version, changes, critical)

        # Step 2 — already staged?
        staged_zip = self._staging / f"cirqen_update_v{new_version}.zip"
        if staged_zip.exists():
            if checksum and _sha256(staged_zip) == checksum:
                change_type = self._peek_change_type(staged_zip)
                self._write_status(update_available=True, update_ready=True,
                                   new_version=new_version, changes=changes,
                                   change_type=change_type,
                                   staged_path=str(staged_zip))
                self.update_ready.emit(new_version, str(staged_zip), change_type)
                return
            staged_zip.unlink(missing_ok=True)

        # Step 3 — download
        if self._stop_event.is_set():
            return

        self._write_status(update_available=True, downloading=True,
                           new_version=new_version, download_progress=0)

        tmp_path = self._staging / f"cirqen_update_v{new_version}.tmp"
        try:
            with requests.get(download_url, headers=self._headers(),
                              stream=True, timeout=DOWNLOAD_TIMEOUT_SECS) as r:
                r.raise_for_status()
                total, received = int(r.headers.get("Content-Length", 0)), 0
                with tmp_path.open("wb") as f:
                    for chunk in r.iter_content(chunk_size=65536):
                        if self._stop_event.is_set():
                            tmp_path.unlink(missing_ok=True)
                            return
                        if chunk:
                            f.write(chunk)
                            received += len(chunk)
                            if total:
                                pct = int(received / total * 100)
                                self._write_status(downloading=True,
                                                   new_version=new_version,
                                                   download_progress=pct)
        except Exception as exc:
            msg = f"Download failed: {exc}"
            tmp_path.unlink(missing_ok=True)
            self._write_status(downloading=False, new_version=new_version, error=msg)
            self.error_occurred.emit(msg)
            return

        # Step 4 — checksum
        if checksum and _sha256(tmp_path) != checksum:
            tmp_path.unlink(missing_ok=True)
            msg = "Checksum mismatch — package corrupted, will retry next cycle"
            self._write_status(new_version=new_version, error=msg)
            self.error_occurred.emit(msg)
            return

        # Step 5 — ZIP sanity + classify
        try:
            with zipfile.ZipFile(tmp_path, "r") as zf:
                if "manifest.json" not in zf.namelist():
                    raise ValueError("manifest.json missing")
                manifest    = json.loads(zf.read("manifest.json"))
                change_type = _classify_package(manifest)
        except Exception as exc:
            tmp_path.unlink(missing_ok=True)
            msg = f"Invalid package: {exc}"
            self._write_status(new_version=new_version, error=msg)
            self.error_occurred.emit(msg)
            return

        # Step 6 — promote
        tmp_path.rename(staged_zip)
        logger.info("AppUpdateService: v%s staged (%s)", new_version, change_type)

        self._write_status(update_available=True, update_ready=True, downloading=False,
                           new_version=new_version, changes=changes,
                           change_type=change_type, staged_path=str(staged_zip))
        self.update_ready.emit(new_version, str(staged_zip), change_type)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _peek_change_type(self, zip_path: Path) -> str:
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                manifest = json.loads(zf.read("manifest.json"))
            return _classify_package(manifest)
        except Exception:
            return "backend"

    def _headers(self) -> dict:
        h = {"User-Agent": "Cirqen-AppUpdateService/1.0"}
        if self._api_key:
            h["X-Api-Key"] = self._api_key
        return h

    def _read_current_version(self) -> str:
        """
        Read the running app version from multiple sources in priority order:

          1. version.txt  (produced by the build/release pipeline)
          2. Django settings.APP_VERSION  (authoritative in-code version)
          3. settings.py parsed directly as text  (Django not yet initialised)
          4. Falls back to "0.0.0" so the server always sees an upgrade need
             rather than silently skipping the check.
        """
        # ── 1. version.txt ────────────────────────────────────────────────
        for candidate in (
            self._app_path / "version.txt",
            self._app_path / "_internal" / "version.txt",
            self._data_path / "version.txt",
        ):
            if candidate.exists():
                ver = candidate.read_text().strip()
                if ver and ver != "0.0.0":
                    logger.debug("Version from %s: %s", candidate.name, ver)
                    return ver

        # ── 2. Django settings.APP_VERSION (if Django is already set up) ──
        try:
            import django
            from django.conf import settings as _dj_settings
            if _dj_settings.configured:
                ver = str(getattr(_dj_settings, "APP_VERSION", "") or "").strip()
                if ver:
                    logger.debug("Version from Django settings: %s", ver)
                    return ver
        except Exception:
            pass

        # ── 3. Parse settings.py directly (Django not yet initialised) ────
        settings_candidates = [
            # Installed DEB layout: e.g. /opt/cirqen/Equiper/settings.py
            self._app_path / "Equiper" / "settings.py",
            self._app_path / "_internal" / "Equiper" / "settings.py",
            # Dev / source layout: project root sibling
            self._app_path.parent / "Equiper" / "settings.py",
        ]
        for sf in settings_candidates:
            if not sf.exists():
                continue
            try:
                for line in sf.read_text(errors="replace").splitlines():
                    line = line.strip()
                    if line.startswith("APP_VERSION"):
                        # handles:  APP_VERSION = '1.3.5'  or  APP_VERSION = "1.3.5"
                        parts = line.split("=", 1)
                        if len(parts) == 2:
                            ver = parts[1].strip().strip("'\"")
                            if ver:
                                logger.debug(
                                    "Version from %s (text parse): %s", sf.name, ver
                                )
                                return ver
            except Exception as exc:
                logger.warning("Could not parse %s: %s", sf, exc)

        logger.warning(
            "AppUpdateService: could not determine current version — "
            "defaulting to 0.0.0.  Update checks may always report an upgrade."
        )
        return "0.0.0"

    def _write_status(self, **fields):
        if "checking" not in fields:
            fields["checking"] = False
        if "downloading" not in fields:
            fields["downloading"] = False
        if fields.get("server_available") is not None or fields.get("error"):
            fields["last_check"] = datetime.now(timezone.utc).isoformat()

        self._status.update(fields)

        status_file = self._status_dir / "update_status.json"
        try:
            status_file.write_text(json.dumps(self._status, indent=2))
        except Exception as exc:
            logger.warning("Cannot write update_status.json: %s", exc)

        try:
            self.status_changed.emit(dict(self._status))
        except Exception:
            pass
