"""Poller indexes are created without locking and without failing (core/db_indexes.py)."""
import psycopg2
import pytest

from core import db_indexes


@pytest.fixture
def cursor(pg_dsn):
    conn = psycopg2.connect(**pg_dsn)
    conn.autocommit = True  # as in the non-atomic migration
    cur = conn.cursor()
    cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    cur.execute('CREATE TABLE "jobcard_jobcard" (id int, updated_at timestamptz)')
    cur.execute('CREATE TABLE "Inventory_equipment" (id int, updated_at timestamptz, '
                'department_id int, active_status boolean)')
    cur.execute('CREATE TABLE "workshop_workshop" (id int)')  # no updated_at column
    yield cur
    conn.close()


def _indexes(cur):
    cur.execute("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
    return {r[0] for r in cur.fetchall()}


def test_creates_indexes_for_existing_columns_and_skips_the_rest(cursor):
    done, skipped, failed = db_indexes.ensure_indexes(cursor)
    names = _indexes(cursor)
    assert "cq_jobcard_jobcard_updated_at" in names
    assert "cq_inventory_equipment_updated_at" in names
    assert "cq_inventory_equipment_department_id_active_status" in names
    assert "cq_workshop_workshop_updated_at" in skipped
    assert failed == []


def test_is_idempotent(cursor):
    db_indexes.ensure_indexes(cursor)
    done, _, failed = db_indexes.ensure_indexes(cursor)
    assert failed == [] and "cq_jobcard_jobcard_updated_at" in done


def test_index_names_fit_postgres_limit():
    assert all(len(name) <= 63 for name, _, _ in db_indexes.planned_indexes())
    assert len({name for name, _, _ in db_indexes.planned_indexes()}) == len(db_indexes.planned_indexes())
