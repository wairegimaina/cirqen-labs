from .agent_prelude import LOG, now_kenyan, now_utc, now_iso, format_kenyan_time
from .agent_prelude import (
    KENYAN_TZ,
    PerformanceMonitor,
    encrypt_token,
    setup_logging,
    load_agent_config,
)
from .agent_prelude import load_config_from_unified_manager, load_config_from_env_fallback
from .agent_prelude import sleep_with_jitter, is_online, CERT_TABLES, DEFAULT_CONFIG
from .agent_prelude import REDIS_AVAILABLE, DatabaseMirror
import os
import logging
import threading

try:
    import redis
except ImportError:
    redis = None
try:
    from .data_checker_client import DataCheckerClient

    DATA_CHECKER_AVAILABLE = True
except ImportError:
    DATA_CHECKER_AVAILABLE = False
    DataCheckerClient = None
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
try:
    from .sql_ident import qualified
except ImportError:  # loaded as a top-level module with sync/ on sys.path
    from sql_ident import qualified


class AgentInitMixin(SmartDeleteMixin):
    """Agent construction: config load, Redis/pool wiring, client registration, mirror init, and data validation helpers."""

    def __init__(self, config: Dict[str, Any] = None, data_path: Path = None):
        """
        Initialize SyncAgent with unified configuration support

        Args:
            config: Optional pre-loaded configuration dict
            data_path: Path to application data directory (for config.py)
        """
        # 🆕 Load configuration intelligently
        if config is None:
            config = load_agent_config(data_path)

        self.config = config
        # Where config.json and endpoints.json live; the endpoint poll needs it.
        self.data_path = Path(data_path) if data_path else Path.home() / ".cmms"
        self.api_url = self.config["api_url"].rstrip("/")

        # ============================================================
        # 🔧 FIX: Extract base URL for data checker (removes /api/sync path)
        # ============================================================
        # The DataCheckerClient expects a base URL without the /api/sync suffix
        # This ensures the correct URL: https://hq-server.../api/sync/data_checker
        if "/api/" in self.api_url:
            self.hq_base_url = self.api_url.split("/api/")[0]
        else:
            self.hq_base_url = self.api_url

        LOG.info(f"   API URL: {self.api_url}")
        LOG.info(f"   HQ Base URL: {self.hq_base_url}")

        self.auth_token = self.config.get("auth_token") or None
        if self.auth_token:
            LOG.info(
                "✅ Auth token loaded (%d chars) — X-API-Key will be sent with every HQ request",
                len(self.auth_token),
            )
        else:
            LOG.warning(
                "⚠️  No auth_token in config — HQ will reject all API calls. "
                "Check sync.auth_token in config.json"
            )

        redis_client = None
        if REDIS_AVAILABLE and self.config["redis"].get("enabled", True):
            try:
                redis_client = self._init_redis()
                LOG.info("✅ Redis connected (using for performance boost)")
            except Exception as e:
                LOG.warning("⚠️  Redis unavailable, using file-only mode: %s", e)

        # Store redis_client for later use
        self.redis = redis_client

        # Initialize state manager with file-based persistence
        state_dir = self.config["sync"].get("state_dir", "~/.cmms")
        self.state = StateManager(state_dir, redis_client)

        # Ensure file logging is active even when launched from a thread.
        # main() calls setup_logging() explicitly; if SyncAgent is instantiated
        # directly (e.g. from main.py SyncAgentThread), loggers only have the
        # console handler added at module import.  We attach a file handler here
        # so every instantiation path produces a log file.
        _resolved_state_dir = Path(os.path.expanduser(state_dir))
        _log_path = str(_resolved_state_dir.parent / "logs" / "sync_agent.log")
        if not any(isinstance(h, logging.FileHandler) for h in LOG.handlers):
            _log_level = getattr(
                logging, os.getenv("SYNC_LOG_LEVEL", "DEBUG").upper(), logging.DEBUG
            )
            setup_logging(log_file=_log_path, level=_log_level)
            LOG.info("SyncAgent: file logging enabled -> %s", _log_path)

        # Initialize machine identification
        self.machine_id = self.get_machine_identifier()
        self.client_name = os.getenv("CLIENT_NAME", f"Workshop-{self.machine_id}")
        self.version = "4.0.0"  # Updated version with active/inactive status system

        # Initialize database pool
        self.pool = self._create_db_pool(self.config["local_db"])

        # Initialize dependency manager
        self.dep_manager = DependencyManager(self.pool)

        # Initialize sync configuration
        self.sync_cfg = self.config["sync"]
        self.tables = self.config["tables"]

        # Verify tables have updated_at columns
        self.verify_tables_have_updated_at()

        # Initialize client_id
        self.client_id = self.auto_register_client()

        # First run of a new install: exchange the installer's enrollment code
        # for this client's own key (sync/enrollment.py).
        if not self.auth_token:
            try:
                from .enrollment import ensure_client_key_for_data_path
            except ImportError:
                from enrollment import ensure_client_key_for_data_path
            self.auth_token = ensure_client_key_for_data_path(
                data_path, self.hq_base_url, self.client_id
            ) or None

        # Thread management
        self.stop_event = threading.Event()
        self.threads = []

        # Cache for JSON columns and table schema info
        self._json_columns_cache = {}
        self._table_schema_cache = {}

        self.mirror_enabled = os.getenv("MIRROR_ENABLED", "false").lower() == "true"
        self.mirror_interval_hours = float(os.getenv("MIRROR_INTERVAL_HOURS", "24"))
        self.mirror = None

        # Initialize mirror if enabled
        if self.mirror_enabled:
            self.initialize_mirror_system()

        LOG.info("=" * 60)
        LOG.info("🔄 Mirror System: %s", "ENABLED" if self.mirror_enabled else "DISABLED")
        if self.mirror_enabled:
            LOG.info("   Interval: %.1fh", self.mirror_interval_hours)
        LOG.info("=" * 60)

        LOG.info("=" * 60)

        # ── Data checker (bootstrap + integrity) ──────────────────
        self.data_checker = None
        if DATA_CHECKER_AVAILABLE:
            self.data_checker = DataCheckerClient(
                hq_url=self.hq_base_url,  # ✅ FIXED: Use base URL without /api/sync
                api_key=self.auth_token or "",
                client_id=self.client_id,
                local_pool=self.pool,
                allowed_tables=self.tables,
                logger=LOG,
            )
            LOG.info("✅ DataCheckerClient ready")
        else:
            LOG.warning("⚠️  DataCheckerClient unavailable — bootstrap check disabled")
        # ──────────────────────────────────────────────────────────

        LOG.info("=" * 60)
        LOG.info("SyncAgent v%s initialized", self.version)
        LOG.info("  Machine: %s", self.machine_id)
        LOG.info("  Client: %s", self.client_id)
        LOG.info("  Tables: %d", len(self.tables))
        LOG.info("  Status System: Active/Inactive with cascade support")
        LOG.info("  Soft Delete: pending_delete → HQ hard delete → propagate")
        LOG.info("  Connection: Waits for HQ, immediate sync on reconnect")
        LOG.info("  Crash recovery: Triple redundancy enabled")
        LOG.info("=" * 60)


    def _init_redis(self):
        """
        Initialize Redis connection with dynamic port support.

        The main application allocates ports dynamically at startup and writes
        the actual assigned ports to session.json.  config.json may contain a
        stale port (e.g. 7788) while the app is actually running on 7789.
        We read session.json first so the sync agent always connects to the
        correct Redis instance.
        """
        import json as _json

        redis_cfg = self.config["redis"]
        host = redis_cfg.get("host", "127.0.0.1")
        port = int(redis_cfg.get("port", 7788))

        # ── Dynamic port: prefer the port recorded in session.json ──────────
        # session.json is written by PortManager on every startup and contains
        # the ports actually in use for this session.
        session_search_paths = [
            Path.home() / ".local/share/cirqen/session.json",
            Path.home() / ".cmms/session.json",
        ]
        for sf in session_search_paths:
            if sf.exists():
                try:
                    session = _json.loads(sf.read_text())
                    dynamic_port = session.get("ports", {}).get("redis") or session.get(
                        "redis_port"
                    )
                    if dynamic_port and int(dynamic_port) != port:
                        LOG.info(
                            "[Redis] Dynamic port detected: using %d "
                            "(config.json says %d, session.json says %d)",
                            int(dynamic_port),
                            port,
                            int(dynamic_port),
                        )
                        port = int(dynamic_port)
                    break
                except Exception as _e:
                    LOG.debug("[Redis] Could not read session.json: %s", _e)
        # ────────────────────────────────────────────────────────────────────

        r = redis.Redis(
            host=host,
            port=port,
            db=int(redis_cfg.get("db", 0)),
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            password=redis_cfg.get("password") or None,
        )
        r.ping()
        LOG.info("[Redis] Connected on %s:%d", host, port)
        return r


    def pull_missing_certificates_from_hq(self):
        """
        ✅ NEW: Actively pull missing certificates from HQ

        Called by consistency checker to fetch certificates that were
        generated on HQ but didn't sync down properly.
        """
        LOG.info("📥 Pulling missing certificates from HQ...")

        conn = None
        try:
            conn = self.pool.getconn()

            # Find sessions that should have certificates but don't
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                        SELECT
                            id,
                            status,
                            schedule_id,
                            created_at,
                            updated_at
                        FROM "CalSoft_calibrationsession"
                        WHERE status IN ('approved', 'approved_pending_certificate')
                        AND (certificate_number IS NULL OR certificate_number = '')
                        AND created_at < NOW() - INTERVAL '1 hour'
                        ORDER BY created_at DESC
                        LIMIT 50
                    """)

                local_missing = cur.fetchall()

                if not local_missing:
                    LOG.info("   ✅ No missing certificates found locally")
                    return 0

                LOG.info(f"   Found {len(local_missing)} sessions potentially missing certificates")

                # Pull from HQ
                import requests

                session_ids = [str(s["id"]) for s in local_missing]

                response = requests.post(
                    f"{self.api_url}/pull_certificates",
                    json={"client_id": self.client_id, "session_ids": session_ids},
                    headers=self._http_headers(),
                    timeout=30,
                )

                if response.status_code != 200:
                    LOG.error(f"   ❌ Pull failed: {response.status_code}")
                    return 0

                data = response.json()
                hq_sessions = data.get("sessions", [])

                LOG.info(f"   📦 HQ returned {len(hq_sessions)} sessions with certificates")

                if not hq_sessions:
                    LOG.info("   ℹ️  HQ has no certificates for these sessions yet")
                    return 0

                # Apply updates locally
                updated_count = 0
                schedule_updates = []

                for hq_session in hq_sessions:
                    session_id = hq_session["id"]
                    cert_number = hq_session["certificate_number"]
                    schedule_id = hq_session.get("schedule_id")

                    LOG.info(f"   📝 Updating session {session_id} with certificate {cert_number}")

                    cur.execute(
                        """
                            UPDATE "CalSoft_calibrationsession"
                            SET certificate_number = %s,
                                status = 'approved',
                                updated_at = NOW()
                            WHERE id = %s
                        """,
                        (cert_number, session_id),
                    )

                    if cur.rowcount > 0:
                        updated_count += 1
                        LOG.info(f"      ✅ Updated locally")

                        # ── Un-stick any pending_certificates row for this session ──
                        # BUGFIX: get_pending_certificates() (sync_agent_7.py) only
                        # ever considers rows with retry_count < 5, and
                        # mark_certificates_failed() sets sync_status='failed' with
                        # no code path anywhere that resets it. A session that failed
                        # to generate 5 times over the normal push path but got its
                        # certificate anyway via this pull-recovery path (or HQ's own
                        # independent session-level self-heal) would otherwise leave
                        # its pending_certificates row permanently marked 'failed'
                        # forever — a false-positive for anyone monitoring that table.
                        # Reconcile it here since we just confirmed HQ has the cert.
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
                            LOG.info(
                                f"      🧹 Reconciled {cur.rowcount} stale pending_certificates "
                                f"row(s) for session {session_id} (now confirmed via HQ)"
                            )

                        # Also update schedule if needed
                        if schedule_id and hq_session.get("schedule_status") == "completed":
                            schedule_updates.append((schedule_id, hq_session.get("completed_date")))

                # Update schedules
                for schedule_id, completed_date in schedule_updates:
                    cur.execute(
                        """
                            UPDATE "calSchedules_calibrationschedule"
                            SET status = 'completed',
                                completed_date = %s,
                                updated_at = NOW()
                            WHERE id = %s
                            AND status != 'completed'
                        """,
                        (completed_date, schedule_id),
                    )

                    if cur.rowcount > 0:
                        LOG.info(f"      ✅ Updated schedule {schedule_id} to completed")

                conn.commit()

                LOG.info(f"   🎉 Successfully pulled and applied {updated_count} certificates!")
                return updated_count

        except Exception as e:
            LOG.error(f"   ❌ Certificate pull failed: {e}")
            LOG.exception(e)
            if conn:
                conn.rollback()
            return 0

        finally:
            if conn:
                self.pool.putconn(conn)

    def get_required_columns(self, table: str) -> List[str]:
        """
        Get list of NOT NULL columns for a table (excluding those with defaults)

        Args:
            table: Table name (with or without schema)

        Returns:
            List of column names that are required (NOT NULL, no default)
        """
        try:
            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            with psycopg2.connect(**self.config["local_db"]) as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(
                        """
                            SELECT column_name
                            FROM information_schema.columns
                            WHERE table_schema = %s
                            AND table_name = %s
                            AND is_nullable = 'NO'
                            AND column_default IS NULL
                            AND column_name NOT IN ('id', 'created_at', 'updated_at')
                            AND column_name NOT LIKE '%%_at'
                        """,
                        (schema, tbl),
                    )

                    columns = [row["column_name"] for row in cur.fetchall()]
                    LOG.debug(f"Required columns for {table}: {columns}")
                    return columns

        except Exception as e:
            LOG.warning(f"Failed to get required columns for {table}: {e}")
            return []

    def validate_update_data(
        self, table: str, row_id: str, data: dict, operation: str
    ) -> Tuple[bool, str]:
        """
        Validate that update data is complete before applying

        Args:
            table: Table name
            row_id: Row ID
            data: Update data dictionary
            operation: Operation type (u, d, activate, deactivate)

        Returns:
            (is_valid, error_message)
        """
        # Delete operations don't need data validation
        if operation in ["d"]:
            return True, ""

        # Check if we have any data
        if not data or data == {}:
            return False, f"No data received from HQ for operation '{operation}'"

        # Ensure row_id is in the data (critical for upsert)
        if "id" not in data or data["id"] is None:
            LOG.warning(f"⚠️  Adding missing id field for {table} id={row_id}")
            data["id"] = row_id

        # For deactivate/activate operations, we might not need all fields
        if operation in ["deactivate", "activate"]:
            # Just need id and status fields
            return True, ""

        # Validate required fields for INSERT/UPDATE operations
        required_columns = self.get_required_columns(table)
        missing_required = []

        for col in required_columns:
            if col not in data or data[col] is None:
                missing_required.append(col)

        if missing_required:
            error_msg = f"Missing required fields: {missing_required}"
            return False, error_msg

        return True, ""

    def certificate_pull_loop(self):
        """
        Background thread that actively pulls missing certificates.

        Runs every CERT_PULL_INTERVAL seconds (default 60s — NOT 15 minutes;
        an earlier version of this docstring said 15 min but the code default
        was already 60s, which is the more useful value for faster recovery,
        so the default was left as-is and only the stale comment is fixed).
        """
        import time

        pull_interval = int(os.getenv("CERT_PULL_INTERVAL", "60"))

        LOG.info(
            f"📥 Certificate pull loop started (interval: {pull_interval}s / {pull_interval/60:.1f}m)"
        )

        first_run = True
        pull_count = 0

        while not self.stop_event.is_set():
            try:
                if first_run:
                    # Wait 2 minutes after startup
                    LOG.info("⏳ Waiting 2 minutes before first certificate pull...")
                    for _ in range(120):
                        if self.stop_event.is_set():
                            return
                        time.sleep(1)
                    first_run = False

                pull_count += 1

                LOG.info("")
                LOG.info(f"📥 Certificate Pull #{pull_count}")

                updated = self.pull_missing_certificates_from_hq()

                if updated > 0:
                    LOG.info(f"   ✅ Pulled {updated} certificates from HQ")
                else:
                    LOG.debug(f"   ℹ️  No certificates to pull")

            except Exception as e:
                LOG.error(f"💥 Certificate pull error: {e}")
                LOG.exception(e)

            # Wait for next interval
            for _ in range(pull_interval):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

        LOG.info("📥 Certificate pull loop exiting")

    def initialize_mirror_system(self):
        """Initialize mirror system within SyncAgent"""
        try:
            LOG.info("🔄 Initializing integrated mirror system...")

            # Create HQ config — sourced from the same loaded config as
            # local_config below (config.json / unified manager), NOT from
            # raw os.getenv() calls. Re-reading env vars here with hardcoded
            # fallback defaults let this silently diverge from config.json
            # (e.g. still pointing at an old/decommissioned Render host after
            # hq_db was migrated to Supabase). sslmode is required by
            # Supabase's pooler; it's a no-op/harmless for plain Postgres.
            hq_config = dict(self.config["hq_db"])
            hq_config.pop("enabled", None)
            hq_config.setdefault("sslmode", os.getenv("POSTGRES_SSLMODE", "require"))

            # Use existing local config
            local_config = self.config["local_db"]

            self.mirror = DatabaseMirror(
                hq_config=hq_config,
                local_config=local_config,
                api_url=self.api_url,
                auth_token=self.auth_token,
                client_id=self.client_id,
                machine_id=self.machine_id,
            )

            LOG.info("✅ Mirror system initialized")
            return True

        except Exception as e:
            LOG.error(f"❌ Mirror initialization failed: {e}")
            return False

    def check_if_new_machine(self) -> bool:
        """Check if this is a new machine that needs initial sync"""
        try:
            conn = self.pool.getconn()

            try:
                with conn.cursor() as cur:
                    if self.tables:
                        first_table = self.tables[0]

                        if "." in first_table:
                            schema, tbl = first_table.split(".", 1)
                        else:
                            schema, tbl = "public", first_table

                        full_table = qualified(schema, tbl)

                        cur.execute(f"SELECT COUNT(*) FROM {full_table} LIMIT 1")
                        count = cur.fetchone()[0]

                        if count == 0:
                            LOG.info("🆕 NEW MACHINE DETECTED (no data)")
                            return True
                        else:
                            LOG.info(f"✅ Existing machine ({count} records)")
                            return False
            finally:
                self.pool.putconn(conn)

        except Exception as e:
            LOG.warning(f"⚠️  Could not check if new machine: {e}")
            return False

    def get_local_record_count(self) -> int:
        """
        Return the total number of records in the first configured table.
        Used to detect empty DB on new install or reinstall.
        Returns 0 on any error (safe default — triggers initial sync).
        """
        try:
            if not self.tables:
                return 0

            first_table = self.tables[0]
            if "." in first_table:
                schema, tbl = first_table.split(".", 1)
            else:
                schema, tbl = "public", first_table

            full_table = qualified(schema, tbl)
            conn = self.pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT COUNT(*) FROM {full_table} LIMIT 1")
                    return cur.fetchone()[0]
            finally:
                self.pool.putconn(conn)
        except Exception as e:
            LOG.warning("Could not get local record count: %s", e)
            return 0

    def perform_initial_mirror_sync(self) -> bool:
        """Perform initial mirror sync for new machine"""
        if not self.mirror:
            LOG.error("❌ Mirror system not initialized")
            return False

        try:
            LOG.info("=" * 80)
            LOG.info("🆕 INITIAL MIRROR SYNC - DOWNLOADING ALL DATA FROM HQ")
            LOG.info("=" * 80)

            if not self.mirror.connect():
                return False

            try:
                success = self.mirror.initial_sync_new_machine(self.tables)

                if success:
                    LOG.info("🎉 Initial sync complete! Machine fully synchronized.")

                return success
            finally:
                self.mirror.disconnect()

        except Exception as e:
            LOG.error(f"❌ Initial mirror sync failed: {e}")
            return False
