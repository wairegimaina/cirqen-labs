"""This hospital's profile from Cirqen Control: which modules are on, and
what the menu calls them.

Control signs one profile per hospital (admin panel > hospital > Profile).
The sync agent fetches it with the address document, keeps it only if it is
signed by Control (the update key, under its own context), is for this PC's
hospital, and is not older than the copy it has; the app reads the copy, so
it works offline. With no profile every module is on, as before.

Pure module (no Django at import): the agent uses fetch_and_store(), the app
uses load() through core/module_gate.py. The module keys are shared with
hq_server/profiles.py; change both together.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from pathlib import Path

LOG = logging.getLogger(__name__)

CONTEXT = b"cirqen-profile-v1\n"
FILE_NAME = "hospital_profile.json"
TIMEOUT = 15

# key: (default menu label, URL prefixes it owns)
MODULES = {
    "jobcard": ("Work Orders", ("/jobcard/",)),
    "inventory": ("Inventory", ("/Inventory/", "/assets/")),
    "machine_reports": ("Machine Reports", ("/machineReports/",)),
    "reports": ("Report Hub", ("/reports/",)),
    "ppms": ("PPM Schedules", ("/ppms/",)),
    "calibration": ("Calibration", ("/calibration/", "/calSchedules/")),
    "parts_tools": ("Parts & Tools", ("/accessories/",)),
}

_cache: dict = {"path": None, "mtime": None, "fields": None}


def verify(payload: dict, hospital_code: str, control_key) -> tuple[dict | None, str]:
    """(profile fields, "") if this payload can be used by this PC, else (None, why)."""
    code = str(hospital_code or "").strip().upper()
    if control_key is None:
        return None, "no update signing public key on this PC"
    raw, signature = payload.get("document"), payload.get("signature")
    if not isinstance(raw, str) or not signature:
        return None, "profile is not signed"
    try:
        control_key.verify(base64.b64decode(signature), CONTEXT + raw.encode())
        fields = json.loads(raw)
    except Exception:  # noqa: BLE001 - any failure is a refusal
        return None, "profile is not signed by Cirqen Control"
    if fields.get("type") != "cirqen-hospital-profile":
        return None, "not a hospital profile"
    if str(fields.get("hospital") or "").strip().upper() != code:
        return None, f"profile is for hospital {fields.get('hospital')!r}, not {code}"
    return fields, ""


def _path(data_path) -> Path:
    return Path(data_path) / FILE_NAME


def load(data_path) -> dict | None:
    """The stored profile's fields, or None (every module on). Re-read only
    when the file changes."""
    path = _path(data_path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if _cache["path"] == path and _cache["mtime"] == mtime:
        return _cache["fields"]
    try:
        fields = json.loads(json.loads(path.read_text())["document"])
    except (OSError, ValueError, KeyError, TypeError):
        fields = None
    _cache.update(path=path, mtime=mtime, fields=fields)
    return fields


def module_states(fields: dict | None) -> dict:
    """{key: {"on": bool, "label": str}} for every module."""
    modules = (fields or {}).get("modules") or {}
    labels = (fields or {}).get("labels") or {}
    return {key: {"on": modules.get(key, True) is not False,
                  "label": (str(labels.get(key) or "").strip() or default)[:40]}
            for key, (default, _) in MODULES.items()}


def module_for_path(path: str) -> str | None:
    for key, (_, prefixes) in MODULES.items():
        if any(path.startswith(p) for p in prefixes):
            return key
    return None


def fetch_and_store(data_path, update_server_url: str, hospital_code: str, session=None) -> str:
    """One poll: fetch this hospital's profile and keep it if it is valid and
    not older than the stored one. Returns what happened. Never raises."""
    import requests

    code = str(hospital_code or "").strip().upper()
    if not code or not update_server_url:
        return "no hospital code or update server"
    http = session or requests
    try:
        response = http.get(f"{update_server_url.rstrip('/')}/api/profiles/{code}/", timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return f"update server unreachable: {str(exc)[:120]}"
    if response.status_code == 404:
        return "no profile for this hospital"
    if response.status_code != 200:
        return f"update server answered HTTP {response.status_code}"
    try:
        from endpoint_sync import _public_key

        payload = response.json()
        fields, why = verify(payload, code, _public_key())
    except Exception as exc:  # noqa: BLE001
        return f"profile unreadable: {exc}"
    if fields is None:
        LOG.error("Refusing hospital profile: %s", why)
        return why
    current = load(data_path)
    if current and int(current.get("profile_version") or 0) > int(fields.get("profile_version") or 0):
        return "an older profile was offered; keeping the newer one"
    if current and current.get("profile_version") == fields.get("profile_version"):
        return "profile unchanged"
    path = _path(data_path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"document": payload["document"], "signature": payload["signature"],
                               "fetched_at": time.time()}))
    tmp.replace(path)
    LOG.info("🏥 Hospital profile %s v%s saved", code, fields.get("profile_version"))
    return "saved"
