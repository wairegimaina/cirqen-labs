# Phase 2 — Outbox CDC (trigger-based change capture)

## What it is

An **opt-in** replacement for the sync agent's timestamp-polling change
detection. Every `INSERT / UPDATE / DELETE` on a synced table fires a trigger
that appends one row to a local `sync_outbox` table with a `BIGSERIAL seq`. The
agent uploads changes with `WHERE seq > last_seq ORDER BY seq` — a **monotonic
integer** high-water mark instead of a wall-clock timestamp.

Code: `sync/outbox.py` (`OutboxMixin`), wired into `SyncAgent` and dispatched
from `upload_loop` when `sync.use_outbox` is true.

## What it fixes

| Defect of the timestamp poller | Outbox result |
|---|---|
| **Hard deletes invisible** — a physical `DELETE` leaves no `updated_at` to find | A `DELETE` trigger records `op='d'` with the OLD row (validated) |
| **Clock-skew** breaks conflict/checkpoint logic | Ordering is by integer `seq`, not time |
| **Boundary skip** — `updated_at > checkpoint` drops rows sharing the checkpoint's exact timestamp | `seq` is unique + strictly increasing; nothing is skipped |
| **Deferred/failed batch advances checkpoint** | Checkpoint advances only for rows HQ accepted; a failed batch is retried |

## How to enable (safe cutover)

1. **Install the triggers** on the running local DB (no restart needed):
   ```bash
   python3 helper_scripts/install_outbox.py           # attach triggers
   python3 helper_scripts/install_outbox.py --status  # verify
   ```
2. **Flip the flag** — set `sync.use_outbox = true` in
   `~/.cirqen/data/config.json` (or `SYNC_USE_OUTBOX=1`).
3. **Restart the agent.** On start it re-runs `install_outbox()` (idempotent),
   then `outbox_upload_loop` drains `sync_outbox` in `seq` order.

The legacy poller and Redis-queue paths are untouched while the flag is off, so
this is fully reversible: set the flag back to false (and optionally
`install_outbox.py --uninstall`) to revert.

## Checkpoint discipline

`outbox_upload_loop` advances `last_outbox_seq` **only after** `upload_batch`
returns success, one batch at a time. A failed upload breaks the drain loop
without advancing, so the same rows are retried on the next cycle — no gaps.

With `sync.outbox_prune = true` (default), acknowledged rows (`seq <= checkpoint`)
are deleted after each successful batch to bound table growth.

## Known limitation (documented)

`BIGSERIAL` values are assigned at statement time, so a transaction that
acquires a **lower** seq but **commits later** than a higher-seq transaction
could momentarily sit "behind" an already-advanced checkpoint and be skipped.

- For this **single-writer desktop DB** (one local Postgres, writes almost
  entirely from the one Django process) concurrent write transactions overlapping
  at commit time are rare, making this low-risk.
- The correct upgrade for a concurrent multi-writer database is a
  **snapshot-aware reader** (only read `seq` below `pg_snapshot_xmin(pg_current_snapshot())`)
  or **logical replication**. This is a follow-up, not required for the desktop
  deployment.

## Operational tools

```bash
python3 helper_scripts/install_outbox.py            # install
python3 helper_scripts/install_outbox.py --status   # table + trigger + row counts
python3 helper_scripts/install_outbox.py --uninstall # drop triggers (keeps data)
```

`agent_status.json` reports `pending_changes` from the outbox backlog while the
flag is on.
