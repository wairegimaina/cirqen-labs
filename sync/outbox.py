"""
Outbox-based change data capture (CDC) for the sync agent.

Replaces timestamp-polling change detection with a database-trigger outbox:

    every INSERT / UPDATE / DELETE on a synced table  ->  one row in sync_outbox

The outbox has a BIGSERIAL ``seq`` primary key, so the agent reads changes with
``WHERE seq > last_seq ORDER BY seq`` — a monotonic integer high-water mark.
This fixes three defects of the timestamp poller in one move:

  * HARD DELETES are captured (a DELETE trigger fires; there is no surviving
    ``updated_at`` for the poller to find).
  * CLOCK SKEW is irrelevant — ordering is by an integer, not wall-clock time.
  * The ``updated_at > checkpoint`` BOUNDARY SKIP (rows sharing the checkpoint
    timestamp) cannot happen: seq values are unique and strictly increasing.

This is OPT-IN behind ``sync.use_outbox`` (default False). Enabling it installs
the trigger infrastructure on next agent start; the legacy poller is untouched
until you flip the flag.

Known limitation (documented, low-risk for a single-writer desktop DB):
a transaction that acquires a lower seq but commits AFTER a higher-seq
transaction could be momentarily skipped by ``seq > last_seq``. For concurrent
multi-writer databases, a snapshot-aware reader (or logical replication) is the
correct upgrade. See PHASE2_OUTBOX.md.
"""
import uuid
import time
from typing import Any, Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor

from .agent_prelude import LOG, format_kenyan_time, now_kenyan
from .event_identity import stable_event_id


# ── Schema + trigger DDL (all idempotent) ─────────────────────────────────────

_OUTBOX_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sync_outbox (
    seq         BIGSERIAL   PRIMARY KEY,
    table_name  TEXT        NOT NULL,
    row_id      TEXT,
    op          TEXT        NOT NULL,        -- 'u' (insert/update) | 'd' (delete)
    payload     JSONB       NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sync_outbox_seq ON sync_outbox (seq);
"""

# Generic capture function: fires AFTER row change, writes one outbox row.
# Returns NULL because AFTER-trigger return values are ignored.
_OUTBOX_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION cirqen_outbox_capture() RETURNS trigger AS $CIRQEN$
DECLARE
    v_row jsonb;
BEGIN
    IF (TG_OP = 'DELETE') THEN
        v_row := to_jsonb(OLD);
    ELSE
        v_row := to_jsonb(NEW);
    END IF;

    INSERT INTO sync_outbox (table_name, row_id, op, payload)
    VALUES (
        TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME,
        v_row->>'id',
        CASE TG_OP WHEN 'DELETE' THEN 'd' ELSE 'u' END,
        v_row
    );
    RETURN NULL;
END;
$CIRQEN$ LANGUAGE plpgsql;
"""

# Trigger name is prefixed so it sorts/fires after app triggers (e.g. updated_at).
_TRIGGER_NAME = "zzz_cirqen_outbox"


class OutboxMixin:
    """Trigger-based CDC. Opt-in via ``sync.use_outbox``."""

    # -- config helpers -------------------------------------------------------

    def outbox_enabled(self) -> bool:
        import os
        cfg = getattr(self, "sync_cfg", {}) or {}
        if "use_outbox" in cfg:
            return bool(cfg["use_outbox"])
        return os.getenv("SYNC_USE_OUTBOX", "0") == "1"

    # -- installation ---------------------------------------------------------

    @staticmethod
    def _split_qualified(table: str) -> Tuple[str, str]:
        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table
        return schema, tbl

    def install_outbox(self) -> bool:
        """
        Create the outbox table, capture function, and per-table triggers.
        Fully idempotent — safe to call on every agent start. Requires DDL
        privileges (the local cirqen role is a superuser).
        """
        conn = None
        installed, skipped = 0, 0
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute(_OUTBOX_TABLE_SQL)
                cur.execute(_OUTBOX_FUNCTION_SQL)

                for table in self.tables:
                    schema, tbl = self._split_qualified(table)
                    # Only attach to tables that actually exist locally.
                    cur.execute("SELECT to_regclass(%s)", (f'"{schema}"."{tbl}"',))
                    if cur.fetchone()[0] is None:
                        skipped += 1
                        LOG.debug("   ⏭️  outbox: table missing locally, skipping %s", table)
                        continue

                    ident = f'"{schema}"."{tbl}"'
                    cur.execute(f'DROP TRIGGER IF EXISTS {_TRIGGER_NAME} ON {ident}')
                    cur.execute(
                        f'CREATE TRIGGER {_TRIGGER_NAME} '
                        f'AFTER INSERT OR UPDATE OR DELETE ON {ident} '
                        f'FOR EACH ROW EXECUTE FUNCTION cirqen_outbox_capture()'
                    )
                    installed += 1
            conn.commit()
            LOG.info("✅ Outbox installed: %d trigger(s) attached, %d table(s) skipped",
                     installed, skipped)
            return True
        except Exception as exc:
            if conn:
                conn.rollback()
            LOG.error("❌ Outbox install failed: %s", exc)
            return False
        finally:
            if conn:
                self.pool.putconn(conn)

    # -- checkpoint (monotonic integer high-water mark) -----------------------

    def get_last_outbox_seq(self) -> int:
        try:
            return int(self.state.get("last_outbox_seq", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def set_last_outbox_seq(self, seq: int) -> None:
        self.state.set("last_outbox_seq", int(seq))

    def outbox_pending_count(self) -> int:
        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.sync_outbox') AS t")
                if cur.fetchone()[0] is None:
                    return 0
                cur.execute("SELECT count(*) FROM sync_outbox WHERE seq > %s",
                            (self.get_last_outbox_seq(),))
                return int(cur.fetchone()[0])
        except psycopg2.Error:
            return 0
        finally:
            if conn:
                self.pool.putconn(conn)

    # -- reading changes ------------------------------------------------------

    def _event_from_outbox_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Map an outbox row to the event shape the uploader already sends."""
        payload = row["payload"] or {}
        client_id = getattr(self, "client_id", None)
        # Version by the row's updated_at when it has one, so the outbox and the
        # timestamp poller produce the same id for the same row version.
        version = (payload.get("updated_at") if isinstance(payload, dict) else None) \
            or f"outbox:{client_id}:{row['seq']}"
        event = {
            "event_id": stable_event_id(row["table_name"], row["row_id"], version, row["op"]),
            # Deterministic idempotency key: stable across retries because seq is
            # immutable. HQ can dedupe on this so a re-sent batch is a no-op.
            "idempotency_key": f"{getattr(self, 'client_id', 'unknown')}:{row['seq']}",
            "table": row["table_name"],
            "row_id": str(row["row_id"]) if row["row_id"] is not None else None,
            "operation": row["op"],  # 'u' | 'd'
            "data": payload,
            "created_at": (row["created_at"].isoformat()
                           if hasattr(row["created_at"], "isoformat") else str(row["created_at"])),
            "source": client_id or "local",
            "machine_id": getattr(self, "machine_id", None),
            "_outbox_seq": row["seq"],
        }
        # Carry status columns through if the table has them (HQ apply reads these).
        if isinstance(payload, dict):
            if "active_status" in payload:
                event["active_status"] = payload["active_status"]
            if "pending_delete" in payload:
                event["pending_delete"] = payload["pending_delete"]
        return event

    def fetch_outbox_batch(self, last_seq: int, limit: int) -> Tuple[List[Dict[str, Any]], int]:
        """
        Return (events, max_seq) for outbox rows with seq > last_seq, in order.
        max_seq is the highest seq included (== last_seq when empty).
        """
        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT seq, table_name, row_id, op, payload, created_at
                    FROM sync_outbox
                    WHERE seq > %s
                    ORDER BY seq ASC
                    LIMIT %s
                    """,
                    (last_seq, limit),
                )
                rows = cur.fetchall()
            if not rows:
                return [], last_seq
            events = [self._event_from_outbox_row(r) for r in rows]
            max_seq = rows[-1]["seq"]
            return events, max_seq
        except Exception as exc:
            LOG.error("❌ fetch_outbox_batch failed: %s", exc)
            return [], last_seq
        finally:
            if conn:
                self.pool.putconn(conn)

    def prune_outbox(self, keep_seq: int) -> int:
        """Delete acknowledged outbox rows (seq <= keep_seq). Returns rows removed."""
        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("DELETE FROM sync_outbox WHERE seq <= %s", (keep_seq,))
                removed = cur.rowcount
            conn.commit()
            return removed
        except Exception as exc:
            if conn:
                conn.rollback()
            LOG.debug("outbox prune skipped: %s", exc)
            return 0
        finally:
            if conn:
                self.pool.putconn(conn)

    # -- main loop ------------------------------------------------------------

    def outbox_upload_loop(self) -> None:
        """
        Outbox replacement for the polling upload loop. Uploads batches in seq
        order and advances the checkpoint ONLY for rows HQ accepted, so a failed
        batch is retried rather than skipped.
        """
        LOG.info("=" * 80)
        LOG.info("⚡ OUTBOX CDC MODE (trigger-based change capture)")
        LOG.info("=" * 80)

        if not self.install_outbox():
            LOG.error("❌ Outbox not installed — falling back to legacy polling loop")
            return self._upload_loop_legacy()

        batch_size = int(self.sync_cfg.get("upload_batch_size", 50))
        poll_interval = max(1, int(self.sync_cfg.get("poll_interval", 1)))
        prune = bool(self.sync_cfg.get("outbox_prune", True))
        # Backpressure: cap batches uploaded per online cycle so a huge backlog
        # can't monopolize the loop (we yield to re-check online/stop between).
        max_batches_per_cycle = int(self.sync_cfg.get("outbox_max_batches_per_cycle", 20))
        throttle_backoff = 0.0  # grows on HTTP 429/503, decays on success

        last_online_check = 0.0
        online_check_interval = 30
        is_online = False
        loop_count = 0

        while not self.stop_event.is_set():
            try:
                loop_count += 1
                now = time.time()
                if now - last_online_check >= online_check_interval:
                    was_online = is_online
                    is_online = self.check_hq_online()
                    last_online_check = now
                    if is_online and not was_online:
                        LOG.info("✅ HQ RECONNECTED at %s", format_kenyan_time(now_kenyan()))
                    elif not is_online and was_online:
                        LOG.warning("⚠️  HQ OFFLINE at %s", format_kenyan_time(now_kenyan()))

                if not is_online:
                    self._outbox_sleep(poll_interval)
                    continue

                # Drain the outbox in seq order, batch by batch (capped per cycle).
                drained = 0
                batches = 0
                while not self.stop_event.is_set() and batches < max_batches_per_cycle:
                    last_seq = self.get_last_outbox_seq()
                    events, max_seq = self.fetch_outbox_batch(last_seq, batch_size)
                    if not events:
                        break

                    success, error = self.upload_batch(events)
                    if not success:
                        if error and str(error).startswith("throttled:"):
                            # HQ is overloaded — honor Retry-After / exponential backoff.
                            throttle_backoff = self._throttle_delay(error, throttle_backoff)
                            LOG.warning("⏳ Outbox backing off %.1fs (seq %s..%s)",
                                        throttle_backoff, last_seq, max_seq)
                            self._outbox_sleep(int(throttle_backoff) or 1)
                        else:
                            LOG.warning("⚠️  Outbox batch upload failed (seq %s..%s): %s — will retry",
                                        last_seq, max_seq, error)
                        break  # do NOT advance checkpoint; retry next cycle

                    throttle_backoff = 0.0  # success clears backpressure
                    self.set_last_outbox_seq(max_seq)
                    drained += len(events)
                    batches += 1
                    if prune:
                        self.prune_outbox(max_seq)

                    if len(events) < batch_size:
                        break  # outbox emptied

                if drained:
                    LOG.info("📤 Outbox drained %d change(s) up to seq %s",
                             drained, self.get_last_outbox_seq())

                if drained or loop_count % 30 == 1:
                    try:
                        self.write_status_file(
                            hq_online=is_online,
                            pending_changes=self.outbox_pending_count(),
                        )
                    except Exception:
                        pass

            except Exception as exc:
                LOG.exception("💥 Outbox loop error: %s", exc)

            self._outbox_sleep(poll_interval)

        LOG.info("Outbox upload loop exiting")

    def _outbox_sleep(self, seconds: int) -> None:
        for _ in range(max(1, seconds)):
            if self.stop_event.is_set():
                return
            time.sleep(1)

    @staticmethod
    def _throttle_delay(error: str, current: float) -> float:
        """
        Next backoff delay after a throttle response. Honors a server-supplied
        Retry-After (``throttled:<code>:<retry_after>``); otherwise exponential
        1→2→4…→60s, capped.
        """
        try:
            parts = str(error).split(":")
            retry_after = parts[2] if len(parts) > 2 else ""
            if retry_after:
                return min(float(retry_after), 60.0)
        except (ValueError, IndexError):
            pass
        return min((current * 2) if current else 1.0, 60.0)
