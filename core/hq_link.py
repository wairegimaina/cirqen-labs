"""Online-first write path: push local saves to HQ while it is reachable.

Reads stay on the local replica (see :mod:`Equiper.db_router`). Writes still land
in the local database first, but when HQ is reachable the rows a request saved
are pushed to HQ's ``/api/sync/upload`` as soon as the response is built, instead
of waiting for the sync agent's next poll. HQ applies them with its normal
idempotent upsert and records them in ``audit_log`` — which is the only thing
``/api/sync/download`` serves to other machines. Writing straight into the HQ
Postgres would skip that, and the change would never reach anyone else.

When HQ is offline, or a push fails, nothing is lost: the row is still local with
a fresh ``updated_at`` and the sync agent uploads it as before. The agent also
re-sends rows that were already pushed; that is harmless because HQ upserts are
idempotent.

Soft and hard deletes are left to the agent, which chooses between
``deactivate`` and ``d`` from local dependency checks.

Callers that must not proceed unless HQ has the data (the Excel import) use
:func:`is_hq_online` and :func:`push_instances` directly.
"""
import json
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import requests
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from sync.event_identity import stable_event_id

logger = logging.getLogger(__name__)

HEALTH_TIMEOUT = 3   # seconds; a slower HQ counts as offline for this check
PUSH_TIMEOUT = 30    # matches the sync agent's upload timeout
ONLINE_TTL = 20      # re-check a healthy HQ at most this often
OFFLINE_TTL = 10     # re-check an unreachable HQ a little sooner
# HQ hands uploads above QUEUE_THRESHOLD_EVENTS (250) to a background worker and
# answers before applying them, so every request stays below it.
MAX_EVENTS_PER_REQUEST = 200
MAX_QUEUED_PUSHES = 50  # beyond this, leave the rows to the sync agent

_status_lock = threading.Lock()
_status = {"online": None, "checked_at": 0.0}
_client_id = None
_local = threading.local()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="hq-push")
_queued = 0
_queued_lock = threading.Lock()


# ── HQ reachability ──────────────────────────────────────────────────────────


def _api_url():
    return (getattr(settings, "HQ_SYNC_API_URL", "") or "").rstrip("/")


def _record_status(online):
    with _status_lock:
        _status["online"] = online
        _status["checked_at"] = time.monotonic()


def _known_offline():
    with _status_lock:
        online, checked_at = _status["online"], _status["checked_at"]
    return online is False and time.monotonic() - checked_at < OFFLINE_TTL


def is_hq_online(force=False):
    """True when HQ's sync API answers its health check.

    Cached briefly so a burst of requests does not each pay a round-trip to HQ;
    pass ``force=True`` before an operation that must not act on a stale answer.
    """
    api_url = _api_url()
    if not api_url:
        return False
    if not force:
        with _status_lock:
            online, checked_at = _status["online"], _status["checked_at"]
        ttl = ONLINE_TTL if online else OFFLINE_TTL
        if online is not None and time.monotonic() - checked_at < ttl:
            return online
    try:
        online = requests.get(f"{api_url}/health", timeout=HEALTH_TIMEOUT).status_code == 200
    except requests.RequestException:
        online = False
    _record_status(online)
    return online


# ── Identity and table mapping ───────────────────────────────────────────────


def _read_text(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def get_client_id():
    """The sync agent's client id, so HQ never echoes these rows back to us."""
    global _client_id
    if _client_id:
        return _client_id

    candidates = []
    state_dir = Path(os.environ.get("SYNC_STATE_DIR") or Path(settings.DATA_PATH) / "sync_state")
    try:
        status = json.loads(_read_text(state_dir / "agent_status.json") or "{}")
        candidates.append(status.get("client_id") if isinstance(status, dict) else None)
    except ValueError:
        pass
    candidates.append(os.getenv("CLIENT_ID", "").strip())
    # Same locations sync.device_id_generator saves the id to.
    for directory in (Path.home() / ".cmms", Path.home() / ".config" / "cmms", Path("/etc/cmms")):
        candidates.append(_read_text(directory / "client_id"))

    _client_id = next((c for c in candidates if c), None)
    return _client_id


def table_for_model(model):
    """The sync agent's name for ``model``'s table, or None if it is not synced."""
    table = f"public.{model._meta.db_table}"
    return table if table in set(getattr(settings, "SYNC_TABLES", None) or ()) else None


# ── Building and sending events ──────────────────────────────────────────────


def _is_soft_deleted(row):
    return (
        row.get("pending_delete") is True
        or row.get("active_status") is False
        or row.get("deleted_at") is not None
    )


def build_events(table, row_ids):
    """Upload events for local rows, in the same shape the sync agent sends.

    Rows are read with ``to_jsonb(t.*)`` exactly as the agent reads them, on the
    current connection, so rows written in an open transaction are included.
    Soft-deleted rows are skipped (see module docstring).
    """
    ids = [str(pk) for pk in row_ids]
    if not ids:
        return []
    schema, _, name = table.rpartition(".")
    qualified = f"{connection.ops.quote_name(schema or 'public')}.{connection.ops.quote_name(name)}"
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT to_jsonb(t.*) FROM {qualified} t WHERE t.id::text = ANY(%s)", [ids])
        rows = [r[0] for r in cursor.fetchall()]

    events = []
    for row in rows:
        if isinstance(row, str):  # Django leaves jsonb undecoded for JSONField
            row = json.loads(row)
        if _is_soft_deleted(row):
            continue
        version = row.get("updated_at") or timezone.now().isoformat()
        event = {
            # Stable id: the agent re-sends rows pushed here, and HQ then drops
            # the duplicate audit row instead of serving it to every machine.
            "event_id": stable_event_id(table, row["id"], version, "u"),
            "table": table,
            "row_id": str(row["id"]),
            "operation": "u",
            "data": row,
            "created_at": version,
            # The real client id, so HQ's download never serves it back to us.
            "source": get_client_id() or "local",
        }
        for flag in ("active_status", "pending_delete"):
            if flag in row:
                event[flag] = row[flag]
        events.append(event)
    return events


def push_events(events):
    """Send events to HQ and wait for the result. Returns ``(ok, error)``.

    ``ok`` is True only when HQ applied every event: a 200 carrying a
    ``queued`` status or ``events_skipped`` is a failure.
    """
    if not events:
        return True, ""
    api_url, client_id = _api_url(), get_client_id()
    if not api_url or not client_id:
        return False, "This machine is not configured to talk to HQ."

    headers = {
        "Content-Type": "application/json",
        "User-Agent": f"Cirqen-Django (Client-ID: {client_id})",
        "X-Client-ID": client_id,
        "X-Device-ID": client_id,
    }
    token = getattr(settings, "SYNC_AUTH_TOKEN", None)
    if token:
        headers["X-API-Key"] = token

    for start in range(0, len(events), MAX_EVENTS_PER_REQUEST):
        chunk = events[start:start + MAX_EVENTS_PER_REQUEST]
        body = json.dumps({"events": chunk, "client_id": client_id}, default=str)
        try:
            response = requests.post(f"{api_url}/upload", data=body, headers=headers, timeout=PUSH_TIMEOUT)
        except requests.RequestException as exc:
            _record_status(False)
            return False, f"Could not reach HQ ({exc.__class__.__name__})."
        if response.status_code != 200:
            return False, f"HQ rejected the upload (HTTP {response.status_code})."
        try:
            payload = response.json()
        except ValueError:
            return False, "HQ returned an unreadable response."
        if payload.get("status") != "success":
            return False, f"HQ did not apply the upload (status: {payload.get('status')})."
        skipped = int(payload.get("events_skipped") or 0)
        if skipped:
            return False, f"HQ could not apply {skipped} of {len(chunk)} records."
    return True, ""


def push_instances(instances):
    """Push saved model instances to HQ and wait. Returns ``(ok, error)``."""
    by_table = {}
    for obj in instances:
        table = table_for_model(type(obj))
        if table is None:
            return False, f"{type(obj).__name__} is not synced to HQ."
        by_table.setdefault(table, []).append(obj.pk)
    events = [event for table, ids in by_table.items() for event in build_events(table, ids)]
    return push_events(events)


# ── Per-request background push ──────────────────────────────────────────────


@contextmanager
def suppressed():
    """Don't collect saves made inside this block for the background push."""
    _local.suppressed = getattr(_local, "suppressed", 0) + 1
    try:
        yield
    finally:
        _local.suppressed -= 1


def record_save(sender, instance, raw=False, using=None, **kwargs):
    """post_save receiver: remember synced rows saved during this request."""
    if raw or not getattr(_local, "collecting", False) or getattr(_local, "suppressed", 0):
        return
    table = table_for_model(sender)
    if table is None:
        return
    pending, pk = _local.pending, instance.pk
    # Only rows that actually commit are pushed; a rolled-back save is dropped.
    transaction.on_commit(lambda: pending.setdefault(table, set()).add(str(pk)), using=using)


def _push_in_background(events):
    global _queued
    try:
        if not is_hq_online():
            return
        ok, error = push_events(events)
        if ok:
            logger.debug("Pushed %d record(s) to HQ", len(events))
        else:
            logger.warning("Instant HQ push failed, the sync agent will upload instead: %s", error)
    except Exception:
        logger.exception("Instant HQ push crashed; the sync agent will upload instead")
    finally:
        with _queued_lock:
            _queued -= 1


def _schedule_push(pending):
    global _queued
    if _known_offline():
        return
    try:
        events = [event for table, ids in pending.items() for event in build_events(table, ids)]
    except Exception:
        logger.exception("Could not read saved rows for the HQ push; leaving them to the sync agent")
        return
    if not events:
        return
    with _queued_lock:
        if _queued >= MAX_QUEUED_PUSHES:
            return
        _queued += 1
    _executor.submit(_push_in_background, events)


class HQInstantPushMiddleware:
    """Push the synced rows a request saved to HQ once its response is built.

    Rows are read locally in the request thread; the HTTP call to HQ runs on a
    single background worker so the user never waits on HQ.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "HQ_INSTANT_PUSH", False):
            return self.get_response(request)
        _local.collecting = True
        _local.pending = {}
        try:
            return self.get_response(request)
        finally:
            pending = _local.pending
            _local.collecting = False
            _local.pending = {}
            if pending:
                _schedule_push(pending)
