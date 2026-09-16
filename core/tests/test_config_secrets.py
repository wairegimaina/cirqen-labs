"""Secrets never come from source (IMPROVEMENT_PLAN.md 3.3)."""
import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase

from config import CirqenConfig  # sync/config.py, the copy Django loads

SECRET_ENV = ("SYNC_AUTH_TOKEN", "HQ_API_KEY", "POSTGRES_HQ_PASSWORD", "CIRQEN_PROVISIONING_FILE")


class ConfigSecretsTests(SimpleTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {k: "" for k in SECRET_ENV})
        patcher.start()
        self.addCleanup(patcher.stop)

    def load(self):
        with mock.patch("builtins.print"):
            return CirqenConfig(self.data, use_env_file=False)

    def test_defaults_carry_no_secrets(self):
        for dotted in CirqenConfig.SECRET_ENV:
            self.assertFalse(CirqenConfig._dig(CirqenConfig.DEFAULT_CONFIG, dotted), dotted)
        self.assertEqual(len(self.load().missing_secrets()), 3)

    def test_provisioning_file_fills_and_persists_secrets(self):
        (self.data / "provisioning.json").write_text(json.dumps(
            {"sync": {"auth_token": "t"}, "update": {"api_key": "k"}, "hq_db": {"password": "p"}}
        ))
        self.assertEqual(self.load().missing_secrets(), [])
        (self.data / "provisioning.json").unlink()
        self.assertEqual(self.load().get("sync.auth_token"), "t")  # now read from config.json

    def test_environment_wins_and_is_not_written_to_disk(self):
        (self.data / "config.json").write_text(json.dumps({"update": {"api_key": "from-file"}}))
        with mock.patch.dict(os.environ, {"HQ_API_KEY": "from-env"}):
            cfg = self.load()
            self.assertEqual(cfg.get("update.api_key"), "from-env")
            with mock.patch("builtins.print"):
                cfg.save()
        saved = json.loads((self.data / "config.json").read_text())
        self.assertEqual(saved["update"]["api_key"], "from-file")

    def test_validate_config_reports_missing_secrets(self):
        ok, errors = self.load().validate_config()
        self.assertFalse(ok)
        self.assertTrue(any("SYNC_AUTH_TOKEN" in e for e in errors))
