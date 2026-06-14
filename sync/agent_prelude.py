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
            "download_interval_seconds": int(os.getenv("SYNC_DOWNLOAD_INTERVAL", "1")),  # ⚡ instant polling
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



