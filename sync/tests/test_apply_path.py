"""
Characterization tests for the LIVE download/apply path
(sync_agent_6.apply_remote_update_locally) — the real code that runs on every
client, now carrying the Phase-1/2 conflict-quarantine, deterministic-resolver,
and schema-drift integrations.

These exercise the actual method, not a reimplementation, so they lock in
current behaviour and catch regressions from the pending mixin-collapse refactor.
"""
import psycopg2.extras


def _seed(pool, id_, status, updated_at):
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.jobcard_jobcard (id, status, updated_at) VALUES (%s,%s,%s)",
            (id_, status, updated_at),
        )
    conn.commit()
    pool.putconn(conn)


def _row(pool, id_):
    conn = pool.getconn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM public.jobcard_jobcard WHERE id=%s", (id_,))
        r = cur.fetchone()
    pool.putconn(conn)
    return r


def _payload(row_id, data, last_modified, op="u"):
    return {"row_id": row_id, "data": data, "operation": op,
            "last_modified": last_modified, "source": "hq"}


def test_remote_newer_is_applied(apply_agent, jobcard_table, pool):
    _seed(pool, 1, "open", "2026-07-21T09:00:00+00:00")
    ok = apply_agent.apply_remote_update_locally(
        "public.jobcard_jobcard",
        _payload(1, {"id": 1, "status": "closed", "updated_at": "2026-07-21T10:00:00+00:00"},
                 "2026-07-21T10:00:00+00:00"),
    )
    assert ok is True
    assert _row(pool, 1)["status"] == "closed"       # remote won


def test_local_newer_remote_quarantined(apply_agent, jobcard_table, pool):
    _seed(pool, 2, "local-edit", "2026-07-21T12:00:00+00:00")
    ok = apply_agent.apply_remote_update_locally(
        "public.jobcard_jobcard",
        _payload(2, {"id": 2, "status": "stale-remote", "updated_at": "2026-07-21T10:00:00+00:00"},
                 "2026-07-21T10:00:00+00:00"),
    )
    assert ok is True
    assert _row(pool, 2)["status"] == "local-edit"   # local kept
    # the discarded remote change must be quarantined, not silently dropped
    assert apply_agent.get_conflict_count() == 1


def test_unknown_column_records_schema_drift_but_still_applies(apply_agent, jobcard_table, pool):
    _seed(pool, 3, "open", "2026-07-21T09:00:00+00:00")
    ok = apply_agent.apply_remote_update_locally(
        "public.jobcard_jobcard",
        _payload(3, {"id": 3, "status": "closed", "hq_only_col": "x",
                     "updated_at": "2026-07-21T10:00:00+00:00"},
                 "2026-07-21T10:00:00+00:00"),
    )
    assert ok is True
    assert _row(pool, 3)["status"] == "closed"       # known columns applied
    assert apply_agent.get_schema_drift_count() >= 1  # drift recorded, not silent


def test_insert_of_new_row(apply_agent, jobcard_table, pool):
    ok = apply_agent.apply_remote_update_locally(
        "public.jobcard_jobcard",
        _payload(99, {"id": 99, "status": "new", "updated_at": "2026-07-21T10:00:00+00:00"},
                 "2026-07-21T10:00:00+00:00"),
    )
    assert ok is True
    assert _row(pool, 99)["status"] == "new"


def test_delete_removes_row(apply_agent, jobcard_table, pool):
    _seed(pool, 5, "doomed", "2026-07-21T09:00:00+00:00")
    ok = apply_agent.apply_remote_update_locally(
        "public.jobcard_jobcard",
        _payload(5, {"id": 5}, "2026-07-21T10:00:00+00:00", op="d"),
    )
    assert ok is True
    assert _row(pool, 5) is None                       # hard-deleted


def test_delete_of_absent_row_is_noop(apply_agent, jobcard_table, pool):
    ok = apply_agent.apply_remote_update_locally(
        "public.jobcard_jobcard",
        _payload(12345, {"id": 12345}, "2026-07-21T10:00:00+00:00", op="d"),
    )
    assert ok is True                                  # idempotent
