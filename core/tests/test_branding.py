from django.test import SimpleTestCase, override_settings

from core.branding import contact_line


class ContactLineTests(SimpleTestCase):
    @override_settings(REPORT_CONTACT={"email": "biomed@knh.go.ke", "phone": "+254 20 272 6300"})
    def test_configured_details_are_printed(self):
        self.assertEqual(contact_line("ISO/IEC 17025:2017"),
                         "Email: biomed@knh.go.ke | Phone: +254 20 272 6300 | ISO/IEC 17025:2017")

    @override_settings(REPORT_CONTACT={"email": "", "phone": ""})
    def test_unset_details_are_left_out_not_faked(self):
        self.assertEqual(contact_line("ISO 9001:2015"), "ISO 9001:2015")
        self.assertEqual(contact_line(), "")
