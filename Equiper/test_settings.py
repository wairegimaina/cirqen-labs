"""Hermetic test settings.

Runs the suite entirely in-process:
  * in-memory SQLite (never touches the real local database),
  * the database router disabled so every model lives in one test DB,
  * local-memory cache + DB sessions so nothing reaches out to Redis,
  * fast password hashing.

Usage:
    ./venv/bin/python manage.py test Inventory --settings=Equiper.test_settings

Tests marked core.testing.requires_postgres skip on SQLite. To run them, point
CIRQEN_TEST_DATABASE_URL at a PostgreSQL server you can create databases on
(Django makes and drops test_<name>):
    CIRQEN_TEST_DATABASE_URL=postgresql://user:pass@127.0.0.1:5432/cirqen \
        ./venv/bin/python manage.py test --tag postgres --settings=Equiper.test_settings
"""
import os
from urllib.parse import unquote, urlparse

from Equiper.settings import *  # noqa: F401,F403

_pg_url = os.getenv("CIRQEN_TEST_DATABASE_URL", "")
if _pg_url:
    _u = urlparse(_pg_url)
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _u.path.lstrip("/") or "cirqen",
            "USER": unquote(_u.username or ""),
            "PASSWORD": unquote(_u.password or ""),
            "HOST": _u.hostname or "127.0.0.1",
            "PORT": str(_u.port or 5432),
        },
    }
else:
    DATABASES = {
        "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
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
