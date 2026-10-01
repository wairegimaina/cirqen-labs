"""The full desktop app, for PCs whose runtime changed (runtime_id.py).

A code package replaces Cirqen's .py files; it can't replace the Python, the
libraries or the embedded databases inside an installed app. When the
version a PC is offered has a different runtime id from the PC's, Control
offers the full app instead: the neutral build that `build.py --publish`
uploaded to the GitHub release desktop-v<version> of the cirqen-labs
repository, with Cirqen_<platform>_v<version>.json describing it.

Control reads the release with GITHUB_TOKEN (read access to the repository),
the same one as for self-hosted HQs, and streams the archive to PCs; PCs
never hold GitHub credentials. What it offers is signed with the update
signing key (CONTEXT), so a PC installs only an app Control vouched for,
checked against its sha256.
"""
from __future__ import annotations

import base64
import json
import os
import time

import hq_releases

CONTEXT = b"cirqen-full-app-v1\n"
CACHE_SECONDS = 600
PLATFORMS = ("linux", "windows")

_cache: dict[tuple[str, str], tuple[float, dict | None]] = {}


def repo() -> str:
    return os.getenv("DESKTOP_REPO", "wairegimaina/cirqen-labs").strip()


def _assets(version: str, transport=None) -> dict[str, dict]:
    with hq_releases._client(transport) as client:
        resp = client.get(f"/repos/{repo()}/releases/tags/desktop-v{version}")
    if resp.status_code == 404:
        return {}
    if resp.status_code != 200:
        raise hq_releases.ReleaseError(f"GitHub refused ({resp.status_code}) the desktop-v{version} release.")
    return {a["name"]: a for a in resp.json().get("assets", [])}


def full_package(version: str, platform: str, transport=None) -> dict | None:
    """{"version", "platform", "runtime_id", "file", "sha256", "size", "asset_id"} or None."""
    if platform not in PLATFORMS:
        return None
    key = (version, platform)
    cached = _cache.get(key)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]
    assets = _assets(version, transport)
    meta_asset = assets.get(f"Cirqen_{platform}_v{version}.json")
    found = None
    if meta_asset:
        with hq_releases._client(transport) as client:
            resp = client.get(f"/repos/{repo()}/releases/assets/{meta_asset['id']}",
                              headers={"Accept": "application/octet-stream"})
        if resp.status_code == 200:
            meta = json.loads(resp.content)
            archive = assets.get(meta.get("file", ""))
            if archive and meta.get("version") == version and meta.get("platform") == platform:
                found = {**{k: meta[k] for k in ("version", "platform", "runtime_id", "file", "sha256", "size")},
                         "asset_id": archive["id"]}
    _cache[key] = (time.time(), found)
    return found


def offer_document(package: dict) -> str:
    return json.dumps({k: package[k] for k in ("version", "platform", "runtime_id", "file", "sha256", "size")},
                      sort_keys=True, separators=(",", ":"))


def signed_offer(package: dict, download_url: str, sign) -> dict:
    """What /api/updates/latest/ tells the PC: the signed description plus
    where to download it. sign(bytes) -> base64 signature or None."""
    document = offer_document(package)
    signature = sign(CONTEXT + document.encode())
    return {"document": document, "signature": signature, "download_url": download_url}


def stream(version: str, platform: str, transport=None):
    """(iterator of bytes, size, file name) for the archive, from GitHub."""
    package = full_package(version, platform, transport)
    if package is None:
        raise hq_releases.ReleaseError("no full app published for that version and platform")
    client = hq_releases._client(transport)
    request = client.build_request("GET", f"/repos/{repo()}/releases/assets/{package['asset_id']}",
                                   headers={"Accept": "application/octet-stream"})
    resp = client.send(request, stream=True)
    if resp.status_code != 200:
        resp.close()
        client.close()
        raise hq_releases.ReleaseError(f"GitHub refused ({resp.status_code}) the full app download.")

    def chunks():
        try:
            yield from resp.iter_bytes(1 << 20)
        finally:
            resp.close()
            client.close()

    return chunks(), package["size"], package["file"]


def verify_offer(offer: dict, public_key_b64: str) -> dict:
    """For tests and the PC: the offered package if Control signed it."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    key.verify(base64.b64decode(offer["signature"]), CONTEXT + offer["document"].encode())
    return json.loads(offer["document"])
