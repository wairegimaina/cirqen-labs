import datetime
from django.conf import settings
import requests
import time
from django.utils import timezone


# ============================================================
# 🔹 GLOBAL CONNECTIVITY CHECK
# ============================================================

HQ_HEALTH_URL = getattr(settings, "HQ_HEALTH_URL", "https://hq-server-dgs6.onrender.com/api/sync/health")

def is_online(url: str = None, timeout: int = 2) -> bool:
    """Check if HQ server is online."""
    try:
        target_url = url or HQ_HEALTH_URL
        r = requests.get(target_url, timeout=timeout)
        return r.status_code == 200 and r.json().get("status") in ["UP", "ok"]
    except Exception:
        return False


# ============================================================
# 🔹 DATABASE ROUTER
# ============================================================

class EquiperDatabaseRouter:
    """
    Dynamic database router for the CMMS + Calibration system.

    Behavior:
        🔸 Reads:
            - HQ PostgreSQL when online AND record older than 30 days.
            - Local PostgreSQL otherwise.
        🔸 Writes:
            - Always local (default), later synced to HQ.
        🔸 Migrations:
            - Run on BOTH HQ and Local databases to keep schema identical.
    """

    HQ_HEALTH_URL = "https://hq-server-dgs6.onrender.com/api/sync/health"  # hospital server health endpoint
    ONLINE_TIMEOUT = 2  # seconds
    _cache_ttl = 60     # seconds to reuse the last known connection status

    _hq_online_cache = None
    _last_check_time = 0

    # --------------------------------------------------------
    # 🧠 Connectivity Check (Cached)
    # --------------------------------------------------------
    def is_hq_online(self) -> bool:
        """
        Cached HQ connectivity check to reduce network calls.
        Returns cached result if last check was within TTL.
        """
        now = time.time()
        if (now - self._last_check_time) < self._cache_ttl:
            return self._hq_online_cache

        # Update cache with current network check
        self._hq_online_cache = is_online(self.HQ_HEALTH_URL, self.ONLINE_TIMEOUT)
        self._last_check_time = now
        return self._hq_online_cache

    # --------------------------------------------------------
    # 📖 Database Read Routing
    # --------------------------------------------------------
    def db_for_read(self, model, **hints):
        """
        Route reads based on connectivity and record age:
            - HQ if online and record older than 30 days.
            - Local (default) otherwise.
        """
        # If HQ is offline, always use local
        if not self.is_hq_online():
            return "default"

        # If model tracks creation date, route older data to HQ
        created_field = next((f for f in model._meta.fields if f.name == "created"), None)
        if created_field:
            instance = hints.get("instance")
            if instance and hasattr(instance, "created"):
                cutoff = timezone.now() - datetime.timedelta(days=30)
                if instance.created < cutoff:
                    return "hq"

        # Default: use local
        return "default"

    # --------------------------------------------------------
    # ✏️ Database Write Routing
    # --------------------------------------------------------
    def db_for_write(self, model, **hints):
        """
        Always write to the local DB.
        Sync agent will replicate changes to HQ when online.
        """
        return "default"

    # --------------------------------------------------------
    # 🔗 Relations Between Databases
    # --------------------------------------------------------
    def allow_relation(self, obj1, obj2, **hints):
        """
        Allow relations between objects from either DB.
        This ensures smooth joins and querysets across both.
        """
        db_list = ("default", "hq")
        if obj1._state.db in db_list and obj2._state.db in db_list:
            return True
        return None

    # --------------------------------------------------------
    # 🧱 Database Migration Policy
    # --------------------------------------------------------
    def allow_migrate(self, db, app_label, model_name=None, **hints):
        """
        Apply migrations on BOTH HQ and Local databases.
        Ensures identical schemas for replication and CDC.
        """
        return db in ("default", "hq")
