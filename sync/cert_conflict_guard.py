"""
sync.cert_conflict_guard — self-healing certificate_number conflict resolution.

Two machines calibrating offline can each mint the same next-in-sequence
BNH-NNNN certificate number for two different session rows before either
side has synced. Once both reach HQ, one of those rows becomes an "orphan":
a different UUID holding a certificate_number that HQ already considers
authoritative for a different row — the exact "BNH-0093 problem" that
`helper_scripts/fix_cert_conflicts.py` and `helper_scripts/cert_checker.py`
were written to diagnose and fix by hand.

This mixin folds that logic into the regular sync loop so it self-heals
instead of needing someone to notice and run the script manually:

  1. HQ's certificate_number is authoritative — restore it on the local row
     sharing the HQ row's id.
  2. The local orphan row (different id, same certificate_number) is
     reassigned the next free, non-conflicting BNH-NNNN number.

Both writes go through the local pool and bump ``updated_at``, so the
existing upload pipeline picks them up and pushes them to HQ on its
normal schedule. No new HQ write path is introduced — HQ is only ever
read here, matching how the rest of the sync agent treats HQ as
authoritative and reaches it exclusively through the sync API upload flow.
"""
import os
import threading
import time

import psycopg2
from psycopg2.extras import RealDictCursor

from .agent_prelude import LOG, now_utc

# config.HQ_ENDPOINT_DEFAULTS names the database "database"; psycopg2 wants
# "dbname". Only these fields can arrive from the update server — a password
# never travels in that document, so it always comes from local config.
HQ_DB_REMOTE_FIELDS = {
    "hq_db.host": "host",
    "hq_db.port": "port",
    "hq_db.database": "dbname",
    "hq_db.user": "user",
    "hq_db.sslmode": "sslmode",
}

SESSION_TABLE = 'public."CalSoft_calibrationsession"'


class CertConflictGuardMixin:
    """Periodically detects and repairs cross-DB certificate_number clashes."""

    @staticmethod
    def _cert_guard_record_reissue(cur, session_id, *, superseded, reissued_as):
        """Write an audit entry for a certificate that changed number.

        Best effort: a repair must not be abandoned because the audit table is
        unavailable, but the failure is logged rather than swallowed so a
        missing trail is visible.
        """
        try:
            cur.execute(
                'INSERT INTO public."CalSoft_calibrationauditlog" '
                '(id, action, description, timestamp, session_id, active_status) '
                "VALUES (gen_random_uuid(), %s, %s, %s, %s, TRUE)",
                (
                    "certificate_reissued",
                    f"Certificate {superseded} was reallocated at HQ; this session "
                    f"was reissued as {reissued_as}. Any document printed as "
                    f"{superseded} for this session is superseded.",
                    now_utc(),
                    session_id,
                ),
            )
        except Exception as exc:
            LOG.error(
                "   cert_conflict_guard: could not record the reissue of %s → %s "
                "for session %s: %s",
                superseded, reissued_as, session_id, exc,
            )

    def _cert_guard_hq_conn(self):
        """Short-lived, read-only connection to HQ used for conflict detection only."""
        hq_config = dict(self.config["hq_db"])
        hq_config.pop("enabled", None)

        # Resolve at connection time, not from the snapshot taken at start-up,
        # so a database move adopted from the update server lands without
        # waiting for a restart — matching how the sync address already
        # behaves. Falls back to the start-up copy if anything goes wrong.
        try:
            from config import resolve_endpoints

            resolved = resolve_endpoints(self.data_path)
            for key, field in HQ_DB_REMOTE_FIELDS.items():
                value, source = resolved.get(key, (None, None))
                # ONLY a value the update server actually sent. Applying any
                # other layer here would overwrite the settings this agent
                # started with using a freshly-resolved default, which is a
                # different thing entirely and not what a fleet move means.
                if source == "remote":
                    hq_config[field] = value
        except (ImportError, OSError, ValueError, AttributeError) as exc:
            # Narrow on purpose: a broad except here hid a real programming
            # error behind a silent fall back to the start-up settings.
            LOG.warning("Using start-up HQ database settings (%s)", exc)

        hq_config.setdefault("sslmode", os.getenv("POSTGRES_SSLMODE", "require"))
        conn = psycopg2.connect(**hq_config, connect_timeout=15)
        conn.autocommit = True
        return conn

    @staticmethod
    def _cert_guard_find_conflicts(hq_conn, local_conn):
        """Return [(local_orphan_id, hq_authoritative_id, cert_number), ...]."""
        with hq_conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"""
                SELECT id::text AS id, certificate_number
                FROM {SESSION_TABLE}
                WHERE certificate_number IS NOT NULL AND certificate_number != ''
            """)
            hq_map = {r["certificate_number"]: r["id"] for r in cur.fetchall()}

        with local_conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(f"""
                SELECT id::text AS id, certificate_number
                FROM {SESSION_TABLE}
                WHERE certificate_number IS NOT NULL AND certificate_number != ''
            """)
            local_map = {r["certificate_number"]: r["id"] for r in cur.fetchall()}

        conflicts = []
        for cert_num, hq_id in hq_map.items():
            local_id = local_map.get(cert_num)
            if local_id and local_id != hq_id:
                conflicts.append((local_id, hq_id, cert_num))
        return conflicts

    @staticmethod
    def _cert_guard_max_seq(cur):
        cur.execute(f"""
            SELECT certificate_number FROM {SESSION_TABLE}
            WHERE certificate_number ~ '^BNH-[0-9]+$'
            ORDER BY CAST(SPLIT_PART(certificate_number, '-', 2) AS INT) DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        return int(row[0].split("-")[1]) if row and row[0] else 0

    @staticmethod
    def _cert_guard_exists(cur, cert_num):
        cur.execute(f"SELECT 1 FROM {SESSION_TABLE} WHERE certificate_number = %s", (cert_num,))
        return cur.fetchone() is not None

    def resolve_certificate_conflicts(self) -> int:
        """
        Detect + repair cross-DB certificate_number clashes.

        Returns the number of conflicts fixed (0 if none found or on error —
        errors are logged and swallowed so a bad HQ read never takes down the
        rest of the sync agent).
        """
        local_conn = None
        hq_conn = None
        fixed = 0
        try:
            local_conn = self.pool.getconn()
            hq_conn = self._cert_guard_hq_conn()

            conflicts = self._cert_guard_find_conflicts(hq_conn, local_conn)
            if not conflicts:
                return 0

            LOG.warning(
                "🔎 cert_conflict_guard: found %d certificate_number clash(es) — repairing",
                len(conflicts),
            )

            with local_conn.cursor(cursor_factory=RealDictCursor) as cur, \
                 hq_conn.cursor() as hq_cur:

                next_n = max(self._cert_guard_max_seq(cur), self._cert_guard_max_seq(hq_cur)) + 1

                for orphan_id, hq_id, cert_num in conflicts:
                    cur.execute(f"SELECT * FROM {SESSION_TABLE} WHERE id = %s", (orphan_id,))
                    orphan_row = cur.fetchone()
                    cur.execute(f"SELECT * FROM {SESSION_TABLE} WHERE id = %s", (hq_id,))
                    hq_local_row = cur.fetchone()

                    if not orphan_row:
                        LOG.debug("   cert_conflict_guard: orphan %s not present locally, skipping", orphan_id)
                        continue

                    # Allocate the orphan's replacement number before touching anything.
                    candidate = f"BNH-{next_n:04d}"
                    while self._cert_guard_exists(cur, candidate) or self._cert_guard_exists(hq_cur, candidate):
                        next_n += 1
                        candidate = f"BNH-{next_n:04d}"
                    next_n += 1

                    # 1) Clear the orphan's certificate_number to release the UNIQUE constraint.
                    cur.execute(
                        f"UPDATE {SESSION_TABLE} SET certificate_number = NULL, updated_at = %s WHERE id = %s",
                        (now_utc(), orphan_id),
                    )

                    # 2) Restore HQ's authoritative number on the matching local row, if present.
                    if hq_local_row and hq_local_row.get("certificate_number") != cert_num:
                        cur.execute(
                            f"UPDATE {SESSION_TABLE} SET certificate_number = %s, updated_at = %s WHERE id = %s",
                            (cert_num, now_utc(), hq_id),
                        )

                    # 3) Give the orphan its own number.
                    #
                    # This is a REISSUE, not a silent renumber. The orphan may
                    # already have had a certificate printed and filed under
                    # `cert_num`; quietly moving it to another number would
                    # leave the filed document and the record disagreeing, with
                    # nothing but a log line to show it happened.
                    #
                    # So the superseded number is recorded on the row and an
                    # audit entry is written. A certificate that was issued and
                    # then superseded is a normal, documentable event; one that
                    # changed number with no trace is not.
                    superseded_note = (
                        f"Certificate {cert_num} was allocated to another session "
                        f"at HQ. This session has been reissued as {candidate}. "
                        f"Any document already printed as {cert_num} for this "
                        f"session is superseded and must be withdrawn."
                    )
                    cur.execute(
                        f"UPDATE {SESSION_TABLE} "
                        f"SET certificate_number = %s, updated_at = %s, "
                        f"    notes = COALESCE(NULLIF(notes, '') || E'\n\n', '') || %s "
                        f"WHERE id = %s",
                        (candidate, now_utc(), superseded_note, orphan_id),
                    )
                    self._cert_guard_record_reissue(
                        cur, orphan_id, superseded=cert_num, reissued_as=candidate
                    )

                    LOG.warning(
                        "   ⚠️  cert_conflict_guard: %s stays with %s…; orphan %s… REISSUED "
                        "%s → %s (superseded, recorded on the session)",
                        cert_num, hq_id[:8], orphan_id[:8], cert_num, candidate,
                    )
                    fixed += 1

            local_conn.commit()
            if fixed:
                LOG.warning(
                    "🔧 cert_conflict_guard: repaired %d certificate_number clash(es); "
                    "changes will reach HQ on the next upload pass", fixed,
                )

        except Exception as e:
            if local_conn:
                local_conn.rollback()
            LOG.error("💥 cert_conflict_guard: repair pass failed: %s", e)
            LOG.exception(e)
        finally:
            if hq_conn:
                hq_conn.close()
            if local_conn:
                self.pool.putconn(local_conn)

        return fixed

    def cert_conflict_guard_loop(self):
        """Background thread: periodically re-checks for certificate_number clashes."""
        interval = int(
            self.sync_cfg.get(
                "cert_conflict_check_interval",
                os.getenv("CERT_CONFLICT_CHECK_INTERVAL", "1800"),
            )
        )

        LOG.info(
            "🛡️  Certificate conflict guard started (checking every %ds / %.1fm)",
            interval, interval / 60,
        )

        # Give the agent time to finish its initial sync before the first check.
        # Jittered, not a flat 60s: this loop opens a direct connection to the
        # HQ database, and a site that powers on its desktops together would
        # otherwise have all of them connect in the same second. Spreading the
        # first check over several minutes turns a spike into a trickle, which
        # is what lets the fleet grow without the pooler refusing connections.
        import random

        for _ in range(int(random.uniform(60, 300))):
            if self.stop_event.is_set():
                return
            time.sleep(1)

        while not self.stop_event.is_set():
            try:
                self.resolve_certificate_conflicts()
            except Exception as e:
                LOG.error("💥 cert_conflict_guard loop error: %s", e)
                LOG.exception(e)

            for _ in range(interval):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

        LOG.info("🛡️  Certificate conflict guard exiting")
