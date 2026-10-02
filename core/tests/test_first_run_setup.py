"""First run creates the local cluster with the app role as its superuser,
then migrates; normal start repairs a password that no longer matches.
Uses this machine's PostgreSQL (bulider_tools/database.py, services.py)."""
import os
import socket
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock, skipUnless

PG_BIN = next(iter(sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True)), None) \
    if Path("/usr/lib/postgresql").is_dir() else None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class PgHelperTests(TestCase):
    def test_tcp_only_no_unix_socket(self):
        from bulider_tools.pg_process import server_options

        opts = server_options(2215)
        self.assertEqual(opts[:2], ["-p", "2215"])
        self.assertIn("unix_socket_directories=", opts)
        self.assertNotIn("-k", opts)

    @skipUnless(os.name == "posix", "POSIX permissions")
    def test_open_data_dir_is_closed(self):
        from bulider_tools.pg_process import fix_data_dir_mode

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp).chmod(0o775)
            fix_data_dir_mode(Path(tmp))
            self.assertEqual(Path(tmp).stat().st_mode & 0o777, 0o700)

    @skipUnless(os.name == "posix", "POSIX ownership")
    def test_data_folder_problems(self):
        from bulider_tools.pg_process import data_folder_problem

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "logs").mkdir()
            self.assertIsNone(data_folder_problem(Path(tmp)))
            with mock.patch("os.geteuid", return_value=0):
                self.assertIn("root", data_folder_problem(Path(tmp)))
            with mock.patch("os.getuid", return_value=os.getuid() + 1):
                self.assertIn("sudo chown -R $USER:", data_folder_problem(Path(tmp)))


@skipUnless(PG_BIN and (PG_BIN / "initdb").exists(), "needs a PostgreSQL install")
class FirstRunSetupTests(TestCase):
    def setUp(self):
        try:
            from bulider_tools import database, services
        except Exception as exc:  # PySide6 missing on this machine
            self.skipTest(f"desktop setup module unavailable: {exc}")
        self.database, self.services = database, services
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        (self.data / "logs").mkdir()
        self.config = {"host": "127.0.0.1", "port": free_port(), "database": "cirqen1",
                       "user": "cirqen1", "password": "first pass;'\"word"}
        ports = SimpleNamespace(get_port=lambda name: self.config["port"])
        for module in (database, services):
            patcher = mock.patch.object(module, "DATA_PATH", self.data)
            patcher.start()
            self.addCleanup(patcher.stop)
        with mock.patch.object(database, "setup_environment", return_value=dict(self.config)), \
                mock.patch.object(database, "RUNTIME_DIR", self.data / "runtime"):
            self.setup = database.FirstRunSetup(ports)
        self.setup.pg_dir = PG_BIN.parent
        self.results = []
        self.setup.setup_complete.connect(lambda ok, msg: self.results.append((ok, msg)))
        self.migrated = mock.patch.object(database.FirstRunSetup, "_run_migrations", return_value=None)
        self.migrated.start()
        self.addCleanup(self.migrated.stop)

    def connect(self, password):
        import psycopg2

        return psycopg2.connect(host="127.0.0.1", port=self.config["port"], dbname="cirqen1",
                                user="cirqen1", password=password, connect_timeout=3)

    def start_server(self):
        server, log = self.setup._start()
        self.addCleanup(lambda: (self.setup._stop(server), log and log.close()))
        self.assertTrue(self.setup._wait_ready(server))
        return server

    def test_setup_makes_cluster_and_database_and_stops(self):
        self.setup.run_setup()
        self.assertEqual(self.results[-1][0], True, self.results)
        self.assertTrue((self.data / "postgres" / "PG_VERSION").exists())
        self.assertFalse((self.data / "postgres" / "postmaster.pid").exists())
        self.assertFalse((self.data / "temp" / "initdb.pw").exists())
        self.assertEqual((self.data / "postgres").stat().st_mode & 0o077, 0)

        self.start_server()
        conn = self.connect(self.config["password"])
        with conn.cursor() as cur:
            cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            self.assertTrue(cur.fetchone()[0])
        conn.close()
        self.assertEqual(list((self.data / "postgres").glob(".s.PGSQL*")), [])

    def test_unfinished_folder_is_moved_aside(self):
        (self.data / "postgres").mkdir()
        (self.data / "postgres" / "base").mkdir()
        self.setup.run_setup()
        self.assertTrue(self.results[-1][0], self.results)
        self.assertEqual(len(list(self.data.glob("postgres.unfinished-*"))), 1)

    def test_failed_migrations_are_reported(self):
        self.migrated.stop()
        with mock.patch.object(self.database.FirstRunSetup, "_run_migrations", return_value="boom"):
            self.setup.run_setup()
        self.migrated.start()
        self.assertFalse(self.results[-1][0])
        self.assertIn("boom", self.results[-1][1])

    def test_start_repairs_a_changed_password(self):
        self.setup.run_setup()
        self.start_server()
        new_config = dict(self.config, password="config.json was recreated")
        manager = SimpleNamespace(db_config=new_config)
        manager._repair_app_password = types.MethodType(self.services.ServiceManager._repair_app_password, manager)
        ensure = types.MethodType(self.services.ServiceManager._ensure_pg_user_and_db_safe, manager)

        self.assertTrue(ensure(self.config["port"], PG_BIN.parent))
        self.connect("config.json was recreated").close()
        rules = [line for line in (self.data / "postgres" / "pg_hba.conf").read_text().splitlines()
                 if line.strip() and not line.startswith("#")]
        self.assertFalse([r for r in rules if "trust" in r], rules)
