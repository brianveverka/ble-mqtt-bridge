"""MQTT publisher with Home Assistant auto-discovery support."""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


def _get_mqtt():
    """Lazy import paho mqtt."""
    try:
        import paho.mqtt.client as mqtt

        return mqtt
    except ImportError:
        raise ImportError("paho-mqtt is required. Install with: pip install paho-mqtt")


class MQTTPublisher:
    """Publishes decoded BLE data to MQTT with HA auto-discovery."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1883,
        username: str = "",
        password: str = "",
        topic_prefix: str = "ble",
        discovery: bool = True,
        discovery_prefix: str = "homeassistant",
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.topic_prefix = topic_prefix
        self.discovery_enabled = discovery
        self.discovery_prefix = discovery_prefix
        self._client = None
        self._connected = False
        self._discovered: set[str] = set()

    def connect(self):
        """Connect to the MQTT broker."""
        mqtt = _get_mqtt()
        self._client = mqtt.Client()
        if self.username:
            self._client.username_pw_set(self.username, self.password)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

        logger.info(f"Connecting to MQTT at {self.host}:{self.port}")
        self._client.connect(self.host, self.port, keepalive=60)
        self._client.loop_start()

        # Wait for connection
        for _ in range(50):
            if self._connected:
                return
            time.sleep(0.1)
        raise ConnectionError(f"Failed to connect to MQTT at {self.host}:{self.port}")

    def disconnect(self):
        """Disconnect from MQTT."""
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def publish_sensor(
        self,
        device_mac: str,
        device_name: str,
        location: str,
        template,
        decoded: dict,
        fahrenheit: bool = False,
    ):
        """Publish decoded sensor values and HA discovery configs."""
        if not self._connected:
            logger.warning("MQTT not connected, skipping publish")
            return

        device_id = device_mac.replace(":", "_").lower()
        base_topic = f"{self.topic_prefix}/{device_id}"

        for field in template.fields:
            value = decoded.get(field.key)
            if value is None:
                continue

            state_topic = f"{base_topic}/{field.key}/state"
            self._client.publish(state_topic, str(value), retain=True)

            # HA auto-discovery
            if self.discovery_enabled:
                self._publish_discovery(
                    device_id, device_name, location, template, field, fahrenheit
                )

        # Publish availability
        self._client.publish(f"{base_topic}/availability", "online", retain=True)

    def _publish_discovery(
        self, device_id: str, device_name: str, location: str, template, field,
        fahrenheit: bool = False,
    ):
        """Publish HA MQTT discovery config for a sensor."""
        disc_key = f"{device_id}_{field.key}"
        if disc_key in self._discovered:
            return

        unique_id = f"ble_{device_id}_{field.key}"
        component = "sensor"
        # location disambiguates multiple devices that share a name
        display_name = f"{device_name} {location}".strip()
        # temperatures are stored as Celsius; show °F when the bridge converts
        unit = "°F" if (field.fahrenheit_convert and fahrenheit) else field.unit

        config = {
            "name": field.name,
            "unique_id": unique_id,
            "state_topic": f"{self.topic_prefix}/{device_id}/{field.key}/state",
            "availability_topic": f"{self.topic_prefix}/{device_id}/availability",
            "device": {
                "identifiers": [device_id],
                "name": display_name,
                "manufacturer": template.manufacturer,
                "model": template.model,
                "via_device": "ble_mqtt_bridge",
            },
        }
        if unit:
            config["unit_of_measurement"] = unit
        if field.device_class:
            config["device_class"] = field.device_class
        if field.state_class:
            config["state_class"] = field.state_class
        if field.icon:
            config["icon"] = field.icon

        topic = f"{self.discovery_prefix}/{component}/{unique_id}/config"
        self._client.publish(topic, json.dumps(config), retain=True)
        self._discovered.add(disc_key)
        logger.debug(f"Published discovery for {unique_id}")

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self._connected = True
            logger.info("Connected to MQTT broker")
        else:
            logger.error(f"MQTT connection failed: rc={rc}")

    def _on_disconnect(self, client, userdata, rc):
        self._connected = False
        logger.warning(f"Disconnected from MQTT broker (rc={rc})")
