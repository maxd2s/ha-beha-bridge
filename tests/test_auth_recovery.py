import contextlib
import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock, patch

import beha_auth
import beha_mqtt


class AuthRecoveryTests(unittest.TestCase):
    def test_stalled_refresh_times_out_without_overwriting_tokens(self):
        release = threading.Event()

        class StalledEndpoint(BaseHTTPRequestHandler):
            def do_POST(self):
                release.wait(2)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), StalledEndpoint)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(beha_auth, "TOKEN_URL", f"http://127.0.0.1:{server.server_port}/token"), \
                 patch.object(beha_auth, "AUTH_TIMEOUT", 0.05), \
                 patch.object(beha_auth, "load_tokens", return_value={"refresh_token": "test"}), \
                 patch.object(beha_auth, "save_tokens") as save:
                with self.assertRaises(TimeoutError):
                    beha_auth.refresh()
                save.assert_not_called()
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join()

    def test_api_recovers_after_refresh_timeout(self):
        response = Mock(status=200)
        response.read.return_value = json.dumps({"places": []}).encode()
        with patch.object(beha_auth, "get_token", side_effect=[TimeoutError(), "test"]) as token, \
             patch.object(beha_auth.urllib.request, "urlopen") as request, \
             patch.object(beha_auth.time, "sleep"), contextlib.redirect_stdout(io.StringIO()):
            request.return_value.__enter__.return_value = response
            self.assertEqual(beha_auth.api("GET", "users/configuration"), {"places": []})
            self.assertEqual(token.call_count, 2)

    def test_failed_sync_preserves_entities_and_next_cycle_recovers(self):
        room = {"room_id": "room", "place_id": "place", "room_name": "Office",
                "target_temperature": 18, "latest_temperature": 17.5,
                "heaters": [{"device_unique_id": "test", "id": "heater", "name": "Office"}]}
        with patch.object(beha_mqtt, "client") as client, \
             patch.object(beha_mqtt, "KNOWN_HEATERS", {"test"}), \
             patch.object(beha_mqtt, "HEATER_TO_ROOM", {}), \
             patch.object(beha_mqtt, "_state_cache", {}), \
             patch.object(beha_auth, "api", side_effect=TimeoutError()), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            beha_mqtt.sync()
            client.publish.assert_not_called()
            self.assertEqual(beha_mqtt.KNOWN_HEATERS, {"test"})
            with patch.object(beha_auth, "get_discovery_info", return_value=[room]):
                beha_mqtt.sync()
            client.publish.assert_any_call("beha/test/availability", "online", retain=True)


if __name__ == "__main__":
    unittest.main()
