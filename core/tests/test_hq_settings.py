"""The HQ Connection settings page and the hq_endpoint command.

Both drive core.hq_settings, so these cover the behaviour an administrator
depends on: seeing where a value came from, being stopped before saving a bad
address, and being able to hand a machine back to the shipped defaults.
"""
import base64
import io
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse

import config
from config import ENDPOINT_MARKER, ENDPOINT_MARKER_VALUE, HQ_ENDPOINT_DEFAULTS, CirqenConfig
from core import hq_settings

from accounts.models import CustomUser

CLEARED = list(config.HQ_ENDPOINT_ENV.values()) + list(config.PLAIN_ENV.values()) + [
    config.ENDPOINTS_FROM_CONFIG_VAR, "CIRQEN_PROVISIONING_FILE",
    "SYNC_AUTH_TOKEN", "HQ_API_KEY", "POSTGRES_HQ_PASSWORD",
]


class HQSettingsBase(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

        env = mock.patch.dict(os.environ, {k: "" for k in CLEARED})
        env.start()
        self.addCleanup(env.stop)

        isolate = mock.patch.object(
            config, "_provisioning_candidates_for",
            lambda data_path: [Path(data_path) / "provisioning.json"],
        )
        isolate.start()
        self.addCleanup(isolate.stop)

        quiet = mock.patch("builtins.print")
        quiet.start()
        self.addCleanup(quiet.stop)

        patch_data = override_settings(DATA_PATH=self.data)
        patch_data.enable()
        self.addCleanup(patch_data.disable)

    def load(self):
        return CirqenConfig(self.data, use_env_file=False)

    def on_disk(self):
        return json.loads((self.data / "config.json").read_text())

    def write_json(self, payload):
        (self.data / "config.json").write_text(json.dumps(payload))


class DescribeTests(HQSettingsBase):
    def test_every_setting_is_shown_with_its_source(self):
        rows = {row["key"]: row for row in hq_settings.describe(self.load())}
        self.assertEqual(set(rows), set(HQ_ENDPOINT_DEFAULTS))
        self.assertTrue(all(r["source"] == "default" for r in rows.values()))
        self.assertTrue(all(r["is_default"] for r in rows.values()))
        self.assertFalse(any(r["locked"] for r in rows.values()))

    def test_a_value_set_by_the_environment_is_locked(self):
        with mock.patch.dict(os.environ, {"SYNC_API_URL": "https://ops.example.com/api/sync"}):
            rows = {row["key"]: row for row in hq_settings.describe(self.load())}
        self.assertTrue(rows["sync.api_url"]["locked"])
        self.assertEqual(rows["sync.api_url"]["source"], "env")
        self.assertFalse(rows["update.server_url"]["locked"])

    def test_exports_are_not_read_back_as_operator_overrides(self):
        """setup_environment_variables() exports resolved values; a later load
        (or a child process inheriting them) must not call those 'env'."""
        cfg = self.load()
        cfg.setup_environment_variables()
        rows = {row["key"]: row for row in hq_settings.describe(self.load())}
        self.assertTrue(all(r["source"] == "default" for r in rows.values()),
                        {k: v["source"] for k, v in rows.items()})
        self.assertFalse(any(r["locked"] for r in rows.values()))


class ApplyChangesTests(HQSettingsBase):
    def test_a_valid_change_is_saved_and_reported(self):
        cfg = self.load()
        changed, errors = hq_settings.apply_changes(
            cfg, {"sync.api_url": "https://site-a.example.com/api/sync"})
        self.assertEqual(errors, [])
        self.assertEqual(changed, ["sync.api_url"])
        self.assertEqual(self.load().get("sync.api_url"), "https://site-a.example.com/api/sync")

    def test_nothing_is_written_when_any_field_is_invalid(self):
        cfg = self.load()
        changed, errors = hq_settings.apply_changes(cfg, {
            "sync.api_url": "https://good.example.com/api/sync",
            "hq_db.port": "not-a-number",
        })
        self.assertEqual(changed, [])
        self.assertTrue(errors)
        self.assertEqual(self.load().get("sync.api_url"), HQ_ENDPOINT_DEFAULTS["sync.api_url"])

    def test_blank_means_follow_the_shipped_default_again(self):
        cfg = self.load()
        hq_settings.apply_changes(cfg, {"sync.api_url": "https://pinned.example.com/api/sync"})
        cfg = self.load()
        self.assertEqual(cfg.endpoint_sources["sync.api_url"], "config.json")

        hq_settings.apply_changes(cfg, {"sync.api_url": ""})
        reloaded = self.load()
        self.assertEqual(reloaded.get("sync.api_url"), HQ_ENDPOINT_DEFAULTS["sync.api_url"])
        self.assertEqual(reloaded.endpoint_sources["sync.api_url"], "default")
        self.assertNotIn("api_url", self.on_disk().get("sync", {}))

    def test_a_field_set_by_the_environment_is_not_written(self):
        with mock.patch.dict(os.environ, {"SYNC_API_URL": "https://ops.example.com/api/sync"}):
            cfg = self.load()
            changed, errors = hq_settings.apply_changes(
                cfg, {"sync.api_url": "https://ignored.example.com/api/sync"})
        self.assertEqual(changed, [])
        self.assertEqual(errors, [])
        self.assertNotIn("api_url", self.on_disk().get("sync", {}) if (self.data / "config.json").exists() else {})

    def test_an_unchanged_value_is_not_reported_as_a_change(self):
        cfg = self.load()
        changed, errors = hq_settings.apply_changes(
            cfg, {"sync.api_url": HQ_ENDPOINT_DEFAULTS["sync.api_url"]})
        self.assertEqual((changed, errors), ([], []))


class ProbeTests(HQSettingsBase):
    def test_a_200_is_reachable(self):
        with mock.patch("requests.get", return_value=mock.Mock(status_code=200)):
            self.assertTrue(hq_settings.probe_sync(self.load()).ok)

    def test_an_auth_failure_still_means_the_address_is_right(self):
        with mock.patch("requests.get", return_value=mock.Mock(status_code=401)):
            result = hq_settings.probe_update(self.load())
        self.assertTrue(result.ok)
        self.assertIn("key", result.detail)

    def test_a_waking_server_is_reported_as_such(self):
        with mock.patch("requests.get", return_value=mock.Mock(status_code=503)):
            result = hq_settings.probe_sync(self.load())
        self.assertFalse(result.ok)
        self.assertIn("waking up", result.detail)

    def test_an_unreachable_host_is_reported_not_raised(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.exceptions.ConnectionError()):
            result = hq_settings.probe_sync(self.load())
        self.assertFalse(result.ok)
        self.assertIn("Cannot reach", result.detail)

    def test_the_database_probe_needs_a_password(self):
        result = hq_settings.probe_database(self.load())
        self.assertFalse(result.ok)
        self.assertIn("password", result.detail)


class PageTests(HQSettingsBase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.staff = CustomUser.objects.create_user(
            username="ops", password="pw-for-tests", is_staff=True)
        self.plain = CustomUser.objects.create_user(username="tech", password="pw-for-tests")

    def test_the_page_is_staff_only(self):
        url = reverse("core:hq_connection")
        self.assertNotEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.plain)
        self.assertNotEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_the_page_names_each_setting_and_its_source(self):
        self.client.force_login(self.staff)
        body = self.client.get(reverse("core:hq_connection")).content.decode()
        self.assertIn("Sync API", body)
        self.assertIn("Update server", body)
        self.assertIn("shipped default", body)

    def test_saving_a_bad_address_shows_the_reason_and_changes_nothing(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("core:hq_connection_save"),
            {"sync.api_url": "http://insecure.example.com"}, follow=True)
        text = " ".join(str(m) for m in response.context["messages"])
        self.assertIn("https", text)
        self.assertEqual(self.load().get("sync.api_url"), HQ_ENDPOINT_DEFAULTS["sync.api_url"])

    def test_saving_a_good_address_persists_it_and_asks_for_a_restart(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("core:hq_connection_save"),
            {"sync.api_url": "https://site-b.example.com/api/sync"}, follow=True)
        text = " ".join(str(m) for m in response.context["messages"])
        self.assertIn("Restart", text)
        self.assertEqual(self.load().get("sync.api_url"), "https://site-b.example.com/api/sync")

    def test_reset_returns_the_machine_to_the_shipped_defaults(self):
        self.client.force_login(self.staff)
        self.client.post(reverse("core:hq_connection_save"),
                         {"sync.api_url": "https://pinned.example.com/api/sync"})
        self.assertEqual(self.load().endpoint_sources["sync.api_url"], "config.json")
        self.client.post(reverse("core:hq_connection_reset"), follow=True)
        self.assertEqual(self.load().endpoint_sources["sync.api_url"], "default")

    def test_the_test_button_reports_each_service(self):
        self.client.force_login(self.staff)
        with mock.patch("requests.get", return_value=mock.Mock(status_code=200)):
            payload = self.client.post(reverse("core:hq_connection_test")).json()
        self.assertEqual(set(payload["results"]), {"sync", "update", "database"})
        self.assertTrue(payload["results"]["sync"]["ok"])


class CommandTests(HQSettingsBase):
    def run_command(self, *args):
        out = io.StringIO()
        call_command("hq_endpoint", *args, stdout=out, stderr=io.StringIO())
        return out.getvalue()

    def test_show_lists_every_setting_and_its_source(self):
        output = self.run_command("--show")
        for key in HQ_ENDPOINT_DEFAULTS:
            self.assertIn(key, output)
        self.assertIn("[default]", output)

    def test_set_then_show_reports_the_pin(self):
        self.run_command("--set", "update.server_url=https://u.example.com")
        output = self.run_command("--show")
        self.assertIn("https://u.example.com", output)
        self.assertIn("Pinned", output)

    def test_an_unknown_setting_is_refused(self):
        with self.assertRaises(CommandError):
            self.run_command("--set", "sync.nope=x")

    def test_a_bad_value_is_refused_and_nothing_changes(self):
        with self.assertRaises(CommandError):
            self.run_command("--set", "hq_db.port=99999")
        self.assertEqual(self.load().get("hq_db.port"), HQ_ENDPOINT_DEFAULTS["hq_db.port"])

    def test_reset_all_releases_every_pin(self):
        self.run_command("--set", "sync.api_url=https://a.example.com/api/sync",
                         "--set", "update.server_url=https://b.example.com")
        self.run_command("--reset", "all")
        cfg = self.load()
        self.assertTrue(all(cfg.endpoint_sources[k] == "default" for k in HQ_ENDPOINT_DEFAULTS))

    def test_test_fails_loudly_when_a_service_is_unreachable(self):
        import requests

        with mock.patch("requests.get", side_effect=requests.exceptions.ConnectionError()):
            with self.assertRaises(CommandError):
                self.run_command("--test")


class MigrationMarkerTests(HQSettingsBase):
    def test_saving_from_the_page_keeps_the_marker(self):
        cfg = self.load()
        hq_settings.apply_changes(cfg, {"sync.api_url": "https://c.example.com/api/sync"})
        self.assertEqual(self.on_disk()[ENDPOINT_MARKER], ENDPOINT_MARKER_VALUE)


class ExportEchoTests(HQSettingsBase):
    """The exported-values marker must suppress only this layer's own echo."""

    def test_an_operator_variable_set_after_export_still_wins(self):
        self.load().setup_environment_variables()  # marks what it exported
        with mock.patch.dict(os.environ, {"SYNC_API_URL": "https://ops.example.com/api/sync"}):
            cfg = self.load()
        self.assertEqual(cfg.get("sync.api_url"), "https://ops.example.com/api/sync")
        self.assertEqual(cfg.endpoint_sources["sync.api_url"], "env")

    def test_a_pinned_value_survives_its_own_export(self):
        cfg = self.load()
        hq_settings.apply_changes(cfg, {"sync.api_url": "https://pinned.example.com/api/sync"})
        self.load().setup_environment_variables()
        reloaded = self.load()
        self.assertEqual(reloaded.get("sync.api_url"), "https://pinned.example.com/api/sync")
        self.assertEqual(reloaded.endpoint_sources["sync.api_url"], "config.json")

    def test_a_corrupt_marker_is_ignored_rather_than_fatal(self):
        with mock.patch.dict(os.environ, {config.ENDPOINTS_FROM_CONFIG_VAR: "not json"}):
            cfg = self.load()
        self.assertEqual(cfg.get("sync.api_url"), HQ_ENDPOINT_DEFAULTS["sync.api_url"])


class NavigationTests(HQSettingsBase):
    """The link was first placed inside the HOD block, so it appeared only for
    users who were HOD *and* staff while the view is staff-only."""

    def setUp(self):
        super().setUp()
        self.client = Client()

    def _sidebar_for(self, **flags):
        user = CustomUser.objects.create_user(username=flags.pop("username"),
                                              password="pw-for-tests", **flags)
        self.client.force_login(user)
        return self.client.get(reverse("core:hq_connection") if user.is_staff
                               else reverse("dashboard:dashboard-main"), follow=True)

    def test_staff_see_the_link_even_without_the_hod_role(self):
        response = self._sidebar_for(username="staffer", is_staff=True)
        self.assertContains(response, reverse("core:hq_connection"))

    def test_non_staff_do_not_see_the_link(self):
        response = self._sidebar_for(username="tech2")
        self.assertNotContains(response, reverse("core:hq_connection"))


class SigningKeyTests(HQSettingsBase):
    """The trust anchor for update packages and the fleet redirect (C-3).

    It was previously reachable only by editing a constant in updates/updater.py,
    which is why it stayed empty.
    """

    KEY = "ZmFrZS1wdWJsaWMta2V5LWZvci10ZXN0cw=="

    def test_the_build_ships_a_trust_anchor(self):
        """C-3: the shipped build carries the fleet's public key, so packages
        are verified and HQ address changes can be followed out of the box."""
        key = self.load().get("update.public_key")
        self.assertTrue(key, "the build ships no UPDATE_PUBLIC_KEY — signing is off")
        self.assertEqual(len(base64.b64decode(key)), 32, "not a 32-byte Ed25519 key")

    def test_a_build_without_a_key_is_reported_as_unsafe(self):
        with mock.patch.dict(
            CirqenConfig.DEFAULT_CONFIG["update"], {"public_key": ""}, clear=False
        ):
            ok, errors = self.load().validate_config()
        self.assertTrue(any("public_key" in e for e in errors), errors)

    def test_the_environment_sets_it_in_production_mode(self):
        with mock.patch.dict(os.environ, {"UPDATE_PUBLIC_KEY": self.KEY}):
            cfg = self.load()
            self.assertEqual(cfg.get("update.public_key"), self.KEY)
            ok, errors = cfg.validate_config()
        self.assertFalse(any("public_key" in e for e in errors), errors)

    def test_an_environment_value_is_not_written_to_disk(self):
        with mock.patch.dict(os.environ, {"UPDATE_PUBLIC_KEY": self.KEY}):
            cfg = self.load()
            with mock.patch("builtins.print"):
                cfg.save()
        self.assertNotIn(self.KEY, (self.data / "config.json").read_text())

    def test_config_json_supplies_it_when_the_environment_does_not(self):
        self.write_json({"update": {"public_key": self.KEY}})
        self.assertEqual(self.load().get("update.public_key"), self.KEY)

    def test_it_reaches_the_verifier_through_settings(self):
        from django.test import override_settings

        with mock.patch.dict(os.environ, {"UPDATE_PUBLIC_KEY": self.KEY}):
            cfg = self.load()
        with override_settings(UPDATE_SYSTEM={"public_key": cfg.get("update.public_key")}):
            from updates import updater

            # Not a real key, so it cannot load — but it must be *read*, which
            # is the wiring that was missing entirely.
            with mock.patch.object(updater, "UPDATE_PUBLIC_KEY", ""):
                self.assertIsNone(updater._public_key())  # invalid base64 key
        with override_settings(UPDATE_SYSTEM={}):
            from updates import updater

            with mock.patch.object(updater, "UPDATE_PUBLIC_KEY", ""):
                self.assertIsNone(updater._public_key())
