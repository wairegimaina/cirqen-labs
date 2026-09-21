"""Update rollback restores the PostgreSQL database (IMPROVEMENT_PLAN.md section 9)."""
import psycopg2
import pytest

from updates import db_snapshot

from .conftest import PG_BIN


@pytest.fixture
def db(pg_dsn, monkeypatch):
    monkeypatch.setenv("CIRQEN_PG_BIN", str(PG_BIN))
    conn = psycopg2.connect(**pg_dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cur.execute("CREATE TABLE equipment (id int PRIMARY KEY, serial text NOT NULL)")
        cur.execute("INSERT INTO equipment VALUES (1, 'A'), (2, 'B')")
        cur.execute("CREATE TABLE django_migrations (app text, name text)")
        cur.execute("INSERT INTO django_migrations VALUES ('Inventory', '0001_initial')")
    yield conn, {"NAME": pg_dsn["dbname"], "USER": pg_dsn["user"], "HOST": pg_dsn["host"],
                 "PORT": pg_dsn["port"], "PASSWORD": ""}
    conn.close()


def _state(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY 1")
        tables = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='equipment' ORDER BY 1")
        columns = [r[0] for r in cur.fetchall()]
        cur.execute("SELECT id, serial FROM equipment ORDER BY id")
        rows = cur.fetchall()
    return tables, columns, rows


def test_restore_undoes_a_partial_migration(db, tmp_path):
    conn, cfg = db
    before = _state(conn)
    snapshot = db_snapshot.dump(cfg, tmp_path / "default.pgdump")

    with conn.cursor() as cur:  # what a failed update's migration might leave behind
        cur.execute("ALTER TABLE equipment ADD COLUMN category text")
        cur.execute("CREATE TABLE new_feature (id int)")
        cur.execute("DELETE FROM equipment WHERE id = 2")
        cur.execute("INSERT INTO django_migrations VALUES ('Inventory', '0002_category')")

    db_snapshot.restore(cfg, snapshot)
    assert _state(conn) == before


def test_failed_restore_leaves_the_database_untouched(db, tmp_path):
    conn, cfg = db
    before = _state(conn)
    bogus = tmp_path / "broken.pgdump"
    bogus.write_bytes(b"not a dump")
    with pytest.raises(db_snapshot.SnapshotError):
        db_snapshot.restore(cfg, bogus)
    assert _state(conn) == before
