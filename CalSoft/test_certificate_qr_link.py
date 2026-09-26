"""An issued certificate's QR code opens HQ's verification page.

The data-only payload could be retyped by anyone; the link shows HQ's own
record of the session (hq_server certificate_verify.py). Documents without an
issued number have nothing to verify and keep the payload.
"""
import os
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings

from CalSoft.models import CalibrationParameter, CalibrationProcedure, CalibrationSession, SessionParameterResolution
from CalSoft.pdf_generators.certificate import BtwelveHospitalCertificateGenerator
from CalSoft.pdf_generators.verification import generate_verification_url

VERIFY = "https://hq.example.test/verify"


class CertificateQrLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="qr_tech", password="x")
        cls.procedure = CalibrationProcedure.objects.create(name="NIBP", created_by=cls.user)
        cls.parameter = CalibrationParameter.objects.create(
            procedure=cls.procedure, name="Systolic", unit="mmHg", standard_reference="REF-1", tolerance=Decimal("3"))

    def _session(self, **values):
        session = CalibrationSession.objects.create(
            procedure=self.procedure, performed_by=self.user, device_serial="SN-QR-1", device_model="Model X",
            overall_pass=True, **values)
        SessionParameterResolution.objects.create(session=session, parameter=self.parameter, resolution=Decimal("1"))
        return session

    def _qr_data(self, session):
        captured = []
        real_add = __import__("qrcode").QRCode.add_data

        def spy(qr, data, *args, **kwargs):
            captured.append(data)
            return real_add(qr, data, *args, **kwargs)

        with mock.patch("qrcode.QRCode.add_data", spy):
            BtwelveHospitalCertificateGenerator(session).generate_qr_code()
        return captured[0]

    @override_settings(CERTIFICATE_VERIFICATION_URL=VERIFY)
    def test_issued_certificate_links_to_hq(self):
        session = self._session(status="approved", certificate_number="BNH-0101")
        self.assertEqual(self._qr_data(session), f"{VERIFY}/BNH-0101/{session.id}")

    @override_settings(CERTIFICATE_VERIFICATION_URL=VERIFY)
    def test_certificate_awaiting_its_number_keeps_the_payload(self):
        session = self._session(status="approved_pending_certificate", certificate_number=None)
        self.assertTrue(self._qr_data(session).startswith("CERT:"))

    @override_settings(CERTIFICATE_VERIFICATION_URL="")
    def test_no_verification_address_keeps_the_payload(self):
        session = self._session(status="approved", certificate_number="BNH-0102")
        self.assertTrue(self._qr_data(session).startswith("CERT:BNH-0102"))


class VerificationUrlTests(SimpleTestCase):
    @override_settings(CERTIFICATE_VERIFICATION_URL=VERIFY + "/")
    def test_number_is_quoted_and_trailing_slash_ignored(self):
        session = mock.Mock(id="1f0e")
        self.assertEqual(generate_verification_url("CAL 7/2026", session), f"{VERIFY}/CAL%207%2F2026/1f0e")

    @override_settings(CERTIFICATE_VERIFICATION_URL=VERIFY)
    def test_no_number_no_link(self):
        self.assertIsNone(generate_verification_url(None, mock.Mock(id="x")))

    def test_address_is_derived_from_hq_sync_url(self):
        from Equiper import settings as project_settings

        env = {k: v for k, v in os.environ.items() if k != "CIRQEN_CERT_VERIFY_URL"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(project_settings, "config", {}), \
                mock.patch.object(project_settings, "HQ_SYNC_API_URL", "https://hq.example.test/api/sync"):
            self.assertEqual(project_settings._certificate_verification_url(), VERIFY)
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(project_settings, "config", {}), \
                mock.patch.object(project_settings, "HQ_SYNC_API_URL", "http://insecure.example/api/sync"):
            self.assertEqual(project_settings._certificate_verification_url(), "")
