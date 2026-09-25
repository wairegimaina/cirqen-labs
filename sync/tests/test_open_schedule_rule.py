"""One open schedule per equipment, as the download path meets it.

HQ keeps at most one open schedule per equipment, so when its open row
arrives and the local database already has a different open one, the local
one is retired and HQ's applied. The completed history under the same
equipment_id must never be touched: the generic unique-conflict resolver
would have picked "the row with this equipment_id" and could have deleted a
completed schedule.
"""
import psycopg2.extras
import pytest

from sync.open_schedule_rule import constraint_name, is_one_open_violation, keeper_key

TABLE = "public.calSchedules_calibrationschedule"


@pytest.fixture()
def schedule_table(pool):
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE public."calSchedules_calibrationschedule" (
                id uuid PRIMARY KEY,
                equipment_id uuid NOT NULL,
                scheduled_month date NOT NULL,
                status text NOT NULL,
                active_status boolean NOT NULL DEFAULT true,
                pending_delete boolean NOT NULL DEFAULT false,
                updated_at timestamptz,
                UNIQUE (equipment_id, scheduled_month)
            )
        """)
        cur.execute("""
            CREATE UNIQUE INDEX calschedules_one_open_schedule
            ON public."calSchedules_calibrationschedule" (equipment_id)
            WHERE active_status AND NOT pending_delete AND status <> 'completed'
        """)
    conn.commit()
    pool.putconn(conn)


EQ = "11111111-1111-1111-1111-111111111111"
DONE = "aaaaaaaa-0000-0000-0000-000000000001"
LOCAL_OPEN = "aaaaaaaa-0000-0000-0000-000000000002"
HQ_OPEN = "bbbbbbbb-0000-0000-0000-000000000003"


def _insert(pool, id_, month, status, updated_at="2026-09-01T00:00:00+00:00"):
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute(
            'INSERT INTO public."calSchedules_calibrationschedule" '
            "(id, equipment_id, scheduled_month, status, updated_at) VALUES (%s,%s,%s,%s,%s)",
            (id_, EQ, month, status, updated_at),
        )
    conn.commit()
    pool.putconn(conn)


def _rows(pool):
    conn = pool.getconn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute('SELECT id::text, status, active_status FROM public."calSchedules_calibrationschedule"')
        rows = {r["id"]: r for r in cur.fetchall()}
    pool.putconn(conn)
    return rows


def test_hq_open_schedule_replaces_the_local_one_and_history_survives(apply_agent, schedule_table, pool):
    apply_agent.tables = [TABLE]
    _insert(pool, DONE, "2026-03-01", "completed")
    _insert(pool, LOCAL_OPEN, "2027-02-01", "pending")

    ok = apply_agent.apply_remote_update_locally(TABLE, {
        "row_id": HQ_OPEN, "operation": "u", "source": "hq",
        "last_modified": "2026-09-20T00:00:00+00:00",
        "data": {"id": HQ_OPEN, "equipment_id": EQ, "scheduled_month": "2027-03-01",
                 "status": "pending", "active_status": True, "pending_delete": False,
                 "updated_at": "2026-09-20T00:00:00+00:00"},
    })

    assert ok is True
    rows = _rows(pool)
    assert rows[HQ_OPEN]["active_status"] is True
    assert rows[LOCAL_OPEN]["active_status"] is False      # retired, not deleted
    assert rows[DONE]["status"] == "completed"               # history untouched
    assert rows[DONE]["active_status"] is True


def test_constraint_name_is_read_from_the_error():
    class Diag:
        constraint_name = "ppms_one_open_schedule"

    class Err(Exception):
        diag = Diag()

    assert is_one_open_violation(Err())
    assert constraint_name(Exception(
        'duplicate key value violates unique constraint "calschedules_one_open_schedule"')) \
        == "calschedules_one_open_schedule"
    assert not is_one_open_violation(Exception(
        'duplicate key value violates unique constraint "x_equipment_id_scheduled_month_key"'))


def test_keeper_prefers_work_under_way_then_the_earliest():
    rows = [("pending", "2027-01-01", "b"), ("in_progress", "2027-06-01", "c"), ("pending", "2026-12-01", "a")]
    assert min(rows, key=lambda r: keeper_key(*r))[2] == "c"
    rows = rows[::2]
    assert min(rows, key=lambda r: keeper_key(*r))[2] == "a"
