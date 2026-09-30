"""The update check names this PC's hospital, so it gets that hospital's release."""
from unittest import mock

from django.test import SimpleTestCase, override_settings

from updates.hospital import latest_params


class LatestParamsTests(SimpleTestCase):
    def test_a_pc_with_a_hospital_code_sends_it(self):
        config = mock.Mock(get=mock.Mock(return_value=" ch0001 "))
        with override_settings(CIRQEN_CONFIG=config):
            self.assertEqual(latest_params("1.7.0", "m1"),
                             {"current_version": "1.7.0", "machine_id": "m1", "hospital_code": "CH0001"})

    def test_a_pc_without_one_asks_as_before(self):
        config = mock.Mock(get=mock.Mock(return_value=""))
        with override_settings(CIRQEN_CONFIG=config):
            self.assertEqual(latest_params("1.7.0", "m1"), {"current_version": "1.7.0", "machine_id": "m1"})
