"""MQTTPublisher contract tests: topics, payloads, retain flags, discovery.

No broker and no paho-mqtt. A recording fake client is injected as
``publisher._client`` with ``_connected`` set by hand, which is the state a
successful ``connect()`` would leave behind. Everything asserted here is a
live MQTT contract -- the retained topics Home Assistant reads -- so these
tests pin exact strings rather than shapes.
"""

import json

import pytest

import mqtt_publisher
from mqtt_publisher import MQTTPublisher
from template import Template

MAC = "A4:C1:38:11:22:33"
DEVICE_ID = "a4_c1_38_11_22_33"
DEVICE_NAME = "Temtop M10"
LOCATION = "Living Room"

# Modelled on templates/temtop_m10.json: one convertible temperature, one
# plain unit, one field with no device_class, one with no icon.
TEMPLATE_DEF = {
    "template_version": 1,
    "name": "Temtop M10",
    "manufacturer": "Temtop",
    "model": "M10",
    "characteristic": "00010203-0405-0607-0809-0a0b0c0d2b10",
    "fields": [
        {
            "key": "formaldehyde",
            "name": "Formaldehyde",
            "start_byte": 18,
            "end_byte": 19,
            "type": "uint16_be",
            "scale": 0.01,
            "unit": "mg/m³",
            "state_class": "measurement",
            "icon": "mdi:chemical-weapon",
        },
        {
            "key": "pm25",
            "name": "PM2.5",
            "start_byte": 22,
            "end_byte": 23,
            "type": "uint16_be",
            "scale": 0.1,
            "unit": "µg/m³",
            "device_class": "pm25",
            "state_class": "measurement",
            "icon": "mdi:air-filter",
        },
        {
            "key": "temp",
            "name": "Temperature",
            "start_byte": 26,
            "end_byte": 27,
            "type": "int16_be",
            "scale": 0.1,
            "unit": "°C",
            "device_class": "temperature",
            "state_class": "measurement",
            "icon": "mdi:thermometer",
            "fahrenheit_convert": True,
        },
        {
            "key": "humidity",
            "name": "Humidity",
            "start_byte": 28,
            "end_byte": 29,
            "type": "uint16_be",
            "scale": 0.1,
            "unit": "%",
            "device_class": "humidity",
            "state_class": "measurement",
        },
        {
            # Bare on purpose: no unit, no device_class, no state_class, no
            # icon. Every one of the four optional discovery keys is guarded
            # by an ``if`` in _publish_discovery, and without a field like this
            # none of those guards is exercised.
            "key": "status",
            "name": "Status",
            "start_byte": 4,
            "end_byte": 9,
            "type": "ascii",
        },
    ],
}

# A realistic decoded packet. ``temp`` is already in whatever scale the
# bridge asked the decoder for -- the publisher never converts.
DECODED = {
    "formaldehyde": 0.03,
    "pm25": 12.4,
    "temp": 21.5,
    "humidity": 44.2,
    "status": "OK",
}


class RecordingClient:
    """Stands in for paho's Client: records publishes, swallows lifecycle.

    ``will_set`` is a deliberate trip-wire rather than a convenience -- see
    ``test_connect_registers_no_last_will``. paho's real client accepts a Last
    Will at any point before ``connect()``, so a fake without the method would
    let a Last Will be added to ``MQTTPublisher.connect()`` unnoticed.
    """

    def __init__(self):
        self.calls: list[tuple[str, str, bool]] = []
        self.lifecycle: list[str] = []
        self.will_set_calls: list[tuple[tuple, dict]] = []
        self.credentials: tuple[str, str] | None = None
        self.connect_args: tuple | None = None
        self.on_connect = None
        self.on_disconnect = None

    def publish(self, topic, payload, retain=False):
        self.calls.append((topic, payload, retain))

    def will_set(self, *args, **kwargs):
        self.will_set_calls.append((args, kwargs))

    def username_pw_set(self, username, password):
        self.lifecycle.append("username_pw_set")
        self.credentials = (username, password)

    def connect(self, host, port, keepalive=60):
        self.lifecycle.append("connect")
        self.connect_args = (host, port, keepalive)

    def loop_start(self):
        self.lifecycle.append("loop_start")
        # paho fires on_connect from its own network thread once CONNACK
        # arrives, positionally and with rc=0 for success.
        self.on_connect(self, None, {}, 0)

    def loop_stop(self):
        self.lifecycle.append("loop_stop")

    def disconnect(self):
        self.lifecycle.append("disconnect")


class FakeMQTTModule:
    """Stands in for the ``paho.mqtt.client`` module that _get_mqtt imports."""

    def __init__(self, client):
        self._client = client

    def Client(self):
        return self._client


def make_template() -> Template:
    return Template(TEMPLATE_DEF)


def make_publisher(**kwargs):
    """Build a publisher wired to a recording client, as if connected."""
    publisher = MQTTPublisher(**kwargs)
    client = RecordingClient()
    publisher._client = client
    publisher._connected = True
    return publisher, client


def publish_once(publisher, decoded=None, fahrenheit=False):
    publisher.publish_sensor(
        MAC,
        DEVICE_NAME,
        LOCATION,
        make_template(),
        DECODED if decoded is None else decoded,
        fahrenheit=fahrenheit,
    )


def topics(client) -> list[str]:
    return [topic for topic, _payload, _retain in client.calls]


def payload_for(client, topic) -> str:
    matches = [p for t, p, _r in client.calls if t == topic]
    assert matches, f"no publish to {topic}; got {topics(client)}"
    return matches[-1]


def test_state_topics_payloads_and_retain():
    publisher, client = make_publisher()
    publish_once(publisher)

    expected = [
        ("ble/a4_c1_38_11_22_33/formaldehyde/state", "0.03", True),
        ("ble/a4_c1_38_11_22_33/pm25/state", "12.4", True),
        ("ble/a4_c1_38_11_22_33/temp/state", "21.5", True),
        ("ble/a4_c1_38_11_22_33/humidity/state", "44.2", True),
        ("ble/a4_c1_38_11_22_33/status/state", "OK", True),
    ]
    states = [c for c in client.calls if c[0].endswith("/state")]
    assert states == expected


def test_availability_is_online_and_retained():
    publisher, client = make_publisher()
    publish_once(publisher)

    availability = [c for c in client.calls if c[0].endswith("/availability")]
    assert availability == [("ble/a4_c1_38_11_22_33/availability", "online", True)]


def test_discovery_topic_and_exact_config():
    publisher, client = make_publisher()
    publish_once(publisher, fahrenheit=False)

    topic = "homeassistant/sensor/ble_a4_c1_38_11_22_33_temp/config"
    matching = [c for c in client.calls if c[0] == topic]
    assert len(matching) == 1
    assert matching[0][2] is True

    config = json.loads(matching[0][1])
    assert config == {
        "name": "Temperature",
        "unique_id": "ble_a4_c1_38_11_22_33_temp",
        "state_topic": "ble/a4_c1_38_11_22_33/temp/state",
        "availability_topic": "ble/a4_c1_38_11_22_33/availability",
        "device": {
            "identifiers": ["a4_c1_38_11_22_33"],
            "name": "Temtop M10 Living Room",
            "manufacturer": "Temtop",
            "model": "M10",
            "via_device": "ble_mqtt_bridge",
        },
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "state_class": "measurement",
        "icon": "mdi:thermometer",
    }
    # Key order is part of the retained payload; pin it too.
    assert list(config) == [
        "name",
        "unique_id",
        "state_topic",
        "availability_topic",
        "device",
        "unit_of_measurement",
        "device_class",
        "state_class",
        "icon",
    ]
    assert list(config["device"]) == [
        "identifiers",
        "name",
        "manufacturer",
        "model",
        "via_device",
    ]


def test_optional_config_keys_omitted_when_falsy():
    publisher, client = make_publisher()
    publish_once(publisher)

    topic = "homeassistant/sensor/ble_a4_c1_38_11_22_33_formaldehyde/config"
    config = json.loads(payload_for(client, topic))
    # No device_class on this field, so the key is absent entirely.
    assert "device_class" not in config
    assert config["unit_of_measurement"] == "mg/m³"
    assert config["icon"] == "mdi:chemical-weapon"

    humidity_topic = "homeassistant/sensor/ble_a4_c1_38_11_22_33_humidity/config"
    humidity = json.loads(payload_for(client, humidity_topic))
    # No icon on this field.
    assert "icon" not in humidity
    assert humidity["device_class"] == "humidity"


def test_field_with_no_optional_metadata_omits_all_four_keys():
    """``status`` carries no unit, device_class, state_class or icon.

    Each of the four is emitted only behind its own ``if`` in
    ``_publish_discovery``. HA reads an absent key and an empty-string key
    differently, and every field in this template used to carry a unit, so
    dropping the ``if unit:`` guard changed nothing the suite could see.
    """
    publisher, client = make_publisher()
    publish_once(publisher)

    topic = "homeassistant/sensor/ble_a4_c1_38_11_22_33_status/config"
    config = json.loads(payload_for(client, topic))
    assert "unit_of_measurement" not in config
    assert "device_class" not in config
    assert "state_class" not in config
    assert "icon" not in config
    assert list(config) == [
        "name",
        "unique_id",
        "state_topic",
        "availability_topic",
        "device",
    ]


@pytest.mark.parametrize(
    ("value", "payload"),
    [
        (0, "0"),
        (0.0, "0.0"),
        (-0.0, "-0.0"),
        (False, "False"),
        ("", ""),
    ],
    ids=["int-zero", "float-zero", "negative-zero", "false", "empty-string"],
)
def test_falsy_but_present_values_are_still_published(value, payload):
    """Only ``None`` suppresses a field; a genuine zero must still be sent.

    The guard is ``if value is None``, not ``if not value``, and the difference
    is load-bearing: the state topics are *retained*, so a dropped ``0`` does
    not read as zero in HA, it goes on reading as whatever the last non-zero
    sample was -- potentially for months. A reading that vanishes is worse than
    a reading that is wrong.
    """
    publisher, client = make_publisher()
    publish_once(publisher, decoded=dict(DECODED, pm25=value))

    assert ("ble/a4_c1_38_11_22_33/pm25/state", payload, True) in client.calls
    # Discovery rides along with the state publish, so it must not be skipped.
    assert "homeassistant/sensor/ble_a4_c1_38_11_22_33_pm25/config" in topics(client)


def test_none_valued_field_publishes_nothing_for_that_field():
    publisher, client = make_publisher()
    decoded = dict(DECODED, temp=None)
    publish_once(publisher, decoded=decoded)

    assert "ble/a4_c1_38_11_22_33/temp/state" not in topics(client)
    assert (
        "homeassistant/sensor/ble_a4_c1_38_11_22_33_temp/config" not in topics(client)
    )
    # The other fields are unaffected.
    assert "ble/a4_c1_38_11_22_33/pm25/state" in topics(client)


def test_missing_key_in_decoded_dict_publishes_nothing_for_that_field():
    publisher, client = make_publisher()
    decoded = {k: v for k, v in DECODED.items() if k != "pm25"}
    publish_once(publisher, decoded=decoded)

    assert "ble/a4_c1_38_11_22_33/pm25/state" not in topics(client)


def test_fahrenheit_true_advertises_f_on_convertible_field():
    publisher, client = make_publisher()
    publish_once(publisher, fahrenheit=True)

    topic = "homeassistant/sensor/ble_a4_c1_38_11_22_33_temp/config"
    config = json.loads(payload_for(client, topic))
    assert config["unit_of_measurement"] == "°F"


def test_fahrenheit_false_advertises_the_templates_own_unit():
    publisher, client = make_publisher()
    publish_once(publisher, fahrenheit=False)

    topic = "homeassistant/sensor/ble_a4_c1_38_11_22_33_temp/config"
    config = json.loads(payload_for(client, topic))
    assert config["unit_of_measurement"] == "°C"


def test_non_temperature_field_unaffected_by_fahrenheit_flag():
    for flag in (False, True):
        publisher, client = make_publisher()
        publish_once(publisher, fahrenheit=flag)
        topic = "homeassistant/sensor/ble_a4_c1_38_11_22_33_pm25/config"
        config = json.loads(payload_for(client, topic))
        assert config["unit_of_measurement"] == "µg/m³"


def test_discovery_published_once_state_published_every_time():
    publisher, client = make_publisher()
    publish_once(publisher)
    publish_once(publisher)

    disc_topic = "homeassistant/sensor/ble_a4_c1_38_11_22_33_temp/config"
    state_topic = "ble/a4_c1_38_11_22_33/temp/state"
    assert topics(client).count(disc_topic) == 1
    assert topics(client).count(state_topic) == 2
    assert topics(client).count("ble/a4_c1_38_11_22_33/availability") == 2


def test_custom_prefixes_flow_through_every_topic():
    publisher, client = make_publisher(
        topic_prefix="sensors/ble", discovery_prefix="ha"
    )
    publish_once(publisher)

    assert "sensors/ble/a4_c1_38_11_22_33/temp/state" in topics(client)
    assert "sensors/ble/a4_c1_38_11_22_33/availability" in topics(client)
    disc_topic = "ha/sensor/ble_a4_c1_38_11_22_33_temp/config"
    assert disc_topic in topics(client)

    config = json.loads(payload_for(client, disc_topic))
    assert config["state_topic"] == "sensors/ble/a4_c1_38_11_22_33/temp/state"
    assert config["availability_topic"] == "sensors/ble/a4_c1_38_11_22_33/availability"
    # The unique_id scheme does not pick up the prefix.
    assert config["unique_id"] == "ble_a4_c1_38_11_22_33_temp"


def test_discovery_disabled_still_publishes_state_and_availability():
    publisher, client = make_publisher(discovery=False)
    publish_once(publisher)

    assert not [t for t in topics(client) if t.startswith("homeassistant/")]
    assert "ble/a4_c1_38_11_22_33/temp/state" in topics(client)
    assert "ble/a4_c1_38_11_22_33/availability" in topics(client)


def test_nothing_is_published_when_not_connected():
    publisher, client = make_publisher()
    publisher._connected = False
    publish_once(publisher)

    assert client.calls == []


def test_disconnect_publishes_no_offline_payload():
    """Pins HALF of a KNOWN DEFECT: teardown publishes no ``offline``.

    The bridge never marks itself offline, so the retained ``online`` outlives
    the process. Fixing that needs a new retained payload and a fleet deploy,
    so it is parked (2026-09-09 audit).

    This test covers the teardown half only -- that ``disconnect()`` itself
    publishes nothing. The other half, that ``connect()`` registers no Last
    Will, is pinned by ``test_connect_registers_no_last_will``. When the fix
    lands, both must be inverted together; neither should be deleted.
    """
    publisher, client = make_publisher()
    publish_once(publisher)
    before = len(client.calls)

    publisher.disconnect()

    assert len(client.calls) == before
    assert "offline" not in [payload for _t, payload, _r in client.calls]
    assert client.lifecycle == ["loop_stop", "disconnect"]
    assert publisher.is_connected() is False


def test_connect_registers_no_last_will(monkeypatch):
    """Pins the OTHER half of that defect: ``connect()`` sets no Last Will.

    Every other test here hands the publisher an already-wired fake and sets
    ``_connected`` by hand, so ``connect()`` was never executed at all -- a
    ``will_set`` call could be added to it and the suite would stay green. This
    drives the real ``connect()`` against the recording fake, whose
    ``will_set`` exists purely as a trip-wire.

    ``_get_mqtt`` is stubbed rather than imported: paho-mqtt is not installed,
    and the lazy shim is what keeps it that way.
    """
    publisher = MQTTPublisher(
        host="broker.example", port=1884, username="user", password="pw"
    )
    client = RecordingClient()
    monkeypatch.setattr(mqtt_publisher, "_get_mqtt", lambda: FakeMQTTModule(client))

    publisher.connect()

    assert client.will_set_calls == []
    assert client.lifecycle == ["username_pw_set", "connect", "loop_start"]
    assert client.connect_args == ("broker.example", 1884, 60)
    assert client.credentials == ("user", "pw")
    # Nothing is published on connect either -- no retained availability marker.
    assert client.calls == []
    assert publisher._client is client
    assert publisher.is_connected() is True
