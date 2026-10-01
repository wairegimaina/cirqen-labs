"""HQ software for hospitals that run their own HQ server (self-hosted).

A Render-hosted HQ is deployed from the panel (render_api.py). A self-hosted
one updates itself: its updater (hq_server selfhost/cirqen_hq_update.py) asks
Control which version this hospital should run, downloads it through Control
and installs it, with a backup and a rollback if the new version isn't healthy.

Versions are the git tags of the private hq_server repository (v1.4.0, ...).
Control reads them with GITHUB_TOKEN (read-only, that repository only), set
once on Control; hospital servers never hold GitHub credentials. Per hospital,
in the panel: follow the newest tag, pin one, or hold.

Only the hospital's own HQ can download: the request is signed with its HQ
identity key (the one in its certificate), so the code isn't open to anyone
who knows a hospital code.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

CONTEXT = b"cirqen-hq-release-v1\n"
DOWNLOAD_CONTEXT = b"cirqen-hq-download-v1\n"
DOWNLOAD_WINDOW = 300            # seconds a signed download request stays valid
TAG = re.compile(r"^v\d+(\.\d+){1,3}$")
GITHUB = "https://api.github.com"
TAGS_TTL = 120

_tags_cache: dict = {"at": 0.0, "tags": []}


class ReleaseError(Exception):
    """GitHub refused or isn't configured; the message is safe to show."""


def repo() -> str:
    return os.getenv("HQ_REPO", "wairegimaina/hq_server").strip()


def configured() -> bool:
    return bool(os.getenv("GITHUB_TOKEN", "").strip())


def _client(transport=None) -> httpx.Client:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise ReleaseError("HQ releases are not connected: set GITHUB_TOKEN on Cirqen Control once.")
    return httpx.Client(base_url=GITHUB, timeout=60, transport=transport, follow_redirects=True,
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                                 "X-GitHub-Api-Version": "2022-11-28"})


def _key(tag: str) -> tuple:
    return tuple(int(x) for x in tag[1:].split("."))


def tags(transport=None, fresh: bool = False) -> list[str]:
    """Release tags, newest first."""
    if not fresh and time.time() - _tags_cache["at"] < TAGS_TTL and _tags_cache["tags"]:
        return _tags_cache["tags"]
    try:
        with _client(transport) as client:
            resp = client.get(f"/repos/{repo()}/tags", params={"per_page": 100})
    except httpx.HTTPError as exc:
        raise ReleaseError(f"GitHub could not be reached ({exc.__class__.__name__}).") from exc
    if resp.status_code != 200:
        raise ReleaseError(f"GitHub refused ({resp.status_code}) listing {repo()}'s tags.")
    found = sorted((t["name"] for t in resp.json() if TAG.match(t.get("name", ""))), key=_key, reverse=True)
    _tags_cache.update(at=time.time(), tags=found)
    return found


def chosen_version(h: dict, transport=None) -> str:
    """The version this hospital's HQ should run; "" for hold or none yet."""
    mode = h.get("hq_release_mode") or "follow"
    if mode == "hold":
        return ""
    if mode == "pin":
        return h.get("hq_release_version") or ""
    available = tags(transport)
    return available[0] if available else ""


def _cache_dir() -> Path:
    path = Path(os.getenv("HQ_RELEASE_CACHE", Path(tempfile.gettempdir()) / "cirqen-hq-releases"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def archive(version: str, transport=None) -> Path:
    """The release as a .tar.gz, fetched once from GitHub and kept."""
    if not TAG.match(version):
        raise ReleaseError("not a release version")
    path = _cache_dir() / f"hq_server-{version}.tar.gz"
    if path.is_file():
        return path
    try:
        with _client(transport) as client, client.stream("GET", f"/repos/{repo()}/tarball/{version}") as resp:
            if resp.status_code != 200:
                raise ReleaseError(f"GitHub refused ({resp.status_code}) the {version} download.")
            tmp = path.with_suffix(".part")
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_bytes():
                    fh.write(chunk)
    except httpx.HTTPError as exc:
        raise ReleaseError(f"GitHub could not be reached ({exc.__class__.__name__}).") from exc
    tmp.replace(path)
    return path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def signed_release(code: str, version: str, digest: str, signing_key) -> dict:
    document = json.dumps({"type": "cirqen-hq-release", "hospital": code, "version": version, "sha256": digest,
                           "issued_at": datetime.now(timezone.utc).isoformat()},
                          sort_keys=True, separators=(",", ":"))
    return {"document": document,
            "signature": base64.b64encode(signing_key.sign(CONTEXT + document.encode())).decode()}


def download_message(code: str, version: str, timestamp: int) -> bytes:
    return DOWNLOAD_CONTEXT + f"{code}\n{version}\n{timestamp}".encode()


def check_download_signature(code: str, version: str, timestamp: str, signature: str,
                             hq_public_b64: str | None, now: float | None = None) -> str:
    """"" if the hospital's HQ signed this download request just now, else why not."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not hq_public_b64:
        return "this hospital has no HQ identity yet"
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return "missing timestamp"
    if abs((now or time.time()) - ts) > DOWNLOAD_WINDOW:
        return "request too old; check the server's clock"
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(hq_public_b64))
        key.verify(base64.b64decode(signature or ""), download_message(code, version, ts))
    except Exception:  # noqa: BLE001
        return "not signed by this hospital's HQ identity"
    return ""
