"""
build_package.py
================
Called automatically from main.py on every Render startup.

Scans the repo root (one level above hq_server/), collects all relevant
source files, and builds a .zip that the Cirqen Updater knows how to apply.

The .zip structure:
  manifest.json          ← required — lists every file with sha256
  files/
    accounts/models.py
    Inventory/views.py
    templates/base.html
    ... etc.

Run manually to test:
  python build_package.py 1.0.1
"""

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path


# ── Which directories to scan ─────────────────────────────────────────────────
# Add any new Django app here so it gets included in update packages.

INCLUDE_DIRS = [
    "accounts",
    "Archives",
    "audit_log",
    "CalSoft",
    "calSchedules",
    "core",
    "dashboard",
    "Equiper",       # settings, urls, wsgi, celery
    "Inventory",
    "jobcard",
    "machineReports",
    "parts_tools",
    "ppms",
    "reporthub",
    "sync",
    "templates",
    "static",
    "updates",
    "UserProfile",
    "users",
    "workshop",
]

INCLUDE_FILES = [
    "manage.py",
    "requirements.txt",
    "version.txt",
]

INCLUDE_EXTENSIONS = {
    ".py",
    ".html",
    ".css",
    ".js",
    ".json",
    ".txt",
    ".md",
    ".svg",
    ".png",
}

# Patterns — if any of these appear anywhere in the relative path, skip the file
EXCLUDE_PATTERNS = [
    "__pycache__",
    ".pyc",
    "signatures/",
    "media/",
    "update_staging/",
    "update_backups/",
    ".env",
    "db.sqlite3",
    "hq_server/",
    "packages/",
    "dist/",
    "build/",
    "build_logs/",
    "staticfiles/",
    "offline_cache/",
    "logs/",
    ".git/",
    "runtime/",
]


# ── Entry point ───────────────────────────────────────────────────────────────

def build_package_if_needed(
    version: str,
    packages_dir: Path,
    repo_root: Path,
) -> dict:
    """
    Called from FastAPI startup.
    If a package for `version` already exists → skip (idempotent).
    Otherwise → scan repo, build manifest, write .zip.
    Returns {"built": True/False, "path": str}
    """
    zip_path = packages_dir / f"cirqen_update_v{version}.zip"
    meta_path = packages_dir / f"cirqen_update_v{version}.json"

    if zip_path.exists() and meta_path.exists():
        return {"built": False, "path": str(zip_path)}

    print(f"📦 Building update package v{version}...")

    files = _collect_files(repo_root)
    print(f"   Collected {len(files)} files")

    zip_path, manifest = _build_zip(version, files, repo_root, zip_path)

    checksum = _sha256(zip_path)
    size_bytes = zip_path.stat().st_size

    meta = {
        "version": version,
        "filename": zip_path.name,
        "checksum": checksum,
        "size_bytes": size_bytes,
        "file_count": len(files),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "changes": _read_changelog(repo_root, version),
        "critical": False,
        "min_version": "0.0.0",
        "manifest": manifest,
    }

    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"✅ Package built: {zip_path.name} ({size_bytes // 1024} KB, {len(files)} files)")
    return {"built": True, "path": str(zip_path), "meta": meta}


# ── File collection ───────────────────────────────────────────────────────────

def _should_exclude(path: Path, repo_root: Path) -> bool:
    relative = str(path.relative_to(repo_root))
    for pattern in EXCLUDE_PATTERNS:
        if pattern in relative:
            return True
    return False


def _collect_files(repo_root: Path) -> list[dict]:
    collected = []

    for dir_name in INCLUDE_DIRS:
        dir_path = repo_root / dir_name
        if not dir_path.exists():
            continue
        for fp in dir_path.rglob("*"):
            if not fp.is_file():
                continue
            if fp.suffix.lower() not in INCLUDE_EXTENSIONS:
                continue
            if _should_exclude(fp, repo_root):
                continue
            relative_path = str(fp.relative_to(repo_root))
            collected.append({
                "path": relative_path,
                "sha256": _sha256(fp),
                "size": fp.stat().st_size,
                "type": _classify(relative_path),
            })

    for fname in INCLUDE_FILES:
        fp = repo_root / fname
        if fp.exists():
            collected.append({
                "path": fname,
                "sha256": _sha256(fp),
                "size": fp.stat().st_size,
                "type": "backend",
            })

    return collected


def _classify(path: str) -> str:
    if "migrations/" in path and path.endswith(".py"):
        return "migration"
    if path.startswith("templates/") or path.endswith(".html"):
        return "template"
    if path.startswith("static/") and (path.endswith(".css") or path.endswith(".js")):
        return "static"
    return "backend"


# ── Zip builder ───────────────────────────────────────────────────────────────

def _build_zip(
    version: str,
    files: list[dict],
    repo_root: Path,
    zip_path: Path,
) -> tuple[Path, dict]:
    manifest = {
        "version": version,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for entry in files:
            src = repo_root / entry["path"]
            if src.exists():
                zf.write(src, f"files/{entry['path']}")

    return zip_path, manifest


# ── Changelog ─────────────────────────────────────────────────────────────────

def _read_changelog(repo_root: Path, version: str) -> str:
    changelog = repo_root / "CHANGELOG.md"
    if not changelog.exists():
        return f"Update to version {version}"
    text = changelog.read_text()
    lines = text.splitlines()
    in_section = False
    section_lines = []
    for line in lines:
        if f"## {version}" in line or f"## v{version}" in line:
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                break
            section_lines.append(line)
    return "\n".join(section_lines).strip() or f"Update to version {version}"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    version = sys.argv[1] if len(sys.argv) > 1 else "1.0.0"
    repo_root = Path(__file__).parent.parent
    packages_dir = Path(__file__).parent / "packages"
    packages_dir.mkdir(exist_ok=True)
    result = build_package_if_needed(version, packages_dir, repo_root)
    print(json.dumps(result, indent=2, default=str))
