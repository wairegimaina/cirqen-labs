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

    def test_pcs_still_on_the_old_shared_key_are_listed(self):
        devices = {"devices": [], "on_shared_key": [{"client_id": "pc-lab", "last_used": "2026-10-01T07:00:00+00:00"}]}
        with mock.patch("requests.request", return_value=reply(devices)):
            page = self.client.get("/settings/computers/")
        self.assertContains(page, "Still on the old shared key (1)")
        self.assertContains(page, "pc-lab")

    def test_a_key_swapped_by_the_sync_agent_is_used_without_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "config.json"
            config_file.write_text(json.dumps({"sync": {"auth_token": "own-key"}}))
            config = mock.Mock(get=mock.Mock(return_value="CH0001"), config_file=config_file)
            with override_settings(CIRQEN_CONFIG=config), \
                    mock.patch("requests.request", return_value=reply({"devices": []})) as req:
                self.client.get("/settings/computers/")
        self.assertEqual(req.call_args.kwargs["headers"]["X-API-Key"], "own-key")   # not settings' "k"

    def test_the_licence_limit_is_shown_and_its_refusal_explained(self):
        listing = {"devices": [], "licence_limit": 3}
        with mock.patch("requests.request", return_value=reply(listing)):
            self.assertContains(self.client.get("/settings/computers/"), "of the 3 your licence allows")
        refusal = reply({"error": "licence_full", "detail": "This hospital's licence allows 3 computers"}, 403)
        with mock.patch("requests.request", side_effect=[refusal, reply(listing)]):
            page = self.client.post("/settings/computers/", {"action": "approve", "device_id": "pc-x"}, follow=True)
        self.assertContains(page, "licence allows 3 computers")


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
