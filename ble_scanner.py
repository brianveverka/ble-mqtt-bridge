"""BLE scanner and connection management using bleak."""

import asyncio
import contextlib
import logging
from typing import Optional, Callable

logger = logging.getLogger(__name__)


def _get_bleak():
    """Lazy import bleak so non-BLE parts work without it."""
    try:
        from bleak import BleakScanner, BleakClient

        return BleakScanner, BleakClient
    except ImportError:
        raise ImportError(
            "bleak is required for BLE operations. Install with: pip install bleak"
        ) from None


async def scan_devices(timeout: int = 10) -> list[dict]:
    """Scan for BLE devices. Returns list of {address, name, rssi}."""
    BleakScanner, _ = _get_bleak()
    logger.info(f"Scanning for BLE devices ({timeout}s)...")
    # bleak >=0.20 moved RSSI onto AdvertisementData (BLEDevice.rssi is gone in 3.x)
    discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
    results = []
    for d, adv in discovered.values():
        results.append(
            {
                "address": d.address,
                "name": adv.local_name or d.name or "Unknown",
                "rssi": adv.rssi,
            }
        )
    logger.info(f"Found {len(results)} devices")
    return results


class BLEConnection:
    """Manages a connection to a single BLE device with GATT notify."""

    def __init__(
        self,
        address: str,
        characteristic: str,
        on_data: Callable[[bytes], None],
        on_disconnect: Optional[Callable] = None,
    ):
        self.address = address
        self.characteristic = characteristic
        self.on_data = on_data
        self.on_disconnect = on_disconnect
        self._client = None
        self._connected = False

    async def connect(self):
        """Connect to the device and subscribe to notifications."""
        BleakScanner, BleakClient = _get_bleak()
        target = self.address
        logger.info(f"Connecting to {self.address}...")
        try:
            self._client = BleakClient(
                target, disconnected_callback=self._handle_disconnect
            )
            await self._client.connect()
        except Exception as exc:
            # BlueZ raises "device not found" until it has discovered the address.
            # Scan once to register it, then retry with the resolved device. (A
            # scan can't see an already-connected device, so we never scan first.)
            if "not found" not in str(exc).lower():
                raise
            logger.info(f"{self.address} not cached; scanning to resolve...")
            target = await BleakScanner.find_device_by_address(self.address, timeout=20.0)
            if target is None:
                raise
            self._client = BleakClient(
                target, disconnected_callback=self._handle_disconnect
            )
            await self._client.connect()
        self._connected = True
        logger.info(f"Connected to {self.address}")

        await self._client.start_notify(
            self.characteristic,
            self._handle_notification,
        )
        logger.info(f"Subscribed to {self.characteristic}")

    async def disconnect(self):
        """Disconnect from the device."""
        if self._client and self._connected:
            with contextlib.suppress(Exception):
                await self._client.stop_notify(self.characteristic)
            await self._client.disconnect()
            self._connected = False
            logger.info(f"Disconnected from {self.address}")

    def is_connected(self) -> bool:
        return (
            self._connected and self._client is not None and self._client.is_connected
        )

    def _handle_notification(self, _sender, data: bytearray):
        self.on_data(bytes(data))

    def _handle_disconnect(self, _client):
        self._connected = False
        logger.warning(f"Disconnected from {self.address}")
        if self.on_disconnect:
            self.on_disconnect(self.address)


async def capture_packets(
    address: str, characteristic: str, duration: int = 30, max_packets: int = 25
) -> list[dict]:
    """Capture raw packets from a device for the decoder wizard."""
    import time

    packets = []
    start = time.time()

    def on_data(data: bytes):
        packets.append(
            {
                "ts": f"{time.time() - start:.3f}",
                "char": characteristic,
                "hex": data.hex(),
                "dec": list(data),
            }
        )

    conn = BLEConnection(address, characteristic, on_data)
    await conn.connect()
    try:
        while time.time() - start < duration and len(packets) < max_packets:
            await asyncio.sleep(0.1)
    finally:
        await conn.disconnect()

    return packets
