"""Schema-drift guard: durable, deduplicated, deterministic new-vs-existing."""


def _rows(pool):
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute("SELECT table_name, missing_cols, hit_count FROM sync_schema_drift ORDER BY id")
        rows = cur.fetchall()
    pool.putconn(conn)
    return rows


def test_first_drift_recorded(agent, pool):
    assert agent.ensure_schema_drift_table() is True
    assert agent.get_schema_drift_count() == 0
    agent.record_schema_drift("public.jobcard_jobcard", ["fixed_by", "previous_status"], row_id=7)
    assert agent.get_schema_drift_count() == 1


def test_dedup_is_order_independent_and_bumps_hits(agent, pool):
    agent.record_schema_drift("public.jobcard_jobcard", ["fixed_by", "previous_status"])
    agent.record_schema_drift("public.jobcard_jobcard", ["previous_status", "fixed_by"])  # shuffled
    rows = _rows(pool)
    assert len(rows) == 1
    assert rows[0][1] == "fixed_by,previous_status"  # sorted key
    assert rows[0][2] == 2                            # hit_count bumped


def test_distinct_column_sets_are_separate_rows(agent, pool):
    agent.record_schema_drift("public.jobcard_jobcard", ["fixed_by"])
    agent.record_schema_drift("public.ppms_ppmschedule", ["new_col"])
    assert agent.get_schema_drift_count() == 2
    assert len(_rows(pool)) == 2


def test_empty_missing_cols_is_noop(agent, pool):
    agent.ensure_schema_drift_table()
    assert agent.record_schema_drift("public.jobcard_jobcard", []) is True
    assert agent.get_schema_drift_count() == 0
