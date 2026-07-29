#!/usr/bin/env python3
"""
Shared credential loader for helper scripts.

Resolution order (first hit wins) for each field:
    1. Environment variable  (POSTGRES_LOCAL_*, POSTGRES_HQ_*)
    2. ~/.cirqen/data/config.json  (the running app's config)
    3. Repo-root .env  (loaded via python-dotenv if available)

NEVER hardcode credentials here. This module exists precisely so that
helper scripts contain no secrets.
"""
import os
import json
from pathlib import Path

# Best-effort: load repo-root .env into the environment.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass


def _load_config_json() -> dict:
    """Load the running app's config.json if present, else {}."""
    candidates = [
        os.environ.get("CIRQEN_DATA_DIR"),
        Path.home() / ".cirqen" / "data",
    ]
    for base in candidates:
        if not base:
            continue
        path = Path(base) / "config.json"
        if path.exists():
            try:
                return json.load(open(path))
            except Exception:
                pass
    return {}


_CFG = _load_config_json()


def _pick(env_key: str, cfg_section: str, cfg_key: str, default: str = "") -> str:
    val = os.getenv(env_key)
    if val:
        return val
    val = _CFG.get(cfg_section, {}).get(cfg_key)
    if val not in (None, ""):
        return str(val)
    return default


LOCAL_DB = {
    "host": _pick("POSTGRES_LOCAL_HOST", "local_db", "host", "127.0.0.1"),
    "port": int(_pick("POSTGRES_LOCAL_PORT", "local_db", "port", "2215")),
    "database": _pick("POSTGRES_LOCAL_DB", "local_db", "database", "cirqen1"),
    "user": _pick("POSTGRES_LOCAL_USER", "local_db", "user", "cirqen1"),
    "password": _pick("POSTGRES_LOCAL_PASSWORD", "local_db", "password"),
}

HQ_DB = {
    "host": _pick("POSTGRES_HQ_HOST", "hq_db", "host"),
    "port": int(_pick("POSTGRES_HQ_PORT", "hq_db", "port", "5432")),
    "database": _pick("POSTGRES_HQ_DB", "hq_db", "database"),
    "user": _pick("POSTGRES_HQ_USER", "hq_db", "user"),
    "password": _pick("POSTGRES_HQ_PASSWORD", "hq_db", "password"),
}


def local_dsn() -> str:
    d = LOCAL_DB
    return f"postgresql://{d['user']}:{d['password']}@{d['host']}:{d['port']}/{d['database']}"


def hq_dsn() -> str:
    d = HQ_DB
    return f"postgresql://{d['user']}:{d['password']}@{d['host']}:{d['port']}/{d['database']}"


def require(*dbs: str) -> None:
    """Fail fast with a clear message if required credentials are missing."""
    checks = {"local": LOCAL_DB, "hq": HQ_DB}
    for name in dbs:
        db = checks[name]
        if not db.get("password") or (name == "hq" and not db.get("host")):
            raise SystemExit(
                f"❌ Missing {name} DB credentials. Set them in .env "
                f"or ~/.cirqen/data/config.json (see .env.example)."
            )
