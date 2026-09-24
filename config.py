#!/usr/bin/env python3
"""
Integrated Configuration Manager for Cirqen Desktop Application
UPDATED WITH CUSTOM PORTS: PostgreSQL 2215, HQ 3315, Redis 7788
INCLUDES UPDATE SYSTEM CONFIGURATION (HQ server URL + API key)
"""

import os
import json
import secrets
from pathlib import Path
from typing import Dict, Any, Optional


# ══════════════════════════════════════════════════════════════════════════════
# HQ ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════
# This block is the ONLY place in the codebase that names where HQ is hosted
# (test_hq_endpoints.py fails the build if a hostname appears anywhere else).
#
# Each value is resolved, highest priority first, from:
#   1. its environment variable          (HQ_ENDPOINT_ENV)   — honoured in
#                                          production as well as in development
#   2. config.json                       — only a value an operator set on
#                                          purpose; defaults are never written
#   3. endpoints.json                    — the signed document the update server
#                                          hands out (endpoint_sync.py). This is
#                                          how a fleet-wide move reaches a machine
#                                          without a release; an operator's own
#                                          pin above still wins.
#   4. provisioning.json                 — an installer can carry addresses
#   5. HQ_ENDPOINT_DEFAULTS below        — what a fresh install uses
#
# Because defaults are not written to config.json, changing HQ_ENDPOINT_DEFAULTS
# and shipping a build moves every machine that has not been pinned by hand.
# describe_endpoints() / resolve_endpoints() report which layer supplied each.
HQ_ENDPOINT_DEFAULTS = {
    # Sync API and certificate authority (~/Desktop/hq_server, Flask).
    "sync.api_url": "https://hq-server-dgs6.onrender.com/api/sync",
    # Update server (cirqen-labs/hq_server, FastAPI). A different service.
    "update.server_url": "https://cirqen-hq.onrender.com",
    # HQ database, reached directly by the sync agent and Django's HQ alias.
    "hq_db.host": "aws-0-eu-north-1.pooler.supabase.com",
    "hq_db.port": 5432,
    "hq_db.database": "postgres",
    "hq_db.user": "postgres.nwlwaeeyduxroykrgksi",
    "hq_db.sslmode": "require",
}

HQ_ENDPOINT_ENV = {
    "sync.api_url": "SYNC_API_URL",
    "update.server_url": "HQ_SERVER_URL",
    "hq_db.host": "POSTGRES_HQ_HOST",
    "hq_db.port": "POSTGRES_HQ_PORT",
    "hq_db.database": "POSTGRES_HQ_DB",
    "hq_db.user": "POSTGRES_HQ_USER",
    "hq_db.sslmode": "POSTGRES_SSLMODE",
}

# Values every config.json written BEFORE endpoint layering may contain because
# they were once the shipped defaults. A file without ENDPOINT_MARKER is
# migrated: any of these (or the current default) is treated as "not set", so
# the machine follows HQ_ENDPOINT_DEFAULTS instead of staying pinned. Anything
# else in such a file was typed by a person and is kept. This set is frozen
# history; it never needs to grow, because files written from now on hold
# overrides only.
PRE_LAYERING_DEFAULTS = {
    "sync.api_url": {"https://hq-server-dgs6.onrender.com/api/sync"},
    "update.server_url": {"https://cirqen-hq.onrender.com"},
    "hq_db.host": {
        "aws-0-eu-north-1.pooler.supabase.com",
        "dpg-d7rk2sa8qa3s73diimb0-a.ohio-postgres.render.com",  # decommissioned
    },
    "hq_db.port": {5432},
    "hq_db.database": {"postgres", "cirqen_hq", "b12technologies"},
    "hq_db.user": {"postgres.nwlwaeeyduxroykrgksi", "cirqen_hq", "b12technologies"},
    "hq_db.sslmode": {"require"},
}

# setup_environment_variables() exports every resolved value into os.environ so
# that legacy code (and child processes) can read POSTGRES_HQ_HOST and friends.
# Those exports are this layer's own output, not an operator's choice, and a
# child process inherits them. Without this marker the child would read its
# parent's export back as an "env" override, mislabel the source and make the
# settings page read-only.
#
# The marker records the exported VALUES as JSON, not just the names: a variable
# is treated as this layer's own echo only while it still holds exactly what we
# wrote. Anything an operator sets differs from that and is honoured normally.
ENDPOINTS_FROM_CONFIG_VAR = "CIRQEN_ENDPOINTS_FROM_CONFIG"

# Written by endpoint_sync.py after it has verified a signed document from the
# update server and confirmed the address answers. Kept in its own file so it is
# never confused with an operator's deliberate pin in config.json, and so a
# single delete puts the machine back on the shipped default.
REMOTE_ENDPOINTS_FILE = "endpoints.json"

# Non-secret settings that must still be overridable by the environment in the
# packaged app, not only in development. Same failure as finding C-2: an
# override that works on a developer's machine and is silently ignored in
# production is worse than no override at all. Values here are never written to
# config.json, so the environment stays the source.
PLAIN_ENV = {
    "update.public_key": "UPDATE_PUBLIC_KEY",
    # The desktop launcher moves the embedded DB off the configured port when
    # another postgres holds it, and hands the live port to its children here.
    "local_db.port": "POSTGRES_LOCAL_PORT",
}

ENDPOINT_MARKER = "_endpoints_v"
ENDPOINT_MARKER_VALUE = 2

_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_SSLMODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")


def _get_path(cfg: dict, dotted: str):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _put_path(cfg: dict, dotted: str, value) -> None:
    *parents, leaf = dotted.split(".")
    node = cfg
    for part in parents:
        node = node.setdefault(part, {})
    node[leaf] = value


def _del_path(cfg: dict, dotted: str) -> None:
    *parents, leaf = dotted.split(".")
    node = cfg
    for part in parents:
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return
    if isinstance(node, dict):
        node.pop(leaf, None)


def coerce_endpoint(key: str, value):
    """Public: used by the settings page and the hq_endpoint command.

    Normalise a raw value: ports become int, URLs and hosts lose whitespace
    and trailing slashes. Raises ValueError naming the setting."""
    if key == "hq_db.port":
        try:
            return int(str(value).strip())
        except ValueError:
            raise ValueError(f"{key} must be a whole number, got {value!r}")
    text = str(value).strip()
    if key in ("sync.api_url", "update.server_url"):
        text = text.rstrip("/")
    return text


def _endpoint_is_override(key: str, stored, migrating: bool) -> bool:
    """Is a value found in config.json something a person set on purpose?"""
    if stored is None or stored == "":
        return False
    try:
        value = coerce_endpoint(key, stored)
    except ValueError:
        return True  # keep it; validation reports it by name
    if value == HQ_ENDPOINT_DEFAULTS[key]:
        return False
    if migrating and value in PRE_LAYERING_DEFAULTS.get(key, ()):
        return False
    return True


def layer_endpoints(stored: dict, provisioning: dict, env, migrating: bool, remote=None):
    """Resolve every HQ endpoint. Returns (values, sources, overrides).

    values     key -> resolved value
    sources    key -> "env" | "config.json" | "remote" | "provisioning" | "default"
    overrides  key -> value that must be written to config.json to keep it
    """
    remote = remote or {}
    values, sources, overrides = {}, {}, {}
    try:
        derived = json.loads(env.get(ENDPOINTS_FROM_CONFIG_VAR) or "{}")
        if not isinstance(derived, dict):
            derived = {}
    except ValueError:
        derived = {}
    for key, default in HQ_ENDPOINT_DEFAULTS.items():
        stored_raw = _get_path(stored, key)
        prov_raw = _get_path(provisioning, key)
        env_name = HQ_ENDPOINT_ENV[key]
        env_raw = (env.get(env_name) or "").strip()
        if env_name in derived and env_raw == str(derived[env_name]):
            env_raw = ""  # our own export echoed back, not an operator's choice

        if _endpoint_is_override(key, stored_raw, migrating):
            overrides[key] = coerce_endpoint(key, stored_raw)
        elif prov_raw not in (None, "") and coerce_endpoint(key, prov_raw) != default:
            overrides[key] = coerce_endpoint(key, prov_raw)

        from_config = _endpoint_is_override(key, stored_raw, migrating)

        if env_raw:
            values[key], sources[key] = coerce_endpoint(key, env_raw), "env"
        elif from_config:
            values[key], sources[key] = overrides[key], "config.json"
        elif key in remote:
            # A fleet-wide move. Below an operator's own pin on purpose: a
            # machine deliberately pointed somewhere stays there.
            values[key], sources[key] = remote[key], "remote"
        elif key in overrides:
            values[key], sources[key] = overrides[key], "provisioning"
        else:
            values[key], sources[key] = default, "default"
    return values, sources, overrides


def validate_endpoints(values: dict, sync_enabled: bool = True, db_enabled: bool = True) -> list:
    """Problems with the resolved addresses, each naming the setting."""
    from urllib.parse import urlparse

    errors = []

    def check_url(key, required, suffix=None, forbid_suffix=None):
        raw = values.get(key)
        if not raw:
            if required:
                errors.append(f"{key} is required")
            return
        parsed = urlparse(str(raw))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            errors.append(f"{key} must be a full URL such as https://host, got {raw!r}")
            return
        if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
            errors.append(f"{key} must use https (plain http is only allowed for localhost)")
        if any(ch.isspace() for ch in str(raw)):
            errors.append(f"{key} must not contain spaces")
        path = parsed.path.rstrip("/")
        if suffix and not path.endswith(suffix):
            errors.append(f"{key} should end with {suffix}, got {raw!r}")
        if forbid_suffix and "/api/" in path:
            errors.append(f"{key} is a server address, not an API path; remove {path!r}")

    check_url("sync.api_url", sync_enabled, suffix="/api/sync")
    check_url("update.server_url", True, forbid_suffix=True)

    if db_enabled:
        host = str(values.get("hq_db.host") or "")
        if not host:
            errors.append("hq_db.host is required")
        elif "://" in host or "/" in host or any(ch.isspace() for ch in host):
            errors.append(f"hq_db.host must be a bare host name, got {host!r}")
        port = values.get("hq_db.port")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            errors.append(f"hq_db.port must be between 1 and 65535, got {port!r}")
        if not values.get("hq_db.database"):
            errors.append("hq_db.database is required")
        if not values.get("hq_db.user"):
            errors.append("hq_db.user is required")
        if values.get("hq_db.sslmode") not in _SSLMODES:
            errors.append(f"hq_db.sslmode must be one of {', '.join(_SSLMODES)}")
    return errors


def _provisioning_candidates_for(data_path: Path) -> list:
    import sys

    candidates = []
    if os.getenv("CIRQEN_PROVISIONING_FILE"):
        candidates.append(Path(os.environ["CIRQEN_PROVISIONING_FILE"]))
    candidates.append(Path(data_path) / "provisioning.json")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "provisioning.json")
    candidates.append(Path(__file__).resolve().parent / "provisioning.json")
    return candidates


def read_remote_endpoints(data_path) -> dict:
    """Addresses adopted from the update server, or {} if none/unreadable.

    Never raises and never fetches: a corrupt or absent file simply means this
    layer has nothing to say, and resolution falls through to the layers below.
    """
    try:
        with open(Path(data_path) / REMOTE_ENDPOINTS_FILE) as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return {}
    adopted = payload.get("endpoints") if isinstance(payload, dict) else None
    if not isinstance(adopted, dict):
        return {}
    resolved = {}
    for key, value in adopted.items():
        if key in HQ_ENDPOINT_DEFAULTS:
            try:
                resolved[key] = coerce_endpoint(key, value)
            except ValueError:
                continue
    return resolved


def _read_provisioning_for(data_path: Path) -> dict:
    for candidate in _provisioning_candidates_for(data_path):
        try:
            if candidate.is_file():
                with open(candidate) as fh:
                    return json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"⚠️  Ignoring unreadable provisioning file {candidate}: {exc}")
    return {}


def resolve_endpoints(data_path=None, env=None) -> dict:
    """Read-only lookup for code that cannot build a CirqenConfig (the desktop
    launcher, helper scripts). Never writes, never prints.

    Returns {key: (value, source)} for every key in HQ_ENDPOINT_DEFAULTS.
    """
    base = Path(data_path) if data_path else Path(
        os.environ.get("CIRQEN_DATA_DIR") or Path.home() / ".cirqen" / "data"
    )
    stored = {}
    try:
        with open(base / "config.json") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            stored = loaded
    except (OSError, ValueError):
        pass
    migrating = bool(stored) and stored.get(ENDPOINT_MARKER) != ENDPOINT_MARKER_VALUE
    values, sources, _ = layer_endpoints(
        stored,
        _read_provisioning_for(base),
        os.environ if env is None else env,
        migrating,
        remote=read_remote_endpoints(base),
    )
    return {key: (values[key], sources[key]) for key in values}



class CirqenConfig:
    """
    Unified configuration manager with custom port configuration and update settings.
    Supports loading from config.json (production/packaged app) or .env (development).
    """

    # Default configuration — baked into the installer.
    # Each client machine gets a config.json derived from these defaults.
    DEFAULT_CONFIG = {
        # ===== SYNC AGENT =====
        "sync": {
            "api_url": HQ_ENDPOINT_DEFAULTS["sync.api_url"],
            "auth_token": "",  # secret: env SYNC_AUTH_TOKEN, or issued at enrollment
            "enrollment_code": "",  # secret: installer's one-time code (provisioning.json)
            "enabled": True,
            "debug": False,
            "poll_interval": 1,
            "download_interval": 5,
            "upload_batch_size": 50,
            "max_retries": 3,
            "retry_backoff": 3.0,
            "heartbeat_interval": 60,
            "certificate_interval": 30,
            "conflict_resolution": "last_write_wins",
            "wait_for_hq": True,
            "max_wait_for_hq": 300,
        },
        # ===== LOCAL DATABASE (Custom Port 2215) =====
        "local_db": {
            "host": "127.0.0.1",
            "port": 2215,
            "database": "cirqen1",
            "user": "cirqen1",
            # secret: generated on a machine's first run and kept in config.json
            # (the embedded database is created with it), or env POSTGRES_LOCAL_PASSWORD.
            "password": "",
        },
        # ===== HQ DATABASE (Supabase pooler) =====
        "hq_db": {
            "host": HQ_ENDPOINT_DEFAULTS["hq_db.host"],
            "port": HQ_ENDPOINT_DEFAULTS["hq_db.port"],
            "database": HQ_ENDPOINT_DEFAULTS["hq_db.database"],
            "user": HQ_ENDPOINT_DEFAULTS["hq_db.user"],
            "password": "",  # secret: env POSTGRES_HQ_PASSWORD or provisioning.json
            "sslmode": HQ_ENDPOINT_DEFAULTS["hq_db.sslmode"],
            "enabled": True,
        },
        # ===== REDIS (Custom Port 7788) =====
        "redis": {
            "host": "127.0.0.1",
            "port": 7788,
            "password": "",
            "enabled": True,
            "pending_list": "cmms:pending",
            "retention_hours": 72,
            "last_download_key": "cmms:last_download_time",
            "last_upload_key": "cmms:last_upload_time",
        },
        # ===== CLIENT IDENTIFICATION =====
        "client": {
            "name": "Test Hospital Workshop",
            "id": None,  # None = auto-generate from MAC address on first run
            # Printed in PDF headers (core/branding.py); left out when empty.
            "email": "",
            "phone": "",
        },
        # ===== MIRROR =====
        "mirror": {
            "enabled": True,
            "interval_hours": 24,
        },
        # ===== UPDATE SYSTEM =====
        # server_url  → your Render HQ FastAPI service
        # api_key     → HQ_API_KEY from Render → Environment tab
        # Both values are read by settings.py → UPDATE_SYSTEM and by
        # views.py → _get_hq_config() for check / download / apply.
        "update": {
            "server_url": HQ_ENDPOINT_DEFAULTS["update.server_url"],
            "api_key": "",  # secret: env HQ_API_KEY or provisioning.json
            # Ed25519 public half of the update server's signing key, from
            # `python hq_server/build_package.py --genkeys`. NOT a secret — it
            # is the trust anchor, so it belongs in the build, and an empty
            # value means unsigned mode: packages are applied unverified and
            # the fleet redirect (endpoint_sync.py) refuses to act at all.
            # Deliberately not something HQ can set remotely; a server that
            # could choose its own verification key is not verified.
            "public_key": "cIa1UoY7prue5F4cr7sNw2vwv8AK+G/Ea4hBYbL/Hz4=",
            "check_interval_hours": 24,
            "auto_apply": False,
            "require_confirmation": True,
            "max_backups": 5,
            "chunk_size": 8192,
            "max_download_size": 500 * 1024 * 1024,  # 500 MB
            "app_dir": None,  # None = BASE_DIR of the running app
            "cache_dir": "~/.cirqen/update_cache",
            "backup_dir": "~/.cirqen/backups",
            "components": {
                "templates": True,
                "static": True,
                "django_apps": True,
                "python_code": True,
                "migrations": True,
            },
        },
        # ===== REDPANDA / KAFKA =====
        "redpanda": {
            "enabled": True,
            "bootstrap_servers": ["127.0.0.1:9092"],
            "consumer_group": "b12tech-sync-service",
            "worker_count": 3,
            "sync_max_retries": 5,
            "enable_dlq": True,
            "dead_letter_topic": "sync_failures",
            "topics": [],
        },
        # ===== DEBEZIUM =====
        "debezium": {
            "table_include_list": [
                "public.CalSoft_calibrationauditlog",
                "public.CalSoft_calibrationnotification",
                "public.CalSoft_calibrationparameter",
                "public.CalSoft_calibrationprocedure",
                "public.CalSoft_calibrationreading",
                "public.CalSoft_calibrationreport",
                "public.CalSoft_calibrationschedule",
                "public.CalSoft_calibrationsession",
                "public.CalSoft_calibrationworkflow",
                "public.CalSoft_equipmentcalibrationprocedure",
                "public.CalSoft_historicalcalibration",
                "public.CalSoft_parameter",
                "public.CalSoft_parametercategory",
                "public.CalSoft_sessionparameterresolution",
                "public.CalSoft_setvalue",
                "public.CalSoft_standard",
                "public.CalSoft_standardparameter",
                "public.CalSoft_standardtype",
                "public.CalSoft_subparameter",
                "public.Inventory_department",
                "public.Inventory_equipment",
                "public.Inventory_equipmentdescription",
                "public.Inventory_manufacturer",
                "public.calSchedules_calibrationauditlog",
                "public.calSchedules_calibrationschedule",
                "public.jobcard_jobcard",
                "public.jobcard_sparepartused",
                "public.machineReports_equipmentcategory",
                "public.machineReports_equipmentstatusreport",
                "public.machineReports_machinerepairhistory",
                "public.machineReports_workshopequipmentreport",
                "public.parts_tools_accessories",
                "public.parts_tools_accessoryrequest",
                "public.parts_tools_tools",
                "public.ppms_auditlog",
                "public.ppms_ppmschedule",
                "public.reporthub_report",
                "public.users_userprofile",
                "public.users_usersecuritylog",
                "public.users_usersignature",
                "public.workshop_workshop",
                "public.accounts_customuser",
                "public.pending_certificates",
                "public.parts_tools_accessoriesname",
                "public.parts_tools_accessoriesmanufacturer",
                "public.parts_tools_toolsmanufacturer",
                "public.parts_tools_toolname",
            ],
        },
        # ===== TABLES TO SYNC =====
        # ORDER MATTERS: parents must come before children (FK dependency order).
        # Violations fixed:
        #   - calSchedules_calibrationschedule must precede CalSoft_calibrationsession
        #   - CalSoft_calibrationsession must precede calibrationreading,
        #     sessionparameterresolution, and pending_certificates
        "sync_tables": [
            # ── Root / reference tables ──────────────────────────────────────
            "public.accounts_customuser",
            "public.workshop_workshop",
            "public.Inventory_department",
            "public.Inventory_manufacturer",
            "public.Inventory_equipmentdescription",
            "public.Inventory_equipment",
            # ── User tables (depend on accounts_customuser) ──────────────────
            "public.users_userprofile",
            "public.users_usersecuritylog",
            "public.users_usersignature",
            # ── Calibration reference / lookup tables ────────────────────────
            "public.CalSoft_parametercategory",
            "public.CalSoft_parameter",
            "public.CalSoft_subparameter",
            "public.CalSoft_standardtype",
            "public.CalSoft_standard",
            "public.CalSoft_standardparameter",
            "public.CalSoft_calibrationprocedure",
            "public.CalSoft_calibrationparameter",
            "public.CalSoft_setvalue",
            # ── Schedule (parent of session) ─────────────────────────────────
            "public.calSchedules_calibrationschedule",
            # ── Session (parent of reading, resolution, pending_certificates) ─
            "public.CalSoft_calibrationsession",
            # ── Session children ─────────────────────────────────────────────
            "public.CalSoft_calibrationreading",
            "public.CalSoft_sessionparameterresolution",
            "public.CalSoft_historicalcalibration",
            "public.CalSoft_calibrationworkflow",
            "public.CalSoft_calibrationreport",
            "public.CalSoft_calibrationauditlog",
            "public.CalSoft_calibrationnotification",
            "public.CalSoft_equipmentcalibrationprocedure",
            "public.calSchedules_calibrationauditlog",
            # ── Certificates (depend on session) ─────────────────────────────
            "public.pending_certificates",
            # ── Job cards ────────────────────────────────────────────────────
            "public.jobcard_jobcard",
            "public.jobcard_sparepartused",
            # ── Machine reports ──────────────────────────────────────────────
            "public.machineReports_equipmentcategory",
            "public.machineReports_equipmentstatusreport",
            "public.machineReports_machinerepairhistory",
            "public.machineReports_workshopequipmentreport",
            # ── Parts / tools (lookups before dependents) ────────────────────
            "public.parts_tools_accessoriesname",
            "public.parts_tools_accessoriesmanufacturer",
            "public.parts_tools_accessories",
            "public.parts_tools_accessoryrequest",
            "public.parts_tools_toolsmanufacturer",
            "public.parts_tools_toolname",
            "public.parts_tools_tools",
            # ── PPM / reports ────────────────────────────────────────────────
            "public.ppms_auditlog",
            "public.ppms_ppmschedule",
            "public.reporthub_report",
        ],
        # ===== SYSTEM =====
        "system": {
            "database_pool_size": 10,
            "external_service_timeout": 10,
            "stats_print_interval": 60,
        },
        # ===== APPLICATION =====
        "app": {
            "django_port": 8000,
            "debug": False,
            "log_level": "INFO",
        },
        # ===== EMAIL (SMTP) =====
        "email": {
            "host": "smtp.gmail.com",
            "port": 587,
            "use_tls": True,
            "host_user": "",
            "host_password": "",
        },
    }

    # ─────────────────────────────────────────────────────────────────────────

    def __init__(self, data_path: Path, use_env_file: bool = None):
        """
        Args:
            data_path:    directory where config.json / secret.key are stored
            use_env_file: True  → load from .env  (dev)
                          False → load from config.json  (production)
                          None  → auto-detect (.env wins if it exists)
        """
        self.data_path = data_path
        self.config_file = data_path / "config.json"
        self.env_file = Path(".env")
        self.endpoint_sources: Dict[str, str] = {}
        self._endpoint_overrides: Dict[str, Any] = {}

        if use_env_file is None:
            use_env_file = self.env_file.exists()

        self.use_env_file = use_env_file

        if use_env_file:
            print("📄 Loading configuration from .env file (development mode)")
            self.config = self._load_from_env()
        else:
            print("📋 Loading configuration from config.json (production mode)")
            self.config = self._load_from_json()

    # ── Loaders ───────────────────────────────────────────────────────────────

    def _load_from_env(self) -> Dict[str, Any]:
        """Load from .env, falling back to DEFAULT_CONFIG for anything missing."""
        try:
            from dotenv import load_dotenv

            load_dotenv(self.env_file)

            cfg = self._deep_copy(self.DEFAULT_CONFIG)

            # sync
            cfg["sync"]["api_url"] = os.getenv("SYNC_API_URL", cfg["sync"]["api_url"])
            cfg["sync"]["auth_token"] = os.getenv("SYNC_AUTH_TOKEN", cfg["sync"]["auth_token"])
            cfg["sync"]["poll_interval"] = int(
                os.getenv("SYNC_POLL_INTERVAL", cfg["sync"]["poll_interval"])
            )
            cfg["sync"]["download_interval"] = int(
                os.getenv("SYNC_DOWNLOAD_INTERVAL", cfg["sync"]["download_interval"])
            )
            cfg["sync"]["upload_batch_size"] = int(
                os.getenv("SYNC_UPLOAD_BATCH_SIZE", cfg["sync"]["upload_batch_size"])
            )
            cfg["sync"]["retry_backoff"] = float(
                os.getenv("SYNC_RETRY_BACKOFF", cfg["sync"]["retry_backoff"])
            )
            cfg["sync"]["heartbeat_interval"] = int(
                os.getenv("SYNC_HEARTBEAT_INTERVAL", cfg["sync"]["heartbeat_interval"])
            )
            cfg["sync"]["certificate_interval"] = int(
                os.getenv("SYNC_MODEL_INTERVAL", cfg["sync"]["certificate_interval"])
            )
            cfg["sync"]["conflict_resolution"] = os.getenv(
                "SYNC_CONFLICT_RESOLUTION", cfg["sync"]["conflict_resolution"]
            )
            cfg["sync"]["debug"] = os.getenv("SYNC_DEBUG", "0") == "1"
            cfg["sync"]["wait_for_hq"] = os.getenv("WAIT_FOR_HQ", "true").lower() == "true"
            cfg["sync"]["max_wait_for_hq"] = int(
                os.getenv("MAX_WAIT_FOR_HQ", cfg["sync"]["max_wait_for_hq"])
            )

            # local_db
            cfg["local_db"]["host"] = os.getenv("POSTGRES_LOCAL_HOST", cfg["local_db"]["host"])
            cfg["local_db"]["port"] = int(os.getenv("POSTGRES_LOCAL_PORT", cfg["local_db"]["port"]))
            cfg["local_db"]["database"] = os.getenv(
                "POSTGRES_LOCAL_DB", cfg["local_db"]["database"]
            )
            cfg["local_db"]["user"] = os.getenv("POSTGRES_LOCAL_USER", cfg["local_db"]["user"])
            cfg["local_db"]["password"] = os.getenv(
                "POSTGRES_LOCAL_PASSWORD", cfg["local_db"]["password"]
            )

            # hq_db
            cfg["hq_db"]["host"] = os.getenv("POSTGRES_HQ_HOST", cfg["hq_db"]["host"])
            cfg["hq_db"]["port"] = int(os.getenv("POSTGRES_HQ_PORT", cfg["hq_db"]["port"]))
            cfg["hq_db"]["database"] = os.getenv("POSTGRES_HQ_DB", cfg["hq_db"]["database"])
            cfg["hq_db"]["user"] = os.getenv("POSTGRES_HQ_USER", cfg["hq_db"]["user"])
            cfg["hq_db"]["password"] = os.getenv("POSTGRES_HQ_PASSWORD", cfg["hq_db"]["password"])
            cfg["hq_db"]["sslmode"] = os.getenv("POSTGRES_SSLMODE", cfg["hq_db"]["sslmode"])

            # redis
            cfg["redis"]["host"] = os.getenv("REDIS_HOST", cfg["redis"]["host"])
            cfg["redis"]["port"] = int(os.getenv("REDIS_PORT", cfg["redis"]["port"]))
            cfg["redis"]["password"] = os.getenv("REDIS_PASSWORD", cfg["redis"]["password"])
            cfg["redis"]["pending_list"] = os.getenv(
                "REDIS_PENDING_LIST", cfg["redis"]["pending_list"]
            )
            cfg["redis"]["retention_hours"] = int(
                os.getenv("REDIS_RETENTION_HOURS", cfg["redis"]["retention_hours"])
            )

            # client
            cfg["client"]["name"] = os.getenv("CLIENT_NAME", cfg["client"]["name"])
            _cid = os.getenv("CLIENT_ID", "").strip()
            cfg["client"]["id"] = _cid if _cid else None

            # mirror
            cfg["mirror"]["enabled"] = os.getenv("MIRROR_ENABLED", "true").lower() == "true"
            cfg["mirror"]["interval_hours"] = float(
                os.getenv("MIRROR_INTERVAL_HOURS", cfg["mirror"]["interval_hours"])
            )

            # update  ← the important new block
            cfg["update"]["server_url"] = os.getenv("HQ_SERVER_URL", cfg["update"]["server_url"])
            cfg["update"]["api_key"] = os.getenv("HQ_API_KEY", cfg["update"]["api_key"])
            cfg["update"]["public_key"] = os.getenv(
                "UPDATE_PUBLIC_KEY", cfg["update"]["public_key"]
            )
            cfg["update"]["check_interval_hours"] = float(
                os.getenv("UPDATE_CHECK_INTERVAL_HOURS", cfg["update"]["check_interval_hours"])
            )
            cfg["update"]["auto_apply"] = os.getenv("AUTO_APPLY_UPDATES", "false").lower() == "true"
            cfg["update"]["require_confirmation"] = (
                os.getenv("REQUIRE_USER_CONFIRMATION", "true").lower() == "true"
            )
            cfg["update"]["max_backups"] = int(
                os.getenv("MAX_BACKUP_COUNT", cfg["update"]["max_backups"])
            )
            cfg["update"]["chunk_size"] = int(
                os.getenv("UPDATE_CHUNK_SIZE", cfg["update"]["chunk_size"])
            )
            cfg["update"]["max_download_size"] = int(
                os.getenv("MAX_DOWNLOAD_SIZE", cfg["update"]["max_download_size"])
            )
            _app_dir = os.getenv("APP_DIR")
            if _app_dir:
                cfg["update"]["app_dir"] = _app_dir
            cfg["update"]["cache_dir"] = os.getenv("UPDATE_CACHE_DIR", cfg["update"]["cache_dir"])
            cfg["update"]["backup_dir"] = os.getenv("BACKUP_DIR", cfg["update"]["backup_dir"])

            # update components
            cfg["update"]["components"]["templates"] = (
                os.getenv("UPDATE_TEMPLATES", "true").lower() == "true"
            )
            cfg["update"]["components"]["static"] = (
                os.getenv("UPDATE_STATIC", "true").lower() == "true"
            )
            cfg["update"]["components"]["django_apps"] = (
                os.getenv("UPDATE_DJANGO_APPS", "true").lower() == "true"
            )
            cfg["update"]["components"]["python_code"] = (
                os.getenv("UPDATE_PYTHON_CODE", "true").lower() == "true"
            )
            cfg["update"]["components"]["migrations"] = (
                os.getenv("UPDATE_MIGRATIONS", "true").lower() == "true"
            )

            # redpanda
            cfg["redpanda"]["enabled"] = os.getenv("REDPANDA_ENABLED", "true").lower() == "true"
            cfg["redpanda"]["bootstrap_servers"] = os.getenv(
                "REDPANDA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"
            ).split(",")
            cfg["redpanda"]["consumer_group"] = os.getenv(
                "REDPANDA_CONSUMER_GROUP", cfg["redpanda"]["consumer_group"]
            )
            cfg["redpanda"]["worker_count"] = int(
                os.getenv("REDPANDA_WORKER_COUNT", cfg["redpanda"]["worker_count"])
            )
            cfg["redpanda"]["sync_max_retries"] = int(
                os.getenv("REDPANDA_SYNC_MAX_RETRIES", cfg["redpanda"]["sync_max_retries"])
            )
            cfg["redpanda"]["enable_dlq"] = (
                os.getenv("REDPANDA_ENABLE_DLQ", "true").lower() == "true"
            )
            cfg["redpanda"]["dead_letter_topic"] = os.getenv(
                "REDPANDA_DEAD_LETTER_TOPIC", cfg["redpanda"]["dead_letter_topic"]
            )

            # tables
            _tables = os.getenv("TABLES", "")
            if _tables:
                cfg["sync_tables"] = [t.strip() for t in _tables.split(",") if t.strip()]
            _deb = os.getenv("DEBEZIUM_TABLE_INCLUDE_LIST", "")
            if _deb:
                cfg["debezium"]["table_include_list"] = [
                    t.strip() for t in _deb.split(",") if t.strip()
                ]

            # system
            cfg["system"]["database_pool_size"] = int(
                os.getenv("DATABASE_POOL_SIZE", cfg["system"]["database_pool_size"])
            )
            cfg["system"]["external_service_timeout"] = int(
                os.getenv("EXTERNAL_SERVICE_TIMEOUT", cfg["system"]["external_service_timeout"])
            )
            cfg["system"]["stats_print_interval"] = int(
                os.getenv("STATS_PRINT_INTERVAL", cfg["system"]["stats_print_interval"])
            )

            # email
            cfg["email"]["host"] = os.getenv("EMAIL_HOST", cfg["email"]["host"])
            cfg["email"]["port"] = int(os.getenv("EMAIL_PORT", cfg["email"]["port"]))
            cfg["email"]["use_tls"] = os.getenv("EMAIL_USE_TLS", "true").lower() == "true"
            cfg["email"]["host_user"] = os.getenv("EMAIL_HOST_USER", cfg["email"]["host_user"])
            cfg["email"]["host_password"] = os.getenv(
                "EMAIL_HOST_PASSWORD", cfg["email"]["host_password"]
            )

            self._resolve_endpoints(cfg, stored={}, migrating=False)  # noqa: E501
            self._apply_secret_sources(cfg)
            print("✓ Configuration loaded from .env file")
            return cfg

        except Exception as exc:
            print(f"⚠️  Error loading .env file: {exc}")
            print("   Falling back to default configuration")
            return self._deep_copy(self.DEFAULT_CONFIG)

    def _load_from_json(self) -> Dict[str, Any]:
        """Load from config.json, creating it on first run.

        HQ addresses follow HQ_ENDPOINT_DEFAULTS unless something set them on
        purpose; only those deliberate values are written back (see the block
        at the top of this file).
        """
        cfg = self._deep_copy(self.DEFAULT_CONFIG)
        stored: dict = {}
        first_run = not self.config_file.exists()
        recreated = False
        if not first_run:
            try:
                with open(self.config_file, "r") as f:
                    stored = json.load(f)
                if not isinstance(stored, dict):
                    raise ValueError("top level is not an object")
                self._deep_merge(cfg, stored)
                print(f"✓ Configuration loaded from {self.config_file}")
            except Exception as exc:
                print(f"⚠️  Error loading config.json: {exc}")
                print("   Recreating from defaults (the unreadable file is kept as config.json.corrupt)")
                self._keep_copy("config.json.corrupt")
                cfg = self._deep_copy(self.DEFAULT_CONFIG)
                stored = {}
                recreated = True
        else:
            print("🔍 First run — creating default config.json")

        migrating = bool(stored) and stored.get(ENDPOINT_MARKER) != ENDPOINT_MARKER_VALUE
        self._resolve_endpoints(cfg, stored, migrating)
        self._apply_plain_env(cfg)

        if first_run:
            if not cfg["local_db"].get("password"):
                cfg["local_db"]["password"] = secrets.token_urlsafe(24)
            self._save_json(self._persistable(cfg))
        elif recreated:
            self._save_json(self._persistable(cfg))
        elif migrating:
            print("   Migrating config.json: HQ addresses now follow the shipped defaults")
            self._keep_copy("config.json.pre-endpoints")
            self._save_json(self._persistable(cfg))

        if self._apply_secret_sources(cfg):
            # Persist secrets that came from a provisioning file, so the file
            # can be deleted after first run. Environment values are not saved.
            self._save_json(self._without_env_secrets(cfg))
        return cfg

    # ── HQ endpoints ─────────────────────────────────────────────────────────

    def _apply_plain_env(self, cfg: dict) -> None:
        """Environment wins for PLAIN_ENV settings, in every mode."""
        self._plain_env_keys = set()
        for dotted, env_name in PLAIN_ENV.items():
            value = (os.getenv(env_name) or "").strip()
            if value:
                if dotted.endswith(".port") and value.isdigit():
                    value = int(value)
                _put_path(cfg, dotted, value)
                self._plain_env_keys.add(dotted)

    def _resolve_endpoints(self, cfg: dict, stored: dict, migrating: bool) -> None:
        """Fill cfg's HQ addresses: env > config.json > remote > provisioning > default."""
        values, sources, overrides = layer_endpoints(
            stored, self._read_provisioning(), os.environ, migrating,
            remote=read_remote_endpoints(self.data_path),
        )
        for key, value in values.items():
            _put_path(cfg, key, value)
        self.endpoint_sources = sources
        self._endpoint_overrides = overrides

    def _persistable(self, cfg: dict) -> dict:
        """Copy of cfg for writing to disk: HQ addresses appear only when set
        on purpose, so a shipped default is never frozen into a machine."""
        saved = self._deep_copy(cfg)
        for key in HQ_ENDPOINT_DEFAULTS:
            if key in self._endpoint_overrides:
                _put_path(saved, key, self._endpoint_overrides[key])
            else:
                _del_path(saved, key)
        # An environment-supplied value must not be frozen into config.json,
        # or unsetting the variable would silently leave the old value behind.
        # Keep whatever the file already held instead.
        on_disk = None
        for dotted in getattr(self, "_plain_env_keys", ()):
            if on_disk is None:
                try:
                    with open(self.config_file) as fh:
                        on_disk = json.load(fh)
                except (OSError, ValueError):
                    on_disk = {}
            _put_path(saved, dotted, _get_path(on_disk, dotted) or "")
        saved[ENDPOINT_MARKER] = ENDPOINT_MARKER_VALUE
        return saved

    def _keep_copy(self, suffix_name: str) -> None:
        """Copy config.json beside itself once, before it is rewritten."""
        target = self.config_file.with_name(suffix_name)
        try:
            if self.config_file.exists() and not target.exists():
                target.write_bytes(self.config_file.read_bytes())
                try:
                    target.chmod(0o600)
                except OSError:
                    pass
        except OSError as exc:
            print(f"⚠️  Could not keep a copy as {suffix_name}: {exc}")

    def sync_url(self) -> str:
        return self.get("sync.api_url")

    def update_url(self) -> str:
        return self.get("update.server_url")

    def describe_endpoints(self) -> list:
        """[{key, value, source}] for every HQ address, for UIs and logs."""
        return [
            {"key": key, "value": self.get(key), "source": self.endpoint_sources.get(key, "default")}
            for key in HQ_ENDPOINT_DEFAULTS
        ]

    # ── Secrets ───────────────────────────────────────────────────────────────
    #
    # Secrets are never baked into this file. Each is resolved from, in order:
    #   1. its environment variable (never written to disk by this class),
    #   2. config.json in the data directory,
    #   3. a provisioning.json shipped with the installer (first run only; the
    #      values are copied into config.json).
    # A missing secret is reported loudly by missing_secrets()/validate_config().

    SECRET_ENV = {
        "sync.auth_token": "SYNC_AUTH_TOKEN",
        "sync.enrollment_code": "SYNC_ENROLLMENT_CODE",
        "update.api_key": "HQ_API_KEY",
        "hq_db.password": "POSTGRES_HQ_PASSWORD",
    }

    def _provisioning_candidates(self):
        return _provisioning_candidates_for(self.data_path)

    def _read_provisioning(self) -> dict:
        for candidate in self._provisioning_candidates():
            try:
                if candidate.is_file():
                    with open(candidate) as fh:
                        return json.load(fh)
            except (OSError, ValueError) as exc:
                print(f"⚠️  Ignoring unreadable provisioning file {candidate}: {exc}")
        return {}

    @staticmethod
    def _dig(cfg: dict, dotted: str):
        node = cfg
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    @staticmethod
    def _put(cfg: dict, dotted: str, value):
        *parents, leaf = dotted.split(".")
        node = cfg
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value

    def _apply_secret_sources(self, cfg: dict) -> bool:
        """Fill secrets from env / provisioning. Returns True if provisioning supplied any."""
        self._env_secret_keys = set()
        provisioning = None
        from_provisioning = False
        for dotted, env_name in self.SECRET_ENV.items():
            env_value = os.getenv(env_name, "").strip()
            if env_value:
                self._put(cfg, dotted, env_value)
                self._env_secret_keys.add(dotted)
                continue
            if self._dig(cfg, dotted):
                continue
            if provisioning is None:
                provisioning = self._read_provisioning()
            value = self._dig(provisioning, dotted)
            if value:
                self._put(cfg, dotted, value)
                from_provisioning = True
        return from_provisioning

    def _without_env_secrets(self, cfg: dict) -> dict:
        """Copy of cfg for writing to disk: env-supplied secrets keep the value
        already stored in config.json instead of the environment's."""
        saved = self._persistable(cfg)
        env_keys = getattr(self, "_env_secret_keys", ())
        if env_keys:
            try:
                with open(self.config_file) as fh:
                    on_disk = json.load(fh)
            except (OSError, ValueError):
                on_disk = {}
            for dotted in env_keys:
                self._put(saved, dotted, self._dig(on_disk, dotted) or "")
        return saved

    def missing_secrets(self) -> list:
        """Secrets that are required by the current config but not set."""
        missing = []
        # A new install has no key yet but an enrollment code to obtain one.
        if self.get("sync.enabled", True) and not (
            self.get("sync.auth_token") or self.get("sync.enrollment_code")
        ):
            missing.append("sync.auth_token (SYNC_AUTH_TOKEN) or sync.enrollment_code (SYNC_ENROLLMENT_CODE)")
        if self.get("hq_db.enabled") and not self.get("hq_db.password"):
            missing.append("hq_db.password (POSTGRES_HQ_PASSWORD)")
        if not self.get("update.api_key"):
            missing.append("update.api_key (HQ_API_KEY)")
        if not self.get("local_db.password"):
            missing.append("local_db.password (POSTGRES_LOCAL_PASSWORD)")
        return missing

    def export_provisioning(self, output_path: Path, include_endpoints: bool = False) -> Path:
        """Write the secrets an installer must carry to provisioning.json.

        include_endpoints also writes the HQ addresses, so an installer can
        point a group of machines at a different HQ without a rebuild.
        """
        data = {}
        for dotted in self.SECRET_ENV:
            if self.get(dotted):
                self._put(data, dotted, self.get(dotted))
        if include_endpoints:
            for dotted in HQ_ENDPOINT_DEFAULTS:
                self._put(data, dotted, self.get(dotted))
        output_path = Path(output_path)
        output_path.write_text(json.dumps(data, indent=2))
        try:
            output_path.chmod(0o600)
        except OSError:
            pass
        return output_path

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _deep_copy(self, d: dict) -> dict:
        """Return a true deep copy (no shared mutable references)."""
        import copy

        return copy.deepcopy(d)

    def _deep_merge(self, base: dict, override: dict):
        """Merge override into base in-place (recursive)."""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def _save_json(self, cfg: dict):
        try:
            with open(self.config_file, "w") as f:
                json.dump(cfg, f, indent=2)
            print(f"✓ Configuration saved to {self.config_file}")
        except Exception as exc:
            print(f"✗ Error saving config: {exc}")

    def _get_or_create_secret_key(self) -> str:
        secret_key_file = self.data_path / "secret.key"
        if secret_key_file.exists():
            return secret_key_file.read_text().strip()
        key = secrets.token_urlsafe(50)
        secret_key_file.write_text(key)
        return key

    # ── Public API ────────────────────────────────────────────────────────────

    def save(self):
        if not self.use_env_file:
            self._save_json(self._without_env_secrets(self.config))

    def get(self, key_path: str, default=None):
        """
        Read a value using dot notation.
        e.g.  config.get('update.server_url')
              config.get('local_db.port')
        """
        keys = key_path.split(".")
        value = self.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def set(self, key_path: str, value):
        """Write a value using dot notation and persist to config.json."""
        if key_path in HQ_ENDPOINT_DEFAULTS:
            value = coerce_endpoint(key_path, value)
            if value == HQ_ENDPOINT_DEFAULTS[key_path]:
                self._endpoint_overrides.pop(key_path, None)
                self.endpoint_sources[key_path] = "default"
            else:
                self._endpoint_overrides[key_path] = value
                self.endpoint_sources[key_path] = "config.json"
        keys = key_path.split(".")
        target = self.config
        for key in keys[:-1]:
            if key not in target:
                target[key] = {}
            target = target[key]
        target[keys[-1]] = value
        if not self.use_env_file:
            self.save()

    # ── Environment variables ─────────────────────────────────────────────────

    def setup_environment_variables(self):
        """
        Export every config value as an environment variable so that
        Django settings.py and third-party libraries can read them normally.
        """
        print("\n" + "=" * 60)
        print("🔧 Setting up environment variables")
        print("=" * 60)

        # sync
        os.environ["SYNC_API_URL"] = self.get("sync.api_url")
        os.environ["SYNC_AUTH_TOKEN"] = self.get("sync.auth_token")
        os.environ["SYNC_POLL_INTERVAL"] = str(self.get("sync.poll_interval"))
        os.environ["SYNC_DOWNLOAD_INTERVAL"] = str(self.get("sync.download_interval"))
        os.environ["SYNC_UPLOAD_BATCH_SIZE"] = str(self.get("sync.upload_batch_size"))
        os.environ["SYNC_RETRY_BACKOFF"] = str(self.get("sync.retry_backoff"))
        os.environ["SYNC_HEARTBEAT_INTERVAL"] = str(self.get("sync.heartbeat_interval"))
        os.environ["SYNC_MODEL_INTERVAL"] = str(self.get("sync.certificate_interval"))
        os.environ["SYNC_CONFLICT_RESOLUTION"] = self.get("sync.conflict_resolution")
        os.environ["SYNC_DEBUG"] = "1" if self.get("sync.debug") else "0"
        os.environ["WAIT_FOR_HQ"] = "true" if self.get("sync.wait_for_hq") else "false"
        os.environ["MAX_WAIT_FOR_HQ"] = str(self.get("sync.max_wait_for_hq"))

        # local_db  (port 2215 by default) — PortManager may have dynamically
        # reallocated this if the default was busy, and sets POSTGRES_LOCAL_PORT
        # itself before this ever runs (e.g. inside the Django/Celery
        # subprocess). setdefault() so we never clobber that with the static
        # config.json port once something else has already resolved it.
        os.environ["POSTGRES_LOCAL_HOST"] = self.get("local_db.host")
        os.environ.setdefault("POSTGRES_LOCAL_PORT", str(self.get("local_db.port")))
        os.environ["POSTGRES_LOCAL_DB"] = self.get("local_db.database")
        os.environ["POSTGRES_LOCAL_USER"] = self.get("local_db.user")
        os.environ["POSTGRES_LOCAL_PASSWORD"] = self.get("local_db.password")

        # hq_db  (Supabase pooler, port 5432)
        os.environ["POSTGRES_HQ_HOST"] = self.get("hq_db.host")
        os.environ["POSTGRES_HQ_PORT"] = str(self.get("hq_db.port"))
        os.environ["POSTGRES_HQ_DB"] = self.get("hq_db.database")
        os.environ["POSTGRES_HQ_USER"] = self.get("hq_db.user")
        os.environ["POSTGRES_HQ_PASSWORD"] = self.get("hq_db.password")
        os.environ["POSTGRES_SSLMODE"] = self.get("hq_db.sslmode", "require")
        # aliases used by Django settings
        os.environ["HQ_DB_HOST"] = self.get("hq_db.host")
        os.environ["HQ_DB_PORT"] = str(self.get("hq_db.port"))
        os.environ["HQ_DB_NAME"] = self.get("hq_db.database")
        os.environ["HQ_DB_USER"] = self.get("hq_db.user")
        os.environ["HQ_DB_PASSWORD"] = self.get("hq_db.password")
        os.environ["HQ_DB_SSLMODE"] = self.get("hq_db.sslmode", "require")

        # redis  (port 7788 by default) — same story as local_db above:
        # PortManager dynamically reallocates this port when the default is
        # busy (typically a just-closed previous session's redis-server
        # still releasing it) and may have already published the real
        # REDIS_HOST/REDIS_PORT to the environment before this runs. Resolve
        # from whatever is already set first, config.json only as a fallback,
        # so every derived URL below (including CELERY_BROKER_URL) points at
        # the redis-server that's actually running instead of a stale default
        # nothing is listening on.
        redis_host_val = os.environ.get("REDIS_HOST") or self.get("redis.host")
        redis_port_val = os.environ.get("REDIS_PORT") or str(self.get("redis.port"))
        os.environ["REDIS_HOST"] = redis_host_val
        os.environ["REDIS_PORT"] = redis_port_val
        os.environ["REDIS_PASSWORD"] = self.get("redis.password")
        os.environ["REDIS_ENABLED"] = "true" if self.get("redis.enabled") else "false"
        os.environ["REDIS_PENDING_LIST"] = self.get("redis.pending_list")
        os.environ["REDIS_RETENTION_HOURS"] = str(self.get("redis.retention_hours"))
        os.environ["SYNC_LAST_DOWNLOAD_KEY"] = self.get("redis.last_download_key")
        os.environ["SYNC_LAST_UPLOAD_KEY"] = self.get("redis.last_upload_key")
        _redis = f"redis://{redis_host_val}:{redis_port_val}"
        os.environ["REDIS_URL"] = f"{_redis}/0"
        os.environ["SESSION_REDIS_URL"] = f"{_redis}/1"
        os.environ["CELERY_BROKER_URL"] = f"{_redis}/2"
        os.environ["CELERY_RESULT_BACKEND"] = f"{_redis}/2"
        os.environ["CHANNELS_REDIS_URL"] = f"{_redis}/3"

        # client
        os.environ["CLIENT_NAME"] = self.get("client.name")

        # mirror
        os.environ["MIRROR_ENABLED"] = "true" if self.get("mirror.enabled") else "false"
        os.environ["MIRROR_INTERVAL_HOURS"] = str(self.get("mirror.interval_hours"))

        # ── update system ── (read by settings.py → UPDATE_SYSTEM)
        os.environ["HQ_SERVER_URL"] = self.get("update.server_url")
        os.environ["UPDATE_PUBLIC_KEY"] = self.get("update.public_key", "") or ""

        # Tell any later load (including a child process) which of the endpoint
        # variables above this layer produced, so they are not read back as
        # operator overrides. See ENDPOINTS_FROM_CONFIG_VAR.
        os.environ[ENDPOINTS_FROM_CONFIG_VAR] = json.dumps({
            HQ_ENDPOINT_ENV[key]: str(self.get(key))
            for key in HQ_ENDPOINT_DEFAULTS
            if self.endpoint_sources.get(key, "default") != "env"
        }, sort_keys=True)
        os.environ["HQ_API_KEY"] = self.get("update.api_key")
        os.environ["UPDATE_CHECK_INTERVAL_HOURS"] = str(self.get("update.check_interval_hours"))
        os.environ["AUTO_APPLY_UPDATES"] = "true" if self.get("update.auto_apply") else "false"
        os.environ["REQUIRE_USER_CONFIRMATION"] = (
            "true" if self.get("update.require_confirmation") else "false"
        )
        os.environ["MAX_BACKUP_COUNT"] = str(self.get("update.max_backups"))
        os.environ["UPDATE_CHUNK_SIZE"] = str(self.get("update.chunk_size"))
        os.environ["MAX_DOWNLOAD_SIZE"] = str(self.get("update.max_download_size"))
        os.environ["UPDATE_CACHE_DIR"] = self.get("update.cache_dir")
        os.environ["BACKUP_DIR"] = self.get("update.backup_dir")
        if self.get("update.app_dir"):
            os.environ["APP_DIR"] = self.get("update.app_dir")
        os.environ["UPDATE_TEMPLATES"] = (
            "true" if self.get("update.components.templates") else "false"
        )
        os.environ["UPDATE_STATIC"] = "true" if self.get("update.components.static") else "false"
        os.environ["UPDATE_DJANGO_APPS"] = (
            "true" if self.get("update.components.django_apps") else "false"
        )
        os.environ["UPDATE_PYTHON_CODE"] = (
            "true" if self.get("update.components.python_code") else "false"
        )
        os.environ["UPDATE_MIGRATIONS"] = (
            "true" if self.get("update.components.migrations") else "false"
        )

        # redpanda
        os.environ["REDPANDA_ENABLED"] = "true" if self.get("redpanda.enabled") else "false"
        os.environ["REDPANDA_BOOTSTRAP_SERVERS"] = ",".join(self.get("redpanda.bootstrap_servers"))
        os.environ["REDPANDA_CONSUMER_GROUP"] = self.get("redpanda.consumer_group")
        os.environ["REDPANDA_WORKER_COUNT"] = str(self.get("redpanda.worker_count"))
        os.environ["REDPANDA_SYNC_MAX_RETRIES"] = str(self.get("redpanda.sync_max_retries"))
        os.environ["REDPANDA_ENABLE_DLQ"] = "True" if self.get("redpanda.enable_dlq") else "False"
        os.environ["REDPANDA_DEAD_LETTER_TOPIC"] = self.get("redpanda.dead_letter_topic")

        # tables
        os.environ["TABLES"] = ",".join(self.get("sync_tables"))
        os.environ["DEBEZIUM_TABLE_INCLUDE_LIST"] = ",".join(
            self.get("debezium.table_include_list")
        )

        # system
        os.environ["DATABASE_POOL_SIZE"] = str(self.get("system.database_pool_size"))
        os.environ["EXTERNAL_SERVICE_TIMEOUT"] = str(self.get("system.external_service_timeout"))
        os.environ["STATS_PRINT_INTERVAL"] = str(self.get("system.stats_print_interval"))

        # email
        os.environ["EMAIL_HOST"] = self.get("email.host", "smtp.gmail.com")
        os.environ["EMAIL_PORT"] = str(self.get("email.port", 587))
        os.environ["EMAIL_USE_TLS"] = "true" if self.get("email.use_tls", True) else "false"
        os.environ["EMAIL_HOST_USER"] = self.get("email.host_user", "")
        os.environ["EMAIL_HOST_PASSWORD"] = self.get("email.host_password", "")

        # sync state dir
        sync_state_dir = self.data_path / "sync_state"
        sync_state_dir.mkdir(exist_ok=True, parents=True)
        os.environ["SYNC_STATE_DIR"] = str(sync_state_dir)

        # django
        os.environ["DEBUG"] = str(self.get("app.debug"))
        os.environ["DJANGO_SECRET_KEY"] = self._get_or_create_secret_key()
        os.environ["DJANGO_ALLOWED_HOSTS"] = "localhost,127.0.0.1"

        missing = self.missing_secrets()
        if missing:
            import logging

            message = (
                "Cirqen is missing required secrets: " + ", ".join(missing) + ". "
                "Set the environment variables, add them to config.json, or place the "
                "installer's provisioning.json in the data directory. Sync and updates "
                "will fail until then."
            )
            print("\n" + "!" * 60 + "\n❌ " + message + "\n" + "!" * 60)
            logging.getLogger("cirqen.config").error(message)

        print("✓ Environment variables configured")
        print(f"  • Local DB  : {self.get('local_db.host')}:{self.get('local_db.port')}")
        print(f"  • HQ DB     : {self.get('hq_db.host')}:{self.get('hq_db.port')}")
        print(f"  • Redis     : {self.get('redis.host')}:{self.get('redis.port')}")
        print(f"  • HQ Server : {self.get('update.server_url')}")
        print(f"  • Updates   : every {self.get('update.check_interval_hours')}h")
        print(f"  • Redpanda  : {'on' if self.get('redpanda.enabled') else 'off'}")
        print(f"  • Tables    : {len(self.get('sync_tables'))} to sync")
        print("=" * 60 + "\n")

    # ── Validation ────────────────────────────────────────────────────────────

    def validate_config(self) -> tuple:
        errors = []

        if self.get("sync.poll_interval") < 1:
            errors.append("sync.poll_interval must be ≥ 1")
        if self.get("sync.download_interval") < 1:
            errors.append("sync.download_interval must be ≥ 1")
        if self.get("sync.upload_batch_size") < 1:
            errors.append("sync.upload_batch_size must be ≥ 1")
        if not self.get("local_db.host"):
            errors.append("local_db.host is required")
        if not self.get("local_db.database"):
            errors.append("local_db.database is required")
        if not self.get("local_db.user"):
            errors.append("local_db.user is required")
        if self.get("local_db.port") != 2215:
            errors.append("⚠ local_db.port should be 2215")
        if self.get("hq_db.port") not in (3315, 5432, 6543):
            errors.append("⚠ hq_db.port should be 5432/6543 (Supabase pooler) or 3315 (custom)")
        if self.get("redis.port") != 7788:
            errors.append("⚠ redis.port should be 7788")
        errors.extend(
            validate_endpoints(
                {key: self.get(key) for key in HQ_ENDPOINT_DEFAULTS},
                sync_enabled=bool(self.get("sync.enabled", True)),
                db_enabled=bool(self.get("hq_db.enabled")),
            )
        )
        for secret in self.missing_secrets():
            errors.append(f"missing secret: {secret}")
        if self.get("update.check_interval_hours") < 0:
            errors.append("update.check_interval_hours cannot be negative")
        if self.get("update.max_backups") < 1:
            errors.append("update.max_backups must be ≥ 1")
        if self.get("update.chunk_size") < 1024:
            errors.append("update.chunk_size must be ≥ 1024 bytes")
        if self.get("update.max_download_size") < 1048576:
            errors.append("update.max_download_size must be ≥ 1 MB")
        if not self.get("update.public_key"):
            errors.append(
                "⚠ update.public_key is empty: update packages are applied WITHOUT "
                "signature checks and HQ address changes cannot be followed. Generate "
                "with `python hq_server/build_package.py --genkeys`."
            )
        if self.get("redpanda.enabled") and not self.get("redpanda.bootstrap_servers"):
            errors.append("redpanda.bootstrap_servers required when redpanda.enabled=True")
        if not self.get("sync_tables"):
            errors.append("sync_tables must have at least one entry")
        if self.get("system.database_pool_size") < 1:
            errors.append("system.database_pool_size must be ≥ 1")
        if self.get("email.host_user") and not self.get("email.host_password"):
            errors.append("email.host_password is required when email.host_user is set")

        return (len(errors) == 0, errors)

    # ── Connection tests ──────────────────────────────────────────────────────

    def test_connections(self) -> dict:
        results = {
            "local_db": False,
            "hq_db": False,
            "redis": False,
            "redpanda": False,
            "update_server": False,
        }

        # local_db
        try:
            import psycopg2

            c = psycopg2.connect(
                host=self.get("local_db.host"),
                port=self.get("local_db.port"),
                database=self.get("local_db.database"),
                user=self.get("local_db.user"),
                password=self.get("local_db.password"),
                connect_timeout=5,
            )
            c.close()
            results["local_db"] = True
            print(f"✓ local_db  ({self.get('local_db.port')})")
        except Exception as exc:
            print(f"✗ local_db: {exc}")

        # hq_db
        if self.get("hq_db.enabled"):
            try:
                import psycopg2

                c = psycopg2.connect(
                    host=self.get("hq_db.host"),
                    port=self.get("hq_db.port"),
                    database=self.get("hq_db.database"),
                    user=self.get("hq_db.user"),
                    password=self.get("hq_db.password"),
                    sslmode=self.get("hq_db.sslmode", "require"),
                    connect_timeout=5,
                )
                c.close()
                results["hq_db"] = True
                print(f"✓ hq_db  ({self.get('hq_db.port')})")
            except Exception as exc:
                print(f"✗ hq_db: {exc}")
        else:
            results["hq_db"] = None

        # redis
        if self.get("redis.enabled"):
            try:
                import redis as redis_lib

                r = redis_lib.Redis(
                    host=self.get("redis.host"),
                    port=self.get("redis.port"),
                    password=self.get("redis.password") or None,
                    socket_connect_timeout=5,
                )
                r.ping()
                results["redis"] = True
                print(f"✓ redis  ({self.get('redis.port')})")
            except Exception as exc:
                print(f"✗ redis: {exc}")
        else:
            results["redis"] = None

        # redpanda
        if self.get("redpanda.enabled"):
            try:
                from kafka import KafkaProducer

                p = KafkaProducer(
                    bootstrap_servers=self.get("redpanda.bootstrap_servers"),
                    request_timeout_ms=5000,
                )
                p.close()
                results["redpanda"] = True
                print("✓ redpanda")
            except Exception as exc:
                print(f"✗ redpanda: {exc}")
        else:
            results["redpanda"] = None

        # update server — hit /health/ with the API key
        if self.get("update.server_url"):
            try:
                import requests

                resp = requests.get(
                    f"{self.get('update.server_url')}/health/",
                    headers={"X-Api-Key": self.get("update.api_key")},
                    timeout=15,
                )
                if resp.status_code == 200:
                    results["update_server"] = True
                    print(f"✓ update_server  ({self.get('update.server_url')})")
                else:
                    print(f"✗ update_server: HTTP {resp.status_code}")
            except Exception as exc:
                print(f"✗ update_server: {exc}")
        else:
            results["update_server"] = None

        return results

    # ── Summary ───────────────────────────────────────────────────────────────

    def get_config_summary(self) -> str:
        comps = self.get("update.components", {})
        enabled_comps = [k for k, v in comps.items() if v]
        return f"""
╔══════════════════════════════════════════════════════════════╗
║              CIRQEN DESKTOP CONFIGURATION                    ║
╚══════════════════════════════════════════════════════════════╝

Source : {'.env' if self.use_env_file else 'config.json'}
DataDir: {self.data_path}

┌─ LOCAL DB (port 2215) ──────────────────────────────────────┐
│  {self.get('local_db.host')}:{self.get('local_db.port')}  db={self.get('local_db.database')}  user={self.get('local_db.user')}
└─────────────────────────────────────────────────────────────┘

┌─ HQ DB (Supabase pooler) ───────────────────────────────────┐
│  enabled={self.get('hq_db.enabled')}
│  {self.get('hq_db.host')}:{self.get('hq_db.port')}  db={self.get('hq_db.database')}  sslmode={self.get('hq_db.sslmode')}
└─────────────────────────────────────────────────────────────┘

┌─ REDIS (port 7788) ─────────────────────────────────────────┐
│  {self.get('redis.host')}:{self.get('redis.port')}  enabled={self.get('redis.enabled')}
└─────────────────────────────────────────────────────────────┘

┌─ UPDATE SYSTEM ─────────────────────────────────────────────┐
│  Server  : {self.get('update.server_url')}
│  API key : {self.get('update.api_key')[:12]}…
│  Interval: every {self.get('update.check_interval_hours')}h
│  Auto    : {self.get('update.auto_apply')}
│  Parts   : {', '.join(enabled_comps) or 'none'}
│  Backups : {self.get('update.max_backups')}  Max: {self.get('update.max_download_size') // (1024*1024)} MB
└─────────────────────────────────────────────────────────────┘

┌─ SYNC AGENT ────────────────────────────────────────────────┐
│  enabled={self.get('sync.enabled')}  poll={self.get('sync.poll_interval')}s
│  tables={len(self.get('sync_tables'))}  batch={self.get('sync.upload_batch_size')}
└─────────────────────────────────────────────────────────────┘

┌─ REDPANDA ──────────────────────────────────────────────────┐
│  enabled={self.get('redpanda.enabled')}  workers={self.get('redpanda.worker_count')}
│  servers={', '.join(self.get('redpanda.bootstrap_servers'))}
└─────────────────────────────────────────────────────────────┘

┌─ CLIENT ────────────────────────────────────────────────────┐
│  name={self.get('client.name')}
└─────────────────────────────────────────────────────────────┘
"""

    # ── .env export ───────────────────────────────────────────────────────────

    def export_to_env_file(self, output_path: Optional[Path] = None):
        if output_path is None:
            output_path = Path(".env")
        lines = [
            "# Cirqen Desktop — generated config",
            "",
            "# Sync",
            f"SYNC_API_URL={self.get('sync.api_url')}",
            f"SYNC_AUTH_TOKEN={self.get('sync.auth_token')}",
            "",
            "# Local DB (port 2215)",
            f"POSTGRES_LOCAL_HOST={self.get('local_db.host')}",
            f"POSTGRES_LOCAL_PORT={self.get('local_db.port')}",
            f"POSTGRES_LOCAL_DB={self.get('local_db.database')}",
            f"POSTGRES_LOCAL_USER={self.get('local_db.user')}",
            f"POSTGRES_LOCAL_PASSWORD={self.get('local_db.password')}",
            "",
            "# HQ DB (Supabase pooler)",
            f"POSTGRES_HQ_HOST={self.get('hq_db.host')}",
            f"POSTGRES_HQ_PORT={self.get('hq_db.port')}",
            f"POSTGRES_HQ_DB={self.get('hq_db.database')}",
            f"POSTGRES_HQ_USER={self.get('hq_db.user')}",
            f"POSTGRES_HQ_PASSWORD={self.get('hq_db.password')}",
            f"POSTGRES_SSLMODE={self.get('hq_db.sslmode')}",
            "",
            "# Redis (port 7788)",
            f"REDIS_HOST={self.get('redis.host')}",
            f"REDIS_PORT={self.get('redis.port')}",
            f"REDIS_PASSWORD={self.get('redis.password')}",
            f"REDIS_PENDING_LIST={self.get('redis.pending_list')}",
            f"REDIS_RETENTION_HOURS={self.get('redis.retention_hours')}",
            "",
            "# Update system",
            f"HQ_SERVER_URL={self.get('update.server_url')}",
            f"HQ_API_KEY={self.get('update.api_key')}",
            f"UPDATE_CHECK_INTERVAL_HOURS={self.get('update.check_interval_hours')}",
            f"AUTO_APPLY_UPDATES={'true' if self.get('update.auto_apply') else 'false'}",
            f"REQUIRE_USER_CONFIRMATION={'true' if self.get('update.require_confirmation') else 'false'}",
            f"MAX_BACKUP_COUNT={self.get('update.max_backups')}",
            f"UPDATE_CHUNK_SIZE={self.get('update.chunk_size')}",
            f"MAX_DOWNLOAD_SIZE={self.get('update.max_download_size')}",
            f"UPDATE_CACHE_DIR={self.get('update.cache_dir')}",
            f"BACKUP_DIR={self.get('update.backup_dir')}",
            f"APP_DIR={self.get('update.app_dir') or ''}",
            f"UPDATE_TEMPLATES={'true' if self.get('update.components.templates') else 'false'}",
            f"UPDATE_STATIC={'true' if self.get('update.components.static') else 'false'}",
            f"UPDATE_DJANGO_APPS={'true' if self.get('update.components.django_apps') else 'false'}",
            f"UPDATE_PYTHON_CODE={'true' if self.get('update.components.python_code') else 'false'}",
            f"UPDATE_MIGRATIONS={'true' if self.get('update.components.migrations') else 'false'}",
            "",
            "# Client",
            f"CLIENT_NAME={self.get('client.name')}",
            f"CLIENT_ID={self.get('client.id') or ''}",
            "",
            "# Mirror",
            f"MIRROR_ENABLED={'true' if self.get('mirror.enabled') else 'false'}",
            f"MIRROR_INTERVAL_HOURS={self.get('mirror.interval_hours')}",
            "",
            "# Redpanda",
            f"REDPANDA_ENABLED={'true' if self.get('redpanda.enabled') else 'false'}",
            f"REDPANDA_BOOTSTRAP_SERVERS={','.join(self.get('redpanda.bootstrap_servers'))}",
            f"REDPANDA_CONSUMER_GROUP={self.get('redpanda.consumer_group')}",
            f"REDPANDA_WORKER_COUNT={self.get('redpanda.worker_count')}",
            f"REDPANDA_SYNC_MAX_RETRIES={self.get('redpanda.sync_max_retries')}",
            f"REDPANDA_ENABLE_DLQ={'True' if self.get('redpanda.enable_dlq') else 'False'}",
            f"REDPANDA_DEAD_LETTER_TOPIC={self.get('redpanda.dead_letter_topic')}",
            "",
            "# Sync + Debezium tables",
            f"TABLES={','.join(self.get('sync_tables'))}",
            f"DEBEZIUM_TABLE_INCLUDE_LIST={','.join(self.get('debezium.table_include_list'))}",
            "",
            "# System",
            f"DATABASE_POOL_SIZE={self.get('system.database_pool_size')}",
            f"EXTERNAL_SERVICE_TIMEOUT={self.get('system.external_service_timeout')}",
            f"STATS_PRINT_INTERVAL={self.get('system.stats_print_interval')}",
            "",
            "# Email (SMTP)",
            f"EMAIL_HOST={self.get('email.host')}",
            f"EMAIL_PORT={self.get('email.port')}",
            f"EMAIL_USE_TLS={'true' if self.get('email.use_tls') else 'false'}",
            f"EMAIL_HOST_USER={self.get('email.host_user')}",
            f"EMAIL_HOST_PASSWORD={self.get('email.host_password')}",
            "",
            "# Timing",
            f"SYNC_POLL_INTERVAL={self.get('sync.poll_interval')}",
            f"SYNC_DOWNLOAD_INTERVAL={self.get('sync.download_interval')}",
            f"SYNC_UPLOAD_BATCH_SIZE={self.get('sync.upload_batch_size')}",
            f"SYNC_RETRY_BACKOFF={self.get('sync.retry_backoff')}",
            f"SYNC_HEARTBEAT_INTERVAL={self.get('sync.heartbeat_interval')}",
            f"SYNC_MODEL_INTERVAL={self.get('sync.certificate_interval')}",
            f"SYNC_CONFLICT_RESOLUTION={self.get('sync.conflict_resolution')}",
            f"SYNC_LAST_DOWNLOAD_KEY={self.get('redis.last_download_key')}",
            f"SYNC_LAST_UPLOAD_KEY={self.get('redis.last_upload_key')}",
            f"SYNC_DEBUG={'1' if self.get('sync.debug') else '0'}",
            f"WAIT_FOR_HQ={'true' if self.get('sync.wait_for_hq') else 'false'}",
            f"MAX_WAIT_FOR_HQ={self.get('sync.max_wait_for_hq')}",
        ]
        try:
            output_path.write_text("\n".join(lines) + "\n")
            print(f"✓ Exported to {output_path}")
        except Exception as exc:
            print(f"✗ Export failed: {exc}")


# ── Utility functions ─────────────────────────────────────────────────────────


def create_default_config(data_path: Path) -> CirqenConfig:
    return CirqenConfig(data_path)


def load_config(
    data_path: Path,
    force_env: bool = False,
    force_json: bool = False,
) -> CirqenConfig:
    if force_env and force_json:
        raise ValueError("Cannot force both .env and config.json")
    use_env = force_env if force_env else (None if not force_json else False)
    return CirqenConfig(data_path, use_env_file=use_env)


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_path = Path("./test_data")
    test_path.mkdir(exist_ok=True)

    cfg = CirqenConfig(test_path)
    print(cfg.get_config_summary())

    ok, errors = cfg.validate_config()
    if ok:
        print("✓ Config valid")
    else:
        for e in errors:
            print(f"  • {e}")

    cfg.setup_environment_variables()

    print(f"HQ_SERVER_URL = {os.getenv('HQ_SERVER_URL')}")
    print(f"HQ_API_KEY    = {os.getenv('HQ_API_KEY')[:12]}…")

    results = cfg.test_connections()
    for svc, status in results.items():
        icon = "✓" if status is True else ("⊘" if status is None else "✗")
        print(f"  {icon} {svc}")

    cfg.export_to_env_file(test_path / "exported.env")
