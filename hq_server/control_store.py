"""Cirqen Control's own records: hospitals, admin accounts, sessions, the
certificates issued to each hospital's HQ, and the audit log.

SQLite on the service's persistent disk (CONTROL_DB, beside HQ_STATE_DB),
like store.py. It never holds hospital data (equipment, job cards,
calibration): that lives only in each hospital's own HQ database.

The audit table is append-only: nothing in this module updates or deletes it.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT = Path(os.environ.get("HQ_STATE_DB", Path(__file__).parent / "hq_state.db")).with_name("control.db")
_local = threading.local()


def db_path() -> Path:
    return Path(os.environ.get("CONTROL_DB") or _DEFAULT)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def conn() -> sqlite3.Connection:
    path = str(db_path())
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "path", None) != path:
        c = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn, _local.path = c, path
    return c


SCHEMA = """
CREATE TABLE IF NOT EXISTS admins (
    id              INTEGER PRIMARY KEY,
    username        TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    totp_secret     TEXT NOT NULL,
    totp_last_step  INTEGER NOT NULL DEFAULT 0,
    role            TEXT NOT NULL CHECK (role IN ('owner', 'support', 'finance')),
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    last_login_at   TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    admin_id    INTEGER NOT NULL REFERENCES admins(id),
    csrf        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    ip          TEXT
);
CREATE TABLE IF NOT EXISTS login_attempts (
    id        INTEGER PRIMARY KEY,
    username  TEXT,
    ip        TEXT,
    at        REAL NOT NULL,
    ok        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS login_attempts_at ON login_attempts (at);
CREATE TABLE IF NOT EXISTS hospitals (
    code         TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    county       TEXT NOT NULL DEFAULT '',
    hod_name     TEXT NOT NULL DEFAULT '',
    hod_email    TEXT NOT NULL DEFAULT '',
    hod_phone    TEXT NOT NULL DEFAULT '',
    sync_url     TEXT NOT NULL DEFAULT '',
    fallbacks    TEXT NOT NULL DEFAULT '[]',
    updates_url  TEXT NOT NULL DEFAULT '',
    cert_prefix  TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'closed')),
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hq_certificates (
    id            INTEGER PRIMARY KEY,
    hospital      TEXT NOT NULL REFERENCES hospitals(code),
    public_key    TEXT NOT NULL,
    urls          TEXT NOT NULL,
    issued_at     TEXT NOT NULL,
    expires_at    TEXT NOT NULL,
    issued_by     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    id      INTEGER PRIMARY KEY,
    at      TEXT NOT NULL,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    target  TEXT NOT NULL DEFAULT '',
    detail  TEXT NOT NULL DEFAULT '{}',
    ip      TEXT NOT NULL DEFAULT ''
);
"""


# Columns added after the first release of the panel: (table, column, definition).
ADDED_COLUMNS = [
    ("hospitals", "release_mode", "TEXT NOT NULL DEFAULT 'follow'"),   # follow | pin | hold
    ("hospitals", "release_version", "TEXT NOT NULL DEFAULT ''"),       # for pin
]


def init() -> None:
    db_path().parent.mkdir(parents=True, exist_ok=True)
    c = conn()
    c.executescript(SCHEMA)
    for table, column, definition in ADDED_COLUMNS:
        existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


# ── audit ────────────────────────────────────────────────────────────────────

def audit(actor: str, action: str, target: str = "", detail: dict | None = None, ip: str = "") -> None:
    conn().execute(
        "INSERT INTO audit (at, actor, action, target, detail, ip) VALUES (?, ?, ?, ?, ?, ?)",
        (now(), actor, action, target, json.dumps(detail or {}, sort_keys=True, default=str), ip or ""),
    )


def audit_entries(limit: int = 200, target: str | None = None) -> list[dict]:
    if target:
        rows = conn().execute("SELECT * FROM audit WHERE target = ? ORDER BY id DESC LIMIT ?", (target, limit))
    else:
        rows = conn().execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) for r in rows]


# ── hospitals ────────────────────────────────────────────────────────────────

HOSPITAL_FIELDS = ("name", "county", "hod_name", "hod_email", "hod_phone", "sync_url", "fallbacks",
                   "updates_url", "cert_prefix", "status", "notes", "release_mode", "release_version")


def _hospital(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    d["fallbacks"] = json.loads(d["fallbacks"] or "[]")
    return d


def get_hospital(code: str) -> dict | None:
    return _hospital(conn().execute("SELECT * FROM hospitals WHERE code = ?", (code,)).fetchone())


def list_hospitals() -> list[dict]:
    return [_hospital(r) for r in conn().execute("SELECT * FROM hospitals ORDER BY code")]


def save_hospital(code: str, values: dict, *, create: bool) -> None:
    fields = {k: values[k] for k in HOSPITAL_FIELDS if k in values}
    if "fallbacks" in fields:
        fields["fallbacks"] = json.dumps(list(fields["fallbacks"]))
    stamp = now()
    if create:
        cols = ["code", *fields, "created_at", "updated_at"]
        conn().execute(
            f"INSERT INTO hospitals ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            (code, *fields.values(), stamp, stamp),
        )
    else:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn().execute(f"UPDATE hospitals SET {sets}, updated_at = ? WHERE code = ?",
                       (*fields.values(), stamp, code))


# ── HQ certificates (public halves only; the HQ's private key is never kept) ──

def record_certificate(hospital: str, public_key: str, urls: list[str], issued_at: str,
                       expires_at: str, issued_by: str) -> None:
    conn().execute(
        "INSERT INTO hq_certificates (hospital, public_key, urls, issued_at, expires_at, issued_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (hospital, public_key, json.dumps(urls), issued_at, expires_at, issued_by),
    )


def latest_certificate(hospital: str) -> dict | None:
    row = conn().execute("SELECT * FROM hq_certificates WHERE hospital = ? ORDER BY id DESC LIMIT 1",
                         (hospital,)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["urls"] = json.loads(d["urls"])
    return d
