"""Which runtime an installed Cirqen has, and which one a release needs.

An in-app update replaces Cirqen's own .py files. It can't replace what the
build froze into the app folder: the Python interpreter, the libraries from
requirements.txt, the embedded PostgreSQL and Redis, and main.py (the
executable's entry point). Those make up the runtime.

The runtime id is a short hash of runtime.json (declared versions),
requirements.txt and main.py. build.py stamps it into each build
(_internal/runtime_id.txt); Control's update packages carry the id of the
code they hold (hq_server/build_package.py). When a PC's id differs from the
offered version's, a code update would run new code on old libraries, so
Control offers the full app instead (build.py --publish) and the PC swaps
its app folder (bulider_tools/full_update.py).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

STAMP_FILE = "runtime_id.txt"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def declared(root: Path) -> dict:
    data = json.loads((Path(root) / "runtime.json").read_text())
    return {k: v for k, v in data.items() if not k.startswith("_")}


def fingerprint(root: Path) -> str:
    """The runtime id of the source tree at root."""
    root = Path(root)
    parts = {"runtime": declared(root), "requirements": _sha256(root / "requirements.txt"),
             "main": _sha256(root / "main.py")}
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:16]


def installed(app_path: Path | None = None) -> str:
    """The runtime id stamped into this installed app ("" if none: a build
    from before runtime ids, or running from source)."""
    candidates = []
    if app_path:
        candidates += [Path(app_path) / "_internal" / STAMP_FILE, Path(app_path) / STAMP_FILE]
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "_internal" / STAMP_FILE)
    for path in candidates:
        try:
            value = path.read_text().strip()
        except OSError:
            continue
        if value:
            return value
    return ""


def platform_name() -> str:
    return "windows" if sys.platform == "win32" else "linux"
