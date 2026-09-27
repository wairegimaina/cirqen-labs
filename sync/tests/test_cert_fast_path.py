"""Certificate numbers reach the approving site in seconds, not a minute.

The approval view NOTIFYs; the agent's certificate loop wakes on it, waits for
the session row to be uploaded, asks HQ, and applies the number HQ returns in
the same response.
"""
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest import mock

import psycopg2
import pytest

from sync.sync_agent_7 import CERT_CHANNEL
from sync.tests.conftest import FakeResponse

SESSIONS = "public.CalSoft_calibrationsession"


@pytest.fixture
def cert_agent(net_agent):
    net_agent.stop_event = threading.Event()
    yield net_agent
    net_agent.stop_event.set()
    net_agent._close_certificate_listener()


@pytest.fixture
def cert_tables(pool):
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute('''CREATE TABLE "calSchedules_calibrationschedule" (
            id uuid PRIMARY KEY, status text, completed_date date, updated_at timestamptz)''')
        cur.execute('''CREATE TABLE "CalSoft_calibrationsession" (
            id uuid PRIMARY KEY, device_serial text, device_model text, status text,
            certificate_number text, schedule_id uuid, updated_at timestamptz)''')
        cur.execute('''CREATE TABLE pending_certificates (
            id uuid PRIMARY KEY, session_id uuid, machine_id text, retry_count int DEFAULT 0,
            sync_status text, pending_delete boolean DEFAULT false, error_message text,
            last_attempt timestamptz, processed_at timestamptz, updated_at timestamptz)''')
    conn.commit()
    pool.putconn(conn)


def _approve(pool, updated_at):
    session_id, pending_id, schedule_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute('INSERT INTO "calSchedules_calibrationschedule" VALUES (%s, %s, NULL, %s)',
                    (str(schedule_id), "pushed", updated_at))
        cur.execute('INSERT INTO "CalSoft_calibrationsession" VALUES (%s, %s, %s, %s, NULL, %s, %s)',
                    (str(session_id), "SN-1", "M-1", "approved_pending_certificate", str(schedule_id), updated_at))
        cur.execute("INSERT INTO pending_certificates (id, session_id, machine_id, sync_status, updated_at) "
                    "VALUES (%s, %s, %s, 'pending', %s)", (str(pending_id), str(session_id), "M-TEST", updated_at))
    conn.commit()
    pool.putconn(conn)
    return str(session_id), str(pending_id)


def test_an_approval_notify_wakes_the_loop(cert_agent, pg_dsn):
    def notify():
        time.sleep(0.3)
        conn = psycopg2.connect(**pg_dsn)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("SELECT pg_notify(%s, %s)", (CERT_CHANNEL, "session"))
        conn.close()

    assert cert_agent.wait_for_certificate_signal(0.2) is False  # opens LISTEN, nothing yet
    threading.Thread(target=notify).start()
    started = time.monotonic()
    assert cert_agent.wait_for_certificate_signal(5) is True
    assert time.monotonic() - started < 2


def test_a_notify_inside_a_rolled_back_transaction_is_not_delivered(cert_agent, pg_dsn):
    cert_agent.wait_for_certificate_signal(0.1)
    conn = psycopg2.connect(**pg_dsn)
    with conn.cursor() as cur:
        cur.execute("SELECT pg_notify(%s, %s)", (CERT_CHANNEL, "session"))
    conn.rollback()
    conn.close()
    assert cert_agent.wait_for_certificate_signal(0.5) is False


def test_waits_for_the_session_upload(cert_agent, pool, cert_tables):
    approved_at = datetime.now(timezone.utc)
    _approve(pool, approved_at)
    cert_agent.state.set(f"last_upload_time:{SESSIONS}", (approved_at - timedelta(seconds=5)).isoformat())

    def upload_loop_catches_up():
        time.sleep(0.4)
        cert_agent.state.set(f"last_upload_time:{SESSIONS}", approved_at.isoformat())

    threading.Thread(target=upload_loop_catches_up).start()
    assert cert_agent.wait_until_sessions_uploaded(timeout=5) is True
    assert cert_agent.wait_until_sessions_uploaded(timeout=0.1) is True  # already uploaded


def test_gives_up_waiting_rather_than_blocking(cert_agent, pool, cert_tables):
    approved_at = datetime.now(timezone.utc)
    _approve(pool, approved_at)
    cert_agent.state.set(f"last_upload_time:{SESSIONS}", (approved_at - timedelta(seconds=5)).isoformat())
    assert cert_agent.wait_until_sessions_uploaded(timeout=0.5) is False


def test_number_in_hq_response_is_applied_immediately(cert_agent, pool, cert_tables):
    session_id, pending_id = _approve(pool, datetime.now(timezone.utc))
    reply = FakeResponse(200, {"status": "success", "certificates_generated": 1, "generated": [
        {"session_id": session_id, "certificate_number": "BNH-0412", "pending_cert_id": pending_id,
         "updated_at": datetime.now(timezone.utc).isoformat()}]})
    with mock.patch.object(cert_agent, "check_hq_online", return_value=True), \
         mock.patch("sync.sync_agent_7.requests.post", return_value=reply) as post:
        cert_agent.sync_pending_certificates()

    assert post.call_args.args[0] == "http://hq.test/api/sync/generate_certificates"
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute('SELECT certificate_number, status FROM "CalSoft_calibrationsession"')
        assert cur.fetchone() == ("BNH-0412", "approved")
        cur.execute("SELECT sync_status FROM pending_certificates")
        assert cur.fetchone() == ("completed",)
        cur.execute('SELECT status FROM "calSchedules_calibrationschedule"')
        assert cur.fetchone() == ("completed",)
    pool.putconn(conn)
    assert cert_agent._download_saw_changes is True
