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

# MQTT return code descriptions
MQTT_RC_CODES = {
    0: "Connected successfully",
    1: "Incorrect protocol version",
    2: "Invalid client identifier",
    3: "Server unavailable",
    4: "Bad username or password",
    5: "Not authorized",
}

# Heater MAC -> heater UUID mapping (for API calls)
HEATER_MAC_TO_UUID = {}
# Heater MAC -> Room ID mapping
HEATER_TO_ROOM = {}

def on_connect(client, userdata, flags, rc, properties=None):
    rc_val = rc if isinstance(rc, int) else rc.value
    desc = MQTT_RC_CODES.get(rc_val, f"Unknown code {rc_val}")
    if rc_val == 0:
        print(f"✅ Connected to MQTT Broker! ({desc})")
        # Subscribe to set commands and mode commands
        client.subscribe(f"{TOPIC_PREFIX}/+/set")
        client.subscribe(f"{TOPIC_PREFIX}/+/mode/set")
        print(f"📡 Subscribed to {TOPIC_PREFIX}/+/set and {TOPIC_PREFIX}/+/mode/set")
        # Publish discovery immediately
        publish_discovery()
    else:
        print(f"❌ Failed to connect: {desc} (rc={rc_val})")

def on_disconnect(client, userdata, flags, rc, properties=None):
    rc_val = rc if isinstance(rc, int) else rc.value
    print(f"⚠️ Disconnected from MQTT Broker (rc={rc_val}). Will auto-reconnect...")

def on_message(client, userdata, msg):
    """Handle incoming set temperature and mode commands"""
    try:
        parts = msg.topic.split("/")
        payload = msg.payload.decode().strip()
        
        if len(parts) >= 3:
            mac_address = parts[1]
            room_id = HEATER_TO_ROOM.get(mac_address)
            heater_uuid = HEATER_MAC_TO_UUID.get(mac_address)
            
            if not room_id or not heater_uuid:
                print(f"❌ Could not find room/heater for MAC {mac_address}")
                return
            
            room_name = next((r["name"] for r in _cached_rooms if r["id"] == room_id), room_id)
            
            # MODE COMMAND: beha/<mac>/mode/set  payload: "heat" or "off"
            if len(parts) == 4 and parts[2] == "mode" and parts[3] == "set":
                print(f"📥 Mode command: {mac_address} -> {payload}")
                
                if payload == "off":
                    print(f"   ❄️ Turning OFF '{room_name}' via native API...")
                    beha_auth.api("PATCH", f"heaters/{heater_uuid}/change_enabled_state",
                                  {"is_enabled": False})
                    print(f"   ✅ Heater disabled")
                    
                elif payload == "heat":
                    print(f"   🔥 Turning ON '{room_name}' via native API...")
                    beha_auth.api("PATCH", f"heaters/{heater_uuid}/change_enabled_state",
                                  {"is_enabled": True})
                    print(f"   ✅ Heater enabled")
                
                time.sleep(2)
                update_state()
                print(f"   📡 State published to MQTT")
                return
            
            # TEMPERATURE COMMAND: beha/<mac>/set  payload: <temp>
            if len(parts) == 3 and parts[2] == "set":
                try:
                    target_temp = float(payload)
                except ValueError:
                    print(f"⚠️ Invalid temp payload: {payload}")
                    return

                print(f"📥 Temp command: {mac_address} -> {target_temp}°C")
                print(f"   🔧 Setting room '{room_name}' to {target_temp}°C...")
                
                beha_auth.api("PATCH", f"places/{beha_auth.PLACE_ID}/rooms/{room_id}/set_target_temperature",
                              {"target_temperature": target_temp})
                print(f"   📤 PATCH sent. Waiting 2s...")

                time.sleep(2)

                # Read back to verify
                try:
                    place = beha_auth.api("GET", f"places/{beha_auth.PLACE_ID}")
                    for room in place["rooms"]:
                        if room["id"] == room_id:
                            confirmed_temp = room["target_temperature"]
                            current_temp = room["latest_temperature"]
                            if abs(confirmed_temp - target_temp) < 0.1:
                                print(f"   ✅ CONFIRMED: '{room_name}' -> {confirmed_temp}°C (current: {current_temp}°C)")
                            else:
                                print(f"   ⚠️ MISMATCH: Requested {target_temp}°C but got {confirmed_temp}°C")
                            break
                except Exception as e:
                    print(f"   ⚠️ Read-back failed: {e}")

                update_state()
                print(f"   📡 State published to MQTT")

    except Exception as e:
        print(f"❌ Error handling message: {e}")

# Cached rooms from last API call
_cached_rooms = []

def fetch_place():
    """Fetch all rooms and heaters in a single API call"""
    global _cached_rooms
    beha_auth.discover()  # Auto-discover PLACE_ID on first call
    place = beha_auth.api("GET", f"places/{beha_auth.PLACE_ID}")
    _cached_rooms = place["rooms"]
    for room in _cached_rooms:
        for h in room["heaters"]:
            mac = h["device_unique_id"]
            HEATER_TO_ROOM[mac] = room["id"]
            HEATER_MAC_TO_UUID[mac] = h["id"]
    return _cached_rooms

def publish_discovery():
    """Publishes HA Auto-Discovery payloads for all known heaters"""
    print("📤 Publishing Home Assistant Auto-Discovery config...")
    try:
        rooms = fetch_place()
        for room in rooms:
            for h in room["heaters"]:
                hid = h["device_unique_id"]
                
                device_info = {
                    "identifiers": [f"beha_{hid}"],
                    "name": f"{h['name']} ({room['name']})",
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
                
                topic = f"{DISCOVERY_PREFIX}/climate/beha_{hid}/config"
                client.publish(topic, json.dumps(payload), retain=True)

        print(f"   ✅ Published discovery for {sum(len(r['heaters']) for r in rooms)} heaters in {len(rooms)} rooms")
                
    except Exception as e:
        print(f"❌ Error in discovery: {e}")

def update_state():
    """Polls Cloud API (single call) and publishes state for all heaters"""
    try:
        rooms = fetch_place()
        
        for room in rooms:
            target_temp = room["target_temperature"]
            
            for h in room["heaters"]:
                hid = h["device_unique_id"]
                is_enabled = h["is_enabled"]
                is_offline = h["is_offline"]
                current_temp = h.get("latest_reading_temperature", room["latest_temperature"])
                
                # Mode: "off" if disabled or offline, "heat" otherwise
                if not is_enabled or is_offline:
                    mode = "off"
                else:
                    mode = "heat"
                
                state_payload = {
                    "current_temperature": current_temp,
                    "target_temperature": target_temp,
                    "mode": mode
                }

                client.publish(f"{TOPIC_PREFIX}/{hid}/availability", "offline" if is_offline else "online", retain=True)
                client.publish(f"{TOPIC_PREFIX}/{hid}/state", json.dumps(state_payload), retain=True)
                
    except Exception as e:
        print(f"❌ Error in update loop: {e}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="homeassistant", help="MQTT Broker Host")
    parser.add_argument("--port", type=int, default=1883, help="MQTT Broker Port")
    parser.add_argument("--user", help="MQTT Username")
    parser.add_argument("--passw", help="MQTT Password")
    args = parser.parse_args()

    global client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="beha_bridge", protocol=mqtt.MQTTv311)
    if args.user:
        client.username_pw_set(args.user, args.passw)
    
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect

    print(f"🔌 Connecting to MQTT Broker at {args.host}:{args.port} (user={args.user})...")
    try:
        client.connect(args.host, args.port, 60)
        print(f"🔌 connect() returned, waiting for on_connect callback...")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        sys.exit(1)

    client.loop_start()

    try:
        while True:
            update_state()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("\n👋 Stopping...")
        client.loop_stop()
        sys.exit(0)

if __name__ == "__main__":
    main()
