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
