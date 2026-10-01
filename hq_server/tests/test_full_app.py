"""PCs on another runtime get the full app instead of a code package
(desktop_releases.py, runtime_id.py)."""
import base64
import hashlib
import json

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

import desktop_releases
import hq_releases

SIGNER = Ed25519PrivateKey.generate()
PUBLIC = base64.b64encode(SIGNER.public_key().public_bytes(serialization.Encoding.Raw,
                                                           serialization.PublicFormat.Raw)).decode()
ARCHIVE = b"pretend this is Cirqen_linux_v2.0.0.tar.gz" * 1000


def fake_github(published=True, runtime="rt-new"):
    meta = {"version": "2.0.0", "platform": "linux", "runtime_id": runtime, "file": "Cirqen_linux_v2.0.0.tar.gz",
            "sha256": hashlib.sha256(ARCHIVE).hexdigest(), "size": len(ARCHIVE)}

    def handler(request):
        path = request.url.path
        if path.endswith("/releases/tags/desktop-v2.0.0"):
            if not published:
                return httpx.Response(404)
            return httpx.Response(200, json={"assets": [{"id": 1, "name": "Cirqen_linux_v2.0.0.json"},
                                                        {"id": 2, "name": "Cirqen_linux_v2.0.0.tar.gz"}]})
        if path.endswith("/releases/assets/1"):
            return httpx.Response(200, content=json.dumps(meta).encode())
        if path.endswith("/releases/assets/2"):
            return httpx.Response(200, content=ARCHIVE)
        return httpx.Response(404)
    return handler


@pytest.fixture
def server(tmp_path, monkeypatch):
    def setup(**github):
        packages = tmp_path / "packages"
        packages.mkdir(exist_ok=True)
        (packages / "cirqen_update_v2.0.0.json").write_text(json.dumps({
            "version": "2.0.0", "filename": "cirqen_update_v2.0.0.zip", "checksum": "c", "size_bytes": 1,
            "runtime_id": "rt-new", "rollout_percent": 100}))
        (packages / "cirqen_update_v2.0.0.zip").write_bytes(b"zip")
        monkeypatch.setenv("CONTROL_DB", str(tmp_path / "control.db"))
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        monkeypatch.setenv("HQ_SIGNING_PRIVATE_KEY", base64.b64encode(SIGNER.private_bytes(
            serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode())
        real = hq_releases._client
        monkeypatch.setattr(hq_releases, "_client",
                            lambda transport=None: real(httpx.MockTransport(fake_github(**github))))
        desktop_releases._cache.clear()
        import main

        monkeypatch.setattr(main, "PACKAGES_DIR", packages)
        monkeypatch.setattr(main, "API_KEY", "k")
        return TestClient(main.app)
    return setup


def latest(client, **params):
    return client.get("/api/updates/latest/", params={"current_version": "1.9.0", **params},
                      headers={"X-Api-Key": "k"}).json()


def test_same_runtime_gets_the_code_package(server):
    answer = latest(server(), runtime_id="rt-new")
    assert answer["update_available"] and "full_required" not in answer
    assert "/api/updates/download/2.0.0/" in answer["download_url"]


def test_a_pc_that_does_not_say_its_runtime_is_unchanged(server):
    assert "full_required" not in latest(server())


def test_another_runtime_gets_the_signed_full_app_and_can_download_it(server):
    client = server()
    answer = latest(client, runtime_id="rt-old")
    assert answer["full_required"] is True
    offer = answer["full_package"]
    package = desktop_releases.verify_offer(offer, PUBLIC)
    assert (package["version"], package["runtime_id"]) == ("2.0.0", "rt-new")
    got = client.get("/api/updates/full/2.0.0/linux/", headers={"X-Api-Key": "k"})
    assert got.status_code == 200 and hashlib.sha256(got.content).hexdigest() == package["sha256"]
    assert client.get("/api/updates/full/2.0.0/linux/").status_code in (401, 403)       # needs the key


def test_until_the_full_app_is_published_no_code_package_is_offered(server):
    answer = latest(server(published=False), runtime_id="rt-old")
    assert answer["full_required"] is True and answer["full_package"] is None
    assert "isn't published" in answer["message"] and "download_url" not in answer


def test_a_full_app_built_for_another_runtime_is_not_offered(server):
    answer = latest(server(runtime="rt-other"), runtime_id="rt-old")
    assert answer["full_package"] is None
