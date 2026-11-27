# BEHA Local Setup for iPhone

A native SwiftUI/CoreBluetooth helper for moving one BEHA Gen 2 heater at a time
from the cloud to a local MQTT broker. No BEHA account, cloud API, router access,
or third-party Swift dependencies are needed. The existing Wi-Fi SSID and password
can stay unchanged. This is an unofficial community tool.

## What is verified

- The BLE sequence is ported from the Python provisioner proven on firmware
  `smartheater-mk2-2.3`.
- Swift protocol tests cover identity mismatch, unsupported firmware, invalid
  responses, broker acknowledgement failure, interrupted apply, and retained or
  wrong-device MQTT telemetry.
- Both simulator and signed iPhone builds compile with Xcode 26.2.
- The native MQTT client has received live telemetry from a real migrated heater.
- **The iPhone Bluetooth migration itself still requires a real-device test.**
  Simulator Bluetooth is unavailable. Other firmware is inspect-only until tested.

## Build and install

Open `BehaSetup.xcodeproj` in Xcode, select the BehaSetup target, choose your Apple
development team under Signing & Capabilities, connect and unlock your iPhone,
select it as the run destination, and Run. Enable Developer Mode on the phone if
iOS asks. No signing team or provisioning profile is stored in the repository.
Personal Team installations expire after seven days and need reinstalling.

Requirements: iOS 17+, Xcode 26.2 for the checked-in project, and a local MQTT
broker reachable over home Wi-Fi. To regenerate the project after changing its
specification, install XcodeGen and run:

```sh
xcodegen generate --spec ios/project.yml
swift test --package-path ios
xcodebuild -project ios/BehaSetup.xcodeproj -scheme BehaSetup \
  -destination 'generic/platform=iOS Simulator' CODE_SIGNING_ALLOWED=NO build
```

## Prepare a private setup file

Copy `setup-profile.example.json` **outside the repository**, replace its example
values, and save it with the `.beha-setup` extension. It contains:

```json
{
  "ssid": "Existing Wi-Fi name",
  "password": "your-wifi-password",
  "broker_uri": "mqtt://heater-user:broker-password@192.0.2.10:1883",
  "heaters": [
    {"name": "Example room", "device_id": "aabbccddeeff"}
  ]
}
```

Use the full Wi-Fi MAC/device ID, not the short Bluetooth suffix. MAC separators
are accepted. Broker URIs must fit the firmware's 198-byte limit. Percent-encode
reserved characters in the username/password (`@` becomes `%40`, for example).
`mqtts://` uses normal certificate validation on the phone; the heater has its own
firmware CA bundle, so a successful phone TLS connection does not guarantee the
heater trusts that certificate. Fresh heater telemetry is the final check.

AirDrop the file to your own iPhone and open it with BEHA Local Setup, or save it
to Files and use **Import setup file**. JSON imports also work. Finder file sharing
is enabled for USB transfer. The app keeps the imported settings in memory; it
does not upload them, log them, store them in preferences, or embed them in the
app. The source file remains in Files until you delete it. Keep it private. The
Clear credentials button removes the in-memory settings. Reimport after relaunch.

## Migrate one heater

1. Join the existing home Wi-Fi on the iPhone. Import the file and choose the room.
2. Close the official BEHA app. Put **only that heater** into Gen 2 Link-Up mode.
   Resetting clears its Wi-Fi settings: turn down through 5° → 0 → flashing rES,
   continue until rES scrolls, then wait for Gn.2 followed by 22° and Bluetooth.
3. Tap **Find nearby heaters**, then the matching `BEHA_xxxxxx` device. Allow
   Bluetooth and Local Network access. Accept an iOS pairing request if shown.
4. The helper first checks MQTT login/subscription, establishes the ESP-IDF
   provisioning session, and reads the **full** device ID and firmware. It will
   not configure a mismatched ID or unsupported firmware.
5. Review the heater, Wi-Fi and broker. Tap **Migrate verified heater** and confirm.
   Keep the app foregrounded and stay near the heater.
6. The helper stages Wi-Fi, writes `iothub_override`, checks its exact
   acknowledgement, then applies Wi-Fi. It waits for fresh, non-retained MQTT
   telemetry on this device's raw event topic before reporting success.
7. Verify readings and a target change in Home Assistant. Only then add that
   heater's WAN block on the router and recheck HA. Test a normal reboot as well.

**MQTT verified does not mean HA verified.** The helper does not use an HA token
or assert that the HA bridge is configured correctly. It never publishes MQTT
control commands, changes firewall rules, resets a heater, or flashes firmware.
It does not touch Plume or create a new Wi-Fi network.

## Failure and recovery

Version 0.1.1 saves a bounded diagnostic report automatically in the app's Files
folder as `BEHA-Diagnostics.json`. It records timestamps, app/iOS versions, the
selected heater ID, RSSI, Bluetooth authorization/state, endpoint properties,
operation names, response sizes, exact Apple error domain/code, and setup write
progress. It excludes Wi-Fi names/passwords, broker URIs/credentials, raw GATT
responses, raw MQTT topics/payloads and NSError descriptions/userInfo. iOS does
not expose saved pairing state through public CoreBluetooth APIs, so the report
records authentication/encryption errors rather than guessing whether bonded.

Use **Share diagnostic report** to export it, or retrieve it through Xcode's
device file tools while connected to your own Mac. Nothing is uploaded
automatically. It persists across relaunch, is excluded from device backups,
and is limited to 200 events. No write retries or provisioning behavior were
changed by this diagnostic update.

- If pairing fails after a heater reset, forget **only that heater's** old BEHA
  pairing in iPhone Bluetooth settings, close the BEHA app, and retry Link-Up.
- Every BLE operation and MQTT preflight has a timeout. Stop cancels active work;
  backgrounding the app also stops setup. There is no automatic retry of writes.
- A missing acknowledgement or disconnect can happen after a successful write.
  The helper explicitly reports uncertainty. It never blindly clears the broker
  override, assumes rollback, or blocks internet access after a failure.
- If Wi-Fi settings were applied, allow the heater time to connect and inspect HA
  or broker traffic before resetting it again. The same setup can be repeated
  after checking the current state. Keep cloud-only heaters online until migrated.
- Restoring the original cloud configuration is not an implemented app feature.

## Implementation

`Core/` contains dependency-free wire formats, validation and the provisioning
sequence. `App/` contains the SwiftUI UI, bounded CoreBluetooth transport and a
small read-only MQTT 3.1.1 client using Network.framework. It uses only public iOS
APIs; the private macOS pairing API from the Python experiment is not used.
Endpoint names are resolved from GATT User Description descriptors (0x2901),
matching the working Python provisioner. Missing or ambiguous names stop setup;
there is no guessed UUID fallback. Firmware 2.3 is checked before settings writes.
MQTT credentials are used for a clean, uniquely named observer connection; the
observer never takes the heater's MQTT client ID.

Version 0.1.2 corrects the original port's fixed endpoint UUID lookup. A real
iPhone report showed successful GATT I/O followed by an invalid session response
before any settings writes. The descriptor mapping and its resolved UUIDs are now
included in diagnostics, and swapped/missing/duplicate mappings have regression tests.

Version 0.1.3 also accepts BEHA's actual firmware label `smartheater-mk2- 2.3`
(a space precedes the version), as captured from Office. The version gate still
rejects other firmware versions and prerelease suffixes.

Version 0.1.4 adds a separate, bounded read of `proto-ver` before any protocol
write, so CoreBluetooth can establish secure access first. The read's value is
discarded: ESP-IDF may return an empty or previous response, which is not identity
verification. Failure stops setup without retrying writes, and diagnostics name
the `pairing_read` operation. This follows Apple's documented encrypted-read
pairing mechanism and the read probe used by the Mac provisioner; recovery from
the observed iPhone ATT error 15 **did not succeed in the first hardware test**.
The iPhone's system log showed an unpaired BLE connection, automatic pairing
triggered by the encrypted read, then an SMP handshake timeout after 30 seconds.
No pairing dialog appeared and no protocol/configuration write was sent. A
"Connected" label therefore does not prove that pairing or encryption succeeded.
The log establishes where pairing stopped, not why the handshake timed out.
The app cannot inspect or delete iOS pairing records.
