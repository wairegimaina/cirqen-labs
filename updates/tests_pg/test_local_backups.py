"""Local database backup and restore, end to end (IMPROVEMENT_PLAN.md section 9).

Exercises core.backups (behind manage.py backup_db / restore_db) against a
throwaway PostgreSQL cluster: back up, lose data, restore, and undo the
restore with the automatic pre-restore backup.
"""
import psycopg2
import pytest
from django.conf import settings

from .conftest import PG_BIN


@pytest.fixture
def local_db(pg_dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("CIRQEN_PG_BIN", str(PG_BIN))
    cfg = {"ENGINE": "django.db.backends.postgresql", "NAME": pg_dsn["dbname"], "USER": pg_dsn["user"],
           "HOST": pg_dsn["host"], "PORT": pg_dsn["port"], "PASSWORD": ""}
    if not settings.configured:
        settings.configure(DATABASES={"default": cfg}, DATA_PATH=tmp_path)
    monkeypatch.setattr(settings, "DATABASES", {"default": cfg}, raising=False)
    monkeypatch.setattr(settings, "DATA_PATH", tmp_path, raising=False)

    conn = psycopg2.connect(**pg_dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        cur.execute("CREATE TABLE jobcard (id int PRIMARY KEY, note text)")
        cur.execute("INSERT INTO jobcard VALUES (1, 'kept'), (2, 'kept')")
    yield conn
    conn.close()


def _notes(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id, note FROM jobcard ORDER BY id")
        return cur.fetchall()


def test_backup_then_restore_recovers_lost_rows(local_db):
    from core import backups

    backup = backups.create_backup()
    with local_db.cursor() as cur:
        cur.execute("DELETE FROM jobcard WHERE id = 2")
        cur.execute("UPDATE jobcard SET note = 'damaged'")

    safety = backups.restore_backup(backup)
    assert _notes(local_db) == [(1, "kept"), (2, "kept")]

    # The pre-restore backup undoes a restore of the wrong file.
    backups.restore_backup(safety)
    assert _notes(local_db) == [(1, "damaged")]


def test_prune_keeps_the_newest_and_all_pre_restore_backups(local_db, tmp_path):
    from core import backups

    directory = backups.backup_dir()
    for stamp in ("20260101-000000", "20260102-000000", "20260103-000000"):
        (directory / f"db-{stamp}.pgdump").write_bytes(b"x")
    (directory / "db-20251231-000000-pre-restore.pgdump").write_bytes(b"x")

    removed = backups.prune(keep=2)
    assert [p.name for p in removed] == ["db-20260101-000000.pgdump"]
    assert (directory / "db-20251231-000000-pre-restore.pgdump").exists()
