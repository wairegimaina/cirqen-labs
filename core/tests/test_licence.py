"""The hospital's licence on its PCs: banner, then read-only, never locked
out; nothing enforced until a licence is published."""
import base64
import json
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

import licence
from core.tests.test_endpoint_sync import make_keypair

User = get_user_model()


def signed(key, *, hospital="CH0001", version=1, ends_on="2026-10-31", grace_days=14, status="active"):
    raw = json.dumps({"type": "cirqen-licence", "v": 1, "hospital": hospital, "plan": "standard", "devices": 10,
                      "starts_on": "2025-11-01", "ends_on": ends_on, "grace_days": grace_days, "status": status,
                      "licence_version": version}, sort_keys=True)
    return {"document": raw, "signature": base64.b64encode(key.sign(licence.CONTEXT + raw.encode())).decode()}


class StateTests(TestCase):
    def state(self, today, **kwargs):
        key, _ = make_keypair()
        return licence.state(json.loads(signed(key, **kwargs)["document"]), today=today)["state"]

    def test_the_states_over_time(self):
        self.assertEqual(self.state(date(2026, 9, 1)), "active")
        self.assertEqual(self.state(date(2026, 10, 2)), "due")
        self.assertEqual(self.state(date(2026, 10, 31)), "due")
        self.assertEqual(self.state(date(2026, 11, 14)), "grace")
        self.assertEqual(self.state(date(2026, 11, 15)), "read_only")
        self.assertEqual(self.state(date(2026, 9, 1), status="suspended"), "read_only")
        self.assertEqual(licence.state(None)["state"], "none")


class GateTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        patch = override_settings(DATA_PATH=self.data)
        patch.enable()
        self.addCleanup(patch.disable)
        from users.models import UserProfile

        self.user = User.objects.create_user("hod", password="x-pass-123456")
        UserProfile.objects.update_or_create(user=self.user, defaults={"role": "HOD", "must_change_password": False})
        self.client.force_login(self.user)
        self.key, _ = make_keypair()

    def publish(self, **kwargs):
        (self.data / licence.FILE_NAME).write_text(json.dumps(signed(self.key, **kwargs)))

    def test_no_licence_means_nothing_is_enforced(self):
        self.assertNotEqual(self.client.post("/Inventory/", {}).status_code, 403)
        self.assertNotContains(self.client.get("/settings/subscription/"), "Read-only from")

    def test_read_only_blocks_saving_but_not_viewing(self):
        self.publish(ends_on="2020-01-31")
        resp = self.client.post("/Inventory/", {})
        self.assertContains(resp, "Nothing new can be saved", status_code=403)
        api = self.client.post("/jobcard/x/", data="{}", content_type="application/json")
        self.assertEqual((api.status_code, api.json()["error"]), (403, "licence_read_only"))
        self.assertEqual(self.client.get("/settings/subscription/").status_code, 200)
        page = self.client.get("/settings/subscription/")
        self.assertContains(page, "Ended: read-only")
        self.assertContains(page, "Renew")                     # the banner, for the HOD

    def test_signing_out_and_the_subscription_page_still_work(self):
        self.publish(ends_on="2020-01-31")
        self.assertNotEqual(self.client.post("/login/logout/").status_code, 403)

    def test_check_now_loads_a_renewal(self):
        self.publish(ends_on="2020-01-31")
        renewed = signed(self.key, version=2, ends_on="2030-01-31")
        _, public = make_keypair()
        response = mock.Mock(status_code=200, json=mock.Mock(return_value=renewed))
        config = mock.Mock(get=mock.Mock(side_effect=lambda k, d=None: {"update.server_url": "https://u.example",
                                                                        "sync.hospital_code": "CH0001"}.get(k, d)))
        with override_settings(CIRQEN_CONFIG=config), \
                mock.patch("requests.get", return_value=response), \
                mock.patch("endpoint_sync._public_key", return_value=self.key.public_key()):
            self.client.post("/settings/subscription/")
        self.assertEqual(licence.load(self.data)["ends_on"], "2030-01-31")
        self.assertNotEqual(self.client.post("/Inventory/", {}).status_code, 403)


class FetchTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.key, public = make_keypair()
        trust = override_settings(UPDATE_SYSTEM={"public_key": public})
        trust.enable()
        self.addCleanup(trust.disable)

    def fetch(self, payload):
        session = mock.Mock(get=mock.Mock(return_value=mock.Mock(status_code=200, json=mock.Mock(return_value=payload))))
        return licence.fetch_and_store(self.data, "https://u.example", "CH0001", session=session)

    def test_only_its_own_signed_licence_and_never_an_older_one(self):
        self.assertEqual(self.fetch(signed(self.key, version=2)), "saved")
        self.assertIn("older", self.fetch(signed(self.key, version=1, ends_on="2099-01-01")))
        self.assertIn("not CH0001", self.fetch(signed(self.key, version=3, hospital="CH0002")))
        other, _ = make_keypair()
        self.assertIn("not signed", self.fetch(signed(other, version=4)))
        self.assertEqual(licence.load(self.data)["licence_version"], 2)


class ClockTests(TestCase):
    """The date a PC judges its licence by can't be wound back."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.key, public = make_keypair()
        trust = override_settings(UPDATE_SYSTEM={"public_key": public})
        trust.enable()
        self.addCleanup(trust.disable)

    def at(self, day):
        return datetime.fromisoformat(f"{day}T09:00:00+03:00")

    def fetch(self, payload, status=200):
        reply = mock.Mock(status_code=status, json=mock.Mock(return_value=payload))
        return licence.fetch_and_store(self.data, "https://u.example", "CH0001",
                                       session=mock.Mock(get=mock.Mock(return_value=reply)))

    def with_issued_at(self, when, **kwargs):
        doc = json.loads(signed(self.key, **kwargs)["document"])
        doc["issued_at"] = when.isoformat()
        raw = json.dumps(doc, sort_keys=True)
        return {"document": raw, "signature": base64.b64encode(self.key.sign(licence.CONTEXT + raw.encode())).decode()}

    def test_turning_the_clock_back_does_not_undo_an_ended_licence(self):
        self.fetch(signed(self.key, ends_on="2026-10-31", grace_days=0))
        self.assertEqual(licence.current(self.data, now=self.at("2026-11-02"))["state"], "read_only")
        self.assertEqual(licence.current(self.data, now=self.at("2026-10-01"))["state"], "read_only")

    def test_control_time_heals_a_clock_that_ran_ahead(self):
        self.fetch(signed(self.key, ends_on="2026-10-31", grace_days=0))
        self.assertEqual(licence.current(self.data, now=self.at("2030-01-01"))["state"], "read_only")
        control_now = self.at("2026-10-01")
        self.fetch(self.with_issued_at(control_now, ends_on="2026-10-31", grace_days=0))   # unchanged version
        self.assertEqual(licence.current(self.data, now=control_now + timedelta(minutes=5))["state"], "active")

    def test_a_deleted_licence_file_means_read_only_until_fetched_again(self):
        self.fetch(signed(self.key, ends_on="2099-01-01"))
        (self.data / licence.FILE_NAME).unlink()
        self.assertEqual(licence.current(self.data), {"state": "read_only", "missing": True})
        self.fetch(signed(self.key, ends_on="2099-01-01"))
        self.assertEqual(licence.current(self.data)["state"], "active")

    def test_if_control_has_no_licence_any_more_nothing_is_enforced(self):
        self.fetch(signed(self.key, ends_on="2099-01-01"))
        (self.data / licence.FILE_NAME).unlink()
        self.fetch({}, status=404)
        self.assertEqual(licence.current(self.data)["state"], "none")

    def test_a_pc_that_never_had_a_licence_is_not_affected(self):
        self.assertEqual(licence.current(self.data)["state"], "none")
