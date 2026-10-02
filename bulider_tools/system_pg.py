"""PostgreSQL installed in the system, not started by Cirqen.

Cirqen used to run its own PostgreSQL from the app folder, as the user, and
that broke in many ways (admin rights on Windows, file permissions, stale pid
files, an app update replacing the binaries under a running server). Now the
database is a system service and Cirqen only connects to it:

  Linux    Ubuntu's postgresql package (a dependency of the .deb). The .deb's
           /usr/lib/cirqen/setup-database (run by postinst, as root) creates
           the cirqen1 role and database and writes /etc/cirqen/local_db.json.
  Windows  The installer runs `Cirqen.exe system-postgres` once with admin
           rights (install_windows below): PostgreSQL 18 from the app is
           copied to Program Files and registered as the CirqenPostgreSQL
           service (NetworkService, automatic start); data and
           local_db.json in %ProgramData%\\Cirqen.

local_db.json holds host, port, database, user and password, and every user
of the PC reads it. A PC that ran the old bundled database has it copied into
the system one on first start (copy_bundled_data); the old folder is kept.

No Django or Qt imports: install_windows runs elevated, from main.py.
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger("Cirqen")

SERVICE_NAME = "CirqenPostgreSQL"
ROLE = DATABASE = "cirqen1"
LINUX_SETUP = "/usr/lib/cirqen/setup-database"
NETWORK_SERVICE_SID = "*S-1-5-20"


def config_path() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Cirqen" / "local_db.json"
    return Path("/etc/cirqen/local_db.json")


def load() -> dict | None:
    """The system database's connection settings, or None when not set up."""
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        cfg = {"host": data.get("host") or "127.0.0.1", "port": int(data["port"]),
               "database": data.get("database") or DATABASE, "user": data.get("user") or ROLE,
               "password": data["password"]}
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return cfg


def fix_hint() -> str:
    if sys.platform == "win32":
        return "Run the Cirqen installer again and allow it to make changes (it sets up the database service)."
    return f"In a terminal run:  sudo {LINUX_SETUP}"


def connect(cfg: dict, database: str | None = None, timeout: int = 3):
    import psycopg2

    return psycopg2.connect(host=cfg["host"], port=cfg["port"], dbname=database or cfg["database"],
                            user=cfg["user"], password=cfg["password"], connect_timeout=timeout)


def start_service() -> None:
    """Ask Windows to start the service (allowed for users: install_windows)."""
    if sys.platform == "win32":
        subprocess.run(["sc", "start", SERVICE_NAME], capture_output=True, timeout=30)


def wait_until_up(cfg: dict, seconds: int = 90) -> str | None:
    """None once Cirqen can log in, else the last error."""
    deadline, error, kicked = time.monotonic() + seconds, "", False
    while time.monotonic() < deadline:
        try:
            connect(cfg).close()
            return None
        except Exception as exc:  # noqa: BLE001
            error = str(exc).strip()
            if "password authentication failed" in error or "does not exist" in error:
                return error
            if not kicked:
                start_service()
                kicked = True
        time.sleep(1)
    return error or "no answer"


def run_elevated_setup() -> bool:
    """Windows: run `Cirqen system-postgres` with admin rights (a UAC prompt)
    and wait for it. True when it ran and local_db.json now exists."""
    if sys.platform != "win32":
        return False
    if getattr(sys, "frozen", False):
        exe, args = sys.executable, "system-postgres"
    else:
        exe, args = sys.executable, f'"{Path(__file__).resolve().parents[1] / "main.py"}" system-postgres'
    command = (f"Start-Process -FilePath '{exe}' -ArgumentList '{args}' -Verb RunAs -Wait "
               "-WindowStyle Hidden")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                       capture_output=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.error(f"[system-pg] Could not start the database setup: {exc}")
    return load() is not None


# ── copying a PC's old bundled database ─────────────────────────────────────

UNSUPPORTED_ON_OLDER_SERVERS = ("SET transaction_timeout",)


def strip_unsupported(sql_line: str) -> bool:
    """False for dump lines an older server rejects (a PostgreSQL 18 dump
    restored into Ubuntu 24.04's PostgreSQL 16)."""
    return not sql_line.startswith(UNSUPPORTED_ON_OLDER_SERVERS)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _tool(pg_dir: Path, name: str) -> str:
    return str(Path(pg_dir) / "bin" / (f"{name}.exe" if sys.platform == "win32" else name))


def _env(pg_dir: Path, password: str | None) -> dict:
    from .pg_process import pg_env

    env = pg_env(pg_dir)
    env.pop("PGPASSWORD", None)
    if password:
        env["PGPASSWORD"] = password
    return env


def target_is_empty(cfg: dict) -> bool:
    conn = connect(cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_tables WHERE schemaname = 'public'")
            return cur.fetchone()[0] == 0
    finally:
        conn.close()


def copy_bundled_data(old_data: Path, old_cfg: dict, target: dict, pg_dir: Path, backups: Path) -> str | None:
    """Copy the database Cirqen used to run itself (old_data) into the system
    one. None when copied or nothing to copy, else why not. The dump stays in
    backups/ and old_data is renamed postgres.copied-to-system."""
    from .pg_process import server_options, start as start_pg_server

    if not (old_data / "PG_VERSION").exists():
        return None
    if not target_is_empty(target):
        logger.info("[system-pg] The system database already has tables; old data not copied")
        return None
    if not Path(_tool(pg_dir, "pg_dump")).exists():
        return f"pg_dump is missing from {pg_dir}: the old database could not be copied"

    server = log = None
    port = None
    try:
        pid_file = old_data / "postmaster.pid"
        try:
            lines = pid_file.read_text().splitlines()
            import psutil

            if psutil.pid_exists(int(lines[0])):
                port = int(lines[3])
        except (OSError, ValueError, IndexError):
            pass
        if port is None:
            for name in ("postmaster.pid", "postmaster.opts"):
                (old_data / name).unlink(missing_ok=True)
            port = _free_port()
            server, log = start_pg_server(Path(_tool(pg_dir, "postgres")), old_data, server_options(port),
                                          backups.parent / "logs" / "postgres_old_copy.log", _env(pg_dir, None))

        # Old PCs: cirqen1 with config.json's password; the oldest also have
        # the OS user as a password-less superuser.
        logins = [(old_cfg["user"], old_cfg.get("password"))]
        try:
            import getpass

            logins.append((getpass.getuser(), None))
        except Exception:  # noqa: BLE001
            pass
        import psycopg2

        login = None
        deadline = time.monotonic() + 90
        while login is None and time.monotonic() < deadline:
            if server is not None and server.poll() is not None:
                return "The old database did not start (logs/postgres_old_copy.log)"
            for user, password in logins:
                try:
                    psycopg2.connect(host="127.0.0.1", port=port, dbname=old_cfg["database"], user=user,
                                     password=password, connect_timeout=3).close()
                    login = (user, password)
                    break
                except psycopg2.OperationalError:
                    continue
            else:
                time.sleep(1)
        if login is None:
            return "Could not log in to the old database to copy it"

        backups.mkdir(parents=True, exist_ok=True)
        dump = backups / f"before-system-postgres-{time.strftime('%Y%m%d-%H%M%S')}.sql"
        result = subprocess.run(
            [_tool(pg_dir, "pg_dump"), "-h", "127.0.0.1", "-p", str(port), "-U", login[0],
             "-d", old_cfg["database"], "--no-owner", "--no-privileges", "-f", str(dump)],
            env=_env(pg_dir, login[1]), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            return f"Copying the old database failed (pg_dump): {result.stderr.strip()[-800:]}"
    finally:
        if server is not None:
            try:
                server.terminate()
                server.wait(timeout=30)
            except Exception:  # noqa: BLE001
                server.kill()
        if log:
            log.close()

    restore = dump.with_suffix(".restore.sql")
    with open(dump, encoding="utf-8") as src, open(restore, "w", encoding="utf-8") as out:
        for line in src:
            if strip_unsupported(line):
                out.write(line)
    try:
        result = subprocess.run(
            [_tool(pg_dir, "psql"), "-h", target["host"], "-p", str(target["port"]), "-U", target["user"],
             "-d", target["database"], "--single-transaction", "-v", "ON_ERROR_STOP=1", "-q",
             "-f", str(restore)],
            env=_env(pg_dir, target["password"]), stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=3600)
    finally:
        restore.unlink(missing_ok=True)
    if result.returncode != 0:
        return f"Copying the old database failed (nothing was changed): {result.stderr.strip()[-800:]}"

    moved = old_data.with_name("postgres.copied-to-system")
    if moved.exists():
        moved = old_data.with_name(f"postgres.copied-to-system-{time.strftime('%Y%m%d-%H%M%S')}")
    old_data.rename(moved)
    logger.info(f"[system-pg] Old database copied into the system one; old files kept in {moved}, dump in {dump}")
    return None


# ── Windows: the service (runs elevated, from the installer) ────────────────

def _run(cmd, log, **kw):
    log.write(f"$ {' '.join(map(str, cmd))}\n")
    result = subprocess.run([str(c) for c in cmd], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            **kw)
    log.write((result.stdout or "") + (result.stderr or "") + f"[exit {result.returncode}]\n")
    log.flush()
    return result


def _port_from_conf(data: Path) -> int | None:
    import re

    try:
        found = re.findall(r"(?m)^\s*port\s*=\s*(\d+)", (data / "postgresql.conf").read_text(encoding="utf-8"))
    except OSError:
        return None
    return int(found[-1]) if found else None


def _pick_port() -> int:
    for port in range(5432, 5460):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return _free_port()


def install_windows(runtime_dir: Path) -> str | None:
    """Install or repair the CirqenPostgreSQL service. None on success."""
    root = config_path().parent
    root.mkdir(parents=True, exist_ok=True)
    with open(root / "setup.log", "a", encoding="utf-8") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} Cirqen database setup\n")
        try:
            error = _install_windows(Path(runtime_dir), root, log)
        except Exception as exc:  # noqa: BLE001
            import traceback

            log.write(traceback.format_exc())
            error = f"{type(exc).__name__}: {exc}"
        log.write(f"result: {error or 'ok'}\n")
        return error


def _install_windows(runtime_dir: Path, root: Path, log) -> str | None:
    src = runtime_dir / "postgresql"
    pg_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Cirqen" / "PostgreSQL"
    data = root / "postgres"
    old = load() or {}
    password = old.get("password") or secrets.token_urlsafe(24)

    if not (pg_dir / "bin" / "postgres.exe").exists():
        if not (src / "bin" / "postgres.exe").exists():
            return f"PostgreSQL is missing from the app ({src})"
        log.write(f"copying {src} -> {pg_dir}\n")
        shutil.copytree(src, pg_dir, dirs_exist_ok=True)
    env = _env(pg_dir, None)

    service = _run(["sc", "query", SERVICE_NAME], log)
    service_exists = service.returncode == 0

    if not (data / "PG_VERSION").exists():
        if service_exists:
            _run(["sc", "stop", SERVICE_NAME], log)
        if data.exists() and any(data.iterdir()):
            data.rename(data.with_name(f"postgres.unfinished-{time.strftime('%Y%m%d-%H%M%S')}"))
        data.mkdir(parents=True, exist_ok=True)
        pwfile = root / "initdb.pw"
        pwfile.write_text(password + "\n", encoding="utf-8")
        try:
            result = _run([pg_dir / "bin" / "initdb.exe", "-D", data, "-U", ROLE, f"--pwfile={pwfile}",
                           "--auth=scram-sha-256", "--encoding=UTF8", "--locale=C"], log, env=env, timeout=600)
        finally:
            pwfile.unlink(missing_ok=True)
        if result.returncode != 0:
            return f"initdb failed: {(result.stderr or result.stdout).strip()[-800:]}"
        port = _pick_port()
        with open(data / "postgresql.conf", "a", encoding="utf-8") as conf:
            conf.write("\n# Cirqen\n"
                       f"port = {port}\n"
                       "listen_addresses = '127.0.0.1'\n"
                       "logging_collector = on\n"
                       "log_directory = 'log'\n")
    port = _port_from_conf(data) or 5432

    # The service account needs full control of the data (as the PostgreSQL
    # installer does it). Only adds: removing the inherited permissions
    # (/inheritance:r) left postgres "Permission denied" on its own files.
    result = _run(["icacls", data, "/grant", f"{NETWORK_SERVICE_SID}:(OI)(CI)F", "/T", "/C", "/Q"], log)
    if result.returncode != 0:
        return f"Could not give the database service access to {data}: {(result.stderr or result.stdout).strip()}"

    if not service_exists:
        result = _run([pg_dir / "bin" / "pg_ctl.exe", "register", "-N", SERVICE_NAME,
                       "-U", r"NT AUTHORITY\NetworkService", "-D", data, "-S", "auto"], log, env=env)
        if result.returncode != 0:
            return f"Registering the service failed: {(result.stderr or result.stdout).strip()[-800:]}"
        _run(["sc", "description", SERVICE_NAME, "PostgreSQL database for Cirqen"], log)
        _run(["sc", "failure", SERVICE_NAME, "reset=", "86400", "actions=", "restart/5000/restart/10000/restart/30000"], log)
        # Let signed-in users start it if it ever stops (Cirqen asks on start).
        sddl = _run(["sc", "sdshow", SERVICE_NAME], log).stdout.strip()
        if sddl.startswith("D:") and "(A;;RPWPLCRC;;;IU)" not in sddl:
            _run(["sc", "sdset", SERVICE_NAME, sddl.replace("D:", "D:(A;;RPWPLCRC;;;IU)", 1)], log)
    _run(["sc", "start", SERVICE_NAME], log)

    cfg = {"host": "127.0.0.1", "port": port, "database": DATABASE, "user": ROLE, "password": password}
    error = wait_until_up({**cfg, "database": "postgres"}, 120)
    if error and "password authentication failed" in error:
        error = _reset_password_windows(pg_dir, data, cfg, log, env)
    if error:
        _diagnose_windows(pg_dir, data, log, env)
        return f"The database service did not answer: {error} (details: {root / 'setup.log'})"

    conn = connect(cfg, "postgres")
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{DATABASE}" ENCODING \'UTF8\' TEMPLATE template0')
    finally:
        conn.close()

    target = config_path()
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    log.write(f"ready: {SERVICE_NAME} on port {port}; settings in {target}\n")
    return None


def _diagnose_windows(pg_dir: Path, data: Path, log, env) -> None:
    """Why the service won't run, into setup.log: its state, what Windows and
    PostgreSQL logged, and whether PostgreSQL runs outside the service."""
    _run(["sc", "queryex", SERVICE_NAME], log)
    _run(["sc", "qc", SERVICE_NAME], log)
    _run(["wevtutil", "qe", "Application", "/q:*[System[Provider[@Name='PostgreSQL']]]", "/c:15", "/rd:true",
          "/f:text"], log)
    _run(["wevtutil", "qe", "System", "/q:*[System[Provider[@Name='Service Control Manager']]]", "/c:8",
          "/rd:true", "/f:text"], log)
    for pg_log in sorted((data / "log").glob("*"))[-2:]:
        log.write(f"--- {pg_log}\n{pg_log.read_text(encoding='utf-8', errors='replace')[-3000:]}\n")
    _run(["icacls", data], log)
    _run(["icacls", data / "postgresql.conf"], log)
    _run(["icacls", pg_dir / "bin" / "postgres.exe"], log)
    trial = data.parent / "trial_start.log"
    _run([pg_dir / "bin" / "pg_ctl.exe", "start", "-D", data, "-l", trial, "-w", "-t", "30"], log, env=env,
         timeout=60)
    if trial.exists():
        log.write(f"--- {trial}\n{trial.read_text(encoding='utf-8', errors='replace')[-3000:]}\n")
    _run([pg_dir / "bin" / "pg_ctl.exe", "stop", "-D", data, "-m", "fast", "-w"], log, env=env, timeout=60)


def _reset_password_windows(pg_dir: Path, data: Path, cfg: dict, log, env) -> str | None:
    """local_db.json was lost: set a new password through a trust rule for
    127.0.0.1 that lasts one reload."""
    hba = data / "pg_hba.conf"
    original = hba.read_text(encoding="utf-8")
    pg_ctl = pg_dir / "bin" / "pg_ctl.exe"
    try:
        hba.write_text(f'host all "{ROLE}" 127.0.0.1/32 trust\n' + original, encoding="utf-8")
        _run([pg_ctl, "reload", "-D", data], log, env=env)
        time.sleep(2)
        import psycopg2

        conn = psycopg2.connect(host="127.0.0.1", port=cfg["port"], dbname="postgres", user=ROLE,
                                connect_timeout=5)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'ALTER ROLE "{ROLE}" WITH PASSWORD %s', (cfg["password"],))
        conn.close()
    except Exception as exc:  # noqa: BLE001
        return f"password reset failed: {exc}"
    finally:
        hba.write_text(original, encoding="utf-8")
        _run([pg_ctl, "reload", "-D", data], log, env=env)
        time.sleep(1)
    return wait_until_up({**cfg, "database": "postgres"}, 30)


def main() -> int:
    """`Cirqen system-postgres`: the installer's elevated step (Windows)."""
    if sys.platform != "win32":
        print(f"On Linux the .deb sets up the database: sudo {LINUX_SETUP}")
        return 1
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    error = install_windows(base / "runtime")
    if error:
        print(error, file=sys.stderr)
        return 1
    return 0
