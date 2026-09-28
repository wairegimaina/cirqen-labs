"""Finding Ansur on this PC and checking the connection is ready to use.

Cirqen and Ansur run on the same Windows desktop. Cirqen writes a work order
into ``jobs``, Ansur saves its record into ``results``, and Cirqen moves what
it has read into ``archive`` (or ``quarantine`` with a reason). Templates
(.mtt) that procedures are mapped to live in ``templates``. All five sit
under one base folder so there is a single thing to back up and to exclude
from antivirus scanning.

Nothing here starts Ansur: opening its window just to test the connection
would get in the technician's way. The checks look at files and folders only.
"""
from __future__ import annotations

import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

FOLDERS = ("jobs", "results", "archive", "quarantine", "templates")
TEMPLATE_SUFFIX = ".mtt"
EXE_NAME = "ansur.exe"

# Ansur's installer has used both of these vendor folders over the years.
_VENDOR_DIRS = ("Fluke", "Fluke Biomedical")


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


def is_windows() -> bool:
    return sys.platform.startswith("win")


def folder_paths(base: str) -> dict[str, Path]:
    root = Path(base)
    return {name: root / name for name in FOLDERS}


def ensure_folders(base: str) -> list[str]:
    """Create any missing folders; return the names created."""
    created = []
    for name, path in folder_paths(base).items():
        if not path.is_dir():
            path.mkdir(parents=True, exist_ok=True)
            created.append(name)
    return created


def _writable(folder: Path) -> bool:
    probe = folder / f".cirqen-probe-{uuid.uuid4().hex}"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def list_templates(base: str) -> list[str]:
    folder = folder_paths(base)["templates"]
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() == TEMPLATE_SUFFIX)


def _registry_dirs() -> list[Path]:
    """Install folders Windows lists for anything named Ansur.

    Read from the uninstall entries rather than a Fluke-specific key, whose
    name is not documented and may differ between Ansur versions.
    """
    try:
        import winreg  # Windows only
    except ImportError:
        return []

    found = []
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, key_path in roots:
        try:
            key = winreg.OpenKey(hive, key_path)
        except OSError:
            continue
        with key:
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    with winreg.OpenKey(key, winreg.EnumKey(key, i)) as app:
                        name = winreg.QueryValueEx(app, "DisplayName")[0]
                        if "ansur" not in str(name).lower():
                            continue
                        location = winreg.QueryValueEx(app, "InstallLocation")[0]
                        if location:
                            found.append(Path(location))
                except OSError:
                    continue
    return found


def _program_files_dirs() -> list[Path]:
    dirs = []
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(var)
        if not base:
            continue
        for vendor in _VENDOR_DIRS:
            vendor_dir = Path(base) / vendor
            if vendor_dir.is_dir():
                dirs += [d for d in vendor_dir.iterdir() if d.is_dir() and "ansur" in d.name.lower()]
    return dirs


def _exe_in(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    for child in folder.iterdir():
        if child.is_file() and child.name.lower() == EXE_NAME:
            return child
    return None


def detect_program() -> str:
    """Path to Ansur's program on this PC, or "" if it cannot be found."""
    for folder in _registry_dirs() + _program_files_dirs():
        exe = _exe_in(folder)
        if exe:
            return str(exe)
    return ""


def validate_program_path(path: str) -> str | None:
    """Why this path cannot be Ansur's program, or None if it can."""
    if not path:
        return None
    if not path.lower().endswith(".exe"):
        return "Choose the program file itself (it ends in .exe)."
    if not Path(path).is_file():
        return "No program at that path on this PC."
    return None


def validate_base_folder(path: str) -> str | None:
    if not path:
        return "Give a folder, for example C:\\CirqenAnsur."
    if not (Path(path).is_absolute() or PureWindowsPath(path).is_absolute()):
        return "Use a full path starting with the drive, for example C:\\CirqenAnsur."
    if path.startswith("\\\\"):
        return "Use a folder on this PC's own disk, not a network share."
    return None


def readiness(cfg, template_maps) -> list[Check]:
    """What works and what does not, in the order to fix it."""
    checks = []

    checks.append(Check(
        "Windows", is_windows(),
        "This PC runs Windows." if is_windows() else "Ansur runs on Windows only; this PC is not Windows.",
        "" if is_windows() else "Use the Windows desktop that has Ansur installed.",
    ))

    program_ok = bool(cfg.program_path) and Path(cfg.program_path).is_file()
    checks.append(Check(
        "Ansur program", program_ok,
        cfg.program_path if program_ok else (
            f"Not found at {cfg.program_path}." if cfg.program_path else "Not set."),
        "" if program_ok else "Press Find Ansur, or paste the path to Ansur's .exe.",
    ))

    folders = folder_paths(cfg.base_folder)
    missing = [n for n, p in folders.items() if not p.is_dir()]
    unwritable = [n for n, p in folders.items() if p.is_dir() and not _writable(p)]
    folders_ok = not missing and not unwritable
    if folders_ok:
        detail = f"{cfg.base_folder}: {', '.join(FOLDERS)}"
    elif missing:
        detail = f"Missing: {', '.join(missing)}."
    else:
        detail = f"Cirqen cannot write to: {', '.join(unwritable)}."
    checks.append(Check(
        "Work folders", folders_ok, detail,
        "" if folders_ok else ("Press Create folders." if missing
                               else "Give this Windows user write access to the folder."),
    ))

    available = {name.lower() for name in list_templates(cfg.base_folder)}
    maps = list(template_maps)
    missing_files = [m.template_file for m in maps if m.template_file.lower() not in available]
    maps_ok = bool(maps) and not missing_files
    if not maps:
        detail, fix = "No procedure is linked to an Ansur template yet.", "Link at least one procedure below."
    elif missing_files:
        detail = f"Not in the templates folder: {', '.join(missing_files)}."
        fix = f"Copy the template files into {folders['templates']}."
    else:
        detail, fix = f"{len(maps)} procedure(s) linked.", ""
    checks.append(Check("Templates", maps_ok, detail, fix))

    return checks


def is_ready(cfg, template_maps) -> bool:
    return all(c.ok for c in readiness(cfg, template_maps))
