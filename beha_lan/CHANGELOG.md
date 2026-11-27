# 0.1.3

- Add device-confirmed power switches, thermostat off/heat commands, child-lock toggles and control-mode selects.
- Resume the last observed enabled mode on power-on; use Remote target if no previous mode is known.
- Map Off, Frost protection, Local dial, Remote target and Weekly schedule consistently across controls.
- Remove hardcoded alarm telemetry and delete its retained discovery entity, together with superseded read-only mode/lock sensors.
- Preserve stale-device, retained-command and delayed-command rejection for every control.
- Update generated dashboards with writable controls and explicit thermostat power buttons.

# 0.1.2

- Decode control modes and child lock into readable states using firmware 2.3 evidence.
- Keep raw codes in sensor attributes and preserve unknown values explicitly.
- Explain that firmware 2.3 hardcodes alarm flags to zero; do not present it as proof of no faults.
- Update dashboard diagnostics and add protocol evidence notes.

# 0.1.1

- Avoid outgoing command replay after reconnect; continue requiring heater feedback.
- Preserve actual MQTT receipt time when events are briefly queued.
- Show an offline heater's connection sensor as disconnected while the bridge is healthy.

# 0.1.0

- Local MQTT climate controls with device-confirmed target and dial feedback.
- Independent freshness/availability per heater, bridge health, reading timestamps,
  mode, child-lock and alarm diagnostics.
- Configured device names or opt-in discovery, reconnect handling, retained-command
  rejection, malformed-payload checks and a stalled-process watchdog.
- Verified on BEHA SmartHeater Gen 2 firmware 2.3. Power and schedule commands remain
  unimplemented pending protocol verification.
