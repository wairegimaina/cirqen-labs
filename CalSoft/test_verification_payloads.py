"""Legacy QR payload readers and verification links (pdf_generators.verification)."""
from types import SimpleNamespace

from django.test import SimpleTestCase, override_settings

from CalSoft.pdf_generators.verification import (
    generate_verification_url, verify_certificate_qr, verify_certificate_qr_with_results,
)

PAYLOAD = ("CERT:BNH-0101|DATE:2026-09-01|SERIAL:PM-7|MODEL:X|HOSPITAL:KNH|STATUS:PASSED|RESULTS:|"
           "NIBP:120>120.5±0.5(PASS)|SpO2-Adult:98>97.8±0.2(PASS)|...+3 MORE POINTS NOT IN QR")


class PayloadTests(SimpleTestCase):
    def test_parsing_never_claims_authenticity(self):
        basic = verify_certificate_qr(PAYLOAD)
        self.assertIsNone(basic["valid"])
        self.assertEqual((basic["certificate_number"], basic["device_serial"]), ("BNH-0101", "PM-7"))

    def test_results_are_read_with_sub_parameters(self):
        result = verify_certificate_qr_with_results(PAYLOAD)
        self.assertIsNone(result["valid"])
        self.assertEqual(result["results_count"], 2)
        self.assertEqual(result["results"][1]["sub_parameter"], "Adult")
        self.assertEqual(result["results"][0]["mean"], "120.5")
        self.assertTrue(result["has_more_results"])

    @override_settings(CERTIFICATE_VERIFICATION_URL="https://hq.example/verify/")
    def test_links_quote_the_number_and_need_both_parts(self):
        session = SimpleNamespace(id="abc")
        self.assertEqual(generate_verification_url("BNH/1", session), "https://hq.example/verify/BNH%2F1/abc")
        self.assertIsNone(generate_verification_url("", session))

    @override_settings(CERTIFICATE_VERIFICATION_URL="")
    def test_no_address_means_no_link(self):
        self.assertIsNone(generate_verification_url("BNH-1", SimpleNamespace(id="abc")))
