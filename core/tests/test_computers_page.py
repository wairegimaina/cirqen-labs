"""Settings > Computers: the HOD approves the PCs waiting, and a waiting PC says so."""
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

import hq_handshake

User = get_user_model()


def reply(body, status=200):
    return mock.Mock(status_code=status, json=mock.Mock(return_value=body))


@override_settings(HQ_SYNC_API_URL="https://hq-ch0001.example.com/api/sync", SYNC_AUTH_TOKEN="k")
class ComputersPageTests(TestCase):
    def setUp(self):
        from core import hq_link
        from users.models import UserProfile

        hq_link._confirmed.update(url="", at=0.0)
        user = User.objects.create_user("hod", password="x-pass-123456")
        UserProfile.objects.update_or_create(user=user, defaults={"role": "HOD", "must_change_password": False})
        self.client.force_login(user)
        patches = [override_settings(CIRQEN_CONFIG=mock.Mock(get=mock.Mock(return_value="CH0001"))),
                   mock.patch("core.hq_link.get_client_id", return_value="pc-hod"),
                   mock.patch.object(hq_handshake, "confirm", return_value=(True, ""))]
        for p in patches:
            p.start() if hasattr(p, "start") else p.enable()
            self.addCleanup(p.stop if hasattr(p, "stop") else p.disable)

    def test_waiting_and_approved_pcs_are_listed(self):
        devices = {"devices": [
            {"device_id": "pc-ward", "device_name": "Ward 5", "joined_at": "2026-10-01T07:00:00+00:00",
             "pending_approval": True},
            {"device_id": "pc-hod", "device_name": "HOD office", "joined_at": "2026-09-01T07:00:00+00:00",
             "pending_approval": False}]}
        with mock.patch("requests.request", return_value=reply(devices)):
            page = self.client.get("/settings/computers/")
        self.assertContains(page, "Waiting for your approval (1)")
        self.assertContains(page, "Ward 5")
        self.assertContains(page, "(this computer)")

    def test_approving_sends_the_pc_and_who(self):
        with mock.patch("requests.request", side_effect=[reply({"status": "approved"}), reply({"devices": []})]) as req:
            self.client.post("/settings/computers/", {"action": "approve", "device_id": "pc-ward"}, follow=True)
        call = req.call_args_list[0]
        self.assertTrue(call.args[1].endswith("/devices/approve"))
        self.assertEqual(call.kwargs["json"]["device_id"], "pc-ward")


class WaitingNoticeTests(TestCase):
    def test_a_waiting_pc_says_so_on_every_page(self):
        from users.models import UserProfile

        user = User.objects.create_user("hod", password="x-pass-123456")
        UserProfile.objects.update_or_create(user=user, defaults={"role": "HOD", "must_change_password": False})
        self.client.force_login(user)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "device_status.json").write_text(json.dumps({"pending_approval": True}))
            with override_settings(DATA_PATH=Path(tmp)):
                self.assertContains(self.client.get("/settings/subscription/"), "waiting for the head of department")
                (Path(tmp) / "device_status.json").write_text(json.dumps({"pending_approval": False}))
                self.assertNotContains(self.client.get("/settings/subscription/"), "waiting for the head of department")
