"""Sentry wiring (IMPROVEMENT_PLAN.md 4.5)."""
import os
from unittest import mock

from django.test import SimpleTestCase

from core import monitoring


class InitSentryTests(SimpleTestCase):
    def setUp(self):
        monitoring._initialised = False
        self.addCleanup(setattr, monitoring, "_initialised", False)

    def test_disabled_without_a_dsn(self):
        with mock.patch.dict(os.environ, {"SENTRY_DSN": ""}):
            self.assertFalse(monitoring.init_sentry("django"))

    def test_initialises_with_release_and_no_pii(self):
        with mock.patch.dict(os.environ, {"SENTRY_DSN": "https://key@example.invalid/1"}), \
                mock.patch("sentry_sdk.init") as init, mock.patch("sentry_sdk.set_tag") as set_tag:
            self.assertTrue(monitoring.init_sentry("sync_agent", client_name="Ward 5"))

        kwargs = init.call_args.kwargs
        self.assertEqual(kwargs["release"], f"equiper@{monitoring.app_version()}")
        self.assertFalse(kwargs["send_default_pii"])
        set_tag.assert_any_call("component", "sync_agent")
        set_tag.assert_any_call("client", "Ward 5")
