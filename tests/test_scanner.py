"""BLEConnection and capture_packets tests: no bleak, no adapter, no radio.

``bleak`` is deliberately not installed. ``ble_scanner`` keeps it behind the
``_get_bleak()`` lazy shim precisely so the module imports on a box with no
Bluetooth stack, and these tests preserve that: they either stub the shim or
drive a ``BLEConnection`` whose ``_client`` was assigned by hand, which is the
state a successful ``connect()`` leaves behind.

Callback arity matters here. bleak calls ``disconnected_callback(client)`` and
``notify_callback(sender, data)`` positionally, so the fakes call them the same
way rather than by keyword.
"""

import asyncio
import importlib.util
import re

import pytest

import ble_scanner
from ble_scanner import BLEConnection

ADDRESS = "AA:BB:CC:11:22:33"
CHAR = "0000fff4-0000-1000-8000-00805f9b34fb"


class FakeClient:
    """Stands in for a BleakClient. Each phase can be told to raise."""

    def __init__(
        self,
        *,
        connect_error=None,
        stop_notify_error=None,
        disconnect_error=None,
        is_connected=True,
    ):
        self.connect_error = connect_error
        self.stop_notify_error = stop_notify_error
        self.disconnect_error = disconnect_error
        self.is_connected = is_connected
        self.target = None
        self.disconnected_callback = None
        self.calls: list[str] = []
        self.notify_args: tuple | None = None
        self.stop_notify_arg = None

    async def connect(self):
        self.calls.append("connect")
        if self.connect_error is not None:
            raise self.connect_error

    async def start_notify(self, characteristic, callback):
        self.calls.append("start_notify")
        self.notify_args = (characteristic, callback)

    async def stop_notify(self, characteristic):
        self.calls.append("stop_notify")
        self.stop_notify_arg = characteristic
        if self.stop_notify_error is not None:
            raise self.stop_notify_error

    async def disconnect(self):
        self.calls.append("disconnect")
        if self.disconnect_error is not None:
            raise self.disconnect_error


class FakeClientFactory:
    """Stands in for ``BleakClient``: hands out pre-built fakes in order."""

    def __init__(self, *clients):
        self._queue = list(clients)
        self.built: list[FakeClient] = []

    def __call__(self, target, disconnected_callback=None):
        client = self._queue.pop(0)
        client.target = target
        client.disconnected_callback = disconnected_callback
        self.built.append(client)
        return client


class FakeScanner:
    """Stands in for ``BleakScanner``: records every resolve attempt."""

    def __init__(self, result=None):
        self.result = result
        self.find_calls: list[tuple[tuple, dict]] = []

    async def find_device_by_address(self, address, **kwargs):
        self.find_calls.append(((address,), kwargs))
        return self.result


class FeedingConnection:
    """Stands in for ``BLEConnection`` inside ``capture_packets``."""

    instances: list["FeedingConnection"] = []

    def __init__(self, address, characteristic, on_data, on_disconnect=None):
        self.address = address
        self.characteristic = characteristic
        self.on_data = on_data
        self.on_disconnect = on_disconnect
        self.connected = False
        FeedingConnection.instances.append(self)

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False


def make_connection(client=None, on_data=None, on_disconnect=None, connected=True):
    """A BLEConnection in the state ``connect()`` would have left it in."""
    conn = BLEConnection(ADDRESS, CHAR, on_data or (lambda _d: None), on_disconnect)
    if client is not None:
        conn._client = client
        conn._connected = connected
    return conn


def stub_bleak(monkeypatch, scanner, factory):
    monkeypatch.setattr(ble_scanner, "_get_bleak", lambda: (scanner, factory))


# --------------------------------------------------------------------------
# disconnect(): the 2x2 raise matrix. contextlib.suppress wraps stop_notify
# ONLY -- widening it to cover self._client.disconnect() would swallow a real
# teardown failure and silently flip _connected to False.
# --------------------------------------------------------------------------


def test_disconnect_clean_path_stops_notify_then_disconnects():
    client = FakeClient()
    conn = make_connection(client)

    asyncio.run(conn.disconnect())

    assert client.calls == ["stop_notify", "disconnect"]
    assert client.stop_notify_arg == CHAR
    assert conn._connected is False


def test_disconnect_swallows_a_raising_stop_notify_and_carries_on():
    client = FakeClient(stop_notify_error=RuntimeError("no such characteristic"))
    conn = make_connection(client)

    asyncio.run(conn.disconnect())  # must not raise

    assert client.calls == ["stop_notify", "disconnect"]
    assert conn._connected is False


def test_disconnect_propagates_a_raising_disconnect_and_stays_connected():
    client = FakeClient(disconnect_error=RuntimeError("adapter gone"))
    conn = make_connection(client)

    with pytest.raises(RuntimeError, match="adapter gone"):
        asyncio.run(conn.disconnect())

    assert client.calls == ["stop_notify", "disconnect"]
    # The suppress() does not reach this line, so the flag is never cleared.
    assert conn._connected is True


def test_disconnect_with_both_raising_still_propagates_the_disconnect_error():
    client = FakeClient(
        stop_notify_error=RuntimeError("no such characteristic"),
        disconnect_error=RuntimeError("adapter gone"),
    )
    conn = make_connection(client)

    with pytest.raises(RuntimeError, match="adapter gone"):
        asyncio.run(conn.disconnect())

    assert client.calls == ["stop_notify", "disconnect"]
    assert conn._connected is True


def test_disconnect_is_a_no_op_when_the_link_was_never_up():
    client = FakeClient()
    conn = make_connection(client, connected=False)

    asyncio.run(conn.disconnect())

    assert client.calls == []


# --------------------------------------------------------------------------
# The two bleak callbacks.
# --------------------------------------------------------------------------


def test_handle_notification_converts_bytearray_to_bytes():
    received = []
    conn = make_connection(on_data=received.append)
    payload = bytearray(b"\x01\x02\xff")

    conn._handle_notification("sender-handle", payload)

    # ``bytearray(b"x") == b"x"`` is True, so equality alone proves nothing.
    assert type(received[0]) is bytes
    assert received == [b"\x01\x02\xff"]

    # And it is a copy, not a view onto the buffer bleak handed us -- whether
    # any backend actually reuses that buffer is not something this repo can
    # verify, so ``bytes()`` is doing the work of not having to care.
    payload[0] = 0x99
    assert received[0] == b"\x01\x02\xff"


def test_handle_disconnect_reports_the_address_not_the_client():
    seen = []
    client = FakeClient()
    conn = make_connection(client, on_disconnect=seen.append)

    conn._handle_disconnect(client)

    assert conn._connected is False
    # The bridge indexes ``self.connections`` by MAC and deletes on this
    # callback; handing it the client object would quietly stop removing
    # anything, and the device would never be re-dialled.
    assert seen == [ADDRESS]


def test_handle_disconnect_without_a_callback_is_harmless():
    conn = make_connection(FakeClient(), on_disconnect=None)

    conn._handle_disconnect(object())

    assert conn._connected is False


def test_is_connected_requires_all_three_conditions():
    conn = BLEConnection(ADDRESS, CHAR, lambda _d: None)
    # No client built yet.
    assert conn.is_connected() is False

    client = FakeClient(is_connected=True)
    conn._client = client

    # Client present and live, but connect() never completed.
    conn._connected = False
    assert conn.is_connected() is False

    conn._connected = True
    assert conn.is_connected() is True

    # bleak says the link dropped underneath us.
    client.is_connected = False
    assert conn.is_connected() is False

    # And the client can go away while the flag is still set.
    client.is_connected = True
    conn._client = None
    assert conn.is_connected() is False


# --------------------------------------------------------------------------
# connect(): direct dial, then the scan-and-retry fallback.
# --------------------------------------------------------------------------


def test_connect_direct_path_subscribes_without_scanning(monkeypatch):
    client = FakeClient()
    factory = FakeClientFactory(client)
    scanner = FakeScanner()
    stub_bleak(monkeypatch, scanner, factory)

    conn = BLEConnection(ADDRESS, CHAR, lambda _d: None)
    asyncio.run(conn.connect())

    # A scan cannot see an already-connected device, so it is never the first
    # move.
    assert scanner.find_calls == []
    assert len(factory.built) == 1
    assert client.target == ADDRESS
    assert client.disconnected_callback == conn._handle_disconnect
    assert client.calls == ["connect", "start_notify"]
    assert client.notify_args == (CHAR, conn._handle_notification)
    assert conn._connected is True


def test_connect_falls_back_to_a_scan_when_the_device_is_not_cached(monkeypatch):
    boom = RuntimeError("Device with address AA:BB:CC:11:22:33 was not found")
    first = FakeClient(connect_error=boom)
    second = FakeClient()
    factory = FakeClientFactory(first, second)
    resolved = object()  # a BLEDevice, as far as this code cares
    scanner = FakeScanner(result=resolved)
    stub_bleak(monkeypatch, scanner, factory)

    conn = BLEConnection(ADDRESS, CHAR, lambda _d: None)
    asyncio.run(conn.connect())

    assert scanner.find_calls == [((ADDRESS,), {"timeout": 20.0})]
    assert first.target == ADDRESS
    assert first.calls == ["connect"]
    # The retry dials the resolved BLEDevice, not the address string again.
    assert second.target is resolved
    assert second.calls == ["connect", "start_notify"]
    assert second.disconnected_callback == conn._handle_disconnect
    assert conn._client is second
    assert conn._connected is True


def test_the_not_found_match_is_case_insensitive(monkeypatch):
    first = FakeClient(connect_error=RuntimeError("Device NOT FOUND: AA:BB"))
    factory = FakeClientFactory(first, FakeClient())
    scanner = FakeScanner(result=object())
    stub_bleak(monkeypatch, scanner, factory)

    conn = BLEConnection(ADDRESS, CHAR, lambda _d: None)
    asyncio.run(conn.connect())

    assert len(scanner.find_calls) == 1
    assert conn._connected is True


def test_an_unrelated_connect_error_reraises_without_scanning(monkeypatch):
    boom = RuntimeError("bluetooth adapter is powered off")
    first = FakeClient(connect_error=boom)
    # A second client and a resolvable scan are staged deliberately: if the
    # "not found" test is dropped, this connect would *succeed* instead.
    factory = FakeClientFactory(first, FakeClient())
    scanner = FakeScanner(result=object())
    stub_bleak(monkeypatch, scanner, factory)

    conn = BLEConnection(ADDRESS, CHAR, lambda _d: None)
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(conn.connect())

    assert excinfo.value is boom
    assert scanner.find_calls == []
    assert len(factory.built) == 1
    assert conn._connected is False


def test_a_scan_that_resolves_nothing_reraises_the_original_error(monkeypatch):
    boom = RuntimeError("Device with address AA:BB:CC:11:22:33 was not found")
    first = FakeClient(connect_error=boom)
    factory = FakeClientFactory(first, FakeClient())
    scanner = FakeScanner(result=None)
    stub_bleak(monkeypatch, scanner, factory)

    conn = BLEConnection(ADDRESS, CHAR, lambda _d: None)
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(conn.connect())

    # The bare ``raise`` re-raises the original, not a new "no device" error.
    assert excinfo.value is boom
    assert scanner.find_calls == [((ADDRESS,), {"timeout": 20.0})]
    assert len(factory.built) == 1
    assert conn._connected is False


# --------------------------------------------------------------------------
# capture_packets and the lazy shim.
# --------------------------------------------------------------------------


def test_capture_packets_record_shape_and_max_packets(monkeypatch):
    FeedingConnection.instances = []
    monkeypatch.setattr(ble_scanner, "BLEConnection", FeedingConnection)

    fed = []

    async def feeding_sleep(_delay):
        fed.append(len(fed) + 1)
        FeedingConnection.instances[-1].on_data(bytes([len(fed), 0xAB]))

    monkeypatch.setattr(ble_scanner.asyncio, "sleep", feeding_sleep)

    packets = asyncio.run(
        ble_scanner.capture_packets(ADDRESS, CHAR, duration=30, max_packets=3)
    )

    # max_packets bounds the poll loop, so the fourth packet is never asked for.
    assert len(packets) == 3
    assert [list(p) for p in packets] == [["ts", "char", "hex", "dec"]] * 3
    assert [p["hex"] for p in packets] == ["01ab", "02ab", "03ab"]
    assert [p["dec"] for p in packets] == [[1, 171], [2, 171], [3, 171]]
    assert {p["char"] for p in packets} == {CHAR}
    assert all(re.fullmatch(r"\d+\.\d{3}", p["ts"]) for p in packets)

    conn = FeedingConnection.instances[-1]
    assert (conn.address, conn.characteristic) == (ADDRESS, CHAR)
    assert conn.connected is False  # torn down in the finally


def test_get_bleak_raises_a_helpful_import_error():
    """The exact message is the whole point of the shim; pin it verbatim."""
    if importlib.util.find_spec("bleak") is not None:
        pytest.skip("bleak is installed, so the ImportError branch is unreachable")

    with pytest.raises(ImportError) as excinfo:
        ble_scanner._get_bleak()

    assert str(excinfo.value) == (
        "bleak is required for BLE operations. Install with: pip install bleak"
    )
    # ``from None``: the raw ModuleNotFoundError is not chained onto it.
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
