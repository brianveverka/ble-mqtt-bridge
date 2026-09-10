FROM python:3.12-slim

LABEL maintainer="ble-mqtt"
LABEL description="BLE → MQTT Bridge with HA auto-discovery and Decoder Wizard"

# System deps — bluez for BLE on Linux hosts with passthrough
RUN apt-get update && apt-get install -y --no-install-recommends \
        bluetooth \
        bluez \
        libbluetooth-dev \
        libglib2.0-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY template.py ble_scanner.py mqtt_publisher.py ./
COPY ble_mqtt_bridge.py bridge_ui.py ui_fragments.py decoder_standalone.py ./
COPY static/ ./static/
COPY templates/ ./templates/

# Directories for runtime data
RUN mkdir -p captures

# Default configs (mount real ones over these)
COPY bridge.conf.example bridge.conf
COPY devices.conf.example devices.conf

# Entry script
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 5000 5001

ENV MODE=bridge-ui
ENV MQTT_HOST=localhost
ENV MQTT_PORT=1883
ENV MQTT_USERNAME=
ENV MQTT_PASSWORD=

ENTRYPOINT ["/docker-entrypoint.sh"]
