"""
Recovery-path tests for the DatabaseMirror (sync/mirror.py).

The mirror reconciles the local DB against HQ by id+timestamp — the path that
rebuilds a new/wiped client from HQ. Exercised here with a REAL second database
("hqdb") created in the same embedded cluster, so the HQ→Local fill runs against
two live Postgres databases (no network, no mocks of the DB layer).
"""
import psycopg2
import psycopg2.extras
import pytest

from sync.mirror import DatabaseMirror, SyncDirection

TABLE = "public.jobcard_jobcard"
DDL = """
CREATE TABLE IF NOT EXISTS public.jobcard_jobcard (
    id bigint PRIMARY KEY,
    status text,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz
)
"""


def _conn(cfg):
    return psycopg2.connect(**cfg)


def _cfg(pg_dsn, dbname):
    return {"host": pg_dsn["host"], "port": pg_dsn["port"],
            "dbname": dbname, "user": pg_dsn["user"], "password": ""}


@pytest.fixture()
def two_db(pg_dsn, pool):
    """
    Yields (hq_config, local_config). Local is the cluster's default DB (already
    cleaned by the `pool` fixture); HQ is a fresh 'hqdb'. Both get the table.
    """
    # create hqdb (CREATE DATABASE cannot run in a transaction)
    admin = _conn(_cfg(pg_dsn, pg_dsn["dbname"]))
    admin.autocommit = True
    with admin.cursor() as cur:
        cur.execute("DROP DATABASE IF EXISTS hqdb")
        cur.execute("CREATE DATABASE hqdb")
    admin.close()

    hq_cfg = _cfg(pg_dsn, "hqdb")
    local_cfg = _cfg(pg_dsn, pg_dsn["dbname"])

    for cfg in (hq_cfg, local_cfg):
        c = _conn(cfg)
        with c.cursor() as cur:
            cur.execute(DDL)
        c.commit()
        c.close()

    yield hq_cfg, local_cfg

    admin = _conn(_cfg(pg_dsn, pg_dsn["dbname"]))
    admin.autocommit = True
    with admin.cursor() as cur:
        # terminate stray backends then drop
        cur.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname='hqdb' AND pid <> pg_backend_pid()")
        cur.execute("DROP DATABASE IF EXISTS hqdb")
    admin.close()


def _insert(cfg, id_, status, ts):
    c = _conn(cfg)
    with c.cursor() as cur:
        cur.execute("INSERT INTO public.jobcard_jobcard (id,status,updated_at) VALUES (%s,%s,%s)",
                    (id_, status, ts))
    c.commit()
    c.close()


def _ids(cfg):
    c = _conn(cfg)
    with c.cursor() as cur:
        cur.execute("SELECT id FROM public.jobcard_jobcard ORDER BY id")
        rows = [r[0] for r in cur.fetchall()]
    c.close()
    return rows


def _mirror(hq_cfg, local_cfg):
    return DatabaseMirror(hq_config=hq_cfg, local_config=local_cfg,
                          api_url="http://hq.test/api/sync", auth_token="T",
                          client_id="CLIENT-TEST", machine_id="M-TEST")


def test_compare_table_contents_reports_differences(two_db):
    hq_cfg, local_cfg = two_db
    _insert(hq_cfg, 1, "a", "2026-07-21T10:00:00+00:00")   # HQ only
    _insert(hq_cfg, 2, "b", "2026-07-21T10:00:00+00:00")   # common
    _insert(local_cfg, 2, "b", "2026-07-21T10:00:00+00:00")
    _insert(local_cfg, 3, "c", "2026-07-21T10:00:00+00:00")  # local only

    m = _mirror(hq_cfg, local_cfg)
    assert m.connect()
    try:
        missing_in_hq, missing_in_local, common = m.compare_table_contents(TABLE)
        assert missing_in_local == {"1"}
        assert missing_in_hq == {"3"}
        assert common == {"2"}
    finally:
        m.disconnect()


def test_mirror_hq_to_local_fills_missing_rows(two_db):
    """The core recovery scenario: a client missing rows pulls them from HQ."""
    hq_cfg, local_cfg = two_db
    _insert(hq_cfg, 1, "from-hq-1", "2026-07-21T10:00:00+00:00")
    _insert(hq_cfg, 2, "from-hq-2", "2026-07-21T10:00:00+00:00")
    assert _ids(local_cfg) == []   # local starts empty

    m = _mirror(hq_cfg, local_cfg)
    assert m.connect()
    try:
        stats = m.mirror_table(TABLE, direction=SyncDirection.HQ_TO_LOCAL)
    finally:
        m.disconnect()

    assert _ids(local_cfg) == [1, 2]           # rows pulled into local
    assert stats.synced_to_local >= 2
