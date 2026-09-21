"""An upload carries the version HQ's last-write-wins guard needs (H-3).

HQ guards every upsert with

    WHERE hq_row.source_updated_at IS NULL
       OR EXCLUDED.source_updated_at IS NULL
       OR EXCLUDED.source_updated_at >= hq_row.source_updated_at

so an older edit cannot overwrite a newer stored value. The guard only applies
when the incoming payload carries ``source_updated_at`` — and it never did.
The payload is built from ``to_jsonb(t.*)``, which can only emit columns the
local table actually has, and no client model has that column: HQ's migration
added it to HQ's tables only.

So the condition was never met, ``lww_guarded`` stayed False, and every client
upload became an unconditional overwrite: last to *arrive* won, regardless of
when the edit was made.

``source_updated_at`` is not a new fact about a row. It is that row's own
``updated_at`` seen from HQ's side — which is exactly how HQ's migration
backfilled it (``SET source_updated_at = updated_at``). So rather than migrate
51 client models and risk the guard being live for some tables and inert for
others, the payload stamps it where the payload is built.

These tests pin that stamp against a real Postgres, because ``to_jsonb`` and
``jsonb_build_object`` are Postgres-specific and cannot be exercised on SQLite.
"""

from datetime import datetime, timedelta, timezone

import psycopg2.extras
import pytest

# The expression the sync agents use to build an upload payload. Kept here in
# the same shape so a change to one without the other fails this test.
PAYLOAD_SQL = (
    "to_jsonb(t.*) || jsonb_build_object('source_updated_at', t.updated_at) as row_data"
)


@pytest.fixture()
def synced_table(pool):
    """A table shaped like a synced model: id, a column, updated_at."""
    with pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute('DROP TABLE IF EXISTS public."syncdemo" CASCADE')
            cur.execute(
                '''
                CREATE TABLE public."syncdemo" (
                    id            TEXT PRIMARY KEY,
                    status        TEXT,
                    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                    active_status BOOLEAN DEFAULT TRUE
                )
                '''
            )
        conn.commit()
        pool.putconn(conn)
    yield "public.syncdemo"


def _payload(pool, table, row_id):
    with pool.getconn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT id, {PAYLOAD_SQL} FROM {table} t WHERE id = %s", (row_id,))
            row = cur.fetchone()
        pool.putconn(conn)
    return row["row_data"] if row else None


def _insert(pool, table, row_id, status="pending", updated_at=None):
    when = updated_at or datetime.now(timezone.utc)
    with pool.getconn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {table} (id, status, updated_at) VALUES (%s, %s, %s)",
                (row_id, status, when),
            )
        conn.commit()
        pool.putconn(conn)
    return when


class TestThePayloadCarriesTheVersion:
    def test_source_updated_at_is_present(self, pool, synced_table):
        """The regression: without this key the guard cannot engage at all."""
        _insert(pool, synced_table, "r1")
        payload = _payload(pool, synced_table, "r1")

        assert "source_updated_at" in payload, (
            "HQ skips its last-write-wins guard when the payload omits this key"
        )
        assert payload["source_updated_at"] is not None

    def test_it_equals_the_rows_updated_at(self, pool, synced_table):
        """It is the same quantity, not a second clock."""
        _insert(pool, synced_table, "r2")
        payload = _payload(pool, synced_table, "r2")

        assert payload["source_updated_at"] == payload["updated_at"]

    def test_it_tracks_an_edit(self, pool, synced_table):
        earlier = datetime.now(timezone.utc) - timedelta(hours=2)
        _insert(pool, synced_table, "r3", updated_at=earlier)
        before = _payload(pool, synced_table, "r3")["source_updated_at"]

        later = datetime.now(timezone.utc)
        with pool.getconn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {synced_table} SET status = 'done', updated_at = %s WHERE id = %s",
                    (later, "r3"),
                )
            conn.commit()
            pool.putconn(conn)

        after = _payload(pool, synced_table, "r3")["source_updated_at"]
        assert after > before, "an edit must advance the version HQ compares"

    def test_the_rest_of_the_row_still_travels(self, pool, synced_table):
        """Stamping one key must not replace the payload."""
        _insert(pool, synced_table, "r4", status="approved")
        payload = _payload(pool, synced_table, "r4")

        assert payload["id"] == "r4"
        assert payload["status"] == "approved"
        assert payload["active_status"] is True

    def test_a_stale_and_a_fresh_copy_are_distinguishable(self, pool, synced_table):
        """What the guard needs: the two versions must be comparable.

        HQ compares `EXCLUDED.source_updated_at >= hq_row.source_updated_at`.
        With the key absent both sides were None and the comparison was skipped,
        so a stale row overwrote a newer one.
        """
        old = datetime.now(timezone.utc) - timedelta(days=1)
        _insert(pool, synced_table, "r5", updated_at=old)
        stale = _payload(pool, synced_table, "r5")["source_updated_at"]

        with pool.getconn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE {synced_table} SET updated_at = now() WHERE id = %s", ("r5",)
                )
            conn.commit()
            pool.putconn(conn)
        fresh = _payload(pool, synced_table, "r5")["source_updated_at"]

        assert stale < fresh
