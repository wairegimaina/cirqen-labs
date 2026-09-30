"""A PC with a hospital code syncs only with an HQ that proves it is that
hospital's: certificate from Control, right hospital, right address, not
expired, and the nonce signed with the certified key. Anything less is a
refusal, and so is any address document that is not for this hospital."""
import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

import config
import endpoint_sync
import hq_handshake
from core.tests.test_endpoint_sync import NEW_SYNC, UPDATE_SERVER, EndpointSyncBase, make_keypair

SERVER_DIR = Path(config.__file__).resolve().parent / "hq_server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import hq_certificates  # noqa: E402  (Control's issuer, hq_server/hq_certificates.py)

HOSPITAL = "CH0001"


def _seed(private):
    from cryptography.hazmat.primitives import serialization

    return base64.b64encode(private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                  serialization.NoEncryption())).decode()


def _public(private):
    return hq_certificates._raw_public(private.public_key())


class HQ:
    """What hq_server answers to /hello (hospital_identity.sign_hello)."""

    def __init__(self, control, *, hospital=HOSPITAL, urls=(NEW_SYNC,), days=365, key=None, answer_as=None):
        self.key = key or make_keypair()[0]
        self.certificate = json.loads(hq_certificates.issue(control, hospital, _public(self.key), list(urls), days))
        self.answer_as = answer_as or hospital

    def answer(self, nonce, *, sign_with=None):
        proof = (sign_with or self.key).sign(hq_handshake.hello_message(nonce, self.answer_as))
        return {"hospital": self.answer_as, "certificate": self.certificate,
                "proof": base64.b64encode(proof).decode()}

    def session(self, **kwargs):
        def post(url, json=None, timeout=None):
            nonce = base64.b64decode(json["nonce"])
            return mock.Mock(status_code=200, json=mock.Mock(return_value=self.answer(nonce, **kwargs)))
        return mock.Mock(post=mock.Mock(side_effect=post))


class CheckAnswerTests(SimpleTestCase):
    def setUp(self):
        self.control, _ = make_keypair()
        self.nonce = os.urandom(32)

    def check(self, hq, **answer_kwargs):
        return hq_handshake.check_answer(hq.answer(self.nonce, **answer_kwargs), self.nonce, NEW_SYNC,
                                         HOSPITAL, self.control.public_key())

    def test_the_right_hq_is_confirmed(self):
        self.assertEqual(self.check(HQ(self.control)), "")

    def test_another_hospitals_hq_is_refused(self):
        self.assertIn("belongs to hospital CH0002", self.check(HQ(self.control, hospital="CH0002", answer_as="CH0002")))

    def test_an_hq_answering_as_another_hospital_is_refused(self):
        self.assertIn("answered as hospital CH0002", self.check(HQ(self.control, answer_as="CH0002")))

    def test_an_address_the_certificate_does_not_cover_is_refused(self):
        hq = HQ(self.control, urls=("https://hq-ch0002.example/api/sync",))
        self.assertIn("does not cover", self.check(hq))

    def test_an_expired_certificate_is_refused(self):
        self.assertIn("expired", self.check(HQ(self.control, days=-1)))

    def test_a_copied_certificate_without_the_key_is_refused(self):
        self.assertIn("could not prove", self.check(HQ(self.control), sign_with=make_keypair()[0]))

    def test_a_certificate_not_from_control_is_refused(self):
        self.assertIn("not signed by Cirqen Control", self.check(HQ(make_keypair()[0])))

    def test_an_endpoint_document_signature_cannot_pass_as_a_certificate(self):
        # Signed by Control's key but over the raw bytes, as address documents are.
        hq = HQ(self.control)
        raw = hq.certificate["document"]
        hq.certificate["signature"] = base64.b64encode(self.control.sign(raw.encode())).decode()
        self.assertIn("not signed by Cirqen Control", self.check(hq))

    def test_a_nonce_from_another_handshake_is_refused(self):
        answer = HQ(self.control).answer(os.urandom(32))
        why = hq_handshake.check_answer(answer, self.nonce, NEW_SYNC, HOSPITAL, self.control.public_key())
        self.assertIn("could not prove", why)

    def test_no_trusted_key_means_no_confirmation(self):
        why = hq_handshake.check_answer(HQ(self.control).answer(self.nonce), self.nonce, NEW_SYNC, HOSPITAL, None)
        self.assertIn("no update signing public key", why)


class ConfirmTests(SimpleTestCase):
    def setUp(self):
        self.control, _ = make_keypair()

    def test_it_confirms_over_the_wire(self):
        ok, why = hq_handshake.confirm(NEW_SYNC, "ch0001", session=HQ(self.control).session(),
                                       control_key=self.control.public_key())
        self.assertTrue(ok, why)

    def test_an_hq_without_identity_is_refused(self):
        session = mock.Mock(post=mock.Mock(return_value=mock.Mock(status_code=404)))
        ok, why = hq_handshake.confirm(NEW_SYNC, HOSPITAL, session=session, control_key=self.control.public_key())
        self.assertFalse(ok)
        self.assertIn("no hospital identity", why)

    def test_an_unreachable_hq_is_refused_without_raising(self):
        session = mock.Mock(post=mock.Mock(side_effect=OSError("no route")))
        ok, why = hq_handshake.confirm(NEW_SYNC, HOSPITAL, session=session, control_key=self.control.public_key())
        self.assertFalse(ok)
        self.assertIn("unreachable", why)


class HospitalDocumentTests(EndpointSyncBase):
    """endpoint_sync with a hospital code (the fleet signing key is self.private)."""

    def document(self, *, hospital=HOSPITAL, **kwargs):
        raw = json.loads(super().document(**kwargs))
        if hospital is not None:
            raw["hospital"] = hospital
        return json.dumps(raw, sort_keys=True, separators=(",", ":"))

    def cycle(self, response, *, handshake=(True, ""), update_ok=True):
        session = mock.Mock(get=mock.Mock(return_value=response))
        with mock.patch.object(hq_handshake, "confirm", return_value=handshake) as confirm, \
                mock.patch.object(endpoint_sync, "_probe_update_server", return_value=update_ok):
            result = endpoint_sync.fetch_and_apply(self.data, UPDATE_SERVER, session=session,
                                                   hospital_code=HOSPITAL)
        return result, session, confirm

    def test_it_asks_for_its_own_hospitals_document(self):
        _, session, _ = self.cycle(self.response(self.document()))
        self.assertEqual(session.get.call_args[0][0], f"{UPDATE_SERVER}/api/endpoints/{HOSPITAL}/")

    def test_its_own_document_is_adopted_after_the_handshake(self):
        result, _, confirm = self.cycle(self.response(self.document()))
        self.assertTrue(result["changed"], result)
        confirm.assert_called_once()
        self.assertEqual(confirm.call_args[0][:2], (NEW_SYNC, HOSPITAL))

    def test_another_hospitals_document_is_refused(self):
        result, _, confirm = self.cycle(self.response(self.document(hospital="CH0002")))
        self.assertFalse(result["changed"])
        self.assertIn("not CH0001", result["reason"])
        confirm.assert_not_called()

    def test_the_fleet_wide_document_is_refused(self):
        result, _, _ = self.cycle(self.response(self.document(hospital=None)))
        self.assertFalse(result["changed"])

    def test_an_address_that_fails_the_handshake_is_not_adopted(self):
        result, _, _ = self.cycle(self.response(self.document()), handshake=(False, "belongs to CH0002"))
        self.assertFalse(result["changed"])
        self.assertIn("failed the hospital check", result["reason"])
        self.assertEqual(endpoint_sync.read_state(self.data), {})

    def test_a_new_update_server_that_does_not_answer_is_not_adopted(self):
        doc = self.document(endpoints={"sync.api_url": NEW_SYNC,
                                       "update.server_url": "https://updates-new.example.com"})
        result, _, _ = self.cycle(self.response(doc), update_ok=False)
        self.assertFalse(result["changed"])
        self.assertIn("update server", result["reason"])

    def test_the_hospital_code_comes_from_provisioning(self):
        (self.data / "provisioning.json").write_text(json.dumps({"sync": {"hospital_code": "CH0001"}}))
        self.assertEqual(self.load_config().get("sync.hospital_code"), "CH0001")


class ControlTests(SimpleTestCase):
    """Cirqen Control's half: hq_server/endpoints.py and hq_certificates.py."""

    def setUp(self):
        self.control, self.public_b64 = make_keypair()
        patch = mock.patch.dict(os.environ, {
            "HQ_SIGNING_PRIVATE_KEY": _seed(self.control),
            "FLEET_HOSPITALS": json.dumps({"ch0001": {"sync": NEW_SYNC + "/", "updates": UPDATE_SERVER,
                                                      "fallbacks": ["https://old.example/api/sync"]}}),
        })
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_hospital_document_is_what_its_pc_accepts(self):
        import endpoints as server_endpoints

        payload = server_endpoints.signed_response(server_endpoints.build_hospital_document("CH0001"))
        with override_settings(UPDATE_SYSTEM={"public_key": self.public_b64}):
            document, why = endpoint_sync.verify_document(payload)
        self.assertIsNotNone(document, why)
        self.assertEqual((document["hospital"], document["endpoints"]["sync.api_url"]), (HOSPITAL, NEW_SYNC))

    def test_an_unknown_hospital_gets_nothing(self):
        import endpoints as server_endpoints

        self.assertIsNone(server_endpoints.build_hospital_document("CH0009"))

    def test_a_malformed_setting_serves_nothing(self):
        import endpoints as server_endpoints

        with mock.patch.dict(os.environ, {"FLEET_HOSPITALS": "{not json"}):
            self.assertIsNone(server_endpoints.build_hospital_document("CH0001"))

    def test_the_command_prints_what_render_needs(self):
        with mock.patch("builtins.print") as printed:
            hq_certificates.main(["new", "--hospital", "ch0001", "--url", NEW_SYNC])
        lines = [c.args[0] for c in printed.call_args_list]
        values = dict(line.split("=", 1) for line in lines if not line.startswith("#"))
        self.assertEqual(values["HOSPITAL_CODE"], HOSPITAL)
        cert = json.loads(values["HQ_CERTIFICATE"])
        fields = json.loads(cert["document"])
        self.assertEqual((fields["hospital"], fields["hq_urls"]), (HOSPITAL, [NEW_SYNC]))
        self.assertLess(datetime.fromisoformat(fields["expires_at"]),
                        datetime.now(timezone.utc) + timedelta(days=366))
        # The printed key is the certified one.
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(values["HQ_IDENTITY_PRIVATE_KEY"]))
        self.assertEqual(_public(key), fields["hq_public_key"])


class HospitalCodeCommandTests(EndpointSyncBase):
    """manage.py hospital_code: saved only when this PC's HQ proves the hospital."""

    def run_command(self, *args, handshake=(True, "")):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        with mock.patch("core.hq_settings.load_config", self.load_config), \
                mock.patch.object(hq_handshake, "confirm", return_value=handshake):
            call_command("hospital_code", *args, stdout=out)
        return out.getvalue()

    def test_it_saves_the_code_when_the_hq_proves_it(self):
        self.run_command("ch0001")
        self.assertEqual(self.load_config().get("sync.hospital_code"), HOSPITAL)

    def test_it_refuses_when_the_hq_cannot(self):
        from django.core.management import CommandError

        with self.assertRaisesMessage(CommandError, "did not prove it is hospital CH0001"):
            self.run_command("CH0001", handshake=(False, "HQ has no hospital identity yet"))
        self.assertFalse(self.load_config().get("sync.hospital_code"))

    def test_force_and_clear(self):
        self.run_command("CH0001", "--force", handshake=(False, "offline"))
        self.assertEqual(self.load_config().get("sync.hospital_code"), HOSPITAL)
        self.run_command("--clear")
        self.assertFalse(self.load_config().get("sync.hospital_code"))


class DirectPushTests(SimpleTestCase):
    """core/hq_link pushes saved rows straight to HQ: with a hospital code it
    names the hospital and pushes only to an HQ that passed the handshake."""

    def setUp(self):
        from core import hq_link

        self.hq_link = hq_link
        hq_link._confirmed.update(url="", at=0.0)
        config = mock.Mock(get=mock.Mock(return_value="CH0001"))
        patch = override_settings(CIRQEN_CONFIG=config, HQ_SYNC_API_URL=NEW_SYNC, SYNC_AUTH_TOKEN="k")
        patch.enable()
        self.addCleanup(patch.disable)

    def push(self, handshake):
        ok_response = mock.Mock(status_code=200, json=mock.Mock(return_value={"status": "success"}))
        with mock.patch.object(self.hq_link, "get_client_id", return_value="pc-1"), \
                mock.patch.object(hq_handshake, "confirm", return_value=handshake), \
                mock.patch("requests.post", return_value=ok_response) as post:
            result = self.hq_link.push_events([{"table": "t", "row_id": "1"}])
        return result, post

    def test_an_unconfirmed_hq_gets_nothing(self):
        (ok, why), post = self.push((False, "belongs to CH0002"))
        self.assertFalse(ok)
        self.assertIn("will sync later", why)
        post.assert_not_called()

    def test_a_confirmed_hq_gets_the_hospital_code(self):
        (ok, _), post = self.push((True, ""))
        self.assertTrue(ok)
        self.assertEqual(post.call_args.kwargs["headers"]["X-Cirqen-Hospital"], "CH0001")
