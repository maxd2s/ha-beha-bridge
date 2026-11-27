#!/command/with-contenv bashio
set -e
export PYTHONUNBUFFERED=1
if ! bashio::services.available mqtt; then
    bashio::log.error "Configure the Mosquitto MQTT broker before starting BEHA LAN."
    exit 1
fi
MQTT_HOST=$(bashio::services mqtt host)
MQTT_PORT=$(bashio::services mqtt port)
MQTT_USER=$(bashio::services mqtt username)
MQTT_PASSWORD=$(bashio::services mqtt password)
export MQTT_HOST MQTT_PORT MQTT_USER MQTT_PASSWORD
exec python3 /bridge.py
