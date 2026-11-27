# BEHA LAN Bridge

Unofficial local MQTT adapter for BEHA SmartHeater Gen 2 heaters. Tested on
firmware 2.3. Gen 1 and other firmware versions are not verified. No cloud account
is required once a heater has been provisioned to your broker.

## Install

1. Install and configure Home Assistant's Mosquitto broker and MQTT integration.
2. Add `https://github.com/maxd2s/ha-beha-bridge` under **Settings → Apps → App
   store → Repositories** (called Add-ons on older HA releases).
3. Install **BEHA LAN Bridge**. Set each heater's full device ID and display name:

   ```yaml
   auto_discover: false
   stale_after: 90
   heaters:
     - device_id: "001122334455"
       name: Study
     - device_id: "aabbccddeeff"
       name: Bedroom
   ```

4. Start the app and enable **Start on boot** and **Watchdog**. Broker credentials
   are obtained from Supervisor; do not enter your BEHA cloud login.
5. Provision each physical heater separately using [the provisioning guide](../tools/PROVISIONING.md).
   Adding a device to this list does not change its Wi-Fi or cloud connection.
   Until fresh local telemetry arrives, its LAN entities remain unavailable.
6. Use the new LAN climate entity on your dashboard. Disable the old cloud climate
   entity for that heater to prevent accidental cloud commands. Other heaters can
   remain on the cloud bridge during migration.

`auto_discover: true` also accepts valid BEHA telemetry from unlisted device IDs
on this broker. Configured names still take precedence. Use a trusted private broker.
Only one instance of this bridge should run on a broker with the default topics.

## What works

- Set targets from 5–30°C in 0.5°C steps; HA updates from the heater's actual reply.
- Read physical dial target changes, measured temperature and last-reading time.
- Turn each heater off/on from its power switch or thermostat.
- Read and change child lock and control mode; all displayed states follow heater telemetry.
- Receive periodic reports roughly every 30 seconds and extra reports on dial changes.
- Mark an individual heater unavailable after 90 seconds without valid fresh data.
- See bridge health separately from individual heater availability.

The climate exposes `off` and `heat`. Heater mode 0 maps to `off`; modes 1–4 map
to `heat` (enabled), without claiming the heating element is currently energized.
Power-on resumes the last enabled mode observed during this bridge process. If
the bridge starts with the heater off and has no previous enabled mode, it uses
Remote target. Changing the target temperature selects Remote target in firmware.

Control mode labels are Off (0), Frost protection at 5°C (1), Local dial (2),
Remote target (3), and Weekly schedule (4). Child lock is Unlocked (0) or Locked (1).
Unknown values remain explicitly unknown. Selecting Weekly schedule activates
the schedule already stored in the heater; this does not create or edit a schedule.
Heating-action detection, schedule editing and firmware updates are not implemented.

**Alarm limitation:** firmware 2.3 hardcodes `alarm_flags` to zero, so the bridge
does not expose alarm status. Upgrading removes the old alarm sensor and replaces
read-only mode/lock sensors with controls. See [protocol evidence](PROTOCOL.md).

The last-reading sensor stays visible when offline so its age continues increasing.
Retained raw telemetry never establishes freshness. Retained and delayed commands
are rejected, and commands to a stale heater are rejected. Restart/reconnection
requires a new device report. An independent process watchdog terminates a stalled
main loop; Supervisor's Watchdog restarts the app.

Commands use non-retained QoS 0 to avoid an outgoing retry queue replaying old
setpoints after reconnect. The heater's state reply is the confirmation; if a
command is lost, the displayed target stays unchanged and you can retry.

## Dashboard

For the thermostat with child lock in its top-left corner, copy
[`ha/beha-thermostat-card.js`](../ha/beha-thermostat-card.js) to HA's
`www/beha-thermostat-card.js` and register `/local/beha-thermostat-card.js?v=2`
as a JavaScript module under Dashboard resources. Reload your browser after
adding the resource. The card keeps HA's native temperature and off/heat controls,
and its lock button follows the child-lock switch's confirmed state. The mode
selector remains available on the device page but is omitted from this dashboard.

The default entity IDs are `climate.beha_lan_<device_id>` and
`sensor.beha_lan_<device_id>_last_reading`, plus
`switch.beha_lan_<device_id>_power`, `switch.beha_lan_<device_id>_child_lock`, and
`select.beha_lan_<device_id>_heater_mode`. Renaming entities in HA overrides these.
Generate a dashboard for any heater list using:

```sh
python3 tools/heaters_dashboard.py --options /path/to/options.json --output heaters-dashboard.json
```

The options JSON uses the same `heaters` array as the app. Paste the generated
configuration into a new dashboard's raw configuration editor. The repository's
`ha/heaters-dashboard.json` is a generic example with sample device IDs.

## Run outside Home Assistant OS

Requires Python 3.11+ and `paho-mqtt==2.1.0`:

```sh
python3 -m pip install paho-mqtt==2.1.0
# Supply MQTT_HOST, MQTT_PORT, MQTT_USER, MQTT_PASSWORD through your service's
# private environment file. Set MQTT_TLS=true for a verified TLS broker.
python3 beha_lan/bridge.py --options /path/to/options.json
```

Use a service manager with restart-on-failure. There is no dependency on a Mac,
Bluetooth, or the provisioning utility during normal operation.

## Protocol and privacy

Heaters publish numeric JSON under `devices/<id>/messages/events/#`. Commands go
to `devices/<id>/messages/devicebound/command` with a string target, for example:

```json
{"com.devicedrive.heater":{"target_temperature":"19.0"}}
```

The adapter republishes only selected numeric fields under `beha_lan/<id>/state`.
Raw event topic suffixes can contain cloud tokens: do not share packet captures or
log full topics. The adapter does not relay them or include them in discovery.
Diagnostic text and schedule identifiers are not forwarded.

Normal reboot persistence and local commands with WAN blocked were verified on
firmware 2.3. Cold boot with WAN already blocked remains unverified. Plain MQTT
was used for the live tests; keep it on a trusted LAN. The firmware has an older
CA bundle, so arbitrary modern MQTT TLS certificates may not be accepted.

## Recovery

Stop the LAN bridge to disable its command handling. This does not restore a
heater's cloud broker. To return a heater to the BEHA app, follow BEHA's documented
Gen 2 reset and provisioning process. A stale phone Bluetooth bond may need to be
forgotten first. Reset clears Wi-Fi setup, so have the network credentials ready.
