"""Cirqen's embedded database (bulider_tools/embedded_pg.py) and the first-run
setup that makes it (bulider_tools/database.py). Uses this machine's
PostgreSQL binaries the way the app uses its bundled ones."""
import json
import os
import socket
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock, skipUnless

PG_BIN = next(iter(sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True)), None) \
    if Path("/usr/lib/postgresql").is_dir() else None
HAS_PG = bool(PG_BIN and (PG_BIN / "initdb").exists())


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class DataFolderTests(TestCase):
    @skipUnless(os.name == "posix", "POSIX ownership")
    def test_data_folder_problems(self):
        from bulider_tools.embedded_pg import data_folder_problem

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "logs").mkdir()
            self.assertIsNone(data_folder_problem(Path(tmp)))
            with mock.patch("os.geteuid", return_value=0):
                self.assertIn("root", data_folder_problem(Path(tmp)))
            with mock.patch("os.getuid", return_value=os.getuid() + 1):
                self.assertIn("sudo chown -R $USER:", data_folder_problem(Path(tmp)))


@skipUnless(HAS_PG, "needs a PostgreSQL install")
class EmbeddedPostgresTests(TestCase):
    def setUp(self):
        from bulider_tools.embedded_pg import EmbeddedPostgres

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.db = EmbeddedPostgres(PG_BIN.parent, self.tmp / "db", self.tmp / "logs")
        self.addCleanup(self.db.stop)

    def test_initialize_is_complete_and_private(self):
        self.db.initialize()
        self.assertTrue(self.db.exists())
        self.assertFalse(self.db.is_ready())          # not migrated yet
        self.assertEqual(sorted(p.name for p in (self.tmp / "db").iterdir()),
                         ["credentials.json", f"pg{self.db.major}"])
        cred = json.loads(self.db.credentials_file.read_text())
        self.assertEqual((cred["user"], cred["database"]), ("cirqen1", "cirqen1"))
        self.assertGreaterEqual(len(cred["password"]), 32)
        if os.name == "posix":
            self.assertEqual(self.db.credentials_file.stat().st_mode & 0o077, 0)
        rules = [r for r in (self.db.data_dir / "pg_hba.conf").read_text().splitlines()
                 if r.strip() and not r.startswith("#")]
        self.assertTrue(all("cirqen1" in r and "scram-sha-256" in r for r in rules), rules)

    def test_an_interrupted_initialize_leaves_nothing_in_the_way(self):
        staging = self.tmp / "db" / f"pg{self.db.major}.new"
        (staging / "base").mkdir(parents=True)
        self.db.initialize()
        self.assertFalse(staging.exists())
        self.assertTrue(self.db.exists())

    def test_start_connect_stop(self):
        self.db.initialize()
        port = self.db.start(free_port())
        self.db.ensure_database(port)
        conn = self.db.connect(port)
        with conn.cursor() as cur:
            cur.execute("SELECT current_database(), current_user, current_setting('listen_addresses'), "
                        "current_setting('jit')")
            self.assertEqual(cur.fetchone(), ("cirqen1", "cirqen1", "127.0.0.1", "off"))
        conn.close()
        self.assertEqual(list(self.db.data_dir.glob(".s.PGSQL*")), [])     # TCP only
        self.assertEqual(self.db.running_port(), port)
        self.db.stop()
        self.assertIsNone(self.db.running_port())

    def test_a_running_server_is_used_again(self):
        self.db.initialize()
        port = self.db.start(free_port())
        self.assertEqual(self.db.start(free_port()), port)

    def test_a_stale_pid_file_does_not_block_start(self):
        self.db.initialize()
        port = self.db.start(free_port())
        self.db.stop()
        (self.db.data_dir / "postmaster.pid").write_text(f"999999\n{self.db.data_dir}\n0\n{port}\n")
        self.assertIsNone(self.db.running_port())
        self.db.start(free_port())

    def test_a_wrong_login_is_reported(self):
        from bulider_tools.embedded_pg import DatabaseError

        self.db.initialize()
        cred = json.loads(self.db.credentials_file.read_text())
        self.db.credentials_file.write_text(json.dumps({**cred, "password": "not it"}))
        with self.assertRaises(DatabaseError) as caught:
            self.db.start(free_port(), timeout=60)
        self.assertIn("login", str(caught.exception))

    def test_missing_binaries_are_reported(self):
        from bulider_tools.embedded_pg import DatabaseError, EmbeddedPostgres

        db = EmbeddedPostgres(self.tmp / "nowhere", self.tmp / "db", self.tmp / "logs")
        with self.assertRaises(DatabaseError) as caught:
            db.initialize()
        self.assertIn("Reinstall", str(caught.exception))
        self.assertIsNone(db.running_port())

    def test_shutdown_stops_the_database(self):
        from bulider_tools import shutdown

        self.db.initialize()
        self.db.start(free_port())
        with mock.patch("bulider_tools.runtime.local_database", return_value=self.db):
            shutdown.shutdown(self.tmp / "app")
        self.assertIsNone(self.db.running_port())


@skipUnless(HAS_PG, "needs a PostgreSQL install")
class FirstRunSetupTests(TestCase):
    def setUp(self):
        try:
            from bulider_tools import database
        except Exception as exc:  # PySide6 missing on this machine
            self.skipTest(f"desktop setup module unavailable: {exc}")
        from bulider_tools.embedded_pg import EmbeddedPostgres

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        (self.data / "logs").mkdir()
        self.db = EmbeddedPostgres(PG_BIN.parent, self.data / "db", self.data / "logs")
        self.addCleanup(self.db.stop)
        self.ports = SimpleNamespace(ports={"postgresql_local": free_port()})
        self.ports.get_port = lambda name: self.ports.ports[name]
        for name, value in (("DATA_PATH", self.data), ("local_database", lambda: self.db),
                            ("setup_environment", lambda pm: {"host": "127.0.0.1",
                                                              "port": pm.get_port("postgresql_local"),
                                                              **self.db.credentials()})):
            patcher = mock.patch.object(database, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.database = database
        self.setup = database.FirstRunSetup(self.ports)
        self.results = []
        self.setup.setup_complete.connect(lambda ok, msg: self.results.append((ok, msg)))

    def test_setup_makes_a_ready_database_and_leaves_it_running(self):
        self.assertTrue(self.setup.is_first_run())
        with mock.patch.object(self.database.FirstRunSetup, "_run_migrations", return_value=None) as migrate:
            self.setup.run_setup()
        self.assertTrue(self.results[-1][0], self.results)
        self.assertTrue(self.db.is_ready())
        self.assertFalse(self.setup.is_first_run())
        port = self.db.running_port()
        self.assertEqual(port, self.ports.get_port("postgresql_local"))
        self.assertEqual(migrate.call_args[0][0]["port"], port)
        self.db.connect(port).close()                     # the app database exists

    def test_failed_migrations_are_reported_and_retried_next_start(self):
        with mock.patch.object(self.database.FirstRunSetup, "_run_migrations", return_value="boom"):
            self.setup.run_setup()
        self.assertFalse(self.results[-1][0])
        self.assertIn("boom", self.results[-1][1])
        self.assertIsNone(self.db.running_port())          # stopped on failure
        self.assertTrue(self.setup.is_first_run())

        with mock.patch.object(self.database.FirstRunSetup, "_run_migrations", return_value=None):
            self.setup.run_setup()
        self.assertTrue(self.results[-1][0], self.results)
