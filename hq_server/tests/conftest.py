"""Cirqen Control tests (the update server and its admin panel).

    cd hq_server && pip install -r requirements.txt pytest && pytest tests
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))          # hq_server modules first (its main.py, not the desktop's)
sys.path.append(str(HERE.parent.parent))      # last: only for the desktop's hq_handshake

import os  # noqa: E402

import pytest  # noqa: E402

# Run the whole suite on PostgreSQL too (as on Render's free plan with a
# Supabase project): TEST_CONTROL_DATABASE_URL=postgresql://... pytest tests
_PG = os.getenv("TEST_CONTROL_DATABASE_URL", "")
_TABLES = ("mpesa_inbox, payments, invoices, licences, enrollment_tokens, hq_certificates, sessions, "
           "login_attempts, audit, admins, hospitals")


@pytest.fixture(autouse=True)
def _control_database(monkeypatch):
    if not _PG:
        yield
        return
    import psycopg2

    monkeypatch.setenv("CONTROL_DATABASE_URL", _PG)
    with psycopg2.connect(_PG) as c, c.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")
    import control_store

    control_store.init()
    yield
