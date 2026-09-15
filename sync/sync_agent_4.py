from .agent_prelude import LOG, now_kenyan, now_utc, now_iso, format_kenyan_time
from .agent_prelude import KENYAN_TZ, PerformanceMonitor, encrypt_token, setup_logging, load_agent_config
from .agent_prelude import load_config_from_unified_manager, load_config_from_env_fallback
from .agent_prelude import sleep_with_jitter, is_online, CERT_TABLES, DEFAULT_CONFIG
import time
from typing import Any, Dict, List, Optional, Tuple, Set
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
import hashlib
from cryptography.fernet import Fernet
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import ThreadedConnectionPool
import requests
import pytz
from .state_manager import StateManager
from .dependency_manager import DependencyManager
from .smart_delete import SmartDeleteMixin

class NetworkLoopsMixin(SmartDeleteMixin):
    """Connectivity (HQ health w/ cold-start retry), the upload/feeder loops, and download_updates orchestration."""
    def handle_inbound_status_changes(self, changes: List[Dict]):
            """
            Handle inbound status changes from HQ broadcast
            Processes activate/deactivate operations
            """
            if not changes:
                return

            LOG.info(f"📥 Received {len(changes)} status changes from HQ")

            applied_count = 0
            failed_count = 0

            for change in changes:
                table = change.get("table")
                row_id = change.get("row_id")
                operation = change.get("operation")

                if not table or not row_id or not operation:
                    LOG.warning("Invalid status change: missing required fields")
                    failed_count += 1
                    continue

                try:
                    success = self.apply_status_change_locally(table, change)

                    if success:
                        applied_count += 1
                        LOG.debug(f"✅ Applied {operation} for {table}[{row_id}]")
                    else:
                        failed_count += 1
                        LOG.warning(f"❌ Failed to apply {operation} for {table}[{row_id}]")

                except Exception as e:
                    LOG.error(f"💥 Error applying status change for {table}[{row_id}]: {e}")
                    failed_count += 1

            LOG.info(f"📥 Status changes applied: {applied_count} successful, {failed_count} failed")
    def wait_for_hq_connection(self, max_wait_seconds=300, check_interval=5):
            """
            Wait for HQ server to become available before starting sync.
            Returns True when connected, False if timeout reached.
            """
            LOG.info("🔌 Waiting for HQ server connection...")
            LOG.info(f"   Server: {self.api_url}")
            LOG.info(f"   Max wait time: {max_wait_seconds} seconds")

            start_time = time.time()
            attempts = 0

            while (time.time() - start_time) < max_wait_seconds:
                attempts += 1

                try:
                    response = requests.get(
                        f"{self.api_url}/health",
                        timeout=5
                    )

                    if response.status_code == 200:
                        data = response.json()
                        if data.get("status") in ["ok", "UP"]:
                            elapsed = time.time() - start_time
                            LOG.info(f"✅ HQ server is ONLINE (connected after {elapsed:.1f}s, {attempts} attempts)")
                            LOG.info(f"   Database: {data.get('checks', {}).get('db', 'Unknown')}")
                            return True

                except requests.exceptions.ConnectionError:
                    elapsed = time.time() - start_time
                    remaining = max_wait_seconds - elapsed

                    if attempts == 1:
                        LOG.warning(f"⚠️  HQ server not reachable yet...")
                    elif attempts % 5 == 0:
                        LOG.info(f"🔄 Still waiting for HQ server... ({elapsed:.0f}s elapsed, {remaining:.0f}s remaining)")


                except Exception as e:
                    LOG.debug(f"Connection check failed: {e}")

                for _ in range(check_interval):
                    if self.stop_event.is_set():
                        LOG.info("ℹ️  Shutdown requested during connection wait")
                        return False
                    time.sleep(1)

            LOG.error(f"❌ Could not connect to HQ server after {max_wait_seconds} seconds")
            return False
    def check_hq_online(self, retries: int = 2):
            """
            Check if HQ is currently reachable, resilient to Render free-tier
            COLD STARTS. A sleeping instance answers a wake request with a 5xx or
            a timeout (then serves 200 seconds later) — treating that first blip
            as "offline" caused false disconnects. So we retry with short backoff
            on TRANSIENT signals (timeout / 502 / 503 / 504), but do NOT retry on
            a hard ConnectionError (no network → retrying is pointless and slow).
            """
            import requests as _rq
            backoff = 1.0
            for attempt in range(retries + 1):
                try:
                    response = requests.get(f"{self.api_url}/health", timeout=5 + attempt * 5)
                    if response.status_code == 200:
                        return True
                    if response.status_code in (502, 503, 504) and attempt < retries:
                        # Server waking up — wait and retry.
                        self._backoff_sleep(backoff)
                        backoff *= 2
                        continue
                    return False
                except _rq.exceptions.ConnectionError:
                    return False  # no network — fail fast, don't retry
                except Exception:
                    # Timeout or other transient error — retry a couple times.
                    if attempt < retries:
                        self._backoff_sleep(backoff)
                        backoff *= 2
                        continue
                    return False
            return False

    def _backoff_sleep(self, seconds: float):
            """Interruptible sleep that respects stop_event."""
            end = time.time() + seconds
            while time.time() < end:
                if getattr(self, "stop_event", None) and self.stop_event.is_set():
                    return
                time.sleep(0.2)
    def immediate_sync_on_reconnect(self):
            """
            ⚡ OPTIMIZED: Immediately sync all pending changes when connection is restored.
            Uses per-table upload for faster sync.
            """
            try:
                LOG.info("🚀 IMMEDIATE SYNC: Checking for offline changes...")

                last_upload_time = self.get_last_upload_time()
                batch_size = int(self.sync_cfg.get("upload_batch_size", 50))

                total_synced = 0
                total_changes = 0

                # Sync each table individually
                for table in self.tables:
                    table_changes = self.fetch_recent_changes_for_table(table, last_upload_time)
                    restore_events = self.detect_local_restores(table, last_upload_time)

                    all_changes = table_changes + restore_events

                    if all_changes:
                        total_changes += len(all_changes)
                        LOG.info(f"📦 {table}: {len(all_changes)} changes to sync")

                        # Upload in batches
                        for i in range(0, len(all_changes), batch_size):
                            batch = all_changes[i:i + batch_size]

                            success, error = self.upload_batch(batch)

                            if success:
                                total_synced += len(batch)
                            else:
                                LOG.error(f"❌ Failed to sync {table}: {error}")
                                break

                if total_changes == 0:
                    LOG.info("✅ No pending changes to sync")
                else:
                    LOG.info(f"🎉 IMMEDIATE SYNC COMPLETE! Synced {total_synced}/{total_changes} changes")

                return True

            except Exception as e:
                LOG.error(f"❌ Immediate sync failed: {e}")
                return False
    def enqueue_change(self, entity: str, record_id: Any, operation: str, data: Optional[Dict] = None):
            """
            ✅ Enqueue a change for instant sync (NON-BLOCKING)

            Call this after EVERY local DB write for instant sync.

            Examples:
                agent.enqueue_change("CalSoft_asset", 123, "u", asset_data)
                agent.enqueue_change("CalSoft_calibrationsession", 456, "u", session_data)
                agent.enqueue_change("CalSoft_asset", 123, "deactivate")

            Args:
                entity: Table name
                record_id: Primary key value
                operation: "u", "d", "activate", "deactivate"
                data: Optional data payload
            """
            if self.redis_queue:
                self.redis_queue.enqueue_change(
                    entity=entity,
                    record_id=record_id,
                    action=operation,
                    data=data,
                    operation=operation
                )
            else:
                LOG.debug(f"Queue unavailable, change will be polled: {entity}:{record_id}")
    def enqueue_batch(self, events: List[Dict]):
            """Enqueue multiple events as a batch"""
            if self.redis_queue:
                self.redis_queue.enqueue_batch(events)
            else:
                LOG.debug(f"Queue unavailable, {len(events)} events will be polled")
    def get_sync_queue_status(self) -> Dict:
            """Get current sync queue status"""
            if self.redis_queue:
                return self.redis_queue.get_status()
            else:
                return {"error": "Queue not available"}
    def get_failed_jobs(self) -> List[Dict]:
            """Get jobs that failed after max retries"""
            if self.redis_queue:
                return self.redis_queue.get_dead_letter_queue()
            else:
                return []
    def retry_failed_jobs(self):
            """Retry all failed jobs"""
            if self.redis_queue:
                self.redis_queue.retry_dead_letter_jobs()
                LOG.info("✅ Retrying all failed jobs")
            else:
                LOG.warning("⚠️  Queue not available")
    def upload_loop(self):
            """
            ⚡ UPLOAD LOOP: Queue-based or legacy polling

            If Redis queue available:
                - Starts queue worker (mutex-protected, one at a time)
                - Starts queue feeder (discovers changes, enqueues them)

            If Redis queue not available:
                - Falls back to legacy polling mode

            If sync.use_outbox is enabled, trigger-based CDC replaces all of the
            above (see sync/outbox.py).
            """
            if self.outbox_enabled():
                return self.outbox_upload_loop()

            if self.redis_queue:
                # ✅ REDIS QUEUE MODE
                LOG.info("=" * 80)
                LOG.info("⚡ REDIS QUEUE MODE ACTIVATED")
                LOG.info("=" * 80)
                LOG.info("   Strategy: Event-driven queue (one at a time)")
                LOG.info("   Features:")
                LOG.info("   • No race conditions (mutex lock)")
                LOG.info("   • Crash recovery (inflight tracking)")
                LOG.info("   • Idempotency (sync_id)")
                LOG.info("   • Exponential backoff retry")
                LOG.info("=" * 80)

                # Start queue worker
                self.redis_queue.start_worker()

                # Start queue feeder
                self._queue_feeder_loop()
            else:
                # ⚠️  LEGACY POLLING MODE
                LOG.warning("=" * 80)
                LOG.warning("⚠️  LEGACY POLLING MODE")
                LOG.warning("=" * 80)
                LOG.warning("   Redis queue not available")
                LOG.warning("   Using old polling-based sync")
                LOG.warning("=" * 80)

                self._upload_loop_legacy()
    def _queue_feeder_loop(self):
            """
            ✅ Queue feeder: Discovers changes and enqueues them

            Discovers changes from database and enqueues them.
            The queue worker handles the actual upload.
            """
            poll_interval = int(self.sync_cfg.get("poll_interval_seconds", 10))

            LOG.info(f"🔄 Queue feeder started (poll interval: {poll_interval}s)")

            loop_count = 0
            last_online_check = time.time()
            online_check_interval = 30
            is_online = False
            first_connection = True

            while not self.stop_event.is_set():
                try:
                    loop_count += 1
                    current_time = time.time()

                    # Check if HQ is online
                    if current_time - last_online_check >= online_check_interval or first_connection:
                        was_online = is_online
                        is_online = self.check_hq_online()
                        last_online_check = current_time

                        if is_online and not was_online:
                            LOG.info("✅ HQ RECONNECTED at %s", format_kenyan_time(now_kenyan()))
                        elif not is_online and was_online:
                            LOG.warning("⚠️  HQ OFFLINE at %s", format_kenyan_time(now_kenyan()))

                        first_connection = False

                    if not is_online:
                        if loop_count % 30 == 1:
                            LOG.debug("🔵 Offline - waiting for HQ connection...")
                        time.sleep(poll_interval)
                        continue

                    # Discover changes and enqueue them
                    last_upload_time = self.get_last_upload_time()
                    any_changes_found = False

                    for table in self.tables:
                        if self.stop_event.is_set():
                            break

                        table_changes = self.fetch_recent_changes_for_table(table, last_upload_time)
                        restore_events = self.detect_local_restores(table, last_upload_time)

                        all_changes = table_changes + restore_events

                        if all_changes:
                            any_changes_found = True
                            LOG.info(f"📝 Discovered {len(all_changes)} changes in {table}")

                            # Enqueue as batch
                            sync_id = self.redis_queue.enqueue_batch(all_changes)
                            LOG.info(f"   Enqueued batch: sync_id={sync_id[:8] if sync_id else 'N/A'}...")

                            # Update checkpoint — ONLY if the enqueue actually
                            # succeeded. enqueue_batch() returns None when Redis
                            # is unavailable; advancing regardless skipped those
                            # rows on every future scan, so they could never
                            # reach HQ by any path.
                            #
                            # NOTE: even on success this advances on ENQUEUE, not
                            # on confirmed delivery to HQ. If the local queue is
                            # flushed or its worker never drains it, these rows
                            # are still silently skipped. Advancing only once the
                            # queue worker confirms upload is the correct design.
                            if sync_id and all_changes:
                                latest_time = max(
                                    e.get("created_at", e.get("updated_at", ""))
                                    for e in all_changes
                                )
                                if latest_time:
                                    self.set_last_upload_time(latest_time)
                            elif not sync_id:
                                LOG.error(
                                    "❌ Enqueue failed for %d changes — holding checkpoint "
                                    "so they are rediscovered on the next scan",
                                    len(all_changes),
                                )

                    # Check queue status periodically
                    if loop_count % 30 == 0:
                        status = self.redis_queue.get_status()
                        LOG.info(f"📊 Queue: {status['queue_length']} pending, "
                                f"{status['success_count']} synced, "
                                f"{status['failed_count']} failed, "
                                f"DLQ: {status.get('dead_letter_queue', 0)}")

                    # Update status file
                    if any_changes_found or loop_count % 30 == 1:
                        status = self.redis_queue.get_status()
                        self.write_status_file(
                            hq_online=is_online,
                            pending_changes=status.get('queue_length', 0)
                        )

                except Exception as e:
                    LOG.exception(f"💥 Queue feeder error: {e}")

                # Sleep between checks
                for _ in range(poll_interval):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

            LOG.info("Queue feeder exiting")
    def _upload_loop_legacy(self):
            """
            ⚠️  LEGACY: Old polling-based upload loop

            This is your ORIGINAL upload_loop code, preserved as fallback.
            """
            poll_interval = int(self.sync_cfg.get("poll_interval_seconds", 1))
            batch_size = int(self.sync_cfg.get("upload_batch_size", 50))

            LOG.info("=" * 80)
            LOG.info("⚡ INSTANT UPLOAD MODE ACTIVATED")
            LOG.info(f"   Strategy: Per-table instant upload (no batch waiting)")
            LOG.info(f"   Polling: Every {poll_interval}s per table")
            LOG.info(f"   Batch size: {batch_size} records")
            LOG.info(f"   Timezone: 🇰🇪 East Africa Time (EAT/UTC+3)")
            LOG.info("=" * 80)

            loop_count = 0
            consecutive_failures = 0
            max_consecutive_failures = 5
            last_online_check = time.time()
            online_check_interval = 30
            is_online = False
            first_connection = True

            while not self.stop_event.is_set():
                try:
                    loop_count += 1
                    current_time = time.time()

                    # Online check
                    if current_time - last_online_check >= online_check_interval or first_connection:
                        was_online = is_online
                        is_online = self.check_hq_online()
                        last_online_check = current_time

                        if is_online and not was_online:
                            LOG.info("✅ HQ RECONNECTED at %s", format_kenyan_time(now_kenyan()))
                            consecutive_failures = 0
                            self.immediate_sync_on_reconnect()
                        elif not is_online and was_online:
                            LOG.warning("⚠️ HQ OFFLINE at %s", format_kenyan_time(now_kenyan()))

                        first_connection = False

                    if not is_online:
                        if loop_count % 30 == 1:
                            LOG.debug("🔵 Offline - waiting for HQ connection...")
                        time.sleep(poll_interval)
                        continue

                    # Check each table individually and upload immediately.
                    # The checkpoint is read PER TABLE: a single shared value let
                    # a busy table (Inventory_equipment) drag it forward past
                    # other tables' older pending rows, which then became
                    # permanently invisible to `updated_at > checkpoint`.
                    for table in self.tables:
                        if self.stop_event.is_set():
                            break

                        last_upload_time = self.get_last_upload_time(table)
                        table_changes = self.fetch_recent_changes_for_table(table, last_upload_time)
                        restore_events = self.detect_local_restores(table, last_upload_time)

                        all_changes = table_changes + restore_events

                        if all_changes:
                            LOG.info("=" * 80)
                            LOG.info(f"⚡ INSTANT UPLOAD TRIGGERED at {format_kenyan_time(now_kenyan())}")
                            LOG.info(f"   Table: {table}")
                            LOG.info(f"   Changes: {len(all_changes)}")
                            LOG.info("=" * 80)

                            # Upload immediately (in batches if needed)
                            for i in range(0, len(all_changes), batch_size):
                                batch = all_changes[i:i + batch_size]
                                batch_num = (i // batch_size) + 1
                                total_batches = (len(all_changes) + batch_size - 1) // batch_size

                                start_time = time.time()
                                success, error = self.upload_batch(batch)
                                duration = time.time() - start_time

                                if success:
                                    consecutive_failures = 0
                                    is_online = True
                                    LOG.info(f"✅ Batch {batch_num}/{total_batches} uploaded in {duration:.2f}s")

                                    # Advance checkpoint so the next poll skips these
                                    # rows — scoped to THIS table, so finishing one
                                    # table cannot skip another's pending rows.
                                    try:
                                        latest_ts = max(
                                            e.get("created_at") or e.get("updated_at") or ""
                                            for e in batch
                                        )
                                        if latest_ts:
                                            self.set_last_upload_time(latest_ts, table=table)
                                    except Exception as _ckpt_err:
                                        LOG.warning("Could not advance upload checkpoint: %s", _ckpt_err)
                                else:
                                    consecutive_failures += 1
                                    is_online = False
                                    LOG.error(f"❌ Batch {batch_num} failed: {error}")

                                    if consecutive_failures >= max_consecutive_failures:
                                        backoff_time = min(300, 60 * consecutive_failures)
                                        LOG.warning(f"Too many failures, backing off {backoff_time}s")
                                        for _ in range(backoff_time):
                                            if self.stop_event.is_set():
                                                break
                                            time.sleep(1)
                                    break

                            LOG.info("=" * 80)

                except Exception as e:
                    LOG.exception("💥 Exception in upload_loop: %s", e)
                    consecutive_failures += 1
                    is_online = False

                # Short sleep for near-instant detection
                for _ in range(poll_interval):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

            LOG.info("Upload loop exiting")
    @staticmethod
    def _parse_download_ts(value):
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def _download_cursor_checkpoint(self, last_ts, next_since, failed_updates):
            """
            Checkpoint for an HQ page that carries a ``next_since`` cursor.

            HQ pages by its own receive time and reports how far the page reached,
            so the checkpoint no longer depends on row timestamps from other
            machines' clocks: an edit made offline and uploaded days later is still
            ahead of it, and a page whose events were all filtered out still moves
            it forward (the old code returned early on an empty page, which is how
            this machine sat on the same 300k-event window since August). If
            anything failed to apply, stop just before the earliest failure so the
            next request fetches it again.
            """
            from datetime import timedelta
            try:
                target = self._parse_download_ts(next_since)
                if failed_updates:
                    cursors = [u.get("cursor") for u in failed_updates]
                    if not all(cursors):
                        return last_ts
                    target = min(
                        [target]
                        + [self._parse_download_ts(c) - timedelta(microseconds=1) for c in cursors]
                    )
                if last_ts and target <= self._parse_download_ts(last_ts):
                    return last_ts
                return target.isoformat()
            except (TypeError, ValueError):
                LOG.warning("Unreadable download cursor %r — keeping checkpoint %s", next_since, last_ts)
                return last_ts

    def download_updates(self):
            """
            ENHANCED: Download updates from HQ with cross-workshop transfer support
            """
            last_ts = self.get_last_download_time()

            url = f"{self.api_url}/download"
            params = {"since": last_ts, "client_id": self.client_id}
            headers = self._http_headers()
            headers["X-Instant-Download"] = "1"

            try:

                # Long read timeout: HQ answers a large backlog page slower than
                # 30s, and a timeout here means the page is lost and re-requested
                # forever. HQ's gunicorn timeout is 120s.
                r = requests.get(url, params=params, headers=headers, timeout=(10, 90))

                if r.status_code != 200:
                    LOG.warning(f"❌ Download returned status {r.status_code}: {r.text}")
                    return

                payload = r.json()
                updates = payload.get("updates", [])
                next_since = payload.get("next_since")

                if not updates:
                    if next_since:
                        checkpoint = self._download_cursor_checkpoint(last_ts, next_since, [])
                        if checkpoint != last_ts:
                            self.set_last_download_time(checkpoint)
                    LOG.debug(f"📭 No new updates available from HQ")
                    return

                # === Enhanced logging with transfer detection ===
                status_updates = [u for u in updates if u.get("operation") in ["activate", "deactivate"]]
                delete_updates = [u for u in updates if u.get("operation") == "d"]
                certificate_updates = [u for u in updates if "certificate" in str(u.get("table", "")).lower()]

                # NEW: Detect cross-workshop transfers
                transfer_updates = []
                for update in status_updates:
                    if update.get("operation") == "activate":
                        # Check if source indicates a transfer
                        source = update.get("source", "")
                        if "transfer" in source.lower() or "cascade" in source.lower():
                            transfer_updates.append(update)

                LOG.info(f"🎯 DOWNLOAD SUCCESS! Received {len(updates)} updates from HQ:")
                if status_updates:
                    LOG.info(f"   📋 {len(status_updates)} status changes ({len(transfer_updates)} transfers)")
                if delete_updates:
                    LOG.info(f"   🗑️  {len(delete_updates)} delete operations")
                if certificate_updates:
                    LOG.info(f"   📜 {len(certificate_updates)} certificate-related updates")

                LOG.debug(f"📊 Sorting updates by table dependencies...")
                try:
                    sorted_updates = self.dep_manager.sort_updates_by_dependency(updates)
                    LOG.debug(f"   ✓ Successfully sorted {len(sorted_updates)} updates by dependencies")
                except Exception as dep_error:
                    LOG.warning("")
                    LOG.warning("=" * 80)
                    LOG.warning("⚠️  DEPENDENCY SORTING FAILED")
                    LOG.warning("=" * 80)
                    LOG.warning(f"   Error: {dep_error}")
                    LOG.warning(f"   This is usually caused by:")
                    LOG.warning(f"   • Circular foreign key references")
                    LOG.warning(f"   • Graph modification during iteration")
                    LOG.warning(f"   • Complex multi-level dependencies")
                    LOG.warning("")
                    LOG.warning(f"   📋 FALLBACK: Proceeding with original order")
                    LOG.warning(f"   Updates will be applied in the order received from HQ")
                    LOG.warning("=" * 80)
                    LOG.warning("")
                    sorted_updates = updates  # Use original order if sorting fails

                applied_count = 0
                failed_updates = []
                latest_ts = last_ts
                certificate_applied = 0
                transfer_applied = 0

                # === FIRST PASS: Apply all updates in dependency order ===
                for update in sorted_updates:
                    table = update.get("table")
                    if not table:
                        LOG.warning(f"⚠️  Update missing table field, skipping")
                        continue

                    row_id = update.get("row_id", "unknown")
                    operation = update.get("operation", "u")

                    # Classify update type (logged in summary, not per-row)
                    is_transfer = update in transfer_updates
                    if is_transfer:
                        LOG.debug(f"   🌍 Processing cross-workshop transfer: {table}[{row_id}]")

                    # BUGFIX: this previously checked `"certificate" in table.lower()`,
                    # but the calibration session table name (the only table that
                    # actually carries certificate_number) is
                    # "public.CalSoft_calibrationsession" — "certificate" is not a
                    # substring of that, so this flag was always False in practice
                    # and certificate-applied counts in the summary log were always
                    # 0. Check the actual table + payload instead.
                    is_certificate_update = bool(table) and "calibrationsession" in table.lower()
                    incoming_cert_number = update.get("data", {}).get("certificate_number") if is_certificate_update else None
                    if incoming_cert_number:
                        LOG.debug(f"   📜 Processing certificate: {incoming_cert_number}")

                    # === Apply the update ===
                    ok = self.apply_remote_update_locally(table, {
                        "row_id": row_id,
                        "data": update.get("data", {}),
                        "operation": operation,
                        "last_modified": update.get("last_modified") or update.get("updated_at") or update.get("ts"),
                        "source": update.get("source", "unknown"),
                        "active_status": update.get("active_status"),
                        "pending_delete": update.get("pending_delete")
                    })

                    if ok:
                        applied_count += 1
                        if is_certificate_update:
                            certificate_applied += 1
                            LOG.debug(f"   ✅ Certificate applied: {table} id={row_id}")
                            if incoming_cert_number:
                                # This is the fast path — normal 15s poll / SSE push,
                                # not the 60s certificate_pull_loop recovery path.
                                # Reconcile here too so a stuck pending_certificates
                                # row (retry_count exhausted on the push side) gets
                                # cleaned up as soon as the cert is actually confirmed,
                                # not just whenever the slower recovery loop next runs.
                                try:
                                    self.reconcile_pending_certificate_for_session(row_id)
                                except Exception as _reconcile_err:
                                    LOG.debug(
                                        "Could not reconcile pending_certificates for session %s: %s",
                                        row_id, _reconcile_err,
                                    )
                        if is_transfer:
                            transfer_applied += 1
                            LOG.debug(f"   ✅ Transfer applied: {table} id={row_id}")
                    else:
                        LOG.warning(f"   ⚠️  Failed to apply: {table} id={row_id} (will retry)")
                        failed_updates.append(update)

                    lm = update.get("last_modified") or update.get("updated_at") or update.get("ts")
                    if lm and lm > latest_ts:
                        latest_ts = lm

                # === SECOND PASS: Retry failed updates ===
                if failed_updates:
                    LOG.info(f"🔄 Retrying {len(failed_updates)} failed updates after dependency resolution...")
                    retry_sorted = self.dep_manager.sort_updates_by_dependency(failed_updates)

                    for update in retry_sorted:
                        table = update.get("table")
                        row_id = update.get("row_id", "unknown")
                        operation = update.get("operation", "u")

                        ok = self.apply_remote_update_locally(table, {
                            "row_id": row_id,
                            "data": update.get("data", {}),
                            "operation": operation,
                            "last_modified": update.get("last_modified") or update.get("updated_at") or update.get("ts"),
                            "source": update.get("source", "unknown"),
                            "active_status": update.get("active_status"),
                            "pending_delete": update.get("pending_delete")
                        })

                        if ok:
                            applied_count += 1
                            failed_updates.remove(update)
                            LOG.debug(f"   ✅ Retry successful: {table} id={row_id}")

                            if table and "calibrationsession" in table.lower():
                                retry_cert_number = update.get("data", {}).get("certificate_number")
                                if retry_cert_number:
                                    try:
                                        self.reconcile_pending_certificate_for_session(row_id)
                                    except Exception as _reconcile_err:
                                        LOG.debug(
                                            "Could not reconcile pending_certificates for session %s: %s",
                                            row_id, _reconcile_err,
                                        )

                failed_count = len(failed_updates)

                # ── SAFE CHECKPOINT CALCULATION ──────────────────────────────
                # BUGFIX: `latest_ts` above was advanced for every update seen,
                # including ones that never applied successfully (first pass
                # failure not fixed by the dependency-ordered retry). Persisting
                # that value unconditionally meant a permanently-failing update
                # dropped out of the `since=` window forever — HQ would never be
                # asked for it again. Instead: if any updates are still failed
                # after the retry pass, never move the checkpoint past the
                # EARLIEST of those failures, so the next poll re-fetches them
                # (and anything after them — safe, since apply is upsert-style
                # and idempotent). Only use the full `latest_ts` when nothing is
                # left unresolved.
                if next_since:
                    safe_checkpoint_ts = self._download_cursor_checkpoint(
                        last_ts, next_since, failed_updates
                    )
                elif failed_updates:
                    failed_timestamps = [
                        u.get("last_modified") or u.get("updated_at") or u.get("ts")
                        for u in failed_updates
                    ]
                    failed_timestamps = [ts for ts in failed_timestamps if ts]
                    if failed_timestamps:
                        earliest_failed_ts = min(failed_timestamps)
                        safe_checkpoint_ts = min(latest_ts, earliest_failed_ts)
                    else:
                        # Failed update has no usable timestamp at all — safest
                        # option is to not advance the checkpoint this round.
                        safe_checkpoint_ts = last_ts
                    if safe_checkpoint_ts < last_ts:
                        safe_checkpoint_ts = last_ts
                else:
                    safe_checkpoint_ts = latest_ts

                # === FINAL SUCCESS LOGGING ===
                if applied_count > 0:
                    success_emoji = "🎯" if applied_count == len(updates) else "✅"
                    LOG.info(f"{success_emoji} DOWNLOAD COMPLETE! Successfully applied {applied_count}/{len(updates)} updates:")

                    if status_updates:
                        LOG.info(f"   📋 {len(status_updates)} status changes processed")
                    if transfer_applied > 0:
                        LOG.info(f"   🌍 {transfer_applied} cross-workshop transfers applied ✨")
                    if certificate_applied > 0:
                        LOG.info(f"   📜 {certificate_applied} certificate updates applied 📜")
                    if delete_updates:
                        LOG.info(f"   🗑️  {len(delete_updates)} delete operations processed")
                    if failed_count == 0:
                        LOG.info(f"   🥳 Perfect sync! All updates applied successfully!")
                    else:
                        LOG.info(f"   ⚠️  {failed_count} updates permanently failed — checkpoint held back so they are retried next poll")

                    if safe_checkpoint_ts != last_ts:
                        self.set_last_download_time(safe_checkpoint_ts)
                        LOG.info(f"   🕐 Last download timestamp updated: {safe_checkpoint_ts}")
                    elif failed_count > 0:
                        LOG.warning(
                            f"   ⏸️  Checkpoint NOT advanced — earliest unresolved failure is at or before "
                            f"the current checkpoint ({last_ts})"
                        )

                else:
                    LOG.warning(f"⚠️  No updates could be applied from this batch")
                    # With a cursor, the checkpoint may still move up to just
                    # before the earliest failure.
                    if next_since and safe_checkpoint_ts != last_ts:
                        self.set_last_download_time(safe_checkpoint_ts)
                    if failed_count > 0:
                        LOG.warning(
                            f"   ⏸️  {failed_count} update(s) failed and checkpoint was not advanced "
                            f"— they will be retried on the next poll"
                        )

            except requests.RequestException as e:
                LOG.error(f"❌ Download request failed: {e}")
            except Exception as e:
                LOG.exception(f"💥 Unexpected error in download updates: {e}")
