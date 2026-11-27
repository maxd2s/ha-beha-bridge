import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from tools import provision_gen2 as provision
from tools.heaters_dashboard import dashboard


class ProvisioningTests(unittest.TestCase):
    def test_sec0_wire_format_matches_esp_idf(self):
        self.assertEqual(provision.session_request().hex(), '10005203a20100')
        self.assertEqual(provision.config_request(0).hex(), '08005200')
        self.assertEqual(provision.config_request(4).hex(), '08047200')

    def test_response_rejects_wrong_message_and_failed_status(self):
        for response in (b'\x08\x03\x6a\x02\x08\x01', b'\x08\x05\x7a\x00'):
            with self.assertRaises(ValueError):
                provision.config_response(response, 3, 13)

    def test_truncated_response_is_not_success(self):
        for response in (b'\x80', b'\x52\x05\x00', b'\x00'):
            with self.assertRaises(ValueError):
                provision.fields(response)

    def test_mismatched_credentials_stop_before_scanning(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = Path(directory) / 'broker.json'
            wifi = Path(directory) / 'wifi.json'
            broker.write_text(json.dumps({'expected_device_id': 'aabbccddeeff', 'broker_uri': 'mqtt://localhost'}))
            wifi.write_text(json.dumps({'ssid': 'test', 'password': 'not-a-real-password'}))
            args = SimpleNamespace(device_id='001122334455', apply=True, broker_file=broker, wifi_file=wifi)
            with patch.object(provision.BleakScanner, 'find_device_by_filter') as scan:
                with self.assertRaises(ValueError):
                    asyncio.run(provision.run(args))
                scan.assert_not_called()

    def test_dashboard_works_for_any_device_with_power_control(self):
        result = dashboard({'heaters': [{'device_id': 'AA:BB:CC:DD:EE:FF', 'name': 'Example'}]})
        encoded = json.dumps(result)
        self.assertIn('climate.beha_lan_aabbccddeeff', encoded)
        self.assertIn('climate-hvac-modes', encoded)


if __name__ == '__main__':
    unittest.main()
