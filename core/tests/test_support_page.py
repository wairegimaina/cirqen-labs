"""Settings > Support access: the HOD allows support, sees the code once,
ends it, and sees every access; only with an HQ that is this hospital's."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

import hq_handshake

User = get_user_model()
SYNC = "https://hq-ch0001.example.com/api/sync"


def reply(body, status=200):
    return mock.Mock(status_code=status, json=mock.Mock(return_value=body))


@override_settings(HQ_SYNC_API_URL=SYNC, SYNC_AUTH_TOKEN="k")
class SupportPageTests(TestCase):
    def setUp(self):
        from core import hq_link
        from users.models import UserProfile

        from workshop.models import Workshop

        hq_link._confirmed.update(url="", at=0.0)
        self.workshop = Workshop.objects.create(name="Biomed")
        for name, role in (("hod", "HOD"), ("tech", "Tech")):
            user = User.objects.create_user(name, password="x-pass-123456")
            extra = {"level": "Engineer", "workshop": self.workshop} if role == "Tech" else {}
            UserProfile.objects.update_or_create(user=user, defaults={"role": role, "must_change_password": False,
                                                                      **extra})
        self.client.force_login(User.objects.get(username="hod"))
        config = mock.Mock(get=mock.Mock(return_value="CH0001"))
        patches = [override_settings(CIRQEN_CONFIG=config),
                   mock.patch("core.hq_link.get_client_id", return_value="pc-1"),
                   mock.patch.object(hq_handshake, "confirm", return_value=(True, ""))]
        for p in patches:
            p.start() if hasattr(p, "start") else p.enable()
            self.addCleanup(p.stop if hasattr(p, "stop") else p.disable)

    def test_the_code_is_shown_once(self):
        with mock.patch("requests.request", side_effect=[
                reply({"code": "ABCD-EFGH", "expires_at": "2026-10-01T10:00:00+00:00"}),
                reply({"active": [], "log": []}), reply({"active": [], "log": []})]) as req:
            self.client.post("/settings/support/", {"hours": "2", "reason": "certificates look wrong"})
            first = self.client.get("/settings/support/")
            second = self.client.get("/settings/support/")
        self.assertContains(first, "ABCD-EFGH")
        self.assertNotContains(second, "ABCD-EFGH")
        body = req.call_args_list[0].kwargs["json"]
        self.assertEqual((body["hours"], body["reason"]), (2, "certificates look wrong"))
        self.assertEqual(req.call_args_list[0].kwargs["headers"]["X-Cirqen-Hospital"], "CH0001")

    def test_ending_and_the_log(self):
        log = {"active": [], "log": [{"at": "2026-10-01T07:00:00+00:00", "event": "export_break_glass",
                                      "who": "cirqen support", "reason": "HOD unreachable", "detail": ""}]}
        with mock.patch("requests.request", side_effect=[reply({"ended": 1}), reply(log)]) as req:
            self.client.post("/settings/support/", {"action": "end"})
            page = self.client.get("/settings/support/")
        self.assertTrue(req.call_args_list[0].args[1].endswith("/support/grants/end"))
        self.assertContains(page, "export break glass")
        self.assertContains(page, "HOD unreachable")

    def test_only_the_hod(self):
        self.client.force_login(User.objects.get(username="tech"))
        self.assertNotEqual(self.client.get("/settings/support/").status_code, 200)

    def test_an_hq_that_is_not_this_hospitals_is_not_asked(self):
        with mock.patch.object(hq_handshake, "confirm", return_value=(False, "belongs to CH0002")), \
                mock.patch("requests.request") as req:
            page = self.client.get("/settings/support/")
        req.assert_not_called()
        self.assertContains(page, "has not proved it is this hospital")
