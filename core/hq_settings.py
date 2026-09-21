"""Reading, testing and changing where this desktop looks for HQ.

The values themselves live in ``config.HQ_ENDPOINT_DEFAULTS`` (the one place a
host is named). This module is the shared logic behind the settings page
(``core.views``) and the ``hq_endpoint`` management command, so both behave
identically: same labels, same validation, same reachability tests.

Nothing here writes a value that equals the shipped default — ``CirqenConfig.set``
treats that as releasing the pin, which is what keeps a machine following future
releases. See README, "Where HQ is hosted".
"""
from __future__ import annotations

import logging
import socket
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from config import (
    HQ_ENDPOINT_DEFAULTS,
    HQ_ENDPOINT_ENV,
    CirqenConfig,
    coerce_endpoint,
    validate_endpoints,
)

logger = logging.getLogger("core.hq_settings")

# How long a reachability probe may take. Short: this runs inside a request.
PROBE_TIMEOUT = 6

# Presentation for each setting, in the order the page shows them.
FIELDS: tuple[dict[str, Any], ...] = (
    {
        "key": "sync.api_url",
        "label": "Sync API",
        "help": "Where calibration records and certificate numbers are exchanged. Ends with /api/sync.",
        "group": "Servers",
        "placeholder": "https://hq.example.com/api/sync",
    },
    {
        "key": "update.server_url",
        "label": "Update server",
        "help": "Where application updates come from. A different service from the sync API; no /api/... path.",
        "group": "Servers",
        "placeholder": "https://updates.example.com",
    },
    {
        "key": "hq_db.host",
        "label": "HQ database host",
        "help": "Reached directly by the sync agent. Changing this needs the matching password.",
        "group": "HQ database",
        "placeholder": "db.example.com",
    },
    {"key": "hq_db.port", "label": "Port", "group": "HQ database", "placeholder": "5432"},
    {"key": "hq_db.database", "label": "Database", "group": "HQ database", "placeholder": "postgres"},
    {"key": "hq_db.user", "label": "User", "group": "HQ database", "placeholder": "postgres.abc"},
    {
        "key": "hq_db.sslmode",
        "label": "SSL mode",
        "group": "HQ database",
        "choices": ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"),
    },
)

# What each source means to someone reading the page.
SOURCE_LABELS = {
    "env": "Environment variable (set outside the app; the app cannot change it here)",
    "config.json": "Set on this machine",
    "remote": "Sent by the update server (fleet-wide move)",
    "provisioning": "Supplied by the installer",
    "default": "Shipped default (follows future releases)",
}


@dataclass
class ProbeResult:
    ok: bool
    detail: str


def load_config() -> CirqenConfig:
    """The same config object Django loaded, re-read so the page shows what is
    on disk now rather than what was loaded at startup."""
    from django.conf import settings

    return CirqenConfig(settings.DATA_PATH, use_env_file=False)


def remote_state(cfg: CirqenConfig) -> dict[str, Any]:
    """What, if anything, this machine has adopted from the update server."""
    try:
        import endpoint_sync
    except ImportError:
        return {"available": False, "adopted": {}}
    state = endpoint_sync.describe(cfg.data_path)
    state["available"] = True
    return state


def describe(cfg: CirqenConfig) -> list[dict[str, Any]]:
    """One row per setting: current value, where it came from, whether the page
    may change it, and what a release would use instead."""
    rows = []
    for field in FIELDS:
        key = field["key"]
        source = cfg.endpoint_sources.get(key, "default")
        rows.append({
            **field,
            "value": cfg.get(key),
            "source": source,
            "source_label": SOURCE_LABELS.get(source, source),
            "env_var": HQ_ENDPOINT_ENV[key],
            "default": HQ_ENDPOINT_DEFAULTS[key],
            "is_default": source == "default",
            # An environment variable wins over config.json, so editing here
            # would have no visible effect. Say so instead of lying.
            "locked": source == "env",
        })
    return rows


def apply_changes(cfg: CirqenConfig, submitted: dict[str, str]) -> tuple[list[str], list[str]]:
    """Validate and save. Returns (changed keys, errors); nothing is written if
    there are errors, so a bad form never half-applies."""
    proposed = {key: cfg.get(key) for key in HQ_ENDPOINT_DEFAULTS}
    errors: list[str] = []
    wanted: dict[str, Any] = {}

    for field in FIELDS:
        key = field["key"]
        if key not in submitted:
            continue
        if cfg.endpoint_sources.get(key) == "env":
            continue  # read-only here; the environment wins anyway
        raw = (submitted.get(key) or "").strip()
        if not raw:
            # Blank means "use the shipped default again".
            wanted[key] = HQ_ENDPOINT_DEFAULTS[key]
            proposed[key] = HQ_ENDPOINT_DEFAULTS[key]
            continue
        try:
            value = coerce_endpoint(key, raw)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        wanted[key] = value
        proposed[key] = value

    errors.extend(
        validate_endpoints(
            proposed,
            sync_enabled=bool(cfg.get("sync.enabled", True)),
            db_enabled=bool(cfg.get("hq_db.enabled")),
        )
    )
    if errors:
        return [], errors

    changed = [key for key, value in wanted.items() if value != cfg.get(key)]
    for key in changed:
        cfg.set(key, wanted[key])
    if changed:
        logger.warning("HQ endpoints changed: %s", ", ".join(changed))
    return changed, []


def _probe_http(url: str, path: str) -> ProbeResult:
    import requests

    if not url:
        return ProbeResult(False, "Not configured")
    target = f"{url.rstrip('/')}{path}"
    try:
        response = requests.get(target, timeout=PROBE_TIMEOUT)
    except requests.exceptions.SSLError as exc:
        return ProbeResult(False, f"TLS failed: {str(exc)[:120]}")
    except requests.exceptions.ConnectionError:
        return ProbeResult(False, "Cannot reach this address")
    except requests.exceptions.Timeout:
        return ProbeResult(False, f"No answer within {PROBE_TIMEOUT}s")
    except requests.RequestException as exc:
        return ProbeResult(False, str(exc)[:120])
    if response.status_code == 200:
        return ProbeResult(True, f"Answered 200 at {path}")
    # A sleeping Render instance answers 502/503 first, then serves normally.
    if response.status_code in (502, 503, 504):
        return ProbeResult(False, f"HTTP {response.status_code} — server may be waking up; try again")
    if response.status_code in (401, 403):
        return ProbeResult(True, f"Reached it (HTTP {response.status_code}: address is right, key is not)")
    return ProbeResult(False, f"HTTP {response.status_code} at {path}")


def probe_sync(cfg: CirqenConfig) -> ProbeResult:
    return _probe_http(cfg.get("sync.api_url"), "/health")


def probe_update(cfg: CirqenConfig) -> ProbeResult:
    return _probe_http(cfg.get("update.server_url"), "/health/")


def probe_database(cfg: CirqenConfig) -> ProbeResult:
    """Open a real connection. A wrong host usually fails here, not at the API."""
    if not cfg.get("hq_db.enabled"):
        return ProbeResult(True, "HQ database is disabled on this machine")
    host = cfg.get("hq_db.host")
    if not host:
        return ProbeResult(False, "Not configured")
    password = cfg.get("hq_db.password")
    if not password:
        return ProbeResult(False, "No password set (POSTGRES_HQ_PASSWORD or config.json)")
    try:
        import psycopg2
    except ImportError:  # pragma: no cover - psycopg2 ships with the app
        return ProbeResult(False, "psycopg2 is not installed")
    try:
        conn = psycopg2.connect(
            host=host,
            port=cfg.get("hq_db.port"),
            dbname=cfg.get("hq_db.database"),
            user=cfg.get("hq_db.user"),
            password=password,
            sslmode=cfg.get("hq_db.sslmode", "require"),
            connect_timeout=PROBE_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 - psycopg2 raises several types
        return ProbeResult(False, str(exc).strip().splitlines()[0][:140])
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    finally:
        conn.close()
    return ProbeResult(True, f"Connected to {host}")


def probe_all(cfg: CirqenConfig) -> dict[str, dict[str, Any]]:
    results = {
        "sync": probe_sync(cfg),
        "update": probe_update(cfg),
        "database": probe_database(cfg),
    }
    return {name: {"ok": r.ok, "detail": r.detail} for name, r in results.items()}


def resolves(host_or_url: str) -> ProbeResult:
    """DNS only — the cheapest way to catch a typed hostname that does not exist."""
    name = host_or_url
    if "://" in name:
        name = urlparse(name).hostname or ""
    if not name:
        return ProbeResult(False, "No host")
    try:
        socket.getaddrinfo(name, None)
    except socket.gaierror as exc:
        return ProbeResult(False, f"{name} does not resolve ({exc.strerror or exc})")
    return ProbeResult(True, f"{name} resolves")
