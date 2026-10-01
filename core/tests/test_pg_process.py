"""Starting the embedded PostgreSQL, including through pg_ctl (Windows with
admin rights: bulider_tools/pg_process.py). Uses this machine's PostgreSQL."""
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from unittest import TestCase, mock, skipUnless

from bulider_tools import pg_process

PG_BIN = next(iter(sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True)), None) \
    if Path("/usr/lib/postgresql").is_dir() else None


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@skipUnless(PG_BIN and (PG_BIN / "initdb").exists(), "needs a PostgreSQL install")
class PgCtlServerTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name) / "pg"
        subprocess.run([str(PG_BIN / "initdb"), "-D", str(self.data), "-A", "trust", "--locale=C"],
                       check=True, capture_output=True)
        self.port = free_port()
        self.log = Path(tmp.name) / "pg.log"

    def connect_ok(self):
        import psycopg2

        for _ in range(40):
            try:
                psycopg2.connect(host="127.0.0.1", port=self.port, dbname="postgres", connect_timeout=2).close()
                return True
            except psycopg2.OperationalError:
                time.sleep(0.5)
        return False

    def test_pg_ctl_starts_and_stops_it_like_a_process(self):
        server = pg_process.PgCtlServer(PG_BIN / "pg_ctl", self.data, ["-p", str(self.port), "-k", str(self.data)],
                                        self.log)
        self.addCleanup(lambda: server.poll() is None and server.kill())
        self.assertTrue(self.connect_ok())
        self.assertIsNone(server.poll())
        self.assertGreater(server.pid, 0)
        server.terminate()
        self.assertEqual(server.wait(timeout=30), 0)
        self.assertEqual(server.poll(), 0)

    def test_on_windows_as_admin_start_goes_through_pg_ctl(self):
        with mock.patch.object(pg_process, "windows_admin", return_value=True), \
                mock.patch.object(Path, "with_name", return_value=PG_BIN / "pg_ctl"):
            server, log = pg_process.start(PG_BIN / "postgres", self.data,
                                           ["-p", str(self.port), "-k", str(self.data)], self.log)
        self.addCleanup(lambda: server.poll() is None and server.kill())
        self.assertIsInstance(server, pg_process.PgCtlServer)
        self.assertIsNone(log)
        self.assertTrue(self.connect_ok())
        server.terminate()
        server.wait(timeout=30)

    def test_elsewhere_it_is_a_plain_process(self):
        server, log = pg_process.start(PG_BIN / "postgres", self.data,
                                       ["-p", str(self.port), "-k", str(self.data)], self.log)
        self.addCleanup(log.close)
        self.addCleanup(server.kill)
        self.assertIsInstance(server, subprocess.Popen)
        self.assertTrue(self.connect_ok())
