#!/command/with-contenv bashio

export PYTHONUNBUFFERED=1

bashio::log.info "========== BEHA Bridge Starting =========="
bashio::log.info "PID: $$"

# --- MQTT Credentials ---
# Use HA Supervisor MQTT service discovery (auto-provisioned by Mosquitto add-on)
if bashio::services.available "mqtt"; then
    MQTT_HOST=$(bashio::services mqtt "host")
    MQTT_PORT=$(bashio::services mqtt "port")
    MQTT_USER=$(bashio::services mqtt "username")
    MQTT_PASS=$(bashio::services mqtt "password")
    bashio::log.info "MQTT: Using HA service discovery credentials"
else
    bashio::log.error "MQTT service not found! Make sure the Mosquitto broker add-on is installed and running."
    exit 1
fi

BEHA_EMAIL=$(bashio::config 'beha_email')
BEHA_PASS=$(bashio::config 'beha_password')

bashio::log.info "MQTT Host: ${MQTT_HOST}:${MQTT_PORT}"
bashio::log.info "MQTT User: ${MQTT_USER}"
bashio::log.info "MQTT Pass: [${#MQTT_PASS} chars]"
bashio::log.info "BEHA Email: ${BEHA_EMAIL}"
bashio::log.info "Python version: $(python3 --version 2>&1)"
bashio::log.info "==========================================="

# Ensure tokens are persisted in /data to survive restart/rebuild
ln -sf /data/beha_tokens.json /root/.beha_tokens.json

# Check if we need to login
if [ ! -f /data/beha_tokens.json ] || [ ! -s /data/beha_tokens.json ]; then
    bashio::log.info "No tokens found. Attempting login..."
    if [ -z "$BEHA_EMAIL" ] || [ -z "$BEHA_PASS" ]; then
        bashio::log.error "BEHA tokens missing and credentials not provided in config!"
        exit 1
    fi
    bashio::log.info "Running: python3 /beha_auth.py login-headless"
    python3 /beha_auth.py login-headless "$BEHA_EMAIL" "$BEHA_PASS" 2>&1
    bashio::log.info "Auth exit code: $?"
fi

bashio::log.info "Starting MQTT Bridge..."
python3 /beha_mqtt.py --host "$MQTT_HOST" --port "$MQTT_PORT" --user "$MQTT_USER" --passw "$MQTT_PASS" 2>&1
