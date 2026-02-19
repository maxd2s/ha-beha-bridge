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

import sys, time, json, argparse, threading
import paho.mqtt.client as mqtt
import beha_auth  # Uses your existing auth script

# Configuration
DISCOVERY_PREFIX = "homeassistant"
TOPIC_PREFIX     = "beha"
POLL_INTERVAL    = 60

# Global state
client = None
stop_event = threading.Event()
KNOWN_HEATERS = set()  # Track heater MACs for add/remove detection

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        print("✅ Connected to MQTT Broker!")
        # Subscribe to temperature and mode commands
        client.subscribe(f"{TOPIC_PREFIX}/+/set")
        client.subscribe(f"{TOPIC_PREFIX}/+/mode/set")
        # Publish discovery immediately
        publish_discovery()
    else:
        print(f"❌ Failed to connect, return code {rc}")

def on_message(client, userdata, msg):
    """Handle incoming set temperature and mode commands"""
    try:
        parts = msg.topic.split("/")

        # Temperature command: beha/<mac>/set  payload: <temp>
        if len(parts) == 3 and parts[2] == "set":
            mac_address = parts[1]
            try:
                target_temp = float(msg.payload.decode())
            except ValueError:
                print(f"⚠️ Invalid temp payload: {msg.payload}")
                return

            print(f"📥 Received set command: {mac_address} -> {target_temp}°C")
            room_id, place_id, heater_id = find_room_for_heater(mac_address)
            if room_id and place_id:
                print(f"   Setting room {room_id} temp...")
                beha_auth.api("PATCH", f"places/{place_id}/rooms/{room_id}/set_target_temperature",
                              {"target_temperature": target_temp})
                update_state()
            else:
                print(f"❌ Could not find room for heater {mac_address}")

        # Mode command: beha/<mac>/mode/set  payload: "heat" or "off"
        elif len(parts) == 4 and parts[2] == "mode" and parts[3] == "set":
            mac_address = parts[1]
            mode = msg.payload.decode().strip().lower()
            print(f"📥 Received mode command: {mac_address} -> {mode}")

            room_id, place_id, heater_id = find_room_for_heater(mac_address)
            if heater_id:
                is_enabled = mode == "heat"
                print(f"   {'Enabling' if is_enabled else 'Disabling'} heater {heater_id}...")
                beha_auth.api("PATCH", f"heaters/{heater_id}/change_enabled_state",
                              {"is_enabled": is_enabled})
                update_state()
            else:
                print(f"❌ Could not find heater for {mac_address}")

    except Exception as e:
        print(f"❌ Error handling message: {e}")

def find_room_for_heater(mac):
    """Finds the (room_id, place_id, heater_id) tuple for the heater with this MAC/ID."""
    return HEATER_TO_ROOM.get(mac, (None, None, None))

# Maps heater MAC -> (room_id, place_id, heater_id)
HEATER_TO_ROOM = {}

def publish_discovery():
    """Publishes HA Auto-Discovery payloads for all known heaters"""
    print("📤 Publishing Home Assistant Auto-Discovery config...")
    try:
        discovery_data = beha_auth.get_discovery_info()
        
        for room in discovery_data:
            rid = room["room_id"]
            pid = room["place_id"]
            name = room["room_name"]
            
            for h in room["heaters"]:
                hid = h["device_unique_id"]  # e.g. f412fa5adefc
                HEATER_TO_ROOM[hid] = (rid, pid, h["id"])
                
                # Device Info
                device_info = {
                    "identifiers": [f"beha_{hid}"],
                    "name": f"{h['name']} ({name})",
                    "manufacturer": "BEHA",
                    "model": "SmartHeater Gen1",
                    "sw_version": "1.0"
                }
                
                # Climate Entity Config
                payload = {
                    "name": None,  # Use device name
                    "unique_id": f"beha_{hid}_climate",
                    "device": device_info,
                    "temperature_unit": "C",
                    "min_temp": 5,
                    "max_temp": 30,
                    "temp_step": 0.5,
                    "modes": ["heat", "off"],
                    
                    "current_temperature_topic": f"{TOPIC_PREFIX}/{hid}/state",
                    "current_temperature_template": "{{ value_json.current_temperature }}",
                    
                    "temperature_command_topic": f"{TOPIC_PREFIX}/{hid}/set",
                    
                    "mode_command_topic": f"{TOPIC_PREFIX}/{hid}/mode/set",
                    "mode_state_topic": f"{TOPIC_PREFIX}/{hid}/state",
                    "mode_state_template": "{{ value_json.mode }}",
                    
                    "temperature_state_topic": f"{TOPIC_PREFIX}/{hid}/state",
                    "temperature_state_template": "{{ value_json.target_temperature }}",
                    
                    "availability_topic": f"{TOPIC_PREFIX}/{hid}/availability"
                }
                
                topic = f"{DISCOVERY_PREFIX}/climate/beha_{hid}/config"
                client.publish(topic, json.dumps(payload), retain=True)
                print(f"   Published discovery for {h['name']}")
                
    except Exception as e:
        print(f"❌ Error in discovery: {e}")

def sync():
    """Full sync: re-publish discovery (handles adds/renames), update state, remove stale entities."""
    global KNOWN_HEATERS
    try:
        discovery_data = beha_auth.get_discovery_info()
        current_heaters = set()

        for room in discovery_data:
            rid = room["room_id"]
            pid = room["place_id"]
            name = room["room_name"]
            target_temp = room["target_temperature"]
            current_temp = room.get("latest_temperature")

            for h in room["heaters"]:
                hid = h["device_unique_id"]
                current_heaters.add(hid)
                HEATER_TO_ROOM[hid] = (rid, pid, h["id"])

                # ── Discovery (re-publish every cycle for renames / new devices) ──
                device_info = {
                    "identifiers": [f"beha_{hid}"],
                    "name": f"{h['name']} ({name})",
                    "manufacturer": "BEHA",
                    "model": "SmartHeater Gen1",
                    "sw_version": "1.0"
                }
                payload = {
                    "name": None,
                    "unique_id": f"beha_{hid}_climate",
                    "device": device_info,
                    "temperature_unit": "C",
                    "min_temp": 5,
                    "max_temp": 30,
                    "temp_step": 0.5,
                    "modes": ["heat", "off"],
                    "current_temperature_topic": f"{TOPIC_PREFIX}/{hid}/state",
                    "current_temperature_template": "{{ value_json.current_temperature }}",
                    "temperature_command_topic": f"{TOPIC_PREFIX}/{hid}/set",
                    "mode_command_topic": f"{TOPIC_PREFIX}/{hid}/mode/set",
                    "mode_state_topic": f"{TOPIC_PREFIX}/{hid}/state",
                    "mode_state_template": "{{ value_json.mode }}",
                    "temperature_state_topic": f"{TOPIC_PREFIX}/{hid}/state",
                    "temperature_state_template": "{{ value_json.target_temperature }}",
                    "availability_topic": f"{TOPIC_PREFIX}/{hid}/availability"
                }
                client.publish(f"{DISCOVERY_PREFIX}/climate/beha_{hid}/config",
                               json.dumps(payload), retain=True)

                # ── State ──
                is_offline = h.get("is_offline", False)
                is_enabled = h.get("is_enabled", True)
                state_payload = {
                    "current_temperature": current_temp,
                    "target_temperature": target_temp,
                    "mode": "heat" if (is_enabled and not is_offline) else "off"
                }
                client.publish(f"{TOPIC_PREFIX}/{hid}/availability",
                               "offline" if is_offline else "online", retain=True)
                client.publish(f"{TOPIC_PREFIX}/{hid}/state",
                               json.dumps(state_payload), retain=True)

                if hid not in KNOWN_HEATERS:
                    print(f"🆕 New heater discovered: {h['name']} ({hid})")

        # ── Remove stale heaters (deleted in Beha app) ──
        removed = KNOWN_HEATERS - current_heaters
        for hid in removed:
            print(f"🗑️  Heater {hid} removed — clearing from HA")
            # Empty retained payload removes entity from HA
            client.publish(f"{DISCOVERY_PREFIX}/climate/beha_{hid}/config", "", retain=True)
            client.publish(f"{TOPIC_PREFIX}/{hid}/state", "", retain=True)
            client.publish(f"{TOPIC_PREFIX}/{hid}/availability", "", retain=True)
            HEATER_TO_ROOM.pop(hid, None)

        KNOWN_HEATERS = current_heaters

    except Exception as e:
        print(f"❌ Error in sync: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="homeassistant", help="MQTT Broker Host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT Broker Port")
    parser.add_argument("--user", help="MQTT Username")
    parser.add_argument("--passw", help="MQTT Password")
    args = parser.parse_args()

    global client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if args.user:
        client.username_pw_set(args.user, args.passw)
    
    client.on_connect = on_connect
    client.on_message = on_message

    print(f"🔌 Connecting to MQTT Broker at {args.host}:{args.port}...")
    try:
        client.connect(args.host, args.port, 60)
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)

    # Start MQTT loop in background
    client.loop_start()

    # Main polling loop
    try:
        while True:
            sync()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\n👋 Stopping...")
        client.loop_stop()
        sys.exit(0)

if __name__ == "__main__":
    main()
