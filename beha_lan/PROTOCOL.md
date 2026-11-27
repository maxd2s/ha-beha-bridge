# Readable diagnostic values

These labels come from static analysis of the official Gen 2 firmware 2.3 image,
with local-dial and remote-target transitions also verified on a running heater.
The mode and lock sensors retain their original `raw_code` attribute. New values
are explicitly unknown, rather than assigned an unverified meaning.

| Field | Code | Display | Evidence |
|---|---:|---|---|
| heater_mode | 0 | Off | Scheduler selects the off control state and displays `OF F`. |
| heater_mode | 1 | Frost protection (5°C) | Scheduler selects a 5.0°C setpoint and displays `Fr o`. |
| heater_mode | 2 | Local dial | Encoder adjustments set mode 2; also the factory default. Verified live. |
| heater_mode | 3 | Remote target | MQTT target command sets mode 3. Verified live after HA commands. |
| heater_mode | 4 | Weekly schedule | Week-program parser selects mode 4; scheduler follows the stored day/time program. |
| child_lock | 0 | Unlocked | Normal encoder handling is enabled. |
| child_lock | 1 | Locked | Encoder enters the lock-handling path and displays `Ch d`; normal adjustment is blocked. |
| alarm_flags | 0 | No flags reported | Literal zero in firmware's telemetry format string, not a measured fault-status word. |

Do not treat alarm flags zero as proof that thermal protection, sensors or heating
hardware are healthy. No alarm bit meanings are exposed by this firmware's JSON
formatter. Nonzero values are displayed as `Unknown alarm flags (0x...)`.
Likewise, “Local dial” or “Remote target” does not mean the heating element is
currently energized. These are read-only diagnostics, not new control commands.

## Reproducible firmware evidence

Official image:
[WRF_V2_PUBLIC__2.3.bin](https://behastgfilesprod.blob.core.windows.net/wrffirmware/WRF_V2_PUBLIC__2.3.bin)

- Size: 1,085,056 bytes.
- SHA-256: `dbf921c1f223f70f7f1539a92a3e0b6908243b7f67f48a2aafafceea48c35540`.
- ESP32-S3 application, `smartheater_mk2`, ESP-IDF v5.0.2-dirty.
- Virtual address `0x42008898`: scheduler/encoder logic and mode transitions.
- `0x42009ea0`: target-temperature command parser sets mode 3.
- `0x42009afc`: week-program parser sets mode 4.
- `0x4200ded4`: telemetry formatter; `alarm_flags` is the literal zero in its format.
- `0x42000930`, `0x42000934`, `0x42000938`: pointers to `Ch d`, `OF F`, `Fr o` display strings.
- `0x420008d8`: float constant 5.0 used in frost mode.
- Shared state: mode byte at `0x3fca00a4`; lock byte at `0x3fca4808`.

These addresses apply to this exact image, not arbitrary firmware versions.
No faults were induced, and no lock/mode commands were sent to obtain these labels.
