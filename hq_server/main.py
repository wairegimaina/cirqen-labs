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
GET  /api/updates/latest/?current_version=1.0.0&machine_id=abc
     → {"update_available": ..., "download_url": ..., "signed": ..., "tree_hash": ...}
GET  /api/updates/download/{version}/?from_version=1.0.0
     → streams the .zip (a slim delta if from_version is given and differs)
POST /api/updates/register/            → client registers itself
POST /api/updates/report/             → client reports an update outcome  (#8)
GET  /api/updates/machines/            → admin: registered machines + versions
GET  /api/updates/packages/            → admin: built packages
POST /api/migrations/acquire|release/  → shared-DB migration lock  (#9, persistent)
GET  /api/migrations/status/
GET  /api/endpoints/                   → signed "where is the sync HQ" document

This server's address never changes, which is why the fleet asks *it* where the
sync HQ is (see endpoints.py). Moving the sync HQ is then an environment change
here, not a client release.

Rollout controls (#7): edit a package's cirqen_update_v<ver>.json to set
"yanked": true (kill switch), "rollout_percent": 0-100 (canary), or
"min_version": "x.y.z" (block too-old clients from jumping directly).
"""
import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import endpoints as fleet_endpoints
import store
from build_package import build_package_if_needed, build_delta_zip

# ── Config ───────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
# Built packages belong on the persistent disk, not inside the deployed code.
# A redeploy wipes the code directory, and with it every PREVIOUS version's
# package and manifest. build_delta_zip needs the old manifest to work out what
# changed, so without them every desktop falls back to a full download — at 500
# desktops that is the difference between a few MB each and the whole package
# each. HQ_PACKAGES_DIR points at the Render disk (see render.yaml).
PACKAGES_DIR = Path(os.environ.get("HQ_PACKAGES_DIR", BASE_DIR / "packages"))
PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
VERSION_FILE = BASE_DIR / "version.txt"

API_KEY = os.environ.get("HQ_API_KEY", "change-this-in-render-env-vars")
LOCK_TIMEOUT_SECONDS = 300  # stale migration locks are auto-stealable after this

app = FastAPI(title="Cirqen HQ Update Server", version="1.0.0")


@app.on_event("startup")
async def on_startup():
    store.init()
    current_version = _read_version()
    print(f"🚀 HQ Server starting — version {current_version}")
    result = build_package_if_needed(
        version=current_version, packages_dir=PACKAGES_DIR, repo_root=BASE_DIR.parent
    )
    print(f"   package: {'built' if result['built'] else 'exists'} → {result['path']}")


# ── Auth ─────────────────────────────────────────────────────────────────────

def _require_api_key(x_api_key: Optional[str]):
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-Api-Key header")


# ── Version helpers ───────────────────────────────────────────────────────────

def _read_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return os.environ.get("APP_VERSION", "1.0.0")


def _parse_version(v: str) -> tuple:
    try:
        parts = [int(x) for x in str(v).strip().split(".")]
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])
    except Exception:
        return (0, 0, 0)


def _all_metas() -> list[dict]:
    metas = []
    for mf in PACKAGES_DIR.glob("cirqen_update_v*.json"):
        try:
            metas.append(json.loads(mf.read_text()))
        except Exception:
            continue
    return metas


def _get_latest_package() -> Optional[dict]:
    """#4 — highest package by NUMERIC version (not lexical), skipping yanked."""
    best, best_ver = None, (-1, -1, -1)
    for data in _all_metas():
        if data.get("yanked"):
            continue
        if not (PACKAGES_DIR / data.get("filename", "")).exists():
            continue
        ver = _parse_version(data.get("version", "0.0.0"))
        if ver > best_ver:
            best, best_ver = data, ver
    return best


def _meta_for(version: str) -> Optional[dict]:
    mf = PACKAGES_DIR / f"cirqen_update_v{version}.json"
    if mf.exists():
        try:
            return json.loads(mf.read_text())
        except Exception:
            return None
    return None


def _in_rollout(machine_id: Optional[str], percent: int) -> bool:
    """#7 — deterministic per-machine bucketing for staged rollout."""
    if percent >= 100:
        return True
    if percent <= 0:
        return False
    if not machine_id:
        return True  # can't bucket an anonymous client — don't hold it back
    bucket = int(hashlib.sha256(machine_id.encode()).hexdigest(), 16) % 100
    return bucket < percent


# ── Health ────────────────────────────────────────────────────────────────────

@app.api_route("/health/", methods=["GET", "HEAD"])
def health():
    return {"status": "ok", "version": _read_version(),
            "timestamp": datetime.now(timezone.utc).isoformat()}


# ── Check for update ──────────────────────────────────────────────────────────

# ── Fleet endpoints (where is the sync HQ?) ──────────────────────────────────

@app.get("/api/endpoints/")
def fleet_endpoint_document():
    """Signed document naming the sync HQ the fleet should use.

    Deliberately unauthenticated. A desktop whose sync key has been lost or
    rejected is exactly the one that most needs to find out where HQ moved to,
    and the document carries no secrets — only addresses, which are public the
    moment any client connects. Integrity comes from the signature, not from
    who is asking.
    """
    return fleet_endpoints.signed_response()


@app.get("/api/updates/latest/")
def check_latest(current_version: str = "0.0.0", machine_id: Optional[str] = None,
                 x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)

    if machine_id:
        store.touch_check(machine_id, current_version)

    latest = _get_latest_package()
    if not latest:
        return {"update_available": False, "message": "No packages available yet"}

    latest_version = latest["version"]
    if _parse_version(latest_version) <= _parse_version(current_version):
        return {"update_available": False, "current_version": current_version,
                "latest_version": latest_version}

    # #7 — kill switch / staged rollout / minimum-version gating
    if latest.get("yanked"):
        return {"update_available": False, "message": "Latest release is withheld"}
    if _parse_version(current_version) < _parse_version(latest.get("min_version", "0.0.0")):
        return {"update_available": False, "message": "Client too old for direct update",
                "min_version": latest.get("min_version")}
    if not _in_rollout(machine_id, int(latest.get("rollout_percent", 100))):
        return {"update_available": False, "message": "Not yet in rollout window"}

    base_url = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000").rstrip("/")
    dl = f"{base_url}/api/updates/download/{latest_version}/"
    if current_version and current_version != "0.0.0":
        dl += f"?from_version={current_version}"  # #6 request a delta

    return {
        "update_available": True,
        "version": latest_version,
        "download_url": dl,
        "checksum": latest["checksum"],            # full-package checksum (delta differs; client re-hashes)
        "size_bytes": latest["size_bytes"],
        "changes": latest.get("changes", ""),
        "critical": latest.get("critical", False),
        "signed": latest.get("signed", False),     # #1
        "tree_hash": latest.get("tree_hash", ""),  # #10
        "built_at": latest.get("built_at", ""),
    }


# ── Download package (full or delta) ──────────────────────────────────────────

@app.get("/api/updates/download/{version}/")
def download_package(version: str, from_version: Optional[str] = None,
                     x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)

    full_zip = PACKAGES_DIR / f"cirqen_update_v{version}.zip"
    if not full_zip.exists():
        raise HTTPException(status_code=404, detail=f"Package v{version} not found")

    # #6 — serve a slim delta when the client tells us where it's coming from.
    if from_version and from_version != version:
        to_meta, from_meta = _meta_for(version), _meta_for(from_version)
        if to_meta and from_meta:
            delta_zip = PACKAGES_DIR / f"cirqen_update_v{version}_from_{from_version}.zip"
            if not delta_zip.exists():
                try:
                    build_delta_zip(full_zip, from_meta["manifest"], to_meta["manifest"], delta_zip)
                except Exception as exc:  # noqa: BLE001
                    print(f"delta build failed ({exc}); serving full package")
                    delta_zip = full_zip
            return FileResponse(str(delta_zip), media_type="application/zip",
                                filename=delta_zip.name, headers={"X-Version": version,
                                                                  "X-Delta-From": from_version})

    return FileResponse(str(full_zip), media_type="application/zip",
                        filename=full_zip.name, headers={"X-Version": version})


# ── Registration + outcome reporting ──────────────────────────────────────────

class MachineInfo(BaseModel):
    machine_id: str
    hostname: str
    current_version: str
    location: Optional[str] = ""


@app.post("/api/updates/register/")
def register_machine(info: MachineInfo, x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)
    store.register_machine(info.machine_id, info.hostname, info.current_version, info.location or "")
    return {"registered": True, "machine_id": info.machine_id}


class UpdateReport(BaseModel):
    machine_id: str
    version: str
    status: str            # "success" | "failed" | "rolled_back"
    error: Optional[str] = ""


@app.post("/api/updates/report/")
def report_outcome(report: UpdateReport, x_api_key: Optional[str] = Header(None)):
    """#8 — client reports the result of an update so the fleet is observable."""
    _require_api_key(x_api_key)
    store.record_report(report.machine_id, report.version, report.status, report.error or "")
    return {"recorded": True}


# Back-compat alias used by updates/sync_hook.py
@app.post("/api/updates/checkin/")
def checkin(info: MachineInfo, x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)
    store.touch_check(info.machine_id, info.current_version)
    return {"ok": True}


@app.get("/api/updates/machines/")
def list_machines(x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)
    machines = store.list_machines()
    return {"count": len(machines), "machines": machines, "latest_version": _read_version()}


@app.get("/api/updates/packages/")
def list_packages(x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)
    return {"count": len(_all_metas()), "packages": sorted(
        _all_metas(), key=lambda d: _parse_version(d.get("version", "0.0.0")), reverse=True)}


# ── Migration lock (#9 — persistent + atomic across processes) ─────────────────

@app.post("/api/migrations/acquire/")
def acquire_migration_lock(machine_id: str, version: str, x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)
    if store.acquire_lock(machine_id, version, LOCK_TIMEOUT_SECONDS):
        return {"granted": True, "message": "Lock acquired. You may migrate HQ database."}
    st = store.lock_status()
    return {"granted": False, "locked_by": st["locked_by"], "locked_at": st["locked_at"],
            "message": "Another machine is currently migrating. Retry in 30 s."}


@app.post("/api/migrations/release/")
def release_migration_lock(machine_id: str, x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)
    if not store.release_lock(machine_id):
        st = store.lock_status()
        raise HTTPException(status_code=403,
                            detail=f"You ({machine_id}) do not hold the lock (held by {st['locked_by']})")
    return {"released": True, "machine_id": machine_id}


@app.get("/api/migrations/status/")
def migration_lock_status(x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)
    return store.lock_status()
