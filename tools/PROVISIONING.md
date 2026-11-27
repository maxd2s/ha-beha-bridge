# Provision a BEHA Gen 2 heater for local MQTT

Experimental utility; verified on one Gen 2 heater running firmware 2.3. It uses
the firmware's broker override, not modified firmware. IDs, Wi-Fi credentials and
broker settings are supplied by the operator; nothing is tied to a particular home.

1. Install Python 3.11+ and `bleak` on a computer with Bluetooth.
2. Find the heater's full 12-hex-digit device ID / Wi-Fi MAC. The advertised BLE
   name contains only the last six digits; the tool checks the full identity
   before writing anything.
3. Create two private files outside the repository and `chmod 600` them:

   `heater-wifi.json`:
   ```json
   {"ssid":"YOUR_2_4_GHZ_NETWORK","password":"YOUR_WIFI_PASSWORD"}
   ```
   `heater-broker.json`:
   ```json
   {"expected_device_id":"001122334455","broker_uri":"mqtt://HEATER_USER:HEATER_PASSWORD@BROKER_LAN_ADDRESS:1883"}
   ```

   Create a persistent MQTT login for heaters in Mosquitto. Use strong URI-safe
   credentials, not temporary Supervisor app credentials. The full URI must be
   shorter than 199 UTF-8 bytes. The broker address must be reachable by the heater.
   MQTT credentials in URI userinfo override the firmware's MAC-based defaults.
4. Close the BEHA phone app and any other BLE clients. Follow the official Gen 2
   reset: turn the dial down through 5° → 0 → flashing `rES`, keep turning until
   `rES` scrolls, then wait for `Gn.2`, 22° and the Bluetooth symbol. This resets
   the heater's configuration; do one heater at a time with recovery credentials ready.
5. Inspect first:

   ```sh
   python3 tools/provision_gen2.py --device-id 001122334455
   ```

6. Apply the Wi-Fi and broker settings explicitly:

   ```sh
   python3 tools/provision_gen2.py --device-id 001122334455 --apply \
     --wifi-file /private/path/heater-wifi.json \
     --broker-file /private/path/heater-broker.json
   ```

On macOS, the opt-in `--pair-macos` workaround can negotiate the protected BLE
link. It uses private CoreBluetooth/Bleak internals and may break across versions.
It only addresses the selected peer and never clears unrelated pairings. Bluetooth
permission must be granted to the application running Python. If a pairing request
appears, review it normally. Other operating systems' pairing behavior is unverified.

The utility discovers endpoint names from GATT descriptors, validates the
provisioning security mode, checks identity, stages Wi-Fi, writes the broker
override, verifies the acknowledgment, applies Wi-Fi and checks connection status.
Inspection does not write persistent settings. An incomplete exchange is **not**
proof that settings were unchanged: verify broker/device state before retrying.
The utility does not blindly erase a previous override after an uncertain failure.

After setup, verify a fresh LAN reading and a target command in HA, then a normal
power cycle. Turn the physical dial and confirm the new target in HA. Repeat this
procedure separately for every heater; adding app configuration alone does not
provision hardware. No cloud token, domain, certificate or firmware is written.

Reference: [BEHA Gen 2 manual](https://www.beha.no/wp-content/uploads/2026/02/Manual-PV-LV-HX-Beha-APP_26.pdf).
