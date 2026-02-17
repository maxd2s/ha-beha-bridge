# BEHA SmartHeater Bridge for Home Assistant

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Add--on-blue?logo=homeassistant)](https://www.home-assistant.io/)
[![BEHA](https://img.shields.io/badge/BEHA-SmartHeater-orange)](https://www.beha.no/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A Home Assistant add-on that bridges **BEHA SmartHeaters** (WiFi Gen1) to Home Assistant via MQTT, providing full climate control including temperature adjustment and native on/off toggle.

## Features

- 🌡️ **Temperature control** — set target temperature from Home Assistant
- ❄️ **Native on/off toggle** — uses the real BEHA API to enable/disable heaters (not a temperature workaround)
- 📡 **MQTT Auto-Discovery** — heaters appear automatically in Home Assistant
- 🔄 **Auto token refresh** — handles Azure B2C authentication with automatic renewal
- 📍 **Auto-discovery** — automatically finds your place, rooms, and heaters
- 🏠 **Multi-heater support** — all heaters across all rooms are exposed

## How It Works

```
BEHA Cloud API ←→ [Bridge Add-on] ←→ MQTT ←→ Home Assistant
```

The bridge authenticates with your BEHA account, polls the BEHA Cloud API every 60 seconds, and publishes heater state to MQTT. Commands from Home Assistant are forwarded to the BEHA API.

## Installation

### Prerequisites

- Home Assistant with the [Mosquitto broker](https://github.com/home-assistant/addons/tree/master/mosquitto) add-on installed
- A BEHA SmartHeater account (the same one you use in the BEHA app)

### Steps

1. **Copy the add-on to your Home Assistant instance:**

   ```bash
   # SSH into your Home Assistant
   ssh root@<your-ha-ip>

   # Create the add-on directory
   mkdir -p /addons/beha_bridge

   # Copy all files from this repo into /addons/beha_bridge/
   ```

   Or use `scp` from your local machine:
   ```bash
   scp beha_auth.py beha_mqtt.py run.sh config.yaml build.yaml Dockerfile \
       root@<your-ha-ip>:/addons/beha_bridge/
   ```

2. **Add the local add-on repository:**
   - Go to **Settings → Add-ons → Add-on Store**
   - Click **⋮** (top right) → **Repositories**
   - Add: `/addons`
   - Click **Close**, then refresh the page

3. **Install and configure:**
   - Find **"BEHA SmartHeater Bridge"** in the add-on store
   - Click **Install**, then go to the **Configuration** tab
   - Enter your BEHA account email and password
   - Click **Save**, then **Start**

4. **Verify:**
   - Check the add-on **Log** tab — you should see `✅ Connected to MQTT Broker!`
   - Your heaters should appear under **Settings → Devices & Services → MQTT**

## Configuration

| Option | Description | Default |
|--------|-------------|---------|
| `beha_email` | Your BEHA account email | *(required)* |
| `beha_password` | Your BEHA account password | *(required)* |
| `mqtt_host` | MQTT broker host | `core-mosquitto` |
| `mqtt_user` | MQTT username | Auto-discovered from HA |
| `mqtt_pass` | MQTT password | Auto-discovered from HA |

> **Note:** MQTT credentials are automatically discovered from the Home Assistant Mosquitto add-on. You only need to set them manually if you're using an external MQTT broker.

## BEHA API Endpoints

For anyone interested in the reverse-engineered BEHA Cloud API:

| Action | Method | Endpoint |
|--------|--------|----------|
| Get all data | `GET` | `/api/places/{place_id}` |
| Set temperature | `PATCH` | `/api/places/{place_id}/rooms/{room_id}/set_target_temperature` |
| Toggle heater | `PATCH` | `/api/heaters/{heater_id}/change_enabled_state` |
| Toggle room | `PATCH` | `/api/places/{place_id}/rooms/{room_id}/change_heaters_enabled_state` |

Authentication is via Azure B2C (PKCE flow) with `behacloud.com` as the API host.

## Architecture

```
beha_auth.py    — Azure B2C authentication, token management, API client
beha_mqtt.py    — MQTT bridge with HA Auto-Discovery, polls API & handles commands
run.sh          — Add-on entrypoint (credential setup, token persistence)
config.yaml     — HA add-on configuration schema
build.yaml      — HA add-on build configuration (multi-arch)
Dockerfile      — Container build (Alpine + Python + paho-mqtt)
```

## Troubleshooting

- **Heaters not appearing?** Check the add-on logs for connection errors. Ensure Mosquitto is running.
- **"Token expired" errors?** The bridge auto-refreshes tokens, but if it persists, restart the add-on to trigger a fresh login.
- **Temperature not updating?** The bridge reads back the temperature after setting it. Check logs for `⚠️ MISMATCH` warnings.

## License

[MIT](LICENSE)
