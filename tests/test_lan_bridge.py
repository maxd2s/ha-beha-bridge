import json
import unittest
from unittest.mock import Mock

from beha_lan.bridge import Bridge, settings, target_command

ONE = "001122334455"
TWO = "aabbccddeeff"


def payload(target=19, current=22, mode=2):
    return json.dumps({"com.devicedrive.heater": {
        "target_temperature": target, "current_temperature": current},
        "com.beha.heater": {"heater_mode": mode, "child_lock": 0, "alarm_flags": 0}}).encode()


class LANTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.client = Mock()
        self.client.publish.return_value.rc = 0
        self.bridge = Bridge(self.client, {"heaters": [
            {"device_id": ONE, "name": "Study"}, {"device_id": TWO, "name": "Bedroom"}]},
            clock=lambda: self.now)
        self.bridge.connect()
        self.client.reset_mock()

    def report(self, hid=ONE, data=None, retained=False):
        self.bridge.handle(f"devices/{hid}/messages/events/secret-header", data or payload(), retained)

    def publications(self, suffix):
        return [c for c in self.client.publish.call_args_list if c.args[0].endswith(suffix)]

    def test_wheel_target_is_relayed_without_commands_or_header(self):
        self.report(data=payload(21))
        self.report(data=payload(19))
        states = [json.loads(c.args[1]) for c in self.publications('/state')]
        self.assertEqual([s['target_temperature'] for s in states], [21, 19])
        self.assertFalse(self.publications('/command'))
        self.assertNotIn('secret-header', str(self.client.publish.call_args_list))

    def test_command_waits_for_real_readback_and_is_never_retained(self):
        self.report()
        self.client.reset_mock()
        self.bridge.handle(f'beha_lan/{ONE}/target/set', b'20.5')
        command = self.publications('/command')[0]
        self.assertEqual(json.loads(command.args[1]), {'com.devicedrive.heater': {'target_temperature': '20.5'}})
        self.assertFalse(command.kwargs['retain'])
        self.assertEqual(command.kwargs['qos'], 0)
        self.assertFalse(self.publications('/state'))

    def test_stale_devices_expire_independently_and_commands_are_rejected(self):
        self.report()
        self.now += 80
        self.report(TWO)
        self.now += 11
        self.bridge.tick()
        self.assertEqual(self.bridge.online, {TWO})
        self.assertIn('offline', [c.args[1] for c in self.publications('/availability')])
        self.bridge.handle(f'beha_lan/{ONE}/target/set', b'20')
        self.assertFalse(self.publications('/command'))

    def test_retained_or_delayed_data_cannot_establish_freshness(self):
        self.report(retained=True)
        self.bridge.handle(f'devices/{ONE}/messages/events/x', payload(), received=self.now-91)
        self.assertFalse(self.bridge.seen)
        self.report()
        self.bridge.handle(f'beha_lan/{ONE}/target/set', b'20', retained=True)
        self.bridge.handle(f'beha_lan/{ONE}/target/set', b'20', received=self.now-6)
        self.assertFalse(self.publications('/command'))

    def test_reconnect_requires_new_telemetry(self):
        self.report()
        self.bridge.disconnect()
        self.bridge.connect()
        self.assertFalse(self.bridge.seen)
        self.assertFalse(self.bridge.online)
        self.bridge.handle(f'beha_lan/{ONE}/target/set', b'20')
        self.assertFalse(self.publications('/command'))

    def test_invalid_packets_do_not_refresh_liveness(self):
        for data in (b'[]', b'null', b'garbage', b'{}', payload(float('nan')),
                     payload(current=True), payload(current=999),
                     b'{"com.devicedrive.heater": {}, "com.beha.heater": null}'):
            self.report(data=data)
        self.assertFalse(self.bridge.seen)

    def test_unknown_device_requires_opt_in(self):
        self.report('abcdef123456')
        self.assertFalse(self.bridge.seen)
        self.bridge.auto_discover = True
        self.report('abcdef123456')
        self.assertIn('abcdef123456', self.bridge.seen)

    def test_unknown_mode_not_misrepresented_as_heat(self):
        self.report(data=payload(mode=8))
        self.assertEqual(json.loads(self.publications('/state')[0].args[1])['mode'], 'None')

    def test_command_validation(self):
        for value in (b'nan', b'inf', b'4', b'31', b'19.1', b'{}', b'1'*100):
            with self.assertRaises((ValueError, TypeError)):
                target_command(value)

    def test_names_ids_and_duplicate_validation(self):
        names, _, _ = settings({'heaters': [{'device_id': 'AA:BB:CC:DD:EE:FF', 'name': 'Bedroom'}]})
        self.assertEqual(names, {TWO: 'Bedroom'})
        with self.assertRaises(ValueError):
            settings({'heaters': [{'device_id': ONE}, {'device_id': ONE}]})

    def test_home_assistant_birth_republishes_discovery(self):
        self.bridge.handle('homeassistant/status', b'online')
        self.assertEqual(len([c for c in self.publications('/config') if '/climate/' in c.args[0]]), 2)


if __name__ == '__main__':
    unittest.main()
