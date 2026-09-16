"""
Pytest fixtures for the sync test suite.

Spins up a throwaway PostgreSQL cluster using the app's *embedded* Postgres
binaries (runtime/postgresql/), so the trigger/JSONB/xmax behaviour is exercised
against a real server — no external DB, no touching the user's data. The whole
cluster is created in a temp dir and torn down after the session.

Binaries are looked up in this order: $CIRQEN_PG_BIN, the embedded
runtime/postgresql/bin (git-ignored, present on dev machines), then a system
PostgreSQL install (initdb on PATH or /usr/lib/postgresql/<ver>/bin, as in CI).

If none is found the DB-backed tests are skipped, and a warning says so loudly.
Set CIRQEN_REQUIRE_PG=1 (CI does) to turn that skip into a failure, so the sync
suite can never pass by silently running nothing.
"""
import os
import shutil
import socket
import subprocess
import tempfile
import time
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _find_pg_bin():
    candidates = []
    if os.environ.get("CIRQEN_PG_BIN"):
        candidates.append(Path(os.environ["CIRQEN_PG_BIN"]))
    candidates.append(REPO_ROOT / "runtime" / "postgresql" / "bin")
    on_path = shutil.which("initdb")
    if on_path:
        candidates.append(Path(on_path).parent)
    candidates.extend(sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True))
    for candidate in candidates:
        if (candidate / "initdb").exists() and (candidate / "pg_ctl").exists():
            return candidate
    return None


PG_BIN = _find_pg_bin()
PG_LIB = PG_BIN.parent / "lib" if PG_BIN else None


def _skip_or_fail(reason):
    if os.environ.get("CIRQEN_REQUIRE_PG") == "1":
        pytest.fail(f"{reason} (CIRQEN_REQUIRE_PG=1)", pytrace=False)
    warnings.warn(f"SYNC DB TESTS SKIPPED: {reason}", stacklevel=2)
    pytest.skip(reason)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _env():
    env = dict(os.environ)
    if PG_LIB and PG_LIB.exists():
        env["LD_LIBRARY_PATH"] = f"{PG_LIB}:{env.get('LD_LIBRARY_PATH', '')}"
    return env


@pytest.fixture(scope="session")
def pg_dsn():
    """Start an embedded throwaway Postgres; yield connection kwargs."""
    if PG_BIN is None:
        _skip_or_fail("no PostgreSQL binaries found (CIRQEN_PG_BIN, runtime/postgresql or system)")

    tmp = Path(tempfile.mkdtemp(prefix="cirqen_pgtest_"))
    data = tmp / "data"
    sock = tmp  # unix socket dir
    port = _free_port()
    env = _env()

    subprocess.run(
        [str(PG_BIN / "initdb"), "-D", str(data), "-U", "tester", "--auth=trust"],
        env=env, check=True, capture_output=True,
    )
    # IMPORTANT: pg_ctl start must NOT capture via pipes — the forked postgres
    # daemon inherits them and holds them open, hanging subprocess.run forever.
    # Redirect the server's own output to a log file (-l) and DEVNULL pg_ctl's.
    subprocess.run(
        [str(PG_BIN / "pg_ctl"), "-D", str(data), "-l", str(data / "server.log"),
         "-o", f"-p {port} -k {sock} -c listen_addresses=", "start"],
        env=env, check=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    import psycopg2
    kwargs = dict(host=str(sock), port=port, user="tester", dbname="postgres")
    # wait for readiness
    for _ in range(30):
        try:
            psycopg2.connect(connect_timeout=2, **kwargs).close()
            break
        except psycopg2.Error:
            time.sleep(0.5)
    else:
        subprocess.run([str(PG_BIN / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
                       env=env, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)
        _skip_or_fail("throwaway postgres did not become ready")

    yield kwargs

    subprocess.run([str(PG_BIN / "pg_ctl"), "-D", str(data), "-m", "immediate", "stop"],
                   env=env, capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture()
def pool(pg_dsn):
    """A fresh connection pool with a clean public schema per test."""
    from psycopg2.pool import ThreadedConnectionPool
    p = ThreadedConnectionPool(1, 5, **pg_dsn)
    conn = p.getconn()
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    conn.commit()
    p.putconn(conn)
    yield p
    try:
        p.closeall()
    except Exception:
        pass  # tolerate an agent __del__ having already closed it


class _State:
    """Minimal StateManager stand-in (dict-backed)."""
    def __init__(self):
        self._d = {}

    def get(self, k, default=None):
        return self._d.get(k, default)

    def set(self, k, v):
        self._d[k] = v


@pytest.fixture()
def agent(pool):
    """
    A lightweight SyncAgent composed only of the Phase 1–2 mixins under test,
    wired to the test pool. Avoids the heavy full-agent __init__ (config, redis,
    HQ registration) so tests stay hermetic.
    """
    from sync.outbox import OutboxMixin
    from sync.conflict_quarantine import ConflictQuarantineMixin
    from sync.conflict_resolver import ConflictResolverMixin
    from sync.schema_guard import SchemaGuardMixin

    class TestAgent(OutboxMixin, ConflictQuarantineMixin,
                    ConflictResolverMixin, SchemaGuardMixin):
        def __init__(self, pool):
            self.pool = pool
            self.state = _State()
            self.machine_id = "M-TEST"
            self.client_id = "CLIENT-TEST"
            self.tables = ["public.jobcard_jobcard"]
            self.sync_cfg = {"upload_batch_size": 2, "use_outbox": True, "outbox_prune": True}

    return TestAgent(pool)


@pytest.fixture()
def apply_agent(pool, pg_dsn):
    """
    The REAL SyncAgent composition (all mixins), with a light __init__ that wires
    only what the download/apply path needs — so tests exercise the actual
    apply_remote_update_locally() code, not a reimplementation.
    """
    from sync.sync_agent import SyncAgent

    class ApplyAgent(SyncAgent):
        def __init__(self, pool, dsn):
            self.pool = pool
            self.state = _State()
            self.machine_id = "M-TEST"
            self.client_id = "CLIENT-TEST"
            self.tables = ["public.jobcard_jobcard"]
            self.sync_cfg = {"upload_batch_size": 50}
            self.config = {
                "sync": {"conflict_resolution": "last_write_wins"},
                "local_db": {
                    "host": dsn["host"], "port": dsn["port"],
                    "database": dsn["dbname"], "user": dsn["user"], "password": "",
                },
            }
            self._table_schema_cache = {}
            self._json_columns_cache = {}
            self._schema_cache = {}

        def __del__(self):
            # The `pool` fixture owns the pool lifecycle; don't let the real
            # SyncAgent.__del__ double-close it during GC.
            pass

    return ApplyAgent(pool, pg_dsn)


@pytest.fixture()
def net_agent(pool, pg_dsn):
    """
    Real SyncAgent (all mixins), light init, wired for the upload/download HTTP
    orchestration tests. `dep_manager` is a trivial pass-through (dependency
    sorting is covered elsewhere and isn't what these tests target).
    """
    import types
    from sync.sync_agent import SyncAgent

    class NetAgent(SyncAgent):
        def __init__(self, pool, dsn):
            self.pool = pool
            self.state = _State()
            self.machine_id = "M-TEST"
            self.client_id = "CLIENT-TEST"
            self.auth_token = "TESTTOKEN"
            self.api_url = "http://hq.test/api/sync"
            self.tables = ["public.jobcard_jobcard"]
            self.sync_cfg = {"upload_batch_size": 50}
            self.config = {
                "sync": {"conflict_resolution": "last_write_wins"},
                "local_db": {
                    "host": dsn["host"], "port": dsn["port"],
                    "database": dsn["dbname"], "user": dsn["user"], "password": "",
                },
            }
            self._table_schema_cache = {}
            self._json_columns_cache = {}
            self._schema_cache = {}
            self.dep_manager = types.SimpleNamespace(
                sort_updates_by_dependency=lambda updates: updates
            )

        def __del__(self):
            pass

    return NetAgent(pool, pg_dsn)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, headers=None, text=""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.headers = headers or {}
        self.text = text

    def json(self):
        return self._json


@pytest.fixture()
def jobcard_table(pool):
    """Create a representative app table in the test DB."""
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE public.jobcard_jobcard (
                id bigint PRIMARY KEY,
                status text,
                updated_at timestamptz
            )
        """)
    conn.commit()
    pool.putconn(conn)
