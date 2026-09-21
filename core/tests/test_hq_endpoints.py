"""Where HQ is hosted lives in one place and can be changed after installation.

Covers config.HQ_ENDPOINT_DEFAULTS: precedence, migration of machines pinned by
older builds, persistence that never freezes a default, validation, and a guard
that no hostname is written anywhere else.
"""
import json
import os
import re
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

import config
from config import (
    ENDPOINT_MARKER,
    ENDPOINT_MARKER_VALUE,
    HQ_ENDPOINT_DEFAULTS,
    HQ_ENDPOINT_ENV,
    PRE_LAYERING_DEFAULTS,
    CirqenConfig,
    resolve_endpoints,
    validate_endpoints,
)

NEW_SYNC = "https://new-hq.example.com/api/sync"
NEW_UPDATES = "https://new-updates.example.com"
CLEARED_ENV = list(HQ_ENDPOINT_ENV.values()) + [
    "SYNC_AUTH_TOKEN", "HQ_API_KEY", "POSTGRES_HQ_PASSWORD", "CIRQEN_PROVISIONING_FILE",
    config.ENDPOINTS_FROM_CONFIG_VAR,
]


class EndpointTestCase(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {k: "" for k in CLEARED_ENV})
        env.start()
        self.addCleanup(env.stop)
        # Never read a developer's real provisioning.json (git-ignored, beside config.py).
        isolate = mock.patch.object(
            config, "_provisioning_candidates_for", lambda data_path: [Path(data_path) / "provisioning.json"]
        )
        isolate.start()
        self.addCleanup(isolate.stop)

    def load(self):
        with mock.patch("builtins.print"):
            return CirqenConfig(self.data, use_env_file=False)

    def write_json(self, payload):
        (self.data / "config.json").write_text(json.dumps(payload))

    def on_disk(self):
        return json.loads((self.data / "config.json").read_text())

    def move_defaults(self):
        patcher = mock.patch.dict(
            HQ_ENDPOINT_DEFAULTS,
            {"sync.api_url": NEW_SYNC, "update.server_url": NEW_UPDATES},
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class FleetMigrationTests(EndpointTestCase):
    """The failure this exists to prevent: an installed machine keeps the
    address it was first run with, however the shipped default changes."""

    def old_style_file(self):
        return {
            "sync": {"api_url": next(iter(PRE_LAYERING_DEFAULTS["sync.api_url"]))},
            "update": {"server_url": next(iter(PRE_LAYERING_DEFAULTS["update.server_url"]))},
            "hq_db": {"host": next(iter(PRE_LAYERING_DEFAULTS["hq_db.host"]))},
        }

    def test_installed_machine_follows_a_changed_default(self):
        self.write_json(self.old_style_file())
        self.load()  # first start on the release that adds layering
        self.move_defaults()  # next release ships a new HQ
        cfg = self.load()
        self.assertEqual(cfg.get("sync.api_url"), NEW_SYNC)
        self.assertEqual(cfg.get("update.server_url"), NEW_UPDATES)
        self.assertEqual(cfg.endpoint_sources["sync.api_url"], "default")

    def test_machine_that_skips_a_release_still_moves(self):
        self.write_json(self.old_style_file())
        self.move_defaults()  # never ran the intermediate release
        self.assertEqual(self.load().get("sync.api_url"), NEW_SYNC)

    def test_old_file_is_rewritten_without_addresses_and_backed_up(self):
        self.write_json(self.old_style_file())
        self.load()
        saved = self.on_disk()
        self.assertNotIn("api_url", saved.get("sync", {}))
        self.assertNotIn("server_url", saved.get("update", {}))
        self.assertEqual(saved[ENDPOINT_MARKER], ENDPOINT_MARKER_VALUE)
        self.assertTrue((self.data / "config.json.pre-endpoints").exists())

    def test_hand_edited_address_in_an_old_file_is_kept(self):
        self.write_json({"sync": {"api_url": "https://hand-edited.example.com/api/sync"}})
        cfg = self.load()
        self.assertEqual(cfg.get("sync.api_url"), "https://hand-edited.example.com/api/sync")
        self.assertEqual(cfg.endpoint_sources["sync.api_url"], "config.json")
        self.move_defaults()
        self.assertEqual(self.load().get("sync.api_url"), "https://hand-edited.example.com/api/sync")

    def test_fresh_install_writes_no_addresses(self):
        cfg = self.load()
        self.assertEqual(cfg.get("sync.api_url"), HQ_ENDPOINT_DEFAULTS["sync.api_url"])
        saved = self.on_disk()
        for key in HQ_ENDPOINT_DEFAULTS:
            self.assertIsNone(config._get_path(saved, key), key)


class PrecedenceTests(EndpointTestCase):
    def test_environment_is_honoured_in_production_mode(self):
        with mock.patch.dict(os.environ, {"SYNC_API_URL": "https://env.example.com/api/sync/"}):
            cfg = self.load()
        self.assertEqual(cfg.get("sync.api_url"), "https://env.example.com/api/sync")
        self.assertEqual(cfg.endpoint_sources["sync.api_url"], "env")

    def test_environment_beats_a_deliberate_override_without_erasing_it(self):
        self.write_json({ENDPOINT_MARKER: ENDPOINT_MARKER_VALUE,
                         "sync": {"api_url": "https://pinned.example.com/api/sync"}})
        with mock.patch.dict(os.environ, {"SYNC_API_URL": "https://env.example.com/api/sync"}):
            cfg = self.load()
            with mock.patch("builtins.print"):
                cfg.save()
        self.assertEqual(self.on_disk()["sync"]["api_url"], "https://pinned.example.com/api/sync")
        self.assertEqual(self.load().get("sync.api_url"), "https://pinned.example.com/api/sync")

    def test_environment_value_is_not_written_to_disk(self):
        with mock.patch.dict(os.environ, {"HQ_SERVER_URL": "https://env.example.com"}):
            cfg = self.load()
            with mock.patch("builtins.print"):
                cfg.save()
        self.assertNotIn("server_url", self.on_disk().get("update", {}))

    def test_set_is_a_deliberate_override_that_survives_reload(self):
        cfg = self.load()
        with mock.patch("builtins.print"):
            cfg.set("sync.api_url", "https://pinned.example.com/api/sync/")
        again = self.load()
        self.assertEqual(again.get("sync.api_url"), "https://pinned.example.com/api/sync")
        self.assertEqual(again.endpoint_sources["sync.api_url"], "config.json")

    def test_setting_the_default_value_releases_the_pin(self):
        cfg = self.load()
        with mock.patch("builtins.print"):
            cfg.set("sync.api_url", "https://pinned.example.com/api/sync")
            cfg.set("sync.api_url", HQ_ENDPOINT_DEFAULTS["sync.api_url"])
        self.assertNotIn("api_url", self.on_disk().get("sync", {}))

    def test_installer_provisioning_can_carry_addresses(self):
        (self.data / "provisioning.json").write_text(json.dumps(
            {"sync": {"api_url": "https://site.example.com/api/sync"}}))
        cfg = self.load()
        self.assertEqual(cfg.get("sync.api_url"), "https://site.example.com/api/sync")
        self.assertEqual(cfg.endpoint_sources["sync.api_url"], "provisioning")
        (self.data / "provisioning.json").unlink()
        self.assertEqual(self.load().get("sync.api_url"), "https://site.example.com/api/sync")

    def test_provisioning_equal_to_the_default_does_not_pin(self):
        (self.data / "provisioning.json").write_text(json.dumps(
            {"sync": {"api_url": HQ_ENDPOINT_DEFAULTS["sync.api_url"]}}))
        self.load()
        self.assertNotIn("api_url", self.on_disk().get("sync", {}))

    def test_describe_endpoints_names_every_source(self):
        with mock.patch.dict(os.environ, {"POSTGRES_HQ_HOST": "db.example.com"}):
            described = {row["key"]: row for row in self.load().describe_endpoints()}
        self.assertEqual(set(described), set(HQ_ENDPOINT_DEFAULTS))
        self.assertEqual(described["hq_db.host"]["source"], "env")
        self.assertEqual(described["sync.api_url"]["source"], "default")


class ResolveEndpointsTests(EndpointTestCase):
    def test_lookup_agrees_with_the_config_class(self):
        self.write_json({ENDPOINT_MARKER: ENDPOINT_MARKER_VALUE,
                         "update": {"server_url": "https://pinned.example.com"}})
        resolved = resolve_endpoints(self.data)
        self.assertEqual(resolved["update.server_url"], ("https://pinned.example.com", "config.json"))
        self.assertEqual(self.load().get("update.server_url"), "https://pinned.example.com")

    def test_lookup_never_writes_a_file(self):
        resolve_endpoints(self.data)
        self.assertEqual(list(self.data.iterdir()), [])

    def test_lookup_reads_environment(self):
        resolved = resolve_endpoints(self.data, env={"HQ_SERVER_URL": "https://env.example.com"})
        self.assertEqual(resolved["update.server_url"], ("https://env.example.com", "env"))


class ValidationTests(EndpointTestCase):
    def values(self, **changes):
        base = dict(HQ_ENDPOINT_DEFAULTS)
        base.update(changes)
        return base

    def test_shipped_defaults_are_valid(self):
        self.assertEqual(validate_endpoints(dict(HQ_ENDPOINT_DEFAULTS)), [])

    def test_plain_http_is_refused_except_for_localhost(self):
        bad = validate_endpoints(self.values(**{"sync.api_url": "http://hq.example.com/api/sync"}))
        self.assertTrue(any("https" in e for e in bad), bad)
        ok = validate_endpoints(self.values(**{"sync.api_url": "http://127.0.0.1:8000/api/sync"}))
        self.assertEqual(ok, [])

    def test_sync_url_must_end_with_api_sync(self):
        bad = validate_endpoints(self.values(**{"sync.api_url": "https://hq.example.com"}))
        self.assertTrue(any("/api/sync" in e for e in bad), bad)

    def test_update_url_must_not_carry_an_api_path(self):
        bad = validate_endpoints(self.values(**{"update.server_url": "https://u.example.com/api/updates"}))
        self.assertTrue(any("update.server_url" in e for e in bad), bad)

    def test_database_host_must_be_bare(self):
        bad = validate_endpoints(self.values(**{"hq_db.host": "postgres://db.example.com"}))
        self.assertTrue(any("hq_db.host" in e for e in bad), bad)

    def test_database_port_and_sslmode_are_checked(self):
        self.assertTrue(validate_endpoints(self.values(**{"hq_db.port": 0})))
        self.assertTrue(validate_endpoints(self.values(**{"hq_db.sslmode": "sometimes"})))

    def test_every_error_names_the_setting(self):
        errors = validate_endpoints(self.values(**{"sync.api_url": "ftp://x", "hq_db.host": ""}))
        self.assertTrue(errors)
        for message in errors:
            self.assertRegex(message, r"(sync|update|hq_db)\.")

    def test_validate_config_reports_a_bad_address(self):
        with mock.patch.dict(os.environ, {"SYNC_API_URL": "http://hq.example.com/api/sync"}):
            ok, errors = self.load().validate_config()
        self.assertTrue(any("sync.api_url" in e and "https" in e for e in errors), errors)


class LocalPasswordTests(EndpointTestCase):
    def test_no_password_is_shipped_in_source(self):
        self.assertEqual(CirqenConfig.DEFAULT_CONFIG["local_db"]["password"], "")

    def test_first_run_generates_a_password_and_keeps_it(self):
        first = self.load().get("local_db.password")
        self.assertGreaterEqual(len(first), 24)
        self.assertEqual(self.load().get("local_db.password"), first)

    def test_existing_install_keeps_the_password_it_has(self):
        self.write_json({"local_db": {"password": "already-in-use"}})
        self.assertEqual(self.load().get("local_db.password"), "already-in-use")

    def test_existing_file_without_a_password_is_reported_not_replaced(self):
        self.write_json({"sync": {"poll_interval": 1}})
        cfg = self.load()
        self.assertEqual(cfg.get("local_db.password"), "")
        self.assertTrue(any("local_db.password" in m for m in cfg.missing_secrets()))


class NoHostnamesElsewhereTests(SimpleTestCase):
    """The next move must not leave stale copies behind, as the last one did."""

    PATTERN = re.compile(
        r"onrender\.com|supabase\.(?:com|co)\b|ohio-postgres|nwlwaeeyduxroykrgksi"
    )
    SKIP_DIRS = {"venv", ".venv", "inv_asset", "node_modules", ".git", "__pycache__",
                 "static", "staticfiles", "build", "dist", "runtime"}

    def test_only_config_py_names_an_hq_host(self):
        root = Path(config.__file__).resolve().parent
        offenders = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.SKIP_DIRS]
            for name in filenames:
                if not name.endswith(".py"):
                    continue
                path = Path(dirpath) / name
                if path in (root / "config.py", Path(__file__).resolve()):
                    continue  # the defaults block, and this file's own pattern list
                text = path.read_text(errors="ignore")
                for lineno, line in enumerate(text.splitlines(), 1):
                    if self.PATTERN.search(line):
                        offenders.append(f"{path.relative_to(root)}:{lineno}")
        self.assertEqual(offenders, [], "HQ hostnames belong in config.HQ_ENDPOINT_DEFAULTS only")
