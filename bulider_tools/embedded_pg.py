"""Cirqen's own PostgreSQL, embedded in the app.

The same way other desktop apps embed their database: the server ships inside
the app (runtime/postgresql, read-only), the cluster lives in the user's own
folder, and Cirqen starts it when it opens and stops it when it closes. No
system service, no admin rights, no installer step, no PostgreSQL on the PC,
nothing outside the app folder and the user's data folder.

    <root>/                      Windows  %LOCALAPPDATA%\\Cirqen\\db
      pg18/                      Linux    ~/.local/share/cirqen/db
      credentials.json           source   <checkout>/data/db
      ready

  pg18/             the cluster, named after the PostgreSQL major that made it.
                    A newer major starts a new, empty cluster that fills from HQ.
  credentials.json  the login Cirqen uses, made with the cluster. It belongs to
                    the cluster, not to config.json, so a new config.json never
                    locks Cirqen out of its own database.
  ready             written once migrations succeeded; until then Cirqen shows
                    the first-run setup.

Creating a cluster is all-or-nothing: initdb runs in pg18.new, which is
renamed to pg18 only when complete. An interrupted first run leaves nothing
half-made.

Every start: if this cluster's server is still running (Cirqen crashed, or the
first-run setup left it up), Cirqen uses it; otherwise pg_ctl starts it.
pg_ctl, not postgres directly, because on Windows it drops administrator
rights postgres refuses to run with. The server listens on 127.0.0.1 only,
with no Unix socket, and only cirqen1 with its password may log in.

No Django or Qt imports: `Cirqen shutdown` (installer, uninstaller) uses this.
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("Cirqen")

USER = DATABASE = "cirqen1"
IS_WINDOWS = sys.platform == "win32"
# No console window for pg_ctl/initdb when Cirqen (a windowed app) runs them.
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}

# Settings Cirqen owns, rewritten on every start so a new app version can
# change them. postgresql.conf includes this file.
SETTINGS = """\
# Written by Cirqen on every start (bulider_tools/embedded_pg.py). Changes are overwritten.
listen_addresses = '127.0.0.1'
unix_socket_directories = ''
max_connections = 60
shared_buffers = 128MB
work_mem = 8MB
maintenance_work_mem = 64MB
jit = off
logging_collector = off
log_min_messages = warning
log_line_prefix = '%m [%p] '
"""

HBA = """\
# Written by Cirqen (bulider_tools/embedded_pg.py): only Cirqen's own login, only from this PC.
host all {user} 127.0.0.1/32 scram-sha-256
host all {user} ::1/128      scram-sha-256
"""


class DatabaseError(Exception):
    """Why the database could not be made or started, in words for the user."""


class EmbeddedPostgres:
    def __init__(self, pg_dir: Path, root: Path, log_dir: Path):
        self.pg_dir = Path(pg_dir)
        self.root = Path(root)
        self.log_dir = Path(log_dir)
        self._major: str | None = None

    # ── where things are ────────────────────────────────────────────────────
    def tool(self, name: str) -> Path:
        return self.pg_dir / "bin" / (f"{name}.exe" if IS_WINDOWS else name)

    @property
    def major(self) -> str:
        """The bundled PostgreSQL's major version, e.g. '18'."""
        if self._major is None:
            if not self.tool("postgres").exists():
                raise DatabaseError(f"PostgreSQL is missing from this installation ({self.pg_dir}). "
                                    "Reinstall Cirqen.")
            out = self._run([self.tool("postgres"), "--version"], timeout=30).stdout
            found = re.search(r"(\d+)(?:\.\d+)?", out or "")
            if not found:
                raise DatabaseError(f"Could not tell the PostgreSQL version from: {out!r}")
            self._major = found.group(1)
        return self._major

    @property
    def data_dir(self) -> Path:
        return self.root / f"pg{self.major}"

    @property
    def credentials_file(self) -> Path:
        return self.root / "credentials.json"

    @property
    def ready_file(self) -> Path:
        return self.root / "ready"

    @property
    def log_file(self) -> Path:
        return self.log_dir / "postgres.log"

    def env(self) -> dict:
        env = os.environ.copy()
        for key in [k for k in env if k.startswith("PG")]:      # PGDATA, PGPORT, ... of another install
            env.pop(key)
        lib = self.pg_dir / "lib"
        if not IS_WINDOWS and lib.is_dir():
            current = env.get("LD_LIBRARY_PATH", "")
            env["LD_LIBRARY_PATH"] = f"{lib}:{current}" if current else str(lib)
        return env

    def _run(self, cmd, timeout=120, **kw) -> subprocess.CompletedProcess:
        """For programs that end by themselves; never for `pg_ctl start`."""
        return subprocess.run([str(c) for c in cmd], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=timeout, env=self.env(), **NO_WINDOW, **kw)

    # ── state ───────────────────────────────────────────────────────────────
    def exists(self) -> bool:
        return (self.data_dir / "PG_VERSION").is_file()

    def is_ready(self) -> bool:
        return self.exists() and self.credentials_file.is_file() and self.ready_file.is_file()

    def mark_ready(self, app_version: str = "") -> None:
        self.ready_file.write_text(json.dumps({"postgres": self.major, "app": app_version,
                                               "at": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")

    def credentials(self) -> dict:
        try:
            data = json.loads(self.credentials_file.read_text(encoding="utf-8"))
            return {"user": data["user"], "password": data["password"], "database": data["database"]}
        except (OSError, ValueError, KeyError, TypeError):
            raise DatabaseError(f"Cirqen's database login ({self.credentials_file}) is missing or damaged.") from None

    def config(self, port: int) -> dict:
        """Connection settings in the shape Django and the services use."""
        return {"host": "127.0.0.1", "port": int(port), **self.credentials()}

    def running_port(self) -> int | None:
        """Port of this cluster's server if it is running, else None."""
        try:
            lines = (self.data_dir / "postmaster.pid").read_text().splitlines()
            pid, port = int(lines[0]), int(lines[3])
        except (OSError, ValueError, IndexError, DatabaseError):
            return None
        try:
            import psutil

            if not psutil.pid_exists(pid) or "postgres" not in psutil.Process(pid).name().lower():
                return None
        except Exception:  # noqa: BLE001
            return None
        return port

    # ── making the cluster ──────────────────────────────────────────────────
    def initialize(self) -> None:
        """Create the cluster and its login. Raises DatabaseError."""
        if self.exists():
            return
        self.root.mkdir(parents=True, exist_ok=True)
        staging = self.root / f"pg{self.major}.new"
        shutil.rmtree(staging, ignore_errors=True)
        password = secrets.token_urlsafe(32)
        pwfile = self.root / "initdb.pw"
        pwfile.write_text(password + "\n", encoding="utf-8")
        _private(pwfile)
        try:
            # On Windows initdb drops administrator rights itself.
            result = self._run([self.tool("initdb"), "-D", staging, "-U", USER, f"--pwfile={pwfile}",
                                "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C", "--no-instructions"],
                               timeout=900)
        except subprocess.TimeoutExpired:
            shutil.rmtree(staging, ignore_errors=True)
            raise DatabaseError("Creating the database took more than 15 minutes. Is the disk very slow or full?")
        finally:
            pwfile.unlink(missing_ok=True)
        if result.returncode != 0:
            shutil.rmtree(staging, ignore_errors=True)
            detail = (result.stderr or result.stdout or "").strip()[-1500:]
            raise DatabaseError(f"Creating the database failed:\n{detail}")

        (staging / "pg_hba.conf").write_text(HBA.format(user=USER), encoding="utf-8")
        with open(staging / "postgresql.conf", "a", encoding="utf-8") as conf:
            conf.write("\ninclude_if_exists = 'cirqen.conf'\n")
        _write_json(self.credentials_file, {"user": USER, "password": password, "database": DATABASE})
        self.ready_file.unlink(missing_ok=True)
        os.replace(staging, self.data_dir)
        logger.info(f"[db] Created the database cluster in {self.data_dir}")

    # ── running it ──────────────────────────────────────────────────────────
    def start(self, port: int, timeout: int = 600, on_wait=None) -> int:
        """Start the server (or find it already running) and wait until Cirqen
        can log in. Returns the port it listens on. Raises DatabaseError.

        timeout is long on purpose: after a power cut PostgreSQL replays its
        log before it answers, and on a slow disk that can take minutes.
        on_wait(seconds) is called while waiting, to keep a UI informed."""
        if not self.exists():
            raise DatabaseError("Cirqen's database has not been created yet.")
        running = self.running_port()
        if running:
            logger.info(f"[db] Already running on port {running}")
            port = running
        else:
            (self.data_dir / "postmaster.pid").unlink(missing_ok=True)       # stale: its process is gone
            (self.data_dir / "cirqen.conf").write_text(SETTINGS, encoding="utf-8")
            _fix_data_dir_mode(self.data_dir)
            self.log_dir.mkdir(parents=True, exist_ok=True)
            _rotate(self.log_file)
            # -W: pg_ctl returns at once and the wait is ours (longer, with a
            # way to tell the user). -o: the port, given each start, so a
            # busy port only means another one next time.
            # No pipes: the server pg_ctl starts would inherit them and hold
            # them open, and on Windows this call would then never return.
            with open(self.log_dir / "pg_ctl.log", "w", encoding="utf-8") as out:
                code = subprocess.run([str(c) for c in (self.tool("pg_ctl"), "start", "-W", "-D", self.data_dir,
                                                        "-l", self.log_file, "-o", f"-p {int(port)}")],
                                      stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                      timeout=120, env=self.env(), **NO_WINDOW).returncode
            if code != 0:
                said = (self.log_dir / "pg_ctl.log").read_text(encoding="utf-8", errors="replace").strip()
                raise DatabaseError(f"PostgreSQL did not start:\n{said}\n{self.log_tail()}".strip())
            logger.info(f"[db] Starting on port {port}")

        started, last_error, gone_since = time.monotonic(), "", None
        while time.monotonic() - started < timeout:
            try:
                self.connect(port, "postgres").close()
                return port
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc).strip()
            if "password authentication failed" in last_error:
                raise DatabaseError("Cirqen's database refused its own login. Its login file "
                                    f"({self.credentials_file}) does not match the database.")
            # postmaster.pid appears as soon as the server is up and goes
            # when it gives up; give it a little while to appear at all.
            if self.running_port() is None:
                gone_since = gone_since or time.monotonic()
                if time.monotonic() - gone_since > 20:
                    raise DatabaseError("PostgreSQL stopped while starting:\n" + self.log_tail())
            else:
                gone_since = None
            if on_wait:
                on_wait(int(time.monotonic() - started))
            time.sleep(0.5)
        raise DatabaseError(f"PostgreSQL did not answer within {timeout // 60} minutes "
                            f"({last_error}).\n{self.log_tail()}")

    def stop(self, timeout: int = 60) -> None:
        """Stop the server if it runs: fast (rolls back open work), then
        immediate, then the process itself."""
        if not self.exists() or self.running_port() is None:
            return
        for mode in ("fast", "immediate"):
            try:
                result = self._run([self.tool("pg_ctl"), "stop", "-D", self.data_dir, "-m", mode, "-w",
                                    "-t", str(timeout)], timeout=timeout + 15)
                if result.returncode == 0 or self.running_port() is None:
                    logger.info(f"[db] Stopped ({mode})")
                    return
            except (OSError, subprocess.TimeoutExpired) as exc:
                logger.warning(f"[db] pg_ctl stop -m {mode}: {exc}")
        try:
            import psutil

            pid = int((self.data_dir / "postmaster.pid").read_text().splitlines()[0])
            psutil.Process(pid).kill()
            logger.warning(f"[db] Killed the server (pid {pid})")
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[db] Could not stop the server: {exc}")

    def connect(self, port: int, database: str | None = None, timeout: int = 3):
        import psycopg2

        cred = self.credentials()
        return psycopg2.connect(host="127.0.0.1", port=int(port), dbname=database or cred["database"],
                                user=cred["user"], password=cred["password"], connect_timeout=timeout)

    def ensure_database(self, port: int) -> None:
        """The app's database exists (it is made on first start)."""
        from psycopg2 import sql

        name = self.credentials()["database"]
        conn = self.connect(port, "postgres")
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
                if not cur.fetchone():
                    cur.execute(sql.SQL("CREATE DATABASE {} ENCODING 'UTF8' TEMPLATE template0")
                                .format(sql.Identifier(name)))
                    logger.info(f"[db] Created database {name}")
        finally:
            conn.close()

    def log_tail(self, chars: int = 1500) -> str:
        try:
            return self.log_file.read_text(encoding="utf-8", errors="replace")[-chars:]
        except OSError:
            return ""

    def remove(self) -> None:
        """Delete the cluster and its login (the server must be stopped)."""
        self.stop()
        shutil.rmtree(self.root, ignore_errors=True)


# ── helpers ─────────────────────────────────────────────────────────────────

def free_port(preferred: range = range(2215, 2251)) -> int:
    for port in preferred:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    _private(tmp)
    os.replace(tmp, path)


def _private(path: Path) -> None:
    if not IS_WINDOWS:          # on Windows the user's profile folder is already private
        try:
            path.chmod(0o600)
        except OSError:
            pass


def _fix_data_dir_mode(data_dir: Path) -> None:
    """PostgreSQL refuses a data folder others can read or write."""
    if IS_WINDOWS:
        return
    try:
        if data_dir.stat().st_uid == os.getuid() and data_dir.stat().st_mode & 0o077:
            data_dir.chmod(0o700)
    except OSError:
        pass


def _rotate(log: Path, max_bytes: int = 20 * 1024 * 1024) -> None:
    try:
        if log.exists() and log.stat().st_size > max_bytes:
            os.replace(log, log.with_suffix(".log.1"))
    except OSError:
        pass


def data_folder_problem(data_path: Path) -> str | None:
    """Why Cirqen can't use its data folder, or None. Running Cirqen once with
    sudo leaves root-owned files behind, and from then on PostgreSQL and the
    logs fail with "Permission denied" for the real user."""
    data_path = Path(data_path)
    if IS_WINDOWS:
        probe = data_path / ".write_test"
        try:
            probe.write_text("ok")
            probe.unlink()
        except OSError as exc:
            return f"Cirqen cannot write to its data folder {data_path}: {exc}"
        return None

    if os.geteuid() == 0:
        return ("Cirqen is running as root (sudo). Start it as your normal user: "
                "PostgreSQL will not run as root, and files made now would be locked to root.")
    uid, foreign = os.getuid(), []
    for root, dirs, files in os.walk(data_path):
        for name in [*dirs, *files]:
            path = Path(root) / name
            try:
                if path.lstat().st_uid != uid:
                    foreign.append(path)
            except OSError:
                continue
            if len(foreign) >= 5:
                break
        if len(foreign) >= 5:
            break
    if not foreign:
        return None
    listed = "\n".join(f"  {p}" for p in foreign)
    return (f"Some files in {data_path} belong to another user (often from running Cirqen with sudo):\n"
            f"{listed}\nFix them once in a terminal, then start Cirqen again:\n"
            f"  sudo chown -R $USER: {data_path}")
