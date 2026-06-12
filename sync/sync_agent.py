#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_agent_enhanced.py - OPTIMIZED: Instant uploads + Kenyan timezone
Version: 4.1.0

Key Optimizations:
1. ⚡ INSTANT UPLOADS: 1-second polling (down from 10s)
2. 🇰🇪 KENYAN TIME: All logs show EAT (UTC+3)
3. 🚀 FASTER QUERIES: Optimized change detection
4. 📊 BETTER BATCHING: Smarter batch sizes
"""

import os
import sys
import json
import time
import uuid
import signal
import logging
import threading
import traceback
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
try:
    from sync.data_checker_client import DataCheckerClient
    DATA_CHECKER_AVAILABLE = True
except ImportError:
    DATA_CHECKER_AVAILABLE = False
from dotenv import load_dotenv
import pytz
# ============================================================
# 🆕 IMPORT UNIFIED CONFIG MANAGER
# ============================================================
try:
    from config import CirqenConfig, load_config
    CONFIG_MANAGER_AVAILABLE = True
except ImportError as e:
    CONFIG_MANAGER_AVAILABLE = False
    from dotenv import load_dotenv

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

# ============================================================
# REDIS QUEUE INTEGRATION
# ============================================================
if REDIS_AVAILABLE:
    try:
        from sync.sync_agent_redis_queue import RedisQueueSync
        REDIS_QUEUE_AVAILABLE = True
    except ImportError as e:
        REDIS_QUEUE_AVAILABLE = False
        RedisQueueSync = None
else:
    REDIS_QUEUE_AVAILABLE = False
    RedisQueueSync = None



# ============================================================
# DEVICE ID AUTO-GENERATION MODULE
# ============================================================

# Try to import device_id module for auto-generation
try:
    from sync.device_id_generator import get_or_create_client_id, get_device_info
    DEVICE_ID_MODULE_AVAILABLE = True
except ImportError:
    DEVICE_ID_MODULE_AVAILABLE = False

    # Fallback function if module not available
    def get_or_create_client_id(state_manager=None):
        """Fallback: use environment or generate simple ID"""
        import uuid
        import hashlib
        client_id = os.getenv('CLIENT_ID', '').strip()
        if client_id:
            return client_id
        # Try state manager
        if state_manager:
            try:
                saved_id = state_manager.get_client_id()
                if saved_id:
                    return saved_id
            except:
                pass
        # Simple MAC-based fallback
        mac = uuid.getnode()
        mac_hash = hashlib.sha1(str(mac).encode()).hexdigest()[:12]
        generated_id = f"mac-{mac_hash}"
        if state_manager:
            try:
                state_manager.set_client_id(generated_id)
            except:
                pass
        return generated_id

# ============================================================
# OPTIONAL DEPENDENCY DETECTION
# ============================================================

# Try to import Redis, but make it optional
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    # Will log warning after logger is set up

# Try to import NetworkX for dependency sorting
try:
    import networkx as nx
    NETWORKX_AVAILABLE = True
except ImportError:
    NETWORKX_AVAILABLE = False
    # Will log warning after logger is set up
try:
    from sync.mirror import DatabaseMirror, SyncDirection
    MIRROR_AVAILABLE = True
except ImportError as e:
    MIRROR_AVAILABLE = False
    DatabaseMirror = None
    SyncDirection = None

# ============================================================
# 🇰🇪 KENYAN TIMEZONE SETUP
# ============================================================

KENYAN_TZ = pytz.timezone('Africa/Nairobi')  # EAT (UTC+3)

# Tables that must be re-checked immediately after a certificate is generated.
# These are the only rows that change during cert generation so we target them
# specifically instead of running a full check across all tables.
CERT_TABLES = [
    "public.CalSoft_calibrationsession",
    "public.CalSoft_calibrationreport",
    "public.calSchedules_calibrationschedule",
]

def now_kenyan() -> datetime:
    """Get current time in Kenyan timezone"""
    return datetime.now(KENYAN_TZ)

def now_utc() -> datetime:
    """Get current time in UTC (for database storage)"""
    return datetime.now(timezone.utc)

def format_kenyan_time(dt: datetime) -> str:
    """Format datetime in Kenyan timezone for display"""
    if dt is None:
        return "N/A"

    # Convert to Kenyan time if not already
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    kenyan_dt = dt.astimezone(KENYAN_TZ)
    return kenyan_dt.strftime("%Y-%m-%d %H:%M:%S EAT")

def now_iso() -> str:
    """ISO timestamp in UTC (for storage)"""
    return now_utc().isoformat()

def rotate_api_key(self, new_key: str):
    """Rotate API key without downtime"""
    self.auth_token = new_key



def encrypt_token(token: str, key: bytes) -> str:
    """Encrypt sensitive tokens"""
    f = Fernet(key)
    return f.encrypt(token.encode()).decode()
# ============================================================
# CUSTOM LOGGING FORMATTER WITH KENYAN TIME
# ============================================================

class KenyanTimeFormatter(logging.Formatter):
    """Logging formatter that uses Kenyan time"""

    def formatTime(self, record, datefmt=None):
        """Override to use Kenyan time"""
        ct = datetime.fromtimestamp(record.created, tz=KENYAN_TZ)
        if datefmt:
            s = ct.strftime(datefmt)
        else:
            s = ct.strftime("%Y-%m-%d %H:%M:%S")
        return s

    def format(self, record):
        # Add timezone indicator to the message
        result = super().format(record)
        return result

# ============================================================
#  EMOJI ENCODING
# ============================================================

import locale

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except AttributeError:
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'replace')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')

try:
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
except locale.Error:
    try:
        locale.setlocale(locale.LC_ALL, 'C.UTF-8')
    except locale.Error:
        pass

# ============================================================
# SETUP LOGGING WITH KENYAN TIME
# ============================================================

# All logger names used across the sync subsystem.
_SYNC_LOGGER_NAMES = [
    "sync_agent",
    "sync_agent_optimized",
    "mirror_sync",
    "data_checker_client",
    "RedisQueueSync",
]

_KENYAN_FORMATTER = KenyanTimeFormatter(
    "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S EAT"
)


def setup_logging(log_file: str = None, level: int = logging.DEBUG) -> None:
    """
    Configure every sync-subsystem logger to write at *level* to the log
    file only — no terminal/console output.

    Call once at module import (file handler only) and again inside
    SyncAgent.__init__() / main() once the log-file path is known.
    """
    handlers: list = []

    if log_file:
        try:
            import os as _os
            _os.makedirs(_os.path.dirname(_os.path.abspath(log_file)), exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
            file_handler.setFormatter(_KENYAN_FORMATTER)
            file_handler.setLevel(level)
            handlers.append(file_handler)
        except Exception as _e:
            pass  # Silently skip if log file cannot be opened

    # Apply to every subsystem logger
    for name in _SYNC_LOGGER_NAMES:
        logger = logging.getLogger(name)
        logger.setLevel(level)
        logger.handlers = []
        logger.propagate = False
        for h in handlers:
            logger.addHandler(h)

    # Silence the root logger so third-party libraries produce no terminal noise
    root = logging.getLogger()
    root.handlers = []
    root.setLevel(logging.CRITICAL)


# Module-level bootstrap: console only, DEBUG level.
setup_logging()

# Single canonical logger used everywhere in this module.
LOG = logging.getLogger("sync_agent_optimized")

# ============================================================
# LOG OPTIONAL DEPENDENCY STATUS (after logger is set up)
# ============================================================

if not REDIS_AVAILABLE:
    LOG.warning("redis-python not installed. Using file-based persistence only.")

if not NETWORKX_AVAILABLE:
    LOG.warning("networkx not installed. Dependency sorting will use simple fallback.")

# Try to import smart delete handler (needs LOG to be set up first)
try:
    from sync.soft_delete_handler import (
        perform_smart_delete,
        SoftDeleteDependencyChecker,
        get_column_type,
        convert_boolean_for_column
    )
    SMART_DELETE_AVAILABLE = True
    LOG.info("✅ Smart delete handler available")
except ImportError as e:
    SMART_DELETE_AVAILABLE = False
    LOG.warning(f"⚠️ Smart delete handler not available: {e}")
    LOG.warning("   Deletes will use simple soft delete without cascade")

# ============================================================
# OPTIMIZED CONFIGURATION
# ============================================================

def load_config_from_unified_manager(data_path: Path = None):
    """
    Load configuration using the unified config manager

    Args:
        data_path: Path to application data directory (default: ~/.cmms)

    Returns:
        Dictionary compatible with sync_agent.py expectations
    """
    if data_path is None:
        data_path = Path.home() / '.cmms'

    data_path.mkdir(parents=True, exist_ok=True)

    # Initialize config manager
    config = load_config(data_path)

    # Setup environment variables for any code that still reads from env
    config.setup_environment_variables()

    # Convert to sync_agent.py expected format
    sync_config = {
        "api_url": config.get('sync.api_url'),
        "auth_token": config.get('sync.auth_token'),

        "local_db": {
            "host": config.get('local_db.host'),
            "port": config.get('local_db.port'),
            "dbname": config.get('local_db.database'),
            "user": config.get('local_db.user'),
            "password": config.get('local_db.password')
        },

        "redis": {
            "host": config.get('redis.host'),
            "port": config.get('redis.port'),
            "db": 0,
            "password": config.get('redis.password'),
            "enabled": config.get('redis.enabled')
        },

        "sync": {
            "poll_interval_seconds": config.get('sync.poll_interval'),
            "upload_batch_size": config.get('sync.upload_batch_size'),
            "download_interval_seconds": config.get('sync.download_interval'),
            "max_upload_retries": config.get('sync.max_retries'),
            "retry_backoff_base": config.get('sync.retry_backoff'),
            "max_retry_backoff": 300.0,
            "heartbeat_interval": config.get('sync.heartbeat_interval'),
            "certificate_sync_interval": config.get('sync.certificate_interval'),
            "conflict_resolution": config.get('sync.conflict_resolution'),
            "state_dir": str(data_path / 'sync_state'),
            "wait_for_hq": config.get('sync.wait_for_hq'),
            "max_wait_for_hq": config.get('sync.max_wait_for_hq')
        },

        "tables": config.get('sync_tables'),

        "client": {
            "name": config.get('client.name')
        },

        "mirror": {
            "enabled": config.get('mirror.enabled'),
            "interval_hours": config.get('mirror.interval_hours')
        },

        "hq_db": {
            "host": config.get('hq_db.host'),
            "port": config.get('hq_db.port'),
            "dbname": config.get('hq_db.database'),
            "user": config.get('hq_db.user'),
            "password": config.get('hq_db.password')
        }
    }

    LOG.info("=" * 80)
    LOG.info("🔧 CONFIGURATION LOADED FROM CONFIG MANAGER")
    LOG.info("=" * 80)
    LOG.info("📍 Data Directory: %s", data_path)
    LOG.info("🔄 Config Source: %s", "config.json" if not config.use_env_file else ".env file")
    LOG.info("")
    LOG.info("📊 Configuration Summary:")
    LOG.info("  • Sync API: %s", sync_config['api_url'])
    LOG.info("  • Local DB: %s:%s/%s",
             sync_config['local_db']['host'],
             sync_config['local_db']['port'],
             sync_config['local_db']['dbname'])
    LOG.info("  • Tables: %d", len(sync_config['tables']))
    LOG.info("  • Poll Interval: %ds ⚡", sync_config['sync']['poll_interval_seconds'])
    LOG.info("=" * 80)

    return sync_config


def load_config_from_env_fallback():
    """
    Fallback configuration loader using dotenv (legacy mode)
    Used when config.py is not available
    """
    LOG.warning("⚠️  Using legacy .env configuration")
    LOG.warning("   Consider migrating to config.py for better management")

    from dotenv import load_dotenv
    load_dotenv()

    return {
        "api_url": os.getenv("SYNC_API_URL", "https://hq-server-dgs6.onrender.com/api/sync"),
        "auth_token": os.getenv("SYNC_AUTH_TOKEN", ""),
        "local_db": {
            "host": os.getenv("POSTGRES_LOCAL_HOST", "cirqenlocal.cp4208se4zb7.eu-north-1.rds.amazonaws.com"),
            "port": int(os.getenv("POSTGRES_LOCAL_PORT", "5432")),
            "dbname": os.getenv("POSTGRES_LOCAL_DB", "criqenlocal"),
            "user": os.getenv("POSTGRES_LOCAL_USER", "criqenlocal"),
            "password": os.getenv("POSTGRES_LOCAL_PASSWORD", "0707337206")
        },
        "redis": {
            "host": os.getenv("REDIS_HOST", "127.0.0.1"),
            "port": int(os.getenv("REDIS_PORT", "6379")),
            "db": 0,
            "password": os.getenv("REDIS_PASSWORD", ""),
            "enabled": os.getenv("REDIS_ENABLED", "true").lower() == "true"
        },
        "sync": {
            "poll_interval_seconds": int(os.getenv("SYNC_POLL_INTERVAL", "1")),
            "upload_batch_size": int(os.getenv("SYNC_UPLOAD_BATCH_SIZE", "50")),
            "download_interval_seconds": int(os.getenv("SYNC_DOWNLOAD_INTERVAL", "2")),  # ⚡ Was 30 — instant polling
            "max_upload_retries": int(os.getenv("SYNC_MAX_RETRIES", "3")),
            "retry_backoff_base": float(os.getenv("SYNC_RETRY_BACKOFF", "2.0")),
            "max_retry_backoff": 300.0,
            "heartbeat_interval": int(os.getenv("SYNC_HEARTBEAT_INTERVAL", "60")),
            "certificate_sync_interval": int(os.getenv("SYNC_CERTIFICATE_INTERVAL", "30")),
            "conflict_resolution": os.getenv("SYNC_CONFLICT_RESOLUTION", "last_write_wins"),
            "state_dir": os.getenv("SYNC_STATE_DIR", "~/.cmms"),
            "wait_for_hq": os.getenv("WAIT_FOR_HQ", "true").lower() == "true",
            "max_wait_for_hq": int(os.getenv("MAX_WAIT_FOR_HQ", "300"))
        },
        "tables": [table.strip() for table in os.getenv("TABLES", "").split(",") if table.strip()],
        "client": {
            "name": os.getenv("CLIENT_NAME", "Workshop")
        },
        "mirror": {
            "enabled": os.getenv("MIRROR_ENABLED", "false").lower() == "true",
            "interval_hours": float(os.getenv("MIRROR_INTERVAL_HOURS", "24"))
        },
        "hq_db": {
            "host": os.getenv("POSTGRES_HQ_HOST", "dpg-d7rk2sa8qa3s73diimb0-a.ohio-postgres.render.com"),
            "port": int(os.getenv("POSTGRES_HQ_PORT", "5432")),
            "dbname": os.getenv("POSTGRES_HQ_DB", "b12technologies"),
            "user": os.getenv("POSTGRES_HQ_USER", "b12technologies"),
            "password": os.getenv("POSTGRES_HQ_PASSWORD", "")
        }
    }


def load_agent_config(data_path: Path = None, force_env: bool = False):
    """
    Smart configuration loader that automatically chooses the best method

    Args:
        data_path: Path to application data directory
        force_env: Force using .env instead of config.py

    Returns:
        Configuration dictionary for sync agent
    """
    if force_env or not CONFIG_MANAGER_AVAILABLE:
        return load_config_from_env_fallback()
    else:
        return load_config_from_unified_manager(data_path)


# Set default config - will be loaded properly in SyncAgent.__init__
DEFAULT_CONFIG = None

# ============================================================
# 📊 PERFORMANCE MONITORING
# ============================================================

class PerformanceMonitor:
    """Monitor sync performance and display stats"""

    def __init__(self):
        self.upload_times = []
        self.upload_counts = []
        self.last_stats_display = now_kenyan()

    def record_upload(self, duration: float, count: int):
        """Record upload performance"""
        self.upload_times.append(duration)
        self.upload_counts.append(count)

        # Keep only last 100 uploads
        if len(self.upload_times) > 100:
            self.upload_times = self.upload_times[-100:]
            self.upload_counts = self.upload_counts[-100:]

    def get_stats(self) -> Dict:
        """Get performance statistics"""
        if not self.upload_times:
            return {}

        return {
            "avg_upload_time": sum(self.upload_times) / len(self.upload_times),
            "total_uploads": len(self.upload_times),
            "total_records": sum(self.upload_counts),
            "avg_records_per_upload": sum(self.upload_counts) / len(self.upload_counts)
        }

    def should_display_stats(self) -> bool:
        """Check if it's time to display stats (every 5 minutes)"""
        now = now_kenyan()
        if (now - self.last_stats_display).total_seconds() > 300:
            self.last_stats_display = now
            return True
        return False


DEFAULT_CONFIG = None

# ---------- Utility ----------

def sleep_with_jitter(seconds: float):
    """Sleep with small random jitter to prevent thundering herd"""
    jitter = 0.1 * (uuid.uuid4().int % 10)
    time.sleep(seconds + jitter)

def is_online(url="https://hq-server-dgs6.onrender.com/api/sync/health", timeout=2) -> bool:
    """Check if HQ server is reachable"""
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code == 200 and r.json().get("status") in ["UP", "ok"]
    except Exception:
        return False

# ---------- File-Based State Manager ----------



class SmartDeleteMixin:

    def detect_local_deletes_with_dependencies(self, table: str, since_ts: str) -> List[Dict]:
        """
        Detect deleted records and determine if they have dependencies.
        Returns list of delete events with smart delete metadata.
        """
        conn = None
        try:
            conn = self.pool.getconn()

            # Get records marked for deletion (pending_delete = TRUE)
            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            quoted_table = f'"{tbl}"'
            full_table = f"{schema}.{quoted_table}"

            # Check if table has status columns
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    AND table_name = %s
                    AND column_name IN ('pending_delete', 'active_status')
                """, (schema, tbl))

                status_columns = {row[0] for row in cur.fetchall()}

            if 'pending_delete' not in status_columns:
                return []  # Table doesn't support soft delete

            # Find records marked for deletion
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(f"""
                    SELECT
                        id,
                        to_jsonb(t.*) as row_data,
                        updated_at,
                        pending_delete,
                        active_status
                    FROM {full_table} t
                    WHERE pending_delete = TRUE
                    AND updated_at > %s
                    ORDER BY updated_at ASC
                    LIMIT 100
                """, (since_ts,))

                deleted_records = cur.fetchall()

                delete_events = []

                for record in deleted_records:
                    row_id = str(record['id'])

                    # Check if this record has dependencies
                    has_deps = self.check_local_dependencies(conn, table, row_id)

                    delete_event = {
                        "event_id": f"delete-{table}-{row_id}-{uuid.uuid4().hex[:8]}",
                        "table": table,
                        "row_id": row_id,
                        "operation": "d",
                        "data": {
                            "id": row_id,
                            "_has_dependencies": has_deps,
                            "_local_soft_delete": True,
                            # Don't include full record data for deletes
                        },
                        "created_at": record['updated_at'].isoformat(),
                        "source": "local",
                        "machine_id": self.machine_id,
                        "metadata": {
                            "has_dependencies": has_deps,
                            "local_soft_delete": True
                        }
                    }

                    delete_events.append(delete_event)

                    LOG.info(f"Detected local delete: {table}[{row_id}] (has_deps={has_deps})")

                return delete_events

        except Exception as e:
            LOG.error(f"Error detecting deletes for {table}: {e}")
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def check_local_dependencies(self, conn, table: str, row_id: str) -> bool:
        """
        ENHANCED: Comprehensive check if a record has ANY foreign key dependencies.
        Returns True if dependencies exist (checks ALL tables, not just tracked ones).

        This prevents hard delete of records that have dependent data,
        avoiding orphaned records and data integrity issues.
        """
        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Get ALL tables that have foreign keys pointing to this table
                # Removed LIMIT to check all dependencies
                cur.execute("""
                    SELECT
                        tc.table_schema AS child_schema,
                        tc.table_name AS child_table,
                        kcu.column_name AS child_fk_column
                    FROM
                        information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage AS ccu
                        ON ccu.constraint_name = tc.constraint_name
                    WHERE
                        tc.constraint_type = 'FOREIGN KEY'
                        AND ccu.table_schema = %s
                        AND ccu.table_name = %s
                """, (schema, tbl))

                child_tables = cur.fetchall()

                if not child_tables:
                    LOG.debug(f"   No FK relationships found for {table}")
                    return False

                LOG.debug(f"   Checking {len(child_tables)} potential child tables for dependencies...")

                # Check each child table for actual records
                for child in child_tables:
                    child_schema = child['child_schema']
                    child_table = child['child_table']
                    fk_column = child['child_fk_column']

                    child_table_full = f"{child_schema}.{child_table}"

                    try:
                        # Count ANY records that reference this parent
                        # This checks ALL records, not just active ones
                        cur.execute(f"""
                            SELECT COUNT(*) as count
                            FROM "{child_schema}"."{child_table}"
                            WHERE "{fk_column}" = %s
                        """, (row_id,))

                        result = cur.fetchone()

                        if result and result['count'] > 0:
                            LOG.info(f"   ✓ Found {result['count']} dependencies in {child_table_full}")
                            # Return immediately on first dependency found
                            return True
                        else:
                            LOG.debug(f"   ○ No dependencies in {child_table_full}")

                    except psycopg2.Error as e:
                        # If we can't check a table, log it but continue
                        LOG.debug(f"   ⚠ Could not check {child_table_full}: {str(e)[:100]}")
                        continue
                    except Exception as e:
                        LOG.debug(f"   ⚠ Unexpected error checking {child_table_full}: {str(e)[:100]}")

                # If we checked all tables and found no dependencies
                LOG.debug(f"   ✓ No dependencies found for {table}[{row_id}]")
                return False

        except psycopg2.Error as e:
            LOG.warning(f"Database error checking dependencies for {table}[{row_id}]: {e}")
            # On database error, assume dependencies exist (safer approach)
            return True
        except Exception as e:
            LOG.warning(f"Unexpected error checking dependencies for {table}[{row_id}]: {e}")
            # On any error, assume dependencies exist (safer approach)
            return True

    def apply_smart_delete_from_hq(self, table: str, payload: Dict) -> bool:
        """
        Apply smart delete from HQ to local database.
        Handles both soft_delete and hard_delete operations.
        """
        operation_type = payload.get("operation")
        row_id = payload.get("row_id")

        LOG.info(f"Applying {operation_type} from HQ: {table}[{row_id}]")

        conn = None
        try:
            conn = self.pool.getconn()

            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            quoted_table = f'"{tbl}"'
            full_table = f"{schema}.{quoted_table}"

            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                if operation_type == "soft_delete":
                    # Apply soft delete locally
                    cur.execute(f"""
                        UPDATE {full_table}
                        SET pending_delete = TRUE,
                            active_status = FALSE,
                            updated_at = NOW()
                        WHERE id = %s
                    """, (row_id,))

                    LOG.info(f"   ✓ Applied soft delete locally")

                elif operation_type == "hard_delete":
                    # Apply hard delete locally
                    cur.execute(f"""
                        DELETE FROM {full_table}
                        WHERE id = %s
                    """, (row_id,))

                    LOG.info(f"   🗑️ Applied hard delete locally")

                conn.commit()
                return True

        except psycopg2.errors.ForeignKeyViolation as e:
            if conn:
                conn.rollback()
            LOG.warning(f"FK violation applying delete: {e}")
            LOG.info(f"   Attempting to apply as soft delete instead...")

            # Try soft delete as fallback
            try:
                with conn.cursor() as cur:
                    cur.execute(f"""
                        UPDATE {full_table}
                        SET pending_delete = TRUE,
                            active_status = FALSE,
                            updated_at = NOW()
                        WHERE id = %s
                    """, (row_id,))
                    conn.commit()
                    LOG.info(f"   ✓ Applied as soft delete instead")
                    return True
            except Exception as e2:
                LOG.error(f"   ✗ Soft delete fallback also failed: {e2}")
                if conn:
                    conn.rollback()
                return False

        except Exception as e:
            LOG.error(f"Error applying delete from HQ: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                self.pool.putconn(conn)



    def detect_local_restores(self, table: str, since_ts: str) -> List[Dict]:
        """
        ✅ FIXED: Detect records that were restored (activated) locally.
        Now properly clears soft delete tracking to allow re-deletion.
        """
        conn = None
        try:
            conn = self.pool.getconn()

            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            quoted_table = f'"{tbl}"'
            full_table = f"{schema}.{quoted_table}"

            # Check if table has status columns
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    AND table_name = %s
                    AND column_name IN ('pending_delete', 'active_status')
                """, (schema, tbl))

                status_columns = {row[0] for row in cur.fetchall()}

            if not status_columns:
                LOG.debug(f"   Table {table} doesn't support restore operations")
                return []

            # Find restore operations from audit log
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        event_id,
                        row_id,
                        data,
                        received_at
                    FROM audit_log
                    WHERE table_name = %s
                    AND operation = 'r'
                    AND received_at > %s
                    ORDER BY received_at ASC
                    LIMIT 100
                """, (table, since_ts))

                audit_restores = cur.fetchall()

                if not audit_restores:
                    return []

                LOG.info(f"📋 Found {len(audit_restores)} restore operations for {table}")

                restore_events = []
                restored_ids = []

                for audit_record in audit_restores:
                    row_id = str(audit_record['row_id'])
                    event_id = audit_record['event_id']

                    # Verify the record exists and is active
                    cur.execute(f"""
                        SELECT
                            id,
                            to_jsonb(t.*) as row_data,
                            updated_at,
                            active_status,
                            pending_delete
                        FROM {full_table} t
                        WHERE id = %s
                    """, (row_id,))

                    current_record = cur.fetchone()

                    if not current_record:
                        LOG.warning(f"   ⚠️ Record {row_id} not found (may have been deleted)")
                        continue

                    # Verify it's actually active
                    is_active = (
                        current_record.get('active_status') == True or
                        current_record.get('pending_delete') == False
                    )

                    if not is_active:
                        LOG.warning(f"   ⚠️ Record {row_id} is not active (skipping)")
                        continue

                    # Create restore event
                    restore_event = {
                        "event_id": event_id or f"restore-{table}-{row_id}-{uuid.uuid4().hex[:8]}",
                        "table": table,
                        "row_id": row_id,
                        "operation": "activate",
                        "data": current_record["row_data"],
                        "created_at": current_record['updated_at'].isoformat(),
                        "source": "local",
                        "machine_id": self.machine_id,
                        "active_status": True,
                        "pending_delete": False,
                        "metadata": {
                            "restore_operation": True,
                            "restored_from_audit": True
                        }
                    }

                    restore_events.append(restore_event)
                    restored_ids.append(row_id)
                    LOG.info(f"✅ Detected local restore: {table}[{row_id}]")

                # 🔥 CRITICAL FIX: Clear soft delete tracking for restored records
                if restored_ids:
                    LOG.info(f"🧹 Clearing soft delete tracking for {len(restored_ids)} restored records...")
                    self._clear_soft_delete_tracking_for_records(table, restored_ids)

                if restore_events:
                    LOG.info(f"🎉 Prepared {len(restore_events)} restore events for sync")

                return restore_events

        except Exception as e:
            LOG.error(f"Error detecting restores for {table}: {e}")
            LOG.exception(e)
            return []
        finally:
            if conn:
                self.pool.putconn(conn)


    def _clear_soft_delete_tracking_for_records(self, table: str, row_ids: List[str]):
        """
        🔥 NEW: Clear soft delete tracking for specific records.
        This allows them to be detected as deleted again after restore.
        """
        state_key = f"synced_soft_deletes_{table}"
        synced_soft_deletes_str = self.state.get(state_key, "")
        synced_soft_deletes = set(synced_soft_deletes_str.split(",")) if synced_soft_deletes_str else set()

        cleared_count = 0
        for row_id in row_ids:
            if row_id in synced_soft_deletes:
                synced_soft_deletes.discard(row_id)
                cleared_count += 1
                LOG.debug(f"   🧹 Cleared tracking for {row_id}")

        # Save updated tracking
        self.state.set(state_key, ",".join(synced_soft_deletes))

        if cleared_count > 0:
            LOG.info(f"   ✅ Cleared {cleared_count} records from soft delete tracking")
            LOG.info(f"   📝 These records can now be deleted again")


    def clear_soft_delete_tracking(self, table: str, row_id: str = None):
        """
        🔧 ENHANCED: Clear soft delete tracking for emergency cleanup.
        """
        state_key = f"synced_soft_deletes_{table}"

        if row_id:
            # Clear specific record
            synced_soft_deletes_str = self.state.get(state_key, "")
            synced_soft_deletes = set(synced_soft_deletes_str.split(",")) if synced_soft_deletes_str else set()

            if row_id in synced_soft_deletes:
                synced_soft_deletes.discard(row_id)
                self.state.set(state_key, ",".join(synced_soft_deletes))
                LOG.info(f"🧹 Cleared soft delete tracking for {table}[{row_id}]")
            else:
                LOG.info(f"ℹ️  {table}[{row_id}] was not in tracking")
        else:
            # Clear entire table tracking
            self.state.set(state_key, "")
            LOG.info(f"🧹 Cleared ALL soft delete tracking for {table}")


    def mark_record_for_deletion(self, table: str, row_id: str) -> bool:
        """
        Mark a record for soft deletion locally before syncing to HQ.
        This ensures proper soft delete flow: local soft delete → HQ evaluation → proper action
        """
        conn = None
        try:
            conn = self.pool.getconn()

            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            quoted_table = f'"{tbl}"'
            full_table = f"{schema}.{quoted_table}"

            # Get schema info
            schema_info = self.get_table_schema_info(table)

            with conn.cursor() as cur:
                # Mark as pending_delete instead of actually deleting
                if schema_info['has_pending_delete']:
                    cur.execute(f"""
                        UPDATE {full_table}
                        SET pending_delete = TRUE,
                            active_status = FALSE,
                            updated_at = NOW()
                        WHERE id = %s
                    """, (row_id,))

                    LOG.info(f"✅ Marked {table}[{row_id}] for deletion (soft delete)")

                    conn.commit()
                    return True
                else:
                    LOG.warning(f"⚠️ Table {table} doesn't support soft delete")

        except Exception as e:
            LOG.error(f"Failed to mark {table}[{row_id}] for deletion: {e}")
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                self.pool.putconn(conn)

    def discover_recent_changes_with_smart_delete(self) -> List[Dict]:
        """
        Enhanced version of discover_recent_changes that detects smart deletes.
        Replace your existing discover_recent_changes with this.
        """
        last_upload_time = self.get_last_upload_time()
        events = []

        LOG.debug("Looking for changes since: %s", last_upload_time)

        for table in self.tables:
            # Get regular updates
            table_events = self.fetch_recent_changes_for_table(table, last_upload_time)
            events.extend(table_events)

            # Get smart delete events
            delete_events = self.detect_local_deletes_with_dependencies(table, last_upload_time)
            events.extend(delete_events)

        if events:
            delete_count = sum(1 for e in events if e.get("operation") == "d")
            update_count = len(events) - delete_count

            LOG.info(f"Discovered {len(events)} changes: {update_count} updates, {delete_count} deletes")

        return events

    def apply_remote_update_with_smart_delete(self, table: str, payload: Dict) -> bool:
        """
        Enhanced version that handles smart delete operations from HQ.
        Replace apply_remote_update_locally call with this.
        """
        operation = payload.get("operation", "u")

        # Handle smart delete operations
        if operation in ["soft_delete", "hard_delete"]:
            return self.apply_smart_delete_from_hq(table, payload)

        # Handle regular operations
        return self.apply_remote_update_locally(table, payload)


class ClientSmartDelete:
    """
    Helper class for sync agent to request smart deletes from HQ
    """

    def __init__(self, agent):
        self.agent = agent
        self.api_url = agent.api_url
        self.headers = agent._http_headers()

    def request_smart_delete(self, table: str, row_id: str, force_hard: bool = False) -> Dict:
        """
        Request HQ to perform smart delete on a record.
        HQ will decide between soft and hard delete based on dependencies.
        """
        try:
            payload = {
                "table": table,
                "row_id": row_id,
                "client_id": self.agent.client_id,
                "force_hard_delete": force_hard
            }

            LOG.info(f"Requesting smart delete from HQ: {table}[{row_id}]")

            response = requests.post(
                f"{self.api_url}/smart_delete",
                json=payload,
                headers=self.headers,
                timeout=30
            )

            if response.status_code == 200:
                result = response.json()
                LOG.info(f"✓ HQ processed delete: {result.get('operation')}")
                LOG.info(f"   Message: {result.get('message')}")
                return result
            else:
                LOG.error(f"✗ Smart delete request failed: {response.status_code}")
                return {"status": "error", "message": response.text}

        except Exception as e:
            LOG.error(f"Error requesting smart delete: {e}")
            return {"status": "error", "message": str(e)}

    def check_dependencies_before_delete(self, table: str, row_id: str) -> Dict:
        """
        Check dependencies before attempting delete.
        Helps UI show warning to users.
        """
        try:
            payload = {
                "table": table,
                "row_id": row_id
            }

            response = requests.post(
                f"{self.api_url}/check_dependencies",
                json=payload,
                headers=self.headers,
                timeout=10
            )

            if response.status_code == 200:
                result = response.json()

                if result.get("has_dependencies"):
                    LOG.info(f"⚠️  {table}[{row_id}] has {result.get('total_dependencies')} dependencies")

                    for dep in result.get("dependencies", []):
                        LOG.info(f"      ↳ {dep['child_table']}: {dep['count']} records")

                return result
            else:
                return {"has_dependencies": False, "error": response.text}

        except Exception as e:
            LOG.error(f"Error checking dependencies: {e}")
            return {"has_dependencies": False, "error": str(e)}





class StateManager:
    """
    Manages persistent state with atomic writes and corruption protection.
    Triple redundancy: Redis (optional) → File (durable) → Safe default
    """

    def __init__(self, state_dir: str, redis_client=None):
        self.state_dir = Path(os.path.expanduser(state_dir))
        self.state_dir.mkdir(parents=True, exist_ok=True)

        self.state_file = self.state_dir / "sync_state.json"
        self.state_file_backup = self.state_dir / "sync_state.json.bak"
        self.client_id_file = self.state_dir / "client_id"

        self.redis = redis_client
        self.use_redis = redis_client is not None

        # Load initial state from file
        self._state_cache = self._load_state_from_file()

        LOG.info("State manager initialized")
        LOG.info("  State file: %s", self.state_file)
        LOG.info("  Redis: %s", "Enabled" if self.use_redis else "Disabled (file-only mode)")

    def _load_state_from_file(self) -> Dict[str, Any]:
        """Load state from file with backup fallback"""
        try:
            with open(self.state_file, 'r') as f:
                state = json.load(f)
                LOG.debug("Loaded state from primary file")
                return state
        except FileNotFoundError:
            LOG.debug("No state file found, starting fresh")
            return {}
        except json.JSONDecodeError as e:
            LOG.warning("State file corrupted: %s, trying backup", e)
        except Exception as e:
            LOG.warning("Failed to load state file: %s, trying backup", e)

        try:
            with open(self.state_file_backup, 'r') as f:
                state = json.load(f)
                LOG.info("✅ Recovered state from backup file")
                self._save_state_to_file(state)
                return state
        except Exception as e:
            LOG.debug("No backup file available: %s", e)

        return {}

    def _save_state_to_file(self, state: Dict[str, Any]):
        """Atomically save state to file with backup"""
        try:
            temp_file = self.state_file.with_suffix('.tmp')
            with open(temp_file, 'w') as f:
                json.dump(state, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())

            if self.state_file.exists():
                try:
                    self.state_file.replace(self.state_file_backup)
                except Exception as e:
                    LOG.debug("Could not create backup: %s", e)

            temp_file.replace(self.state_file)
            LOG.debug("State saved to file")

        except Exception as e:
            LOG.error("Failed to save state file: %s", e)

    def get(self, key: str, default: Any = None) -> Any:
        """Get value with triple redundancy: Redis → Cache → File → Default"""
        if self.use_redis:
            try:
                value = self.redis.get(f"cmms:{key}")
                if value is not None:
                    return value
            except Exception as e:
                LOG.debug("Redis read failed for %s: %s", key, e)

        if key in self._state_cache:
            return self._state_cache[key]

        self._state_cache = self._load_state_from_file()
        if key in self._state_cache:
            return self._state_cache[key]

        return default

    def set(self, key: str, value: Any):
        """Set value with dual write: Redis + File"""
        self._state_cache[key] = value

        if self.use_redis:
            try:
                self.redis.set(f"cmms:{key}", value)
            except Exception as e:
                LOG.debug("Redis write failed for %s: %s", key, e)

        self._save_state_to_file(self._state_cache)

    def get_client_id(self) -> Optional[str]:
        """Get client ID from Redis or file"""
        if self.use_redis:
            try:
                client_id = self.redis.get("cmms:client_id")
                if client_id:
                    return client_id
            except Exception:
                pass

        if "client_id" in self._state_cache:
            return self._state_cache["client_id"]

        try:
            with open(self.client_id_file, 'r') as f:
                return f.read().strip()
        except FileNotFoundError:
            return None

    def set_client_id(self, client_id: str):
        """Store client ID with triple redundancy"""
        self._state_cache["client_id"] = client_id

        if self.use_redis:
            try:
                self.redis.set("cmms:client_id", client_id)
            except Exception:
                pass

        self._save_state_to_file(self._state_cache)

        try:
            with open(self.client_id_file, 'w') as f:
                f.write(client_id)
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            LOG.warning("Failed to write client_id file: %s", e)

# ---------- Dependency Discovery & Sorting ----------

class DependencyManager:
    """Manages table dependencies for proper sync ordering"""

    def __init__(self, pool):
        self.pool = pool
        self._dependencies_cache = None
        self._fk_relationships_cache = None
        self._sorted_tables_cache = None

    def get_foreign_keys_from_db(self):
        """Fetch all foreign key relationships from database schema"""
        if self._fk_relationships_cache is not None:
            return self._fk_relationships_cache

        query = """
            SELECT
                tc.table_schema,
                tc.table_name,
                kcu.column_name,
                ccu.table_schema AS foreign_table_schema,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name
            FROM
                information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                  AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
            ORDER BY tc.table_schema, tc.table_name;
        """

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                self._fk_relationships_cache = cur.fetchall()
                return self._fk_relationships_cache
        except Exception as e:
            LOG.error("Failed to fetch FK relationships: %s", e)
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def build_dependency_graph(self, tables):
        """Build dependency graph: child_table -> [parent_tables]"""
        fk_rows = self.get_foreign_keys_from_db()
        dependencies = defaultdict(set)
        fk_details = defaultdict(list)

        for row in fk_rows:
            schema = row['table_schema']
            child = row['table_name']
            fschema = row['foreign_table_schema']
            parent = row['foreign_table_name']
            child_col = row['column_name']
            parent_col = row['foreign_column_name']

            child_full = f"{schema}.{child}"
            parent_full = f"{fschema}.{parent}"

            if child_full in tables:
                dependencies[child_full].add(parent_full)
                fk_details[child_full].append({
                    'parent_table': parent_full,
                    'child_column': child_col,
                    'parent_column': parent_col
                })

        self._dependencies_cache = {k: list(v) for k, v in dependencies.items()}
        return self._dependencies_cache, fk_details

    def discover_table_dependencies_nx(self):
        """Auto-discover dependencies and sort using NetworkX"""
        if not NETWORKX_AVAILABLE:
            LOG.warning("NetworkX not available, using simple ordering")
            return []

        if self._sorted_tables_cache is not None:
            return self._sorted_tables_cache

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        tc.table_schema AS child_schema,
                        tc.table_name AS child_table,
                        ccu.table_schema AS parent_schema,
                        ccu.table_name AS parent_table
                    FROM
                        information_schema.table_constraints AS tc
                    JOIN information_schema.key_column_usage AS kcu
                        ON tc.constraint_name = kcu.constraint_name
                        AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage AS ccu
                        ON ccu.constraint_name = tc.constraint_name
                    WHERE
                        tc.constraint_type = 'FOREIGN KEY'
                        AND tc.table_schema = 'public'
                    ORDER BY
                        parent_table, child_table;
                """)

                rows = cur.fetchall()

                G = nx.DiGraph()
                for child_schema, child_table, parent_schema, parent_table in [
                    (r[0], r[1], r[2], r[3]) for r in rows
                ]:
                    parent = f"{parent_schema}.{parent_table}"
                    child = f"{child_schema}.{child_table}"
                    G.add_edge(parent, child)

                try:
                    sorted_tables = list(nx.topological_sort(G))
                    LOG.info("📊 Discovered %d table dependencies", len(sorted_tables))
                    self._sorted_tables_cache = sorted_tables
                    return sorted_tables
                except nx.NetworkXError:
                    LOG.warning("⚠️ Cyclic dependencies detected; using node order")
                    self._sorted_tables_cache = list(G.nodes())
                    return self._sorted_tables_cache

        except Exception as e:
            LOG.error("Failed to discover table dependencies: %s", e)
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def sort_updates_by_dependency(self, updates):
        """Sort updates based on table dependencies (parents first)"""
        sorted_tables = self.discover_table_dependencies_nx()

        if not sorted_tables:
            return updates

        table_priority = {table: idx for idx, table in enumerate(sorted_tables)}

        def sort_key(update):
            table = update.get("table")
            priority = table_priority.get(table, 999999)
            return (priority, update.get("row_id", ""))

        return sorted(updates, key=sort_key)

# ---------- Enhanced Sync Agent Class ----------

class SyncAgent(SmartDeleteMixin):
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
            LOG.info("✅ Auth token loaded (%d chars) — X-API-Key will be sent with every HQ request",
                     len(self.auth_token))
        else:
            LOG.warning("⚠️  No auth_token in config — HQ will reject all API calls. "
                        "Check sync.auth_token in config.json")

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
            _log_level = getattr(logging,
                                 os.getenv("SYNC_LOG_LEVEL", "DEBUG").upper(),
                                 logging.DEBUG)
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

        # ============================================================
        # INITIALIZE REDIS QUEUE
        # ============================================================
        self.redis_queue = self._init_redis_queue()
        if not self.redis_queue:
            LOG.warning("=" * 80)
            LOG.warning("⚠️  REDIS QUEUE NOT AVAILABLE")
            LOG.warning("=" * 80)
            LOG.warning("   Falling back to LEGACY POLLING MODE")
            LOG.warning("   Limitations:")
            LOG.warning("   • Possible race conditions")
            LOG.warning("   • No crash recovery")
            LOG.warning("   • No idempotency guarantees")
            LOG.warning("=" * 80)

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
                    dynamic_port = (
                        session.get("ports", {}).get("redis")
                        or session.get("redis_port")
                    )
                    if dynamic_port and int(dynamic_port) != port:
                        LOG.info(
                            "[Redis] Dynamic port detected: using %d "
                            "(config.json says %d, session.json says %d)",
                            int(dynamic_port), port, int(dynamic_port)
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
            password=redis_cfg.get("password") or None
        )
        r.ping()
        LOG.info("[Redis] Connected on %s:%d", host, port)
        return r


    def _init_redis_queue(self):
        """Initialize Redis queue sync (if available)"""
        if not hasattr(self, 'redis') or self.redis is None:
            return None

        if not REDIS_QUEUE_AVAILABLE:
            return None

        # ── Auth token sanity check ──────────────────────────────────────────
        # If auth_token is missing the queue will be initialised but every
        # upload request will be rejected by HQ with 403/401.  Log clearly
        # so the problem is visible without diving into request traces.
        if not self.auth_token:
            LOG.warning(
                "⚠️  RedisQueueSync: auth_token is MISSING — "
                "all upload requests will be rejected by HQ (X-API-Key header "
                "will not be sent). Check sync.auth_token in config.json."
            )
        else:
            LOG.info(
                "[RedisQueue] Auth token loaded (%d chars) — "
                "X-API-Key header will be sent with every request.",
                len(self.auth_token)
            )
        # ────────────────────────────────────────────────────────────────────

        try:
            queue = RedisQueueSync(
                redis_client=self.redis,
                api_url=self.api_url,
                client_id=self.client_id,
                auth_token=self.auth_token,
                logger=LOG,
                batch_size=int(self.sync_cfg.get("upload_batch_size", 50)),
                max_retry_attempts=5
            )
            LOG.info("=" * 80)
            LOG.info("✅ REDIS QUEUE SYNC INITIALIZED")
            LOG.info("=" * 80)
            LOG.info("   Mode: Queue-based (one at a time)")
            LOG.info("   Features:")
            LOG.info("   • No race conditions (mutex lock)")
            LOG.info("   • Crash recovery (inflight tracking)")
            LOG.info("   • Idempotency (sync_id)")
            LOG.info("   • Exponential backoff retry")
            LOG.info("   • Dead letter queue")
            LOG.info("=" * 80)
            return queue
        except Exception as e:
            LOG.error("❌ Redis queue init failed: %s", e)
            return None

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

                session_ids = [str(s['id']) for s in local_missing]

                response = requests.post(
                    f"{self.api_url}/pull_certificates",
                    json={
                        "client_id": self.client_id,
                        "session_ids": session_ids
                    },
                    headers=self._http_headers(),
                    timeout=30
                )

                if response.status_code != 200:
                    LOG.error(f"   ❌ Pull failed: {response.status_code}")
                    return 0

                data = response.json()
                hq_sessions = data.get('sessions', [])

                LOG.info(f"   📦 HQ returned {len(hq_sessions)} sessions with certificates")

                if not hq_sessions:
                    LOG.info("   ℹ️  HQ has no certificates for these sessions yet")
                    return 0

                # Apply updates locally
                updated_count = 0
                schedule_updates = []

                for hq_session in hq_sessions:
                    session_id = hq_session['id']
                    cert_number = hq_session['certificate_number']
                    schedule_id = hq_session.get('schedule_id')

                    LOG.info(f"   📝 Updating session {session_id} with certificate {cert_number}")

                    cur.execute("""
                        UPDATE "CalSoft_calibrationsession"
                        SET certificate_number = %s,
                            status = 'approved',
                            updated_at = NOW()
                        WHERE id = %s
                    """, (cert_number, session_id))

                    if cur.rowcount > 0:
                        updated_count += 1
                        LOG.info(f"      ✅ Updated locally")

                        # Also update schedule if needed
                        if schedule_id and hq_session.get('schedule_status') == 'completed':
                            schedule_updates.append((schedule_id, hq_session.get('completed_date')))

                # Update schedules
                for schedule_id, completed_date in schedule_updates:
                    cur.execute("""
                        UPDATE "calSchedules_calibrationschedule"
                        SET status = 'completed',
                            completed_date = %s,
                            updated_at = NOW()
                        WHERE id = %s
                        AND status != 'completed'
                    """, (completed_date, schedule_id))

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
                    cur.execute("""
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_schema = %s
                        AND table_name = %s
                        AND is_nullable = 'NO'
                        AND column_default IS NULL
                        AND column_name NOT IN ('id', 'created_at', 'updated_at')
                        AND column_name NOT LIKE '%_at'
                    """, (schema, tbl))

                    columns = [row['column_name'] for row in cur.fetchall()]
                    LOG.debug(f"Required columns for {table}: {columns}")
                    return columns

        except Exception as e:
            LOG.warning(f"Failed to get required columns for {table}: {e}")
            return []


    def validate_update_data(self, table: str, row_id: str, data: dict, operation: str) -> Tuple[bool, str]:
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
        if operation in ['d']:
            return True, ""

        # Check if we have any data
        if not data or data == {}:
            return False, f"No data received from HQ for operation '{operation}'"

        # Ensure row_id is in the data (critical for upsert)
        if 'id' not in data or data['id'] is None:
            LOG.warning(f"⚠️  Adding missing id field for {table} id={row_id}")
            data['id'] = row_id

        # For deactivate/activate operations, we might not need all fields
        if operation in ['deactivate', 'activate']:
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
        ✅ NEW: Background thread that actively pulls missing certificates

        Runs every 15 minutes to ensure certificates sync down from HQ
        """
        import time

        pull_interval = int(os.getenv("CERT_PULL_INTERVAL", "60"))

        LOG.info(f"📥 Certificate pull loop started (interval: {pull_interval}s / {pull_interval/60:.1f}m)")

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

            # Create HQ config
            hq_config = {
                "host": os.getenv("POSTGRES_HQ_HOST", "dpg-d7rk2sa8qa3s73diimb0-a.ohio-postgres.render.com"),
                "port": int(os.getenv("POSTGRES_HQ_PORT", "5432")),
                "dbname": os.getenv("POSTGRES_HQ_DB", "b12technologies"),
                "user": os.getenv("POSTGRES_HQ_USER", "b12technologies"),
                "password": os.getenv("POSTGRES_HQ_PASSWORD", "")
            }

            # Use existing local config
            local_config = self.config["local_db"]

            self.mirror = DatabaseMirror(
                hq_config=hq_config,
                local_config=local_config,
                api_url=self.api_url,
                auth_token=self.auth_token,
                client_id=self.client_id,
                machine_id=self.machine_id
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

                        quoted_table = f'"{tbl}"'
                        full_table = f"{schema}.{quoted_table}"

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

            full_table = f"{schema}.\"{tbl}\""
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

    def perform_mirror_sync_now(self, direction: str = "bidirectional") -> Dict:
        """Perform immediate mirror sync"""
        if not self.mirror:
            return {"success": False, "error": "Mirror not initialized"}

        try:
            LOG.info("🔄 Manual mirror sync triggered...")

            direction_map = {
                "bidirectional": SyncDirection.BIDIRECTIONAL,
                "hq_to_local": SyncDirection.HQ_TO_LOCAL,
                "local_to_hq": SyncDirection.LOCAL_TO_HQ
            }

            sync_direction = direction_map.get(direction, SyncDirection.BIDIRECTIONAL)

            if not self.mirror.connect():
                return {"success": False, "error": "Connection failed"}

            try:
                stats = self.mirror.mirror_all_tables(
                    tables=self.tables,
                    direction=sync_direction,
                    conflict_strategy="last_write_wins"
                )

                total_synced_to_hq = sum(s.synced_to_hq for s in stats)
                total_synced_to_local = sum(s.synced_to_local for s in stats)
                total_conflicts = sum(s.conflicts for s in stats)
                total_errors = sum(s.errors for s in stats)
                total_changes = total_synced_to_hq + total_synced_to_local

                if total_changes > 0:
                    LOG.info(f"📡 Broadcasting {total_changes} changes...")
                    self.mirror.broadcast_changes_to_agents(stats)

                return {
                    "success": True,
                    "tables_processed": len(stats),
                    "synced_to_hq": total_synced_to_hq,
                    "synced_to_local": total_synced_to_local,
                    "conflicts_resolved": total_conflicts,
                    "errors": total_errors,
                    "changes_broadcasted": total_changes > 0
                }
            finally:
                self.mirror.disconnect()

        except Exception as e:
            LOG.error(f"❌ Mirror sync failed: {e}")
            return {"success": False, "error": str(e)}

    def mirror_sync_loop(self):
        """Background thread for periodic mirror sync"""
        interval_seconds = int(self.mirror_interval_hours * 3600)

        LOG.info("🔄 Mirror sync loop started (interval: %.1fh)", self.mirror_interval_hours)

        sync_count = 0

        while not self.stop_event.is_set():
            try:
                sync_count += 1

                LOG.info("")
                LOG.info("🔄 Scheduled Mirror Sync #%d", sync_count)

                result = self.perform_mirror_sync_now(direction="bidirectional")

                if result["success"]:
                    total_changes = result.get("synced_to_hq", 0) + result.get("synced_to_local", 0)

                    if total_changes > 0:
                        LOG.info(f"✅ {total_changes} changes synced")
                    else:
                        LOG.info(f"✅ All databases consistent")
                else:
                    LOG.error(f"❌ Mirror sync failed: {result.get('error')}")

                next_sync = datetime.now() + timedelta(seconds=interval_seconds)
                LOG.info(f"⏰ Next sync: {next_sync.strftime('%Y-%m-%d %H:%M:%S')}")

            except Exception as e:
                LOG.error(f"❌ Mirror loop error: {e}")

            for _ in range(interval_seconds):
                if self.stop_event.is_set():
                    break
                time.sleep(1)

        LOG.info("🔄 Mirror sync loop exiting")

    def get_mirror_status(self) -> Dict:
        """Get mirror system status"""
        if not self.mirror:
            return {"enabled": False, "status": "not_initialized"}

        return {
            "enabled": self.mirror_enabled,
            "status": "active",
            "interval_hours": self.mirror_interval_hours,
            "tables_monitored": len(self.tables),
            "client_id": self.client_id
        }

    def get_table_schema_info(self, table):
            """Get schema information about table columns"""
            if table in self._table_schema_cache:
                return self._table_schema_cache[table]

            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            conn = None
            try:
                conn = self.pool.getconn()
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    # Fetch ALL columns so we can filter out HQ-only columns
                    # that haven't been migrated to this client yet.
                    cur.execute("""
                        SELECT column_name, data_type, udt_name, character_maximum_length
                        FROM information_schema.columns
                        WHERE table_schema = %s
                        AND table_name = %s
                    """, (schema, tbl))

                    columns = cur.fetchall()
                    all_col_names = {c['column_name'] for c in columns}

                    LOG.debug(f"Schema check for {table}: Found {len(columns)} columns")

                    schema_info = {
                        'has_pending_delete': 'pending_delete' in all_col_names,
                        'has_active_status': 'active_status' in all_col_names,
                        'columns': all_col_names,  # full set for upsert filtering
                    }

                    self._table_schema_cache[table] = schema_info

                    LOG.debug(f"Schema info for {table}: {schema_info}")
                    return schema_info
            except Exception as e:
                LOG.warning(f"Could not fetch schema info for {table}: {e}")
                LOG.exception(e)
                return {'has_pending_delete': False, 'has_active_status': False, 'columns': set()}
            finally:
                if conn:
                    self.pool.putconn(conn)

    def get_json_columns(self, table):
        """Get list of JSON/JSONB columns for a table"""
        if table in self._json_columns_cache:
            return self._json_columns_cache[table]

        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    AND table_name = %s
                    AND data_type IN ('json', 'jsonb')
                """, (schema, tbl))

                json_cols = [row[0] for row in cur.fetchall()]
                self._json_columns_cache[table] = json_cols
                return json_cols
        except Exception as e:
            LOG.warning(f"Could not fetch JSON columns for {table}: {e}")
            return []
        finally:
            if conn:
                self.pool.putconn(conn)

    def verify_tables_have_updated_at(self):
        """Verify that all configured tables have updated_at columns"""
        conn = None
        try:
            conn = self.pool.getconn()
            with conn.cursor() as cur:
                valid_tables = []
                for table in self.tables:
                    if '.' in table:
                        schema, tbl = table.split('.', 1)
                    else:
                        schema, tbl = 'public', table

                    cur.execute("""
                        SELECT EXISTS (
                            SELECT FROM information_schema.columns
                            WHERE table_schema = %s
                            AND table_name = %s
                            AND column_name = 'updated_at'
                        )
                    """, (schema, tbl))

                    if cur.fetchone()[0]:
                        valid_tables.append(table)
                        LOG.debug("✓ Table has updated_at: %s", table)
                    else:
                        LOG.warning("✗ Table missing updated_at, skipping: %s", table)

                self.tables = valid_tables
                LOG.info("Verified %d/%d tables have updated_at", len(valid_tables), len(self.config["tables"]))

        except Exception as e:
            LOG.error("Failed to verify tables: %s", e)
        finally:
            if conn:
                self.pool.putconn(conn)

    def get_machine_identifier(self):
        """Get unique machine identifier based on MAC address"""
        env_machine_id = os.getenv("MACHINE_ID")
        if env_machine_id:
            return env_machine_id

        try:
            mac_int = uuid.getnode()
            mac_hex = ":".join(f"{(mac_int >> ele) & 0xff:02x}" for ele in range(40, -1, -8))
            mac_hash = hashlib.sha1(mac_hex.encode()).hexdigest()[:12]
            return f"mac-{mac_hash}"
        except Exception:
            try:
                hostname = os.uname().nodename
                return f"host-{hostname}"
            except:
                return f"machine-{str(uuid.uuid4())[:8]}"

    def _create_db_pool(self, db_cfg: Dict[str, Any]) -> ThreadedConnectionPool:
        """Create PostgreSQL connection pool"""
        try:
            pool = ThreadedConnectionPool(
                minconn=1,
                maxconn=5,
                host=db_cfg["host"],
                port=int(db_cfg.get("port", 5432)),
                database=db_cfg["dbname"],
                user=db_cfg["user"],
                password=db_cfg["password"]
            )
            LOG.info("✅ PostgreSQL connection pool created")
            return pool
        except Exception as e:
            LOG.error("Failed to create DB connection pool: %s", e)
            raise

    def auto_register_client(self) -> str:
        """Auto-register or retrieve client ID"""
        if DEVICE_ID_MODULE_AVAILABLE:
            # Try calling with state_manager parameter first
            try:
                client_id = get_or_create_client_id(state_manager=self.state)
            except TypeError:
                # Fallback: function doesn't accept state_manager parameter
                try:
                    client_id = get_or_create_client_id()
                except TypeError:
                    # Function might need no arguments at all
                    client_id = get_or_create_client_id
                    if callable(client_id):
                        client_id = client_id()
        else:
            # Generate fallback client ID
            client_id = self.state.get_client_id()
            if not client_id:
                import uuid
                import hashlib
                mac = uuid.getnode()
                mac_hash = hashlib.sha1(str(mac).encode()).hexdigest()[:12]
                client_id = f"mac-{mac_hash}"
                self.state.set_client_id(client_id)

        LOG.info(f"Client ID: {client_id}")
        return client_id

    def get_last_download_time(self) -> str:
        """
        Get last successful download time.

        If no checkpoint exists (fresh install or reinstall that wiped state),
        we set a flag so start() knows to run a full mirror sync instead of
        relying on the audit-log-based download, which only knows about changes
        since the audit log started — it cannot reconstruct a full DB from scratch.
        """
        last_time = self.state.get("last_download_time")
        if last_time:
            return last_time

        # No checkpoint = state was wiped (reinstall) or this is a new machine.
        # Signal that a full mirror is needed by setting the flag — start() reads
        # this before launching threads.
        LOG.warning("⚠️  No download checkpoint found — state was wiped or this is a new install.")
        LOG.warning("   Will use epoch timestamp for audit-log download, but a mirror sync")
        LOG.warning("   is strongly recommended to recover records predating the audit log.")
        self._needs_mirror_sync = True

        # ── FIX: Also wipe the upload checkpoint so the upload loop re-scans
        # ALL local records and pushes them to HQ.  Without this, a reinstall
        # that still has stale data keeps a recent last_upload_time and the
        # upload loop skips every record that predates it — HQ never receives
        # the existing local data.
        existing_upload_ts = self.state.get("last_upload_time")
        if existing_upload_ts:
            LOG.warning(
                "⚠️  Wiping stale upload checkpoint (%s) so all local records "
                "are re-uploaded to HQ after reinstall.",
                existing_upload_ts,
            )
            self.state.set("last_upload_time", None)

        # Use epoch so the audit-log download at least fetches everything audited
        return "1970-01-01T00:00:00+00:00"


    def detect_local_restores(self, table: str, since_ts: str) -> List[Dict]:
        """
        ✅ FIXED: Detect records that were restored (activated) locally.
        Now properly clears soft delete tracking to allow re-deletion.

        This function:
        1. Finds restore operations from audit_log
        2. Verifies records are actually active now
        3. Clears soft delete tracking for restored records
        4. Returns restore events for sync to HQ
        """
        conn = None
        try:
            conn = self.pool.getconn()

            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            quoted_table = f'"{tbl}"'
            full_table = f"{schema}.{quoted_table}"

            # Check if table has status columns
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    AND table_name = %s
                    AND column_name IN ('pending_delete', 'active_status')
                """, (schema, tbl))

                status_columns = {row[0] for row in cur.fetchall()}

            if not status_columns:
                LOG.debug(f"   Table {table} doesn't support restore operations (no status columns)")
                return []

            # Find restore operations from audit log
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        event_id,
                        row_id,

                        received_at
                    FROM audit_log
                    WHERE table_name = %s
                    AND operation = 'r'
                    AND received_at > %s
                    ORDER BY received_at ASC
                    LIMIT 100
                """, (table, since_ts))

                audit_restores = cur.fetchall()

                if not audit_restores:
                    LOG.debug(f"   No restore operations found in audit_log for {table}")
                    return []

                LOG.info(f"📋 Found {len(audit_restores)} restore operations in audit_log for {table}")

                restore_events = []
                restored_ids = []  # Track IDs to clear from soft delete tracking

                for audit_record in audit_restores:
                    row_id = str(audit_record['row_id'])
                    event_id = audit_record['event_id']
                    audit_data = audit_record.get('data', {})

                    # Verify the record still exists and is active
                    cur.execute(f"""
                        SELECT
                            id,
                            to_jsonb(t.*) as row_data,
                            updated_at,
                            active_status,
                            pending_delete
                        FROM {full_table} t
                        WHERE id = %s
                    """, (row_id,))

                    current_record = cur.fetchone()

                    if not current_record:
                        LOG.warning(f"   ⚠️ Record {table}[{row_id}] not found (may have been deleted)")
                        continue

                    # Verify it's actually active now
                    is_active = (
                        current_record.get('active_status') == True or
                        current_record.get('pending_delete') == False
                    )

                    if not is_active:
                        LOG.warning(f"   ⚠️ Record {table}[{row_id}] is not active (skipping)")
                        continue

                    # Create restore event for sync
                    restore_event = {
                        "event_id": event_id or f"restore-{table}-{row_id}-{uuid.uuid4().hex[:8]}",
                        "table": table,
                        "row_id": row_id,
                        "operation": "activate",
                        "data": current_record["row_data"],
                        "created_at": current_record['updated_at'].isoformat(),
                        "source": "local",
                        "machine_id": self.machine_id,
                        "active_status": True,
                        "pending_delete": False,
                        "metadata": {
                            "restore_operation": True,
                            "restored_from_audit": True,
                            "audit_data": audit_data
                        }
                    }

                    restore_events.append(restore_event)
                    restored_ids.append(row_id)  # Track for cleanup
                    LOG.info(f"✅ Detected local restore: {table}[{row_id}]")

                if restore_events:
                    LOG.info(f"🎉 Prepared {len(restore_events)} restore events for sync")

                    # 🔥 CRITICAL FIX: Clear soft delete tracking for restored records
                    if restored_ids:
                        state_key = f"synced_soft_deletes_{table}"
                        synced_soft_deletes_str = self.state.get(state_key, "")
                        synced_soft_deletes = set(synced_soft_deletes_str.split(",")) if synced_soft_deletes_str else set()

                        cleared_count = 0
                        for restored_id in restored_ids:
                            if restored_id in synced_soft_deletes:
                                synced_soft_deletes.discard(restored_id)
                                cleared_count += 1
                                LOG.debug(f"   🧹 Cleared soft delete tracking for {restored_id}")

                        # Save updated tracking
                        if cleared_count > 0:
                            self.state.set(state_key, ",".join(synced_soft_deletes))
                            LOG.info(f"   ✨ Cleared {cleared_count} records from soft delete tracking")
                            LOG.info(f"   📝 These records can now be deleted again if needed")

                return restore_events

        except Exception as e:
            LOG.error(f"Error detecting restores for {table}: {e}")
            LOG.exception(e)
            return []
        finally:
            if conn:
                self.pool.putconn(conn)


    def fetch_recent_changes_for_table(self, table: str, since_ts: str, limit: int = 500):
        """
        ⚡ ENHANCED: Optimized change detection with batching, caching, and better error handling

        Key improvements:
        1. ✅ Proper detection of all change types (updates, soft deletes, re-deletes)
        2. ⚡ Batch processing for large result sets
        3. 🎯 Smart caching of schema info and state
        4. 📊 Enhanced metrics and logging
        5. 🔒 Better transaction handling
        6. 🚀 Index hints for faster queries
        """
        conn = None
        start_time = time.time()

        try:
            conn = self.pool.getconn()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Parse table name
                if '.' in table:
                    schema, tbl = table.split('.', 1)
                else:
                    schema, tbl = 'public', table

                quoted_table = f'"{tbl}"'
                full_table_name = f"{schema}.{quoted_table}"

                # ⚡ Parse and validate timestamp
                since_dt = self._parse_timestamp(since_ts)

                # 🎯 Cache schema info (avoid repeated lookups)
                cache_key = f"schema_info_{table}"
                schema_info = getattr(self, '_schema_cache', {}).get(cache_key)

                if not schema_info:
                    schema_info = self.get_table_schema_info(table)
                    if not hasattr(self, '_schema_cache'):
                        self._schema_cache = {}
                    self._schema_cache[cache_key] = schema_info

                has_pending_delete = schema_info.get('has_pending_delete', False)
                has_active_status = schema_info.get('has_active_status', False)
                has_deleted_at = schema_info.get('has_deleted_at', False)

                # ⚡ OPTIMIZED QUERY with index hints
                query = f"""
                    SELECT
                        id,
                        to_jsonb(t.*) as row_data,
                        updated_at,
                        created_at
                        {', pending_delete' if has_pending_delete else ''}
                        {', active_status' if has_active_status else ''}
                        {', deleted_at' if has_deleted_at else ''}
                    FROM {full_table_name} t
                    WHERE updated_at > %s::timestamptz
                    ORDER BY updated_at ASC
                    LIMIT %s
                """

                # Execute with explain for optimization insights (debug mode)
                if LOG.isEnabledFor(logging.DEBUG):
                    cur.execute(f"EXPLAIN ANALYZE {query}", (since_dt, limit))
                    explain_result = cur.fetchall()
                    LOG.debug(f"📊 Query plan for {table}:\n{explain_result[0] if explain_result else 'N/A'}")

                cur.execute(query, (since_dt, limit))
                rows = cur.fetchall()

                query_time = time.time() - start_time


                if not rows:
                    return []

                # 🎯 Load soft delete tracking state once
                state_key = f"synced_soft_deletes_{table}"
                synced_soft_deletes = self._load_soft_delete_state(state_key)

                LOG.info(f"📋 Processing {len(rows)} changed records in {table}")
                LOG.debug(f"   📊 Tracking {len(synced_soft_deletes)} soft deletes")

                # 📊 Initialize metrics
                metrics = {
                    'active_updates': 0,
                    'soft_deletes': 0,
                    'hard_deletes': 0,
                    'skipped_synced': 0,
                    'restored_then_deleted': 0,
                    'deactivations': 0
                }

                changes = []
                new_soft_deletes = []
                batch_size = 100  # Process in batches for better memory management

                # ⚡ Process records in batches
                for batch_start in range(0, len(rows), batch_size):
                    batch = rows[batch_start:batch_start + batch_size]
                    batch_changes = self._process_record_batch(
                        batch, table, conn,
                        has_pending_delete, has_active_status, has_deleted_at,
                        synced_soft_deletes, new_soft_deletes, metrics
                    )
                    changes.extend(batch_changes)

                # 💾 Persist updated soft delete tracking
                if new_soft_deletes:
                    self._save_soft_delete_state(state_key, synced_soft_deletes, new_soft_deletes)

                # 📊 Log comprehensive summary
                self._log_change_summary(table, metrics, query_time)

                return changes

        except Exception as e:
            LOG.error(f"❌ Error fetching changes for {table}: {e}")
            LOG.exception(e)
            return []
        finally:
            if conn:
                self.pool.putconn(conn)


    def _parse_timestamp(self, since_ts: str) -> datetime:
        """Parse timestamp with fallback handling"""
        if isinstance(since_ts, datetime):
            return since_ts

        try:
            return datetime.fromisoformat(since_ts.replace('Z', '+00:00'))
        except Exception as e:
            LOG.warning(f"⚠️ Invalid timestamp: {since_ts}, using 1 hour ago")
            return datetime.now(timezone.utc) - timedelta(hours=1)


    def _load_soft_delete_state(self, state_key: str) -> set:
        """Load and parse soft delete tracking state"""
        state_str = self.state.get(state_key, "")
        if not state_str:
            return set()

        try:
            return set(state_str.split(","))
        except Exception as e:
            LOG.warning(f"⚠️ Failed to parse soft delete state: {e}")
            return set()


    def _save_soft_delete_state(self, state_key: str, current_set: set, new_deletes: list):
        """Save soft delete tracking with size management"""
        current_set.update(new_deletes)

        # Keep reasonable size (max 10,000 IDs)
        if len(current_set) > 10000:
            current_set = set(list(current_set)[-10000:])

        self.state.set(state_key, ",".join(current_set))
        LOG.debug(f"   💾 Saved {len(new_deletes)} new soft deletes ({len(current_set)} total)")


    def _process_record_batch(self, batch: list, table: str, conn,
                            has_pending_delete: bool, has_active_status: bool,
                            has_deleted_at: bool, synced_soft_deletes: set,
                            new_soft_deletes: list, metrics: dict) -> list:
        """Process a batch of records efficiently"""
        changes = []

        for row in batch:
            event_timestamp = row.get("updated_at") or row.get("created_at") or datetime.now(timezone.utc)
            row_id = str(row["id"])

            # 🔍 Determine current delete status
            is_soft_deleted = self._is_record_soft_deleted(
                row, has_pending_delete, has_active_status, has_deleted_at
            )

            was_tracked = row_id in synced_soft_deletes

            # ⚡ Skip already-synced soft deletes (optimization)
            if is_soft_deleted and was_tracked:
                metrics['skipped_synced'] += 1
                LOG.debug(f"   ⏭️ Skip already-synced: {row_id}")
                continue

            # 🔄 Detect re-delete scenario
            if is_soft_deleted and not was_tracked and synced_soft_deletes:
                metrics['restored_then_deleted'] += 1
                LOG.info(f"   🔄 Re-delete detected: {row_id}")

            # 🎯 Process based on status
            if is_soft_deleted:
                change_event = self._create_soft_delete_event(
                    row, row_id, table, event_timestamp, conn, metrics
                )
                new_soft_deletes.append(row_id)
            else:
                change_event = self._create_update_event(
                    row, row_id, table, event_timestamp,
                    has_pending_delete, has_active_status, metrics
                )

            changes.append(change_event)

        return changes


    def _is_record_soft_deleted(self, row: dict, has_pending_delete: bool,
                                has_active_status: bool, has_deleted_at: bool) -> bool:
        """Check if record is soft deleted"""
        # Check pending_delete flag
        if has_pending_delete:
            val = row.get('pending_delete')
            if val in (True, 'Y', '1', 't', 'true', 1):
                return True

        # Check active_status flag
        if has_active_status:
            val = row.get('active_status')
            if val in (False, 'N', '0', 'f', 'false', 0):
                return True

        # Check deleted_at timestamp
        if has_deleted_at:
            val = row.get('deleted_at')
            if val is not None:
                return True

        return False


    def _create_soft_delete_event(self, row: dict, row_id: str, table: str,
                                timestamp: datetime, conn, metrics: dict) -> dict:
        """Create event for soft deleted record"""
        has_deps = self.check_local_dependencies(conn, table, row_id)

        if has_deps:
            metrics['deactivations'] += 1
            LOG.info(f"   🟡 Deactivate (has deps): {row_id}")

            return {
                "event_id": str(uuid.uuid4()),
                "table": table,
                "row_id": row_id,
                "operation": "deactivate",
                "data": row["row_data"],
                "created_at": timestamp.isoformat(),
                "source": "local",
                "machine_id": self.machine_id,
                "active_status": False,
                "pending_delete": True,
                "has_dependencies": True
            }
        else:
            metrics['hard_deletes'] += 1
            LOG.info(f"   🔴 Hard delete (no deps): {row_id}")

            return {
                "event_id": str(uuid.uuid4()),
                "table": table,
                "row_id": row_id,
                "operation": "d",
                "data": {"id": row_id},
                "created_at": timestamp.isoformat(),
                "source": "local",
                "machine_id": self.machine_id,
                "has_dependencies": False
            }


    def _create_update_event(self, row: dict, row_id: str, table: str,
                            timestamp: datetime, has_pending_delete: bool,
                            has_active_status: bool, metrics: dict) -> dict:
        """Create event for active record update"""
        metrics['active_updates'] += 1
        LOG.debug(f"   ✅ Active update: {row_id}")

        event = {
            "event_id": str(uuid.uuid4()),
            "table": table,
            "row_id": row_id,
            "operation": "u",
            "data": row["row_data"],
            "created_at": timestamp.isoformat(),
            "source": "local",
            "machine_id": self.machine_id
        }

        # Include status fields
        if has_active_status:
            event["active_status"] = row.get("active_status")
        if has_pending_delete:
            event["pending_delete"] = row.get("pending_delete")

        return event


    def _log_change_summary(self, table: str, metrics: dict, query_time: float):
        """Log comprehensive change summary"""
        total_changes = (metrics['active_updates'] + metrics['soft_deletes'] +
                        metrics['hard_deletes'])

        if total_changes > 0:
            LOG.info(f"✅ {table} summary ({query_time:.2f}s):")
            LOG.info(f"   • {metrics['active_updates']} active updates")
            LOG.info(f"   • {metrics['deactivations']} deactivations (with deps)")
            LOG.info(f"   • {metrics['hard_deletes']} hard deletes (no deps)")

            if metrics['restored_then_deleted'] > 0:
                LOG.info(f"   • {metrics['restored_then_deleted']} re-deletes ✨")
            if metrics['skipped_synced'] > 0:
                LOG.debug(f"   • {metrics['skipped_synced']} skipped (already synced)")
        else:
            LOG.debug(f"ℹ️ {table}: No changes detected")


    def upload_batch(self, events: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
        """
        ✅ ENHANCED: Upload with better logging to show what's being uploaded
        """
        if not events:
            LOG.debug("📭 No events to upload")
            return True, None

        # Categorize events
        delete_events = [ev for ev in events if ev.get("operation") == "d"]
        status_events = [ev for ev in events if ev.get("operation") in ["activate", "deactivate"]]
        update_events = [ev for ev in events if ev.get("operation") == "u"]

        LOG.info(f"📤 Uploading batch of {len(events)} events:")
        if update_events:
            LOG.info(f"   • {len(update_events)} regular updates")
        if delete_events:
            LOG.info(f"   • {len(delete_events)} hard deletes")
        if status_events:
            LOG.info(f"   • {len(status_events)} status changes")

        url = f"{self.api_url}/upload"
        data = {
            "events": events,
            "client_id": self.client_id,
            "machine_id": self.machine_id
        }
        headers = self._http_headers()

        try:
            r = requests.post(url, json=data, headers=headers, timeout=30)

            if r.status_code == 200:
                LOG.info("✅ Batch uploaded successfully")
                resp_json = r.json()

                # ✅ CRITICAL: Update last_upload_time to the LATEST event timestamp
                if events:
                    latest_event_time = max(
                        event.get("created_at", event.get("updated_at", ""))
                        for event in events
                    )
                    if latest_event_time:
                        self.set_last_upload_time(latest_event_time)
                        LOG.info(f"   📅 Updated checkpoint to: {latest_event_time}")

                deferred_count = resp_json.get("deferred", 0)
                if deferred_count > 0:
                    LOG.warning(f"   ⏸️ Server deferred {deferred_count} events (missing parent records)")

                return True, None
            else:
                error_msg = f"Upload failed: {r.status_code} - {r.text}"
                LOG.warning(error_msg)
                return False, error_msg

        except requests.RequestException as e:
            error_msg = f"Upload request failed: {str(e)}"
            LOG.warning(error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"Unexpected upload error: {str(e)}"
            LOG.exception(error_msg)
            return False, error_msg

    def discover_recent_changes(self) -> List[Dict[str, Any]]:
        """
        ✅ ENHANCED: Discover all types of changes with better logging

        Scans for:
        1. Regular updates/inserts
        2. Soft deletes (with dependency checks)
        3. Restores (from audit_log)
        """
        last_upload_time = self.get_last_upload_time()

        pass

        all_events = []
        table_summary = {}

        for table in self.tables:
            LOG.debug(f"📋 Checking {table}...")

            # Get regular updates/inserts (includes soft deletes)
            table_events = self.fetch_recent_changes_for_table(table, last_upload_time)

            # Get restore events (from audit log)
            restore_events = self.detect_local_restores(table, last_upload_time)

            total_for_table = len(table_events) + len(restore_events)

            if total_for_table > 0:
                # Categorize events for this table
                updates = sum(1 for e in table_events if e.get("operation") == "u")
                deletes = sum(1 for e in table_events if e.get("operation") == "d")
                deactivates = sum(1 for e in table_events if e.get("operation") == "deactivate")

                table_summary[table] = {
                    'updates': updates,
                    'deletes': deletes,
                    'deactivates': deactivates,
                    'restores': len(restore_events),
                    'total': total_for_table
                }

                summary_parts = []
                if updates > 0:
                    summary_parts.append(f"{updates} updates")
                if deletes > 0:
                    summary_parts.append(f"{deletes} deletes")
                if deactivates > 0:
                    summary_parts.append(f"{deactivates} deactivates")
                if len(restore_events) > 0:
                    summary_parts.append(f"{len(restore_events)} restores")

                LOG.info(f"   ✅ {table}: {', '.join(summary_parts)}")

            all_events.extend(table_events)
            all_events.extend(restore_events)

        # Overall summary

        if all_events:
            delete_count = sum(1 for e in all_events if e.get("operation") == "d")
            restore_count = sum(1 for e in all_events if e.get("operation") == "activate")
            deactivate_count = sum(1 for e in all_events if e.get("operation") == "deactivate")
            update_count = len(all_events) - delete_count - restore_count - deactivate_count

            LOG.warning("=" * 80)
            LOG.warning(f"📊 CHANGES DETECTED: {len(all_events)} total")
            LOG.warning(f"   • Updates: {update_count}")
            if delete_count > 0:
                LOG.warning(f"   • Deletes: {delete_count}")
            if deactivate_count > 0:
                LOG.warning(f"   • Deactivates: {deactivate_count}")
            if restore_count > 0:
                LOG.warning(f"   • Restores: {restore_count}")
            LOG.warning("=" * 80)

        return all_events

    def get_last_upload_time(self) -> str:
        """
        Get last upload checkpoint.

        If no checkpoint exists, use epoch so the upload loop scans ALL local
        records and pushes them to HQ — not just the last hour.
        """
        last_time = self.state.get("last_upload_time")

        if last_time:
            LOG.debug(f"📅 Last upload time from state: {last_time}")
            return last_time

        LOG.warning("⚠️  No upload checkpoint found — scanning ALL local records (epoch baseline)")
        return "1970-01-01T00:00:00+00:00"


    def set_last_upload_time(self, timestamp: str = None):
        """
        ✅ ENHANCED: Update last upload time with verification
        """
        ts = timestamp or now_iso()
        self.state.set("last_upload_time", ts)
        LOG.info(f"✅ Updated last_upload_time: {ts}")

        # Verify it was saved
        saved_ts = self.state.get("last_upload_time")
        if saved_ts != ts:
            LOG.error(f"❌ Failed to save last_upload_time! Got: {saved_ts}")
        else:
            LOG.debug(f"   ✅ Verified: timestamp saved correctly")

    def set_last_download_time(self, timestamp: str = None):
        """Update last download timestamp with dual persistence"""
        ts = timestamp or now_iso()
        self.state.set("last_download_time", ts)
        LOG.debug("Updated last_download_time: %s", ts)

    def _http_headers(self) -> Dict[str, str]:
        """Generate HTTP headers for API requests"""
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"CMMS-Sync-Agent/4.1.0 (Client-ID: {self.client_id})"
        }
        if self.auth_token:
            # ✅ FIXED: Use X-API-Key header (server expects this)
            headers["X-API-Key"] = self.auth_token
        return headers




    def get_column_type_client(self, conn, table: str, column: str) -> str:
        _COLUMN_TYPE_CACHE = {}

        if not column:
            return "unknown"

        if "." in table:
            schema, tbl = table.split(".", 1)
        else:
            schema, tbl = "public", table

        # Use cache if available
        cache_key = f"{schema}.{tbl}.{column}"
        if cache_key in _COLUMN_TYPE_CACHE:
            return _COLUMN_TYPE_CACHE[cache_key]

        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        data_type,
                        udt_name,
                        character_maximum_length,
                        column_default
                    FROM information_schema.columns
                    WHERE table_schema = %s
                    AND table_name = %s
                    AND column_name = %s
                    LIMIT 1
                """, (schema, tbl.strip('"'), column))

                result = cur.fetchone()
                if not result:
                    LOG.warning(f"⚠️ Could not find column type for {schema}.{tbl}.{column}")
                    _COLUMN_TYPE_CACHE[cache_key] = "unknown"
                    return "unknown"

                data_type = result["data_type"]
                udt_name = result["udt_name"]
                max_length = result["character_maximum_length"]
                column_default = result["column_default"]

                # --- Type detection logic ---
                # Native boolean
                if data_type == "boolean" or udt_name == "bool":
                    detected = "boolean"

                # CHAR(1), VARCHAR(1), or bpchar(1)
                elif (data_type in ("character", "character varying", "text") or udt_name == "bpchar") and (max_length == 1):
                    detected = "char1"

                # ENUM-like (common in PostgreSQL when udt_name != builtins)
                elif udt_name not in ("bpchar", "varchar", "text", "bool", "int4", "int8"):
                    detected = "enum"

                # Generic text-based flag
                elif data_type in ("character varying", "text"):
                    detected = "text"

                # Fallback
                else:
                    detected = "unknown"

                _COLUMN_TYPE_CACHE[cache_key] = detected

                LOG.debug(
                    f"🔍 Column type detected: {schema}.{tbl}.{column} → {detected} "
                    f"({data_type}, udt={udt_name}, max_len={max_length}, default={column_default})"
                )

                return detected

        except Exception as e:
            LOG.error(f"❌ Error getting column type for {table}.{column}: {e}")
            _COLUMN_TYPE_CACHE[cache_key] = "unknown"
            return "unknown"


    def convert_boolean_for_column_client(self, value, column_type):

        """
        Enhanced: Convert boolean-like value to the correct format for a given column type.

        Supports:
        ✅ boolean → True/False
        ✅ char1   → 'Y'/'N' or '1'/'0'
        ✅ text    → 'active'/'inactive'
        ✅ enum    → 'active'/'inactive' (default fallback)
        ✅ unknown → returns bool(value)
        """

        # Normalize incoming value first
        if isinstance(value, str):
            val_lower = value.strip().lower()
            if val_lower in ("1", "true", "t", "yes", "y", "active"):
                value = True
            elif val_lower in ("0", "false", "f", "no", "n", "inactive"):
                value = False
            else:
                # unknown string – treat non-empty as True
                value = bool(value)
        elif isinstance(value, (int, float)):
            value = bool(value)

        # Now handle conversion by target column type
        if column_type == "boolean":
            return bool(value)

        elif column_type == "char1":
            # Use 'Y'/'N' for better readability (vs. '1'/'0')
            return "Y" if value else "N"

        elif column_type in ("text", "varchar", "character varying"):
            return "active" if value else "inactive"

        elif column_type == "enum":
            # Many PostgreSQL enums for status use active/inactive or enabled/disabled
            return "active" if value else "inactive"

        elif column_type == "unknown":
            LOG.warning(f"⚠️ Unknown column type, defaulting to boolean: {value}")
            return bool(value)

        else:
            # Fallback for any type not explicitly handled
            LOG.debug(f"ℹ️ Unhandled type {column_type}, defaulting to bool: {value}")
            return bool(value)



    # ============================================================
    # ENHANCED STATUS CHANGE HANDLING (ACTIVATE/DEACTIVATE)
    # ============================================================


    def apply_status_change_locally(self, table: str, payload: Dict[str, Any]) -> bool:
        """
        FIXED: Apply status change (activate/deactivate) to local database.
        Now prevents duplicate column assignments.
        """
        operation = payload.get("operation")
        row_id = payload.get("row_id")
        data = payload.get("data", {})
        active_status = payload.get("active_status")
        pending_delete = payload.get("pending_delete")
        source = payload.get("source", "unknown")

        # CRITICAL FIX: Determine correct values based on operation
        if operation == "deactivate":
            active_status = False
            pending_delete = True
        elif operation == "activate":
            active_status = True
            pending_delete = False
        else:
            # Fallback to payload values
            if active_status is None:
                active_status = True
            if pending_delete is None:
                pending_delete = False

        LOG.info(f"📋 Status change operation: {operation} for {table}[{row_id}]")
        LOG.debug(f"   Target state: active_status={active_status}, pending_delete={pending_delete}")
        LOG.debug(f"   Source: {source}")

        conn = None
        try:
            conn = self.pool.getconn()

            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            quoted_table = f'"{tbl}"'
            full_table = f"{schema}.{quoted_table}"

            # === Check current local state ===
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(f"""
                    SELECT id, active_status, pending_delete, updated_at
                    FROM {full_table}
                    WHERE id = %s
                """, (row_id,))

                local_record = cur.fetchone()

            # === CRITICAL FIX: Skip if already in correct state ===
            if local_record:
                current_active = local_record.get('active_status')
                current_pending = local_record.get('pending_delete')

                # Check if record is already in the target state
                if operation == "activate":
                    already_correct = (current_active is True and current_pending is False)
                elif operation == "deactivate":
                    already_correct = (current_active is False and current_pending is True)
                else:
                    already_correct = False

                if already_correct:
                    LOG.info(f"   ✅ Record already in correct state - skipping update")
                    LOG.debug(f"      Current: active={current_active}, pending={current_pending}")
                    return True

                LOG.debug(f"   Current state: active={current_active}, pending={current_pending}")
                LOG.debug(f"   Will update to: active={active_status}, pending={pending_delete}")

            # === Get schema info ===
            schema_info = self.get_table_schema_info(table)

            # === SYSTEM FIELDS TO EXCLUDE from data loop ===
            # These are handled separately to avoid duplicates
            SYSTEM_FIELDS = {
                'id',           # Primary key - never update
                'created_at',   # Creation timestamp - never update
                'updated_at',   # Update timestamp - always set to NOW()
                'active_status',   # Status field - handled separately
                'pending_delete'   # Delete flag - handled separately
            }

            # === HANDLE ACTIVATE OPERATION ===
            if operation == "activate":
                LOG.info("=" * 80)
                LOG.info(f"✨ ACTIVATE OPERATION from HQ")
                LOG.info(f"   Equipment ID: {row_id}")
                LOG.info(f"   Source: {source}")

                if not local_record:
                    LOG.info(f"   Record does NOT exist locally - will create")

                    # INSERT new record
                    from datetime import datetime, timezone as dt_timezone

                    insert_data = {}

                    # Add user data fields (excluding system fields)
                    for key, value in data.items():
                        if key not in SYSTEM_FIELDS:
                            insert_data[key] = value

                    # Set system fields
                    insert_data['id'] = row_id
                    insert_data['active_status'] = True
                    insert_data['pending_delete'] = False

                    # Set timestamps
                    now = datetime.now(dt_timezone.utc)
                    if 'created_at' not in data:
                        insert_data['created_at'] = now
                    else:
                        insert_data['created_at'] = data['created_at']
                    insert_data['updated_at'] = now

                    cols = list(insert_data.keys())
                    vals = [insert_data[c] for c in cols]

                    col_list = ", ".join([f'"{c}"' for c in cols])
                    placeholders = ", ".join(["%s"] * len(cols))

                    with conn.cursor() as cur:
                        insert_sql = f"""
                            INSERT INTO {full_table} ({col_list})
                            VALUES ({placeholders})
                            ON CONFLICT (id) DO UPDATE SET
                            active_status = EXCLUDED.active_status,
                            pending_delete = EXCLUDED.pending_delete,
                            updated_at = EXCLUDED.updated_at
                        """

                        cur.execute(insert_sql, vals)
                        LOG.info(f"   ✅ Created new record: {table}[{row_id}]")
                        conn.commit()
                        return True
                else:
                    # UPDATE existing record
                    with conn.cursor(cursor_factory=RealDictCursor) as cur:
                        update_parts = []
                        update_values = []

                        # 1. Set status fields first
                        if schema_info.get('has_pending_delete'):
                            pd_type = self.get_column_type_client(conn, table, 'pending_delete')
                            pd_value = self.convert_boolean_for_column_client(False, pd_type)
                            update_parts.append('pending_delete = %s')
                            update_values.append(pd_value)
                            LOG.debug(f"   Setting pending_delete = {repr(pd_value)}")

                        if schema_info.get('has_active_status'):
                            as_type = self.get_column_type_client(conn, table, 'active_status')
                            as_value = self.convert_boolean_for_column_client(True, as_type)
                            update_parts.append('active_status = %s')
                            update_values.append(as_value)
                            LOG.debug(f"   Setting active_status = {repr(as_value)}")

                        # 2. Update other data fields (excluding system fields)
                        for key, value in data.items():
                            if key not in SYSTEM_FIELDS:
                                update_parts.append(f'"{key}" = %s')
                                update_values.append(value)
                                LOG.debug(f"   Setting {key} from data")

                        # 3. Always set updated_at to NOW() (NEVER from data)
                        update_parts.append('updated_at = NOW()')

                        # 4. Add WHERE clause value
                        update_values.append(row_id)

                        update_sql = f"""
                            UPDATE {full_table}
                            SET {', '.join(update_parts)}
                            WHERE id = %s
                            RETURNING id
                        """

                        LOG.debug(f"   Executing UPDATE with {len(update_parts)} fields")
                        LOG.debug(f"   SQL: {update_sql}")

                        cur.execute(update_sql, update_values)

                        if cur.rowcount > 0:
                            LOG.info(f"   ✅ Activated: {table}[{row_id}]")
                            conn.commit()
                            return True
                        else:
                            LOG.warning(f"   ⚠️ Update affected 0 rows")
                            conn.rollback()
                            return False

            # === HANDLE DEACTIVATE OPERATION ===
            elif operation == "deactivate":
                LOG.info(f"🔴 DEACTIVATE operation for {table}[{row_id}]")

                if not local_record:
                    LOG.warning(f"   ⚠️ Record not found locally")
                    return True

                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    update_parts = []
                    update_values = []

                    if schema_info.get('has_pending_delete'):
                        pd_type = self.get_column_type_client(conn, table, 'pending_delete')
                        pd_value = self.convert_boolean_for_column_client(True, pd_type)
                        update_parts.append("pending_delete = %s")
                        update_values.append(pd_value)

                    if schema_info.get('has_active_status'):
                        as_type = self.get_column_type_client(conn, table, 'active_status')
                        as_value = self.convert_boolean_for_column_client(False, as_type)
                        update_parts.append("active_status = %s")
                        update_values.append(as_value)

                    if not update_parts:
                        LOG.warning(f"   ⚠️ No status columns found")
                        return False

                    # Always update timestamp (NEVER from data)
                    update_parts.append("updated_at = NOW()")
                    update_values.append(row_id)

                    update_sql = f"""
                        UPDATE {full_table}
                        SET {', '.join(update_parts)}
                        WHERE id = %s
                        RETURNING id
                    """

                    cur.execute(update_sql, update_values)

                    if cur.rowcount > 0:
                        LOG.info(f"   ✅ Deactivated {table}[{row_id}]")
                        conn.commit()
                        return True
                    else:
                        LOG.warning(f"   ⚠️ Record not found")
                        return False

            else:
                LOG.warning(f"   ⚠️ Unknown operation: {operation}")
                return False

        except Exception as e:
            LOG.error(f"Failed to apply status change for {table}[{row_id}]: {e}")
            LOG.error(traceback.format_exc())
            if conn:
                conn.rollback()
            return False
        finally:
            if conn:
                self.pool.putconn(conn)

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


    # ============================================================
    # ENHANCED CONNECTION HANDLING
    # ============================================================

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

    def check_hq_online(self):
        """Quick check if HQ is currently online"""
        try:
            response = requests.get(
                f"{self.api_url}/health",
                timeout=3
            )
            return response.status_code == 200
        except:
            return False

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

    def immediate_download_on_reconnect(self):
        """
        Immediately download all updates from HQ when connection is restored.
        This catches up on everything that happened at HQ while we were offline.
        """
        try:
            LOG.info("📥 IMMEDIATE DOWNLOAD: Fetching updates from HQ...")
            self.download_updates()
            LOG.info("✅ Immediate download complete")
            return True
        except Exception as e:
            LOG.error(f"❌ Immediate download failed: {e}")
            return False
    # ============================================================
    # UPLOAD LOGIC (updated_at BASED WITH STATUS SUPPORT)
    # ============================================================



# ============================================================
    # REDIS QUEUE HELPERS
    # ============================================================

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
        """
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

                        # Update checkpoint
                        if all_changes:
                            latest_time = max(
                                e.get("created_at", e.get("updated_at", ""))
                                for e in all_changes
                            )
                            if latest_time:
                                self.set_last_upload_time(latest_time)

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

                # Check each table individually and upload immediately
                last_upload_time = self.get_last_upload_time()

                for table in self.tables:
                    if self.stop_event.is_set():
                        break

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

                                # Advance checkpoint so the next poll skips these rows
                                try:
                                    latest_ts = max(
                                        e.get("created_at") or e.get("updated_at") or ""
                                        for e in batch
                                    )
                                    if latest_ts:
                                        self.set_last_upload_time(latest_ts)
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
    # ============================================================
    # DOWNLOAD LOGIC WITH STATUS AND FK HANDLING
    # ============================================================



    """
    Enhanced download_updates to properly handle cross-workshop transfers

    """

    def download_updates(self):
        """
        ENHANCED: Download updates from HQ with cross-workshop transfer support
        """
        last_ts = self.get_last_download_time()

        url = f"{self.api_url}/download"
        params = {"since": last_ts, "client_id": self.client_id}
        headers = self._http_headers()

        try:

            r = requests.get(url, params=params, headers=headers, timeout=30)

            if r.status_code != 200:
                LOG.warning(f"❌ Download returned status {r.status_code}: {r.text}")
                return

            payload = r.json()
            updates = payload.get("updates", [])

            if not updates:
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

                is_certificate_update = "certificate" in table.lower()
                if is_certificate_update:
                    cert_number = update.get("data", {}).get("certificate_number")
                    if cert_number:
                        LOG.debug(f"   📜 Processing certificate: {cert_number}")

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

            failed_count = len(failed_updates)

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
                    LOG.info(f"   ⚠️  {failed_count} updates failed")

                if latest_ts != last_ts:
                    self.set_last_download_time(latest_ts)
                    LOG.info(f"   🕐 Last download timestamp updated: {latest_ts}")

            else:
                LOG.warning(f"⚠️  No updates could be applied from this batch")

        except requests.RequestException as e:
            LOG.error(f"❌ Download request failed: {e}")
        except Exception as e:
            LOG.exception(f"💥 Unexpected error in download updates: {e}")

    def auto_recover_missing_parents_from_hq(self, table: str, row_id: str, fk_column: str, parent_table: str, parent_id: str) -> bool:
        """
        🆕 AUTO-RECOVERY: Fetch missing parent record from HQ and insert locally

        This solves the core issue where equipment/other parent records are missing locally,
        blocking dependent records like calibration sessions from syncing.

        Args:
            table: Child table that has the FK violation
            row_id: Child record ID
            fk_column: Foreign key column name
            parent_table: Parent table name
            parent_id: Missing parent record ID

        Returns:
            True if parent was recovered, False otherwise
        """
        LOG.info("=" * 80)
        LOG.info("🔧 AUTO-RECOVERY: Fetching missing parent from HQ")
        LOG.info(f"   Child: {table}[{row_id}]")
        LOG.info(f"   Missing Parent: {parent_table}[{parent_id}]")
        LOG.info(f"   FK Column: {fk_column}")
        LOG.info("=" * 80)

        try:
            # Query HQ database directly for the missing parent
            hq_config = {
                "host": os.getenv("POSTGRES_HQ_HOST", "dpg-d7rk2sa8qa3s73diimb0-a.ohio-postgres.render.com"),
                "port": int(os.getenv("POSTGRES_HQ_PORT", "5432")),
                "dbname": os.getenv("POSTGRES_HQ_DB", "b12technologies"),
                "user": os.getenv("POSTGRES_HQ_USER", "b12technologies"),
                "password": os.getenv("POSTGRES_HQ_PASSWORD", "")
            }

            # Connect to HQ database
            import psycopg2
            from psycopg2.extras import RealDictCursor

            hq_conn = psycopg2.connect(**hq_config, cursor_factory=RealDictCursor)

            try:
                if "." in parent_table:
                    schema, tbl = parent_table.split(".", 1)
                else:
                    schema, tbl = "public", parent_table

                quoted_table = f'"{tbl}"'
                full_table = f"{schema}.{quoted_table}"

                # Fetch parent record from HQ
                with hq_conn.cursor() as cur:
                    cur.execute(f"""
                        SELECT to_jsonb(t.*) as data
                        FROM {full_table} t
                        WHERE id = %s
                    """, (parent_id,))

                    result = cur.fetchone()

                    if not result:
                        LOG.warning(f"   ❌ Parent record NOT FOUND in HQ: {parent_table}[{parent_id}]")
                        LOG.warning(f"      This is an orphaned reference!")
                        return False

                    parent_data = result['data']
                    LOG.info(f"   ✅ Found parent in HQ: {parent_table}[{parent_id}]")

            finally:
                hq_conn.close()

            # Now insert the parent record locally
            conn = None
            try:
                conn = self.pool.getconn()

                with conn.cursor() as cur:
                    # Check if parent already exists locally (race condition check)
                    cur.execute(f"""
                        SELECT id FROM {full_table} WHERE id = %s
                    """, (parent_id,))

                    if cur.fetchone():
                        LOG.info(f"   ℹ️  Parent already exists locally (race condition)")
                        return True

                    # Prepare insert
                    cols = list(parent_data.keys())
                    vals = [parent_data[c] for c in cols]

                    col_list = ", ".join([f'"{c}"' for c in cols])
                    placeholders = ", ".join(["%s"] * len(cols))

                    insert_sql = f"""
                        INSERT INTO {full_table} ({col_list})
                        VALUES ({placeholders})
                        ON CONFLICT (id) DO NOTHING
                    """

                    LOG.info(f"   📥 Inserting parent into local database...")
                    cur.execute(insert_sql, vals)

                    if cur.rowcount > 0:
                        conn.commit()
                        LOG.info(f"   ✅ AUTO-RECOVERY SUCCESS!")
                        LOG.info(f"      Parent record {parent_table}[{parent_id}] copied from HQ to local")
                        LOG.info("=" * 80)
                        return True
                    else:
                        LOG.info(f"   ℹ️  Parent already inserted (conflict)")
                        return True

            except Exception as local_error:
                LOG.error(f"   ❌ Failed to insert parent locally: {local_error}")
                if conn:
                    conn.rollback()
                return False
            finally:
                if conn:
                    self.pool.putconn(conn)

        except Exception as e:
            LOG.error(f"❌ AUTO-RECOVERY FAILED: {e}")
            LOG.exception(e)
            return False

    def apply_remote_update_locally(self, table: str, payload: Dict[str, Any]) -> bool:
        """
        Apply a remote update to local DB with comprehensive smart delete support and auto-recovery.

        ✅ ENHANCED VERSION 5.0 - With NULL validation and better error handling

        Handles:
        - Regular updates/inserts with conflict resolution
        - Status changes (activate/deactivate)
        - Smart deletes with dependency-aware cascade
        - Automatic FK-violation fallback to soft delete
        - Auto-recovery of missing parent records from HQ
        - ✅ NEW: Data validation before insert/update
        - ✅ NEW: Enhanced NULL violation error handling
        - ✅ NEW: Better diagnostic logging
        """
        conn = None
        try:
            conn = self.pool.getconn()

            operation = payload.get("operation", "u")
            data = payload.get("data", {})
            row_id = payload.get("row_id")
            remote_updated_at = payload.get("last_modified") or payload.get("updated_at")
            source = payload.get("source", "unknown")

            if "." in table:
                schema, tbl = table.split(".", 1)
            else:
                schema, tbl = "public", table

            quoted_table = f'"{tbl}"'
            full_table = f"{schema}.{quoted_table}"

            # ============================================================
            # Guard: skip broadcast/mirror-sync signals entirely.
            # operation='b' or table='system' are sentinel rows that
            # og_server writes to audit_log to notify clients; they are
            # not real DB rows and must never be upserted locally.
            # ============================================================
            if operation == "b" or tbl == "system":
                LOG.debug(f"⏭️  Skipping broadcast signal: op={operation} table={table}")
                return True

            # Debug logging for non-delete operations
            if operation not in ['d']:
                LOG.debug(f"📥 Processing update: {table}[{row_id}] op={operation}")

            # ============================================================
            # Handle status change operations (activate/deactivate)
            # ============================================================
            if operation in ["activate", "deactivate"]:
                LOG.info(f"📋 Status change operation: {operation} for {table}[{row_id}]")
                return self.apply_status_change_locally(table, payload)

            # ============================================================
            # ENHANCED: Handle DELETE with smart delete system
            # ============================================================
            if operation == 'd':
                LOG.info(f"🗑️  DELETE from HQ: {table}[{row_id}] source={source}")

                with conn.cursor() as cur:
                    # First, check if record exists locally
                    cur.execute(f"SELECT id FROM {full_table} WHERE id = %s", (row_id,))
                    record_exists = cur.fetchone()

                    if not record_exists:
                        LOG.debug(f"✓ Record already deleted locally: {table}[{row_id}]")
                        return True

                    LOG.info(f"🔍 Record exists locally. Checking delete strategy...")

                    # Get table schema info
                    schema_info = self.get_table_schema_info(table)

                    LOG.debug(f"   Table schema:")
                    LOG.debug(f"      has_pending_delete: {schema_info['has_pending_delete']}")
                    LOG.debug(f"      has_active_status: {schema_info['has_active_status']}")

                    # ============================================================
                    # PHASE 1: Attempt hard delete first
                    # ============================================================
                    try:
                        LOG.info(f"🔨 Phase 1: Attempting hard delete...")

                        cur.execute(f"DELETE FROM {full_table} WHERE id = %s", (row_id,))
                        deleted_count = cur.rowcount

                        if deleted_count > 0:
                            conn.commit()
                            LOG.info(f"✅ Hard delete successful: {table}[{row_id}]")
                            LOG.info(f"   Deleted {deleted_count} record(s)")
                            LOG.info("=" * 70)
                            return True
                        else:
                            LOG.warning(f"⚠️  Record not found during delete: {table}[{row_id}]")
                            return True

                    except psycopg2.errors.ForeignKeyViolation as fk_error:
                        # ============================================================
                        # PHASE 2: FK Violation - Must use soft delete
                        # ============================================================
                        conn.rollback()

                        LOG.warning("")
                        LOG.warning("=" * 70)
                        LOG.warning("⚠️  HARD DELETE FAILED - FOREIGN KEY CONSTRAINT")
                        LOG.warning("=" * 70)

                        # Extract FK constraint details from error
                        error_str = str(fk_error)
                        LOG.warning(f"   Error: {error_str[:200]}")

                        # Parse which table is blocking
                        if "still referenced from table" in error_str:
                            blocking_table = error_str.split('table "')[-1].split('"')[0]
                            LOG.warning(f"   Blocking table: {blocking_table}")

                        # Check if table supports soft delete
                        if not schema_info['has_pending_delete'] and not schema_info['has_active_status']:
                            LOG.error("")
                            LOG.error("❌ CANNOT SOFT DELETE")
                            LOG.error(f"   Table '{table}' has no status columns")
                            LOG.error("   Options:")
                            LOG.error("   1. Add pending_delete or active_status columns")
                            LOG.error("   2. Manually delete dependent records first")
                            LOG.error("   3. Contact administrator")
                            LOG.error("=" * 70)
                            return False

                        LOG.warning("")
                        LOG.warning("🔄 Phase 2: Converting to SOFT DELETE with dependency cascade...")
                        LOG.warning("=" * 70)

                        # ============================================================
                        # Use smart delete handler for comprehensive cascade
                        # ============================================================
                        try:
                            from sync.soft_delete_handler import (
                                perform_smart_delete,
                                SoftDeleteDependencyChecker
                            )

                            LOG.info("✓ Smart delete handler loaded")
                            LOG.info("")

                            # Execute smart delete with full dependency cascade
                            success, operation_type, message, changes = perform_smart_delete(
                                conn=conn,
                                table=table,
                                row_id=row_id,
                                client_id=f"agent-{self.client_id}",
                                force_hard_delete=False
                            )

                            if success:
                                LOG.info("")
                                LOG.info("=" * 70)
                                LOG.info(f"✅ SMART DELETE CASCADE SUCCESSFUL")
                                LOG.info(f"   Operation: {operation_type.upper()}")
                                LOG.info(f"   Primary record: {table}[{row_id}]")
                                LOG.info(f"   Total changes: {len(changes)} records affected")
                                LOG.info(f"   Cascaded: {len(changes) - 1} dependent records")
                                LOG.info(f"   Message: {message}")
                                LOG.info("=" * 70)

                                # Log affected tables summary
                                if changes:
                                    affected_tables = {}
                                    for change in changes:
                                        tbl = change.get('table', 'unknown')
                                        affected_tables[tbl] = affected_tables.get(tbl, 0) + 1

                                    LOG.info("")
                                    LOG.info("📊 Cascade Summary:")
                                    for tbl, count in sorted(affected_tables.items()):
                                        LOG.info(f"   • {tbl}: {count} record(s)")
                                    LOG.info("")

                                return True
                            else:
                                LOG.error("")
                                LOG.error("=" * 70)
                                LOG.error(f"❌ SMART DELETE FAILED")
                                LOG.error(f"   Error: {message}")
                                LOG.error("=" * 70)
                                return False

                        except ImportError as import_error:
                            # ============================================================
                            # FALLBACK: Manual soft delete without cascade
                            # ============================================================
                            LOG.warning("")
                            LOG.warning("⚠️  Smart delete handler not available")
                            LOG.warning(f"   Error: {import_error}")
                            LOG.warning("")
                            LOG.warning("🔄 Falling back to SIMPLE SOFT DELETE (no cascade)")
                            LOG.warning("   ⚠️  WARNING: Dependent records will NOT be soft deleted")
                            LOG.warning("=" * 70)

                            # Simple soft delete without dependency cascade
                            update_parts = []

                            if schema_info['has_pending_delete']:
                                update_parts.append("pending_delete = TRUE")
                                LOG.info("   Setting: pending_delete = TRUE")

                            if schema_info['has_active_status']:
                                update_parts.append("active_status = FALSE")
                                LOG.info("   Setting: active_status = FALSE")

                            if update_parts:
                                update_parts.append("updated_at = NOW()")
                                update_sql = f"""
                                    UPDATE {full_table}
                                    SET {', '.join(update_parts)}
                                    WHERE id = %s
                                """

                                cur.execute(update_sql, (row_id,))
                                conn.commit()

                                LOG.info("")
                                LOG.info("✅ Simple soft delete applied (PRIMARY RECORD ONLY)")
                                LOG.info("   ⚠️  Dependent records NOT cascaded")
                                LOG.info("   Consider installing soft_delete_handler.py for full cascade")
                                LOG.info("=" * 70)
                                return True
                            else:
                                LOG.error("")
                                LOG.error("❌ Cannot soft delete: no status columns available")
                                LOG.error("=" * 70)
                                return False

                        except Exception as smart_delete_error:
                            LOG.error("")
                            LOG.error("=" * 70)
                            LOG.error(f"💥 SMART DELETE ERROR")
                            LOG.error(f"   {str(smart_delete_error)}")
                            LOG.error("=" * 70)
                            LOG.exception(smart_delete_error)
                            return False

                    except Exception as delete_error:
                        conn.rollback()
                        LOG.error("")
                        LOG.error("=" * 70)
                        LOG.error(f"💥 DELETE OPERATION ERROR")
                        LOG.error(f"   Table: {table}")
                        LOG.error(f"   Row ID: {row_id}")
                        LOG.error(f"   Error: {str(delete_error)}")
                        LOG.error("=" * 70)
                        LOG.exception(delete_error)
                        return False

            # ============================================================
            # Handle regular UPDATE/INSERT operations
            # ============================================================
            with conn.cursor() as cur:
                # Conflict resolution with timezone normalization
                if self.config["sync"].get("conflict_resolution") == "last_write_wins" and remote_updated_at:
                    cur.execute(f"""
                        SELECT updated_at
                        FROM {full_table}
                        WHERE id = %s
                    """, (row_id,))

                    result = cur.fetchone()
                    if result and result[0]:
                        local_updated_at = result[0]

                        def normalize_to_utc(timestamp):
                            if isinstance(timestamp, datetime):
                                if timestamp.tzinfo is None:
                                    return timestamp.replace(tzinfo=timezone.utc)
                                else:
                                    return timestamp.astimezone(timezone.utc)
                            elif isinstance(timestamp, str):
                                try:
                                    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                                    if dt.tzinfo is None:
                                        return dt.replace(tzinfo=timezone.utc)
                                    return dt.astimezone(timezone.utc)
                                except ValueError:
                                    return datetime.now(timezone.utc)
                            else:
                                return datetime.now(timezone.utc)

                        local_utc = normalize_to_utc(local_updated_at)
                        remote_utc = normalize_to_utc(remote_updated_at)
                        time_diff = abs((local_utc - remote_utc).total_seconds())

                        if local_utc > remote_utc and time_diff > 1.0:
                            LOG.warning("⚠️  Conflict detected for %s id=%s: local is newer, skipping",
                                    table, row_id)
                            return True
                        elif time_diff <= 1.0:
                            LOG.debug("Timestamps represent same moment (diff=%.3fs): %s id=%s",
                                    time_diff, table, row_id)

                # ✅ CRITICAL FIX: Validate data before processing
                is_valid, validation_error = self.validate_update_data(table, row_id, data, operation)

                if not is_valid:
                    LOG.error("")
                    LOG.error("=" * 80)
                    LOG.error("💥 DATA VALIDATION FAILED")
                    LOG.error("=" * 80)
                    LOG.error(f"   Table: {table}")
                    LOG.error(f"   Row ID: {row_id}")
                    LOG.error(f"   Operation: {operation}")
                    LOG.error(f"   Error: {validation_error}")
                    LOG.error(f"   Data keys received: {list(data.keys()) if data else 'NONE'}")
                    LOG.error("=" * 80)
                    LOG.error("")
                    LOG.error("🔍 ROOT CAUSE:")
                    LOG.error("   The HQ server sent an incomplete update")
                    LOG.error("   This usually means:")
                    LOG.error("   1. audit_log.data field is empty/NULL")
                    LOG.error("   2. Server didn't fetch actual row data")
                    LOG.error("   3. Row was deleted before sync")
                    LOG.error("")
                    LOG.error("💡 SOLUTION:")
                    LOG.error(f"   1. Check HQ server logs for table: {table}")
                    LOG.error(f"   2. Verify row exists: SELECT * FROM {table} WHERE id = '{row_id}'")
                    LOG.error(f"   3. Check audit_log: SELECT * FROM audit_log WHERE row_id = '{row_id}'")
                    LOG.error("=" * 80)
                    LOG.error("")

                    return False

                LOG.debug(f"✓ Data validation passed for {table} id={row_id}")

                # Get schema info and set defaults for status columns
                schema_info = self.get_table_schema_info(table)

                if schema_info['has_pending_delete'] and 'pending_delete' not in data:
                    data['pending_delete'] = False
                    LOG.debug("Added default pending_delete=False for %s id=%s", table, row_id)

                if schema_info['has_active_status'] and 'active_status' not in data:
                    data['active_status'] = True
                    LOG.debug("Added default active_status=True for %s id=%s", table, row_id)

                # Get JSON columns and prepare values correctly
                json_columns = self.get_json_columns(table)

                def prepare_value(key, val):
                    if val is None:
                        return None
                    elif key in json_columns:
                        if isinstance(val, (dict, list)):
                            return Json(val)
                        elif isinstance(val, str):
                            try:
                                parsed = json.loads(val)
                                return Json(parsed)
                            except:
                                return Json(val)
                        else:
                            return Json(val)
                    elif isinstance(val, (dict, list)):
                        return Json(val)
                    else:
                        return val

                local_columns = schema_info.get('columns', set())
                processed_data = {}
                for key, value in data.items():
                    if key == 'pending_delete' and not schema_info['has_pending_delete']:
                        continue
                    if key == 'active_status' and not schema_info['has_active_status']:
                        continue
                    # Drop columns that don't exist locally — these are HQ-only
                    # fields added by migrations not yet applied on this client
                    # (e.g. previous_status, fixed_by). Silently skipping them
                    # prevents UndefinedColumn crashes; they'll be picked up once
                    # the client migration runs.
                    if local_columns and key not in local_columns:
                        LOG.warning(
                            "Warning: Skipping column '%s' for %s — not in local schema "                            "(migration pending?)", key, table
                        )
                        continue
                    processed_data[key] = prepare_value(key, value)

                cols = list(processed_data.keys())
                vals = [processed_data[c] for c in cols] if cols else []

                if cols:
                    col_list = ", ".join([f'"{c}"' for c in cols])
                    placeholders = ", ".join(["%s"] * len(cols))
                    set_clause = ", ".join([f'"{c}" = EXCLUDED."{c}"' for c in cols])

                    sql_upsert = f"""
                        INSERT INTO {full_table} ({col_list})
                        VALUES ({placeholders})
                        ON CONFLICT (id) DO UPDATE SET {set_clause};
                    """

                    cur.execute(sql_upsert, vals)
                    LOG.debug("✓ Applied remote update for %s id=%s", table, row_id)
                else:
                    LOG.warning("⚠️  No data to apply for %s id=%s", table, row_id)

                conn.commit()
                return True

        except psycopg2.errors.ForeignKeyViolation as fk_error:
            if conn:
                conn.rollback()

            LOG.warning("")
            LOG.warning("=" * 80)
            LOG.warning("⚠️  FOREIGN KEY VIOLATION DETECTED")
            LOG.warning("=" * 80)

            error_str = str(fk_error)
            LOG.warning(f"   Error: {error_str[:300]}")

            # 🔧 ENHANCED: Parse FK violation to extract parent info
            import re

            # Extract FK column and value from error message
            # Format: Key (equipment_id)=(uuid) is not present in table "Inventory_equipment"
            fk_match = re.search(r'Key \((\w+)\)=\(([^)]+)\)', error_str)

            if fk_match:
                fk_column = fk_match.group(1)
                parent_id = fk_match.group(2)

                LOG.info(f"   📋 Parsed FK details:")
                LOG.info(f"      FK Column: {fk_column}")
                LOG.info(f"      Parent ID: {parent_id}")

                # Determine parent table from FK relationships
                try:
                    conn_temp = self.pool.getconn()

                    try:
                        if "." in table:
                            schema, tbl = table.split(".", 1)
                        else:
                            schema, tbl = "public", table

                        with conn_temp.cursor(cursor_factory=RealDictCursor) as cur:
                            cur.execute("""
                                SELECT
                                    ccu.table_schema || '.' || ccu.table_name as parent_table,
                                    ccu.column_name as parent_column
                                FROM information_schema.table_constraints AS tc
                                JOIN information_schema.key_column_usage AS kcu
                                    ON tc.constraint_name = kcu.constraint_name
                                    AND tc.table_schema = kcu.table_schema
                                JOIN information_schema.constraint_column_usage AS ccu
                                    ON ccu.constraint_name = tc.constraint_name
                                WHERE tc.constraint_type = 'FOREIGN KEY'
                                    AND tc.table_schema = %s
                                    AND tc.table_name = %s
                                    AND kcu.column_name = %s
                                LIMIT 1
                            """, (schema, tbl, fk_column))

                            fk_info = cur.fetchone()

                            if fk_info:
                                parent_table = fk_info['parent_table']

                                LOG.info(f"   🎯 Identified parent table: {parent_table}")
                                LOG.info("")
                                LOG.info("   🔧 ATTEMPTING AUTO-RECOVERY FROM HQ...")
                                LOG.info("=" * 80)

                                # 🆕 TRY TO AUTO-RECOVER THE MISSING PARENT
                                recovery_success = self.auto_recover_missing_parents_from_hq(
                                    table=table,
                                    row_id=row_id,
                                    fk_column=fk_column,
                                    parent_table=parent_table,
                                    parent_id=parent_id
                                )

                                if recovery_success:
                                    LOG.info("")
                                    LOG.info("=" * 80)
                                    LOG.info("✅ AUTO-RECOVERY SUCCESSFUL!")
                                    LOG.info("=" * 80)
                                    LOG.info(f"   Parent {parent_table}[{parent_id}] recovered from HQ")
                                    LOG.info(f"   🔄 RETRYING child record insertion...")
                                    LOG.info("=" * 80)

                                    # RETRY the original update now that parent exists
                                    return self.apply_remote_update_locally(table, payload)
                                else:
                                    LOG.error("")
                                    LOG.error("=" * 80)
                                    LOG.error("❌ AUTO-RECOVERY FAILED")
                                    LOG.error("=" * 80)
                                    LOG.error(f"   Could not recover {parent_table}[{parent_id}] from HQ")
                                    LOG.error(f"   This is an orphaned reference!")
                                    LOG.error("=" * 80)
                                    return False
                            else:
                                LOG.error(f"   ❌ Could not determine parent table for FK: {fk_column}")
                                return False
                    finally:
                        self.pool.putconn(conn_temp)

                except Exception as lookup_error:
                    LOG.error(f"   ❌ Error during FK lookup: {lookup_error}")
                    return False
            else:
                LOG.error("   ❌ Could not parse FK violation error")
                return False

        except psycopg2.errors.UniqueViolation as uv_error:
            # ── Duplicate value on a non-PK unique constraint (e.g. certificate_number) ──
            # The upsert only conflicts on (id), so a second unique column that already
            # belongs to a different row triggers this.  Treat as a skip: the record
            # already exists locally under a different id — applying it would corrupt data.
            if conn:
                conn.rollback()

            error_msg = str(uv_error)
            # Extract the constraint and duplicate value for a helpful log message
            try:
                constraint = error_msg.split('constraint "')[1].split('"')[0]
            except Exception:
                constraint = "unknown"
            try:
                dup_value = error_msg.split("Key (")[1].split(")=")[0] + "=" + error_msg.split(")=(")[1].split(")")[0]
            except Exception:
                dup_value = error_msg[:120]

            LOG.warning("")
            LOG.warning("=" * 80)
            LOG.warning("⚠️  UNIQUE CONSTRAINT VIOLATION — SKIPPING RECORD")
            LOG.warning("=" * 80)
            LOG.warning(f"   Table     : {table}")
            LOG.warning(f"   Row ID    : {row_id}")
            LOG.warning(f"   Constraint: {constraint}")
            LOG.warning(f"   Duplicate : {dup_value}")
            LOG.warning("")
            LOG.warning("   This means the local DB already has a *different* row with the")
            LOG.warning("   same unique value.  Possible causes:")
            LOG.warning("   • Certificate was generated locally AND at HQ before sync completed")
            LOG.warning("   • Record was inserted locally with a conflicting number")
            LOG.warning("")
            LOG.warning("   The remote record has been SKIPPED to protect local data integrity.")
            LOG.warning("   Review both records manually and merge if needed.")
            LOG.warning("=" * 80)
            LOG.warning("")
            # Return True so the download loop marks this record as processed and
            # advances the checkpoint — we don't want to retry it on every cycle.
            return True

        except psycopg2.errors.NotNullViolation as null_error:
            # ✅ NEW: Enhanced NULL violation error handler
            if conn:
                conn.rollback()

            # Extract column name from error message
            error_msg = str(null_error)
            try:
                column_match = error_msg.split('column "')[1].split('"')[0]
            except:
                column_match = 'unknown'

            try:
                failing_row = error_msg.split('Failing row contains (')[1].split(')')[0]
            except:
                failing_row = 'not available'

            LOG.error("")
            LOG.error("=" * 80)
            LOG.error("💥 NULL CONSTRAINT VIOLATION")
            LOG.error("=" * 80)
            LOG.error(f"   Table: {table}")
            LOG.error(f"   Row ID: {row_id}")
            LOG.error(f"   Column: {column_match}")
            LOG.error(f"   Operation: {operation}")
            LOG.error(f"   Data keys: {list(data.keys()) if data else 'NONE'}")
            LOG.error("=" * 80)
            LOG.error("")
            LOG.error("🔍 DIAGNOSTIC INFORMATION:")
            LOG.error(f"   • The column '{column_match}' requires a value but received NULL")
            LOG.error(f"   • Failing row: ({failing_row})")
            LOG.error(f"   • This indicates incomplete data from HQ server")
            LOG.error("")
            LOG.error("📊 DATA ANALYSIS:")
            if data:
                LOG.error(f"   Received {len(data)} fields:")
                for key, value in list(data.items())[:10]:  # Show first 10
                    value_str = 'NULL' if value is None else f'{type(value).__name__}'
                    LOG.error(f"      • {key}: {value_str}")
                if len(data) > 10:
                    LOG.error(f"      ... and {len(data) - 10} more fields")
            else:
                LOG.error("   ⚠️  NO DATA RECEIVED FROM SERVER")
            LOG.error("")
            LOG.error("🛠️  TROUBLESHOOTING STEPS:")
            LOG.error(f"   1. SSH to HQ server")
            LOG.error(f"   2. Check audit_log:")
            LOG.error(f"      SELECT data FROM audit_log WHERE row_id = '{row_id}' ORDER BY received_at DESC LIMIT 1;")
            LOG.error(f"   3. Check source table:")
            LOG.error(f"      SELECT * FROM {table} WHERE id = '{row_id}';")
            LOG.error(f"   4. Verify server logs:")
            LOG.error(f"      tail -f logs/sync_server.log | grep '{row_id}'")
            LOG.error("")
            LOG.error("💡 COMMON CAUSES:")
            LOG.error("   • Server fetch_updates_since() not fetching row data")
            LOG.error("   • audit_log.data field is empty/NULL")
            LOG.error("   • Row deleted before sync completed")
            LOG.error("   • Database constraint mismatch between HQ and client")
            LOG.error("")
            LOG.error("🔧 TECHNICAL DETAILS:")
            LOG.error(f"   {error_msg}")
            LOG.error("=" * 80)
            LOG.error("")

            return False

        except Exception as e:
            # ✅ ENHANCED: Better generic error handler
            if conn:
                conn.rollback()

            LOG.error("")
            LOG.error("=" * 80)
            LOG.error("💥 UNEXPECTED ERROR APPLYING UPDATE")
            LOG.error("=" * 80)
            LOG.error(f"   Table: {table}")
            LOG.error(f"   Row ID: {row_id}")
            LOG.error(f"   Operation: {operation}")
            LOG.error(f"   Error Type: {type(e).__name__}")
            LOG.error(f"   Error Message: {str(e)}")
            LOG.error("=" * 80)
            LOG.error("")
            LOG.error("📊 UPDATE DETAILS:")
            LOG.error(f"   • Has data: {bool(data)}")
            if data:
                LOG.error(f"   • Data fields: {len(data)}")
                LOG.error(f"   • Sample keys: {list(data.keys())[:5]}")
            LOG.error(f"   • Timestamp: {remote_updated_at}")
            LOG.error("")
            LOG.error("🔍 STACK TRACE:")
            LOG.exception(e)
            LOG.error("=" * 80)
            LOG.error("")

            return False

        finally:
            if conn:
                self.pool.putconn(conn)
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

    # ============================================================
    # CERTIFICATE SYNC FEATURES
    # ============================================================

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

    # ============================================================
    # HEARTBEAT & HEALTH
    # ============================================================

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

    # ============================================================
    # MAIN AGENT CONTROL
    # ============================================================
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

        # 2b. ⚡ Instant Cert Notify Thread (Redis pub/sub — wakes up on cert_ready)
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



    def write_status_file(self, hq_online: bool, pending_changes: int = 0, last_sync: str = None):
        """
        Write sync agent status to file for UI monitoring

        Creates/updates agent_status.json with current sync state.
        Uses atomic write to prevent corruption.

        Args:
            hq_online: Whether HQ server is reachable
            pending_changes: Number of pending changes to upload
            last_sync: ISO timestamp of last successful sync

        Status file location: {state_dir}/agent_status.json
        """
        try:
            # Ensure directory exists
            status_dir = self.state.state_dir / 'sync_state'
            status_dir.mkdir(parents=True, exist_ok=True)

            status_file = status_dir / 'agent_status.json'

            # Prepare status data
            status_data = {
                'hq_online': hq_online,
                'pending_changes': pending_changes,
                'last_sync': last_sync or self.state.get('last_upload_time'),
                'last_update': now_iso(),
                'client_id': self.client_id,
                'machine_id': self.machine_id,
                'version': self.version,
                'tables_monitored': len(self.tables)
            }

            # Atomic write using temporary file
            import tempfile
            import shutil

            try:
                # Create temp file in same directory for atomic move
                with tempfile.NamedTemporaryFile(
                    mode='w',
                    dir=status_file.parent,
                    delete=False,
                    suffix='.tmp',
                    prefix='agent_status_'
                ) as f:
                    json.dump(status_data, f, indent=2)
                    temp_path = f.name

                # Atomic replace
                shutil.move(temp_path, status_file)

                LOG.debug(
                    f"📊 Status updated: HQ={'online' if hq_online else 'offline'}, "
                    f"pending={pending_changes}"
                )

            except Exception as write_error:
                # Clean up temp file if it exists
                try:
                    if 'temp_path' in locals() and Path(temp_path).exists():
                        Path(temp_path).unlink()
                except:
                    pass
                raise write_error

        except Exception as e:
            LOG.debug(f"Could not write status file: {e}")
            # Don't raise - status file is non-critical


    def get_status_summary(self) -> dict:
        """
        Get current sync agent status summary

        Returns:
            dict: Current status including:
                - hq_online: bool
                - pending_changes: int
                - last_sync: str (ISO timestamp)
                - uptime: float (seconds)
        """
        try:
            status_file = self.state.state_dir / 'sync_state' / 'agent_status.json'

            if status_file.exists():
                with open(status_file, 'r') as f:
                    return json.load(f)
            else:
                return {
                    'hq_online': False,
                    'pending_changes': 0,
                    'last_sync': None,
                    'last_update': None,
                    'error': 'Status file not found'
                }
        except Exception as e:
            return {
                'hq_online': False,
                'pending_changes': 0,
                'last_sync': None,
                'error': str(e)
            }
def print_startup_banner():
    """Display startup banner with Kenyan time"""
    current_time = format_kenyan_time(now_kenyan())

    LOG.info("")
    LOG.info("=" * 80)
    LOG.info("⚡ INSTANT SYNC AGENT - KENYAN EDITION")
    LOG.info("=" * 80)
    LOG.info(f"   🕐 Started at: {current_time}")
    LOG.info(f"   🇰🇪 Timezone: East Africa Time (EAT/UTC+3)")
    LOG.info(f"   ⚡ Mode: INSTANT UPLOADS (1-second polling)")
    LOG.info(f"   🚀 Performance: Optimized for speed")
    LOG.info("=" * 80)
    LOG.info("")

# ---------- Signal Handlers ----------

def signal_handler(signum, frame):
    LOG.info("Received signal %d, shutting down...", signum)

# ---------- Main Entry Point ----------

def main():
    """Main entry point with config.py support and thread safety"""
    import threading


    is_main_thread = threading.current_thread() is threading.main_thread()

    if is_main_thread:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        LOG.info("✅ Signal handlers registered (main thread)")
    else:
        LOG.info("⚠️  Running in background thread - signal handlers skipped")

    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='Cirqen Sync Agent')
    parser.add_argument('--data-dir', type=Path,
                       default=Path.home() / '.cmms',
                       help='Application data directory (default: ~/.cmms)')
    parser.add_argument('--use-env', action='store_true',
                       help='Force using .env file instead of config.py')
    parser.add_argument('--config-summary', action='store_true',
                       help='Print configuration summary and exit')
    parser.add_argument('--log-level', type=str, default='DEBUG',
                       choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                       help='Logging verbosity (default: DEBUG)')
    parser.add_argument('--log-file', type=str, default=None,
                       help='Optional path to a log file (in addition to stdout)')

    # parse_known_args: tolerate extra argv injected by the thread launcher
    args, _unknown = parser.parse_known_args()

    # Re-apply logging now that we know the desired level and file
    _level = getattr(logging, args.log_level.upper(), logging.DEBUG)
    _log_file = args.log_file
    if _log_file is None:
        # Default log file next to the data dir
        _default_log = Path(args.data_dir) / 'logs' / 'sync_agent.log'
        _log_file = str(_default_log)
    setup_logging(log_file=_log_file, level=_level)
    LOG.info("Logging initialised — level=%s, file=%s", args.log_level, _log_file)

    # Load configuration
    config = load_agent_config(args.data_dir, force_env=args.use_env)

    # If user just wants to see config, show it and exit
    if args.config_summary:
        if CONFIG_MANAGER_AVAILABLE and not args.use_env:
            cfg_manager = load_config(args.data_dir)
            print(cfg_manager.get_config_summary())
        else:
            print("\n📋 Current Configuration:")
            print(json.dumps(config, indent=2))
        return

    # Start sync agent
    agent = None
    try:
        agent = SyncAgent(config=config, data_path=args.data_dir)
        agent.start()
    except KeyboardInterrupt:
        LOG.info("Interrupted by user")
    except Exception as e:
        LOG.exception("Fatal error: %s", e)
        sys.exit(1)
    finally:
        if agent:
            agent.stop()


if __name__ == "__main__":
    main()
