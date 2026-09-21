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

An event-driven alternative (trigger-based outbox CDC, Phase 2) was built but
never wired into the running agent, and has since been removed; change
detection is the timestamp poller.

## 4.14 — Observability (DONE)

```bash
python3 helper_scripts/sync_status.py          # human-readable health summary
python3 helper_scripts/sync_status.py --json    # machine-readable
```

Combines `agent_status.json` (online, last sync, pending, conflicts, drift) with
live local-DB counts (unreviewed conflicts, open schema drift).
Exits non-zero when something needs attention (conflicts or drift present), so it
can be dropped into a cron/monitoring check.

## 4.15 — Idempotency + backpressure (DONE)

* **Idempotency keys.** Upload batches carry deterministic idempotency keys
  (`upload_batch`, `sync_agent_3.py`) so HQ can dedupe retried batches.
  *(HQ must honor the key for full dedupe.)*
* **Backpressure.** `upload_batch` detects HTTP **429/503** and returns a
  `throttled:<code>:<retry_after>` signal; the upload loop
  (`upload_loop_with_background_init`, `sync_agent_8.py`) honors `Retry-After`
  before its normal backoff instead of hammering a struggling HQ.

## New config knobs (all optional, sane defaults)

| Key / env | Default | Meaning |
|---|---|---|
| `SYNC_EXPLAIN` / `sync.explain_queries` | off | Capture query plans (debug only) |
