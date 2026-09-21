"""Hermetic test settings.

Runs the suite entirely in-process:
  * in-memory SQLite for both DB aliases (never touches the real local or the
    remote HQ Postgres),
  * the database router disabled so every model lives in one test DB,
  * local-memory cache + DB sessions so nothing reaches out to Redis,
  * fast password hashing.

Usage:
    ./venv/bin/python manage.py test Inventory --settings=Equiper.test_settings
"""
from Equiper.settings import *  # noqa: F401,F403

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    "hq": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
}

# All models resolve to the single default test DB.
DATABASE_ROUTERS = []

# Never talk to Redis during tests.
CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "sessions": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "offline": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
    "throttle": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "throttle"},
}

SESSION_ENGINE = "django.contrib.sessions.backends.db"

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Never talk to HQ; tests that exercise HQ calls mock them.
HQ_SYNC_API_URL = ""
HQ_INSTANT_PUSH = False

# Keep test output quiet and deterministic.
import logging  # noqa: E402
logging.disable(logging.CRITICAL)
