"""Cirqen Control's own records: hospitals, admin accounts, sessions, the
certificates issued to each hospital's HQ, and the audit log.

Where they live:
  CONTROL_DATABASE_URL set   PostgreSQL (e.g. a Supabase project of its
                             own). For hosts without a persistent disk,
                             such as Render's free plan.
  otherwise                  SQLite at CONTROL_DB (beside HQ_STATE_DB), on
                             the service's persistent disk, like store.py.
Callers write SQLite-style SQL ("?" placeholders); _PgConnection adapts it.
It never holds hospital data (equipment, job cards, calibration): that
lives only in each hospital's own HQ database.

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


def database_url() -> str:
    return os.environ.get("CONTROL_DATABASE_URL", "").strip()


def is_postgres() -> bool:
    return bool(database_url())


def available() -> bool:
    """Whether Control has its records store yet (callers that only read)."""
    return is_postgres() or db_path().exists()


# Tables whose id is generated: an INSERT into them returns it (lastrowid).
_SERIAL_TABLES = {"admins", "login_attempts", "hq_certificates", "audit", "invoices", "payments"}


class _PgCursor:
    def __init__(self, cur, lastrowid=None):
        self._cur, self.lastrowid = cur, lastrowid

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur.fetchall() if self._cur.description else [])


class _PgConnection:
    """The few sqlite3.Connection calls this code uses, on psycopg2:
    "?" placeholders, INSERT OR IGNORE, lastrowid, executescript. Autocommit,
    like the SQLite connection (isolation_level=None)."""

    def __init__(self, url):
        import psycopg2

        self._psycopg2 = psycopg2
        self._url = url
        self._connect()

    def _connect(self):
        from psycopg2.extras import DictCursor

        self._conn = self._psycopg2.connect(self._url, cursor_factory=DictCursor, connect_timeout=15)
        self._conn.autocommit = True

    def _translate(self, sql):
        text = sql.replace("?", "%s")
        if text.lstrip().upper().startswith("INSERT OR IGNORE"):
            text = text.replace("INSERT OR IGNORE", "INSERT", 1) + " ON CONFLICT DO NOTHING"
        return text

    def execute(self, sql, params=()):
        text = self._translate(sql)
        returning = None
        head = text.lstrip().upper()
        if head.startswith("INSERT INTO ") and "RETURNING" not in head:
            table = text.lstrip().split()[2].split("(")[0].lower()
            if table in _SERIAL_TABLES:
                text += " RETURNING id"
                returning = True
        for attempt in (1, 2):
            try:
                cur = self._conn.cursor()
                cur.execute(text, tuple(params))
                break
            except (self._psycopg2.OperationalError, self._psycopg2.InterfaceError):
                # Supabase closes idle connections; reconnect once.
                if attempt == 2 or not self._conn.closed:
                    raise
                self._connect()
        lastrowid = cur.fetchone()[0] if returning else None
        return _PgCursor(cur, lastrowid)

    def executescript(self, script):
        self._conn.cursor().execute(script)


def _pg_schema(schema: str) -> str:
    return (schema.replace("INTEGER PRIMARY KEY,", "BIGSERIAL PRIMARY KEY,")
            .replace(" REAL NOT NULL", " DOUBLE PRECISION NOT NULL"))


def conn():
    if is_postgres():
        c = getattr(_local, "pg", None)
        if c is None or getattr(_local, "pg_url", None) != database_url() or c._conn.closed:
            c = _PgConnection(database_url())
            _local.pg, _local.pg_url = c, database_url()
        return c
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
CREATE TABLE IF NOT EXISTS enrollment_tokens (
    token_id    TEXT PRIMARY KEY,
    hospital    TEXT NOT NULL REFERENCES hospitals(code),
    document    TEXT NOT NULL,
    max_uses    INTEGER NOT NULL,
    expires_at  TEXT NOT NULL,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS licences (
    hospital         TEXT PRIMARY KEY REFERENCES hospitals(code),
    plan             TEXT NOT NULL DEFAULT 'standard',
    devices          INTEGER NOT NULL DEFAULT 10,
    starts_on        TEXT NOT NULL,
    ends_on          TEXT NOT NULL,
    grace_days       INTEGER NOT NULL DEFAULT 14,
    status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended')),
    licence_version  INTEGER NOT NULL DEFAULT 1,
    updated_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS invoices (
    id           INTEGER PRIMARY KEY,
    number       TEXT NOT NULL UNIQUE,
    hospital     TEXT NOT NULL REFERENCES hospitals(code),
    issued_on    TEXT NOT NULL,
    due_on       TEXT NOT NULL,
    months       INTEGER NOT NULL,
    amount_kes   INTEGER NOT NULL CHECK (amount_kes > 0),
    description  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'paid', 'void')),
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payments (
    id               INTEGER PRIMARY KEY,
    hospital         TEXT NOT NULL REFERENCES hospitals(code),
    invoice_id       INTEGER REFERENCES invoices(id),
    amount_kes       INTEGER NOT NULL,
    method           TEXT NOT NULL CHECK (method IN ('mpesa', 'bank', 'cash', 'other')),
    reference        TEXT NOT NULL,
    paid_on          TEXT NOT NULL,
    months_credited  INTEGER NOT NULL DEFAULT 0,
    reverses         INTEGER REFERENCES payments(id),
    note             TEXT NOT NULL DEFAULT '',
    recorded_by      TEXT NOT NULL,
    recorded_at      TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS payments_reference ON payments (method, reference) WHERE reverses IS NULL;
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
    ("hospitals", "profile", "TEXT NOT NULL DEFAULT '{}'"),            # modules + labels (profiles.py)
    ("hospitals", "profile_version", "INTEGER NOT NULL DEFAULT 0"),    # 0: no profile published
    ("enrollment_tokens", "revoked_at", "TEXT"),                      # set: its PCs can no longer join
    ("hospitals", "shared_key_retired_at", "TEXT"),                    # set: its HQ refuses the old shared sync key
]


def init() -> None:
    if is_postgres():
        c = conn()
        c.executescript(_pg_schema(SCHEMA))
        for table, column, definition in ADDED_COLUMNS:
            c.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
        return
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
                   "updates_url", "cert_prefix", "status", "notes", "release_mode", "release_version",
                   "profile", "profile_version", "shared_key_retired_at")


def _hospital(row) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    d["fallbacks"] = json.loads(d["fallbacks"] or "[]")
    d["profile"] = json.loads(d.get("profile") or "{}")
    return d


def get_hospital(code: str) -> dict | None:
    return _hospital(conn().execute("SELECT * FROM hospitals WHERE code = ?", (code,)).fetchone())


def list_hospitals() -> list[dict]:
    return [_hospital(r) for r in conn().execute("SELECT * FROM hospitals ORDER BY code")]


def save_hospital(code: str, values: dict, *, create: bool) -> None:
    fields = {k: values[k] for k in HOSPITAL_FIELDS if k in values}
    if "fallbacks" in fields:
        fields["fallbacks"] = json.dumps(list(fields["fallbacks"]))
    if "profile" in fields:
        fields["profile"] = json.dumps(fields["profile"], sort_keys=True)
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


# ── enrollment tokens (the signed document; the token is re-derived from it) ──

def record_enrollment_token(token_id: str, hospital: str, document: str, max_uses: int, expires_at: str,
                            created_by: str) -> None:
    conn().execute(
        "INSERT INTO enrollment_tokens (token_id, hospital, document, max_uses, expires_at, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (token_id, hospital, document, max_uses, expires_at, created_by, now()),
    )


def enrollment_tokens(hospital: str) -> list[dict]:
    return [dict(r) for r in conn().execute(
        "SELECT * FROM enrollment_tokens WHERE hospital = ? ORDER BY created_at DESC", (hospital,))]


def enrollment_token(token_id: str) -> dict | None:
    row = conn().execute("SELECT * FROM enrollment_tokens WHERE token_id = ?", (token_id,)).fetchone()
    return dict(row) if row else None


def revoke_enrollment_token(token_id: str) -> bool:
    cur = conn().execute("UPDATE enrollment_tokens SET revoked_at = ? WHERE token_id = ? AND revoked_at IS NULL",
                         (now(), token_id))
    return bool(getattr(cur, "rowcount", 0) or (enrollment_token(token_id) or {}).get("revoked_at"))
