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

class SyncAgent(SmartDeleteMixin):
    def download_loop(self):
            """Enhanced download loop with connection awareness"""
            interval = int(self.sync_cfg.get("download_interval_seconds", 15))

            LOG.info("📥 Download loop started (checking every %d seconds) 🔄", interval)

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

                for i in range(interval):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

            LOG.info("📥 Download loop exiting")
    def sync_notify_listener(self):
            """
            Subscribe to HQ Server-Sent Events for instant table updates.
            Redis pub/sub stays server-side only; clients receive events over HTTP.
            """
            while not self.stop_event.is_set():
                url = f"{self.api_url}/events"
                headers = self._http_headers()
                headers["Accept"] = "text/event-stream"
                headers["X-Instant-Download"] = "1"

                try:
                    with requests.get(url, headers=headers, stream=True, timeout=(10, 300)) as response:
                        if response.status_code != 200:
                            LOG.warning("SSE sync listener rejected by HQ: %s %s", response.status_code, response.text[:200])
                            time.sleep(10)
                            continue

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
                                origin_client = payload.get("client_id")
                                if origin_client and origin_client == self.client_id:
                                    continue
                                LOG.info("📡 SSE sync update received from HQ: %s", payload)
                                try:
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
                        WHERE id = ANY(%s)
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
                payload = {
                    "client_id": self.client_id,
                    "machine_id": self.machine_id,
                    "version": self.version,
                    "status": "active",
                    "last_upload": self.state.get("last_upload_time"),
                    "last_download": self.state.get("last_download_time"),
                    "local_record_count": self.get_local_record_count(),  # lets HQ detect empty-DB reinstalls
                }

                response = requests.post(
                    f"{self.api_url}/heartbeat",
                    json=payload,
                    headers=self._http_headers(),
                    timeout=5
                )

                if response.status_code == 200:
                    LOG.debug("Heartbeat sent successfully")
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

class SyncAgent(SmartDeleteMixin):
    def download_loop(self):
            """Enhanced download loop with connection awareness"""
            interval = int(self.sync_cfg.get("download_interval_seconds", 15))

            LOG.info("📥 Download loop started (checking every %d seconds) 🔄", interval)

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

                for i in range(interval):
                    if self.stop_event.is_set():
                        break
                    time.sleep(1)

            LOG.info("📥 Download loop exiting")
    def sync_notify_listener(self):
            """
            Subscribe to HQ Server-Sent Events for instant table updates.
            Redis pub/sub stays server-side only; clients receive events over HTTP.
            """
            while not self.stop_event.is_set():
                url = f"{self.api_url}/events"
                headers = self._http_headers()
                headers["Accept"] = "text/event-stream"
                headers["X-Instant-Download"] = "1"

                try:
                    with requests.get(url, headers=headers, stream=True, timeout=(10, 300)) as response:
                        if response.status_code != 200:
                            LOG.warning("SSE sync listener rejected by HQ: %s %s", response.status_code, response.text[:200])
                            time.sleep(10)
                            continue

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
                        WHERE id = ANY(%s)
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
                payload = {
                    "client_id": self.client_id,
                    "machine_id": self.machine_id,
                    "version": self.version,
                    "status": "active",
                    "last_upload": self.state.get("last_upload_time"),
                    "last_download": self.state.get("last_download_time"),
                    "local_record_count": self.get_local_record_count(),  # lets HQ detect empty-DB reinstalls
                }

                response = requests.post(
                    f"{self.api_url}/heartbeat",
                    json=payload,
                    headers=self._http_headers(),
                    timeout=5
                )

                if response.status_code == 200:
                    LOG.debug("Heartbeat sent successfully")
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
