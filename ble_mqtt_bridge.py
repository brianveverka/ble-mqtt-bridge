"""BLE → MQTT Bridge runtime service."""

import asyncio
import logging
import signal
import sys
import time
from pathlib import Path

from template import TemplateRegistry, load_devices_config, load_bridge_config
from ble_scanner import BLEConnection
from mqtt_publisher import MQTTPublisher

logger = logging.getLogger("ble_bridge")


class BLEBridge:
    """Main bridge that connects BLE devices, decodes, and publishes to MQTT."""

    def __init__(
        self, config_path: str = "bridge.conf", devices_path: str = "devices.conf"
    ):
        self.config = load_bridge_config(config_path)
        self.devices = load_devices_config(devices_path)
        self.registry = TemplateRegistry()
        self.publisher = MQTTPublisher(
            host=self.config["mqtt"]["host"],
            port=self.config["mqtt"]["port"],
            username=self.config["mqtt"]["username"],
            password=self.config["mqtt"]["password"],
            topic_prefix=self.config["mqtt"]["topic_prefix"],
            discovery=self.config["mqtt"]["discovery"],
            discovery_prefix=self.config["mqtt"]["discovery_prefix"],
        )
        self.fahrenheit = bool(self.config["bridge"].get("fahrenheit", False))
        self.connections: dict[str, BLEConnection] = {}
        self._running = False

    def run(self):
        """Run the bridge (blocking)."""
        log_level = self.config["bridge"].get("log_level", "INFO")
        logging.basicConfig(
            level=getattr(logging, log_level, logging.INFO),
            format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        )

        logger.info("Starting BLE→MQTT Bridge")
        logger.info(f"Loaded {len(self.registry.templates)} templates")
        logger.info(f"Configured {len(self.devices)} devices")

        # Connect to MQTT
        self.publisher.connect()

        # Run BLE loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Handle shutdown
        def shutdown(sig, frame):
            logger.info("Shutting down...")
            self._running = False

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        self._running = True
        try:
            loop.run_until_complete(self._run_loop())
        finally:
            loop.run_until_complete(self._cleanup())
            loop.close()
            self.publisher.disconnect()

    async def _run_loop(self):
        """Main async loop - connects and maintains device connections."""
        reconnect_delay = self.config["bridge"].get("reconnect_delay", 5)

        while self._running:
            for mac, dev_cfg in self.devices.items():
                if not self._running:
                    break
                if mac in self.connections and self.connections[mac].is_connected():
                    continue

                template_id = dev_cfg.get("template")
                template = self.registry.get(template_id)
                if not template:
                    logger.warning(f"No template '{template_id}' for {mac}, skipping")
                    continue

                if not template.characteristic:
                    logger.warning(
                        f"Template '{template_id}' has no characteristic, skipping {mac}"
                    )
                    continue

                try:
                    conn = await self._connect_device(mac, dev_cfg, template)
                    self.connections[mac] = conn
                except Exception as e:
                    logger.error(f"Failed to connect to {mac}: {e}")

            await asyncio.sleep(reconnect_delay)

    async def _connect_device(self, mac: str, dev_cfg: dict, template):
        """Connect to a device and set up data handler."""
        device_name = dev_cfg.get("name", mac)
        location = dev_cfg.get("location", "")

        def on_data(data: bytes):
            decoded = template.decode(data, fahrenheit=self.fahrenheit)
            logger.debug(f"{mac}: {decoded}")
            self.publisher.publish_sensor(
                mac, device_name, location, template, decoded, fahrenheit=self.fahrenheit
            )

        def on_disconnect(addr):
            logger.warning(f"Device {addr} disconnected, will reconnect")
            if addr in self.connections:
                del self.connections[addr]

        conn = BLEConnection(mac, template.characteristic, on_data, on_disconnect)
        await conn.connect()
        logger.info(f"Device {mac} ({device_name}) connected and streaming")
        return conn

    async def _cleanup(self):
        """Disconnect all devices."""
        for mac, conn in self.connections.items():
            try:
                await conn.disconnect()
            except Exception:
                pass
        self.connections.clear()


def main():
    bridge = BLEBridge()
    bridge.run()


if __name__ == "__main__":
    main()
