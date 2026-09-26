"""Certificate-number clash repair, talking to HQ through the API only."""
import uuid
from unittest import mock

import psycopg2.extras
import pytest

from sync.cert_conflict_guard import CertConflictGuardMixin


class Agent(CertConflictGuardMixin):
    def __init__(self, pool):
        self.pool = pool
        self.api_url = "https://hq.example/api/sync"
        self.client_id = "ward-7"

    def _http_headers(self):
        return {"X-API-Key": "k"}


@pytest.fixture
def sessions(pool):
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute('DROP TABLE IF EXISTS public."CalSoft_calibrationauditlog"')
        cur.execute('DROP TABLE IF EXISTS public."CalSoft_calibrationsession"')
        cur.execute('CREATE TABLE public."CalSoft_calibrationsession" ('
                    'id uuid PRIMARY KEY, certificate_number text UNIQUE, notes text, updated_at timestamptz)')
        cur.execute('CREATE TABLE public."CalSoft_calibrationauditlog" (id uuid, action text, description text, '
                    'timestamp timestamptz, session_id uuid, active_status boolean)')
    conn.commit()
    pool.putconn(conn)
    yield
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute('DROP TABLE public."CalSoft_calibrationauditlog"')
        cur.execute('DROP TABLE public."CalSoft_calibrationsession"')
    conn.commit()
    pool.putconn(conn)


def _add(pool, row_id, number):
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute('INSERT INTO public."CalSoft_calibrationsession" (id, certificate_number) VALUES (%s, %s)',
                    (row_id, number))
    conn.commit()
    pool.putconn(conn)


def _rows(pool):
    conn = pool.getconn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute('SELECT id::text AS id, certificate_number, notes FROM public."CalSoft_calibrationsession"')
        rows = {r["id"]: r for r in cur.fetchall()}
        cur.execute('SELECT action FROM public."CalSoft_calibrationauditlog"')
        audit = [r["action"] for r in cur.fetchall()]
    pool.putconn(conn)
    return rows, audit


def _hq(conflicts, max_sequence):
    response = mock.Mock()
    response.json.return_value = {"status": "ok", "conflicts": conflicts, "max_sequence": max_sequence}
    response.raise_for_status.return_value = None
    return response


def test_orphan_is_reissued_above_hq_and_local_numbers(pool, sessions):
    orphan, hq_owner = str(uuid.uuid4()), str(uuid.uuid4())
    _add(pool, orphan, "BNH-0093")
    _add(pool, str(uuid.uuid4()), "BNH-0120")  # highest locally
    with mock.patch("sync.cert_conflict_guard.requests.post",
                    return_value=_hq([{"certificate_number": "BNH-0093", "hq_id": hq_owner}], 150)) as post:
        assert Agent(pool).resolve_certificate_conflicts() == 1

    sent = post.call_args.kwargs["json"]["certificates"]
    assert {"id": orphan, "certificate_number": "BNH-0093"} in sent
    assert post.call_args.args[0] == "https://hq.example/api/sync/certificates/conflicts"
    rows, audit = _rows(pool)
    assert rows[orphan]["certificate_number"] == "BNH-0151"
    assert "BNH-0093" in rows[orphan]["notes"]
    assert audit == ["certificate_reissued"]


def test_no_conflicts_changes_nothing(pool, sessions):
    row = str(uuid.uuid4())
    _add(pool, row, "BNH-0001")
    with mock.patch("sync.cert_conflict_guard.requests.post", return_value=_hq([], 1)):
        assert Agent(pool).resolve_certificate_conflicts() == 0
    assert _rows(pool)[0][row]["certificate_number"] == "BNH-0001"


def test_hq_error_is_not_treated_as_no_conflicts(pool, sessions):
    _add(pool, str(uuid.uuid4()), "BNH-0001")
    failing = mock.Mock()
    failing.raise_for_status.side_effect = RuntimeError("503")
    with mock.patch("sync.cert_conflict_guard.requests.post", return_value=failing):
        assert Agent(pool).resolve_certificate_conflicts() == 0  # logged, nothing changed
