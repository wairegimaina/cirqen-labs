"""Conflict quarantine: LWW losers are persisted, not silently dropped."""


def _fetch_one(pool):
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name, row_id, winner, resolution, remote_data->>'note', source, reviewed
            FROM sync_conflicts ORDER BY id DESC LIMIT 1
        """)
        row = cur.fetchone()
    pool.putconn(conn)
    return row


def test_ensure_table_and_zero_count(agent):
    assert agent.ensure_conflict_table() is True
    assert agent.get_conflict_count() == 0


def test_record_conflict_persists_loser_payload(agent, pool):
    ok = agent.record_conflict(
        table="public.jobcard_jobcard", row_id=999, operation="u",
        winner="local", resolution="local_newer_remote_discarded",
        local_updated_at="2026-07-21T10:00:00+00:00",
        remote_updated_at="2026-07-21T09:00:00+00:00",
        remote_data={"id": 999, "note": "discarded-remote"}, source="unit-test",
    )
    assert ok is True
    assert agent.get_conflict_count(unreviewed_only=True) == 1
    row = _fetch_one(pool)
    assert row[:6] == ("public.jobcard_jobcard", "999", "local",
                       "local_newer_remote_discarded", "discarded-remote", "unit-test")
    assert row[6] is False  # unreviewed


def test_unreviewed_filter(agent, pool):
    agent.record_conflict(
        table="t", row_id=1, operation="u", winner="local",
        resolution="r", remote_data={"a": 1},
    )
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute("UPDATE sync_conflicts SET reviewed = TRUE")
    conn.commit()
    pool.putconn(conn)
    assert agent.get_conflict_count(unreviewed_only=True) == 0
    assert agent.get_conflict_count(unreviewed_only=False) == 1
