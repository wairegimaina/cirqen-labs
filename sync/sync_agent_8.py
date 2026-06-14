from .agent_prelude import LOG, now_kenyan, now_utc, now_iso, format_kenyan_time
from .agent_prelude import KENYAN_TZ, PerformanceMonitor, encrypt_token, setup_logging, load_agent_config
from .agent_prelude import load_config_from_unified_manager, load_config_from_env_fallback
from .agent_prelude import sleep_with_jitter, is_online, CERT_TABLES, DEFAULT_CONFIG
from typing import Any, Dict, List, Optional, Tuple, Set
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
import hashlib
import os
import threading
import time
from cryptography.fernet import Fernet
import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import ThreadedConnectionPool
import requests
import pytz
from .state_manager import StateManager
from .dependency_manager import DependencyManager
from .smart_delete import SmartDeleteMixin

class SyncAgent(SmartDeleteMixin):
    def start(self):
            """
            ⚡ OPTIMIZED: Instant startup - threads start immediately
            Heavy operations run in background without blocking
            """
            startup_time = time.time()

            LOG.info("=" * 80)
            LOG.info("🚀 Starting SyncAgent v%s", self.version)
            LOG.info("=" * 80)

            # Quick health check (non-blocking)
            is_online = self.check_hq_online()
            if is_online:
                LOG.info("✅ HQ server is ONLINE")
            else:
                LOG.info("⚠️  HQ server offline - will sync when available")

            # ============================================================
            # DETECT REINSTALL / NEW MACHINE
            # Trigger a full mirror sync (HQ → Local) before any threads
            # start if EITHER:
            #   (a) The local DB is completely empty, OR
            #   (b) The sync state was wiped (no download checkpoint) — this
            #       covers reinstalls where some stale data already exists but
            #       may be days/weeks behind HQ.
            #
            # Why mirror instead of the normal download loop?
            # The download loop pulls from the audit_log (changes since a
            # timestamp). If state was wiped, there is no reliable timestamp,
            # and the audit log does not contain records created before it
            # existed. The mirror does a direct DB-to-DB ID + timestamp
            # comparison and fills every gap regardless of audit history.
            # ============================================================
            needs_mirror = False
            mirror_reason = ""

            if self.mirror_enabled and self.mirror:
                # Touch get_last_download_time so it can set _needs_mirror_sync
                self.get_last_download_time()

                local_count = self.get_local_record_count()

                if local_count == 0:
                    needs_mirror = True
                    mirror_reason = "empty local DB (new install or wiped reinstall)"
                elif getattr(self, "_needs_mirror_sync", False):
                    needs_mirror = True
                    mirror_reason = "sync state was wiped (reinstall with existing data)"

            # ============================================================
            # DATA CHECKER: fast row-count bootstrap via HTTP API.
            # Runs BEFORE the mirror so it can satisfy the need without
            # requiring a direct DB-to-DB connection.  If it succeeds,
            # the mirror is skipped.  If HQ is unreachable, we fall back
            # to the mirror as before.  If neither is available, the normal
            # audit-log download loop continues as a last resort.
            # ============================================================
            if self.data_checker and is_online and needs_mirror:
                LOG.info("=" * 80)
                LOG.info("🔍 DATA CHECKER: bootstrapping from HQ via API…")
                LOG.info(f"   Reason for sync: {mirror_reason}")
                LOG.info("=" * 80)
                try:
                    dc_result = self.data_checker.check_and_sync(
                        send_checksums=False,
                        force=(mirror_reason == "empty local DB (new install or wiped reinstall)"),
                    )
                    if dc_result.get("error"):
                        LOG.warning(
                            "⚠️  data_checker could not reach HQ (%s) — falling back to mirror",
                            dc_result["error"],
                        )
                    elif dc_result.get("client_leads"):
                        # ── HQ was wiped; client holds the authoritative data ──────
                        # Do NOT pull from HQ (it has nothing).  Push everything up
                        # right now, in dependency order, using the same upload
                        # endpoint the normal loop uses.  This is immediate and
                        # complete — we don't wait for the polling cycle.
                        LOG.warning("=" * 80)
                        LOG.warning(
                            "⬆️  CLIENT LEADS HQ: local=%d rows vs HQ=%d — pushing all local data to HQ now",
                            dc_result.get("client_total_rows", 0),
                            dc_result.get("hq_total_rows", 0),
                        )
                        LOG.warning("=" * 80)
                        needs_mirror = False
                        self._needs_mirror_sync = False
                        try:
                            push_result = self.data_checker.push_all_to_hq(
                                client_id=self.client_id,
                                machine_id=self.machine_id,
                                upload_url=f"{self.api_url}/upload",
                                http_headers=self._http_headers(),
                            )
                            if push_result["success"]:
                                LOG.info(
                                    "✅ push_all_to_hq complete: %d/%d rows pushed in %.1fs",
                                    push_result["total_uploaded"],
                                    push_result["total_rows"],
                                    push_result["total_duration_s"],
                                )
                                # Advance the checkpoint to now so the upload loop
                                # does not re-send every row on the next poll.
                                self.set_last_upload_time()
                            else:
                                LOG.error(
                                    "❌ push_all_to_hq had failures: %s — "
                                    "resetting checkpoint to epoch as fallback",
                                    push_result["failed_tables"],
                                )
                                self.state.set("last_upload_time", "1970-01-01T00:00:00+00:00")
                        except Exception as _push_err:
                            LOG.error(
                                "❌ push_all_to_hq raised: %s — resetting checkpoint to epoch",
                                _push_err,
                            )
                            self.state.set("last_upload_time", "1970-01-01T00:00:00+00:00")
                    elif dc_result["synced_tables"]:
                        LOG.info(
                            "✅ data_checker bootstrapped %d table(s) in %.1fs — skipping mirror",
                            len(dc_result["synced_tables"]),
                            dc_result["total_duration_s"],
                        )
                        needs_mirror = False
                        self._needs_mirror_sync = False
                        # ── FIX: Force the upload loop to re-upload ALL local records
                        # to HQ.  data_checker only syncs HQ → local; the local DB may
                        # have 2,000+ records that HQ is missing.  Resetting the upload
                        # checkpoint to epoch ensures the feeder loop scans everything.
                        LOG.info(
                            "🔄 Resetting upload checkpoint to epoch so all local records "
                            "are pushed to HQ (data_checker is download-only)."
                        )
                        self.state.set("last_upload_time", "1970-01-01T00:00:00+00:00")
                    else:
                        LOG.info("✅ data_checker: local DB already matches HQ — skipping mirror")
                        needs_mirror = False
                        self._needs_mirror_sync = False
                except Exception as _dc_err:
                    LOG.error("❌ data_checker raised an exception: %s — falling back to mirror", _dc_err)
            elif self.data_checker and is_online and not needs_mirror:
                # DB seems intact — still run a lightweight count-only check
                # to catch any silent drift (mismatched counts, missed changes).
                LOG.info("🔍 data_checker: running routine integrity check (with checksums)…")
                try:
                    dc_result = self.data_checker.check_and_sync(send_checksums=True, force=False)
                    if dc_result.get("synced_tables"):
                        LOG.info(
                            "🔧 data_checker corrected %d drifted table(s)",
                            len(dc_result["synced_tables"]),
                        )
                    else:
                        LOG.info("✅ data_checker: all tables in sync")
                except Exception as _dc_err:
                    LOG.warning("⚠️  data_checker routine check failed: %s", _dc_err)

            if needs_mirror:
                LOG.info("=" * 80)
                LOG.info("🔄 RECOVERY MIRROR SYNC REQUIRED")
                LOG.info(f"   Reason: {mirror_reason}")
                LOG.info("   Running full HQ → Local mirror before starting threads...")
                LOG.info("=" * 80)
                try:
                    sync_ok = self.perform_initial_mirror_sync()
                    if sync_ok:
                        LOG.info("✅ Recovery mirror sync complete — proceeding to normal operation")
                        self._needs_mirror_sync = False
                    else:
                        LOG.warning("⚠️  Recovery mirror sync failed — normal loops will continue")
                        LOG.warning("   Stale or missing data may exist until the next mirror run")
                except Exception as _init_err:
                    LOG.error("❌ Recovery mirror sync raised an exception: %s", _init_err)
                    LOG.info("   Agent will continue; scheduled mirror loop will retry later")
            elif self.mirror_enabled:
                LOG.info("✅ Local DB has %d records and state is intact — skipping recovery sync",
                         self.get_local_record_count())

            # ============================================================
            # START ALL THREADS IMMEDIATELY - NO BLOCKING
            # ============================================================
            LOG.info("")
            LOG.info("=" * 80)
            LOG.info("🎬 STARTING BACKGROUND THREADS")
            LOG.info("=" * 80)

            # 1. Upload Thread (handles initial sync in background)
            upload_thread = threading.Thread(
                target=self.upload_loop_with_background_init,
                name="UploadThread"
            )
            upload_thread.daemon = True
            upload_thread.start()
            self.threads.append(upload_thread)
            LOG.info("   ✅ Upload thread started")

            # 2. Download Thread
            download_thread = threading.Thread(
                target=self.download_loop,
                name="DownloadThread"
            )
            download_thread.daemon = True
            download_thread.start()
            self.threads.append(download_thread)
            LOG.info("   ✅ Download thread started")

            # 2b. ⚡ Instant HQ SSE Thread
            sse_thread = threading.Thread(
                target=self.sync_notify_listener,
                name="SyncNotifyThread"
            )
            sse_thread.daemon = True
            sse_thread.start()
            self.threads.append(sse_thread)
            LOG.info("   ✅ HQ SSE listener thread started")

            # 2c. ⚡ Instant Cert Notify Thread (Redis pub/sub — wakes up on cert_ready)
            cert_notify_thread = threading.Thread(
                target=self.cert_notify_listener,
                name="CertNotifyThread"
            )
            cert_notify_thread.daemon = True
            cert_notify_thread.start()
            self.threads.append(cert_notify_thread)
            LOG.info("   ✅ Cert notify listener thread started (instant cert delivery via Redis)")

            # 3. Certificate Sync Thread
            cert_thread = threading.Thread(
                target=self.certificate_sync_loop,
                name="CertificateSyncThread"
            )
            cert_thread.daemon = True
            cert_thread.start()
            self.threads.append(cert_thread)
            LOG.info("   ✅ Certificate sync thread started")

            # 4. Heartbeat Thread
            heartbeat_thread = threading.Thread(
                target=self.heartbeat_loop,
                name="HeartbeatThread"
            )
            heartbeat_thread.daemon = True
            heartbeat_thread.start()
            self.threads.append(heartbeat_thread)
            LOG.info("   ✅ Heartbeat thread started")

            # 5. Mirror Sync Thread (delayed start)
            if self.mirror_enabled:
                mirror_thread = threading.Thread(
                    target=self.delayed_mirror_sync_loop,
                    name="MirrorSyncThread"
                )
                mirror_thread.daemon = True
                mirror_thread.start()
                self.threads.append(mirror_thread)
                LOG.info("   ✅ Mirror sync thread started (delayed)")

            elapsed = time.time() - startup_time

            # 6. Certificate Pull Thread (NEW!)
            cert_pull_thread = threading.Thread(
                target=self.certificate_pull_loop,
                name="CertificatePullThread"
            )
            cert_pull_thread.daemon = True
            cert_pull_thread.start()
            self.threads.append(cert_pull_thread)
            LOG.info("   ✅ Certificate pull thread started")

            LOG.info("")
            LOG.info("=" * 80)
            LOG.info(f"⚡ AGENT READY! (startup: {elapsed:.2f}s)")
            LOG.info("=" * 80)
            LOG.info("")
            LOG.info("📊 Configuration:")
            LOG.info("   • Client ID: %s", self.client_id)
            LOG.info("   • Machine ID: %s", self.machine_id)
            LOG.info("   • Tables: %d", len(self.tables))
            LOG.info("")
            LOG.info("⏱️  Sync Intervals:")
            LOG.info("   • 📤 Upload: Every %ds", self.sync_cfg.get("poll_interval_seconds", 10))
            LOG.info("   • 📥 Download: Every %ds", self.sync_cfg.get("download_interval_seconds", 15))
            LOG.info("   • 📜 Certificates: Every %ds", self.sync_cfg.get("certificate_sync_interval", 30))
            LOG.info("   • 💓 Heartbeat: Every %ds", self.sync_cfg.get("heartbeat_interval", 60))
            if self.mirror_enabled:
                LOG.info("   • 🔄 Mirror: Every %.1fh (starts in 5 min)", self.mirror_interval_hours)
            LOG.info("")
            LOG.info("🛠️  Features:")
            LOG.info("   • 🔄 Status System: Active")
            LOG.info("   • 🗑️  Soft Delete: Enabled")
            LOG.info("   • 🔄 Smart Delete: %s", "Available" if SMART_DELETE_AVAILABLE else "Not Available")
            if self.mirror_enabled:
                LOG.info("   • 🔄 Mirror System: Enabled")
            LOG.info("")
            LOG.info("=" * 80)
            LOG.info("✅ NOW LIVE - Actively monitoring for changes!")
            LOG.info("=" * 80)
            LOG.info("")

            # ============================================================
            # MAIN LOOP - Monitor threads
            # ============================================================
            try:
                while any(t.is_alive() for t in self.threads):
                    for t in self.threads:
                        t.join(timeout=1)

                    if self.stop_event.is_set():
                        LOG.info("🛑 Stop event detected")
                        break

            except KeyboardInterrupt:
                LOG.info("")
                LOG.info("⚠️  INTERRUPT SIGNAL RECEIVED - Shutting down...")
                self.stop()

            except Exception as e:
                LOG.error("❌ FATAL ERROR: %s", str(e))
                LOG.exception(e)
                self.stop()

            finally:
                LOG.info("👋 SYNC AGENT SHUTDOWN COMPLETE")
    def delayed_mirror_sync_loop(self):
            """
            🔄 Mirror sync with delayed start to avoid blocking startup
            First sync happens 5 minutes after agent starts
            """
            delay_minutes = int(os.getenv("MIRROR_STARTUP_DELAY_MINUTES", "5"))

            LOG.info(f"🔄 Mirror sync: First sync in {delay_minutes} minutes...")

            # Wait before first sync
            for i in range(delay_minutes * 60):
                if self.stop_event.is_set():
                    LOG.info("Mirror sync cancelled during startup delay")
                    return
                time.sleep(1)

                # Log countdown every minute
                if (i + 1) % 60 == 0:
                    remaining = delay_minutes - ((i + 1) // 60)
                    if remaining > 0:
                        LOG.debug(f"🔄 Mirror sync starts in {remaining} minute(s)...")

            LOG.info("🔄 Starting delayed mirror sync...")

            # Now run normal mirror sync loop
            self.mirror_sync_loop()
    def upload_loop_with_background_init(self):
            """
            ⚡ OPTIMIZED: Upload loop that does initial sync in background
            ENHANCED: Now writes status file for UI monitoring

            Features:
            - Instant startup (no blocking)
            - Background initial sync
            - Per-table instant upload
            - Real-time HQ connection status
            - Status file updates for UI
            """
            poll_interval = int(self.sync_cfg.get("poll_interval_seconds", 10))
            batch_size = int(self.sync_cfg.get("upload_batch_size", 50))

            LOG.info("=" * 80)
            LOG.info("⚡ UPLOAD LOOP ACTIVE")
            LOG.info("   Mode: Per-table instant upload")
            LOG.info("   Poll interval: %ds", poll_interval)
            LOG.info("   Batch size: %d records", batch_size)
            LOG.info("   Status updates: Enabled")
            LOG.info("=" * 80)

            # ============================================================
            # PHASE 1: Optional background initial sync
            # ============================================================
            skip_initial = os.getenv("SKIP_INITIAL_SYNC", "false").lower() == "true"

            if not skip_initial:
                LOG.info("🔄 Background: Running initial sync check...")
                try:
                    # Check if there are offline changes (doesn't block startup)
                    last_upload = self.get_last_upload_time()

                    # Quick check: only if last upload was more than 1 hour ago
                    try:
                        last_upload_dt = datetime.fromisoformat(last_upload.replace('Z', '+00:00'))
                        hours_ago = (now_utc() - last_upload_dt).total_seconds() / 3600

                        # Treat epoch timestamp (set during reinstall recovery) as
                        # "never uploaded" regardless of how recent it looks numerically.
                        is_epoch = last_upload.startswith("1970-01-01")
                        if hours_ago > 1 or is_epoch:
                            LOG.info(f"   Last upload was {hours_ago:.1f}h ago - checking for offline changes...")
                            changes = self.discover_recent_changes()

                            if changes:
                                LOG.info(f"   Found {len(changes)} offline changes - syncing now...")

                                # 📊 UPDATE STATUS: Initial sync in progress
                                self.write_status_file(
                                    hq_online=True,
                                    pending_changes=len(changes)
                                )

                                for i in range(0, len(changes), batch_size):
                                    batch = changes[i:i + batch_size]
                                    success, error = self.upload_batch(batch)

                                    if success:
                                        remaining = len(changes) - (i + len(batch))
                                        # 📊 UPDATE STATUS: Progress
                                        self.write_status_file(
                                            hq_online=True,
                                            pending_changes=remaining,
                                            last_sync=now_iso()
                                        )
                                    else:
                                        LOG.warning(f"   Initial sync batch failed: {error}")
                                        # 📊 UPDATE STATUS: Failed
                                        self.write_status_file(
                                            hq_online=False,
                                            pending_changes=len(changes) - i
                                        )
                                        break

                                LOG.info("✅ Background initial sync complete")
                            else:
                                LOG.info("   No offline changes found")
                                # 📊 UPDATE STATUS: Idle
                                self.write_status_file(hq_online=True, pending_changes=0)
                        else:
                            LOG.info(f"   Last upload was recent ({hours_ago:.1f}h ago) - skipping initial sync")
                            # 📊 UPDATE STATUS: Idle
                            self.write_status_file(hq_online=True, pending_changes=0)

                    except Exception as e:
                        LOG.debug(f"Could not parse last upload time: {e}")

                except Exception as e:
                    LOG.warning(f"⚠️  Background initial sync failed: {e}")
                    # 📊 UPDATE STATUS: Error
                    self.write_status_file(hq_online=False, pending_changes=0)
            else:
                LOG.info("⚡ Skipped initial sync (SKIP_INITIAL_SYNC=true)")

            # ============================================================
            # PHASE 2: Normal real-time upload loop
            # ============================================================
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

                    # ============================================================
                    # PERIODIC ONLINE CHECK
                    # ============================================================
                    if current_time - last_online_check >= online_check_interval or first_connection:
                        was_online = is_online
                        is_online = self.check_hq_online()
                        last_online_check = current_time

                        # 📊 UPDATE STATUS: Connection state changed
                        if is_online != was_online:
                            self.write_status_file(hq_online=is_online, pending_changes=0)

                        if is_online and not was_online:
                            LOG.info("✅ HQ RECONNECTED at %s", format_kenyan_time(now_kenyan()))
                            consecutive_failures = 0

                            # Attempt immediate sync on reconnection
                            try:
                                changes = self.discover_recent_changes()
                                if changes:
                                    LOG.info(f"📤 Syncing {len(changes)} changes after reconnection...")
                                    self.write_status_file(
                                        hq_online=True,
                                        pending_changes=len(changes)
                                    )
                            except Exception as e:
                                LOG.debug(f"Could not check for changes on reconnect: {e}")

                        elif not is_online and was_online:
                            LOG.warning("⚠️ HQ OFFLINE at %s", format_kenyan_time(now_kenyan()))

                        first_connection = False

                    # ============================================================
                    # OFFLINE MODE: Wait for connection
                    # ============================================================
                    if not is_online:
                        if loop_count % 30 == 1:
                            LOG.warning("💤 Agent idle - HQ offline, waiting for connection...")
                            # 📊 UPDATE STATUS: Offline (every 30 loops = ~5 minutes)
                            self.write_status_file(hq_online=False, pending_changes=0)

                        time.sleep(poll_interval)
                        continue

                    # ============================================================
                    # ONLINE MODE: Check for changes per table
                    # ============================================================
                    last_upload_time = self.get_last_upload_time()
                    any_changes_found = False

                    for table in self.tables:
                        if self.stop_event.is_set():
                            break

                        # Check this specific table for changes
                        table_changes = self.fetch_recent_changes_for_table(table, last_upload_time)
                        restore_events = self.detect_local_restores(table, last_upload_time)

                        all_changes = table_changes + restore_events

                        if all_changes:
                            any_changes_found = True

                            # 📊 UPDATE STATUS: Active with pending changes
                            self.write_status_file(
                                hq_online=True,
                                pending_changes=len(all_changes)
                            )

                            LOG.info("=" * 80)
                            LOG.info(f"⚡ CHANGE DETECTED at {format_kenyan_time(now_kenyan())}")
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

                                    # ── Advance the upload checkpoint to the latest
                                    # updated_at in this batch so the next poll only
                                    # picks up rows that changed AFTER this moment.
                                    # Without this the loop falls back to epoch and
                                    # re-uploads every row on every restart.
                                    try:
                                        latest_ts = max(
                                            e.get("created_at") or e.get("updated_at") or ""
                                            for e in batch
                                        )
                                        if latest_ts:
                                            self.set_last_upload_time(latest_ts)
                                    except Exception as _ckpt_err:
                                        LOG.warning("Could not advance upload checkpoint: %s", _ckpt_err)

                                    # 📊 UPDATE STATUS: Success with remaining count
                                    remaining = len(all_changes) - (i + len(batch))
                                    self.write_status_file(
                                        hq_online=True,
                                        pending_changes=remaining,
                                        last_sync=now_iso()
                                    )
                                else:
                                    consecutive_failures += 1
                                    is_online = False
                                    LOG.error(f"❌ Batch {batch_num}/{total_batches} failed: {error}")

                                    # 📊 UPDATE STATUS: Failed
                                    self.write_status_file(
                                        hq_online=False,
                                        pending_changes=len(all_changes) - i
                                    )

                                    if consecutive_failures >= max_consecutive_failures:
                                        backoff_time = min(300, 60 * consecutive_failures)
                                        LOG.warning(f"Too many failures, backing off {backoff_time}s")

                                        for _ in range(backoff_time):
                                            if self.stop_event.is_set():
                                                break
                                            time.sleep(1)
                                    break

                            LOG.info("=" * 80)

                    # ============================================================
                    # IDLE STATE: No changes found
                    # ============================================================
                    if not any_changes_found and is_online:
                        # 📊 UPDATE STATUS: Idle (periodically, every ~5 minutes)
                        if loop_count % 30 == 1:
                            self.write_status_file(
                                hq_online=True,
                                pending_changes=0
                            )
                            LOG.warning("💤 Idle - no changes detected")

                except Exception as e:
                    LOG.exception("💥 Upload loop error: %s", e)
                    consecutive_failures += 1
                    is_online = False

                    # 📊 UPDATE STATUS: Error
                    self.write_status_file(hq_online=False, pending_changes=0)

                # Sleep between checks
                for _ in range(poll_interval):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

            LOG.info("Upload loop exiting")

            # 📊 FINAL STATUS: Shutting down
            try:
                self.write_status_file(hq_online=False, pending_changes=0)
            except:
                pass
    def stop(self):
            """Stop all sync loops gracefully"""
            LOG.info("Stopping SyncAgent...")
            self.stop_event.set()

            # Stop Redis queue worker
            if hasattr(self, 'redis_queue') and self.redis_queue:
                self.redis_queue.stop()

            for t in self.threads:
                if t.is_alive():
                    t.join(timeout=5)

            LOG.info("SyncAgent stopped")
    def __del__(self):
            """Cleanup on destruction"""
            if hasattr(self, 'pool'):
                self.pool.closeall()
