"""
Background drift reconciliation.

The problem this solves
-----------------------
The upload loop only ever looks forward: it scans each table with
``WHERE updated_at > checkpoint``. Anything that ends up BEHIND a checkpoint
without having reached HQ is invisible forever — the agent has no mechanism to
notice, because "nothing newer than the checkpoint" and "fully in sync" look
identical to it.

Two real ways rows ended up stranded:

  * A single shared checkpoint across all tables (fixed in sync_agent_3/4). A
    busy table dragged it past other tables' older pending rows. This is how
    calSchedules_calibrationschedule froze at 150/3,791 and ppms_ppmschedule at
    606/3,776 while every upload reported success.
  * Events dead-lettered by HQ on a partial failure. HQ returns 200 so one bad
    row cannot block the queue, and the agent advances past them.

Both are silent. This loop is the safety net: it periodically asks HQ for its row
counts, compares them with local, and rewinds the checkpoint of any table HQ is
behind on so the normal upload loop rediscovers those rows. Re-uploading is safe
because HQ applies events as idempotent upserts.

It deliberately does NOT push rows itself — it only makes work visible to the
existing upload path, so there is exactly one code path that writes to HQ.
"""

import os
import time
from typing import Dict

from .agent_prelude import LOG

EPOCH = "1970-01-01T00:00:00+00:00"


class DriftReconcilerMixin:
    # How often to compare counts. Cheap (one HTTP round-trip plus local
    # COUNT(*)s), but not free on a small HQ instance, so default to 15 minutes.
    DRIFT_CHECK_INTERVAL_S = int(os.getenv("DRIFT_CHECK_INTERVAL_SECONDS", "900"))
    # Short: this is a desktop app, so the FIRST check is what recovers rows
    # stranded by a previous run. Long enough to let startup bootstrap settle,
    # short enough that a user restarting the app sees it heal on its own.
    DRIFT_STARTUP_DELAY_S = int(os.getenv("DRIFT_STARTUP_DELAY_SECONDS", "120"))
    # Ignore deficits below this. Counts are read at slightly different moments,
    # so a table mid-upload legitimately differs by a few rows; rewinding for
    # that would cause constant pointless re-scans.
    DRIFT_MIN_DEFICIT = int(os.getenv("DRIFT_MIN_DEFICIT", "5"))
    # Give a rewound table time to actually re-upload before considering it
    # again — otherwise every cycle rewinds it and it never makes progress.
    DRIFT_REWIND_COOLDOWN_S = int(os.getenv("DRIFT_REWIND_COOLDOWN_SECONDS", "1800"))

    # Stop rewinding a table whose deficit refuses to shrink. Not every gap is
    # closeable: calSchedules_calibrationschedule has a UNIQUE
    # (equipment_id, scheduled_month) on HQ and upload_processing deliberately
    # MERGES collisions, so N local rows can legitimately become fewer HQ rows.
    # Rewinding such a table forever just re-uploads the same rows every cycle.
    DRIFT_MAX_FUTILE_REWINDS = int(os.getenv("DRIFT_MAX_FUTILE_REWINDS", "3"))
    # Deficit must shrink by at least this fraction to count as progress.
    DRIFT_PROGRESS_FRACTION = float(os.getenv("DRIFT_PROGRESS_FRACTION", "0.02"))
    # State key for tables given up on. Persisted because the futility counters
    # live in memory: without it every app restart rewound the same structurally
    # different tables (calibration and PPM schedules) to epoch again and
    # re-uploaded thousands of unchanged rows.
    DRIFT_GIVEN_UP_KEY = "drift_given_up_tables"

    def _drift_init(self):
        self._drift_last_rewind = {}
        self._drift_rewind_counts = {}
        self._drift_last_deficit = {}
        self._drift_futile_counts = {}
        self._drift_last_checkpoint = {}
        self._drift_given_up = self._load_drift_given_up()

    def _load_drift_given_up(self):
        if os.getenv("DRIFT_RESET_GIVEN_UP", "0") == "1":
            self.state.set(self.DRIFT_GIVEN_UP_KEY, "")
            return set()
        tables = {t for t in str(self.state.get(self.DRIFT_GIVEN_UP_KEY) or "").split(",") if t}
        if tables:
            LOG.info(
                "DRIFT: not rewinding %s — given up in an earlier run "
                "(set DRIFT_RESET_GIVEN_UP=1 to try again)",
                ", ".join(sorted(tables)),
            )
        return tables

    def _drift_give_up(self, table: str):
        self._drift_given_up.add(table)
        self.state.set(self.DRIFT_GIVEN_UP_KEY, ",".join(sorted(self._drift_given_up)))

    def _drift_rewind_table(self, table: str, deficit: int):
        """Rewind one table's checkpoint so the upload loop re-scans it."""
        self.state.set(f"last_upload_time:{table}", EPOCH)
        self._drift_last_rewind[table] = time.time()
        self._drift_rewind_counts[table] = self._drift_rewind_counts.get(table, 0) + 1
        self._drift_last_deficit[table] = deficit
        # Record what the rewind WROTE, not what was there before it. Otherwise
        # the next cycle compares against a stale value, cannot see that the
        # upload loop has since advanced past EPOCH, and rewinds again — undoing
        # the walk it was supposed to be protecting.
        self._drift_last_checkpoint[table] = EPOCH
        LOG.warning(
            "♻️  DRIFT: %s is %d rows behind HQ — checkpoint rewound to epoch "
            "so the upload loop re-scans it (rewind #%d)",
            table, deficit, self._drift_rewind_counts[table],
        )

    def _drift_is_futile(self, table: str, deficit: int) -> bool:
        """
        True once a table has been rewound repeatedly without the gap closing.

        A structural deficit (server-side dedupe, rows HQ intentionally rejects)
        looks identical to a sync backlog from the outside — the only way to tell
        them apart is that re-uploading does not change the number. Giving up
        keeps the reconciler from re-sending thousands of rows every cycle
        forever, and turns a silent loop into one clear log line.
        """
        previous = self._drift_last_deficit.get(table)
        if previous is None:
            return False

        improved = previous - deficit
        if improved > max(1, previous * self.DRIFT_PROGRESS_FRACTION):
            # Real progress — reset the futility counter.
            self._drift_futile_counts.pop(table, None)
            return False

        n = self._drift_futile_counts.get(table, 0) + 1
        self._drift_futile_counts[table] = n
        if n < self.DRIFT_MAX_FUTILE_REWINDS:
            LOG.warning(
                "DRIFT: %s deficit barely moved (%d → %d) after a rewind "
                "(%d/%d before giving up)",
                table, previous, deficit, n, self.DRIFT_MAX_FUTILE_REWINDS,
            )
            return False

        LOG.error(
            "⛔ DRIFT: %s stuck at ~%d rows behind after %d rewinds — no longer "
            "retrying. This is very likely a STRUCTURAL difference, not a sync "
            "backlog: HQ merges rows that collide on a unique key (e.g. "
            "calSchedules_calibrationschedule's UNIQUE (equipment_id, "
            "scheduled_month)), so N local rows can legitimately become fewer HQ "
            "rows. Compare local COUNT(*) against COUNT(DISTINCT <unique key>) "
            "to confirm. Set DRIFT_MAX_FUTILE_REWINDS higher to keep trying.",
            table, deficit, n,
        )
        return True

    def reconcile_drift_once(self) -> Dict[str, int]:
        """
        One comparison pass. Returns {table: deficit} for tables rewound.

        Only acts when LOCAL leads HQ. The reverse (HQ ahead) is the download
        loop's job, and rewinding an upload checkpoint would do nothing for it.
        """
        rewound: Dict[str, int] = {}

        if not getattr(self, "data_checker", None):
            return rewound

        local_counts = self.data_checker.compute_local_counts()
        if not local_counts:
            return rewound

        resp = self.data_checker.compare_with_hq(local_counts)
        if not resp:
            LOG.debug("DRIFT: HQ unreachable for comparison — skipping this cycle")
            return rewound

        # HQ reports drift as out_of_sync entries, each carrying both counts:
        #   {"table": ..., "hq_count": N, "client_count": M, "direction": ...}
        # Tables it considers in sync are simply absent, which is what we want —
        # only genuine deficits get here.
        out_of_sync = resp.get("out_of_sync") or []
        if not out_of_sync:
            LOG.info("✅ DRIFT: HQ reports every table in sync")
            return rewound

        now = time.time()
        for entry in out_of_sync:
            if not isinstance(entry, dict):
                continue
            table = entry.get("table")
            hq_n = entry.get("hq_count")
            local_n = entry.get("client_count")
            if not table or not isinstance(hq_n, int) or not isinstance(local_n, int):
                continue

            # hq_count == -1 means "missing on client" — HQ leads, so this is the
            # download loop's problem and rewinding an UPLOAD checkpoint would
            # achieve nothing.
            if hq_n < 0:
                continue

            deficit = local_n - hq_n
            if deficit < self.DRIFT_MIN_DEFICIT:
                continue

            if table in self._drift_given_up:
                continue

            # ── Is the upload loop already working through this table? ────────
            # The table scan is capped (LIMIT 500), so a large backlog is walked
            # forward in pages: each pass uploads a page and advances the
            # checkpoint. Rewinding to epoch mid-walk THROWS AWAY that position
            # and restarts from the oldest rows, so the table makes progress
            # between rewinds and then loses it — the deficit never closes even
            # though uploads are succeeding. Only rewind a table that is
            # genuinely stationary.
            current_ckpt = self.state.get(f"last_upload_time:{table}")
            previous_ckpt = self._drift_last_checkpoint.get(table)
            self._drift_last_checkpoint[table] = current_ckpt

            if previous_ckpt is not None and current_ckpt != previous_ckpt:
                LOG.info(
                    "DRIFT: %s is %d behind but its checkpoint is advancing "
                    "(%s → %s) — upload loop is working through the backlog, "
                    "leaving it alone",
                    table, deficit, str(previous_ckpt)[:19], str(current_ckpt)[:19],
                )
                # Progress is real, so this is not a futile cycle.
                self._drift_futile_counts.pop(table, None)
                self._drift_last_deficit[table] = deficit
                continue

            last = self._drift_last_rewind.get(table, 0)
            if now - last < self.DRIFT_REWIND_COOLDOWN_S:
                LOG.info(
                    "DRIFT: %s still %d behind, but rewound %.0fm ago — waiting "
                    "for the re-scan to finish before trying again",
                    table, deficit, (now - last) / 60,
                )
                continue

            # Checked only after the cooldown, so the comparison is against a
            # deficit that had a full re-scan window to improve.
            if self._drift_is_futile(table, deficit):
                self._drift_give_up(table)
                continue

            self._drift_rewind_table(table, deficit)
            rewound[table] = deficit

        if not rewound:
            LOG.info("✅ DRIFT: no table is meaningfully behind HQ")
        return rewound

    def drift_reconciliation_loop(self):
        """Periodic self-heal for rows stranded behind an upload checkpoint."""
        self._drift_init()

        LOG.info(
            "🔎 Drift reconciler: first check in %dm, then every %dm",
            self.DRIFT_STARTUP_DELAY_S // 60, self.DRIFT_CHECK_INTERVAL_S // 60,
        )

        # Interruptible sleep so agent shutdown is not delayed by up to 15m.
        def _sleep(seconds):
            for _ in range(seconds):
                if self.stop_event.is_set():
                    return False
                time.sleep(1)
            return True

        if not _sleep(self.DRIFT_STARTUP_DELAY_S):
            return

        while not self.stop_event.is_set():
            try:
                if getattr(self, "check_hq_online", None) and not self.check_hq_online():
                    LOG.debug("DRIFT: HQ offline — skipping this cycle")
                else:
                    self.reconcile_drift_once()
            except Exception as exc:
                # Never let a reconciliation failure kill the thread; it is a
                # safety net and must outlive transient errors.
                LOG.error("DRIFT: reconciliation cycle failed: %s", exc, exc_info=True)

            if not _sleep(self.DRIFT_CHECK_INTERVAL_S):
                return
