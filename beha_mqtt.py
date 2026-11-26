#!/usr/bin/env python3
"""
BEHA SmartHeater MQTT Bridge for Home Assistant
-----------------------------------------------
Bridges the BEHA Cloud API to MQTT with Home Assistant Auto-Discovery.

Usage:
    python3 beha_mqtt.py --host <mqtt_broker_ip> --user <user> --pass <pass>

Requirements:
    pip3 install paho-mqtt
"""

import sys, time, json, argparse, threading, os, signal
from datetime import datetime, timezone
import paho.mqtt.client as mqtt
import beha_auth  # Uses your existing auth script

# Configuration
DISCOVERY_PREFIX = "homeassistant"
TOPIC_PREFIX     = "beha"
POLL_INTERVAL    = 60
BRIDGE_VERSION = "1.2.0"
BRIDGE_TOPIC = f"{TOPIC_PREFIX}/bridge/availability"
HEALTH_INTERVAL = 10
STALE_AFTER = 180
SYNC_DEADLINE = 120
VERIFY_DELAY     = 5   # Seconds to wait before verification sync after a command

# Global state
client = None
stop_event = threading.Event()
KNOWN_HEATERS = set()  # Track heater MACs for add/remove detection
sync_lock = threading.Lock()  # Prevent overlapping syncs
sync_requested = threading.Event()
progress_lock = threading.Lock()
last_success = None
sync_started = None
mqtt_connected = threading.Event()

def on_connect(client, userdata, flags, rc, properties=None):
    global last_success
    if rc == 0:
        print("Connected to MQTT broker; requesting cloud sync")
        with progress_lock:
            last_success = None
        client.publish(BRIDGE_TOPIC, "offline", qos=1, retain=True)
        client.subscribe(f"{TOPIC_PREFIX}/+/set")
        client.subscribe(f"{TOPIC_PREFIX}/+/mode/set")
        client.subscribe(f"{DISCOVERY_PREFIX}/status")
        mqtt_connected.set()
        sync_requested.set()  # No cloud I/O on the MQTT callback thread.
    else:
        print(f"MQTT connection failed: {rc}")


def on_disconnect(client, userdata, flags, rc, properties=None):
    mqtt_connected.clear()


def on_message(client, userdata, msg):
    """Handle incoming set temperature and mode commands — dispatches to a worker thread."""
    try:
        if msg.topic == f"{DISCOVERY_PREFIX}/status":
            if msg.payload == b"online":
                sync_requested.set()
            return
        parts = msg.topic.split("/")

        # Temperature command: beha/<mac>/set  payload: <temp>
        if len(parts) == 3 and parts[2] == "set":
            mac_address = parts[1]
            try:
                target_temp = float(msg.payload.decode())
            except ValueError:
                print(f"⚠️ Invalid temp payload: {msg.payload}")
                return
            # Handle in background thread so MQTT loop stays responsive
            threading.Thread(
                target=handle_set_temperature,
                args=(mac_address, target_temp),
                daemon=True
            ).start()

        # Mode command: beha/<mac>/mode/set  payload: "heat" or "off"
        elif len(parts) == 4 and parts[2] == "mode" and parts[3] == "set":
            mac_address = parts[1]
            mode = msg.payload.decode().strip().lower()
            threading.Thread(
                target=handle_set_mode,
                args=(mac_address, mode),
                daemon=True
            ).start()

    except Exception as e:
        print(f"❌ Error dispatching message: {e}")


def handle_set_temperature(mac_address, target_temp):
    """Set temperature: optimistic publish → API call → verification sync."""
    print(f"📥 Received set command: {mac_address} -> {target_temp}°C")
    room_id, place_id, heater_id = find_room_for_heater(mac_address)
    if not (room_id and place_id):
        print(f"❌ Could not find room for heater {mac_address}")
        return

    # 1. Optimistic state: tell HA the new temp immediately
    publish_optimistic_temp(mac_address, target_temp)
    print(f"⚡ Optimistic state published for {mac_address}: {target_temp}°C")

    # 2. Send to Beha Cloud API (with retry)
    try:
        print(f"   Setting room {room_id} temp via API...")
        beha_auth.api("PATCH", f"places/{place_id}/rooms/{room_id}/set_target_temperature",
                       {"target_temperature": target_temp})
        print(f"✅ API accepted temp {target_temp}°C for room {room_id}")
    except Exception as e:
        print(f"❌ API call failed for {mac_address}: {e}")
        # Even on failure, keep the optimistic state — next sync() will correct it
        #   This avoids flicker; if the user retries, it will work

    # 3. Schedule verification sync after delay
    threading.Timer(VERIFY_DELAY, verification_sync, args=(mac_address, "temp", target_temp)).start()


def handle_set_mode(mac_address, mode):
    """Set mode: optimistic publish → API call → verification sync."""
    print(f"📥 Received mode command: {mac_address} -> {mode}")
    room_id, place_id, heater_id = find_room_for_heater(mac_address)
    if not heater_id:
        print(f"❌ Could not find heater for {mac_address}")
        return

    is_enabled = mode == "heat"

    # 1. Optimistic state
    publish_optimistic_mode(mac_address, mode)
    print(f"⚡ Optimistic state published for {mac_address}: {mode}")

    # 2. Send to Beha Cloud API
    try:
        print(f"   {'Enabling' if is_enabled else 'Disabling'} heater {heater_id} via API...")
        beha_auth.api("PATCH", f"heaters/{heater_id}/change_enabled_state",
                       {"is_enabled": is_enabled})
        print(f"✅ API accepted mode '{mode}' for heater {heater_id}")
    except Exception as e:
        print(f"❌ API call failed for {mac_address}: {e}")

    # 3. Schedule verification sync
    threading.Timer(VERIFY_DELAY, verification_sync, args=(mac_address, "mode", mode)).start()


def publish_optimistic_temp(mac_address, target_temp):
    """Immediately publish the new target temp to MQTT so HA sees it without waiting for sync."""
    state_topic = f"{TOPIC_PREFIX}/{mac_address}/state"
    try:
        # Read the current retained state and update only target_temperature
        # We build from what we know in HEATER_TO_ROOM to avoid an API call
        current_state = get_last_known_state(mac_address)
        current_state["target_temperature"] = target_temp
        client.publish(state_topic, json.dumps(current_state), retain=True)
    except Exception as e:
        print(f"⚠️ Optimistic temp publish failed: {e}")


def publish_optimistic_mode(mac_address, mode):
    """Immediately publish the new mode to MQTT so HA sees it without waiting for sync."""
    state_topic = f"{TOPIC_PREFIX}/{mac_address}/state"
    try:
        current_state = get_last_known_state(mac_address)
        current_state["mode"] = mode
        client.publish(state_topic, json.dumps(current_state), retain=True)
    except Exception as e:
        print(f"⚠️ Optimistic mode publish failed: {e}")


# Cache of last-known state per heater for optimistic updates
_state_cache = {}

def get_last_known_state(mac_address):
    """Return the last known state dict for a heater, or a safe default."""
    return dict(_state_cache.get(mac_address, {
        "current_temperature": None,
        "target_temperature": None,
        "mode": "heat"
    }))


def verification_sync(mac_address, field, expected_value):
    # Coalesce verification with regular polling on the one cloud sync worker.
    sync_requested.set()


def find_room_for_heater(mac):
    """Finds the (room_id, place_id, heater_id) tuple for the heater with this MAC/ID."""
    return HEATER_TO_ROOM.get(mac, (None, None, None))

# Maps heater MAC -> (room_id, place_id, heater_id)
HEATER_TO_ROOM = {}

def publish_config(component, object_id, payload):
    client.publish(f"{DISCOVERY_PREFIX}/{component}/{object_id}/config",
                   json.dumps(payload), retain=True)


def publish_bridge_discovery():
    device = {"identifiers": ["beha_bridge"], "name": "BEHA Bridge",
              "manufacturer": "BEHA", "model": "Cloud MQTT Bridge",
              "sw_version": BRIDGE_VERSION}
    publish_config("binary_sensor", "beha_bridge_health", {
        "name": "Health", "unique_id": "beha_bridge_health", "device": device,
        "device_class": "connectivity", "entity_category": "diagnostic",
        "state_topic": BRIDGE_TOPIC, "payload_on": "online", "payload_off": "offline",
        "expire_after": HEALTH_INTERVAL * 3})
    publish_config("sensor", "beha_bridge_last_sync", {
        "name": "Last successful sync", "unique_id": "beha_bridge_last_sync",
        "device": device, "device_class": "timestamp", "entity_category": "diagnostic",
        "state_topic": f"{TOPIC_PREFIX}/bridge/last_sync"})


def publish_heater_discovery(room, heater):
    hid = heater["device_unique_id"]
    device = {"identifiers": [f"beha_{hid}"],
              "name": f"{heater['name']} ({room['room_name']})",
              "manufacturer": "BEHA", "model": "SmartHeater"}
    payload = {
        "name": None, "unique_id": f"beha_{hid}_climate", "device": device,
        "temperature_unit": "C", "min_temp": 5, "max_temp": 30,
        "temp_step": 0.5, "modes": ["heat", "off"],
        "current_temperature_topic": f"{TOPIC_PREFIX}/{hid}/state",
        "current_temperature_template": "{{ value_json.current_temperature }}",
        "temperature_command_topic": f"{TOPIC_PREFIX}/{hid}/set",
        "mode_command_topic": f"{TOPIC_PREFIX}/{hid}/mode/set",
        "mode_state_topic": f"{TOPIC_PREFIX}/{hid}/state",
        "mode_state_template": "{{ value_json.mode }}",
        "temperature_state_topic": f"{TOPIC_PREFIX}/{hid}/state",
        "temperature_state_template": "{{ value_json.target_temperature }}",
        "availability": [{"topic": BRIDGE_TOPIC},
                         {"topic": f"{TOPIC_PREFIX}/{hid}/availability"}],
        "availability_mode": "all"
    }
    publish_config("climate", f"beha_{hid}", payload)
    # Keep the last reading visible even when the heater or bridge is offline.
    # A timestamp lets HA calculate age continuously without a running bridge.
    publish_config("sensor", f"beha_{hid}_last_reading", {
        "name": "Last reading", "unique_id": f"beha_{hid}_last_reading",
        "device": device, "device_class": "timestamp", "entity_category": "diagnostic",
        "state_topic": f"{TOPIC_PREFIX}/{hid}/last_reading"})


def reading_timestamp(value):
    if not isinstance(value, str):
        return "None"  # HA's timestamp sensor reset payload.
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # BEHA returns naive UTC timestamps, as used by its cloud backend.
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        return "None"


def publish_health(now=None):
    now = time.monotonic() if now is None else now
    with progress_lock:
        healthy = last_success is not None and now - last_success < STALE_AFTER
        stalled = sync_started is not None and now - sync_started > SYNC_DEADLINE
    if mqtt_connected.is_set():
        client.publish(BRIDGE_TOPIC, "online" if healthy and not stalled else "offline",
                       qos=1, retain=True)
    return stalled


def health_watchdog():
    while not stop_event.wait(HEALTH_INTERVAL):
        if publish_health():
            print("Cloud sync exceeded 120 seconds; exiting for Supervisor restart", flush=True)
            # A stuck Python worker cannot be safely killed. LWT covers abrupt exit;
            # the add-on watchdog restarts the process. run.sh uses exec.
            os._exit(1)


def sync():
    """Fetch/validate the whole snapshot before publishing or deleting entities."""
    global KNOWN_HEATERS, last_success, sync_started
    with sync_lock:
        with progress_lock:
            sync_started = time.monotonic()
        try:
            discovery_data = beha_auth.get_discovery_info()
            publish_bridge_discovery()
            current_heaters = set()
            for room in discovery_data:
                for h in room["heaters"]:
                    hid = h["device_unique_id"]
                    current_heaters.add(hid)
                    HEATER_TO_ROOM[hid] = (room["room_id"], room["place_id"], h["id"])
                    publish_heater_discovery(room, h)
                    state = {"current_temperature": room.get("latest_temperature"),
                             "target_temperature": room["target_temperature"],
                             "mode": "heat" if h.get("is_enabled", True) else "off"}
                    _state_cache[hid] = state
                    client.publish(f"{TOPIC_PREFIX}/{hid}/availability",
                                   "offline" if h.get("is_offline", False) else "online", retain=True)
                    client.publish(f"{TOPIC_PREFIX}/{hid}/state", json.dumps(state), retain=True)
                    client.publish(f"{TOPIC_PREFIX}/{hid}/last_reading",
                                   reading_timestamp(h.get("latest_reading_timestamp")), retain=True)

            for hid in KNOWN_HEATERS - current_heaters:
                for component, object_id in [("climate", f"beha_{hid}"),
                                             ("sensor", f"beha_{hid}_last_reading")]:
                    client.publish(f"{DISCOVERY_PREFIX}/{component}/{object_id}/config", "", retain=True)
                for suffix in ("state", "availability", "last_reading"):
                    client.publish(f"{TOPIC_PREFIX}/{hid}/{suffix}", "", retain=True)
                HEATER_TO_ROOM.pop(hid, None)
                _state_cache.pop(hid, None)
            KNOWN_HEATERS = current_heaters
            client.publish(f"{TOPIC_PREFIX}/bridge/last_sync",
                           datetime.now(timezone.utc).isoformat(), retain=True)
            with progress_lock:
                last_success = time.monotonic()
            publish_health()
            print(f"Sync complete ({len(current_heaters)} heaters)")
            return True
        except Exception as e:
            print(f"Error in sync: {e}")
            return False
        finally:
            with progress_lock:
                sync_started = None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="homeassistant")
    parser.add_argument("--port", type=int, default=1883)
    parser.add_argument("--user")
    parser.add_argument("--passw")
    args = parser.parse_args()
    global client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if args.user:
        client.username_pw_set(args.user, args.passw)
    client.will_set(BRIDGE_TOPIC, "offline", qos=1, retain=True)
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: (stop_event.set(), sync_requested.set()))
    client.connect(args.host, args.port, 30)
    client.loop_start()
    threading.Thread(target=health_watchdog, name="health-watchdog", daemon=True).start()
    try:
        while not stop_event.is_set():
            sync_requested.wait(POLL_INTERVAL)
            sync_requested.clear()
            if not stop_event.is_set() and mqtt_connected.is_set():
                sync()
    finally:
        stop_event.set()
        if client.is_connected():
            client.publish(BRIDGE_TOPIC, "offline", qos=1, retain=True).wait_for_publish(timeout=2)
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
