# BEHA SmartHeater Bridge for Home Assistant

[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-Add--on-blue?logo=homeassistant)](https://www.home-assistant.io/)
[![BEHA](https://img.shields.io/badge/BEHA-SmartHeater-orange)](https://www.beha.no/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

THIS IS NOT OFFICIAL BEHA ADD-ON

A Home Assistant add-on that bridges **BEHA SmartHeaters** (WiFi Gen1/Gen2) to Home Assistant via MQTT, providing full climate control including temperature adjustment and native on/off toggle.

## Features

- 🌡️ **Temperature control** — set target temperature from Home Assistant
- ❄️ **Native on/off toggle** — uses the real BEHA API to enable/disable heaters
- 📡 **MQTT Auto-Discovery** — heaters appear automatically in Home Assistant as climate entities
- 🔄 **Dynamic sync** — add, remove, or rename heaters/rooms in the BEHA app and changes reflect in HA within 60 seconds
- 🔐 **Auto token refresh** — handles Azure B2C authentication with automatic renewal
- 📍 **Full auto-discovery** — automatically finds all your places, rooms, and heaters (no hardcoded IDs)
- 🏠 **Multi-place support** — works across multiple homes/locations in your BEHA account

## How It Works

```
BEHA Cloud API ←→ [Bridge Add-on] ←→ MQTT ←→ Home Assistant
```

The bridge authenticates with your BEHA account, polls the BEHA Cloud API every 60 seconds, and publishes heater state to MQTT. Commands from Home Assistant (temperature changes, on/off) are forwarded to the BEHA API. All data is fetched in a **single API call** per cycle for optimal performance.

---

## Prerequisites

Before installing the BEHA Bridge, make sure you have:

### 1. Home Assistant OS 

This add-on requires **Home Assistant OS** (e.g. HA Green, HA Yellow, Raspberry Pi).

### 2. SSH Access

You need SSH access to your Home Assistant instance to copy the add-on files. Install the **Terminal & SSH** add-on if you haven't already:

1. Go to **Settings → Add-ons → Add-on Store**
2. Search for **"Terminal & SSH"**
3. Click **Install**, then go to the **Configuration** tab
4. Set a password (or add your SSH public key)
5. Enable **"Show in sidebar"** (optional but helpful)
6. Click **Start**

> **Note:** Make sure **Advanced Mode** is enabled in your user profile (**Settings → People → your user → Advanced Mode**), otherwise some add-ons may not be visible.

### 3. Mosquitto MQTT Broker

The BEHA Bridge communicates with Home Assistant via MQTT. Install the **Mosquitto broker** add-on:

1. Go to **Settings → Add-ons → Add-on Store**
2. Search for **"Mosquitto broker"**
3. Click **Install**, then **Start**
4. Go to **Settings → Devices & Services → MQTT** and click **Configure** to complete the integration setup

> **Note:** The bridge auto-discovers MQTT credentials from the Mosquitto add-on — no manual MQTT configuration needed.

### 4. BEHA Account

You need a BEHA SmartHeater account — the same email and password you use in the **BEHA WiFi SmartHeater** mobile app. Your heaters must be set up and visible in the app before using this bridge.

---

## Installation

### Step 1: Clone or Download the Repository

On your local machine:

```bash
git clone https://github.com/maxd2s/ha-beha-bridge.git
cd ha-beha-bridge
```

Or download the ZIP from GitHub and extract it.

### Step 2: Copy Files to Home Assistant

Use `scp` to transfer the add-on files to your HA instance. Replace `<HA_IP>` with your Home Assistant's IP address (e.g. `homeassistant.local`):

```bash
# Create the add-on directory on HA
ssh root@<HA_IP> "mkdir -p /addons/beha_bridge"

# Copy all required files
scp beha_auth.py beha_mqtt.py run.sh config.yaml build.yaml Dockerfile \
    root@<HA_IP>:/addons/beha_bridge/
```

**Required files:**

| File | Purpose |
|------|---------|
| `beha_auth.py` | Azure B2C authentication, token management, API client |
| `beha_mqtt.py` | MQTT bridge with HA Auto-Discovery |
| `run.sh` | Add-on entrypoint (credential setup, token persistence) |
| `config.yaml` | Add-on configuration schema |
| `build.yaml` | Build configuration (multi-arch support) |
| `Dockerfile` | Container build (Alpine + Python + paho-mqtt) |

### Step 3: Add Local Add-on Repository

1. In Home Assistant, go to **Settings → Add-ons**
2. Click **Add-on Store** (bottom right)
3. Click the **⋮** menu (top right) → **Repositories**
4. Enter `/addons` and click **Add**
5. Click **Close**
6. Refresh the page (pull down or click the reload icon)

### Step 4: Install the Add-on

1. In the Add-on Store, scroll down to **Local add-ons**
2. Find **"BEHA SmartHeater Bridge"** and click it
3. Click **Install** — this will build the Docker container (may take 1-2 minutes)

### Step 5: Configure

1. Go to the **Configuration** tab of the add-on
2. Fill in:

| Field | Value |
|-------|-------|
| **beha_email** | Your BEHA account email |
| **beha_password** | Your BEHA account password |

3. Click **Save**

### Step 6: Start & Verify

1. Click **Start**
2. Go to the **Log** tab — you should see:

```
✅ Connected to MQTT Broker!
📤 Publishing Home Assistant Auto-Discovery config...
   Published discovery for heater near balkon
   Published discovery for guest room heater
   ...
🆕 New heater discovered: heater near balkon (f412fa5a3f28)
```

3. Your heaters should now appear under **Settings → Devices & Services → MQTT → devices**

### Step 7: (Optional) Enable Start on Boot

1. Go to the **Info** tab of the add-on
2. Enable **"Start on boot"**
3. Enable **"Watchdog"** (auto-restart on crash)

---

## Configuration Reference

| Option | Description | Default |
|--------|-------------|---------|
| `beha_email` | Your BEHA account email address | *(required)* |
| `beha_password` | Your BEHA account password | *(required)* |

> **MQTT connection** is fully automatic — credentials are discovered from the Mosquitto add-on via the HA Supervisor API. No MQTT configuration needed.

---

## Dynamic Device Sync

The bridge automatically syncs with the BEHA cloud every 60 seconds:

| Change in BEHA App | What Happens in HA |
|---------------------|--------------------|
| Add a new heater or room | 🆕 Entity appears automatically |
| Remove a heater or room | 🗑️ Entity is removed from HA |
| Rename a heater or room | Device name updates automatically |
| Change temperature in the app | HA shows the new temperature |
| Turn heater on/off in the app | HA reflects the new state |

---

## BEHA API Reference

For anyone interested in the reverse-engineered BEHA Cloud API:

| Action | Method | Endpoint |
|--------|--------|----------|
| Full configuration | `GET` | `/api/users/configuration` |
| Place details | `GET` | `/api/places/{place_id}` |
| Heater details | `GET` | `/api/heaters/{heater_id}` |
| Set temperature | `PATCH` | `/api/places/{place_id}/rooms/{room_id}/set_target_temperature` |
| Toggle heater on/off | `PATCH` | `/api/heaters/{heater_id}/change_enabled_state` |
| Toggle room on/off | `PATCH` | `/api/places/{place_id}/rooms/{room_id}/change_heaters_enabled_state` |

- **Base URL:** `https://behacloud.com/api`
- **Auth:** Azure B2C with PKCE flow (tenant: `behaiotbcdb.b2clogin.com`)
- **Token refresh:** Automatic via refresh token

---

## Architecture

```
beha_auth.py    — Azure B2C authentication, token management, API client
beha_mqtt.py    — MQTT bridge with HA Auto-Discovery, polls API & handles commands
run.sh          — Add-on entrypoint (credential setup, token persistence)
config.yaml     — HA add-on configuration schema
build.yaml      — HA add-on build configuration (multi-arch: aarch64, amd64, armv7)
Dockerfile      — Container build (Alpine + Python + paho-mqtt)
```

**Data flow per 60s cycle (single API call):**

```
GET /api/users/configuration
    → Returns all places, rooms, heaters, temperatures, status
    → Bridge publishes state to MQTT
    → Bridge re-publishes discovery (for renames/additions)
    → Bridge detects removed devices and clears them
```

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| **Add-on not visible in store** | Make sure `/addons` repository is added and Advanced Mode is enabled |
| **"Tokens missing" error** | Enter your BEHA email and password in the Configuration tab |
| **Heaters not appearing** | Check logs for MQTT connection errors. Ensure Mosquitto is running and the MQTT integration is configured |
| **"Token expired" errors** | The bridge auto-refreshes tokens. If it persists, restart the add-on to trigger a fresh login |
| **Temperature not updating** | The bridge polls every 60 seconds. Check logs for API errors |
| **Heater shows as "unavailable"** | The heater may be offline (no WiFi). Check the BEHA app |

### Checking Add-on Logs

Via the UI:
- Go to **Settings → Add-ons → BEHA SmartHeater Bridge → Log**

Via SSH:
```bash
ha addons logs local_beha_bridge
```

### Manual Token Reset

If authentication is broken, you can force a fresh login:

```bash
# SSH into HA
ssh root@<HA_IP>

# Delete the stored tokens
rm /data/beha_tokens.json

# Restart the add-on (it will log in again using your configured credentials)
ha addons restart local_beha_bridge
```

---

## Updating

To update the bridge after a new release:

```bash
# On your local machine
cd ha-beha-bridge
git pull

# Upload updated files to HA
scp beha_auth.py beha_mqtt.py run.sh config.yaml build.yaml Dockerfile \
    root@<HA_IP>:/addons/beha_bridge/

# Rebuild the add-on (SSH into HA)
ssh root@<HA_IP> "ha addons rebuild local_beha_bridge"
```

The add-on will restart automatically with the new code. Your credentials and tokens are preserved.

---

## License

[MIT](LICENSE)
