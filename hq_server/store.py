"""
Persistent HQ state — machine registry + migration lock (SQLite).

#9 hardening. Replaces the previous in-memory dicts, which reset on every
worker restart and (being ``threading.Lock``) only worked inside one process.
SQLite serialises writes, so the migration lock is atomic across all processes
and workers that share this file.

Multi-instance note: point ``HQ_STATE_DB`` at a path on a Render *persistent
disk* to survive deploys. For a horizontally-scaled deployment (2+ instances
with separate filesystems) swap this module's DB URL for the shared HQ
PostgreSQL — the function signatures are the seam; callers don't change.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_DB_PATH = Path(os.environ.get("HQ_STATE_DB", Path(__file__).parent / "hq_state.db"))
_local = threading.local()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(str(_DB_PATH), timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=30000")
        _local.conn = c
    return c


def init() -> None:
    c = _conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS machines (
            machine_id      TEXT PRIMARY KEY,
            hostname        TEXT,
            current_version TEXT,
            location        TEXT,
            registered_at   TEXT,
            last_check      TEXT,
            last_seen       TEXT,
            last_status     TEXT,
            last_error      TEXT
        );
        CREATE TABLE IF NOT EXISTS migration_lock (
            id        INTEGER PRIMARY KEY CHECK (id = 1),
            locked_by TEXT,
            locked_at TEXT,
            version   TEXT
        );
        INSERT OR IGNORE INTO migration_lock (id, locked_by, locked_at, version)
        VALUES (1, NULL, NULL, NULL);
        """
    )


# ── Machine registry ──────────────────────────────────────────────────────────

def register_machine(machine_id: str, hostname: str, current_version: str, location: str = "") -> None:
    _conn().execute(
        """
        INSERT INTO machines (machine_id, hostname, current_version, location, registered_at, last_seen)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(machine_id) DO UPDATE SET
            hostname=excluded.hostname,
            current_version=excluded.current_version,
            location=excluded.location,
            last_seen=excluded.last_seen
        """,
        (machine_id, hostname, current_version, location, _now(), _now()),
    )


def touch_check(machine_id: str, current_version: str) -> None:
    _conn().execute(
        """
        INSERT INTO machines (machine_id, current_version, last_check, last_seen, registered_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(machine_id) DO UPDATE SET
            current_version=excluded.current_version,
            last_check=excluded.last_check,
            last_seen=excluded.last_seen
        """,
        (machine_id, current_version, _now(), _now(), _now()),
    )


def record_report(machine_id: str, version: str, status: str, error: str = "") -> None:
    """#8 — record the outcome of an update attempt reported by a client."""
    _conn().execute(
        """
        INSERT INTO machines (machine_id, current_version, last_status, last_error, last_seen, registered_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(machine_id) DO UPDATE SET
            last_status=excluded.last_status,
            last_error=excluded.last_error,
            last_seen=excluded.last_seen,
            current_version=CASE WHEN excluded.last_status='success'
                                 THEN excluded.current_version ELSE machines.current_version END
        """,
        (machine_id, version, status, error[:2000], _now(), _now()),
    )


def list_machines() -> list[dict]:
    return [dict(r) for r in _conn().execute("SELECT * FROM machines ORDER BY last_seen DESC")]


# ── Migration lock (atomic via SQLite) ────────────────────────────────────────

def acquire_lock(machine_id: str, version: str, timeout_seconds: int) -> bool:
    """Atomically grant the lock if free or stale. Returns True on success."""
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT locked_by, locked_at FROM migration_lock WHERE id=1").fetchone()
        held_by, locked_at = (row["locked_by"], row["locked_at"]) if row else (None, None)
        free = held_by is None
        if not free and locked_at:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(locked_at)).total_seconds()
                free = age > timeout_seconds  # steal a stale lock
            except Exception:
                free = True
        if free or held_by == machine_id:
            c.execute(
                "UPDATE migration_lock SET locked_by=?, locked_at=?, version=? WHERE id=1",
                (machine_id, _now(), version),
            )
            c.execute("COMMIT")
            return True
        c.execute("COMMIT")
        return False
    except Exception:
        try:
            c.execute("ROLLBACK")
        except Exception:
            pass
        raise


def release_lock(machine_id: str) -> bool:
    c = _conn()
    cur = c.execute(
        "UPDATE migration_lock SET locked_by=NULL, locked_at=NULL, version=NULL "
        "WHERE id=1 AND locked_by=?",
        (machine_id,),
    )
    return cur.rowcount > 0


def lock_status() -> dict:
    row = _conn().execute("SELECT locked_by, locked_at, version FROM migration_lock WHERE id=1").fetchone()
    locked_by = row["locked_by"] if row else None
    return {
        "locked": locked_by is not None,
        "locked_by": locked_by,
        "locked_at": row["locked_at"] if row else None,
        "version": row["version"] if row else None,
    }
