import json
import unittest
from unittest.mock import Mock

from beha_lan.bridge import Bridge, COMMANDS, HEATER_MODES, control_command, diagnostic_label, settings, target_command, telemetry
from tools.heaters_dashboard import dashboard

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

    def test_readable_modes_and_raw_code_preserved(self):
        expected = ['Off', 'Frost protection (5°C)', 'Local dial', 'Remote target', 'Weekly schedule']
        for mode, label in enumerate(expected):
            self.report(data=payload(mode=mode))
            state = json.loads(self.publications('/state')[-1].args[1])
            self.assertEqual(state['heater_mode'], mode)
            self.assertEqual(state['heater_mode_text'], label)

    def test_unknown_diagnostic_values_are_not_guessed(self):
        self.assertEqual(diagnostic_label('heater_mode', 9), 'Unknown mode (9)')
        self.assertEqual(diagnostic_label('child_lock', 0), 'Unlocked')
        self.assertEqual(diagnostic_label('child_lock', 1), 'Locked')
        self.assertEqual(diagnostic_label('child_lock', 2), 'Unknown lock state (2)')
        self.assertNotIn('alarm_flags', telemetry(payload()))

    def test_power_and_mode_follow_each_actual_mode(self):
        for mode in range(5):
            state = telemetry(payload(mode=mode))
            self.assertEqual(state['mode'], 'off' if mode == 0 else 'heat')
            self.assertEqual(state['power'], 'OFF' if mode == 0 else 'ON')
        state = telemetry(payload(mode=99))
        self.assertEqual((state['mode'], state['power']), ('None', 'None'))
        state = telemetry(payload(mode=True))
        self.assertEqual(state['power'], 'None')
        packet = json.loads(payload()); packet['com.beha.heater']['child_lock'] = 9
        self.assertEqual(telemetry(json.dumps(packet))['child_lock_state'], 'None')

    def test_controls_encode_strings_and_wait_for_feedback(self):
        self.report()
        commands = [('power', b'OFF', 'heater_mode', '0'),
                    ('child_lock', b'ON', 'child_lock', '1'),
                    ('child_lock', b'OFF', 'child_lock', '0'),
                    ('hvac_mode', b'off', 'heater_mode', '0')]
        commands += [('heater_mode', label.encode(), 'heater_mode', str(code)) for code, label in HEATER_MODES.items()]
        for kind, value, key, code in commands:
            self.client.reset_mock()
            self.bridge.handle(f'beha_lan/{ONE}/{kind}/set', value)
            command = self.publications('/command')[0]
            self.assertEqual(json.loads(command.args[1]), {'com.beha.heater': {key: code}})
            self.assertEqual(command.kwargs, {'retain': False, 'qos': 0})
            self.assertFalse(self.publications('/state'))

    def test_power_resumes_last_confirmed_non_off_mode_per_heater(self):
        self.report(data=payload(mode=4))
        self.report(data=payload(mode=0))
        self.report(TWO, data=payload(mode=0))
        self.client.reset_mock()
        self.bridge.handle(f'beha_lan/{ONE}/power/set', b'ON')
        self.bridge.handle(f'beha_lan/{TWO}/hvac_mode/set', b'heat')
        commands = [json.loads(c.args[1])['com.beha.heater']['heater_mode'] for c in self.publications('/command')]
        self.assertEqual(commands, ['4', '3'])

    def test_all_control_topics_reject_retained_delayed_and_stale_commands(self):
        values = dict(target=b'20', power=b'ON', hvac_mode=b'heat', child_lock=b'ON', heater_mode=b'Local dial')
        self.report()
        for kind in COMMANDS:
            topic = f'beha_lan/{ONE}/{kind}/set'
            self.bridge.handle(topic, values[kind], retained=True)
            self.bridge.handle(topic, values[kind], received=self.now-6)
        self.now += 91
        for kind in COMMANDS:
            self.bridge.handle(f'beha_lan/{ONE}/{kind}/set', values[kind])
        self.assertFalse(self.publications('/command'))

    def test_invalid_control_values_cannot_reach_heater(self):
        for kind in COMMANDS[1:]:
            for value in (b'999', b'{}', b'ON\x00', b'\xff', b'x'*100):
                with self.assertRaises((ValueError, UnicodeError)):
                    control_command(kind, value)

    def test_discovery_replaces_old_sensors_with_confirmed_controls(self):
        self.bridge.discovery(ONE)
        configs = {c.args[0]:c.args[1] for c in self.publications('/config')}
        uid = f'beha_lan_{ONE}'
        for key in ('heater_mode', 'child_lock', 'alarm_flags'):
            self.assertEqual(configs[f'homeassistant/sensor/{uid}_{key}/config'], '')
        for component, key in [('switch', 'power'), ('switch', 'child_lock'), ('select', 'heater_mode')]:
            config = json.loads(configs[f'homeassistant/{component}/{uid}_{key}/config'])
            self.assertFalse(config['optimistic'])
            self.assertFalse(config['retain'])
            self.assertEqual(config['state_topic'], f'beha_lan/{ONE}/state')
        climate = json.loads(configs[f'homeassistant/climate/{uid}/config'])
        self.assertEqual(climate['modes'], ['off', 'heat'])

    def test_generated_dashboard_uses_writable_entities_without_alarm(self):
        result = dashboard({'heaters': [{'device_id': ONE, 'name': 'Study'}]})
        text = json.dumps(result)
        for entity in (f'switch.beha_lan_{ONE}_power', f'switch.beha_lan_{ONE}_child_lock', f'select.beha_lan_{ONE}_heater_mode'):
            self.assertIn(entity, text)
        self.assertNotIn('alarm', text.lower())

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
