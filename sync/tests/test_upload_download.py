"""
Mock-HQ tests for the upload/download HTTP orchestration
(sync_agent_3.upload_batch, sync_agent_4.download_updates).

HQ is stubbed by monkeypatching the module-level `requests`, so these exercise
the real request-building, checkpoint, idempotency, backpressure, and
apply-on-download logic without a network or server.
"""
import psycopg2.extras
import sync.sync_agent_3 as m3
import sync.sync_agent_4 as m4
from sync.tests.conftest import FakeResponse


# ── helpers ──────────────────────────────────────────────────────────────────

def _events():
    return [
        {"event_id": "e1", "idempotency_key": "CLIENT-TEST:1", "table": "public.jobcard_jobcard",
         "row_id": "1", "operation": "u", "data": {"id": 1}, "created_at": "2026-07-21T10:00:00+00:00"},
        {"event_id": "e2", "idempotency_key": "CLIENT-TEST:2", "table": "public.jobcard_jobcard",
         "row_id": "2", "operation": "u", "data": {"id": 2}, "created_at": "2026-07-21T10:00:01+00:00"},
    ]


def _row(pool, id_):
    conn = pool.getconn()
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM public.jobcard_jobcard WHERE id=%s", (id_,))
        r = cur.fetchone()
    pool.putconn(conn)
    return r


# ── upload_batch ─────────────────────────────────────────────────────────────

def test_upload_success_sets_checkpoint_and_sends_idempotency(net_agent, monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        captured["headers"] = headers
        return FakeResponse(200, {"deferred": 0})

    monkeypatch.setattr(m3, "requests", type("R", (), {"post": staticmethod(fake_post),
                                                       "RequestException": Exception})())
    ok, err = net_agent.upload_batch(_events())

    assert ok is True and err is None
    assert captured["url"] == "http://hq.test/api/sync/upload"
    assert captured["body"]["idempotency_key"]                       # batch key present
    assert captured["headers"]["X-API-Key"] == "TESTTOKEN"           # auth sent
    assert net_agent.get_last_upload_time() == "2026-07-21T10:00:01+00:00"  # checkpoint = latest


def test_upload_batch_key_is_deterministic(net_agent, monkeypatch):
    seen = []

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.append(json["idempotency_key"])
        return FakeResponse(200, {})

    monkeypatch.setattr(m3, "requests", type("R", (), {"post": staticmethod(fake_post),
                                                       "RequestException": Exception})())
    net_agent.upload_batch(_events())
    net_agent.upload_batch(_events())   # identical batch retried
    assert seen[0] == seen[1]           # same key => HQ can dedupe


def test_upload_throttle_returns_backpressure_signal(net_agent, monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return FakeResponse(503, {}, headers={"Retry-After": "30"})

    monkeypatch.setattr(m3, "requests", type("R", (), {"post": staticmethod(fake_post),
                                                       "RequestException": Exception})())
    before = net_agent.get_last_upload_time()
    ok, err = net_agent.upload_batch(_events())
    assert ok is False
    assert err.startswith("throttled:503:30")           # carries Retry-After
    assert net_agent.get_last_upload_time() == before    # checkpoint NOT advanced


def test_upload_server_error_returns_failure(net_agent, monkeypatch):
    def fake_post(url, json=None, headers=None, timeout=None):
        return FakeResponse(500, {}, text="boom")

    monkeypatch.setattr(m3, "requests", type("R", (), {"post": staticmethod(fake_post),
                                                       "RequestException": Exception})())
    ok, err = net_agent.upload_batch(_events())
    assert ok is False and "500" in err


def test_upload_empty_is_noop(net_agent):
    ok, err = net_agent.upload_batch([])
    assert ok is True and err is None


# ── download_updates ─────────────────────────────────────────────────────────

def test_download_applies_updates_to_local_db(net_agent, jobcard_table, monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse(200, {"updates": [
            {"table": "public.jobcard_jobcard", "row_id": "1", "operation": "u",
             "data": {"id": 1, "status": "from-hq", "updated_at": "2026-07-21T10:00:00+00:00"},
             "last_modified": "2026-07-21T10:00:00+00:00", "source": "hq"},
        ]})

    monkeypatch.setattr(m4, "requests", type("R", (), {"get": staticmethod(fake_get)})())
    net_agent.download_updates()
    assert _row(net_agent.pool, 1)["status"] == "from-hq"   # applied to local DB


def test_download_delete_removes_local_row(net_agent, jobcard_table, monkeypatch):
    conn = net_agent.pool.getconn()
    with conn.cursor() as cur:
        cur.execute("INSERT INTO public.jobcard_jobcard VALUES (9,'x',now())")
    conn.commit()
    net_agent.pool.putconn(conn)

    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse(200, {"updates": [
            {"table": "public.jobcard_jobcard", "row_id": "9", "operation": "d",
             "data": {"id": 9}, "last_modified": "2026-07-21T11:00:00+00:00", "source": "hq"},
        ]})

    monkeypatch.setattr(m4, "requests", type("R", (), {"get": staticmethod(fake_get)})())
    net_agent.download_updates()
    assert _row(net_agent.pool, 9) is None


def test_download_empty_page_advances_to_hq_cursor(net_agent, jobcard_table, monkeypatch):
    net_agent.set_last_download_time("2026-07-21T09:00:00+00:00")

    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse(200, {"updates": [], "next_since": "2026-07-21T12:00:00+00:00"})

    monkeypatch.setattr(m4, "requests", type("R", (), {"get": staticmethod(fake_get)})())
    net_agent.download_updates()
    assert net_agent.get_last_download_time() == "2026-07-21T12:00:00+00:00"


def test_download_cursor_never_moves_backwards(net_agent, jobcard_table, monkeypatch):
    net_agent.set_last_download_time("2026-07-21T12:00:00+00:00")

    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse(200, {"updates": [], "next_since": "2026-07-21T09:00:00+00:00"})

    monkeypatch.setattr(m4, "requests", type("R", (), {"get": staticmethod(fake_get)})())
    net_agent.download_updates()
    assert net_agent.get_last_download_time() == "2026-07-21T12:00:00+00:00"


def test_download_cursor_holds_before_earliest_failure(net_agent, jobcard_table, monkeypatch):
    net_agent.set_last_download_time("2026-07-21T09:00:00+00:00")

    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse(200, {"next_since": "2026-07-21T10:10:00+00:00", "updates": [
            {"table": "public.jobcard_jobcard", "row_id": "1", "operation": "u",
             "data": {"id": 1, "status": "ok", "updated_at": "2026-07-21T10:00:00+00:00"},
             "last_modified": "2026-07-21T10:00:00+00:00", "cursor": "2026-07-21T10:00:00+00:00"},
            {"table": "public.jobcard_jobcard", "row_id": "2", "operation": "u",
             "data": {"id": 2, "status": "bad", "updated_at": "2026-07-21T10:05:00+00:00"},
             "last_modified": "2026-07-21T10:05:00+00:00", "cursor": "2026-07-21T10:05:00+00:00"},
        ]})

    real_apply = net_agent.apply_remote_update_locally

    def apply(table, payload):
        return False if payload["row_id"] == "2" else real_apply(table, payload)

    monkeypatch.setattr(net_agent, "apply_remote_update_locally", apply)
    monkeypatch.setattr(m4, "requests", type("R", (), {"get": staticmethod(fake_get)})())
    net_agent.download_updates()

    assert _row(net_agent.pool, 1)["status"] == "ok"
    assert net_agent.get_last_download_time() == "2026-07-21T10:04:59.999999+00:00"


def test_download_empty_is_noop(net_agent, jobcard_table, monkeypatch):
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse(200, {"updates": []})

    monkeypatch.setattr(m4, "requests", type("R", (), {"get": staticmethod(fake_get)})())
    net_agent.download_updates()   # should simply return without error
