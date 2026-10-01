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


def start(pg_bin: Path, data_dir: Path, options: list[str], log_path: Path, env=None):
    """(process, log_handle) for the server at data_dir. options: postgres
    arguments after -D (e.g. ["-p", "2215", "-k", data_dir]). log_handle is
    an open file the caller closes, or None when pg_ctl writes the log."""
    pg_bin = Path(pg_bin)
    pg_ctl = pg_bin.with_name("pg_ctl.exe")
    if windows_admin() and pg_ctl.exists():
        return PgCtlServer(pg_ctl, data_dir, options, log_path, env), None
    log_handle = open(log_path, "a")
    process = subprocess.Popen([str(pg_bin), "-D", str(data_dir), *options],
                               stdout=log_handle, stderr=log_handle, env=env)
    return process, log_handle
