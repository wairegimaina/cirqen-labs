"""A module the hospital's profile switches off is closed, not only hidden;
without a profile every module is on. The profile is taken only from
Control, only for this hospital, and never replaced by an older one."""
import base64
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

import hospital_profile
from core.tests.test_endpoint_sync import make_keypair

User = get_user_model()


def signed(key, *, hospital="CH0001", version=1, modules=None, labels=None):
    raw = json.dumps({"type": "cirqen-hospital-profile", "v": 1, "hospital": hospital,
                      "profile_version": version, "modules": modules or {}, "labels": labels or {}},
                     sort_keys=True)
    return {"document": raw, "signature": base64.b64encode(key.sign(hospital_profile.CONTEXT + raw.encode())).decode()}


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
        (self.data / hospital_profile.FILE_NAME).write_text(json.dumps(signed(self.key, **kwargs)))

    def test_without_a_profile_every_module_is_on(self):
        self.assertNotEqual(self.client.get("/calibration/").status_code, 404)
        self.assertTrue(all(s["on"] for s in hospital_profile.module_states(None).values()))

    def test_a_switched_off_module_is_closed_including_typed_addresses(self):
        self.publish(modules={"calibration": False})
        for path in ("/calibration/", "/calSchedules/anything/"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 404)
            self.assertContains(resp, "not enabled for your hospital", status_code=404)
        self.assertNotEqual(self.client.get("/Inventory/").status_code, 404)

    def test_the_menu_hides_it_and_uses_the_hospitals_labels(self):
        self.publish(modules={"parts_tools": False}, labels={"inventory": "Equipment register"})
        page = self.client.get("/dashboard/", follow=True).content.decode()
        self.assertIn("Equipment register", page)
        self.assertNotIn("Parts &amp; Tools", page)

    def test_the_home_address_avoids_a_switched_off_module(self):
        self.publish(modules={"machine_reports": False})
        resp = self.client.get("/")
        self.assertEqual((resp.status_code, resp["Location"]), (302, "/dashboard/"))


class FetchTests(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.key, public = make_keypair()
        trust = override_settings(UPDATE_SYSTEM={"public_key": public})
        trust.enable()
        self.addCleanup(trust.disable)

    def fetch(self, payload, status=200):
        session = mock.Mock(get=mock.Mock(return_value=mock.Mock(status_code=status, json=mock.Mock(return_value=payload))))
        return hospital_profile.fetch_and_store(self.data, "https://updates.example.com", "CH0001", session=session)

    def test_its_own_profile_is_kept(self):
        self.assertEqual(self.fetch(signed(self.key, version=3)), "saved")
        self.assertEqual(hospital_profile.load(self.data)["profile_version"], 3)

    def test_an_older_profile_does_not_replace_a_newer_one(self):
        self.fetch(signed(self.key, version=3, modules={"calibration": False}))
        self.assertIn("older", self.fetch(signed(self.key, version=2)))
        self.assertFalse(hospital_profile.load(self.data)["modules"]["calibration"])

    def test_another_hospitals_or_an_unsigned_profile_is_refused(self):
        self.assertIn("not CH0001", self.fetch(signed(self.key, hospital="CH0002")))
        other, _ = make_keypair()
        self.assertIn("not signed by Cirqen Control", self.fetch(signed(other)))
        self.assertIsNone(hospital_profile.load(self.data))
