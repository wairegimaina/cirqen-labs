"""Missing FK parents are fetched from HQ through the API, not its database."""
import uuid
from unittest import mock

import pytest

from sync.sync_agent_5 import ParentRecoveryMixin


class Agent(ParentRecoveryMixin):
    def __init__(self, pool):
        self.pool = pool
        self.api_url = "https://hq.example/api/sync"
        self.client_id = "ward-7"
        self.drift = []

    def _http_headers(self):
        return {"X-API-Key": "k"}

    def get_table_schema_info(self, table):
        return {"columns": {"id", "name", "meta", "photo"}}

    def record_schema_drift(self, table, columns, row_id=None):
        self.drift.append((table, columns))


@pytest.fixture
def parents(pool):
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS public.recovery_parent")
        cur.execute("CREATE TABLE public.recovery_parent (id uuid PRIMARY KEY, name text, meta jsonb, photo bytea)")
    conn.commit()
    pool.putconn(conn)
    yield
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute("DROP TABLE public.recovery_parent")
    conn.commit()
    pool.putconn(conn)


def _response(status, rows):
    response = mock.Mock(status_code=status, text="")
    response.json.return_value = {"status": "ok", "rows": rows}
    return response


def test_parent_is_fetched_by_id_and_inserted(pool, parents):
    parent_id = str(uuid.uuid4())
    row = {"id": parent_id, "name": "Ventilator", "meta": {"a": 1}, "photo": "__b64__:AQI=",
           "source_updated_at": "2026-01-01T00:00:00Z"}
    agent = Agent(pool)
    with mock.patch("sync.sync_agent_5.requests.post", return_value=_response(200, [row])) as post:
        assert agent.auto_recover_missing_parents_from_hq(
            "public.child", "c1", "parent_id", "recovery_parent", parent_id)
    assert post.call_args.args[0] == "https://hq.example/api/sync/data_checker/fetch_rows"
    assert post.call_args.kwargs["json"]["table"] == "public.recovery_parent"
    assert post.call_args.kwargs["json"]["ids"] == [parent_id]
    assert agent.drift == [("public.recovery_parent", ["source_updated_at"])]

    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute("SELECT name, meta, photo FROM public.recovery_parent WHERE id = %s", (parent_id,))
        name, meta, photo = cur.fetchone()
    pool.putconn(conn)
    assert (name, meta, bytes(photo)) == ("Ventilator", {"a": 1}, b"\x01\x02")


@pytest.mark.parametrize("status,rows", [(200, []), (503, [])])
def test_missing_or_unavailable_parent_is_not_recovered(pool, parents, status, rows):
    with mock.patch("sync.sync_agent_5.requests.post", return_value=_response(status, rows)):
        assert not Agent(pool).auto_recover_missing_parents_from_hq(
            "public.child", "c1", "parent_id", "recovery_parent", str(uuid.uuid4()))
