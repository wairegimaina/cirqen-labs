"""
Conflict quarantine for the sync agent.

When last-write-wins resolution discards a change, the losing version is no
longer silently dropped — it is written to the local ``sync_conflicts`` table
so it can be reviewed (and, if needed, re-applied) by an operator.

This is a local diagnostic table. It is intentionally NOT in ``sync_tables``:
conflicts are per-client and must never propagate to HQ.
"""
from typing import Any, Dict, Optional

import psycopg2
from psycopg2.extras import Json

from .agent_prelude import LOG, now_utc


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS sync_conflicts (
    id                BIGSERIAL PRIMARY KEY,
    table_name        TEXT        NOT NULL,
    row_id            TEXT        NOT NULL,
    operation         TEXT,
    winner            TEXT        NOT NULL,   -- 'local' | 'remote'
    resolution        TEXT        NOT NULL,   -- e.g. 'local_newer_remote_discarded'
    local_updated_at  TIMESTAMPTZ,
    remote_updated_at TIMESTAMPTZ,
    remote_data       JSONB,                  -- the losing remote payload (if remote lost)
    local_data        JSONB,                  -- the losing local snapshot (if local lost)
    source            TEXT,
    client_id         TEXT,
    detected_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed          BOOLEAN     NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_sync_conflicts_unreviewed
    ON sync_conflicts (reviewed, detected_at DESC);
"""


class ConflictQuarantineMixin:
    """Mixin adding a durable record for last-write-wins losers."""

    def ensure_conflict_table(self) -> bool:
        """Create sync_conflicts (idempotent). Cached after first success."""
        if getattr(self, "_conflict_table_ready", False):
            return True
        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute(_CREATE_SQL)
            conn.commit()
            self._conflict_table_ready = True
            return True
        except Exception as exc:  # pragma: no cover - defensive
            if conn:
                conn.rollback()
            LOG.error("⚠️  Could not ensure sync_conflicts table: %s", exc)
            return False
        finally:
            if conn:
                self.pool.putconn(conn)

    def record_conflict(
        self,
        *,
        table: str,
        row_id: Any,
        operation: Optional[str],
        winner: str,
        resolution: str,
        local_updated_at: Any = None,
        remote_updated_at: Any = None,
        remote_data: Optional[Dict[str, Any]] = None,
        local_data: Optional[Dict[str, Any]] = None,
        source: Optional[str] = None,
    ) -> bool:
        """
        Persist a conflict loser. Uses its own short-lived connection so it is
        isolated from (and cannot be rolled back by) the caller's transaction.
        Never raises — quarantining must not break sync.
        """
        if not self.ensure_conflict_table():
            return False

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sync_conflicts (
                        table_name, row_id, operation, winner, resolution,
                        local_updated_at, remote_updated_at,
                        remote_data, local_data, source, client_id, detected_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        table,
                        str(row_id),
                        operation,
                        winner,
                        resolution,
                        local_updated_at,
                        remote_updated_at,
                        Json(remote_data) if remote_data is not None else None,
                        Json(local_data) if local_data is not None else None,
                        source,
                        getattr(self, "client_id", None),
                        now_utc(),
                    ),
                )
            conn.commit()
            LOG.warning(
                "🗄️  Conflict quarantined: %s[%s] winner=%s (%s) — loser saved to sync_conflicts",
                table, row_id, winner, resolution,
            )
            return True
        except Exception as exc:
            if conn:
                conn.rollback()
            # Last resort: at least the payload is in the log, not lost silently.
            LOG.error(
                "❌ Failed to quarantine conflict for %s[%s]: %s | remote_data=%s",
                table, row_id, exc, remote_data,
            )
            return False
        finally:
            if conn:
                self.pool.putconn(conn)

    def get_conflict_count(self, unreviewed_only: bool = True) -> int:
        """Return the number of quarantined conflicts (for status reporting)."""
        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                # The table is created on the first conflict; until then a
                # count would log an error in postgres on every status write.
                cur.execute("SELECT to_regclass('public.sync_conflicts')")
                if cur.fetchone()[0] is None:
                    return 0
                if unreviewed_only:
                    cur.execute("SELECT count(*) FROM sync_conflicts WHERE reviewed = FALSE")
                else:
                    cur.execute("SELECT count(*) FROM sync_conflicts")
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except psycopg2.Error:
            return 0
        finally:
            if conn:
                self.pool.putconn(conn)
