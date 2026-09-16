# cirqen-labs Sync Agent — Correctness & Code-Quality Review

Companion to `hq_server/CODE_REVIEW.md`, same method: each section carries a
**rating /10** (correctness + code quality) and a **correctness check** of
concrete defects. Focus is the recently-hardened `sync/` package (Phase 1–4
capability mixins + composition); the legacy engine is flagged for a later pass.

Severity key: 🔴 correctness bug · 🟠 latent / conditional bug ·
🟡 quality / maintainability · 🟢 note.

**Overall:** markedly stronger than the HQ side. Connection handling is
consistent (`getconn`/`try/finally`/`putconn`, independent commits, "never raise"
side-tables), the resolver is genuinely convergent, and the docs
(`ARCHITECTURE.md`, `PHASE2/4`) match the code. The main real risk is the
outbox prune interacting with the documented seq-gap.

> **Correction (Pass 2 — §5 does not describe production behavior).**
> Traced directly, not assumed: `sync_agent_8.py`'s `LifecycleMixin.start()`
> registers `self.upload_loop_with_background_init` (`sync_agent_8.py:467`) as
> the `UploadThread` target. `sync_agent_4.py`'s `upload_loop` — the *only*
> caller of `outbox_upload_loop()`/`_upload_loop_legacy()` — has **zero
> callers anywhere in the repo** (confirmed via a repo-wide grep excluding
> `/dist/`). So §5's 8/10 rating of `outbox.py`'s "genuinely good engineering"
> (durable seq checkpoint, backpressure, capped drain) describes an engine
> that never runs. The code actually executing in production is a third,
> separate, previously-unaudited implementation:
> `upload_loop_with_background_init`. Investigated and fixed two real bugs
> there (status reporting hardcoded `pending_changes=0` for the entire
> offline duration instead of reporting the real backlog; HQ's 429/503
> `Retry-After` throttle signal — already surfaced by `upload_batch`'s return
> value — was parsed by nobody, so a throttled device retried almost
> immediately instead of backing off as instructed, worst exactly when many
> devices reconnect after an outage at once). Also deleted
> `immediate_download_on_reconnect` (`sync_agent_4.py`), dead code with no
> callers — `download_loop` (`sync_agent_7.py`) already has its own working
> reconnect-edge download trigger via a separate path, so this wasn't a
> functional gap, just a red herring for future readers.
> **§5's rating stands only for `outbox.py` as a standalone module — it is
> not evidence about the app's actual reconnect/resume behavior.** If
> `outbox.py` is ever wired in as the real `UploadThread` target, re-audit
> before trusting it in production: it's never run under real load.

---

## 1. Package composition — `sync_agent.py` + `ARCHITECTURE.md` — **9/10**

The 9-way legacy `SyncAgent` was renamed into role-named mixins with a documented
MRO; capability mixins are listed first so they override the legacy engine.

- ✅ MRO is correct for intent: `OutboxMixin, ConflictQuarantineMixin,
  ConflictResolverMixin, SchemaGuardMixin` precede the legacy engine, so capability
  methods win on name overlap, and the legacy 1→9 order is preserved.
- ✅ `main()` handles the "not in main thread" signal-registration case
  (`ValueError`) — correct when embedded in a host process thread.
- 🟢 Capability mixins are leaf implementations (no `super()` chaining), so MRO
  ordering only matters for name-overlap overrides — which is exactly the
  documented purpose. Fine.

---

## 2. Conflict resolver — `conflict_resolver.py` — **9/10**

Replaces the old ±1 s "same moment, last-applied-wins" (non-convergent) with a
total order over `(updated_at, tiebreak_key)`. Both peers applying the same rule
converge on the same winner regardless of arrival order. This is the correct
shape for distributed LWW.

- 🟢 `_norm` treats a **naive** datetime as UTC (`replace(tzinfo=utc)`). If the
  local DB ever returns naive *local* timestamps, they'd be mislabeled as UTC.
  Low risk (rows carry tz-aware `updated_at`), but worth asserting at the source.
- 🟢 Missing-timestamp policy: both-missing and local-missing → `remote`. A local
  edit that lacks `updated_at` therefore loses to remote and can be overwritten.
  Documented and defensible (apply is an idempotent upsert), just be aware.
- 🟢 **HQ must mirror this exact rule** for end-to-end convergence — the docstring
  says so, and per `hq_server/CODE_REVIEW.md §5` the HQ side currently does *not*
  compare `updated_at` at all. That asymmetry is the real gap, and it lives on the
  HQ side, not here.

---

## 3. Conflict quarantine — `conflict_quarantine.py` — **8/10**

LWW losers are persisted to a local `sync_conflicts` table instead of being
dropped. Uses its own short-lived connection so it can't be rolled back by the
caller's transaction, and is written to never raise — both correct choices.

- 🟡 `ensure_conflict_table` caches `_conflict_table_ready=True` forever; if the
  table is later dropped, inserts fail until restart. Minor.
- 🟢 `local_updated_at`/`remote_updated_at` are passed through raw (str or
  datetime) into `TIMESTAMPTZ` columns — psycopg2 adapts both, fine.
- 🟢 Correctly kept out of `sync_tables` (per-client diagnostic, must not
  propagate to HQ) — good boundary discipline.

---

## 4. Schema-drift guard — `schema_guard.py` — **8/10**

Records each distinct drift (table + sorted missing-column set) once to
`sync_schema_drift`, escalates first sight to ERROR, dedupes after, and exposes a
count for the status file. Turns silent per-row column drops into a visible,
actionable condition.

- ✅ `RETURNING (xmax = 0) AS is_new` is the correct trick to distinguish an
  INSERT from an `ON CONFLICT DO UPDATE` — loud exactly once per distinct drift.
- 🟢 `get_schema_drift_count` hardcodes `public.sync_schema_drift`; if the table
  is ever created in another schema the count silently reads 0. Same pattern in
  `outbox.outbox_pending_count`. Low risk on this single-schema DB.

---

## 5. Outbox CDC — `outbox.py` — **8/10**

Trigger-based change capture with a monotonic `BIGSERIAL seq` high-water mark.
Checkpoint advances **only** after HQ accepts a batch, prune runs after success,
backpressure honors `Retry-After`, and the drain loop is capped per cycle and
respects `stop_event`. Genuinely good engineering.

- 🟠 **Prune converts the documented seq-gap from "momentary skip" into permanent
  loss.** The header documents that a txn assigned a *lower* seq but committing
  *after* a higher-seq txn can be skipped by `seq > last_seq`. With
  `outbox_prune=True` (the default), `prune_outbox(max_seq)` then `DELETE ... WHERE
  seq <= max_seq` removes that late-committing row before it is ever read — so it
  is lost, not merely delayed. Rare on a single-writer desktop, but the default
  prune removes the safety net the "low-risk" framing assumes.
  **Mitigation:** prune with a grace margin (e.g. `seq <= keep_seq` **and**
  `created_at < now() - interval '30s'`), or gate reads below
  `pg_snapshot_xmin(pg_current_snapshot())` as the docstring's follow-up suggests.

- 🟢 `_event_from_outbox_row` builds a stable `idempotency_key =
  "<client_id>:<seq>"` — immutable across retries, so HQ dedupe is sound (assuming
  HQ honors it; per `hq_server/CODE_REVIEW.md` that's the client half only).
- 🟢 `fetch_outbox_batch` returns `([], last_seq)` on DB error — indistinguishable
  from "empty", so a transient error just yields a no-op cycle without advancing
  the checkpoint. Safe, though it hides errors behind a DEBUG-less path.
- 🟢 `install_outbox` needs DDL/superuser; on failure `outbox_upload_loop` falls
  back to `_upload_loop_legacy()` — correct graceful degradation.

---

## 6. Network resilience / cold start — `check_hq_online` (`sync_agent_4.py`) — **8/10**

Correctly distinguishes a Render free-tier **cold start** (retry on
timeout / 502 / 503 / 504 with exponential backoff) from a **hard
`ConnectionError`** (no network → fail fast, no retry). Verified against the code:

- ✅ `except ConnectionError: return False` (no retry); timeouts fall to the
  generic `except Exception` retry branch. Matches `PHASE4_OPS.md §4.12` exactly.
- ✅ `_backoff_sleep` is interruptible (checks `stop_event` every 0.2 s) — a stop
  during backoff is honored promptly.
- 🟢 The real single-point-of-failure fix (move HQ off Render free tier) is
  correctly called out in the docs as out-of-repo; the client half here only
  reduces false disconnects.

---

## 7. Not yet deep-audited — legacy engine `sync_agent_1..9`

Per `ARCHITECTURE.md`, these hold the pre-existing engine (init, schema/change
detection, upload, network loops, parent recovery, apply-remote, download/cert/
heartbeat, lifecycle, status). Only the integration points touched by the new
work were spot-checked here (`upload_batch` throttle contract in `_3`,
`check_hq_online`/`upload_loop` in `_4`). The documented deferred refactor
(single-responsibility *objects* instead of multiple inheritance) should wait
until these thread/heartbeat/cert paths have integration coverage — agreed.

| Module | Responsibility | First thing to audit next |
|---|---|---|
| `sync_agent_1` | construction / registration | pool init, validation helpers |
| `sync_agent_2` | schema introspection + timestamp poller | boundary-skip vs. outbox parity |
| `sync_agent_3` | event build, `upload_batch` | idempotency-key coverage on legacy path |
| `sync_agent_4` | HQ health + **dead** upload/feeder-loop dispatcher | see Pass 2 correction above — `upload_loop`/`_upload_loop_legacy` have zero callers, not worth auditing further; `check_hq_online` (still live, §6) is the only reachable part |
| `sync_agent_5` | FK parent recovery | recursion/cycle bounds |
| `sync_agent_6` | `apply_remote_update_locally` | where resolver + schema-guard are actually invoked |
| `sync_agent_7` | download / cert / heartbeat | cert binary handling |
| `sync_agent_8` | lifecycle / threads / **actual** upload loop | now covered — §8 below |
| `sync_agent_9` | status reporting | drift/conflict/backlog surfaced correctly |

---

## 8. Thread lifecycle + actual production upload loop — `sync_agent_8.py` — **8/10** *(audited; fixes applied)*

`LifecycleMixin.start()` spawns 8-9 daemon threads (upload, download, SSE/Redis
notify listeners, cert sync/pull, conflict guard, mirror) and is the real home
of `UploadThread`'s target — see the Pass 2 correction above for why this,
not `outbox.py`, is what actually runs in production.

- ✅ **Real watchdog with a restart budget.** `start()`'s own loop
  (`:291-407`, 1s tick) checks `is_alive()` per named thread and respawns dead
  ones, capped at `SYNC_THREAD_MAX_RESTARTS` (default 5) with the budget reset
  after 30 min stable. If the budget is exhausted the thread is left dead but
  `report_critical_failure(...)` sends HQ the captured traceback — a silently-dead
  worker is exactly the failure mode this exists to catch, and it's covered.
- ✅ Every loop body is `try/except Exception` around the whole `while` block, with
  the interval sleep itself checking `stop_event`; no network exception path was
  found that can escape a loop undetected. `download_loop` (`sync_agent_7.py`)
  already has correct reconnect-edge logic (`is_online and not was_online` →
  immediate `download_updates()`), independent of the upload side.
- 🟠 **Offline status reporting was hardcoded (FIXED).** `upload_loop_with_background_init`
  wrote `pending_changes=0` at every offline status update (`:589` pre-fix,
  `:619` pre-fix) regardless of actual backlog — a device offline for hours
  reported "0 pending / fully synced" the entire time. Added
  `_count_pending_changes()` (reuses `discover_recent_changes()`, a pure
  local-DB read, safe while offline) and wired it into both offline
  status-write sites.
- 🟠 **HQ's throttle signal was ignored (FIXED).** `upload_batch`
  (`sync_agent_3.py`) already detects 429/503 and encodes the server's
  `Retry-After` as `"throttled:<code>:<retry_after>"`, but the caller treated
  it like any other failure — bump `consecutive_failures`, retry almost
  immediately, only back off after 5 consecutive failures on a flat schedule.
  Worst exactly when many devices reconnect after an outage and hammer an
  already-struggling HQ. Added `_parse_throttle_retry_after()` and honor it
  before falling through to the flat backoff.
- 🟢 `stop()`'s `t.join(timeout=5)` won't actually wait out a blocking SSE
  read (up to 300s) or `pubsub.listen()` — harmless since threads are
  daemonized, but `stop()` doesn't guarantee a clean exit within its own
  timeout. Not fixed this pass (cosmetic — process exit reclaims the thread
  regardless).
- 🟢 Deleted `immediate_download_on_reconnect` (`sync_agent_4.py`) — dead
  code, zero callers, superseded by `download_loop`'s own working trigger.

---

## Summary

| # | Section | Rating |
|---|---|---|
| 1 | Package composition | 9/10 |
| 2 | Conflict resolver | 9/10 |
| 3 | Conflict quarantine | 8/10 |
| 4 | Schema-drift guard | 8/10 |
| 5 | Outbox CDC (`outbox.py`, standalone rating — **not reachable in production**, see Pass 2) | 8/10 |
| 6 | Cold-start / network resilience (`check_hq_online`) | 8/10 |
| 8 | Thread lifecycle + actual upload loop (`sync_agent_8.py`) | 8/10 |

**Top fixes, in priority order**

1. ✅ *Resolved: `outbox.py`, the Redis upload queue and `sync_agent_4`'s dead
   dispatcher were deleted; `upload_loop_with_background_init` is the only
   upload engine.* Original finding: 🔴 **Decide what to do about `outbox.py` being dead code.** Either wire it
   in as the real `UploadThread` target (re-audit first — it's never run
   under load) and delete `upload_loop_with_background_init`, or delete
   `outbox.py`/`sync_agent_4.upload_loop`/`_upload_loop_legacy` and stop
   maintaining two upload engines where only one has ever executed. Leaving
   both in place is the actual risk now — the next person to touch "the
   upload loop" has a 50/50 chance of editing the dead one.
2. 🟠 Outbox: give `prune_outbox` a grace margin (or snapshot-aware read) so the
   documented seq-gap can't become permanent data loss under the default prune (§5)
   — relevant only if item 1 resolves toward keeping `outbox.py` live.
3. 🟢 Make HQ mirror the deterministic resolver rule (§2) — this is the biggest
   end-to-end convergence gap, and the fix lives on the **HQ** side
   (`hq_server/CODE_REVIEW.md §5`: gate `ON CONFLICT DO UPDATE` on `updated_at`).
4. 🟡 Un-hardcode `public.` in the drift/outbox count helpers (§4, §5).
5. 🟡 Invalidate the `_conflict_table_ready` cache if the table can disappear (§3).

**Cross-cutting positive:** the new mixins consistently use
`getconn`/`try/finally`/`putconn` with independent commits — the exact discipline
the HQ pool's `drop_connection` gets wrong (`hq_server/CODE_REVIEW.md §2`). Keep
this pattern as the house style.
