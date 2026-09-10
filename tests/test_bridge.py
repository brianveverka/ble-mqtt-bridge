"""BLEBridge loop tests: which devices get dialled, and what the callbacks do.

No bleak, no broker, no radio. ``TemplateRegistry`` and ``BLEConnection`` are
stubbed at their import sites in ``ble_mqtt_bridge``, and one pass of
``_run_loop`` is driven by replacing ``asyncio.sleep`` with a coroutine that
clears the running flag, so the loop terminates after exactly one sweep.
"""

import asyncio

import pytest

import ble_mqtt_bridge

BRIDGE_CONF = """\
[mqtt]
host = mqtt.example
port = 1883
topic_prefix = ble
discovery = true
discovery_prefix = homeassistant

[bridge]
reconnect_delay = 5
log_level = INFO
fahrenheit = true
"""

GOOD_MAC = "AA:BB:CC:00:00:01"
BAD_TEMPLATE_MAC = "AA:BB:CC:00:00:02"
NO_CHAR_MAC = "AA:BB:CC:00:00:03"

DEVICES_CONF = f"""\
[device:{GOOD_MAC}]
template = good
name = Good Sensor
location = Kitchen

[device:{BAD_TEMPLATE_MAC}]
template = nope
name = Unknown Template

[device:{NO_CHAR_MAC}]
template = no_char
name = Broadcast Only
location = Shed
"""


class StubTemplate:
    """Only the attributes BLEBridge actually touches."""

    def __init__(self, characteristic="0000fff4-0000-1000-8000-00805f9b34fb"):
        self.characteristic = characteristic
        self.manufacturer = "Stub"
        self.model = "S1"
        self.fields = []
        self.decode_calls: list[tuple[bytes, bool]] = []

    def decode(self, data, fahrenheit=False):
        self.decode_calls.append((bytes(data), fahrenheit))
        return {"temp": 21.5}


class StubRegistry:
    def __init__(self, templates):
        self.templates = dict(templates)

    def get(self, template_id):
        return self.templates.get(template_id)


class StubConnection:
    """Records construction args and the callbacks the bridge handed over."""

    instances: list["StubConnection"] = []
    connect_error: Exception | None = None

    def __init__(self, address, characteristic, on_data, on_disconnect=None):
        self.address = address
        self.characteristic = characteristic
        self.on_data = on_data
        self.on_disconnect = on_disconnect
        self.connected = False
        StubConnection.instances.append(self)

    async def connect(self):
        if StubConnection.connect_error is not None:
            raise StubConnection.connect_error
        self.connected = True

    async def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected


class RecordingPublisher:
    def __init__(self):
        self.calls: list[tuple[tuple, dict]] = []

    def publish_sensor(self, *args, **kwargs):
        self.calls.append((args, kwargs))


@pytest.fixture(autouse=True)
def _reset_stub_connection():
    StubConnection.instances = []
    StubConnection.connect_error = None
    yield
    StubConnection.instances = []
    StubConnection.connect_error = None


def make_bridge(tmp_path, monkeypatch, templates, devices_conf=DEVICES_CONF):
    bridge_conf_path = tmp_path / "bridge.conf"
    bridge_conf_path.write_text(BRIDGE_CONF, encoding="utf-8")
    devices_conf_path = tmp_path / "devices.conf"
    devices_conf_path.write_text(devices_conf, encoding="utf-8")

    monkeypatch.setattr(
        ble_mqtt_bridge, "TemplateRegistry", lambda: StubRegistry(templates)
    )
    monkeypatch.setattr(ble_mqtt_bridge, "BLEConnection", StubConnection)

    bridge = ble_mqtt_bridge.BLEBridge(
        str(bridge_conf_path), str(devices_conf_path)
    )
    bridge.publisher = RecordingPublisher()
    return bridge


def run_one_sweep(bridge, monkeypatch):
    """Drive _run_loop for exactly one pass over the device list."""

    async def fake_sleep(_delay):
        bridge._running = False

    monkeypatch.setattr(ble_mqtt_bridge.asyncio, "sleep", fake_sleep)
    bridge._running = True
    asyncio.run(bridge._run_loop())


def default_templates():
    return {"good": StubTemplate(), "no_char": StubTemplate(characteristic="")}


def test_config_is_loaded_from_the_given_paths(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, default_templates())
    assert bridge.fahrenheit is True
    assert set(bridge.devices) == {GOOD_MAC, BAD_TEMPLATE_MAC, NO_CHAR_MAC}


def test_unknown_template_is_skipped_without_connecting(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, default_templates())
    run_one_sweep(bridge, monkeypatch)

    dialled = [c.address for c in StubConnection.instances]
    assert BAD_TEMPLATE_MAC not in dialled
    assert BAD_TEMPLATE_MAC not in bridge.connections


def test_template_without_characteristic_is_skipped(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, default_templates())
    run_one_sweep(bridge, monkeypatch)

    dialled = [c.address for c in StubConnection.instances]
    assert NO_CHAR_MAC not in dialled
    assert NO_CHAR_MAC not in bridge.connections
    # The one healthy device still got its turn.
    assert dialled == [GOOD_MAC]


def test_already_connected_device_is_not_redialled(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, default_templates())
    existing = StubConnection(GOOD_MAC, "char", lambda _d: None)
    existing.connected = True
    bridge.connections[GOOD_MAC] = existing
    StubConnection.instances = []

    run_one_sweep(bridge, monkeypatch)

    assert StubConnection.instances == []
    assert bridge.connections[GOOD_MAC] is existing


def test_disconnected_device_in_connections_is_redialled(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, default_templates())
    stale = StubConnection(GOOD_MAC, "char", lambda _d: None)
    stale.connected = False
    bridge.connections[GOOD_MAC] = stale
    StubConnection.instances = []

    run_one_sweep(bridge, monkeypatch)

    assert [c.address for c in StubConnection.instances] == [GOOD_MAC]
    assert bridge.connections[GOOD_MAC] is not stale


def test_a_raising_connection_is_caught_and_others_still_attempted(
    tmp_path, monkeypatch
):
    devices_conf = f"""\
[device:{GOOD_MAC}]
template = good
name = Explodes

[device:AA:BB:CC:00:00:09]
template = good
name = Fine
location = Den
"""
    templates = {"good": StubTemplate()}
    bridge = make_bridge(tmp_path, monkeypatch, templates, devices_conf=devices_conf)

    attempted = []
    real_connect = bridge._connect_device

    async def flaky_connect(mac, dev_cfg, template):
        attempted.append(mac)
        if mac == GOOD_MAC:
            raise RuntimeError("radio busy")
        return await real_connect(mac, dev_cfg, template)

    monkeypatch.setattr(bridge, "_connect_device", flaky_connect)
    run_one_sweep(bridge, monkeypatch)

    assert attempted == [GOOD_MAC, "AA:BB:CC:00:00:09"]
    assert GOOD_MAC not in bridge.connections
    assert "AA:BB:CC:00:00:09" in bridge.connections


def test_clearing_running_mid_sweep_abandons_the_rest_of_the_device_list(
    tmp_path, monkeypatch
):
    """A shutdown signal during a dial must stop the sweep, not finish the list.

    ``run_one_sweep``'s fake sleep clears the flag at the *tail* of the loop,
    which never reaches the ``if not self._running: break`` guard inside the
    for-loop -- so that guard was invisible to the suite. It matters because a
    single dial can block for tens of seconds (an unresolvable device alone
    costs a 20s scan), and without it SIGTERM waits out every device left in
    the list.
    """
    second = "AA:BB:CC:00:00:0A"
    third = "AA:BB:CC:00:00:0B"
    devices_conf = f"""\
[device:{GOOD_MAC}]
template = good
name = First

[device:{second}]
template = good
name = Second

[device:{third}]
template = good
name = Third
"""
    templates = {"good": StubTemplate()}
    bridge = make_bridge(tmp_path, monkeypatch, templates, devices_conf=devices_conf)

    attempted = []
    real_connect = bridge._connect_device

    async def stopping_connect(mac, dev_cfg, template):
        attempted.append(mac)
        bridge._running = False  # as the SIGTERM handler would, mid-dial
        return await real_connect(mac, dev_cfg, template)

    monkeypatch.setattr(bridge, "_connect_device", stopping_connect)

    slept = []

    async def counting_sleep(delay):
        slept.append(delay)

    monkeypatch.setattr(ble_mqtt_bridge.asyncio, "sleep", counting_sleep)
    bridge._running = True
    asyncio.run(bridge._run_loop())

    assert attempted == [GOOD_MAC]
    assert [c.address for c in StubConnection.instances] == [GOOD_MAC]
    assert list(bridge.connections) == [GOOD_MAC]
    # ``break`` leaves the for-loop, not the while-loop, so the tail sleep
    # still runs exactly once before the while condition ends the run.
    assert slept == [5]


def test_on_data_decodes_with_bridge_fahrenheit_and_forwards_to_publisher(
    tmp_path, monkeypatch
):
    templates = default_templates()
    bridge = make_bridge(tmp_path, monkeypatch, templates)
    run_one_sweep(bridge, monkeypatch)

    conn = bridge.connections[GOOD_MAC]
    assert conn.characteristic == templates["good"].characteristic

    packet = bytes.fromhex("deadbeef")
    conn.on_data(packet)

    template = templates["good"]
    assert template.decode_calls == [(packet, True)]

    assert len(bridge.publisher.calls) == 1
    args, kwargs = bridge.publisher.calls[0]
    assert args == (GOOD_MAC, "Good Sensor", "Kitchen", template, {"temp": 21.5})
    assert kwargs == {"fahrenheit": True}


def test_on_data_uses_fahrenheit_false_when_the_bridge_is_celsius(
    tmp_path, monkeypatch
):
    templates = default_templates()
    bridge = make_bridge(tmp_path, monkeypatch, templates)
    bridge.fahrenheit = False
    run_one_sweep(bridge, monkeypatch)

    bridge.connections[GOOD_MAC].on_data(b"\x01\x02")

    assert templates["good"].decode_calls == [(b"\x01\x02", False)]
    _args, kwargs = bridge.publisher.calls[0]
    assert kwargs == {"fahrenheit": False}


def test_on_disconnect_removes_the_mac_from_connections(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, default_templates())
    run_one_sweep(bridge, monkeypatch)

    conn = bridge.connections[GOOD_MAC]
    assert conn.on_disconnect is not None

    conn.on_disconnect(GOOD_MAC)
    assert GOOD_MAC not in bridge.connections

    # Idempotent: a second callback for an already-removed mac is harmless.
    conn.on_disconnect(GOOD_MAC)
    assert GOOD_MAC not in bridge.connections


def test_devices_with_no_name_fall_back_to_the_mac(tmp_path, monkeypatch):
    devices_conf = f"[device:{GOOD_MAC}]\ntemplate = good\n"
    templates = {"good": StubTemplate()}
    bridge = make_bridge(tmp_path, monkeypatch, templates, devices_conf=devices_conf)
    run_one_sweep(bridge, monkeypatch)

    bridge.connections[GOOD_MAC].on_data(b"\x00")
    args, _kwargs = bridge.publisher.calls[0]
    assert args[1] == GOOD_MAC
    assert args[2] == ""


def test_cleanup_disconnects_everything_and_survives_a_raiser(tmp_path, monkeypatch):
    bridge = make_bridge(tmp_path, monkeypatch, default_templates())
    run_one_sweep(bridge, monkeypatch)

    good = bridge.connections[GOOD_MAC]

    class Exploder:
        async def disconnect(self):
            raise RuntimeError("already gone")

    bridge.connections["AA:BB:CC:00:00:FF"] = Exploder()

    asyncio.run(bridge._cleanup())

    assert good.connected is False
    assert bridge.connections == {}
