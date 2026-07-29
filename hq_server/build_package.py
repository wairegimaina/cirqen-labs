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

from __future__ import annotations

import base64
import hashlib
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path


# ── Package signing (Ed25519) ─────────────────────────────────────────────────
# HQ signs the manifest bytes with a private key held only in the server env
# (HQ_SIGNING_PRIVATE_KEY = base64 of the 32-byte raw seed). Clients verify with
# the embedded public key, so integrity AND authenticity are guaranteed — a
# tampered or spoofed package is rejected before a single file is written.
#
# Generate a keypair once:   python build_package.py --genkeys
# Put the PRIVATE value in Render env; embed the PUBLIC value in the client.

def _load_private_key():
    raw = os.environ.get("HQ_SIGNING_PRIVATE_KEY", "").strip()
    if not raw:
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        return Ed25519PrivateKey.from_private_bytes(base64.b64decode(raw))
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  HQ_SIGNING_PRIVATE_KEY is set but invalid: {exc}")
        return None


def _sign_bytes(data: bytes) -> str | None:
    key = _load_private_key()
    if key is None:
        return None
    return base64.b64encode(key.sign(data)).decode()


def _genkeys():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.generate()
    seed = priv.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub = priv.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    print("HQ_SIGNING_PRIVATE_KEY (server env, keep secret):")
    print("  " + base64.b64encode(seed).decode())
    print("\nUPDATE_PUBLIC_KEY (embed in the client):")
    print("  " + base64.b64encode(pub).decode())


def _tree_hash(files: list[dict]) -> str:
    """Order-independent hash of the whole desired file set (path→sha256)."""
    joined = "\n".join(f"{e['path']}:{e['sha256']}" for e in sorted(files, key=lambda e: e["path"]))
    return hashlib.sha256(joined.encode()).hexdigest()


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

    # Files that existed in the previous package but are gone now (e.g. a module
    # that became a package during a refactor). Clients delete these so stale
    # modules don't linger and shadow their replacements.
    deletions = _compute_deletions(packages_dir, version, files)
    if deletions:
        print(f"   {len(deletions)} file(s) removed since previous version")

    zip_path, manifest = _build_zip(version, files, repo_root, zip_path, deletions)

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
        "min_version": "0.0.0",           # #7 clients older than this can't jump here
        "rollout_percent": 100,           # #7 staged rollout (edit the .json to canary)
        "yanked": False,                  # #7 kill switch — set true to pull a bad release
        "signed": manifest.pop("_signed", False),        # #1
        "tree_hash": manifest.get("tree_hash", ""),      # #10
        "deletions": manifest.get("deletions", []),      # surfaced for delta builds
        "manifest": manifest,
    }
    manifest.pop("_manifest_sha256", None)

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

def _previous_meta(packages_dir: Path, current_version: str) -> dict | None:
    """Return the most recent prior package meta (highest version < current)."""
    cur = _parse_version(current_version)
    best = None
    best_ver = (-1, -1, -1)
    for mf in packages_dir.glob("cirqen_update_v*.json"):
        try:
            data = json.loads(mf.read_text())
        except Exception:
            continue
        ver = _parse_version(data.get("version", "0.0.0"))
        if ver < cur and ver > best_ver:
            best_ver, best = ver, data
    return best


def _compute_deletions(packages_dir: Path, version: str, files: list[dict]) -> list[str]:
    """Paths present in the previous package but absent from the current build."""
    prev = _previous_meta(packages_dir, version)
    if not prev:
        return []
    prev_files = prev.get("manifest", {}).get("files", [])
    prev_paths = {e["path"] for e in prev_files}
    current_paths = {e["path"] for e in files}
    return sorted(prev_paths - current_paths)


def _parse_version(v: str) -> tuple:
    try:
        parts = [int(x) for x in str(v).strip().split(".")]
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])
    except Exception:
        return (0, 0, 0)


def _build_zip(
    version: str,
    files: list[dict],
    repo_root: Path,
    zip_path: Path,
    deletions: list[str] | None = None,
) -> tuple[Path, dict]:
    # Fix version.txt entry to reflect the correct version content *before*
    # the tree hash / signature are computed over the file set.
    version_content = version + "\n"
    version_sha256 = hashlib.sha256(version_content.encode()).hexdigest()
    for entry in files:
        if entry["path"] == "version.txt":
            entry["sha256"] = version_sha256
            entry["size"] = len(version_content.encode())
            break

    manifest = {
        "version": version,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "deletions": deletions or [],
        "tree_hash": _tree_hash(files),   # #10 full-tree verification
    }

    manifest_bytes = json.dumps(manifest, indent=2).encode()
    signature = _sign_bytes(manifest_bytes)  # #1 Ed25519 signature (or None)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr("manifest.json", manifest_bytes)
        if signature:
            zf.writestr("manifest.sig", signature)
        # Always write the correct version into the package
        zf.writestr("files/version.txt", version_content)
        for entry in files:
            if entry["path"] == "version.txt":
                continue
            src = repo_root / entry["path"]
            if src.exists():
                zf.write(src, f"files/{entry['path']}")

    manifest["_signed"] = bool(signature)
    manifest["_manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
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

def build_delta_zip(full_zip: Path, from_manifest: dict, to_manifest: dict, out_zip: Path) -> Path:
    """
    #6 — Build a slim delta package containing only files whose sha256 changed
    (or are new) between ``from_manifest`` and ``to_manifest``, plus the full
    ``deletions`` list. Reuses the already-built full package as the source of
    file contents, so no repo access is needed at request time.
    """
    from_sha = {e["path"]: e["sha256"] for e in from_manifest.get("files", [])}
    changed = [e for e in to_manifest.get("files", []) if from_sha.get(e["path"]) != e["sha256"]]

    delta_manifest = dict(to_manifest)
    delta_manifest["files"] = changed
    delta_manifest["delta_from"] = from_manifest.get("version")
    delta_bytes = json.dumps(delta_manifest, indent=2).encode()
    signature = _sign_bytes(delta_bytes)

    changed_paths = {e["path"] for e in changed}
    with zipfile.ZipFile(full_zip, "r") as src, \
            zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as dst:
        dst.writestr("manifest.json", delta_bytes)
        if signature:
            dst.writestr("manifest.sig", signature)
        for name in src.namelist():
            if name.startswith("files/") and name[len("files/"):] in changed_paths:
                dst.writestr(name, src.read(name))
    return out_zip


if __name__ == "__main__":
    import sys
    if "--genkeys" in sys.argv:
        _genkeys()
        sys.exit(0)
    version = sys.argv[1] if len(sys.argv) > 1 else "1.0.0"
    repo_root = Path(__file__).parent.parent
    packages_dir = Path(__file__).parent / "packages"
    packages_dir.mkdir(exist_ok=True)
    result = build_package_if_needed(version, packages_dir, repo_root)
    print(json.dumps(result, indent=2, default=str))
