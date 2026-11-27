# Readable diagnostic values

These labels come from static analysis of the official Gen 2 firmware 2.3 image,
with all five mode selections and both lock states verified through HA writes
and fresh heater telemetry on 2026-11-27.
The mode selector retains its `raw_code` attribute. Unknown control values display
unknown and are never offered as writable options.

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
formatter. The bridge omits alarm flags and removes the old alarm-status entity.
Likewise, “Local dial” or “Remote target” does not mean the heating element is
currently energized.

## Writable controls

The `com.beha.heater` parser at `0x4200a048` accepts `heater_mode` and
`child_lock` as **JSON strings**, assigns the shared state, and saves it. The
bridge restricts mode writes to 0–4 and lock writes to 0–1:

```json
{"com.beha.heater":{"heater_mode":"0"}}
{"com.beha.heater":{"child_lock":"1"}}
```

Power-off sends mode 0. Power-on restores the last observed mode 1–4, or uses
mode 3 when there is no observed previous enabled mode. Weekly schedule uses
the existing stored schedule. Neither sending a command nor receiving a broker
acknowledgement updates HA state; only a fresh heater report does.

Live validation of version 0.1.3 confirmed power off/on through both switches
and climate controls, lock/unlock, and Local dial/Remote target selection on
four Gen 2 heaters with WAN blocked. Frost protection and Weekly schedule
selection were additionally confirmed on one heater. This verifies mode
selection and reported state, not the execution of a complete weekly program.
All original targets, modes and lock settings were restored after testing.

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
No faults were induced to obtain these labels.
