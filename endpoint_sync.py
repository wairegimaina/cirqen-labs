"""Ask the update server where the sync HQ is, and follow it if it has moved.

The update server's address is the one thing baked into a build and it never
changes, so it is the only thing a desktop can always reach. That makes it the
right place to learn that the sync HQ has moved to a new host — including when
the old host is already gone, which is exactly when a client-to-old-host
redirect would be useless.

    update server  /api/endpoints/  ->  signed document  ->  verify  ->  probe
                                                                          |
                          endpoints.json  <- adopt -----------------------+
                                                                          |
                          keep what we had  <- reject --------------------+

Four things have to be true before an address is adopted, because getting this
wrong strands a machine where no one can reach it:

1. the document carries a valid Ed25519 signature from the update server's key
   (the same key that signs update packages);
2. it is recent, so an old document cannot be replayed to drag the fleet back;
3. the address passes the same validation the settings page applies;
4. the address actually answers ``/health``.

After adoption, ``note_health`` watches the new address. If it fails
consistently for long enough, the previous address is restored automatically —
a bad move undoes itself rather than needing someone on site.

No Django import: this runs inside the sync agent as well.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import config as cirqen_config
from config import (
    HQ_ENDPOINT_DEFAULTS,
    REMOTE_ENDPOINTS_FILE,
    coerce_endpoint,
    validate_endpoints,
)

LOG = logging.getLogger("endpoint_sync")

FETCH_TIMEOUT = 15
PROBE_TIMEOUT = 10
# Consecutive failures, and elapsed seconds, before a freshly adopted address is
# abandoned. Both must be exceeded: a brief outage should not undo a good move.
REVERT_AFTER_FAILURES = 5
REVERT_AFTER_SECONDS = 600


def _state_path(data_path) -> Path:
    return Path(data_path) / REMOTE_ENDPOINTS_FILE


def read_state(data_path) -> dict:
    try:
        with open(_state_path(data_path)) as fh:
            payload = json.load(fh)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(data_path, state: dict) -> None:
    path = _state_path(data_path)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)  # atomic: a torn file would silently unconfigure a machine
    try:
        path.chmod(0o600)
    except OSError:
        pass


# ── Verification ─────────────────────────────────────────────────────────────

def _public_key():
    """The update server's signing key, from Django settings when available,
    else the constant embedded in the updater."""
    raw = ""
    try:
        from django.conf import settings

        raw = (getattr(settings, "UPDATE_SYSTEM", {}) or {}).get("public_key") or ""
    except Exception:  # noqa: BLE001 - Django may not be configured here
        pass
    if not raw:
        raw = os.environ.get("UPDATE_PUBLIC_KEY", "").strip()
    if not raw:
        try:
            from updates.updater import UPDATE_PUBLIC_KEY

            raw = (UPDATE_PUBLIC_KEY or "").strip()
        except Exception:  # noqa: BLE001
            raw = ""
    if not raw:
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        return Ed25519PublicKey.from_public_bytes(base64.b64decode(raw))
    except Exception as exc:  # noqa: BLE001
        LOG.error("Update signing public key is set but unusable: %s", exc)
        return None


def verify_document(payload: dict) -> tuple[dict | None, str]:
    """Return (document, "") when the response can be trusted, else (None, why)."""
    raw = payload.get("document")
    signature = payload.get("signature") or ""
    if not isinstance(raw, str) or not raw:
        return None, "response carried no document"

    key = _public_key()
    if key is None:
        # Refuse rather than trust. An unverified redirect can point the whole
        # fleet anywhere; staying put is always the safer failure.
        return None, "no update signing public key is configured on this machine"
    if not signature:
        return None, "document is unsigned"
    try:
        key.verify(base64.b64decode(signature), raw.encode())
    except Exception:  # noqa: BLE001 - any failure is a refusal
        return None, "signature does not match the update server's key"

    try:
        document = json.loads(raw)
    except ValueError:
        return None, "document is not valid JSON"
    if not isinstance(document, dict):
        return None, "document is not an object"

    issued = document.get("issued_at")
    max_age = document.get("max_age_seconds") or 7 * 24 * 3600
    try:
        issued_at = datetime.fromisoformat(str(issued))
        if issued_at.tzinfo is None:
            issued_at = issued_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None, "document has no usable issued_at"
    age = (datetime.now(timezone.utc) - issued_at).total_seconds()
    if age > float(max_age):
        return None, f"document is stale ({int(age)}s old); possible replay"
    if age < -300:
        return None, "document is dated in the future"

    return document, ""


def usable_endpoints(document: dict) -> tuple[dict, list[str]]:
    """The addresses in a verified document that are well formed."""
    advertised = document.get("endpoints")
    if not isinstance(advertised, dict) or not advertised:
        return {}, []

    proposed = dict(HQ_ENDPOINT_DEFAULTS)
    candidate = {}
    for key, value in advertised.items():
        if key not in HQ_ENDPOINT_DEFAULTS:
            continue  # never let the server introduce settings we do not know
        try:
            candidate[key] = coerce_endpoint(key, value)
        except ValueError:
            continue
    proposed.update(candidate)
    return candidate, validate_endpoints(proposed)


# ── The cycle ────────────────────────────────────────────────────────────────

def _probe(url: str) -> bool:
    import requests

    try:
        response = requests.get(f"{url.rstrip('/')}/health", timeout=PROBE_TIMEOUT)
    except requests.RequestException:
        return False
    return response.status_code in (200, 401, 403)


def fetch_and_apply(data_path, update_server_url: str, session=None) -> dict:
    """One poll. Returns a result dict describing what happened and why.

    Never raises: this runs on a background loop, and a failure to learn about
    a move must not disturb a machine that is working.
    """
    import requests

    http = session or requests
    result = {"changed": False, "adopted": None, "reason": ""}

    if not update_server_url:
        result["reason"] = "no update server address configured"
        return result

    try:
        response = http.get(
            f"{update_server_url.rstrip('/')}/api/endpoints/", timeout=FETCH_TIMEOUT
        )
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"update server unreachable: {str(exc)[:120]}"
        return result
    if response.status_code != 200:
        result["reason"] = f"update server answered HTTP {response.status_code}"
        return result

    try:
        payload = response.json()
    except ValueError:
        result["reason"] = "update server response was not JSON"
        return result

    document, why = verify_document(payload)
    if document is None:
        LOG.warning("Refusing endpoint document: %s", why)
        result["reason"] = why
        return result

    state = read_state(data_path)

    if not document.get("configured"):
        # The server has no opinion. Explicitly not a signal to unconfigure.
        result["reason"] = "update server advertises no addresses"
        return result

    if document.get("serial") and document["serial"] == state.get("serial"):
        result["reason"] = "already on the advertised addresses"
        return result

    endpoints, errors = usable_endpoints(document)
    if errors:
        LOG.error("Refusing advertised addresses: %s", "; ".join(errors))
        result["reason"] = "advertised addresses failed validation: " + "; ".join(errors)
        return result
    if not endpoints:
        result["reason"] = "document carried no addresses this client understands"
        return result

    sync_url = endpoints.get("sync.api_url")
    if sync_url and not _probe(sync_url):
        LOG.warning("Advertised sync address %s does not answer; keeping current", sync_url)
        result["reason"] = f"advertised address {sync_url} does not answer"
        return result

    previous = state.get("endpoints") or {}
    _write_state(data_path, {
        "serial": document.get("serial"),
        "endpoints": endpoints,
        "fallbacks": document.get("fallbacks") or {},
        "adopted_at": datetime.now(timezone.utc).isoformat(),
        "adopted_monotonic": time.time(),
        "previous": previous,
        "failures_since_adopt": 0,
        "source_document_issued_at": document.get("issued_at"),
    })
    LOG.warning("Adopted HQ addresses from the update server: %s", endpoints)
    result.update({"changed": True, "adopted": endpoints, "reason": "adopted"})
    return result


def note_health(data_path, healthy: bool) -> bool:
    """Record whether the adopted address is working; revert if it is not.

    Returns True when a revert happened. Called from the agent's existing
    health check, so it costs no extra traffic.
    """
    state = read_state(data_path)
    if not state.get("endpoints"):
        return False

    if healthy:
        if state.get("failures_since_adopt"):
            state["failures_since_adopt"] = 0
            _write_state(data_path, state)
        return False

    failures = int(state.get("failures_since_adopt") or 0) + 1
    state["failures_since_adopt"] = failures
    elapsed = time.time() - float(state.get("adopted_monotonic") or 0)

    if failures < REVERT_AFTER_FAILURES or elapsed < REVERT_AFTER_SECONDS:
        _write_state(data_path, state)
        return False

    previous = state.get("previous") or {}
    LOG.error(
        "Adopted HQ address failed %d times over %ds — reverting to %s",
        failures, int(elapsed), previous or "the shipped default",
    )
    if previous:
        _write_state(data_path, {
            "serial": None,  # so the same document can be offered again later
            "endpoints": previous,
            "fallbacks": {},
            "adopted_at": datetime.now(timezone.utc).isoformat(),
            "adopted_monotonic": time.time(),
            "previous": {},
            "failures_since_adopt": 0,
            "reverted_from": state.get("endpoints"),
        })
    else:
        try:
            _state_path(data_path).unlink()  # back to the shipped default
        except OSError:
            pass
    return True


def poll_seconds(data_path, default: int = 900) -> int:
    document_value = read_state(data_path).get("poll_seconds")
    try:
        return max(60, int(document_value))
    except (TypeError, ValueError):
        return default


def describe(data_path) -> dict:
    """What this machine has adopted, for the settings page and the command."""
    state = read_state(data_path)
    return {
        "adopted": state.get("endpoints") or {},
        "adopted_at": state.get("adopted_at"),
        "serial": state.get("serial"),
        "failures_since_adopt": state.get("failures_since_adopt") or 0,
        "reverted_from": state.get("reverted_from"),
    }


def clear(data_path) -> bool:
    """Forget anything adopted from the update server."""
    try:
        _state_path(data_path).unlink()
        return True
    except OSError:
        return False


__all__ = [
    "clear", "describe", "fetch_and_apply", "note_health", "poll_seconds",
    "read_state", "usable_endpoints", "verify_document",
    "cirqen_config",
]
