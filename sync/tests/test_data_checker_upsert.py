"""First-install bootstrap must tolerate columns HQ has and this client lacks.

HQ's LWW migration added ``source_updated_at`` to every HQ table but not to
clients. Before the fix, every bootstrapped row failed with UndefinedColumn
(logged only at DEBUG), so a fresh install booted with an empty database.
"""
from sync.data_checker_client import DataCheckerClient


def _client(pool):
    return DataCheckerClient(
        hq_url="http://hq.invalid", api_key="k", client_id="c",
        local_pool=pool, allowed_tables=["public.jobcard_jobcard"],
    )


def test_upsert_skips_hq_only_columns(pool, jobcard_table):
    rows = [
        {"id": 1, "status": "open", "updated_at": "2026-09-23T10:00:00+00:00",
         "source_updated_at": "2026-09-23T10:00:00+00:00"},
        {"id": 2, "status": "closed", "updated_at": "2026-09-23T11:00:00+00:00",
         "source_updated_at": None},
    ]
    ok, failed = _client(pool)._upsert_rows("public.jobcard_jobcard", rows)
    assert (ok, failed) == (2, 0)

    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute("SELECT id, status FROM public.jobcard_jobcard ORDER BY id")
        assert cur.fetchall() == [(1, "open"), (2, "closed")]
    pool.putconn(conn)
