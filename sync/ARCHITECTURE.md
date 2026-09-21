# Sync package architecture

The `SyncAgent` (in `sync/sync_agent.py`) is composed by mixins. The legacy
engine lives in `sync_agent_1..9`; each file used to declare an **identically
named** `class SyncAgent(SmartDeleteMixin)`, which made the method-resolution
order unreadable. Each now has a descriptive role name.

## Composition (MRO order)

Capability mixins first (they override the legacy engine where names overlap),
then the legacy engine in dependency order:

| Class | File | Responsibility |
|---|---|---|
| `ConflictQuarantineMixin` | `conflict_quarantine.py` | Persist LWW losers to `sync_conflicts` instead of dropping |
| `ConflictResolverMixin` | `conflict_resolver.py` | Deterministic, convergent LWW decision |
| `SchemaGuardMixin` | `schema_guard.py` | Record + surface schema drift (`sync_schema_drift`) |
| `AgentInitMixin` | `sync_agent_1.py` | Construction: config, Redis/pool, registration, mirror init, validation helpers |
| `SchemaAndChangeDetectionMixin` | `sync_agent_2.py` | DB pool/schema introspection, download checkpoint, timestamp poller |
| `UploadMixin` | `sync_agent_3.py` | Event build, `upload_batch` (idempotency + backpressure), checkpoints |
| `NetworkLoopsMixin` | `sync_agent_4.py` | HQ health (cold-start retry), `download_updates` |
| `ParentRecoveryMixin` | `sync_agent_5.py` | Recover missing FK parents from HQ |
| `ApplyRemoteUpdateMixin` | `sync_agent_6.py` | `apply_remote_update_locally` — the download-apply path |
| `DownloadCertHeartbeatMixin` | `sync_agent_7.py` | Download loop, certificate sync, notify listeners, heartbeat |
| `LifecycleMixin` | `sync_agent_8.py` | `start()`/`stop()` thread orchestration, background init, **the upload loop** (`upload_loop_with_background_init`) |
| `StatusReportingMixin` | `sync_agent_9.py` | Status file + summary for the UI |

Supporting modules: `agent_prelude.py` (shared helpers/logging), `mirror.py`
(DB-to-DB reconciliation), `data_checker_client.py` (HTTP bootstrap),
`smart_delete.py` / `soft_delete_handler.py` (delete cascade),
`state_manager.py`,
`dependency_manager.py`.

## Tests

`sync/tests/` run against a throwaway Postgres. Coverage:
resolver, schema guard, quarantine, the live apply path, upload/
download HTTP orchestration (mocked HQ), and mirror HQ→Local recovery (two DBs).

## One upload engine

Upload runs in exactly one place: `LifecycleMixin.upload_loop_with_background_init`
(`sync_agent_8.py`), which finds changes with the timestamp poller
(`discover_recent_changes`) and sends them with `upload_batch`. Two alternative
engines that never ran in production — trigger-based outbox CDC (`outbox.py`)
and a Redis upload queue (`sync_agent_redis_queue.py`) — were removed together
with their dispatcher in `sync_agent_4.py` (CODE_REVIEW.md, top fix 1).

## Known follow-up (not yet done)

This step gave the mixins readable names and a documented map — the **safe** part
of the "collapse the 9-way inheritance" goal. The deeper refactor (composition of
single-responsibility *objects* — `Uploader`, `Downloader`, … — instead of
multiple inheritance) is deliberately deferred: it touches thread orchestration,
heartbeat, and cert loops that are not yet integration-tested against a running
agent, so it should wait until those paths have coverage too.
