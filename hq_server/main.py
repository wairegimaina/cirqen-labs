"""
Cirqen HQ Update Server
=======================
Deploy this on Render (separate service from the main Django app).

On every Render deploy (GitHub push):
  1. Startup reads VERSION from version.txt
  2. build_package() scans the repo and builds a new .zip if version changed
  3. The .zip is stored in /packages/ and served to all Cirqen client machines

Endpoints
---------
GET  /health/
     → {"status": "ok", "version": "..."}

GET  /api/updates/latest/?current_version=1.0.0&machine_id=abc
     → {"update_available": true/false, "version": "...", "download_url": "...", ...}

GET  /api/updates/download/{version}/
     → streams the .zip to the client

POST /api/updates/register/
     → client registers itself (machine_id, hostname, version, location)

GET  /api/updates/machines/
     → admin: list all registered machines and their current versions

GET  /api/updates/packages/
     → admin: list all built packages

── Migration lock (prevents race when multiple machines update simultaneously) ──

POST /api/migrations/acquire/?machine_id=X&version=Y
     → {"granted": true}  or  {"granted": false, "locked_by": "..."}

POST /api/migrations/release/?machine_id=X
     → {"released": true}

GET  /api/migrations/status/
     → current lock state
"""

import hashlib
import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from build_package import build_package_if_needed

# ── Config ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent

PACKAGES_DIR = BASE_DIR / "packages"
PACKAGES_DIR.mkdir(exist_ok=True)

VERSION_FILE = BASE_DIR / "version.txt"

# Set HQ_API_KEY in Render → Environment tab.
# generateValue: true in render.yaml auto-creates one on first deploy.
API_KEY = os.environ.get("HQ_API_KEY", "change-this-in-render-env-vars")

# In-memory machine registry (resets on Render restart — acceptable for telemetry)
registered_machines: dict[str, dict] = {}

# ── Migration lock (in-memory, one lock per HQ server process) ───────────────

_migration_lock = threading.Lock()
_migration_status: dict = {
    "locked": False,
    "locked_by": None,
    "locked_at": None,
    "version": None,
}
# Auto-expire the lock if a machine crashes mid-migration and never releases.
# A background thread checks this every 60 s.
LOCK_TIMEOUT_SECONDS = 300   # 5 minutes — more than enough for any migration

# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Cirqen HQ Update Server",
    description="Broadcasts updates to all Cirqen client machines",
    version="1.0.0",
)


# ── Startup: auto-build package on every Render deploy ───────────────────────

@app.on_event("startup")
async def on_startup():
    print("🚀 HQ Server starting — checking if update package needs building...")
    current_version = _read_version()
    print(f"   Current version: {current_version}")

    result = build_package_if_needed(
        version=current_version,
        packages_dir=PACKAGES_DIR,
        repo_root=BASE_DIR.parent,  # one level up = root of your GitHub repo
    )

    if result["built"]:
        print(f"✅ Built update package v{current_version} → {result['path']}")
    else:
        print(f"ℹ️  Package v{current_version} already exists, skipping build.")

    # Start background thread that auto-expires stale migration locks
    t = threading.Thread(target=_lock_watchdog, daemon=True)
    t.start()


# ── Auth ─────────────────────────────────────────────────────────────────────

def _require_api_key(x_api_key: Optional[str] = Header(None)):
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Api-Key header")


# ── Version helpers ───────────────────────────────────────────────────────────

def _read_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return os.environ.get("APP_VERSION", "1.0.0")


def _parse_version(v: str) -> tuple:
    try:
        parts = [int(x) for x in v.strip().split(".")]
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])
    except Exception:
        return (0, 0, 0)


def _get_latest_package() -> Optional[dict]:
    meta_files = sorted(PACKAGES_DIR.glob("*.json"), reverse=True)
    for mf in meta_files:
        try:
            data = json.loads(mf.read_text())
            zip_path = PACKAGES_DIR / data["filename"]
            if zip_path.exists():
                return data
        except Exception:
            continue
    return None


# ── Health ────────────────────────────────────────────────────────────────────

@app.api_route("/health/", methods=["GET", "HEAD"])
def health():
    return {
        "status": "ok",
        "version": _read_version(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "registered_machines": len(registered_machines),
    }

# ── Check for update ──────────────────────────────────────────────────────────

@app.get("/api/updates/latest/")
def check_latest(
    current_version: str = "0.0.0",
    machine_id: Optional[str] = None,
    x_api_key: Optional[str] = Header(None),
):
    """
    Called by every Cirqen client on startup / scheduled check.
    Returns whether a newer version is available and the download URL.
    """
    _require_api_key(x_api_key)

    latest = _get_latest_package()
    if not latest:
        return {"update_available": False, "message": "No packages available yet"}

    latest_version = latest["version"]
    update_available = _parse_version(latest_version) > _parse_version(current_version)

    if machine_id:
        registered_machines.setdefault(machine_id, {})
        registered_machines[machine_id].update({
            "last_check": datetime.now(timezone.utc).isoformat(),
            "current_version": current_version,
        })

    if not update_available:
        return {
            "update_available": False,
            "current_version": current_version,
            "latest_version": latest_version,
        }

    base_url = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000").rstrip("/")

    return {
        "update_available": True,
        "version": latest_version,
        "download_url": f"{base_url}/api/updates/download/{latest_version}/",
        "checksum": latest["checksum"],
        "size_bytes": latest["size_bytes"],
        "changes": latest.get("changes", ""),
        "critical": latest.get("critical", False),
        "min_version": latest.get("min_version", "0.0.0"),
        "file_count": latest.get("file_count", 0),
        "built_at": latest.get("built_at", ""),
    }


# ── Download package ──────────────────────────────────────────────────────────

@app.get("/api/updates/download/{version}/")
def download_package(version: str, x_api_key: Optional[str] = Header(None)):
    """Streams the .zip update package to the requesting client machine."""
    _require_api_key(x_api_key)

    zip_path = PACKAGES_DIR / f"cirqen_update_v{version}.zip"
    if not zip_path.exists():
        raise HTTPException(status_code=404, detail=f"Package v{version} not found")

    return FileResponse(
        path=str(zip_path),
        media_type="application/zip",
        filename=f"cirqen_update_v{version}.zip",
        headers={"X-Version": version},
    )


# ── Machine registration ──────────────────────────────────────────────────────

class MachineInfo(BaseModel):
    machine_id: str
    hostname: str
    current_version: str
    location: Optional[str] = ""


@app.post("/api/updates/register/")
def register_machine(info: MachineInfo, x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)
    registered_machines[info.machine_id] = {
        **info.dict(),
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }
    return {"registered": True, "machine_id": info.machine_id}


@app.get("/api/updates/machines/")
def list_machines(x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)
    return {
        "count": len(registered_machines),
        "machines": list(registered_machines.values()),
        "latest_version": _read_version(),
    }


@app.get("/api/updates/packages/")
def list_packages(x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)
    packages = []
    for mf in sorted(PACKAGES_DIR.glob("*.json"), reverse=True):
        try:
            packages.append(json.loads(mf.read_text()))
        except Exception:
            pass
    return {"count": len(packages), "packages": packages}


# ─────────────────────────────────────────────────────────────────────────────
# Migration lock
# ─────────────────────────────────────────────────────────────────────────────
#
# Problem without this:
#   Machine 1 starts migrating HQ PostgreSQL...
#   Machine 2 also starts migrating HQ PostgreSQL at the same time...
#   Machine 3 also starts migrating HQ PostgreSQL at the same time...
#   → Django advisory lock handles the DB side BUT machines 2 & 3 hang,
#     their update process times out, they report failure — even though
#     nothing is actually wrong.
#
# Solution: one machine acquires the lock, migrates, releases.
# Others poll and wait. After 5 attempts they check if HQ is already
# fully migrated (showmigrations) and skip if it is.
#
# NOTE: This lock is only relevant if you ever add a shared HQ PostgreSQL
# database (--database=hq). For the current SQLite-per-machine setup the
# lock acquire/release is a no-op round-trip that takes ~10ms and causes
# no harm. Wire it in now so it's ready when you add HQ Postgres.
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/migrations/acquire/")
def acquire_migration_lock(
    machine_id: str,
    version: str,
    x_api_key: Optional[str] = Header(None),
):
    """
    Client calls this BEFORE running `manage.py migrate --database=hq`.
    Returns {"granted": true} if the lock was acquired.
    Returns {"granted": false, ...} if another machine holds it.
    The client should retry up to 5 times with 30 s between attempts.
    """
    _require_api_key(x_api_key)

    # Non-blocking attempt — if another thread holds it we return immediately
    if _migration_lock.acquire(blocking=False):
        _migration_status.update({
            "locked": True,
            "locked_by": machine_id,
            "locked_at": datetime.now(timezone.utc).isoformat(),
            "version": version,
        })
        return {
            "granted": True,
            "message": "Lock acquired. You may migrate HQ database.",
        }

    return {
        "granted": False,
        "locked_by": _migration_status["locked_by"],
        "locked_at": _migration_status["locked_at"],
        "version": _migration_status["version"],
        "message": "Another machine is currently migrating. Retry in 30 s.",
    }


@app.post("/api/migrations/release/")
def release_migration_lock(
    machine_id: str,
    x_api_key: Optional[str] = Header(None),
):
    """
    Client calls this AFTER migrate finishes — success or failure.
    Always released in a finally block on the client side.
    """
    _require_api_key(x_api_key)

    if _migration_status["locked_by"] != machine_id:
        raise HTTPException(
            status_code=403,
            detail=f"You ({machine_id}) do not hold the lock "
                   f"(held by {_migration_status['locked_by']})",
        )

    _migration_status.update({
        "locked": False,
        "locked_by": None,
        "locked_at": None,
        "version": None,
    })
    try:
        _migration_lock.release()
    except RuntimeError:
        pass  # already released — safe to ignore

    return {"released": True, "machine_id": machine_id}


@app.get("/api/migrations/status/")
def migration_lock_status(x_api_key: Optional[str] = Header(None)):
    """Check current migration lock state — useful for debugging."""
    _require_api_key(x_api_key)
    return _migration_status


# ── Lock watchdog (auto-expire stale locks) ───────────────────────────────────

def _lock_watchdog():
    """
    Background thread. Checks every 60 s whether the migration lock has been
    held for longer than LOCK_TIMEOUT_SECONDS. If so it force-releases it so
    other machines aren't blocked forever by a crashed client.
    """
    import time
    while True:
        time.sleep(60)
        if not _migration_status["locked"]:
            continue
        locked_at_str = _migration_status.get("locked_at")
        if not locked_at_str:
            continue
        try:
            locked_at = datetime.fromisoformat(locked_at_str)
            # Make locked_at offset-aware if it isn't
            if locked_at.tzinfo is None:
                locked_at = locked_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - locked_at).total_seconds()
            if age > LOCK_TIMEOUT_SECONDS:
                stale_holder = _migration_status["locked_by"]
                _migration_status.update({
                    "locked": False,
                    "locked_by": None,
                    "locked_at": None,
                    "version": None,
                })
                try:
                    _migration_lock.release()
                except RuntimeError:
                    pass
                print(
                    f"⚠️  Migration lock held by {stale_holder} for {age:.0f}s "
                    f"(>{LOCK_TIMEOUT_SECONDS}s) — force-released."
                )
        except Exception as exc:
            print(f"Lock watchdog error: {exc}")
