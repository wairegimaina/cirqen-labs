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

# Use XDG_DATA_HOME so directories are always created in a user-writable
# location, even when the app runs from a read-only PyInstaller _internal/ path.
_CIRQEN_DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "cirqen"
UPDATE_STAGING = _CIRQEN_DATA / "update_staging"
UPDATE_BACKUPS = _CIRQEN_DATA / "update_backups"
UPDATE_STAGING.mkdir(parents=True, exist_ok=True)
UPDATE_BACKUPS.mkdir(parents=True, exist_ok=True)

STATE_FILE = _CIRQEN_DATA / "update_state.json"      # #5 crash-recovery breadcrumb
LOCK_FILE = _CIRQEN_DATA / "update.lock"             # #5 single-flight guard
BACKUP_KEEP = 5                                      # #10 backup retention

# #1 — Ed25519 public key that authenticates HQ packages. Embed the value
# printed by `python hq_server/build_package.py --genkeys`. If left empty the
# updater runs in unsigned (legacy) mode; once set, an unsigned/forged package
# is refused. Source of truth: settings.UPDATE_SYSTEM["public_key"], with this
# constant as a fallback for builds that don't route it through config.
UPDATE_PUBLIC_KEY = ""


def _public_key():
    raw = (getattr(settings, "UPDATE_SYSTEM", {}) or {}).get("public_key") or UPDATE_PUBLIC_KEY
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        import base64
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(raw))
    except Exception as exc:  # noqa: BLE001
        logger.error("Invalid UPDATE_PUBLIC_KEY: %s", exc)
        return None


def _verify_signature(manifest_bytes: bytes, signature_b64: Optional[str]):
    """Raise ValueError unless the manifest is authentic (when a key is set)."""
    pub = _public_key()
    if pub is None:
        logger.warning("Update signature NOT verified — no public key configured (unsigned mode)")
        return
    if not signature_b64:
        raise ValueError("Package is unsigned but a signing key is configured — refusing to apply")
    import base64
    try:
        pub.verify(base64.b64decode(signature_b64), manifest_bytes)
    except Exception as exc:  # noqa: BLE001 — cryptography raises InvalidSignature
        raise ValueError(f"Package signature verification FAILED: {exc}") from exc


# ── Persisted state (crash recovery) ──────────────────────────────────────────

def _write_state(phase: str, version: str, **extra):
    try:
        STATE_FILE.write_text(json.dumps({"phase": phase, "version": version,
                                          "ts": datetime.now().isoformat(), **extra}))
    except OSError:
        pass


def _clear_state():
    try:
        STATE_FILE.unlink()
    except OSError:
        pass


def _acquire_lock() -> bool:
    """Best-effort single-flight lock; steals a stale (>1h) lock from a dead run."""
    try:
        if LOCK_FILE.exists():
            age = time.time() - LOCK_FILE.stat().st_mtime
            if age < 3600:
                return False
        LOCK_FILE.write_text(str(os.getpid()))
        return True
    except OSError:
        return True  # never let lock IO block an update outright


def _release_lock():
    try:
        LOCK_FILE.unlink()
    except OSError:
        pass


def recover_if_needed(progress_queue: "Optional[queue.Queue]" = None) -> bool:
    """
    #5 — Call once at app startup. If a previous update died mid-apply, restore
    the most recent backup for that version so the app never boots half-patched.
    Returns True if a recovery rollback was performed.
    """
    if not STATE_FILE.exists():
        return False
    try:
        state = json.loads(STATE_FILE.read_text())
    except Exception:
        _clear_state()
        return False
    phase, version = state.get("phase"), state.get("version", "")
    if phase in {"complete", None}:
        _clear_state()
        return False
    logger.warning("Detected interrupted update (phase=%s, v=%s) — rolling back", phase, version)
    ok = rollback(version, progress_queue)
    _clear_state()
    _release_lock()
    return ok


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _emit(q: queue.Queue, event: str, data: dict):
    q.put({"event": event, "data": data})


def _invalidate_bytecode(py_path: Path):
    """
    Ensure a freshly written .py is not shadowed by a stale compiled copy.

    The backend now loads from loose source under _internal/ (see cirqen.spec),
    so Python caches each module's bytecode in an adjacent __pycache__/*.pyc.
    After we overwrite the .py we (a) bump its mtime so CPython's source-newer
    check forces a recompile, and (b) delete the matching cached .pyc outright.
    """
    if py_path.suffix != ".py":
        return
    try:
        os.utime(py_path, None)  # mark as modified "now"
    except OSError:
        pass
    cache = py_path.parent / "__pycache__"
    if cache.is_dir():
        stem = py_path.stem
        for pyc in cache.glob(f"{stem}.*.pyc"):
            try:
                pyc.unlink()
            except OSError:
                pass


# ── Main updater ──────────────────────────────────────────────────────────────

class Updater:
    """
    Thread-safe update applier. Supports remote downloads and local files.

    Usage::

        progress_q = queue.Queue()

        # Remote download from Render HQ:
        updater = Updater(
            package_url="https://cirqen-hq.onrender.com/api/updates/download/1.0.1/",
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
        # #5 — single-flight: never let two updates apply files concurrently.
        if not _acquire_lock():
            self._emit("error", {"message": "Another update is already in progress."})
            return
        try:
            self._emit("started", {"message": f"Starting update to v{self.version}"})
            self._preflight()                                   # #5 disk + writability

            _write_state("acquiring", self.version)
            archive_path = self._acquire()                      # #5 resumable download
            manifest, extract_dir = self._validate_and_extract(archive_path)  # #1 verify signature

            _write_state("backing_up", self.version)
            self._backup(manifest, extract_dir)
            self._backup_database()                             # #3 snapshot DB before migrate

            _write_state("applying", self.version)
            self._apply_files(manifest, extract_dir)            # #2 atomic + skip-unchanged
            self._apply_deletions(manifest)
            self._verify_tree(manifest)                         # #10 on-disk verification

            _write_state("migrating", self.version)
            self._run_migrations()

            _write_state("verifying", self.version)
            if not self._health_check():                        # #2 fresh-process health gate
                raise RuntimeError("Post-update health check failed")

            _write_state("complete", self.version)
            self._signal_restart()
            self._report("success")                             # #8
            self._emit("complete", {
                "message": f"Update to v{self.version} applied. Restart required.",
                "restart_required": True,
            })

        except Exception as exc:
            logger.exception("Update failed — attempting rollback")
            self._emit("error", {"message": str(exc)})
            rolled = self._rollback(str(exc))                   # #2 auto-rollback
            self._report("rolled_back" if rolled else "failed", str(exc))  # #8
        finally:
            _clear_state()
            _release_lock()
            self._prune_backups()                               # #10 retention
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
            "progress": 50,
        })
        shutil.copy2(src, dest)
        self._emit("downloading", {"message": "Package ready.", "progress": 100})
        return dest

    def _download(self) -> Path:
        # Detect extension from the path only (URLs may carry ?from_version=…).
        path_part = self.package_url.split("?", 1)[0].lower()
        if path_part.endswith(".tar.xz"):
            ext = ".tar.xz"
        elif path_part.endswith(".tar.gz"):
            ext = ".tar.gz"
        else:
            ext = ".zip"

        archive_path = self.staging_dir / f"update_v{self.version}{ext}"
        part = archive_path.with_name(archive_path.name + ".part")

        # #5 — resume a partial download if one is present.
        resume_from = part.stat().st_size if part.exists() else 0
        headers = dict(self.download_headers)
        if resume_from:
            headers["Range"] = f"bytes={resume_from}-"

        self._emit("downloading", {
            "message": "Resuming download…" if resume_from else "Downloading update package…",
            "progress": 0,
        })
        response = requests.get(self.package_url, stream=True, timeout=120, headers=headers)

        # Server ignored our Range (sent 200, not 206) → start clean.
        if resume_from and response.status_code == 200:
            resume_from = 0
            try:
                part.unlink()
            except OSError:
                pass
        response.raise_for_status()

        total = int(response.headers.get("Content-Length", 0)) + resume_from
        downloaded = resume_from
        with part.open("ab" if resume_from else "wb") as f:
            for chunk in response.iter_content(chunk_size=65536):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        self._emit("downloading", {
                            "message": f"Downloading… {downloaded // 1024} KB / {total // 1024} KB",
                            "progress": int(downloaded / total * 100),
                        })

        os.replace(part, archive_path)  # atomic promote of the completed download
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

        # #1 — authenticate the manifest BEFORE trusting anything inside it.
        manifest_bytes = manifest_path.read_bytes()
        sig_path = extract_dir / "manifest.sig"
        signature = sig_path.read_text().strip() if sig_path.exists() else None
        _verify_signature(manifest_bytes, signature)
        self._emit("validating", {"message": "Package signature verified."
                                  if _public_key() else "Package accepted (unsigned mode)."})

        manifest = json.loads(manifest_bytes)

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

        skipped = 0
        for i, entry in enumerate(file_list, 1):
            src = files_dir / entry["path"]
            dest = BASE_DIR / entry["path"]
            if not src.exists():
                continue  # delta packages only ship changed files
            # #6 — skip files already identical on disk (saves writes + mtime churn)
            want = entry.get("sha256")
            if want and dest.exists() and _sha256(dest) == want:
                skipped += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            # #2 — write to a temp sibling then atomically replace, so a crash
            # mid-write can never leave a half-written module on disk.
            tmp = dest.parent / f".{dest.name}.new"
            shutil.copy2(src, tmp)
            os.replace(tmp, dest)
            _invalidate_bytecode(dest)  # backend loads loose source — drop stale .pyc
            label = type_labels.get(entry.get("type", "backend"), "📄 File")
            self._emit("applying", {
                "message": f"{label}: {entry['path']}",
                "progress": int(i / total * 100),
                "current": i,
                "total": total,
            })

        self._emit("applying", {
            "message": f"Applied {total - skipped} file(s); {skipped} already current.",
            "progress": 100,
        })

    # ── Deletions (files removed in the new version, e.g. after a refactor) ────

    def _apply_deletions(self, manifest: dict):
        """
        Remove files that no longer exist in the new version.

        Without this a structural refactor (e.g. ``Inventory/views.py`` becoming
        the ``Inventory/views/`` package) would leave the old module on disk,
        shadowing or colliding with the replacement. Each removed file is backed
        up first (so ``rollback`` can restore it) and its cached bytecode dropped.
        Empty parent directories left behind are pruned.
        """
        deletions = manifest.get("deletions", [])
        if not deletions:
            return

        removed = 0
        for rel in deletions:
            dest = BASE_DIR / rel
            if not dest.exists():
                continue
            # Back up before removing, so rollback is complete.
            bak = self.backup_dir / "__deleted__" / rel
            bak.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(dest, bak)
            except OSError:
                pass
            _invalidate_bytecode(dest)
            try:
                dest.unlink()
                removed += 1
            except OSError as exc:
                logger.warning("Could not delete %s: %s", rel, exc)
                continue
            # Prune now-empty parent directories (but never climb past BASE_DIR).
            parent = dest.parent
            while parent != BASE_DIR and parent.is_dir() and not any(parent.iterdir()):
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

        self._emit("applying", {"message": f"Removed {removed} obsolete file(s)."})

    # ── Preflight / verification / health / rollback (#2, #3, #5, #10) ─────────

    def _preflight(self):
        """#5 — fail fast before touching anything if we can't safely apply."""
        if not os.access(BASE_DIR, os.W_OK):
            raise RuntimeError(f"Install directory is not writable: {BASE_DIR}")
        try:
            free = shutil.disk_usage(str(BASE_DIR)).free
            if free < 500 * 1024 * 1024:  # 500 MB headroom for staging + backup
                raise RuntimeError(f"Insufficient disk space ({free // (1024 * 1024)} MB free)")
        except OSError:
            pass
        self._emit("preflight", {"message": "Preflight checks passed."})

    def _backup_database(self):
        """#3 — snapshot the local database so a bad migration can be rolled back.

        SQLite files are copied; the local PostgreSQL database ("default") is
        pg_dumped (updates/db_snapshot.py). The shared HQ database is never
        snapshotted here: it is migrated under the HQ lock, not per machine.
        A PostgreSQL snapshot that cannot be taken stops the update before
        anything is applied.
        """
        from updates import db_snapshot

        self._db_backups = []
        for alias, cfg in (getattr(settings, "DATABASES", {}) or {}).items():
            engine = cfg.get("ENGINE", "")
            name = cfg.get("NAME", "")
            if engine.endswith("sqlite3") and name and Path(name).exists():
                dest = self.backup_dir / "__db__" / f"{alias}.sqlite3"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(name, dest)
                self._db_backups.append((alias, str(name), str(dest)))
            elif alias == "default" and "postgresql" in engine:
                self._emit("backing_up", {"message": "Snapshotting local database…"})
                dest = db_snapshot.dump(cfg, self.backup_dir / "__db__" / f"{alias}.pgdump")
                self._db_backups.append((alias, name, str(dest)))
        if self._db_backups:
            self._emit("backing_up", {"message": f"Backed up {len(self._db_backups)} database file(s)."})

    def _verify_tree(self, manifest: dict):
        """#10 — re-hash applied files on disk; abort if any didn't land intact."""
        bad = []
        for entry in manifest.get("files", []):
            dest = BASE_DIR / entry["path"]
            want = entry.get("sha256")
            if want and dest.exists() and _sha256(dest) != want:
                bad.append(entry["path"])
        if bad:
            raise ValueError(f"Post-apply verification failed for {len(bad)} file(s): {bad[:5]}")
        self._emit("verifying", {"message": f"Verified {len(manifest.get('files', []))} file(s) on disk."})

    def _health_check(self) -> bool:
        """#2 — run `manage.py check` in a fresh process against the NEW code."""
        manage_py = BASE_DIR / "manage.py"
        if not manage_py.exists():
            logger.warning("manage.py not found — skipping health check")
            return True
        self._emit("verifying", {"message": "Running post-update health check…"})
        try:
            result = subprocess.run(
                [sys.executable, str(manage_py), "check"],
                capture_output=True, text=True, cwd=str(BASE_DIR), timeout=180,
            )
        except subprocess.TimeoutExpired:
            logger.error("Health check timed out")
            return False
        if result.returncode != 0:
            logger.error("Health check FAILED:\n%s", (result.stderr or result.stdout)[:800])
            return False
        return True

    def _rollback(self, reason: str) -> bool:
        """#2 — restore the pre-update state (files + deletions + DB)."""
        if not self.backup_dir.exists() or not any(self.backup_dir.iterdir()):
            return False  # nothing was applied yet — nothing to undo
        self._emit("rolling_back", {"message": f"Rolling back: {reason}"})
        _restore_from_backup(self.backup_dir)  # restores files + deletions + DB snapshots
        SENTINEL_FILE.write_text(f"rollback_{self.version}")
        self._emit("rolled_back", {
            "message": "Rolled back to the previous version. Restart required.",
            "restart_required": True,
        })
        return True

    def _report(self, status: str, error: str = ""):
        """#8 — best-effort report of the outcome to HQ (fleet observability)."""
        cfg = getattr(settings, "UPDATE_SYSTEM", {}) or {}
        url = (cfg.get("server_url") or "").rstrip("/")
        api_key = cfg.get("api_key") or ""
        if not url or not api_key:
            return
        try:
            requests.post(
                f"{url}/api/updates/report/",
                json={"machine_id": self._machine_id(), "version": self.version,
                      "status": status, "error": error[:1000]},
                headers={"X-Api-Key": api_key}, timeout=10,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to report update outcome: %s", exc)

    def _prune_backups(self):
        """#10 — keep only the most recent BACKUP_KEEP backups."""
        try:
            backups = sorted([d for d in UPDATE_BACKUPS.iterdir() if d.is_dir()],
                             key=lambda d: d.stat().st_mtime, reverse=True)
            for old in backups[BACKUP_KEEP:]:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass

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
            timeout=600,  # never let a stuck migration hang the whole update
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
                timeout=600,
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
            timeout=120,
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

def _restore_from_backup(backup_dir: Path):
    """Restore changed files, re-create deleted files, and restore DB snapshots."""
    manifest_path = backup_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}

    # Changed files that were overwritten.
    for entry in manifest.get("files", []):
        src = backup_dir / entry["path"]
        dest = BASE_DIR / entry["path"]
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            _invalidate_bytecode(dest)

    # Files the update had deleted (backed up under __deleted__/).
    deleted_root = backup_dir / "__deleted__"
    if deleted_root.is_dir():
        for src in deleted_root.rglob("*"):
            if src.is_file():
                dest = BASE_DIR / src.relative_to(deleted_root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                _invalidate_bytecode(dest)

    # #3 — DB snapshots taken before migrating, keyed by alias.
    db_root = backup_dir / "__db__"
    if db_root.is_dir():
        from updates import db_snapshot

        for alias, cfg in (getattr(settings, "DATABASES", {}) or {}).items():
            snap = db_root / f"{alias}.sqlite3"
            name = cfg.get("NAME", "")
            if snap.exists() and name:
                try:
                    shutil.copy2(snap, name)
                except OSError as exc:
                    logger.error("DB restore failed for %s: %s", alias, exc)
            pg_snap = db_root / f"{alias}.pgdump"
            if pg_snap.exists():
                try:
                    db_snapshot.restore(cfg, pg_snap)
                except db_snapshot.SnapshotError as exc:
                    logger.error("PostgreSQL restore failed for %s (snapshot kept at %s): %s",
                                 alias, pg_snap, exc)


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
    if not (backup_dir / "manifest.json").exists():
        _emit(q, "error", {"message": "Backup manifest missing"})
        return False

    _emit(q, "rolling_back", {"message": f"Restoring from {backup_dir.name}…"})
    _restore_from_backup(backup_dir)
    _emit(q, "complete", {
        "message": "Rollback complete. Restart required.",
        "restart_required": True,
    })
    SENTINEL_FILE.write_text(f"rollback_{version}")
    return True
