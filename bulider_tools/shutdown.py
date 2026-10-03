"""`Cirqen shutdown`: stop everything this copy of Cirqen runs, for the
installer (before it replaces files) and the uninstaller (before it deletes
them). Processes are matched by where their program lives, never by name, so
another PostgreSQL or Redis on the PC is never touched.

No Django or Qt imports.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _inside(path: str, folder: str) -> bool:
    return bool(path) and os.path.normcase(os.path.abspath(path)).startswith(folder)


def shutdown(app_dir: Path) -> int:
    import psutil

    from .runtime import local_database, logger

    folder = os.path.normcase(os.path.abspath(app_dir)) + os.sep
    me = os.getpid()

    def ours(include_postgres: bool):
        found = []
        for proc in psutil.process_iter(["pid", "exe", "cmdline"]):
            if proc.pid == me:
                continue
            try:
                exe = proc.info["exe"] or ((proc.info["cmdline"] or [""])[0])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if not _inside(exe, folder):
                continue
            if "postgres" in os.path.basename(exe).lower() and not include_postgres:
                continue
            found.append(proc)
        return found

    # The app, its web server, workers and Redis first, so nothing restarts
    # or still uses the database; then the database, cleanly; then anything left.
    def end(procs):
        for proc in procs:
            try:
                proc.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(procs, timeout=10)
        for proc in alive:
            try:
                proc.kill()
            except psutil.Error:
                pass

    end(ours(include_postgres=False))
    try:
        local_database().stop(timeout=30)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[shutdown] database: {exc}")
    end(ours(include_postgres=True))
    return 0


def main() -> int:
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
    else:
        app_dir = Path(__file__).resolve().parents[1]
    return shutdown(app_dir)
