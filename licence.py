"""This hospital's licence from Cirqen Control, and what it means on this PC.

Control signs the hospital's licence (plan, end date, grace days, status)
when it is set or a payment extends it (hq_server/billing.py). The sync agent
fetches it with the address document and keeps it only if Control signed it
(update key, own context), it is for this PC's hospital and it is not older
than the stored copy. The app reads the copy, so the end date, not the
network, decides, and a renewal needs no key typed in.

States (East Africa Time):
  none         no licence published for this hospital: nothing is enforced
  active       more than 30 days left
  due          30 days or fewer left: amber banner
  grace        ended, within the grace days: red banner, everything works
  read_only    after grace, or suspended: viewing, printing and exporting
               work; nothing new can be saved (core/licence_gate.py)

Read-only, never locked out: this is medical-equipment maintenance, and a
lapsed payment must not stop anyone looking up a device, a certificate or a
safety alert.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

LOG = logging.getLogger(__name__)

CONTEXT = b"cirqen-licence-v1\n"   # same as hq_server/billing.py
FILE_NAME = "hospital_licence.json"
TIMEOUT = 15
EAT = timezone(timedelta(hours=3))

_cache: dict = {"path": None, "mtime": None, "fields": None}


def verify(payload: dict, hospital_code: str, control_key) -> tuple[dict | None, str]:
    code = str(hospital_code or "").strip().upper()
    if control_key is None:
        return None, "no update signing public key on this PC"
    raw, signature = payload.get("document"), payload.get("signature")
    if not isinstance(raw, str) or not signature:
        return None, "licence is not signed"
    try:
        control_key.verify(base64.b64decode(signature), CONTEXT + raw.encode())
        fields = json.loads(raw)
    except Exception:  # noqa: BLE001
        return None, "licence is not signed by Cirqen Control"
    if fields.get("type") != "cirqen-licence":
        return None, "not a licence"
    if str(fields.get("hospital") or "").strip().upper() != code:
        return None, f"licence is for hospital {fields.get('hospital')!r}, not {code}"
    return fields, ""


def _path(data_path) -> Path:
    return Path(data_path) / FILE_NAME


def load(data_path) -> dict | None:
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


def state(fields: dict | None, today: date | None = None) -> dict:
    """{"state", "ends_on", "days_left", "read_only_from", "plan", "devices"}."""
    if not fields:
        return {"state": "none"}
    today = today or datetime.now(EAT).date()
    try:
        ends = date.fromisoformat(fields["ends_on"])
        grace = int(fields.get("grace_days") or 0)
    except (KeyError, TypeError, ValueError):
        return {"state": "none"}
    read_only_from = ends + timedelta(days=grace + 1)
    info = {"ends_on": ends, "days_left": (ends - today).days, "read_only_from": read_only_from,
            "plan": fields.get("plan"), "devices": fields.get("devices")}
    if fields.get("status") == "suspended":
        return {**info, "state": "read_only", "suspended": True}
    if today <= ends:
        return {**info, "state": "due" if (ends - today).days < 30 else "active"}
    if today < read_only_from:
        return {**info, "state": "grace"}
    return {**info, "state": "read_only"}


def fetch_and_store(data_path, update_server_url: str, hospital_code: str, session=None) -> str:
    """One poll. Never raises. A newer licence_version replaces the stored one."""
    import requests

    code = str(hospital_code or "").strip().upper()
    if not code or not update_server_url:
        return "no hospital code or update server"
    http = session or requests
    try:
        response = http.get(f"{update_server_url.rstrip('/')}/api/licences/{code}/", timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return f"update server unreachable: {str(exc)[:120]}"
    if response.status_code == 404:
        return "no licence for this hospital"
    if response.status_code != 200:
        return f"update server answered HTTP {response.status_code}"
    try:
        from endpoint_sync import _public_key

        payload = response.json()
        fields, why = verify(payload, code, _public_key())
    except Exception as exc:  # noqa: BLE001
        return f"licence unreadable: {exc}"
    if fields is None:
        LOG.error("Refusing licence: %s", why)
        return why
    current = load(data_path)
    have = int((current or {}).get("licence_version") or 0)
    offered = int(fields.get("licence_version") or 0)
    if current and have > offered:
        return "an older licence was offered; keeping the newer one"
    if current and have == offered:
        return "licence unchanged"
    path = _path(data_path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"document": payload["document"], "signature": payload["signature"],
                               "fetched_at": time.time()}))
    tmp.replace(path)
    LOG.info("🧾 Licence for %s v%s saved (ends %s)", code, offered, fields.get("ends_on"))
    return "saved"
