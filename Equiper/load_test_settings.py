"""Settings for the Level 6 load test (core.tests.test_load_level6).

The hermetic test settings on PostgreSQL, which the load test needs: SQLite
timings say nothing about a hospital server. Connection from the standard
PG* variables.

    CIRQEN_LOAD_TEST=1 PGHOST=127.0.0.1 PGUSER=postgres PGPASSWORD=... \\
      python manage.py test core.tests.test_load_level6 --settings=Equiper.load_test_settings
"""
import os

from Equiper.test_settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("PGDATABASE", "cirqen_load"),
        "USER": os.getenv("PGUSER", "postgres"),
        "PASSWORD": os.getenv("PGPASSWORD", ""),
        "HOST": os.getenv("PGHOST", "127.0.0.1"),
        "PORT": os.getenv("PGPORT", "5432"),
    },
}
