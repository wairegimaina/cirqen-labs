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

The date used is not simply this PC's clock (licence_clock.json): it never
goes back past the latest time this PC has seen, and Control's signed time
(issued_at, on every licence fetch) resets it, so turning the clock back
doesn't undo an ended licence, and a clock that ran ahead heals once online.
A PC that had a licence and lost the file is read-only until it fetches the
licence again.
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

CLOCK_FILE = "licence_clock.json"
CLOCK_STEP = timedelta(hours=1)   # the clock file is written at most this often

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


def _parse_time(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _read_clock(data_path) -> dict:
    try:
        data = json.loads((Path(data_path) / CLOCK_FILE).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_clock(data_path, data: dict) -> None:
    path = Path(data_path) / CLOCK_FILE
    try:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    except OSError as exc:
        LOG.warning("Could not write %s: %s", path, exc)


def note_control_time(data_path, issued_at) -> None:
    """Control's signed time from a licence it just sent: the trusted clock
    moves to it, back as well as forward (a PC clock that ran ahead heals)."""
    moment = _parse_time(issued_at)
    if moment is None:
        return
    clock = _read_clock(data_path)
    clock.update(latest=moment.isoformat(), control_at=moment.isoformat(), seen=True)
    _write_clock(data_path, clock)


def trusted_now(data_path, now: datetime | None = None) -> datetime:
    """This PC's time, but never earlier than the latest time it has seen."""
    now = now or datetime.now(timezone.utc)
    clock = _read_clock(data_path)
    latest = _parse_time(clock.get("latest"))
    if latest is None or now >= latest + CLOCK_STEP:
        clock["latest"] = now.isoformat()
        _write_clock(data_path, clock)
        return now
    return max(now, latest)


def current(data_path, now: datetime | None = None) -> dict:
    """state() for this PC, on the trusted date."""
    fields = load(data_path)
    if fields is None:
        if _read_clock(data_path).get("seen"):
            return {"state": "read_only", "missing": True}
        return state(None)
    return state(fields, today=trusted_now(data_path, now).astimezone(EAT).date())


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
        clock = _read_clock(data_path)
        if clock.get("seen") and load(data_path) is None:
            _write_clock(data_path, {**clock, "seen": False})   # Control says there is none any more
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
    note_control_time(data_path, fields.get("issued_at"))
    stored = load(data_path)
    have = int((stored or {}).get("licence_version") or 0)
    offered = int(fields.get("licence_version") or 0)
    if stored and have > offered:
        return "an older licence was offered; keeping the newer one"
    if stored and have == offered:
        return "licence unchanged"
    path = _path(data_path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"document": payload["document"], "signature": payload["signature"],
                               "fetched_at": time.time()}))
    tmp.replace(path)
    clock = _read_clock(data_path)
    if not clock.get("seen"):
        _write_clock(data_path, {**clock, "seen": True})
    LOG.info("🧾 Licence for %s v%s saved (ends %s)", code, offered, fields.get("ends_on"))
    return "saved"
