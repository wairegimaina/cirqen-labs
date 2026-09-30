"""Where the fleet should look for the sync HQ — signed, and served from here.

This server's own address is the one thing baked into every client build, and
it never moves. That makes it the right place to answer "where is the sync HQ
now?": a desktop can ask even when the sync HQ it knows about is unreachable,
switched off or already replaced. Moving the sync HQ from one host to another
is then a change to this server's environment, not a release.

The document is built from environment variables and signed on every request,
so it needs no storage of its own and survives a redeploy intact. Signing uses
the same Ed25519 key as update packages (``HQ_SIGNING_PRIVATE_KEY``), which the
client already embeds the public half of.

Environment
-----------
FLEET_SYNC_API_URL     the sync API every desktop should use (required to serve
                       anything; unset means "no opinion", see below)
FLEET_SYNC_FALLBACKS   comma-separated addresses to try if the primary fails
FLEET_POLL_SECONDS     how often clients should re-ask (default 900)
FLEET_MAX_AGE_SECONDS  how long a fetched document stays valid (default 7 days)

FLEET_DEFAULT_HOSPITAL a hospital code (e.g. CH0001): when that hospital is
                       set up in the admin panel with a sync address, this
                       document uses its address and fallbacks instead of the
                       two settings above, so PCs without a hospital code
                       follow the panel too. Until then, the settings above.

With FLEET_SYNC_API_URL unset the endpoint reports ``configured: false`` and
carries no addresses, so a client keeps whatever it already has. That is the
safe default for a server that has not been told anything yet: silence must
never be read as "move to nowhere".

Per hospital
------------
FLEET_HOSPITALS        JSON, one entry per hospital:
                       {"CH0001": {"sync": "https://hq-ch0001.cirqenlabs.com/api/sync",
                                   "fallbacks": ["https://old-hq.example.com/api/sync"],
                                   "updates": "https://updates.cirqenlabs.com"}}
                       served at /api/endpoints/<code>/. Each document names its
                       hospital, and a desktop with a hospital code accepts only
                       its own. A hospital set up in the admin panel
                       (control_store) takes precedence over this setting.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone

DEFAULT_POLL_SECONDS = 900
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _int_env(name: str, default: int) -> int:
    try:
        value = int(_env(name))
    except ValueError:
        return default
    return value if value > 0 else default


def _default_hospital() -> dict | None:
    """FLEET_DEFAULT_HOSPITAL's addresses from the admin panel, when that
    hospital is set up there with a sync address and is not closed. PCs
    without a hospital code (older installs) then follow the panel too, so
    the panel is the one place addresses are edited."""
    code = _env("FLEET_DEFAULT_HOSPITAL").upper()
    if not code:
        return None
    entry = _panel_entry(code)
    if not entry or entry.get("closed") or not str(entry.get("sync") or "").strip():
        return None
    return entry


def build_endpoints() -> dict:
    """The addresses this server currently advertises, or {} if told nothing."""
    endpoints: dict[str, object] = {}

    default = _default_hospital()
    sync_url = (str(default["sync"]) if default else _env("FLEET_SYNC_API_URL")).strip().rstrip("/")
    if sync_url:
        endpoints["sync.api_url"] = sync_url

    # No HQ database addresses: desktops never connect to the HQ database.

    return endpoints


def _fallbacks() -> list:
    default = _default_hospital()
    if default:
        return [str(u).strip().rstrip("/") for u in default.get("fallbacks") or [] if str(u).strip()]
    raw = _env("FLEET_SYNC_FALLBACKS")
    return [part.strip().rstrip("/") for part in raw.split(",") if part.strip()]


def build_document() -> dict:
    """The document clients verify and act on. ``serial`` is a hash of the
    addresses, so it changes exactly when they do and cannot be forgotten."""
    endpoints = build_endpoints()
    fallbacks = _fallbacks()

    material = json.dumps(
        {"endpoints": endpoints, "fallbacks": fallbacks},
        sort_keys=True, separators=(",", ":"),
    )
    serial = hashlib.sha256(material.encode()).hexdigest()[:16]

    return {
        "serial": serial,
        # Refreshed on every request, and covered by the signature, so an old
        # copy replayed by an attacker ages out instead of being adopted.
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "configured": bool(endpoints),
        "endpoints": endpoints,
        "fallbacks": {"sync.api_url": fallbacks} if fallbacks else {},
        "poll_seconds": _int_env("FLEET_POLL_SECONDS", DEFAULT_POLL_SECONDS),
        "max_age_seconds": _int_env("FLEET_MAX_AGE_SECONDS", DEFAULT_MAX_AGE_SECONDS),
    }


HOSPITAL_CODE = re.compile(r"^[A-Z0-9][A-Z0-9-]{1,31}$")


def hospitals() -> dict:
    """FLEET_HOSPITALS parsed: {"CH0001": {"sync": ..., "fallbacks": [...], "updates": ...}}.
    A malformed setting serves nothing rather than something wrong."""
    raw = _env("FLEET_HOSPITALS")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k).strip().upper(): v for k, v in parsed.items()
            if isinstance(v, dict) and HOSPITAL_CODE.match(str(k).strip().upper())}


def _panel_entry(code: str) -> dict | None:
    """The hospital as set in the admin panel (control_store), in the
    FLEET_HOSPITALS shape; None if the panel does not know it."""
    try:
        import control_store

        if not control_store.available():
            return None
        row = control_store.get_hospital(code)
    except Exception:  # noqa: BLE001 - no panel database yet: fall back to the env
        return None
    if row is None:
        return None
    if row["status"] == "closed":
        return {"closed": True}
    return {"sync": row["sync_url"], "fallbacks": row["fallbacks"], "updates": row["updates_url"]}


def build_hospital_document(code: str) -> dict | None:
    """The document for one hospital, from the admin panel or else
    FLEET_HOSPITALS; None for an unknown or closed hospital."""
    code = str(code or "").strip().upper()
    entry = _panel_entry(code)
    if entry is None:
        entry = hospitals().get(code)
    if entry is None or entry.get("closed"):
        return None
    endpoints: dict[str, object] = {}
    sync_url = str(entry.get("sync") or "").strip().rstrip("/")
    if sync_url:
        endpoints["sync.api_url"] = sync_url
    updates_url = str(entry.get("updates") or "").strip().rstrip("/")
    if updates_url:
        endpoints["update.server_url"] = updates_url
    fallbacks = [str(u).strip().rstrip("/") for u in entry.get("fallbacks") or [] if str(u).strip()]
    material = json.dumps({"hospital": code, "endpoints": endpoints, "fallbacks": fallbacks},
                          sort_keys=True, separators=(",", ":"))
    return {
        "hospital": code,
        "serial": hashlib.sha256(material.encode()).hexdigest()[:16],
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "configured": bool(endpoints),
        "endpoints": endpoints,
        "fallbacks": {"sync.api_url": fallbacks} if fallbacks else {},
        "poll_seconds": _int_env("FLEET_POLL_SECONDS", DEFAULT_POLL_SECONDS),
        "max_age_seconds": _int_env("FLEET_MAX_AGE_SECONDS", DEFAULT_MAX_AGE_SECONDS),
    }


def canonical_bytes(document: dict) -> bytes:
    """Exactly what is signed and verified — byte-for-byte on both sides."""
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def signed_response(document: dict | None = None) -> dict:
    """The document plus its signature, ready to return.

    An unsigned response is still served (so an operator can see what the
    server would say) but clients refuse to act on one.
    """
    from build_package import _sign_bytes

    if document is None:
        document = build_document()
    payload = canonical_bytes(document)
    signature = _sign_bytes(payload)

    return {
        "document": payload.decode(),
        "signature": signature or "",
        "signed": bool(signature),
        "algorithm": "ed25519",
    }
