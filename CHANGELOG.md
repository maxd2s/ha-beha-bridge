# Changelog

## [1.2.0] - 2026-11-26

- Add MQTT last will, bridge health heartbeat and last successful sync diagnostics.
- Mark heaters unavailable after 180 seconds without a successful sync; terminate a
  sync stalled for 120 seconds so the enabled Supervisor watchdog can restart it.
- Serialize token refresh and atomically replace tokens while preserving the /data symlink.
- Validate the complete cloud snapshot before changing/removing retained entities.
- Move discovery and verification cloud requests onto the polling worker.
- Add per-heater last-reading timestamps and dashboard reading age.
- Handle Home Assistant MQTT birth messages and require bridge and heater availability.


## [1.1.1] - 2026-11-26

### Fixed
- Bound all login and token exchange/refresh requests with a 10-second timeout.
- Retry token refresh network failures within the existing API retry budget.
- Preserve MQTT entities and their last state when cloud discovery fails.

## [1.1.0] - 2026-03-01

### Fixed
- **Temperature commands lost on quick dialog close** — Added optimistic MQTT state
  publishing so HA sees new values instantly, before cloud confirmation
- **`update_state()` undefined** — Replaced with proper post-command verification logic
- **MQTT thread blocked during API calls** — Commands now execute in background threads

### Added
- Post-command verification sync (5s delay) confirms cloud accepted the change
- API retry with exponential backoff (3 attempts) for transient cloud failures
- API call timeout (10s) prevents indefinite hangs
- State cache for optimistic updates
- Thread-safe sync with locking to prevent overlapping API calls

## [1.0.0] - 2026-02-17

### Added
- Initial release
- MQTT bridge with Home Assistant Auto-Discovery
- Temperature control via BEHA Cloud API
- Native on/off toggle using `PATCH heaters/{id}/change_enabled_state`
- Room-level toggle using `PATCH rooms/{id}/change_heaters_enabled_state`
- Automatic Azure B2C authentication with token refresh
- Auto-discovery of places, rooms, and heaters (no hardcoded IDs)
- Multi-architecture support (aarch64, amd64, armv7)
- Token persistence across add-on restarts
- Read-back verification for temperature commands
