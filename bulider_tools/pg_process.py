"""Start the embedded PostgreSQL server, including on Windows with admin rights.

postgres.exe refuses to run as a user with administrative rights ("Execution
of PostgreSQL by a user with administrative permissions is not permitted").
That is a PC whose user runs with full admin rights (UAC off, or "Run as
administrator"). pg_ctl is PostgreSQL's own way round it: it starts the
server with a restricted token. So on Windows, when Cirqen has admin rights,
the server is started with `pg_ctl start`, and PgCtlServer gives callers the
same poll/terminate/kill/wait/pid as the subprocess.Popen they use elsewhere.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

STARTUP_GRACE = 30          # seconds pg_ctl may take before postmaster.pid exists


def windows_admin() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


class PgCtlServer:
    """A PostgreSQL server started (and stopped) through pg_ctl."""

    def __init__(self, pg_ctl: Path, data_dir: Path, options: list[str], log_path: Path, env=None):
        self.pg_ctl, self.data_dir, self.env = str(pg_ctl), str(data_dir), env
        self.returncode = None
        self._started = time.monotonic()
        quoted = " ".join(f'"{o}"' if " " in o else o for o in options)
        # Without -w pg_ctl returns once the server is launched; callers wait
        # for it to accept connections themselves, as with postgres directly.
        # No pipes: the server pg_ctl starts inherits them and keeps them open
        # while it runs, so reading pg_ctl's output (even with a timeout) never
        # returns on Windows. The server's own messages go to log_path (-l).
        result = subprocess.run([self.pg_ctl, "start", "-D", self.data_dir, "-o", quoted, "-l", str(log_path)],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, env=env)
        if result.returncode != 0:
            self.returncode = result.returncode

    @property
    def pid(self) -> int:
        try:
            return int((Path(self.data_dir) / "postmaster.pid").read_text().splitlines()[0])
        except (OSError, ValueError, IndexError):
            return 0

    def _running(self) -> bool:
        status = subprocess.run([self.pg_ctl, "status", "-D", self.data_dir], capture_output=True, env=self.env)
        return status.returncode == 0

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if self._running() or time.monotonic() - self._started < STARTUP_GRACE:
            return None
        self.returncode = 1
        return self.returncode

    def _stop(self, mode: str) -> None:
        result = subprocess.run([self.pg_ctl, "stop", "-D", self.data_dir, "-m", mode, "-w", "-t", "30"],
                                capture_output=True, text=True, env=self.env)
        if result.returncode != 0 and self._running():
            # pg_ctl couldn't stop it: end the server process itself.
            try:
                import psutil

                if self.pid:
                    psutil.Process(self.pid).kill()
            except Exception:  # noqa: BLE001
                pass
        self.returncode = 0

    def terminate(self) -> None:
        self._stop("fast")

    def kill(self) -> None:
        self._stop("immediate")

    def wait(self, timeout=None):
        # Never wait forever on a server that won't stop (callers expect wait()
        # to return after terminate()/kill()).
        deadline = time.monotonic() + (60 if timeout is None else timeout)
        while self._running():
            if time.monotonic() > deadline:
                raise subprocess.TimeoutExpired(self.pg_ctl, timeout)
            time.sleep(0.5)
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode


def tool(pg_dir: Path, name: str) -> Path:
    """Path of a bundled PostgreSQL program (initdb, postgres, pg_ctl, ...)."""
    return Path(pg_dir) / "bin" / (f"{name}.exe" if sys.platform == "win32" else name)


def pg_env(pg_dir: Path) -> dict:
    """Environment for the bundled programs: their own lib folder first."""
    import os

    env = os.environ.copy()
    lib = Path(pg_dir) / "lib"
    if sys.platform != "win32" and lib.is_dir():
        current = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = f"{lib}:{current}" if current else str(lib)
    return env


def server_options(port: int) -> list[str]:
    """postgres arguments after -D. TCP on 127.0.0.1 only and no Unix socket:
    everything connects over TCP, and a socket file brings its own failures
    (folder not writable, path longer than 107 characters, stale lock files)."""
    return ["-p", str(port), "-c", "listen_addresses=127.0.0.1", "-c", "unix_socket_directories="]


def fix_data_dir_mode(data_dir: Path) -> None:
    """PostgreSQL refuses a data folder others can read or write ("data
    directory has invalid permissions"), which a copy or restore can leave."""
    if sys.platform == "win32":
        return
    import os

    data_dir = Path(data_dir)
    try:
        if data_dir.is_dir() and data_dir.stat().st_uid == os.getuid() and data_dir.stat().st_mode & 0o077:
            data_dir.chmod(0o700)
    except OSError:
        pass


def data_folder_problem(data_path: Path) -> str | None:
    """Why Cirqen can't use its data folder, or None. Running Cirqen once with
    sudo leaves root-owned files (database, logs) behind, and from then on
    PostgreSQL and the logs fail with "Permission denied" for the real user."""
    import os

    data_path = Path(data_path)
    if sys.platform == "win32":
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


def start(pg_bin: Path, data_dir: Path, options: list[str], log_path: Path, env=None):
    """(process, log_handle) for the server at data_dir. options: postgres
    arguments after -D (server_options(port)). log_handle is
    an open file the caller closes, or None when pg_ctl writes the log."""
    pg_bin = Path(pg_bin)
    pg_ctl = pg_bin.with_name("pg_ctl.exe")
    if windows_admin() and pg_ctl.exists():
        return PgCtlServer(pg_ctl, data_dir, options, log_path, env), None
    log_handle = open(log_path, "a")
    process = subprocess.Popen([str(pg_bin), "-D", str(data_dir), *options],
                               stdout=log_handle, stderr=log_handle, env=env)
    return process, log_handle
