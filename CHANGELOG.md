# Changelog

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
