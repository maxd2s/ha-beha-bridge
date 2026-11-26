import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import beha_auth as auth
import beha_mqtt as bridge


def configuration():
    return {'places': [{'id': 'place', 'rooms': [{
        'id': 'room', 'name': 'Office', 'target_temperature': 20,
        'latest_temperature': 21, 'heaters': [{
            'id': 'heater', 'name': 'Office', 'device_unique_id': 'test',
            'is_enabled': True, 'is_offline': False,
            'latest_reading_timestamp': '2026-11-26T17:03:19'}]}]}]}


class ReliabilityTests(unittest.TestCase):
    def test_concurrent_expired_token_callers_refresh_once_and_preserve_symlink(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'persisted.json'
            link = Path(directory) / 'token.json'
            target.write_text(json.dumps({'access_token': 'old', 'refresh_token': 'refresh',
                                          'saved_at': 0, 'expires_in': 0}))
            link.symlink_to(target)
            response = Mock()
            def read():
                time.sleep(.03)  # Give all callers time to contend on the expired token.
                return json.dumps({'access_token': 'new', 'expires_in': 3600}).encode()
            response.read.side_effect = read
            with patch.object(auth, 'TOKEN_FILE', str(link)), \
                 patch.object(auth.urllib.request, 'urlopen', return_value=response) as request, \
                 contextlib.redirect_stdout(io.StringIO()):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    self.assertEqual(list(pool.map(lambda _: auth.get_token(), range(8))), ['new'] * 8)
                self.assertEqual(request.call_count, 1)
                self.assertTrue(link.is_symlink())
                self.assertEqual(auth.load_tokens()['refresh_token'], 'refresh')
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_failed_atomic_save_keeps_previous_tokens_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'token.json'
            target.write_text('{"access_token":"old"}')
            with patch.object(auth, 'TOKEN_FILE', str(target)), \
                 patch.object(auth.os, 'replace', side_effect=OSError('disk error')):
                with self.assertRaises(OSError):
                    auth.save_tokens({'access_token': 'new'})
            self.assertEqual(json.loads(target.read_text()), {'access_token': 'old'})
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_malformed_snapshots_never_publish_or_remove_existing_entities(self):
        variants = [None, {}, {'unexpected': 'schema changed'}, {'places': None},
                    {'places': [{'id': 'place'}]}]
        for path in [('places', 0, 'rooms'),
                     ('places', 0, 'rooms', 0, 'heaters'),
                     ('places', 0, 'rooms', 0, 'heaters', 0, 'is_offline')]:
            value = configuration()
            obj = value
            for key in path[:-1]:
                obj = obj[key]
            del obj[path[-1]]
            variants.append(value)
        malformed_late = configuration()
        malformed_late['places'].append({'id': 'broken'})
        variants.append(malformed_late)
        for value in variants:
            with self.subTest(value=value), patch.object(auth, 'api', return_value=value), \
                 patch.object(bridge, 'client') as client, \
                 patch.object(bridge, 'KNOWN_HEATERS', {'test'}), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertFalse(bridge.sync())
                client.publish.assert_not_called()
                self.assertEqual(bridge.KNOWN_HEATERS, {'test'})

    def test_explicit_empty_account_removes_entities(self):
        with patch.object(auth, 'api', return_value={'places': []}), \
             patch.object(bridge, 'KNOWN_HEATERS', {'test'}), \
             patch.object(bridge, 'HEATER_TO_ROOM', {}), patch.object(bridge, '_state_cache', {}), \
             patch.object(bridge, 'client') as client, contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(bridge.sync())
            client.publish.assert_any_call('homeassistant/climate/beha_test/config', '', retain=True)
            client.publish.assert_any_call('homeassistant/sensor/beha_test_last_reading/config', '', retain=True)

    def test_connect_callback_does_not_call_cloud_and_requests_sync(self):
        with patch.object(auth, 'get_discovery_info') as cloud, \
             patch.object(bridge, 'sync_requested', threading.Event()) as requested, \
             patch.object(bridge, 'mqtt_connected', threading.Event()), \
             patch.object(bridge, 'last_success', 1), contextlib.redirect_stdout(io.StringIO()):
            client = Mock()
            bridge.on_connect(client, None, {}, 0)
            cloud.assert_not_called()
            self.assertTrue(requested.is_set())
            client.publish.assert_called_once_with(bridge.BRIDGE_TOPIC, 'offline', qos=1, retain=True)

    def test_stale_sync_marks_offline_and_success_recovers(self):
        connected = threading.Event(); connected.set()
        with patch.object(bridge, 'mqtt_connected', connected), \
             patch.object(bridge, 'last_success', 100), \
             patch.object(bridge, 'sync_started', None), patch.object(bridge, 'client') as client:
            self.assertFalse(bridge.publish_health(now=281))
            client.publish.assert_called_with(bridge.BRIDGE_TOPIC, 'offline', qos=1, retain=True)
            with patch.object(auth, 'api', return_value=configuration()), \
                 patch.object(bridge, 'KNOWN_HEATERS', set()), \
                 patch.object(bridge, 'HEATER_TO_ROOM', {}), patch.object(bridge, '_state_cache', {}), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(bridge.sync())
            client.publish.assert_any_call(bridge.BRIDGE_TOPIC, 'online', qos=1, retain=True)
            client.publish.assert_any_call('beha/test/last_reading', '2026-11-26T17:03:19+00:00', retain=True)
            climate = next(json.loads(call.args[1]) for call in client.publish.call_args_list
                           if call.args[0] == 'homeassistant/climate/beha_test/config')
            self.assertEqual(climate['availability_mode'], 'all')
            self.assertEqual(len(climate['availability']), 2)
            self.assertNotIn('availability_topic', climate)

    def test_watchdog_exits_stalled_worker(self):
        connected = threading.Event(); connected.set()
        with patch.object(bridge, 'sync_started', 100), patch.object(bridge, 'last_success', 190), \
             patch.object(bridge.time, 'monotonic', return_value=221), \
             patch.object(bridge, 'mqtt_connected', connected), patch.object(bridge, 'client') as client, \
             patch.object(bridge, 'stop_event') as stop, \
             patch.object(bridge.os, '_exit', side_effect=SystemExit(1)) as exit_, \
             contextlib.redirect_stdout(io.StringIO()):
            stop.wait.return_value = False
            with self.assertRaises(SystemExit):
                bridge.health_watchdog()
            exit_.assert_called_once_with(1)
            client.publish.assert_called_with(bridge.BRIDGE_TOPIC, 'offline', qos=1, retain=True)

    def test_reading_age_is_not_replaced_with_poll_time(self):
        self.assertEqual(bridge.reading_timestamp('2026-03-14T09:53:59'), '2026-03-14T09:53:59+00:00')
        self.assertEqual(bridge.reading_timestamp(None), 'None')
        self.assertEqual(bridge.reading_timestamp('invalid'), 'None')


if __name__ == '__main__':
    unittest.main()
