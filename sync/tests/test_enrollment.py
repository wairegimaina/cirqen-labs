"""Client enrollment with HQ (IMPROVEMENT_PLAN.md 3.3)."""
from unittest import mock

import requests

from sync import enrollment


class FakeConfig:
    def __init__(self, **values):
        self.values = {"client.name": "Ward 5", **values}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def _response(status, body):
    resp = mock.Mock(status_code=status, text=str(body))
    resp.json.return_value = body
    return resp


def test_existing_key_is_used_without_calling_hq():
    session = mock.Mock()
    cfg = FakeConfig(**{"sync.auth_token": "have-one", "sync.enrollment_code": "code"})
    with mock.patch.object(enrollment, "enroll") as enroll:
        assert enrollment.ensure_client_key(cfg, "https://hq", "dev-1") == "have-one"
    enroll.assert_not_called()
    session.post.assert_not_called()


def test_code_is_exchanged_for_a_key_and_cleared():
    cfg = FakeConfig(**{"sync.enrollment_code": "code-one"})
    with mock.patch("requests.post", return_value=_response(200, {"api_key": "issued"})) as post:
        assert enrollment.ensure_client_key(cfg, "https://hq/", "dev-1") == "issued"
    assert post.call_args.args[0] == "https://hq/api/sync/enroll"
    assert post.call_args.kwargs["json"]["client_id"] == "dev-1"
    assert cfg.get("sync.auth_token") == "issued"
    assert cfg.get("sync.enrollment_code") == ""


def test_hq_unreachable_keeps_the_code_for_next_start():
    cfg = FakeConfig(**{"sync.enrollment_code": "code-one"})
    with mock.patch("requests.post", side_effect=requests.ConnectionError("offline")):
        assert enrollment.ensure_client_key(cfg, "https://hq", "dev-1") is None
    assert cfg.get("sync.enrollment_code") == "code-one"


def test_refusal_does_not_store_anything():
    cfg = FakeConfig(**{"sync.enrollment_code": "code-one"})
    with mock.patch("requests.post", return_value=_response(409, {"error": "already"})):
        assert enrollment.ensure_client_key(cfg, "https://hq", "dev-1") is None
    assert not cfg.get("sync.auth_token")


def test_with_a_hospital_code_the_token_goes_only_to_a_confirmed_hq():
    import hq_handshake

    cfg = FakeConfig(**{"sync.enrollment_code": "cqe1.x.y", "sync.hospital_code": "CH0001"})
    with mock.patch.object(hq_handshake, "confirm", return_value=(False, "belongs to CH0002")) as confirm, \
            mock.patch("requests.post") as post:
        assert enrollment.ensure_client_key(cfg, "https://hq/", "dev-1") is None
    confirm.assert_called_once_with("https://hq/api/sync", "CH0001")
    post.assert_not_called()
    assert cfg.get("sync.enrollment_code") == "cqe1.x.y"

    with mock.patch.object(hq_handshake, "confirm", return_value=(True, "")), \
            mock.patch("requests.post", return_value=_response(200, {"api_key": "issued"})):
        assert enrollment.ensure_client_key(cfg, "https://hq/", "dev-1") == "issued"


def test_the_used_token_is_removed_from_provisioning(tmp_path, monkeypatch):
    import json

    import config

    prov = tmp_path / "provisioning.json"
    prov.write_text(json.dumps({"sync": {"hospital_code": "CH0001", "enrollment_code": "cqe1.a.b",
                                         "api_url": "https://hq/api/sync"}}))
    monkeypatch.setattr(config, "_provisioning_candidates_for", lambda data_path: [prov])
    cfg = FakeConfig(**{"sync.enrollment_code": "cqe1.a.b"})
    cfg.data_path = tmp_path
    with mock.patch("requests.post", return_value=_response(200, {"api_key": "issued"})):
        assert enrollment.ensure_client_key(cfg, "https://hq", "dev-1") == "issued"
    data = json.loads(prov.read_text())
    assert data["sync"] == {"hospital_code": "CH0001", "enrollment_code": "", "api_url": "https://hq/api/sync"}


# ── swapping the old shared key for this PC's own ────────────────────────────

def test_a_pc_on_the_shared_key_swaps_it_once(tmp_path):
    prov = tmp_path / "provisioning.json"
    prov.write_text('{"sync": {"auth_token": "shared", "hospital_code": "CH0001"}}')
    cfg = FakeConfig(**{"sync.auth_token": "shared", "sync.hospital_code": "ch0001"})
    cfg.data_path = tmp_path
    with mock.patch("requests.post", return_value=_response(200, {"api_key": "own"})) as post:
        assert enrollment.ensure_own_key(cfg, "https://hq/", "dev-1") == "own"
    assert post.call_args.args[0] == "https://hq/api/sync/rekey"
    headers = post.call_args.kwargs["headers"]
    assert (headers["X-API-Key"], headers["X-Client-ID"], headers["X-Cirqen-Hospital"]) == ("shared", "dev-1", "CH0001")
    assert headers["User-Agent"].startswith("CMMS-Sync-Agent")
    assert (cfg.get("sync.auth_token"), cfg.get("sync.key_own")) == ("own", True)
    assert '"auth_token": ""' in prov.read_text()                      # the shared key is gone from the file
    with mock.patch("requests.post") as again:
        assert enrollment.ensure_own_key(cfg, "https://hq/", "dev-1") is None
    again.assert_not_called()


def test_a_pc_with_its_own_key_is_marked_and_left_alone():
    cfg = FakeConfig(**{"sync.auth_token": "already-own"})
    with mock.patch("requests.post", return_value=_response(400, {"error": "own key"})):
        assert enrollment.ensure_own_key(cfg, "https://hq", "dev-1") is None
    assert (cfg.get("sync.auth_token"), cfg.get("sync.key_own")) == ("already-own", True)


def test_an_older_hq_or_no_network_means_try_again_next_start():
    for outcome in (_response(404, {}), requests.ConnectionError("offline"), _response(409, {"error": "x"})):
        cfg = FakeConfig(**{"sync.auth_token": "shared"})
        kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
        with mock.patch("requests.post", **kwargs):
            assert enrollment.ensure_own_key(cfg, "https://hq", "dev-1") is None
        assert (cfg.get("sync.auth_token"), cfg.get("sync.key_own")) == ("shared", None)


def test_an_enrolled_pc_never_asks_to_swap():
    cfg = FakeConfig(**{"sync.enrollment_code": "code-one"})
    with mock.patch("requests.post", return_value=_response(200, {"api_key": "issued"})):
        enrollment.ensure_client_key(cfg, "https://hq", "dev-1")
    assert cfg.get("sync.key_own") is True


def test_a_swapped_key_replaces_one_from_the_environment(tmp_path, monkeypatch):
    from config import CirqenConfig

    monkeypatch.setenv("SYNC_AUTH_TOKEN", "shared")
    cfg = CirqenConfig(tmp_path, use_env_file=False)
    assert cfg.get("sync.auth_token") == "shared"
    cfg.replace_secret("sync.auth_token", "own")
    import json
    import os

    assert json.loads((tmp_path / "config.json").read_text())["sync"]["auth_token"] == "own"
    assert os.environ["SYNC_AUTH_TOKEN"] == "own"
