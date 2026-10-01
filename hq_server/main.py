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

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

import endpoints as fleet_endpoints
import releases
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

app = FastAPI(title="Cirqen HQ Update Server", version="1.0.0", docs_url=None, redoc_url=None,
              openapi_url=None)

# The admin panel (/admin): hospitals, HQ identities, admins, audit log.
import admin_panel  # noqa: E402

admin_panel.install(app, versions=lambda: [m["version"] for m in sorted(
    _all_metas(), key=lambda d: _parse_version(d.get("version", "0.0.0")), reverse=True) if not m.get("yanked")])


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


@app.get("/api/endpoints/{hospital}/")
def hospital_endpoint_document(hospital: str):
    """The same signed document for one hospital (FLEET_HOSPITALS). Unknown
    hospitals get 404, never the fleet document: a desktop with a hospital
    code must not be moved by anything that is not addressed to it."""
    document = fleet_endpoints.build_hospital_document(hospital)
    if document is None:
        raise HTTPException(status_code=404, detail="unknown hospital")
    return fleet_endpoints.signed_response(document)


@app.get("/api/profiles/{hospital}/")
def hospital_profile_document(hospital: str):
    """The hospital's signed profile (modules and menu labels, profiles.py).
    404 until one is published, or for a closed hospital; a PC then keeps
    every module on (or its last profile)."""
    import control_store
    import hq_certificates
    import profiles

    code = hospital.strip().upper()
    row = control_store.get_hospital(code) if control_store.available() else None
    if row is None or row["status"] == "closed" or not row["profile_version"]:
        raise HTTPException(status_code=404, detail="no profile")
    try:
        key = hq_certificates._signing_key(None)
    except SystemExit:
        raise HTTPException(status_code=503, detail="no signing key") from None
    return profiles.signed(code, row["profile"], row["profile_version"], key)


@app.get("/api/licences/{hospital}/")
def hospital_licence_document(hospital: str):
    """The hospital's signed licence (billing.py): plan, end date, grace,
    status. 404 until the hospital has a licence; its PCs then enforce
    nothing, as before."""
    import billing
    import control_store
    import hq_certificates

    code = hospital.strip().upper()
    lic = billing.get_licence(code) if control_store.available() else None
    if lic is None:
        raise HTTPException(status_code=404, detail="no licence")
    try:
        key = hq_certificates._signing_key(None)
    except SystemExit:
        raise HTTPException(status_code=503, detail="no signing key") from None
    return billing.signed_licence(lic, key)


@app.get("/api/hq/{hospital}/enrollment/{token_id}/status")
def enrollment_status(hospital: str, token_id: str):
    """Asked by a hospital's HQ before an installer file enrolls a PC: has it
    been revoked in the admin panel? Signed, and names the hospital and
    token, so the HQ can trust it (hospital_identity.check_enrollment_not_revoked)."""
    import base64
    import json as _json

    import control_store
    import hq_certificates

    code = hospital.strip().upper()
    row = control_store.enrollment_token(token_id) if control_store.available() else None
    if row is None or row["hospital"] != code:
        raise HTTPException(status_code=404, detail="unknown installer")
    try:
        key = hq_certificates._signing_key(None)
    except SystemExit:
        raise HTTPException(status_code=503, detail="no signing key") from None
    document = _json.dumps({"type": "cirqen-enrollment-status", "hospital": code, "token_id": token_id,
                            "revoked": bool(row.get("revoked_at")),
                            "issued_at": datetime.now(timezone.utc).isoformat()},
                           sort_keys=True, separators=(",", ":"))
    signature = base64.b64encode(key.sign(b"cirqen-enrollment-status-v1\n" + document.encode())).decode()
    return {"document": document, "signature": signature}


@app.get("/api/hq/{hospital}/settings")
def hq_settings(hospital: str):
    """Settings a hospital's HQ takes from the admin panel instead of from its
    Render environment. Signed and naming the hospital, so the HQ can trust it
    (hospital_identity.fetch_hq_settings on the HQ)."""
    import base64
    import json as _json

    import control_store
    import hq_certificates

    code = hospital.strip().upper()
    h = control_store.get_hospital(code) if control_store.available() else None
    if h is None:
        raise HTTPException(status_code=404, detail="unknown hospital")
    try:
        key = hq_certificates._signing_key(None)
    except SystemExit:
        raise HTTPException(status_code=503, detail="no signing key") from None
    document = _json.dumps({"type": "cirqen-hq-settings", "hospital": code,
                            "shared_sync_key": "retired" if h.get("shared_key_retired_at") else "accepted",
                            "issued_at": datetime.now(timezone.utc).isoformat()},
                           sort_keys=True, separators=(",", ":"))
    signature = base64.b64encode(key.sign(b"cirqen-hq-settings-v1\n" + document.encode())).decode()
    return {"document": document, "signature": signature}


# ── Self-hosted HQs: their software, through Control (hq_releases.py) ────────

@app.get("/api/hq/{hospital}/hq-release")
def hq_release(hospital: str):
    """Which HQ version this hospital's own server should run. Signed; an
    empty version means hold (or none published yet)."""
    import control_store
    import hq_certificates
    import hq_releases

    code = hospital.strip().upper()
    h = control_store.get_hospital(code) if control_store.available() else None
    if h is None:
        raise HTTPException(status_code=404, detail="unknown hospital")
    try:
        key = hq_certificates._signing_key(None)
    except SystemExit:
        raise HTTPException(status_code=503, detail="no signing key") from None
    try:
        version = hq_releases.chosen_version(h)
        digest = hq_releases.sha256(hq_releases.archive(version)) if version else ""
    except hq_releases.ReleaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return hq_releases.signed_release(code, version, digest, key)


@app.get("/api/hq/{hospital}/hq-release/{version}.tar.gz")
def hq_release_download(hospital: str, version: str, request: Request):
    """The release archive, only for this hospital's own HQ (signed request)
    and only the version the panel gives it."""
    import control_store
    import hq_releases

    code = hospital.strip().upper()
    h = control_store.get_hospital(code) if control_store.available() else None
    if h is None:
        raise HTTPException(status_code=404, detail="unknown hospital")
    cert = control_store.latest_certificate(code)
    why = hq_releases.check_download_signature(code, version, request.headers.get("x-cirqen-timestamp"),
                                               request.headers.get("x-cirqen-signature"),
                                               cert["public_key"] if cert else None)
    if why:
        raise HTTPException(status_code=403, detail=why)
    try:
        if version != hq_releases.chosen_version(h):
            raise HTTPException(status_code=404, detail="not this hospital's version")
        path = hq_releases.archive(version)
    except hq_releases.ReleaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return FileResponse(path, media_type="application/gzip", filename=f"hq_server-{version}.tar.gz")


SELFHOST_FILES = {"install.sh": "text/x-shellscript", "cirqen_hq_update.py": "text/x-python"}


@app.get("/api/hq/selfhost/{name}")
def hq_selfhost_file(name: str):
    """The self-hosted HQ installer and updater, from the newest HQ release
    (hq_server selfhost/; no secrets in them)."""
    import hq_releases

    if name not in SELFHOST_FILES:
        raise HTTPException(status_code=404, detail="no such file")
    try:
        available = hq_releases.tags()
        if not available:
            raise HTTPException(status_code=404, detail="no HQ release yet")
        with hq_releases._client() as client:
            resp = client.get(f"/repos/{hq_releases.repo()}/contents/selfhost/{name}",
                              params={"ref": available[0]}, headers={"Accept": "application/vnd.github.raw"})
    except hq_releases.ReleaseError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"GitHub answered {resp.status_code}")
    return Response(resp.content, media_type=SELFHOST_FILES[name])


# ── M-Pesa (Daraja C2B): payments arriving by themselves (mpesa.py) ───────────

def _caller_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return (forwarded.split(",")[-1].strip() if forwarded else "") or (request.client.host if request.client else "")


@app.post("/api/pay/mpesa/confirm/{secret}")
async def mpesa_confirmation(secret: str, request: Request):
    """Safaricom's confirmation of a Paybill payment. Stored once per receipt
    number, then matched to an invoice or left for finance. Always answers
    'accepted' to a genuine call, so Safaricom does not retry forever."""
    import mpesa

    if not mpesa.secret_ok(secret):
        raise HTTPException(status_code=404)
    if not mpesa.ip_ok(_caller_ip(request)):
        raise HTTPException(status_code=403)
    try:
        payload = await request.json()
        row = mpesa.receive(payload if isinstance(payload, dict) else {})
        print(f"💰 M-Pesa {row['trans_id']} KES {row['amount_kes']} account {row['bill_ref']}: {row['status']}")
    except Exception as exc:  # noqa: BLE001 - never make Safaricom retry a payment we cannot read
        print(f"⚠️  M-Pesa confirmation not stored: {exc}")
    return {"ResultCode": 0, "ResultDesc": "Accepted"}


@app.post("/api/pay/mpesa/validate/{secret}")
async def mpesa_validation(secret: str, request: Request):
    """Asked before a payment completes (only if Safaricom enabled external
    validation for the Paybill). Accepts, unless
    MPESA_REJECT_UNKNOWN_ACCOUNTS=true and the account is not a hospital code
    or open invoice."""
    import mpesa

    if not mpesa.secret_ok(secret):
        raise HTTPException(status_code=404)
    if not mpesa.ip_ok(_caller_ip(request)):
        raise HTTPException(status_code=403)
    if mpesa.env("MPESA_REJECT_UNKNOWN_ACCOUNTS").lower() == "true":
        try:
            payload = await request.json()
            ref = str((payload or {}).get("BillRefNumber") or "").strip().upper()
            hospital, _, _ = mpesa._target(ref)
            if hospital is None and not (ref and mpesa.cs.get_hospital(ref)):
                return {"ResultCode": "C2B00012", "ResultDesc": "Rejected"}
        except Exception:  # noqa: BLE001
            pass
    return {"ResultCode": "0", "ResultDesc": "Accepted"}


@app.get("/api/updates/latest/")
def check_latest(current_version: str = "0.0.0", machine_id: Optional[str] = None,
                 hospital_code: Optional[str] = None, runtime_id: Optional[str] = None,
                 platform: str = "linux", x_api_key: Optional[str] = Header(None)):
    _require_api_key(x_api_key)

    if machine_id:
        store.touch_check(machine_id, current_version)

    # Each hospital follows the newest release, is pinned to one, or is on
    # hold (admin panel; releases.py). PCs without a hospital code follow.
    latest, why = releases.choose(_all_metas(), current_version, releases.policy_for(hospital_code))
    if latest is None:
        newest = _get_latest_package()
        return {"update_available": False, "current_version": current_version,
                "latest_version": newest["version"] if newest else None,
                **({"message": why} if why else {})}

    latest_version = latest["version"]

    # #7 — kill switch / staged rollout / minimum-version gating
    if latest.get("yanked"):
        return {"update_available": False, "message": "Latest release is withheld"}
    if _parse_version(current_version) < _parse_version(latest.get("min_version", "0.0.0")):
        return {"update_available": False, "message": "Client too old for direct update",
                "min_version": latest.get("min_version")}
    if not _in_rollout(machine_id, int(latest.get("rollout_percent", 100))):
        return {"update_available": False, "message": "Not yet in rollout window"}

    base_url = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:8000").rstrip("/")

    # A PC on another runtime (Python, libraries, embedded databases) can't
    # run this version's code on what it has: offer the full app instead
    # (desktop_releases.py). PCs that don't say their runtime get code updates.
    needed = latest.get("runtime_id") or ""
    if runtime_id and needed and runtime_id != needed:
        return _full_app_offer(latest, platform, base_url)

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


def _full_app_offer(latest: dict, platform: str, base_url: str) -> dict:
    import build_package
    import desktop_releases
    import hq_releases

    version = latest["version"]
    common = {"update_available": True, "version": version, "full_required": True,
              "changes": latest.get("changes", ""), "critical": latest.get("critical", False)}
    try:
        package = desktop_releases.full_package(version, platform)
    except hq_releases.ReleaseError as exc:
        return {**common, "full_package": None, "message": f"The full app can't be checked right now: {exc}"}
    if package is None or package["runtime_id"] != latest.get("runtime_id"):
        return {**common, "full_package": None,
                "message": f"Cirqen {version} needs the full app, which isn't published yet."}
    offer = desktop_releases.signed_offer(package, f"{base_url}/api/updates/full/{version}/{platform}/",
                                          build_package._sign_bytes)
    if not offer["signature"]:
        return {**common, "full_package": None, "message": "Control has no update signing key."}
    return {**common, "full_package": offer}


@app.get("/api/updates/full/{version}/{platform}/")
def download_full_app(version: str, platform: str, x_api_key: Optional[str] = Header(None)):
    """The full app (desktop_releases.py), streamed from its GitHub release."""
    from fastapi.responses import StreamingResponse

    import desktop_releases
    import hq_releases

    _require_api_key(x_api_key)
    try:
        chunks, size, name = desktop_releases.stream(version, platform)
    except hq_releases.ReleaseError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return StreamingResponse(chunks, media_type="application/octet-stream",
                             headers={"Content-Length": str(size),
                                      "Content-Disposition": f'attachment; filename="{name}"'})


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
