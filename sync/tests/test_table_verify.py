"""
verify_tables_have_updated_at: keeps tables with updated_at, drops those without,
and now also flags tables missing created_at (a mirror-recovery requirement).
"""


def _mk(pool, sql):
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()
    pool.putconn(conn)


def test_verification_filters_and_flags(apply_agent, pool, caplog):
    _mk(pool, "CREATE TABLE public.good (id bigint PRIMARY KEY, created_at timestamptz, updated_at timestamptz)")
    _mk(pool, "CREATE TABLE public.no_created (id bigint PRIMARY KEY, updated_at timestamptz)")
    _mk(pool, "CREATE TABLE public.no_updated (id bigint PRIMARY KEY)")

    tables = ["public.good", "public.no_created", "public.no_updated"]
    apply_agent.tables = list(tables)
    apply_agent.config["tables"] = list(tables)

    apply_agent.verify_tables_have_updated_at()

    # tables WITHOUT updated_at are dropped from sync; the rest are kept
    assert apply_agent.tables == ["public.good", "public.no_created"]
    assert "public.no_updated" not in apply_agent.tables


def test_created_at_gap_is_surfaced(apply_agent, pool):
    import logging
    _mk(pool, "CREATE TABLE public.no_created (id bigint PRIMARY KEY, updated_at timestamptz)")
    apply_agent.tables = ["public.no_created"]
    apply_agent.config["tables"] = ["public.no_created"]

    # The sync loggers set propagate=False, so attach a capturing handler
    # directly to the logger the agent uses.
    records = []

    class _Cap(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    logger = logging.getLogger("sync_agent_optimized")
    handler = _Cap()
    logger.addHandler(handler)
    try:
        apply_agent.verify_tables_have_updated_at()
    finally:
        logger.removeHandler(handler)

    # kept for the poller, but the missing created_at (mirror requirement) is warned
    assert apply_agent.tables == ["public.no_created"]
    assert any("created_at" in m for m in records)
