"""Test helpers shared across apps."""
import unittest

from django.db import connection
from django.test import tag


def requires_postgres(cls):
    """Mark tests that run the raw PostgreSQL SQL the sync agent and migrate_hq use
    (now(), pg_indexes, UUID parameters, the raw driver connection).

    The normal suite runs on in-memory SQLite and skips them. Set
    CIRQEN_TEST_DATABASE_URL to run them on PostgreSQL; CI does, with
    ``manage.py test --tag postgres``.
    """
    cls = tag("postgres")(cls)
    return unittest.skipUnless(
        connection.vendor == "postgresql", "needs PostgreSQL: set CIRQEN_TEST_DATABASE_URL"
    )(cls)
