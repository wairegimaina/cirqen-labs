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
            online = self._check_hq_online_once(retries, _rq, backoff)
            # A move adopted from the update server is on probation: sustained
            # failure of the new address reverts it without anyone on site.
            try:
                import endpoint_sync

                endpoint_sync.note_health(self.data_path, online)
            except Exception:  # noqa: BLE001 - never let this break the check
                pass
            return online

    def _check_hq_online_once(self, retries, _rq, backoff):
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

    def note_hq_rejected_key(self, status: int, where: str):
            """Say plainly that HQ refused this client's key, and why it matters.

            Without this a rejected key looks like any other failed request, and
            the most likely cause — HQ was moved to a host that does not carry
            the client keys — is invisible. Rate-limited to once every 10
            minutes so a 5-second loop cannot flood the log.
            """
            now = time.time()
            last = getattr(self, "_last_key_rejection_log", 0)
            if now - last < 600:
                return
            self._last_key_rejection_log = now

            adopted = ""
            try:
                import endpoint_sync

                state = endpoint_sync.describe(self.data_path)
                if state.get("adopted"):
                    adopted = (
                        f" This machine recently followed an HQ move to "
                        f"{state['adopted'].get('sync.api_url')} (adopted {state.get('adopted_at')})."
                    )
            except Exception:  # noqa: BLE001
                pass

            LOG.error(
                "🔑 HQ rejected this client's sync key (HTTP %s on %s at %s).%s "
                "Sync will not work until the key is accepted. If HQ was moved to a new "
                "host, its sync_api_keys table must move with it, or this client must be "
                "re-issued a key (POST /api/admin/generate_api_key with replace=true). "
                "Local work is unaffected and will upload once the key is valid.",
                status, where, self.api_url, adopted,
            )

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

                if r.status_code in (401, 403):
                    self.note_hq_rejected_key(r.status_code, "download")
                    return

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
                    # Lets download_loop stretch its interval while nothing is
                    # happening; any real update below snaps it back.
                    return

                # Something arrived: keep the download loop at its fast interval.
                self._download_saw_changes = True

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
