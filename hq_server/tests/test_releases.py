"""Each hospital's PCs are offered the release chosen for that hospital."""
import json

import pytest
from fastapi.testclient import TestClient

import control_store as cs
import releases

METAS = [{"version": v, "checksum": "x", "size_bytes": 1} for v in ("1.7.0", "1.8.0", "1.9.0")]


@pytest.mark.parametrize("policy, current, expected", [
    ({"mode": "follow", "version": ""}, "1.7.0", "1.9.0"),
    ({"mode": "pin", "version": "1.8.0"}, "1.7.0", "1.8.0"),
    ({"mode": "pin", "version": "1.8.0"}, "1.9.0", None),      # never moved back
    ({"mode": "hold", "version": ""}, "1.7.0", None),
    ({"mode": "pin", "version": "2.0.0"}, "1.7.0", None),      # not built
])
def test_the_choice(policy, current, expected):
    chosen, _ = releases.choose(METAS, current, policy)
    assert (chosen or {}).get("version") == expected


def test_a_yanked_release_is_never_offered():
    metas = [*METAS[:2], {**METAS[2], "yanked": True}]
    chosen, _ = releases.choose(metas, "1.7.0", {"mode": "follow", "version": ""})
    assert chosen["version"] == "1.8.0"


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("CONTROL_DB", str(tmp_path / "control.db"))
    packages = tmp_path / "packages"
    packages.mkdir()
    for meta in METAS:
        (packages / f"cirqen_update_v{meta['version']}.json").write_text(json.dumps(meta))
    import main

    monkeypatch.setattr(main, "PACKAGES_DIR", packages)
    monkeypatch.setattr(main, "API_KEY", "k")
    cs.init()
    for code, mode, version in (("CH0001", "pin", "1.8.0"), ("CH0002", "hold", "")):
        cs.save_hospital(code, {"name": code, "release_mode": mode, "release_version": version}, create=True)
    return TestClient(main.app)


def latest(client, **params):
    return client.get("/api/updates/latest/", params={"current_version": "1.7.0", **params},
                      headers={"X-Api-Key": "k"}).json()


def test_each_hospital_gets_its_own_release(server):
    assert latest(server, hospital_code="ch0001")["version"] == "1.8.0"
    held = latest(server, hospital_code="CH0002")
    assert held["update_available"] is False and "on hold" in held["message"]
    assert latest(server)["version"] == "1.9.0"                      # no hospital code: newest
    assert latest(server, hospital_code="CH0009")["version"] == "1.9.0"  # unknown: newest
