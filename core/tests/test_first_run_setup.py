"""First run creates the local cluster with the app role as its superuser,
then migrates; normal start repairs a password that no longer matches.
Uses this machine's PostgreSQL (bulider_tools/database.py, services.py)."""
import os
import socket
import tempfile
import time
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
        patcher = mock.patch.object(database, "uses_system_db", return_value=False)   # running from source
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


@skipUnless(PG_BIN and (PG_BIN / "initdb").exists(), "needs a PostgreSQL install")
class SystemDatabaseTests(TestCase):
    """The installed app: PostgreSQL is a system service (system_pg.py)."""

    def setUp(self):
        try:
            from bulider_tools import database, system_pg
        except Exception as exc:  # PySide6 missing on this machine
            self.skipTest(f"desktop setup module unavailable: {exc}")
        self.database, self.system_pg = database, system_pg
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.system = self._system_cluster()

    def _system_cluster(self):
        """Like Ubuntu's: superuser postgres; cirqen1 an ordinary role owning its database."""
        import subprocess

        data, port = self.tmp / "system", free_port()
        subprocess.run([str(PG_BIN / "initdb"), "-D", str(data), "-U", "postgres", "-A", "trust", "--locale=C"],
                       check=True, capture_output=True)
        server = subprocess.Popen([str(PG_BIN / "postgres"), "-D", str(data), "-p", str(port), "-k", "",
                                   "-c", "listen_addresses=127.0.0.1"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (server.terminate(), server.wait(30)))
        import psycopg2

        for _ in range(60):
            try:
                conn = psycopg2.connect(host="127.0.0.1", port=port, dbname="postgres", user="postgres")
                break
            except psycopg2.OperationalError:
                time.sleep(0.5)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE ROLE cirqen1 LOGIN CREATEDB PASSWORD 'sys-pass'")
            cur.execute("CREATE DATABASE cirqen1 OWNER cirqen1 TEMPLATE template0 ENCODING 'UTF8'")
        conn.close()
        return {"host": "127.0.0.1", "port": port, "database": "cirqen1", "user": "cirqen1",
                "password": "sys-pass"}

    def _old_bundled_database(self):
        """A PC's old database, made by the old first-run setup, with records in it."""
        data = self.tmp / "data"
        (data / "logs").mkdir(parents=True)
        cfg = {"host": "127.0.0.1", "port": free_port(), "database": "cirqen1", "user": "cirqen1",
               "password": "old-pass"}
        ports = SimpleNamespace(get_port=lambda name: cfg["port"])
        with mock.patch.object(self.database, "DATA_PATH", data), \
                mock.patch.object(self.database, "setup_environment", return_value=dict(cfg)), \
                mock.patch.object(self.database, "uses_system_db", return_value=False), \
                mock.patch.object(self.database.FirstRunSetup, "_run_migrations", return_value=None):
            setup = self.database.FirstRunSetup(ports)
            setup.pg_dir = PG_BIN.parent
            setup.run_setup()
            server, log = setup._start()
            self.assertTrue(setup._wait_ready(server))
            conn = setup._connect("cirqen1")
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("CREATE TABLE equipment (id serial PRIMARY KEY, name text NOT NULL)")
                cur.execute("INSERT INTO equipment (name) VALUES ('Infusion pump'), ('Defibrillator')")
            conn.close()
            setup._stop(server)
            if log:
                log.close()
        return data, cfg

    def rows(self, cfg):
        conn = self.system_pg.connect(cfg)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM equipment ORDER BY id")
                return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()

    def test_load_reads_local_db_json(self):
        path = self.tmp / "local_db.json"
        with mock.patch.object(self.system_pg, "config_path", return_value=path):
            self.assertIsNone(self.system_pg.load())
            path.write_text('{"port": 5433, "password": "x"}')
            self.assertEqual(self.system_pg.load(), {"host": "127.0.0.1", "port": 5433, "database": "cirqen1",
                                                     "user": "cirqen1", "password": "x"})

    def test_vc_runtime_is_put_next_to_postgres(self):
        """The service can't use the copies in Cirqen's folder (error 1053 on
        PCs without the Visual C++ redistributable)."""
        import io

        from bulider_tools import system_pg

        with tempfile.TemporaryDirectory() as tmp:
            app, bin_dir = Path(tmp, "app"), Path(tmp, "bin")
            (app / "_internal" / "PySide6").mkdir(parents=True)
            bin_dir.mkdir()
            (app / "_internal" / "VCRUNTIME140.dll").write_text("vc")
            (app / "_internal" / "VCRUNTIME140_1.dll").write_text("vc1")
            (app / "_internal" / "PySide6" / "MSVCP140.dll").write_text("cp")
            (bin_dir / "msvcp140_1.dll").write_text("kept")
            log = io.StringIO()
            system_pg._copy_vc_runtime(app, bin_dir, log)
            self.assertEqual((bin_dir / "vcruntime140.dll").read_text(), "vc")
            self.assertEqual((bin_dir / "vcruntime140_1.dll").read_text(), "vc1")
            self.assertEqual((bin_dir / "msvcp140.dll").read_text(), "cp")
            self.assertEqual((bin_dir / "msvcp140_1.dll").read_text(), "kept")

    def test_newer_dump_settings_are_dropped(self):
        self.assertFalse(self.system_pg.strip_unsupported("SET transaction_timeout = 0;\n"))
        self.assertTrue(self.system_pg.strip_unsupported("SET statement_timeout = 0;\n"))

    def test_old_records_are_copied_and_old_folder_kept(self):
        data, old = self._old_bundled_database()
        error = self.system_pg.copy_bundled_data(data / "postgres", old, self.system, PG_BIN.parent,
                                                 data / "backups")
        self.assertIsNone(error)
        self.assertEqual(self.rows(self.system), ["Infusion pump", "Defibrillator"])
        self.assertFalse((data / "postgres").exists())
        self.assertTrue((data / "postgres.copied-to-system" / "PG_VERSION").exists())
        self.assertEqual(len(list((data / "backups").glob("before-system-postgres-*.sql"))), 1)
        self.assertEqual(list((data / "backups").glob("*.restore.sql")), [])

    def test_an_old_folder_from_a_failed_first_run_is_set_aside(self):
        """initdb timed out on the first run: the cluster exists (OS user as
        superuser) but cirqen1 was never created, so there is nothing to copy."""
        import getpass
        import subprocess

        data = self.tmp / "data"
        (data / "logs").mkdir(parents=True)
        subprocess.run([str(PG_BIN / "initdb"), "-D", str(data / "postgres"), "-U", getpass.getuser(),
                        "-A", "trust", "--locale=C"], check=True, capture_output=True)
        old = {"host": "127.0.0.1", "port": free_port(), "database": "cirqen1", "user": "cirqen1",
               "password": "old-pass"}
        self.assertIsNone(self.system_pg.copy_bundled_data(data / "postgres", old, self.system, PG_BIN.parent,
                                                           data / "backups"))
        self.assertFalse((data / "postgres").exists())
        self.assertEqual(len(list(data.glob("postgres.never-used-*"))), 1)
        self.assertEqual(list((data / "backups").glob("*.sql")), [])

    def test_a_database_already_in_use_is_not_overwritten(self):
        data, old = self._old_bundled_database()
        conn = self.system_pg.connect(self.system)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE equipment (id serial PRIMARY KEY, name text)")
            cur.execute("INSERT INTO equipment (name) VALUES ('Already here')")
        conn.close()
        self.assertIsNone(self.system_pg.copy_bundled_data(data / "postgres", old, self.system, PG_BIN.parent,
                                                           data / "backups"))
        self.assertEqual(self.rows(self.system), ["Already here"])
        self.assertTrue((data / "postgres" / "PG_VERSION").exists())

    def test_first_run_with_the_system_database(self):
        data, old = self._old_bundled_database()
        ports = SimpleNamespace(get_port=lambda name: 2215)
        with mock.patch.object(self.database, "DATA_PATH", data), \
                mock.patch.object(self.database, "SYSTEM_DB_READY", data / "system_db_ready"), \
                mock.patch.object(self.system_pg, "load", return_value=dict(self.system)), \
                mock.patch.object(self.database, "setup_environment", return_value=dict(self.system)), \
                mock.patch.object(self.database.FirstRunSetup, "_run_migrations", return_value=None):
            (data / "config.json").write_text('{"local_db": {"user": "cirqen1", "password": "old-pass"}}')
            setup = self.database.FirstRunSetup(ports)
            setup.pg_data, setup.pg_dir = data / "postgres", PG_BIN.parent
            results = []
            setup.setup_complete.connect(lambda ok, msg: results.append((ok, msg)))
            self.assertTrue(setup.is_first_run())
            setup.run_setup()
            self.assertTrue(results[-1][0], results)
            self.assertFalse(setup.is_first_run())
        self.assertEqual(self.rows(self.system), ["Infusion pump", "Defibrillator"])

    def test_no_system_database_says_how_to_fix(self):
        ports = SimpleNamespace(get_port=lambda name: 2215)
        with mock.patch.object(self.database, "DATA_PATH", self.tmp), \
                mock.patch.object(self.database, "SYSTEM_DB_READY", self.tmp / "system_db_ready"), \
                mock.patch.object(self.system_pg, "load", return_value=None), \
                mock.patch.object(self.database, "uses_system_db", return_value=True), \
                mock.patch.object(self.database, "setup_environment", return_value=dict(self.system)):
            setup = self.database.FirstRunSetup(ports)
            results = []
            setup.setup_complete.connect(lambda ok, msg: results.append((ok, msg)))
            setup.run_setup()
        self.assertFalse(results[-1][0])
        self.assertIn("setup-database" if os.name == "posix" else "installer", results[-1][1])
