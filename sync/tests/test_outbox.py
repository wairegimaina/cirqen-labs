"""Outbox CDC: trigger capture, hard deletes, monotonic seq, checkpoint discipline."""


def _install(agent, jobcard_table):
    assert agent.install_outbox() is True


def _exec(pool, sql, args=None):
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute(sql, args or ())
    conn.commit()
    pool.putconn(conn)


def test_insert_update_delete_captured_in_order(agent, jobcard_table, pool):
    _install(agent, jobcard_table)
    _exec(pool, "INSERT INTO public.jobcard_jobcard VALUES (1,'open', now())")
    _exec(pool, "UPDATE public.jobcard_jobcard SET status='closed' WHERE id=1")
    _exec(pool, "DELETE FROM public.jobcard_jobcard WHERE id=1")

    events, max_seq = agent.fetch_outbox_batch(0, 100)
    ops = [(e["_outbox_seq"], e["operation"], e["data"].get("status")) for e in events]
    assert ops == [(1, "u", "open"), (2, "u", "closed"), (3, "d", "closed")]
    assert max_seq == 3


def test_hard_delete_is_captured(agent, jobcard_table, pool):
    """The defect the timestamp poller could NOT detect."""
    _install(agent, jobcard_table)
    _exec(pool, "INSERT INTO public.jobcard_jobcard VALUES (7,'x', now())")
    _exec(pool, "DELETE FROM public.jobcard_jobcard WHERE id=7")

    events, _ = agent.fetch_outbox_batch(0, 100)
    deletes = [e for e in events if e["operation"] == "d"]
    assert len(deletes) == 1
    assert deletes[0]["row_id"] == "7"
    assert deletes[0]["data"]["id"] == 7  # OLD row preserved in payload


def test_seq_is_monotonic_and_unique(agent, jobcard_table, pool):
    _install(agent, jobcard_table)
    for i in range(5):
        _exec(pool, "INSERT INTO public.jobcard_jobcard VALUES (%s,'s',now())", (i,))
    events, _ = agent.fetch_outbox_batch(0, 100)
    seqs = [e["_outbox_seq"] for e in events]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))


def test_batching_and_checkpoint_advances(agent, jobcard_table, pool):
    _install(agent, jobcard_table)
    for i in range(5):
        _exec(pool, "INSERT INTO public.jobcard_jobcard VALUES (%s,'s',now())", (i,))

    drained = []
    while True:
        last = agent.get_last_outbox_seq()
        events, max_seq = agent.fetch_outbox_batch(last, 2)  # batch size 2
        if not events:
            break
        assert len(events) <= 2
        drained += events
        agent.set_last_outbox_seq(max_seq)
        if len(events) < 2:
            break
    assert len(drained) == 5
    assert agent.get_last_outbox_seq() == 5
    assert agent.outbox_pending_count() == 0


def test_failed_batch_does_not_advance_checkpoint(agent, jobcard_table, pool):
    """Simulate the loop's discipline: on failure we must NOT advance."""
    _install(agent, jobcard_table)
    _exec(pool, "INSERT INTO public.jobcard_jobcard VALUES (1,'a',now())")
    start = agent.get_last_outbox_seq()

    last = agent.get_last_outbox_seq()
    events, max_seq = agent.fetch_outbox_batch(last, 50)
    assert events
    upload_ok = False  # pretend HQ upload failed
    if upload_ok:
        agent.set_last_outbox_seq(max_seq)
    # checkpoint unchanged => same rows re-fetched next cycle
    assert agent.get_last_outbox_seq() == start
    again, _ = agent.fetch_outbox_batch(agent.get_last_outbox_seq(), 50)
    assert len(again) == len(events)


def test_prune_removes_acknowledged_rows(agent, jobcard_table, pool):
    _install(agent, jobcard_table)
    for i in range(3):
        _exec(pool, "INSERT INTO public.jobcard_jobcard VALUES (%s,'s',now())", (i,))
    _, max_seq = agent.fetch_outbox_batch(0, 100)
    agent.set_last_outbox_seq(max_seq)
    removed = agent.prune_outbox(max_seq)
    assert removed == 3
    assert agent.outbox_pending_count() == 0


def test_install_skips_missing_tables(agent, jobcard_table):
    agent.tables = ["public.jobcard_jobcard", "public.not_a_real_table"]
    assert agent.install_outbox() is True  # missing table skipped, not fatal


def test_idempotency_key_is_stable_across_refetch(agent, jobcard_table, pool):
    _install(agent, jobcard_table)
    _exec(pool, "INSERT INTO public.jobcard_jobcard VALUES (1,'a',now())")
    first, _ = agent.fetch_outbox_batch(0, 100)
    second, _ = agent.fetch_outbox_batch(0, 100)  # re-fetch same rows (a retry)
    # Both are deterministic: idempotency_key is client:seq, and event_id is
    # derived from the row version so HQ's audit_log dedupes a re-send.
    assert first[0]["idempotency_key"] == f"{agent.client_id}:1"
    assert first[0]["idempotency_key"] == second[0]["idempotency_key"]
    assert first[0]["event_id"] == second[0]["event_id"]


def test_throttle_delay_honors_retry_after_and_backs_off():
    from sync.outbox import OutboxMixin as O
    # explicit Retry-After wins
    assert O._throttle_delay("throttled:429:15", 1.0) == 15.0
    assert O._throttle_delay("throttled:503:120", 1.0) == 60.0  # capped
    # no Retry-After -> exponential from 0 then doubling, capped at 60
    assert O._throttle_delay("throttled:503:", 0.0) == 1.0
    assert O._throttle_delay("throttled:503:", 1.0) == 2.0
    assert O._throttle_delay("throttled:503:", 40.0) == 60.0
