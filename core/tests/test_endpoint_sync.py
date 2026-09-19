"""Following a sync-HQ move announced by the update server.

The risk being tested is not "does it work" but "does it refuse when it
should": an unsigned, stale, malformed or unreachable address must never be
adopted, because a machine that follows a bad one is a machine nobody can
reach.
"""
import base64
import json
import os
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

import config
import endpoint_sync
from config import HQ_ENDPOINT_DEFAULTS, CirqenConfig

NEW_SYNC = "https://host-b.example.com/api/sync"
OLD_SYNC = "https://host-a.example.com/api/sync"
UPDATE_SERVER = "https://updates.example.com"

CLEARED = list(config.HQ_ENDPOINT_ENV.values()) + [
    config.ENDPOINTS_FROM_CONFIG_VAR, "CIRQEN_PROVISIONING_FILE", "UPDATE_PUBLIC_KEY",
]


def make_keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    public_b64 = base64.b64encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode()
    return private, public_b64


class EndpointSyncBase(SimpleTestCase):
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

        self.private, self.public_b64 = make_keypair()
        key = mock.patch.dict(os.environ, {"UPDATE_PUBLIC_KEY": self.public_b64})
        key.start()
        self.addCleanup(key.stop)
        # The build now ships a real UPDATE_PUBLIC_KEY, and _public_key() reads
        # settings before the environment, so the throwaway test key has to go
        # there or every document would be checked against the fleet's real key.
        trust = override_settings(UPDATE_SYSTEM={"public_key": self.public_b64})
        trust.enable()
        self.addCleanup(trust.disable)

    # ── helpers ──────────────────────────────────────────────────────────────

    def document(self, *, endpoints=None, issued_at=None, serial="aaa111", configured=True):
        payload = {
            "serial": serial,
            "issued_at": (issued_at or datetime.now(timezone.utc)).isoformat(),
            "configured": configured,
            "endpoints": endpoints if endpoints is not None else {"sync.api_url": NEW_SYNC},
            "fallbacks": {},
            "poll_seconds": 900,
            "max_age_seconds": 7 * 24 * 3600,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def response(self, raw, *, sign=True, status=200, private=None):
        signature = ""
        if sign:
            signature = base64.b64encode((private or self.private).sign(raw.encode())).decode()
        return mock.Mock(
            status_code=status,
            json=mock.Mock(return_value={
                "document": raw, "signature": signature,
                "signed": bool(signature), "algorithm": "ed25519",
            }),
        )

    def run_cycle(self, response, *, probe_ok=True):
        session = mock.Mock(get=mock.Mock(return_value=response))
        with mock.patch.object(endpoint_sync, "_probe", return_value=probe_ok):
            return endpoint_sync.fetch_and_apply(self.data, UPDATE_SERVER, session=session)

    def load_config(self):
        with mock.patch("builtins.print"):
            return CirqenConfig(self.data, use_env_file=False)


class AdoptionTests(EndpointSyncBase):
    def test_a_valid_document_is_adopted(self):
        result = self.run_cycle(self.response(self.document()))
        self.assertTrue(result["changed"], result)
        self.assertEqual(
            endpoint_sync.read_state(self.data)["endpoints"]["sync.api_url"], NEW_SYNC)

    def test_an_adopted_address_is_what_the_application_then_uses(self):
        self.run_cycle(self.response(self.document()))
        cfg = self.load_config()
        self.assertEqual(cfg.get("sync.api_url"), NEW_SYNC)
        self.assertEqual(cfg.endpoint_sources["sync.api_url"], "remote")

    def test_the_same_document_twice_is_not_re_adopted(self):
        self.run_cycle(self.response(self.document()))
        again = self.run_cycle(self.response(self.document()))
        self.assertFalse(again["changed"])
        self.assertIn("already", again["reason"])

    def test_a_second_move_is_followed(self):
        self.run_cycle(self.response(self.document()))
        third = "https://host-c.example.com/api/sync"
        self.run_cycle(self.response(
            self.document(endpoints={"sync.api_url": third}, serial="bbb222")))
        self.assertEqual(self.load_config().get("sync.api_url"), third)

    def test_the_state_file_is_not_config_json(self):
        self.run_cycle(self.response(self.document()))
        self.assertTrue((self.data / "endpoints.json").exists())
        if (self.data / "config.json").exists():
            saved = json.loads((self.data / "config.json").read_text())
            self.assertNotIn("api_url", saved.get("sync", {}))


class RefusalTests(EndpointSyncBase):
    """Each of these would strand machines if it were accepted."""

    def test_an_unsigned_document_is_refused(self):
        result = self.run_cycle(self.response(self.document(), sign=False))
        self.assertFalse(result["changed"])
        self.assertIn("unsigned", result["reason"])

    def test_a_document_signed_by_the_wrong_key_is_refused(self):
        other, _ = make_keypair()
        result = self.run_cycle(self.response(self.document(), private=other))
        self.assertFalse(result["changed"])
        self.assertIn("signature", result["reason"])

    def test_a_tampered_document_is_refused(self):
        raw = self.document()
        response = self.response(raw)
        tampered = raw.replace(NEW_SYNC, "https://attacker.example.com/api/sync")
        response.json.return_value["document"] = tampered
        result = self.run_cycle(response)
        self.assertFalse(result["changed"])
        self.assertIn("signature", result["reason"])

    def test_a_replayed_old_document_is_refused(self):
        stale = datetime.now(timezone.utc) - timedelta(days=30)
        result = self.run_cycle(self.response(self.document(issued_at=stale)))
        self.assertFalse(result["changed"])
        self.assertIn("stale", result["reason"])

    def test_an_address_that_does_not_answer_is_refused(self):
        result = self.run_cycle(self.response(self.document()), probe_ok=False)
        self.assertFalse(result["changed"])
        self.assertIn("does not answer", result["reason"])

    def test_an_invalid_address_is_refused(self):
        result = self.run_cycle(self.response(
            self.document(endpoints={"sync.api_url": "http://insecure.example.com"})))
        self.assertFalse(result["changed"])
        self.assertIn("validation", result["reason"])

    def test_no_public_key_means_refuse_not_trust(self):
        with mock.patch.dict(os.environ, {"UPDATE_PUBLIC_KEY": ""}), \
             mock.patch.object(endpoint_sync, "_public_key", return_value=None):
            result = self.run_cycle(self.response(self.document()))
        self.assertFalse(result["changed"])
        self.assertIn("public key", result["reason"])

    def test_an_unconfigured_server_does_not_unconfigure_the_client(self):
        self.run_cycle(self.response(self.document()))
        result = self.run_cycle(self.response(
            self.document(endpoints={}, configured=False, serial="ccc333")))
        self.assertFalse(result["changed"])
        self.assertEqual(self.load_config().get("sync.api_url"), NEW_SYNC)

    def test_unknown_settings_cannot_be_introduced(self):
        self.run_cycle(self.response(self.document(
            endpoints={"sync.api_url": NEW_SYNC, "app.debug": True})))
        self.assertNotIn("app.debug", endpoint_sync.read_state(self.data)["endpoints"])

    def test_an_unreachable_update_server_changes_nothing(self):
        session = mock.Mock(get=mock.Mock(side_effect=OSError("no route")))
        result = endpoint_sync.fetch_and_apply(self.data, UPDATE_SERVER, session=session)
        self.assertFalse(result["changed"])
        self.assertEqual(self.load_config().get("sync.api_url"),
                         HQ_ENDPOINT_DEFAULTS["sync.api_url"])


class PrecedenceTests(EndpointSyncBase):
    def test_an_operator_pin_beats_a_fleet_move(self):
        cfg = self.load_config()
        with mock.patch("builtins.print"):
            cfg.set("sync.api_url", OLD_SYNC)
        self.run_cycle(self.response(self.document()))
        reloaded = self.load_config()
        self.assertEqual(reloaded.get("sync.api_url"), OLD_SYNC)
        self.assertEqual(reloaded.endpoint_sources["sync.api_url"], "config.json")

    def test_an_environment_variable_beats_a_fleet_move(self):
        self.run_cycle(self.response(self.document()))
        with mock.patch.dict(os.environ, {"SYNC_API_URL": OLD_SYNC}):
            cfg = self.load_config()
        self.assertEqual(cfg.get("sync.api_url"), OLD_SYNC)
        self.assertEqual(cfg.endpoint_sources["sync.api_url"], "env")

    def test_a_fleet_move_beats_the_shipped_default(self):
        self.run_cycle(self.response(self.document()))
        self.assertEqual(self.load_config().endpoint_sources["sync.api_url"], "remote")

    def test_clearing_returns_the_machine_to_the_default(self):
        self.run_cycle(self.response(self.document()))
        endpoint_sync.clear(self.data)
        cfg = self.load_config()
        self.assertEqual(cfg.get("sync.api_url"), HQ_ENDPOINT_DEFAULTS["sync.api_url"])
        self.assertEqual(cfg.endpoint_sources["sync.api_url"], "default")

    def test_a_corrupt_state_file_falls_through_rather_than_breaking(self):
        (self.data / "endpoints.json").write_text("{ not json")
        cfg = self.load_config()
        self.assertEqual(cfg.get("sync.api_url"), HQ_ENDPOINT_DEFAULTS["sync.api_url"])


class RevertTests(EndpointSyncBase):
    def adopt(self):
        self.run_cycle(self.response(self.document()))

    def test_a_brief_outage_does_not_revert(self):
        self.adopt()
        for _ in range(endpoint_sync.REVERT_AFTER_FAILURES + 2):
            self.assertFalse(endpoint_sync.note_health(self.data, False))
        self.assertEqual(self.load_config().get("sync.api_url"), NEW_SYNC)

    def test_sustained_failure_reverts_to_the_shipped_default(self):
        self.adopt()
        state = endpoint_sync.read_state(self.data)
        state["adopted_monotonic"] = time.time() - (endpoint_sync.REVERT_AFTER_SECONDS + 60)
        endpoint_sync._write_state(self.data, state)

        reverted = False
        for _ in range(endpoint_sync.REVERT_AFTER_FAILURES + 1):
            reverted = endpoint_sync.note_health(self.data, False) or reverted
        self.assertTrue(reverted)
        self.assertEqual(self.load_config().get("sync.api_url"),
                         HQ_ENDPOINT_DEFAULTS["sync.api_url"])

    def test_sustained_failure_reverts_to_the_previous_address(self):
        self.adopt()
        third = "https://host-c.example.com/api/sync"
        self.run_cycle(self.response(
            self.document(endpoints={"sync.api_url": third}, serial="bbb222")))

        state = endpoint_sync.read_state(self.data)
        state["adopted_monotonic"] = time.time() - (endpoint_sync.REVERT_AFTER_SECONDS + 60)
        endpoint_sync._write_state(self.data, state)
        for _ in range(endpoint_sync.REVERT_AFTER_FAILURES + 1):
            endpoint_sync.note_health(self.data, False)

        self.assertEqual(self.load_config().get("sync.api_url"), NEW_SYNC)

    def test_recovery_clears_the_failure_count(self):
        self.adopt()
        endpoint_sync.note_health(self.data, False)
        endpoint_sync.note_health(self.data, True)
        self.assertEqual(endpoint_sync.read_state(self.data)["failures_since_adopt"], 0)

    def test_health_reports_are_harmless_when_nothing_was_adopted(self):
        self.assertFalse(endpoint_sync.note_health(self.data, False))


class ServerDocumentTests(SimpleTestCase):
    """The update server's half, exercised directly (hq_server/endpoints.py)."""

    def setUp(self):
        import sys

        server_dir = Path(config.__file__).resolve().parent / "hq_server"
        if str(server_dir) not in sys.path:
            sys.path.insert(0, str(server_dir))
        from cryptography.hazmat.primitives import serialization

        self.private, self.public_b64 = make_keypair()
        seed = self.private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        patch = mock.patch.dict(os.environ, {
            "HQ_SIGNING_PRIVATE_KEY": base64.b64encode(seed).decode(),
            "FLEET_SYNC_API_URL": NEW_SYNC,
            "FLEET_SYNC_FALLBACKS": OLD_SYNC,
        })
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_served_document_is_what_a_client_accepts(self):
        import endpoints as server_endpoints

        payload = server_endpoints.signed_response()
        self.assertTrue(payload["signed"])
        with override_settings(UPDATE_SYSTEM={"public_key": self.public_b64}):
            document, why = endpoint_sync.verify_document(payload)
        self.assertIsNotNone(document, why)
        self.assertEqual(document["endpoints"]["sync.api_url"], NEW_SYNC)

    def test_the_serial_changes_only_when_the_addresses_change(self):
        import endpoints as server_endpoints

        first = server_endpoints.build_document()["serial"]
        self.assertEqual(server_endpoints.build_document()["serial"], first)
        with mock.patch.dict(os.environ, {"FLEET_SYNC_API_URL": OLD_SYNC}):
            self.assertNotEqual(server_endpoints.build_document()["serial"], first)

    def test_an_unset_server_advertises_nothing(self):
        import endpoints as server_endpoints

        with mock.patch.dict(os.environ, {"FLEET_SYNC_API_URL": "", "FLEET_SYNC_FALLBACKS": ""}):
            document = server_endpoints.build_document()
        self.assertFalse(document["configured"])
        self.assertEqual(document["endpoints"], {})

    def test_a_password_is_never_advertised(self):
        import endpoints as server_endpoints

        with mock.patch.dict(os.environ, {"FLEET_HQ_DB_PASSWORD": "should-not-appear"}):
            document = server_endpoints.build_document()
        self.assertNotIn("hq_db.password", document["endpoints"])
        self.assertNotIn("should-not-appear", json.dumps(document))


class LiveSwitchTests(SimpleTestCase):
    """A fleet move must take effect without waiting for a restart.

    Every network loop builds its URL from self.api_url at call time, so the
    running agent can be repointed in place. Without this a move only lands
    when each desktop happens to restart, and the old host can never safely be
    switched off.
    """

    class FakeAgent:
        api_url = "https://host-a.example.com/api/sync"
        hq_base_url = "https://host-a.example.com"

    def setUp(self):
        from sync.sync_agent_7 import DownloadCertHeartbeatMixin

        self.agent = self.FakeAgent()
        self.apply = DownloadCertHeartbeatMixin._apply_new_sync_url.__get__(self.agent)

    def test_a_new_address_is_applied_in_place(self):
        self.apply({"sync.api_url": NEW_SYNC})
        self.assertEqual(self.agent.api_url, NEW_SYNC)
        self.assertEqual(self.agent.hq_base_url, "https://host-b.example.com")

    def test_the_base_url_drops_the_api_path(self):
        self.apply({"sync.api_url": "https://host-c.example.com/api/sync"})
        self.assertEqual(self.agent.hq_base_url, "https://host-c.example.com")

    def test_the_same_address_is_a_no_op(self):
        before = self.agent.api_url
        self.apply({"sync.api_url": before})
        self.assertEqual(self.agent.api_url, before)

    def test_a_document_carrying_no_sync_address_changes_nothing(self):
        before = self.agent.api_url
        self.apply({"hq_db.host": "db.example.com"})
        self.assertEqual(self.agent.api_url, before)

    def test_a_trailing_slash_is_normalised(self):
        self.apply({"sync.api_url": NEW_SYNC + "/"})
        self.assertEqual(self.agent.api_url, NEW_SYNC)
