# Phase 4 — Scale & Ops

Client-side hardening for reliability at fleet scale. All items below are done
and verified; the one infrastructure action that must happen outside this repo
is called out explicitly.

## 4.12 — Resilience to HQ cold starts (client half — DONE)

`check_hq_online()` no longer treats a Render free-tier cold start as "offline".
It retries on **transient** signals (timeout / HTTP 502 / 503 / 504 — a sleeping
instance waking up) with short exponential backoff, but **fails fast on a hard
`ConnectionError`** (no network → retrying is pointless). Verified:

| Scenario | Result | Attempts |
|---|---|---|
| 200 | online | 1 |
| ConnectionError (no network) | offline | 1 (fast) |
| 503 → 200 (cold start) | online | 2 |
| timeout → 200 | online | 2 |
| persistent 503 | offline | 3 (retries exhausted) |

### ⚠️ Infrastructure action (NOT in this repo — you must do it)

The real SPOF fix is to **move the HQ service off Render's free tier** (which
sleeps and cold-starts). That is a Render dashboard change on the separate HQ
repo. The client resilience above reduces false disconnects but does not remove
the single-point-of-failure.

## 4.13 — Right-sized polling (DONE)

The legacy poller ran `EXPLAIN ANALYZE` on **every** query whenever the sync
logger was at DEBUG (the default) — and `ANALYZE` *executes* the query an extra
time, doubling DB load on every poll of every table. Now:

* Query-plan capture is gated behind an **explicit opt-in** (`SYNC_EXPLAIN=1` or
  `sync.explain_queries=true`), not the log level.
* It uses plain `EXPLAIN` (no `ANALYZE`), so it never executes the query.

The structural fix for polling load is the **outbox** (Phase 2): enable it and
change detection is event-driven instead of scanning ~40 tables every second.

## 4.14 — Observability (DONE)

```bash
python3 helper_scripts/sync_status.py          # human-readable health summary
python3 helper_scripts/sync_status.py --json    # machine-readable
```

Combines `agent_status.json` (online, last sync, pending, conflicts, drift) with
live local-DB counts (outbox backlog, unreviewed conflicts, open schema drift).
Exits non-zero when something needs attention (conflicts or drift present), so it
can be dropped into a cron/monitoring check.

## 4.15 — Idempotency + backpressure (DONE)

* **Idempotency keys.** Every outbox event carries a deterministic
  `idempotency_key = "<client_id>:<seq>"` (stable across retries, since `seq` is
  immutable), and each upload batch carries a `sha256` batch key derived from
  them. HQ can dedupe on these so at-least-once delivery is safe. *(HQ must honor
  the key for full dedupe — client half is done here.)*
* **Backpressure.** `upload_batch` detects HTTP **429/503** and returns a
  `throttled:<code>:<retry_after>` signal; the outbox loop then backs off
  (honoring `Retry-After`, else exponential 1→60s) instead of hammering a
  struggling HQ. A per-cycle cap (`outbox_max_batches_per_cycle`, default 20)
  keeps a large backlog from monopolizing the loop.

## New config knobs (all optional, sane defaults)

| Key / env | Default | Meaning |
|---|---|---|
| `SYNC_EXPLAIN` / `sync.explain_queries` | off | Capture query plans (debug only) |
| `sync.outbox_max_batches_per_cycle` | 20 | Backpressure cap per online cycle |
| `sync.use_outbox` | false | Enable trigger-based CDC (Phase 2) |
| `sync.outbox_prune` | true | Delete acknowledged outbox rows |
