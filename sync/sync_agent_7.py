from .agent_prelude import LOG, now_kenyan, now_utc, now_iso, format_kenyan_time
from .agent_prelude import KENYAN_TZ, PerformanceMonitor, encrypt_token, setup_logging, load_agent_config
from .agent_prelude import load_config_from_unified_manager, load_config_from_env_fallback
from .agent_prelude import sleep_with_jitter, is_online, CERT_TABLES, DEFAULT_CONFIG
from typing import Any, Dict, List, Optional, Tuple, Set
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
import hashlib
from .agent_prelude import LOG, now_kenyan, now_utc, now_iso, format_kenyan_time
from .agent_prelude import KENYAN_TZ, PerformanceMonitor, encrypt_token, setup_logging, load_agent_config
from .agent_prelude import load_config_from_unified_manager, load_config_from_env_fallback
from .agent_prelude import sleep_with_jitter, is_online, CERT_TABLES, DEFAULT_CONFIG
from .agent_prelude import REDIS_AVAILABLE
from typing import Any, Dict, List, Optional, Tuple, Set
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import defaultdict
import hashlib
import json
import os
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

# Instant push is a nicety; correctness comes from interval polling. When HQ is
# at its stream cap, retry slowly rather than adding to the pressure.
SSE_BASE_BACKOFF = 30.0
SSE_MAX_BACKOFF = 600.0

# Idle download pacing (see download_loop).
DOWNLOAD_IDLE_AFTER = 3
DOWNLOAD_IDLE_MAX_INTERVAL = int(os.getenv("SYNC_DOWNLOAD_IDLE_MAX", "120"))


class DownloadCertHeartbeatMixin(SmartDeleteMixin):
    """Download loop, certificate sync, SSE/Redis notify listeners, and heartbeat."""
    def _apply_new_sync_url(self, adopted: dict):
            """Point the running loops at a newly adopted sync address.

            Every network loop builds its URL from self.api_url at call time, so
            reassigning it here takes effect on the next iteration — seconds,
            not whenever the machine next restarts. That matters: a fleet move
            is only finished when the old host can be switched off, and a
            hospital desktop may run for weeks between restarts.

            The mirror is the exception: it was handed api_url when it was
            constructed and keeps its own copy until the agent restarts. It runs
            every 24h and its database connection is unaffected by a server
            move, so it is left to pick the change up on restart.
            """
            new_url = (adopted.get("sync.api_url") or "").rstrip("/")
            if not new_url or new_url == self.api_url:
                return

            previous = self.api_url
            self.api_url = new_url
            self.hq_base_url = (
                new_url.split("/api/")[0] if "/api/" in new_url else new_url
            )
            LOG.warning(
                "🧭 Sync address switched live: %s -> %s (mirror follows on restart)",
                previous, new_url,
            )

    def endpoint_sync_loop(self):
            """Ask the update server whether the sync HQ has moved.

            The update server's address never changes, so this keeps working
            when the sync HQ is unreachable — which is exactly when a move
            needs to be discovered. Adoption is guarded by signature, freshness,
            validation and a health probe (endpoint_sync.py); anything short of
            all four leaves this machine where it is.
            """
            import random

            try:
                import endpoint_sync
            except ImportError:
                LOG.info("endpoint_sync unavailable — HQ address changes will need a release")
                return

            update_url = (os.getenv("HQ_SERVER_URL") or "").rstrip("/")
            if not update_url:
                LOG.info("No update server address — skipping HQ endpoint checks")
                return

            LOG.info("🧭 HQ endpoint check started (update server: %s)", update_url)

            # Spread the fleet out: 500 desktops restarting together must not
            # arrive at the same second.
            sleep_with_jitter(random.uniform(5, 60))

            while not self.stop_event.is_set():
                try:
                    result = endpoint_sync.fetch_and_apply(self.data_path, update_url)
                    if result.get("changed"):
                        LOG.warning(
                            "🧭 HQ addresses changed by the update server: %s",
                            result.get("adopted"),
                        )
                        self._apply_new_sync_url(result.get("adopted") or {})
                    else:
                        LOG.debug("HQ endpoint check: %s", result.get("reason"))
                except Exception as exc:  # noqa: BLE001 - never kill the thread
                    LOG.debug("HQ endpoint check failed: %s", exc)

                interval = 900
                try:
                    interval = endpoint_sync.poll_seconds(self.data_path, 900)
                except Exception:  # noqa: BLE001
                    pass
                for _ in range(interval):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

            LOG.info("🧭 HQ endpoint check exiting")

    def download_loop(self):
            """Enhanced download loop with connection awareness"""
            import random

            interval = int(self.sync_cfg.get("download_interval_seconds", 15))

            LOG.info(
                "📥 Download loop started (every %ds when active, backing off to %ds when idle) 🔄",
                interval, DOWNLOAD_IDLE_MAX_INTERVAL,
            )

            idle_cycles = 0
            loop_count = 0
            consecutive_failures = 0
            max_consecutive_failures = 5
            is_online = False
            first_connection = True

            while not self.stop_event.is_set():
                try:
                    loop_count += 1

                    if loop_count % 3 == 1 or first_connection:
                        was_online = is_online
                        is_online = self.check_hq_online()

                        if is_online and not was_online:
                            LOG.info("✅ HQ server connection restored!")
                            consecutive_failures = 0

                            LOG.info("=" * 60)
                            LOG.info("📥 Starting immediate download of HQ updates...")
                            LOG.info("=" * 60)
                            self.download_updates()
                            LOG.info("=" * 60)

                        elif not is_online and was_online:
                            LOG.warning("⚠️  HQ server connection lost")

                        first_connection = False

                    if not is_online:
                        if loop_count % 300 == 1:  # Every 5 minutes
                            LOG.warning("💤 Download paused - HQ offline")
                    else:
                        # 🔇 Silent check - only log if updates received
                        self.download_updates()

                except requests.RequestException as e:
                    consecutive_failures += 1
                    is_online = False

                    if consecutive_failures <= 3:
                        LOG.debug("Download failed (attempt %d): %s", consecutive_failures, str(e)[:100])
                    elif consecutive_failures == max_consecutive_failures:
                        LOG.error("❌ Download failed %d times - HQ may be offline", consecutive_failures)

                except Exception as e:
                    LOG.exception("💥 Exception in download_loop: %s", e)

                # Adaptive pacing. The configured interval is what a busy site
                # needs; an idle one does not, and at fleet scale the idle cost
                # dominates (every desktop polling every 5s is 0.2 req/s each,
                # ~100 req/s across 500 machines with nothing happening).
                # Quiet cycles stretch the wait up to DOWNLOAD_IDLE_MAX_INTERVAL;
                # any change, or an SSE wake-up, snaps it straight back.
                if getattr(self, "_download_saw_changes", False):
                    idle_cycles = 0
                    self._download_saw_changes = False
                else:
                    idle_cycles += 1

                wait = interval
                if idle_cycles >= DOWNLOAD_IDLE_AFTER:
                    wait = min(interval * (2 ** min(idle_cycles - DOWNLOAD_IDLE_AFTER + 1, 5)),
                               DOWNLOAD_IDLE_MAX_INTERVAL)
                # Jitter: 500 desktops started by the same morning routine must
                # not land on HQ in lockstep.
                wait = max(1, int(wait * random.uniform(0.85, 1.15)))

                for _ in range(wait):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

            LOG.info("📥 Download loop exiting")
    def sync_notify_listener(self):
            """
            Subscribe to HQ Server-Sent Events for instant table updates.
            Redis pub/sub stays server-side only; clients receive events over HTTP.
            """
            import random

            rejected_backoff = SSE_BASE_BACKOFF

            while not self.stop_event.is_set():
                url = f"{self.api_url}/events"
                headers = self._http_headers()
                headers["Accept"] = "text/event-stream"
                headers["X-Instant-Download"] = "1"

                try:
                    with requests.get(url, headers=headers, stream=True, timeout=(10, 300)) as response:
                        if response.status_code != 200:
                            # 503 means HQ is at its stream cap. Instant push is
                            # an optimisation — interval polling below still
                            # syncs correctly — so back off hard rather than
                            # hammering. Honour Retry-After when HQ sends it,
                            # and jitter so a fleet does not retry in lockstep.
                            retry_after = response.headers.get("Retry-After")
                            try:
                                wait = float(retry_after) if retry_after else rejected_backoff
                            except (TypeError, ValueError):
                                wait = rejected_backoff
                            wait = min(max(wait, 10.0), SSE_MAX_BACKOFF)
                            rejected_backoff = min(rejected_backoff * 2, SSE_MAX_BACKOFF)
                            if response.status_code == 503:
                                LOG.info(
                                    "HQ is at its stream capacity; using interval polling "
                                    "and retrying in %.0fs", wait,
                                )
                            else:
                                LOG.warning("SSE sync listener rejected by HQ: %s %s",
                                            response.status_code, response.text[:200])
                            sleep_with_jitter(wait * random.uniform(0.8, 1.2))
                            continue

                        rejected_backoff = SSE_BASE_BACKOFF  # connected: reset
                        LOG.info("✅ SSE sync listener connected to HQ")
                        for line in response.iter_lines(decode_unicode=True):
                            if self.stop_event.is_set():
                                break
                            if not line:
                                continue
                            if line.startswith("data:"):
                                data = line[5:].strip()
                                if not data or data == "[DONE]":
                                    continue
                                try:
                                    payload = json.loads(data)
                                except Exception as parse_error:
                                    LOG.debug("Ignoring invalid SSE payload: %s", parse_error)
                                    continue
                                origin_client = payload.get("client_id")
                                if origin_client and origin_client == self.client_id:
                                    continue
                                LOG.info("📡 SSE sync update received from HQ: %s", payload)
                                try:
                                    # An SSE wake-up is activity: bring the
                                    # download loop back to its fast interval.
                                    self._download_saw_changes = True
                                    self.download_updates()
                                except Exception as download_error:
                                    LOG.warning("SSE-triggered download failed: %s", download_error)
                except requests.RequestException as request_error:
                    LOG.warning("SSE sync listener connection error: %s — retrying", request_error)
                    time.sleep(5)
                except Exception as listener_error:
                    LOG.warning("SSE sync listener error: %s — retrying", listener_error)
                    time.sleep(5)

                if not self.stop_event.is_set():
                    time.sleep(2)

            LOG.info("📡 SSE sync listener exiting")
    def cert_notify_listener(self):
            """
            ⚡ INSTANT CERT DELIVERY: Subscribe to Redis cert_ready channel.

            When the server generates a certificate it publishes on 'cert_ready'.
            This listener wakes up immediately and calls download_updates() so
            clients see the new certificate without waiting for the next poll cycle.

            Falls back silently if Redis is unavailable.
            """
            if not REDIS_AVAILABLE:
                LOG.info("⏭️  cert_notify_listener skipped (redis not installed)")
                return

            redis_cfg = self.sync_cfg.get("redis", {})
            redis_host = redis_cfg.get("host", os.getenv("REDIS_HOST", "127.0.0.1"))
            redis_port = int(redis_cfg.get("port", os.getenv("REDIS_PORT", 7788)))
            redis_db   = int(redis_cfg.get("db",   os.getenv("REDIS_DB_CACHE", 1)))

            LOG.info("🔔 cert_notify_listener starting (Redis %s:%s db=%s)", redis_host, redis_port, redis_db)

            while not self.stop_event.is_set():
                try:
                    import redis as _redis
                    r = _redis.Redis(host=redis_host, port=redis_port, db=redis_db,
                                     socket_timeout=5, socket_connect_timeout=5)
                    pubsub = r.pubsub()
                    pubsub.subscribe("cert_ready")
                    LOG.info("✅ Subscribed to cert_ready — will download instantly on certificate events")

                    for message in pubsub.listen():
                        if self.stop_event.is_set():
                            break
                        if message.get('type') == 'message':
                            try:
                                import json as _json
                                data = _json.loads(message['data'])
                                LOG.info(
                                    "🚀 cert_ready received (session=%s) — triggering immediate download",
                                    data.get('session_id', '?')
                                )
                            except Exception:
                                LOG.info("🚀 cert_ready received — triggering immediate download")
                            # Fire download immediately, don't wait for next poll
                            try:
                                self.download_updates()
                            except Exception as _dl_err:
                                LOG.warning("cert_notify download_updates error: %s", _dl_err)

                            # ── Data-checker targeted sync ──────────────────────
                            # The broadcast writes to audit_log but can silently
                            # fail to reach this client.  As a guaranteed fallback,
                            # run a row-count + checksum check on just the cert
                            # tables so any drift is corrected immediately without
                            # waiting for the next startup or full audit-log cycle.
                            if self.data_checker:
                                try:
                                    _session_id = data.get('session_id', '?') if isinstance(data, dict) else '?'
                                    LOG.info(
                                        "🔍 cert data-check: verifying cert tables for session %s",
                                        _session_id,
                                    )
                                    _dc = self.data_checker.check_specific_tables(
                                        tables=CERT_TABLES,
                                        force=False,
                                        send_checksums=True,
                                    )
                                    if _dc.get("synced_tables"):
                                        LOG.info(
                                            "🔧 cert data-check: corrected %d table(s) for session %s",
                                            len(_dc["synced_tables"]), _session_id,
                                        )
                                    else:
                                        LOG.info(
                                            "✅ cert data-check: tables in sync for session %s",
                                            _session_id,
                                        )
                                except Exception as _dc_err:
                                    LOG.warning("cert data-check error: %s", _dc_err)

                except Exception as e:
                    if not self.stop_event.is_set():
                        LOG.debug("cert_notify_listener Redis error: %s — retrying in 10s", e)
                        # Sleep then reconnect
                        for _ in range(10):
                            if self.stop_event.is_set():
                                break
                            time.sleep(1)

            LOG.info("🔔 cert_notify_listener exiting")
    def sync_pending_certificates(self):
            """Sync pending certificates to HQ for generation"""
            pending_certs = self.get_pending_certificates()

            if not pending_certs:
                LOG.debug("📋 No pending certificates to sync")
                return

            if not self.check_hq_online():
                LOG.debug("🌐 HQ server offline, skipping certificate sync")
                return

            LOG.info("📜 CERTIFICATE SYNC: Preparing to sync %d pending certificates to HQ:", len(pending_certs))

            for cert in pending_certs:
                session_id = cert.get("session_id", "unknown")
                device_info = f"{cert.get('device_model', 'Unknown')} - {cert.get('device_serial', 'Unknown')}"


            try:
                payload = {
                    "client_id": self.client_id,
                    "machine_id": self.machine_id,
                    "pending_certificates": pending_certs
                }

                LOG.info("📤 Sending certificate batch to HQ...")
                response = requests.post(
                    f"{self.api_url}/generate_certificates",
                    json=payload,
                    headers=self._http_headers(),
                    timeout=30
                )

                if response.status_code == 200:
                    result = response.json()
                    generated_count = result.get("certificates_generated", 0)

                    if generated_count > 0:
                        LOG.info("🎓 CERTIFICATE SUCCESS! HQ generated %d certificates:", generated_count)
                        for cert_info in result.get("generated", []):
                            session_id = cert_info.get("session_id")
                            cert_number = cert_info.get("certificate_number")
                            LOG.info("   📄 Session %s → %s 🎉", session_id, cert_number)
                    else:
                        LOG.info("👍 Certificate sync completed (no new certificates generated)")

                    self.process_certificate_response(result)
                else:
                    LOG.warning("❌ Certificate sync failed: %d - %s", response.status_code, response.text)

                    self.mark_certificates_failed(pending_certs, f"HTTP {response.status_code}")

            except Exception as e:
                LOG.error("💥 Error syncing certificates: %s", e)
                self.mark_certificates_failed(pending_certs, str(e))
    def get_pending_certificates(self):
            """Get pending certificates from database"""
            conn = None
            try:
                conn = self.pool.getconn()
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT
                            pc.id as pending_cert_id,
                            pc.session_id,
                            pc.machine_id,
                            pc.retry_count,
                            pc.updated_at as pending_cert_updated_at,
                            cs.device_serial,
                            cs.device_model,
                            cs.updated_at as session_updated_at
                        FROM pending_certificates pc
                        JOIN "CalSoft_calibrationsession" cs ON pc.session_id = cs.id
                        WHERE pc.sync_status = 'pending'
                        AND pc.pending_delete = false
                        AND pc.retry_count < 5
                        ORDER BY pc.updated_at DESC, cs.updated_at DESC
                        LIMIT 20
                    """)

                    pending_certs = cur.fetchall()
                    return [{
                        "pending_cert_id": str(cert["pending_cert_id"]),
                        "session_id": str(cert["session_id"]),
                        "device_serial": cert["device_serial"],
                        "device_model": cert["device_model"],
                        "machine_id": cert["machine_id"],
                        "retry_count": cert["retry_count"],
                        "updated_at": cert["session_updated_at"].isoformat() if cert["session_updated_at"] else None
                    } for cert in pending_certs]

            except Exception as e:
                LOG.error(f"Error fetching pending certificates: {e}")
                return []
            finally:
                if conn:
                    self.pool.putconn(conn)
    def process_certificate_response(self, response):
            """Process HQ response and update local sessions"""
            generated = response.get("generated", [])
            server_updated_at = response.get("server_updated_at")

            for cert_info in generated:
                session_id = cert_info["session_id"]
                certificate_number = cert_info["certificate_number"]
                pending_cert_id = cert_info.get("pending_cert_id")
                hq_updated_at = cert_info.get("updated_at", server_updated_at)

                self.update_local_certificate(session_id, certificate_number, pending_cert_id, hq_updated_at)
    def update_local_certificate(self, session_id, certificate_number, pending_cert_id=None, hq_updated_at=None):
            """Update local session with certificate number"""
            conn = None
            try:
                conn = self.pool.getconn()
                with conn.cursor() as cur:
                    if hq_updated_at:
                        try:
                            updated_time = datetime.fromisoformat(hq_updated_at.replace('Z', '+00:00'))
                            if updated_time.tzinfo is None:
                                updated_time = updated_time.replace(tzinfo=timezone.utc)
                        except (ValueError, AttributeError):
                            updated_time = datetime.now(timezone.utc)
                    else:
                        updated_time = datetime.now(timezone.utc)

                    cur.execute("""
                        UPDATE "CalSoft_calibrationsession"
                        SET certificate_number = %s, status = 'approved', updated_at = %s
                        WHERE id = %s
                    """, (certificate_number, updated_time, session_id))

                    cur.execute("""
                        UPDATE "calSchedules_calibrationschedule"
                        SET status = 'completed', completed_date = %s, updated_at = %s
                        WHERE id = (
                            SELECT schedule_id FROM "CalSoft_calibrationsession"
                            WHERE id = %s
                        )
                    """, (updated_time.date(), updated_time, session_id))

                    if pending_cert_id:
                        cur.execute("""
                            UPDATE pending_certificates
                            SET sync_status = 'completed', processed_at = %s, updated_at = %s
                            WHERE id = %s
                        """, (updated_time, updated_time, pending_cert_id))

                    conn.commit()
                    LOG.info(f"✅ Updated session {session_id} with certificate {certificate_number}")


            except Exception as e:
                LOG.error(f"Error updating session {session_id}: {e}")
                if conn:
                    conn.rollback()
            finally:
                if conn:
                    self.pool.putconn(conn)
    def mark_certificates_failed(self, certificates, error_message):
            """Mark certificates as failed"""
            if not certificates:
                return

            cert_ids = [str(cert.get('pending_cert_id')) for cert in certificates if cert.get('pending_cert_id')]

            if not cert_ids:
                return

            conn = None
            try:
                conn = self.pool.getconn()
                with conn.cursor() as cur:
                    current_time = datetime.now(timezone.utc)
                    cur.execute("""
                        UPDATE pending_certificates
                        SET sync_status = 'failed',
                            error_message = %s,
                            last_attempt = %s,
                            retry_count = retry_count + 1,
                            updated_at = %s
                        WHERE id = ANY(%s::uuid[])
                    """, (error_message, current_time, current_time, cert_ids))
                    conn.commit()

                    LOG.warning(f"❌ Marked {len(cert_ids)} certificates as failed: {error_message}")


            except Exception as e:
                LOG.error(f"Error marking certificates as failed: {e}")
                if conn:
                    conn.rollback()
            finally:
                if conn:
                    self.pool.putconn(conn)
    def reconcile_pending_certificate_for_session(self, session_id):
            """
            Reset any stale pending_certificates row for this session now that
            we know (from wherever the caller confirmed it — normal download
            poll, SSE push, or the certificate_pull_loop recovery path) that a
            real certificate exists for it.

            BUGFIX: mark_certificates_failed() sets sync_status='failed' with
            retry_count incremented, and get_pending_certificates() only ever
            considers retry_count < 5 — there is no path anywhere that resets
            either field. A session that failed 5 times over the normal push
            path but got its certificate anyway (HQ's own independent
            session-level self-heal in certificate_service_base.py, or a
            different client racing it) left its pending_certificates row
            permanently 'failed' forever: a false-positive for any monitoring
            built on that table. Call this the moment a certificate_number is
            confirmed for a session, from whichever code path noticed first.
            """
            conn = None
            try:
                conn = self.pool.getconn()
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE pending_certificates
                        SET sync_status = 'completed',
                            processed_at = NOW(),
                            updated_at = NOW()
                        WHERE session_id = %s
                        AND sync_status != 'completed'
                        """,
                        (session_id,),
                    )
                    if cur.rowcount > 0:
                        conn.commit()
                        LOG.info(
                            "🧹 Reconciled %d stale pending_certificates row(s) for session %s",
                            cur.rowcount, session_id,
                        )
                    else:
                        conn.rollback()
            except Exception as e:
                LOG.debug("Could not reconcile pending_certificates for session %s: %s", session_id, e)
                if conn:
                    conn.rollback()
            finally:
                if conn:
                    self.pool.putconn(conn)
    def certificate_sync_loop(self):
            """Periodically sync pending certificates with HQ"""
            interval = int(self.sync_cfg.get("certificate_sync_interval", 30))

            LOG.info("📜 Certificate sync loop started (checking every %d seconds) 🎓", interval)


            loop_count = 0
            while not self.stop_event.is_set():
                try:
                    loop_count += 1
                    LOG.debug("📋 Certificate check #%d...", loop_count)
                    self.sync_pending_certificates()
                except Exception as e:
                    LOG.exception("💥 Exception in certificate_sync_loop: %s", e)

                for i in range(interval):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

            LOG.info("📜 Certificate sync loop exiting")
    def send_heartbeat(self):
            """Send heartbeat to HQ server"""
            try:
                # agent_status.json (write_status_file(), sync_agent_9.py) was
                # previously local-only — useful for an in-app UI, but if the
                # whole app isn't running (the failure mode behind the original
                # 11h-stuck-certificates incident) nobody was looking at it.
                # Piggyback it on the heartbeat that's already sent every
                # `heartbeat_interval` seconds so HQ's dashboard/alerting can
                # see per-client sync health, not just "was it reachable".
                try:
                    local_status = self.get_status_summary()
                except Exception:
                    local_status = {}

                payload = {
                    "client_id": self.client_id,
                    "machine_id": self.machine_id,
                    "version": self.version,
                    "status": "active",
                    "last_upload": self.state.get("last_upload_time"),
                    "last_download": self.state.get("last_download_time"),
                    "local_record_count": self.get_local_record_count(),  # lets HQ detect empty-DB reinstalls
                    "agent_status": {
                        "hq_online": local_status.get("hq_online"),
                        "pending_changes": local_status.get("pending_changes"),
                        "unreviewed_conflicts": local_status.get("unreviewed_conflicts"),
                        "schema_drift": local_status.get("schema_drift"),
                        "last_status_update": local_status.get("last_update"),
                    },
                }

                response = requests.post(
                    f"{self.api_url}/heartbeat",
                    json=payload,
                    headers=self._http_headers(),
                    timeout=5
                )

                if response.status_code == 200:
                    LOG.debug("Heartbeat sent successfully")
                elif response.status_code in (401, 403):
                    self.note_hq_rejected_key(response.status_code, "heartbeat")
                else:
                    LOG.warning("Heartbeat failed: %d", response.status_code)

            except Exception as e:
                LOG.debug("Heartbeat failed: %s", e)
    def heartbeat_loop(self):
            """Periodically send heartbeat to HQ"""
            interval = int(self.sync_cfg.get("heartbeat_interval", 60))

            LOG.info("Heartbeat loop started (interval=%ds)", interval)

            while not self.stop_event.is_set():
                try:
                    self.send_heartbeat()
                except Exception as e:
                    LOG.debug("Heartbeat error: %s", e)

                for _ in range(interval):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

            LOG.info("Heartbeat loop exiting")
