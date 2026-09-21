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
