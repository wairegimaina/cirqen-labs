"""Startup check that says plainly whether the Redis caches are reachable.

The caches are configured to fail soft (see ``CACHES`` in settings), so an
unreachable Redis no longer breaks the app — it just makes every lookup a miss.
That silence is the danger: this check puts one clear line in the log at
startup so a slow app can be traced to a dead cache.
"""
import logging

from django.conf import settings
from django.core.cache import caches

logger = logging.getLogger("cirqen.cache")

PROBE_KEY = "cirqen:health:probe"


def cache_is_available(alias):
    """True when a value written to the cache can be read back."""
    try:
        cache = caches[alias]
        cache.set(PROBE_KEY, "ok", 5)
        return cache.get(PROBE_KEY) == "ok"
    except Exception:
        return False


def log_cache_status():
    """Log one line per Redis-backed cache; return {alias: available}."""
    status = {}
    for alias, conf in settings.CACHES.items():
        if "redis" not in conf.get("BACKEND", "").lower():
            continue
        status[alias] = available = cache_is_available(alias)
        if not available:
            logger.warning(
                "Cache %r (%s) is unavailable; running without it. Pages will be "
                "slower and sessions are read from the database.",
                alias, conf.get("LOCATION"),
            )
    return status
