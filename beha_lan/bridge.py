#!/usr/bin/env python3
"""BEHA Gen 2 LAN MQTT adapter. Never logs raw device topics or payloads."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
from pathlib import Path
import queue
import re
import signal
import threading
import time

import paho.mqtt.client as mqtt

VERSION = "0.1.2"
PREFIX = "beha_lan"
BRIDGE = f"{PREFIX}/bridge/availability"
ID = re.compile(r"^[0-9a-f]{12}$")
LOG = logging.getLogger(__name__)
HEATER_MODES = {0: "Off", 1: "Frost protection (5°C)", 2: "Local dial",
                3: "Remote target", 4: "Weekly schedule"}


def diagnostic_label(key, value):
    """Decoded from firmware 2.3; never guess meanings for new values/bits."""
    if key == "heater_mode":
        return HEATER_MODES.get(value, f"Unknown mode ({value})")
    if key == "child_lock":
        return {0: "Unlocked", 1: "Locked"}.get(value, f"Unknown lock state ({value})")
    if key == "alarm_flags":
        # Firmware 2.3 hardcodes zero in its JSON format string. Zero is not
        # evidence of a functioning alarm monitor or an absence of faults.
        return "No flags reported" if value == 0 else f"Unknown alarm flags (0x{value:X})"
    raise ValueError("Unsupported diagnostic")


def device_id(value):
    value = str(value).lower().replace(":", "").replace("-", "")
    if not ID.fullmatch(value):
        raise ValueError("Heater ID must be a 12-digit MAC address")
    return value


def settings(data):
    heaters = {}
    for item in data.get("heaters", []):
        hid = device_id(item["device_id"])
        if hid in heaters:
            raise ValueError("Duplicate heater ID")
        name = item.get("name", "").strip() or f"BEHA {hid[-6:].upper()}"
        heaters[hid] = name
    stale = data.get("stale_after", 90)
    if type(stale) is not int or not 60 <= stale <= 600:
        raise ValueError("stale_after must be 60–600 seconds")
    auto = data.get("auto_discover", False)
    if type(auto) is not bool:
        raise ValueError("auto_discover must be a boolean")
    return heaters, stale, auto


def numeric(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def telemetry(payload):
    if len(payload) > 16384:
        raise ValueError("Oversize telemetry")
    data = json.loads(payload)
    temperature = data["com.devicedrive.heater"]
    beha = data["com.beha.heater"]
    if not isinstance(temperature, dict) or not isinstance(beha, dict):
        raise ValueError("Invalid telemetry sections")
    target, current = temperature["target_temperature"], temperature["current_temperature"]
    if not numeric(target, 0, 35) or not numeric(current, -40, 125):
        raise ValueError("Invalid temperatures")
    result = {"target_temperature": target, "current_temperature": current,
              "mode": "heat" if beha.get("heater_mode") in (2, 3) else "None"}
    for key in ("heater_mode", "child_lock", "alarm_flags"):
        if type(beha.get(key)) is int and 0 <= beha[key] <= 65535:
            result[key] = beha[key]
            result[f"{key}_text"] = diagnostic_label(key, beha[key])
    # Schedule names/IDs and cloud-token-bearing topics are deliberately not relayed.
    return result


def target_command(payload):
    if len(payload) > 32:
        raise ValueError("Oversize command")
    value = float(payload)
    if not numeric(value, 5, 30) or value * 2 != round(value * 2):
        raise ValueError("Target must be 5–30°C in half-degree steps")
    return json.dumps({"com.devicedrive.heater": {"target_temperature": str(value)}})


class Bridge:
    def __init__(self, client, options, clock=time.monotonic):
        self.client = client
        self.names, self.stale_after, self.auto_discover = settings(options)
        self.clock = clock
        self.seen = {}
        self.online = set()
        self.connected = False

    def publish(self, topic, payload, retain=True, qos=1):
        if isinstance(payload, dict):
            payload = json.dumps(payload, allow_nan=False)
        return self.client.publish(topic, payload, qos=qos, retain=retain)

    def config(self, component, object_id, payload):
        self.publish(f"homeassistant/{component}/{object_id}/config", payload)

    def discovery(self, hid):
        base = f"{PREFIX}/{hid}"
        uid = f"beha_lan_{hid}"
        device = {"identifiers": [uid], "name": self.names[hid],
                  "manufacturer": "BEHA", "model": "SmartHeater Gen 2 (LAN)"}
        availability = [{"topic": BRIDGE}, {"topic": f"{base}/availability"}]
        common = {"device": device, "availability": availability, "availability_mode": "all"}
        self.config("climate", uid, {
            **common, "name": None, "unique_id": f"{uid}_climate",
            "default_entity_id": f"climate.{uid}", "temperature_unit": "C",
            "min_temp": 5, "max_temp": 30, "temp_step": 0.5, "modes": ["heat"],
            "temperature_command_topic": f"{base}/target/set", "retain": False,
            "temperature_state_topic": f"{base}/state",
            "temperature_state_template": "{{ value_json.target_temperature }}",
            "current_temperature_topic": f"{base}/state",
            "current_temperature_template": "{{ value_json.current_temperature }}",
            "mode_state_topic": f"{base}/state",
            "mode_state_template": "{{ value_json.mode }}",
            "optimistic": False})
        for key, name in (("heater_mode", "Control mode"), ("child_lock", "Child lock"),
                          ("alarm_flags", "Alarm status")):
            attributes = "{{ {'raw_code': value_json." + key + " | default(none)} | tojson }}"
            if key == "alarm_flags":
                attributes = ("{{ {'raw_code': value_json.alarm_flags | default(none), "
                              "'reporting_note': 'Tested firmware 2.3 hardcodes zero; this is not a fault-health check.'} | tojson }}")
            self.config("sensor", f"{uid}_{key}", {
                **common, "name": name, "unique_id": f"{uid}_{key}",
                "default_entity_id": f"sensor.{uid}_{key}", "entity_category": "diagnostic",
                "state_topic": f"{base}/state",
                "value_template": "{{ value_json." + key + "_text | default('None') }}",
                "json_attributes_topic": f"{base}/state", "json_attributes_template": attributes})
        self.config("sensor", f"{uid}_last_reading", {
            "device": device, "name": "Last reading", "unique_id": f"{uid}_last_reading",
            "default_entity_id": f"sensor.{uid}_last_reading", "device_class": "timestamp",
            "entity_category": "diagnostic", "state_topic": f"{base}/last_reading"})
        self.config("binary_sensor", f"{uid}_connection", {
            **common, "name": "Connection", "unique_id": f"{uid}_connection",
            "availability": [{"topic": BRIDGE}],
            "default_entity_id": f"binary_sensor.{uid}_connection",
            "device_class": "connectivity", "entity_category": "diagnostic",
            "state_topic": f"{base}/availability", "payload_on": "online", "payload_off": "offline"})

    def all_discovery(self):
        device = {"identifiers": ["beha_lan_bridge"], "name": "BEHA LAN Bridge",
                  "manufacturer": "Community", "model": "LAN MQTT adapter", "sw_version": VERSION}
        self.config("binary_sensor", "beha_lan_bridge_health", {
            "device": device, "name": "Health", "unique_id": "beha_lan_bridge_health",
            "default_entity_id": "binary_sensor.beha_lan_bridge_health", "device_class": "connectivity",
            "entity_category": "diagnostic", "state_topic": BRIDGE,
            "payload_on": "online", "payload_off": "offline", "expire_after": 30})
        for hid in self.names:
            self.discovery(hid)

    def connect(self):
        self.connected = True
        self.seen.clear()
        self.online.clear()
        self.publish(BRIDGE, "offline")
        for hid in self.names:
            self.publish(f"{PREFIX}/{hid}/availability", "offline")
        self.all_discovery()
        self.client.subscribe([("devices/+/messages/events/#", 1),
                               (f"{PREFIX}/+/target/set", 1), ("homeassistant/status", 1)])
        self.tick()

    def disconnect(self):
        self.connected = False
        self.seen.clear()
        self.online.clear()

    def handle(self, topic, payload, retained=False, received=None):
        now = self.clock()
        received = now if received is None else received
        if topic == "homeassistant/status":
            if payload == b"online":
                self.all_discovery()
            return
        # Retained commands must never replay, and retained telemetry cannot prove liveness.
        if retained or not self.connected:
            return
        parts = topic.split("/")
        if len(parts) >= 5 and parts[0] == "devices" and parts[2:4] == ["messages", "events"]:
            hid = parts[1]
            if not ID.fullmatch(hid) or (hid not in self.names and not self.auto_discover):
                return
            if now - received >= self.stale_after:
                return
            try:
                state = telemetry(payload)
            except (ValueError, KeyError, TypeError, UnicodeError):
                LOG.warning("Ignored invalid heater telemetry")
                return
            if hid not in self.names:
                self.names[hid] = f"BEHA {hid[-6:].upper()}"
                self.discovery(hid)
            self.seen[hid] = received
            self.online.add(hid)
            stamp = (datetime.now(timezone.utc) - timedelta(seconds=max(0, now - received))).isoformat()
            self.publish(f"{PREFIX}/{hid}/state", state)
            self.publish(f"{PREFIX}/{hid}/last_reading", stamp)
            self.publish(f"{PREFIX}/{hid}/availability", "online")
        elif len(parts) == 4 and parts[0] == PREFIX and parts[2:] == ["target", "set"]:
            hid = parts[1]
            if now - received > 5 or hid not in self.seen or now - self.seen[hid] >= self.stale_after:
                LOG.warning("Rejected command: heater has no fresh LAN reading")
                return
            try:
                command = target_command(payload)
            except (ValueError, TypeError, UnicodeError):
                LOG.warning("Rejected invalid temperature command")
                return
            # Never publish an optimistic state; HA changes only on the heater's reply.
            # QoS 0 avoids paho's outgoing QoS 1 reconnect queue replaying a stale
            # setpoint after a network outage. Actual heater feedback confirms success.
            result = self.publish(f"devices/{hid}/messages/devicebound/command", command,
                                  retain=False, qos=0)
            if result.rc != mqtt.MQTT_ERR_SUCCESS:
                LOG.warning("Temperature command was not sent; waiting for reconnection")

    def tick(self):
        if not self.connected:
            return
        now = self.clock()
        for hid in list(self.online):
            if now - self.seen[hid] >= self.stale_after:
                self.publish(f"{PREFIX}/{hid}/availability", "offline")
                self.online.remove(hid)
        self.publish(BRIDGE, "online")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--options", default="/data/options.json")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    options = json.loads(Path(args.options).read_text())
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.username_pw_set(os.environ.get("MQTT_USER"), os.environ.get("MQTT_PASSWORD"))
    if os.environ.get("MQTT_TLS", "false").lower() == "true":
        client.tls_set()
    client.will_set(BRIDGE, "offline", qos=1, retain=True)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.max_queued_messages_set(1000)
    bridge = Bridge(client, options)
    events = queue.Queue(maxsize=1000)
    stopped = threading.Event()
    progress = [time.monotonic()]

    def enqueue(event):
        try:
            events.put_nowait(event)
        except queue.Full:
            LOG.error("MQTT event queue overflow; restarting")
            os._exit(1)

    client.on_connect = lambda c, u, f, r, p: enqueue(("connect",)) if r == 0 else LOG.error("MQTT connection rejected")
    client.on_disconnect = lambda *args: enqueue(("disconnect",))
    client.on_message = lambda c, u, m: enqueue(("message", m.topic, m.payload, m.retain, time.monotonic()))
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())

    def watchdog():
        while not stopped.wait(5):
            if time.monotonic() - progress[0] > 30:
                LOG.error("LAN event loop stalled; restarting")
                os._exit(1)

    client.connect_async(os.environ.get("MQTT_HOST", "localhost"), int(os.environ.get("MQTT_PORT", "1883")), 20)
    client.loop_start()
    threading.Thread(target=watchdog, daemon=True).start()
    LOG.info("BEHA LAN bridge %s started; %d configured heaters", VERSION, len(bridge.names))
    last_tick = 0
    try:
        while not stopped.is_set():
            progress[0] = time.monotonic()
            try:
                event = events.get(timeout=1)
                if event[0] == "connect":
                    bridge.connect()
                    LOG.info("Local MQTT connected")
                elif event[0] == "disconnect":
                    bridge.disconnect()
                else:
                    bridge.handle(*event[1:])
            except queue.Empty:
                pass
            if time.monotonic() - last_tick >= 5:
                bridge.tick()
                last_tick = time.monotonic()
    finally:
        stopped.set()
        if client.is_connected():
            bridge.publish(BRIDGE, "offline").wait_for_publish(timeout=2)
        client.disconnect()
        client.loop_stop()


if __name__ == "__main__":
    main()
