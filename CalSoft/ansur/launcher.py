"""Starting Ansur, and asking it for its detailed PDF of a record.

Both run Ansur's program with arguments from the Ansur connection page, so
the exact command-line form can be confirmed at the site without a code
change. Ansur is started as the signed-in Windows user (a service cannot
open a window on the technician's desktop), and never through a shell.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from . import setup

PDF_TIMEOUT_SECONDS = 120


class LaunchError(RuntimeError):
    pass


def _command(cfg, template, **values):
    if not setup.is_windows():
        raise LaunchError("Ansur runs on Windows only; open this page on the Ansur PC.")
    program = cfg.program_path
    if not program or not Path(program).is_file():
        raise LaunchError("Ansur's program is not set up. Check the Ansur connection page (Calibration menu).")
    args = template
    for key, value in values.items():
        args = args.replace("{" + key + "}", str(value))
    return [program, *[a.strip('"') for a in shlex.split(args, posix=False)]]


def start(cfg, job_file) -> None:
    """Open Ansur on the work order. Returns at once; Ansur keeps running."""
    command = _command(cfg, cfg.launch_arguments, job=job_file)
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen(command, close_fds=True, creationflags=flags)
    except OSError as exc:
        raise LaunchError(f"Could not start Ansur: {exc.strerror or exc}.")


def make_pdf(cfg, record_path) -> Path | None:
    """Run Ansur's silent PDF export; the PDF it writes, or None."""
    record_path = Path(record_path)
    before = {p: p.stat().st_mtime for p in record_path.parent.glob("*.pdf")}
    try:
        command = _command(cfg, cfg.pdf_arguments, record=record_path)
        subprocess.run(command, timeout=PDF_TIMEOUT_SECONDS, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (LaunchError, OSError, subprocess.TimeoutExpired):
        return None
    expected = record_path.with_suffix(".pdf")
    if expected.is_file():
        return expected
    fresh = [p for p in record_path.parent.glob("*.pdf")
             if p not in before or p.stat().st_mtime > before[p]]
    matching = [p for p in fresh if record_path.stem.lower() in p.stem.lower()]
    return (matching or fresh or [None])[0]


def is_running(cfg) -> bool | None:
    """Whether Ansur's program is running on this PC; None if unknown."""
    if not setup.is_windows() or not cfg.program_path:
        return None
    name = Path(cfg.program_path).name
    try:
        result = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return f'"{name.lower()}"' in result.stdout.lower()
