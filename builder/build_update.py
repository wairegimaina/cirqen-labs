#!/usr/bin/env python3
"""
Equiper — Update Package Builder
=================================
Run this on your **dev laptop** after making code changes.
It produces a .zip file ready to upload to the production server.

Usage
-----
  # Basic (prompts for version/changes interactively)
  python build_update.py

  # Explicit options
  python build_update.py --version 1.3.1 --changes "Fixed PPM bug, added export"

  # Critical update
  python build_update.py --version 1.3.2 --critical --changes "Security fix"

  # Include only specific apps / directories
  python build_update.py --version 1.4.0 --include CalSoft users templates/Calibrition

  # Compare against a git tag to auto-detect changed files
  python build_update.py --version 1.4.0 --since-tag v1.3.0

Output
------
  equiper_update_v<VERSION>.zip
    ├── manifest.json
    └── files/
        └── <changed files mirroring project structure>

Upload the zip to the server via USB/SCP/SFTP, then use the
"Check Updates" page in Equiper to apply it.
"""

import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# Configuration — adjust these to match your project layout
# ─────────────────────────────────────────────────────────────────────────────

# Root of the Django project (where manage.py lives)
PROJECT_ROOT = Path(__file__).parent

# Directories / files that are NEVER included in a package
ALWAYS_EXCLUDE = {
    "__pycache__",
    ".git",
    ".gitignore",
    "*.pyc",
    "*.pyo",
    "*.log",
    ".env",
    ".env.*",
    "node_modules",
    "venv",
    ".venv",
    "staticfiles",   # collected static — server runs collectstatic itself
    "media",
    "update_staging",
    "update_backups",
    "logs",
    "*.sqlite3",
    "*.db",
    "build_update.py",  # exclude this script itself
    "equiper_update_*.zip",
}

# File extensions classified as "static"
STATIC_EXTS = {".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
               ".woff", ".woff2", ".ttf", ".eot", ".xlsx", ".pdf"}

# File extensions classified as "migration"
MIGRATION_EXTS = {".py"}
MIGRATION_PATTERN = "/migrations/"

# Default directories to scan when no --include is given
DEFAULT_SCAN_DIRS = [
    # Django apps
    "accounts", "audit_log", "CalSoft", "calSchedules", "dashboard",
    "Inventory", "jobcard", "machineReports", "parts_tools", "ppms",
    "reporthub", "updates", "users", "workshop",
    # Project config
    "Equiper",
    # Templates & static
    "templates", "static",
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def is_excluded(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    for part in parts:
        if part in ALWAYS_EXCLUDE or part.startswith("."):
            return True
    for pattern in ALWAYS_EXCLUDE:
        if "*" in pattern:
            import fnmatch
            if fnmatch.fnmatch(parts[-1], pattern):
                return True
    return False


def classify_file(rel_path: str) -> str:
    p = Path(rel_path)
    if p.suffix in STATIC_EXTS and "/static/" in rel_path:
        return "static"
    if p.suffix in MIGRATION_EXTS and MIGRATION_PATTERN in rel_path:
        return "migration"
    if "/templates/" in rel_path and p.suffix == ".html":
        return "template"
    return "backend"


def collect_files(scan_dirs: list[str]) -> list[Path]:
    """Walk scan_dirs and collect all non-excluded files."""
    collected = []
    for d in scan_dirs:
        target = PROJECT_ROOT / d
        if not target.exists():
            print(f"  ⚠ Skipping '{d}' — not found")
            continue
        if target.is_file():
            rel = target.relative_to(PROJECT_ROOT)
            if not is_excluded(str(rel)):
                collected.append(target)
        else:
            for path in target.rglob("*"):
                if path.is_file():
                    rel = path.relative_to(PROJECT_ROOT)
                    if not is_excluded(str(rel)):
                        collected.append(path)
    return collected


def get_git_changed_files(since_tag: str) -> list[str]:
    """Return list of files changed since the given git tag."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", since_tag, "HEAD"],
            capture_output=True, text=True, cwd=str(PROJECT_ROOT)
        )
        if result.returncode != 0:
            print(f"  ⚠ git diff failed: {result.stderr.strip()}")
            return []
        files = [f.strip() for f in result.stdout.splitlines() if f.strip()]
        print(f"  ✓ {len(files)} files changed since {since_tag}")
        return files
    except FileNotFoundError:
        print("  ⚠ git not found — cannot use --since-tag")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def build(args):
    version = args.version or input("Version (e.g. 1.3.1): ").strip()
    if not version:
        print("Error: version is required.")
        sys.exit(1)

    changes = args.changes or input("Release notes / changes (optional): ").strip()
    critical = args.critical

    print(f"\n{'='*55}")
    print(f"  Building Equiper Update Package v{version}")
    print(f"  {'[CRITICAL]' if critical else ''}")
    print(f"{'='*55}")

    # Determine which files to include
    if args.since_tag:
        print(f"\n📋 Collecting files changed since {args.since_tag}…")
        git_files = get_git_changed_files(args.since_tag)
        files_to_include = [PROJECT_ROOT / f for f in git_files
                            if (PROJECT_ROOT / f).exists() and not is_excluded(f)]
    else:
        scan = args.include if args.include else DEFAULT_SCAN_DIRS
        print(f"\n📋 Scanning {len(scan)} directories…")
        files_to_include = collect_files(scan)

    if not files_to_include:
        print("\n❌ No files found to include. Check your --include paths.")
        sys.exit(1)

    print(f"   Found {len(files_to_include)} files\n")

    # Build manifest entries
    manifest_files = []
    for path in sorted(files_to_include):
        rel = str(path.relative_to(PROJECT_ROOT))
        manifest_files.append({
            "path": rel,
            "sha256": sha256(path),
            "type": classify_file(rel),
            "size": path.stat().st_size,
        })

    manifest = {
        "version": version,
        "min_version": args.min_version,
        "created_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "changes": changes,
        "critical": critical,
        "file_count": len(manifest_files),
        "files": manifest_files,
    }

    # Create zip
    output_name = f"equiper_update_v{version}.zip"
    output_path = PROJECT_ROOT / output_name

    print(f"📦 Creating {output_name}…")

    types_count = {}
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Write manifest first
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        # Write files
        for entry in manifest_files:
            src = PROJECT_ROOT / entry["path"]
            arcname = f"files/{entry['path']}"
            zf.write(src, arcname)
            types_count[entry["type"]] = types_count.get(entry["type"], 0) + 1
            print(f"   + {entry['type']:10s}  {entry['path']}")

    zip_size = output_path.stat().st_size
    zip_sha  = sha256(output_path)

    print(f"\n{'='*55}")
    print(f"  ✅ Build complete!")
    print(f"  Output  : {output_name}")
    print(f"  Size    : {zip_size / 1048576:.2f} MB")
    print(f"  SHA-256 : {zip_sha}")
    print(f"  Files   : {len(manifest_files)} total")
    for ftype, count in sorted(types_count.items()):
        print(f"            {count:4d}  {ftype}")
    print(f"{'='*55}")
    print()
    print("Next steps:")
    print(f"  1. Transfer {output_name} to the server (USB / SCP / SFTP)")
    print("  2. Log in to Equiper → Check Updates (sidebar)")
    print("  3. Upload Package tab → drop the file → Upload")
    print("  4. Available tab → Apply")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Build an Equiper update .zip package for the production server."
    )
    parser.add_argument("--version", "-v", help="Version string, e.g. 1.3.1")
    parser.add_argument("--changes", "-c", help="Release notes / change description")
    parser.add_argument("--critical", action="store_true", help="Mark as a critical update")
    parser.add_argument("--min-version", default="0.0.0",
                        help="Minimum version required to apply this update (default: 0.0.0)")
    parser.add_argument("--include", nargs="+", metavar="PATH",
                        help="Specific dirs/files to include (default: all app directories)")
    parser.add_argument("--since-tag", metavar="TAG",
                        help="Include only files changed since this git tag")
    args = parser.parse_args()
    build(args)


if __name__ == "__main__":
    main()
