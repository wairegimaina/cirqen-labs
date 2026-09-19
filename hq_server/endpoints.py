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
FLEET_HQ_DB_HOST/_PORT/_DATABASE/_USER/_SSLMODE   optional; only set these when
                       the HQ database moves. Never a password.
FLEET_POLL_SECONDS     how often clients should re-ask (default 900)
FLEET_MAX_AGE_SECONDS  how long a fetched document stays valid (default 7 days)

With FLEET_SYNC_API_URL unset the endpoint reports ``configured: false`` and
carries no addresses, so a client keeps whatever it already has. That is the
safe default for a server that has not been told anything yet: silence must
never be read as "move to nowhere".
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

DEFAULT_POLL_SECONDS = 900
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600

# Only these may be carried. A password is deliberately not in the list: the
# document is served to every desktop and is not a secret channel.
DB_FIELDS = {
    "FLEET_HQ_DB_HOST": "hq_db.host",
    "FLEET_HQ_DB_PORT": "hq_db.port",
    "FLEET_HQ_DB_DATABASE": "hq_db.database",
    "FLEET_HQ_DB_USER": "hq_db.user",
    "FLEET_HQ_DB_SSLMODE": "hq_db.sslmode",
}


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _int_env(name: str, default: int) -> int:
    try:
        value = int(_env(name))
    except ValueError:
        return default
    return value if value > 0 else default


def build_endpoints() -> dict:
    """The addresses this server currently advertises, or {} if told nothing."""
    endpoints: dict[str, object] = {}

    sync_url = _env("FLEET_SYNC_API_URL").rstrip("/")
    if sync_url:
        endpoints["sync.api_url"] = sync_url

    for env_name, key in DB_FIELDS.items():
        raw = _env(env_name)
        if not raw:
            continue
        if key == "hq_db.port":
            try:
                endpoints[key] = int(raw)
            except ValueError:
                continue  # a malformed port is dropped, not advertised
        else:
            endpoints[key] = raw

    return endpoints


def _fallbacks() -> list:
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


def canonical_bytes(document: dict) -> bytes:
    """Exactly what is signed and verified — byte-for-byte on both sides."""
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode()


def signed_response() -> dict:
    """The document plus its signature, ready to return.

    An unsigned response is still served (so an operator can see what the
    server would say) but clients refuse to act on one.
    """
    from build_package import _sign_bytes

    document = build_document()
    payload = canonical_bytes(document)
    signature = _sign_bytes(payload)

    return {
        "document": payload.decode(),
        "signature": signature or "",
        "signed": bool(signature),
        "algorithm": "ed25519",
    }
