"""
Schema-drift guard.

When HQ sends a column the local table does not have (a migration applied at HQ
but not yet on this client), the applier used to drop the column with a per-row
WARNING that scrolled away — silent partial writes.

This records each distinct drift once to a durable ``sync_schema_drift`` table
(deduplicated by table + missing-column set), escalates the log to ERROR, and
exposes a count for the status file so the condition is *visible* and can be
acted on (apply the pending migration) instead of quietly corrupting rows.
"""
from typing import Iterable, List

import psycopg2

from .agent_prelude import LOG, now_utc


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS sync_schema_drift (
    id            BIGSERIAL PRIMARY KEY,
    table_name    TEXT NOT NULL,
    missing_cols  TEXT NOT NULL,          -- comma-joined, sorted
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    hit_count     BIGINT NOT NULL DEFAULT 1,
    sample_row_id TEXT,
    resolved      BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (table_name, missing_cols)
);
"""


class SchemaGuardMixin:
    """Durable, deduplicated recording of schema drift."""

    def ensure_schema_drift_table(self) -> bool:
        if getattr(self, "_schema_drift_ready", False):
            return True
        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute(_CREATE_SQL)
            conn.commit()
            self._schema_drift_ready = True
            return True
        except Exception as exc:
            if conn:
                conn.rollback()
            LOG.error("⚠️  Could not ensure sync_schema_drift table: %s", exc)
            return False
        finally:
            if conn:
                self.pool.putconn(conn)

    def record_schema_drift(self, table: str, missing_cols: Iterable[str], row_id=None) -> bool:
        """
        Record (or bump) a drift incident. Loud on first sight, deduplicated after.
        Never raises — recording must not break sync.
        """
        cols: List[str] = sorted({str(c) for c in missing_cols})
        if not cols:
            return True
        key = ",".join(cols)

        if not self.ensure_schema_drift_table():
            return False

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sync_schema_drift (table_name, missing_cols, sample_row_id, first_seen, last_seen)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (table_name, missing_cols) DO UPDATE
                        SET hit_count = sync_schema_drift.hit_count + 1,
                            last_seen = EXCLUDED.last_seen,
                            resolved  = FALSE
                    RETURNING (xmax = 0) AS is_new
                    """,
                    (table, key, str(row_id) if row_id is not None else None, now_utc(), now_utc()),
                )
                is_new = cur.fetchone()[0]
            conn.commit()
            if is_new:
                LOG.error(
                    "🧱 SCHEMA DRIFT on %s: local table is missing column(s) %s that HQ is sending. "
                    "These fields are being DROPPED on apply — run the pending migration on this client.",
                    table, key,
                )
            else:
                LOG.debug("schema drift bumped: %s missing %s", table, key)
            return True
        except Exception as exc:
            if conn:
                conn.rollback()
            LOG.error("❌ Failed to record schema drift for %s (%s): %s", table, key, exc)
            return False
        finally:
            if conn:
                self.pool.putconn(conn)

    def get_schema_drift_count(self, unresolved_only: bool = True) -> int:
        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.sync_schema_drift') AS t")
                if cur.fetchone()[0] is None:
                    return 0
                if unresolved_only:
                    cur.execute("SELECT count(*) FROM sync_schema_drift WHERE resolved = FALSE")
                else:
                    cur.execute("SELECT count(*) FROM sync_schema_drift")
                return int(cur.fetchone()[0])
        except psycopg2.Error:
            return 0
        finally:
            if conn:
                self.pool.putconn(conn)
