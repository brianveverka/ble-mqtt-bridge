"""Tests for :mod:`template` — the BLE packet decoding template system.

These are characterisation tests as much as unit tests. Several of them pin
behaviour that is *not* desirable but *is* current (short reads, silently
dropped config sections); those are marked PINNED and name the reason. Do not
"fix" a PINNED assertion without changing the production behaviour first.

Every expected numeric value here is hand-computed from the formula in
``TemplateField.decode`` (``value * scale + offset``, then the optional
``* 9 / 5 + 32``, then ``round(value, 2)``) — not captured from a run.
"""

import json
import struct
from pathlib import Path

import pytest

import template

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"


def make_field(**overrides) -> template.TemplateField:
    """A TemplateField from a minimal definition plus overrides."""
    definition = {"key": "v", "start_byte": 0}
    definition.update(overrides)
    return template.TemplateField(definition)


# ---------------------------------------------------------------------------
# 1. _decode_raw — every branch of the dispatch
# ---------------------------------------------------------------------------

# (field_type, raw bytes, expected raw value)
RAW_CASES = [
    ("uint8", b"\x2a", 42),
    ("uint8", b"\xff", 255),
    ("int8", b"\xff", -1),
    ("int8", b"\x80", -128),
    ("int8", b"\x7f", 127),
    ("uint16_be", b"\x01\x2c", 300),
    ("uint16_be", b"\xff\xff", 65535),
    ("uint16_le", b"\x01\x2c", 11265),
    ("int16_be", b"\xff\xfe", -2),
    ("int16_be", b"\x09\xc4", 2500),
    ("int16_le", b"\xfe\xff", -2),
    ("uint32_be", b"\x00\x01\x00\x00", 65536),
    ("uint32_le", b"\x00\x00\x01\x00", 65536),
    ("float32_be", b"\x3f\xc0\x00\x00", 1.5),
    ("float32_le", b"\x00\x00\xc0\x3f", 1.5),
    ("float32_be", b"\x3f\x00\x00\x00", 0.5),
    ("bool", b"\x00", False),
    ("bool", b"\x01", True),
    ("bool", b"\xff", True),
    ("ascii", b"V1.6.3", "V1.6.3"),
]


@pytest.mark.parametrize("field_type,raw,expected", RAW_CASES)
def test_decode_raw_branches(field_type, raw, expected):
    field = make_field(type=field_type)
    result = field._decode_raw(raw)
    assert result == expected
    assert type(result) is type(expected)


def test_decode_raw_covers_every_documented_type():
    """The 12 supported types all have a branch (no silent fall-through)."""
    every_type = {
        "uint8",
        "int8",
        "uint16_be",
        "uint16_le",
        "int16_be",
        "int16_le",
        "uint32_be",
        "uint32_le",
        "float32_be",
        "float32_le",
        "bool",
        "ascii",
    }
    assert every_type == {c[0] for c in RAW_CASES}
    for field_type in sorted(every_type):
        # 4 bytes is enough for the widest type; none of these may raise.
        make_field(type=field_type)._decode_raw(b"\x01\x02\x03\x04")


def test_decode_raw_ascii_strips_trailing_nuls_and_replaces_bad_bytes():
    assert make_field(type="ascii")._decode_raw(b"V1.6.3\x00\x00") == "V1.6.3"
    # errors="replace" -> U+FFFD, and only NULs are stripped (not whitespace)
    replacement_char = chr(0xFFFD)
    assert make_field(type="ascii")._decode_raw(b"AB\xffC") == "AB" + replacement_char + "C"
    assert make_field(type="ascii")._decode_raw(b" hi \x00") == " hi "


def test_decode_raw_unknown_type_raises_value_error():
    field = make_field(type="uint24_be")
    with pytest.raises(ValueError, match="Unsupported field type: uint24_be"):
        field._decode_raw(b"\x01\x02\x03")


def test_default_field_type_is_uint8():
    assert make_field().field_type == "uint8"
    assert make_field().decode(b"\x07") == 7


def test_field_name_defaults_to_the_key():
    """An omitted "name" falls back to the field's own key, not to ""."""
    assert make_field().name == "v"
    assert make_field().to_dict()["name"] == "v"
    assert make_field(key="probe").name == "probe"
    # an explicit name still wins
    assert make_field(name="Probe").name == "Probe"


def test_field_end_byte_defaults_to_start_byte():
    """An omitted "end_byte" is start_byte — a one-byte slice there, not byte 0.

    Every other field in this file starts at byte 0, where a `0` default would
    be indistinguishable; this one deliberately does not.
    """
    field = make_field(start_byte=3)
    assert field.end_byte == 3
    assert field.to_dict()["end_byte"] == 3
    # data[3:4] -> 0x2a, and nothing from bytes 0-2 leaks in
    assert field.decode(b"\x00\x00\x00\x2a") == 42


# ---------------------------------------------------------------------------
# 2. TemplateField.decode — scale / offset / fahrenheit / rounding
# ---------------------------------------------------------------------------


def test_decode_scale_alone():
    # 100 * 0.1 = 10.000000000000002 -> round(_, 2) == 10.0
    result = make_field(type="uint8", scale=0.1).decode(b"\x64")
    assert result == 10.0
    # `== 10.0` alone also passes for int 10, and an int here would mean the
    # float scale was never applied at all — so pin the type too.
    assert type(result) is float


def test_decode_offset_alone_stays_an_int():
    # 100 * 1 + (-32) == 68, int arithmetic, so no rounding is applied
    result = make_field(type="uint8", offset=-32).decode(b"\x64")
    assert result == 68
    assert isinstance(result, int)


def test_decode_scale_and_offset_together():
    # 200 * 0.5 + 3 = 103.0
    assert make_field(type="uint8", scale=0.5, offset=3).decode(b"\xc8") == 103.0


def test_decode_no_scale_no_offset_is_identity():
    assert make_field(type="uint16_be", end_byte=1).decode(b"\x01\x2c") == 300


def test_decode_fahrenheit_flag_false_means_no_conversion():
    field = make_field(type="uint8", fahrenheit_convert=True)
    assert field.decode(b"\x64", fahrenheit=False) == 100
    # the default is also False
    assert field.decode(b"\x64") == 100


def test_decode_fahrenheit_flag_true_converts():
    field = make_field(type="uint8", fahrenheit_convert=True)
    # 100 * 9 / 5 + 32 = 212.0
    assert field.decode(b"\x64", fahrenheit=True) == 212.0


def test_decode_fahrenheit_ignored_when_field_not_marked():
    """fahrenheit=True must not touch a field without fahrenheit_convert."""
    field = make_field(type="uint8", fahrenheit_convert=False)
    assert field.decode(b"\x64", fahrenheit=True) == 100


def test_decode_rounds_to_two_decimal_places():
    # 3 * 0.3333333333 = 0.9999999999 -> 1.0
    assert make_field(type="uint8", scale=0.3333333333).decode(b"\x03") == 1.0
    # 12345 * 0.001 = 12.345000000000001 -> 12.35
    assert make_field(type="uint16_be", end_byte=1, scale=0.001).decode(b"\x30\x39") == 12.35


def test_decode_bool_skips_arithmetic_even_with_scale_and_offset():
    """bool is excluded by the `not isinstance(value, bool)` guard."""
    field = make_field(type="bool", scale=100, offset=5, fahrenheit_convert=True)
    assert field.decode(b"\x01", fahrenheit=True) is True
    assert field.decode(b"\x00", fahrenheit=True) is False


def test_decode_ascii_skips_arithmetic_even_with_scale_and_offset():
    field = make_field(type="ascii", end_byte=1, scale=100, offset=5)
    assert field.decode(b"OK") == "OK"


def test_decode_slices_start_to_end_byte_inclusive():
    field = make_field(type="uint16_be", start_byte=2, end_byte=3)
    assert field.decode(b"\xaa\xbb\x01\x2c\xcc") == 300


# ---------------------------------------------------------------------------
# 3. Golden decodes from the repo's own calibrations
#    These are regression tests for a documented past bug. See AGENT_GUIDE.md.
# ---------------------------------------------------------------------------

# templates/thermopro_tp960.json — probe is byte 2, uint8, calibrated against
# two equilibrium fixed points: ice water and a rolling boil.
THERMOPRO_PROBE = {
    "key": "probe",
    "start_byte": 2,
    "end_byte": 2,
    "type": "uint8",
    "scale": 1.07527,
    "offset": -39.785,
    "fahrenheit_convert": True,
}


def thermopro_packet(probe_byte: int) -> bytes:
    """An 8-byte TP960 packet shaped `10 00 [probe] 00 [..] 0a [..] 00`."""
    return bytes([0x10, 0x00, probe_byte, 0x00, 0x00, 0x0A, 0x00, 0x00])


def test_thermopro_ice_water_is_zero_celsius():
    """b2=37 is the ice-water fixed point: 37 * 1.07527 - 39.785 = -1e-05."""
    field = template.TemplateField(THERMOPRO_PROBE)
    result = field.decode(thermopro_packet(37))
    # round(-1e-05, 2) is -0.0, and -0.0 == 0.0 in Python — which means a bare
    # `== 0.0` would also pass for False, int 0 and +0.0, i.e. for a decoder
    # that had stopped doing the arithmetic at all. The sign is the evidence
    # that `37 * 1.07527 - 39.785` really ran and landed just below zero, so
    # pin the exact repr (negative zero included) as well as the value.
    assert repr(result) == "-0.0"
    assert result == 0.0


def test_thermopro_rolling_boil_is_one_hundred_celsius():
    """b2=130 is the boiling fixed point: 130 * 1.07527 - 39.785 = 100.0001."""
    field = template.TemplateField(THERMOPRO_PROBE)
    assert field.decode(thermopro_packet(130)) == 100.0


def test_thermopro_fixed_points_in_fahrenheit():
    field = template.TemplateField(THERMOPRO_PROBE)
    assert field.decode(thermopro_packet(37), fahrenheit=True) == 32.0
    assert field.decode(thermopro_packet(130), fahrenheit=True) == 212.0


def test_thermopro_calibration_matches_the_shipped_template():
    """The constants above must not drift from templates/thermopro_tp960.json."""
    with open(TEMPLATES_DIR / "thermopro_tp960.json", encoding="utf-8") as f:
        shipped = json.load(f)
    probe = next(f for f in shipped["fields"] if f["key"] == "probe")
    assert probe["start_byte"] == 2
    assert probe["end_byte"] == 2
    assert probe["type"] == "uint8"
    assert probe["scale"] == 1.07527
    assert probe["offset"] == -39.785


# templates/temtop_p2.json — temperature is uint16_be over bytes 24-25, x0.1.
TEMTOP_TEMP = {
    "key": "temp",
    "start_byte": 24,
    "end_byte": 25,
    "type": "uint16_be",
    "scale": 0.1,
    "fahrenheit_convert": True,
}


def temtop_packet(temp_hi: int, temp_lo: int) -> bytes:
    """A 49-byte P2 packet with the temperature word placed at bytes 24-25."""
    packet = bytearray(49)
    packet[24] = temp_hi
    packet[25] = temp_lo
    return bytes(packet)


def test_temtop_temperature_reads_the_full_uint16_above_25_5c():
    """REGRESSION: the temperature word must be read as uint16_be, not one byte.

    Bytes 24-25 are 0x01 0x2C = 300, so the true reading is 30.0 C.

    An earlier decoder read only byte 25. That would give 0x2C = 44, x0.1 =
    4.4 C — a plausible-looking number that silently replaced every reading
    above 25.5 C (the point where the value no longer fits in one byte). This
    is the exact bug recorded in AGENT_GUIDE.md; this test exists to stop it
    recurring. If this ever asserts 4.4, the uint16 read has been broken again.
    """
    field = template.TemplateField(TEMTOP_TEMP)
    packet = temtop_packet(0x01, 0x2C)
    assert field.decode(packet) == 30.0

    # Demonstrate the wrong answer explicitly so the contrast is in the test,
    # not only in the comment above.
    single_byte = template.TemplateField(
        {"key": "temp", "start_byte": 25, "end_byte": 25, "type": "uint8", "scale": 0.1}
    )
    assert single_byte.decode(packet) == 4.4


def test_temtop_temperature_in_fahrenheit():
    """30.0 C -> 30.0 * 9 / 5 + 32 = 86.0 F."""
    field = template.TemplateField(TEMTOP_TEMP)
    assert field.decode(temtop_packet(0x01, 0x2C), fahrenheit=True) == 86.0


def test_temtop_temperature_below_the_single_byte_ceiling_still_agrees():
    """Below 25.5 C both readings coincide — which is why the bug hid so long."""
    field = template.TemplateField(TEMTOP_TEMP)
    # 0x00 0xC8 = 200 -> 20.0 C
    assert field.decode(temtop_packet(0x00, 0xC8)) == 20.0


def test_temtop_calibration_matches_the_shipped_template():
    with open(TEMPLATES_DIR / "temtop_p2.json", encoding="utf-8") as f:
        shipped = json.load(f)
    temp = next(f for f in shipped["fields"] if f["key"] == "temp")
    assert temp["start_byte"] == 24
    assert temp["end_byte"] == 25
    assert temp["type"] == "uint16_be"
    assert temp["scale"] == 0.1


# ---------------------------------------------------------------------------
# 4. Template.decode — per-field failures are swallowed into None
# ---------------------------------------------------------------------------


def short_packet_template() -> template.Template:
    return template.Template(
        {
            "name": "Short Packet Probe",
            "fields": [
                {"key": "head", "start_byte": 0, "end_byte": 1, "type": "uint16_be"},
                {"key": "far", "start_byte": 10, "end_byte": 10, "type": "uint8"},
                {"key": "straddle", "start_byte": 1, "end_byte": 2, "type": "uint16_be"},
            ],
        }
    )


def test_template_decode_missing_field_becomes_none_without_raising():
    """A field past the end of the packet yields None; siblings still decode."""
    result = short_packet_template().decode(b"\x01\x2c")
    assert result == {"head": 300, "far": None, "straddle": None}


def test_template_decode_preserves_the_templates_field_order():
    """Result key order follows the template's `fields` list, not the reverse.

    Every other assertion here compares dicts with `==`, which ignores order;
    this is the one place the iteration order of `Template.decode` is pinned.
    """
    result = short_packet_template().decode(b"\x01\x2c")
    assert list(result) == ["head", "far", "straddle"]


def test_template_decode_catches_index_error_and_struct_error():
    tmpl = short_packet_template()
    # "far" -> data[10:11] is empty -> raw[0] -> IndexError
    with pytest.raises(IndexError):
        tmpl.fields[1].decode(b"\x01\x2c")
    # "straddle" -> data[1:3] is one byte -> struct.unpack(">H", ...) -> struct.error
    with pytest.raises(struct.error):
        tmpl.fields[2].decode(b"\x01\x2c")
    # ...but Template.decode swallows both.
    assert tmpl.decode(b"\x01\x2c") == {"head": 300, "far": None, "straddle": None}


def test_template_decode_does_not_swallow_value_error():
    """Only IndexError/struct.error are caught — an unknown type still raises."""
    tmpl = template.Template(
        {"name": "Bad Type", "fields": [{"key": "x", "start_byte": 0, "type": "nope"}]}
    )
    with pytest.raises(ValueError):
        tmpl.decode(b"\x00\x01\x02")


def test_template_decode_passes_fahrenheit_through_to_fields():
    tmpl = template.Template({"name": "TP", "fields": [THERMOPRO_PROBE]})
    assert tmpl.decode(thermopro_packet(130)) == {"probe": 100.0}
    assert tmpl.decode(thermopro_packet(130), fahrenheit=True) == {"probe": 212.0}


def test_template_defaults():
    tmpl = template.Template({"name": "Bare"})
    assert tmpl.template_version == 1
    assert tmpl.manufacturer == ""
    assert tmpl.model == ""
    assert tmpl.ble_name_prefix == ""
    assert tmpl.packet_length is None
    assert tmpl.characteristic == ""
    assert tmpl.fields == []
    assert tmpl.decode(b"\x00") == {}


# ---------------------------------------------------------------------------
# 5. Round-trip: shipped templates survive Template(...).to_dict() unchanged
# ---------------------------------------------------------------------------

SHIPPED_IDS = ["govee_h5075", "temtop_m10", "temtop_p2", "thermopro_tp960"]


def test_shipped_template_ids_are_exactly_what_is_on_disk():
    on_disk = sorted(p.stem for p in TEMPLATES_DIR.glob("*.json") if p.name != "index.json")
    assert on_disk == SHIPPED_IDS


@pytest.mark.parametrize("template_id", SHIPPED_IDS)
def test_shipped_template_round_trips_to_dict(template_id):
    """to_dict() must reproduce the on-disk JSON exactly, as parsed data."""
    with open(TEMPLATES_DIR / f"{template_id}.json", encoding="utf-8") as f:
        on_disk = json.load(f)

    # Apply the constructor's defaults so an on-disk file that omitted one of
    # these still compares equal (none of the four do today).
    expected = dict(on_disk)
    expected.setdefault("template_version", 1)
    expected.setdefault("manufacturer", "")
    expected.setdefault("model", "")

    produced = template.Template(on_disk).to_dict()
    assert produced == expected
    # Key order is part of the contract, not just key membership.
    assert list(produced) == list(expected)
    for got, want in zip(produced["fields"], expected["fields"], strict=True):
        assert list(got) == list(want)


def test_shipped_optional_keys_present_and_absent_are_stable():
    """Pin exactly which optional keys each shipped template emits."""
    produced = {}
    for template_id in SHIPPED_IDS:
        with open(TEMPLATES_DIR / f"{template_id}.json", encoding="utf-8") as f:
            produced[template_id] = template.Template(json.load(f)).to_dict()

    # govee_h5075 has no packet_length; the others do.
    assert "packet_length" not in produced["govee_h5075"]
    assert produced["temtop_p2"]["packet_length"] == 49
    assert produced["temtop_m10"]["packet_length"] == 49
    assert produced["thermopro_tp960"]["packet_length"] == 8
    for template_id in SHIPPED_IDS:
        assert "ble_name_prefix" in produced[template_id]
        assert "characteristic" in produced[template_id]

    def field(template_id, key):
        return next(f for f in produced[template_id]["fields"] if f["key"] == key)

    # temtop_p2 "aqi": no scale (1), no offset, no unit.
    aqi = field("temtop_p2", "aqi")
    assert "scale" not in aqi and "offset" not in aqi and "unit" not in aqi
    assert "fahrenheit_convert" not in aqi
    # temtop_p2 "co2": unit but no scale.
    co2 = field("temtop_p2", "co2")
    assert "scale" not in co2 and co2["unit"] == "ppm"
    # thermopro "ambient": offset but no scale.
    ambient = field("thermopro_tp960", "ambient")
    assert "scale" not in ambient and ambient["offset"] == -32
    assert ambient["fahrenheit_convert"] is True
    # temtop_m10 "formaldehyde": no device_class.
    formaldehyde = field("temtop_m10", "formaldehyde")
    assert "device_class" not in formaldehyde and formaldehyde["state_class"] == "measurement"


def test_to_dict_omits_identity_scale_but_keeps_other_scales():
    assert "scale" not in make_field().to_dict()
    assert "scale" not in make_field(scale=1).to_dict()
    assert "scale" not in make_field(scale=1.0).to_dict()
    assert make_field(scale=0.1).to_dict()["scale"] == 0.1
    assert make_field(scale=0).to_dict()["scale"] == 0


def test_to_dict_omits_falsy_offset_and_emits_literal_true_for_fahrenheit():
    assert "offset" not in make_field(offset=0).to_dict()
    assert make_field(offset=-32).to_dict()["offset"] == -32
    # fahrenheit_convert is emitted as a literal True, never the raw attribute.
    field = make_field(fahrenheit_convert="yes please")
    assert field.to_dict()["fahrenheit_convert"] is True
    assert "fahrenheit_convert" not in make_field(fahrenheit_convert=False).to_dict()


def test_field_to_dict_key_order_is_fixed():
    field = make_field(
        name="Probe",
        end_byte=1,
        type="uint16_be",
        scale=0.1,
        offset=-1,
        unit="C",
        device_class="temperature",
        state_class="measurement",
        icon="mdi:thermometer",
        fahrenheit_convert=True,
    )
    assert list(field.to_dict()) == [
        "key",
        "name",
        "start_byte",
        "end_byte",
        "type",
        "scale",
        "offset",
        "unit",
        "device_class",
        "state_class",
        "icon",
        "fahrenheit_convert",
    ]


def test_template_to_dict_key_order_is_fixed_and_fields_come_last():
    tmpl = template.Template(
        {
            "name": "N",
            "manufacturer": "M",
            "model": "Mo",
            "ble_name_prefix": "P",
            "packet_length": 8,
            "characteristic": "C",
            "fields": [],
        }
    )
    assert list(tmpl.to_dict()) == [
        "template_version",
        "name",
        "manufacturer",
        "model",
        "ble_name_prefix",
        "packet_length",
        "characteristic",
        "fields",
    ]
    # packet_length is dropped when falsy, and fields still comes last.
    bare = template.Template({"name": "N"})
    assert list(bare.to_dict()) == ["template_version", "name", "manufacturer", "model", "fields"]
    assert list(template.Template({"name": "N", "packet_length": 0}).to_dict())[-1] == "fields"
    assert "packet_length" not in template.Template({"name": "N", "packet_length": 0}).to_dict()


# ---------------------------------------------------------------------------
# 6. load_bridge_config
# ---------------------------------------------------------------------------

DEFAULT_BRIDGE_CONFIG = {
    "mqtt": {
        "host": "localhost",
        "port": 1883,
        "username": "",
        "password": "",
        "topic_prefix": "ble",
        "discovery": True,
        "discovery_prefix": "homeassistant",
    },
    "bridge": {
        "scan_timeout": 10,
        "reconnect_delay": 5,
        "log_level": "INFO",
        "fahrenheit": False,
    },
}


def test_load_bridge_config_missing_file_returns_defaults(tmp_path):
    config = template.load_bridge_config(str(tmp_path / "nope.conf"))
    assert config == DEFAULT_BRIDGE_CONFIG
    # scan_timeout is unused by the bridge but is still settable, so it stays.
    assert config["bridge"]["scan_timeout"] == 10


def test_load_bridge_config_defaults_are_not_shared_between_calls(tmp_path):
    """A caller mutating the returned dict must not poison the next call."""
    missing = str(tmp_path / "nope.conf")
    first = template.load_bridge_config(missing)
    first["mqtt"]["host"] = "poisoned"
    first["bridge"]["extra"] = "poisoned"
    del first["bridge"]["log_level"]

    second = template.load_bridge_config(missing)
    assert second == DEFAULT_BRIDGE_CONFIG
    assert second["mqtt"] is not first["mqtt"]


def test_load_bridge_config_parses_and_coerces(tmp_path):
    path = tmp_path / "bridge.conf"
    path.write_text(
        "# a leading comment\n"
        "\n"
        "[mqtt]\n"
        "host = broker.lan\n"
        "port = 1884\n"
        "  discovery   =   no  \n"
        # Both comments below contain "=", so each would be split into a real
        # key/value pair if the `line.startswith("#")` skip ever went away.
        # The second is indented, so it also depends on the `line.strip()`
        # happening *before* the "#" test.
        "# host = poisoned.lan\n"
        "   # port = 9999\n"
        "username =\n"
        "topic_prefix = ble2\n"
        "\n"
        "[bridge]\n"
        "fahrenheit = YES\n"
        "log_level = DEBUG\n"
        "reconnect_delay = 7\n",
        encoding="utf-8",
    )
    config = template.load_bridge_config(str(path))

    assert config["mqtt"]["host"] == "broker.lan"
    assert config["mqtt"]["port"] == 1884
    assert isinstance(config["mqtt"]["port"], int)
    assert config["mqtt"]["discovery"] is False
    assert config["mqtt"]["username"] == ""
    assert config["mqtt"]["topic_prefix"] == "ble2"
    # untouched defaults survive
    assert config["mqtt"]["password"] == ""
    assert config["mqtt"]["discovery_prefix"] == "homeassistant"

    assert config["bridge"]["fahrenheit"] is True
    assert config["bridge"]["log_level"] == "DEBUG"
    assert config["bridge"]["reconnect_delay"] == 7
    assert config["bridge"]["scan_timeout"] == 10

    # Neither comment leaked a key: the section still holds exactly the seven
    # default keys, so no "# host" / "# port" pair was manufactured.
    assert set(config["mqtt"]) == set(DEFAULT_BRIDGE_CONFIG["mqtt"])


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("TRUE", True),
        ("True", True),
        ("yes", True),
        ("YES", True),
        ("false", False),
        ("FALSE", False),
        ("no", False),
        ("NO", False),
        ("0", 0),
        ("1", 1),
        ("1883", 1883),
        ("007", 7),
        ("-5", "-5"),  # isdigit() is False for a leading minus
        ("1.5", "1.5"),  # isdigit() is False for a dot
        ("1883abc", "1883abc"),
        ("", ""),
        ("on", "on"),  # only true/yes/false/no are booleans
        ("off", "off"),
        ("localhost", "localhost"),
    ],
)
def test_load_bridge_config_value_coercion(tmp_path, raw, expected):
    path = tmp_path / "bridge.conf"
    path.write_text(f"[mqtt]\nhost = {raw}\n", encoding="utf-8")
    value = template.load_bridge_config(str(path))["mqtt"]["host"]
    assert value == expected
    assert type(value) is type(expected)


def test_load_bridge_config_ignores_unknown_sections(tmp_path):
    """PINNED: a section that is not already in the defaults is dropped whole."""
    path = tmp_path / "bridge.conf"
    path.write_text(
        "[wibble]\nhost = ignored.lan\nanything = 1\n[mqtt]\nhost = kept.lan\n",
        encoding="utf-8",
    )
    config = template.load_bridge_config(str(path))
    assert "wibble" not in config
    assert config["mqtt"]["host"] == "kept.lan"


def test_load_bridge_config_ignores_pairs_before_any_section(tmp_path):
    path = tmp_path / "bridge.conf"
    path.write_text("host = orphan.lan\n[mqtt]\nport = 1885\n", encoding="utf-8")
    config = template.load_bridge_config(str(path))
    assert config["mqtt"]["host"] == "localhost"
    assert config["mqtt"]["port"] == 1885


def test_load_bridge_config_accepts_unknown_keys_in_known_sections(tmp_path):
    """PINNED: keys are not validated, only sections are."""
    path = tmp_path / "bridge.conf"
    path.write_text("[mqtt]\nnot_a_real_key = 42\n", encoding="utf-8")
    assert template.load_bridge_config(str(path))["mqtt"]["not_a_real_key"] == 42


def test_load_bridge_config_splits_on_the_first_equals_only(tmp_path):
    path = tmp_path / "bridge.conf"
    path.write_text("[mqtt]\npassword = a=b=c\n", encoding="utf-8")
    assert template.load_bridge_config(str(path))["mqtt"]["password"] == "a=b=c"


def test_load_bridge_config_ignores_lines_without_equals(tmp_path):
    path = tmp_path / "bridge.conf"
    path.write_text("[mqtt]\ngarbage line\nhost = ok.lan\n", encoding="utf-8")
    config = template.load_bridge_config(str(path))
    assert config["mqtt"]["host"] == "ok.lan"
    assert "garbage line" not in config["mqtt"]


def test_load_bridge_config_empty_section_name_swallows_following_pairs(tmp_path):
    """PINNED: `[]` sets section to "", which is falsy, so its pairs are dropped."""
    path = tmp_path / "bridge.conf"
    path.write_text("[]\nhost = dropped.lan\n", encoding="utf-8")
    assert template.load_bridge_config(str(path))["mqtt"]["host"] == "localhost"


def test_load_bridge_config_header_must_be_bracketed_at_both_ends(tmp_path):
    """A header needs the closing `]`; an unterminated `[abc` is not a section.

    It is not a pair either (no "="), so it vanishes and the section in force
    does not change — the pair after it still lands in [mqtt].
    """
    path = tmp_path / "bridge.conf"
    path.write_text(
        "[mqtt]\nhost = good.lan\n[abc\nport = 1884\n",
        encoding="utf-8",
    )
    config = template.load_bridge_config(str(path))
    assert "abc" not in config
    assert "ab" not in config  # nor the name a `[abc` header would have carried
    assert config["mqtt"]["host"] == "good.lan"
    assert config["mqtt"]["port"] == 1884


def test_load_bridge_config_indented_section_header_still_opens_a_section(tmp_path):
    """Lines are stripped before the header test, so `   [bridge]` is a header."""
    path = tmp_path / "bridge.conf"
    path.write_text(
        "[mqtt]\nport = 1884\n   [bridge]\nlog_level = DEBUG\n",
        encoding="utf-8",
    )
    config = template.load_bridge_config(str(path))
    assert config["bridge"]["log_level"] == "DEBUG"
    assert config["mqtt"]["port"] == 1884
    # ...and the pair did not stay behind in the previous section
    assert "log_level" not in config["mqtt"]


# ---------------------------------------------------------------------------
# 7. load_devices_config
# ---------------------------------------------------------------------------


def test_load_devices_config_missing_file_returns_empty(tmp_path):
    assert template.load_devices_config(str(tmp_path / "nope.conf")) == {}


def test_load_devices_config_multiple_devices_and_comments(tmp_path):
    path = tmp_path / "devices.conf"
    path.write_text(
        "# devices\n"
        "\n"
        "[device:DC:8E:95:34:3B:02]\n"
        "template = thermopro_tp960\n"
        "name     = ThermoPro Probe\n"
        "location = Kitchen\n"
        "\n"
        "   # a comment in the middle\n"
        "[device:AA:BB:CC:DD:EE:FF]\n"
        "template = govee_h5075\n"
        "name = Living Room\n",
        encoding="utf-8",
    )
    assert template.load_devices_config(str(path)) == {
        "DC:8E:95:34:3B:02": {
            "template": "thermopro_tp960",
            "name": "ThermoPro Probe",
            "location": "Kitchen",
        },
        "AA:BB:CC:DD:EE:FF": {
            "template": "govee_h5075",
            "name": "Living Room",
        },
    }


def test_load_devices_config_ignores_pairs_before_any_header(tmp_path):
    """PINNED: a stray `key = value` before the first [device:...] is dropped."""
    path = tmp_path / "devices.conf"
    path.write_text(
        "orphan = 1\ntemplate = nowhere\n[device:AA:BB]\nname = Real\n",
        encoding="utf-8",
    )
    assert template.load_devices_config(str(path)) == {"AA:BB": {"name": "Real"}}


def test_load_devices_config_empty_device_block(tmp_path):
    path = tmp_path / "devices.conf"
    path.write_text("[device:AA:BB]\n[device:CC:DD]\nname = Second\n", encoding="utf-8")
    assert template.load_devices_config(str(path)) == {"AA:BB": {}, "CC:DD": {"name": "Second"}}


def test_load_devices_config_repeated_header_resets_the_block(tmp_path):
    """PINNED: a repeated [device:...] wipes what was collected for that MAC."""
    path = tmp_path / "devices.conf"
    path.write_text(
        "[device:AA:BB]\nname = First\n[device:AA:BB]\nlocation = Hall\n",
        encoding="utf-8",
    )
    assert template.load_devices_config(str(path)) == {"AA:BB": {"location": "Hall"}}


def test_load_devices_config_non_device_bracket_line_is_not_a_header(tmp_path):
    """PINNED: only `[device:` opens a block; `[mqtt]` has no `=` so it vanishes."""
    path = tmp_path / "devices.conf"
    path.write_text("[device:AA:BB]\nname = Real\n[mqtt]\nhost = x\n", encoding="utf-8")
    assert template.load_devices_config(str(path)) == {
        "AA:BB": {"name": "Real", "host": "x"},
    }


def test_load_devices_config_bracket_line_containing_equals_becomes_a_pair(tmp_path):
    """PINNED quirk: `[foo=bar]` is not a device header, so it falls through
    to the pair branch and is stored with the brackets attached."""
    path = tmp_path / "devices.conf"
    path.write_text("[device:AA:BB]\n[foo=bar]\n", encoding="utf-8")
    assert template.load_devices_config(str(path)) == {"AA:BB": {"[foo": "bar]"}}


def test_load_devices_config_splits_on_the_first_equals_only(tmp_path):
    path = tmp_path / "devices.conf"
    path.write_text("[device:AA:BB]\nname = a=b\n", encoding="utf-8")
    assert template.load_devices_config(str(path)) == {"AA:BB": {"name": "a=b"}}


def test_load_devices_config_header_bracketing_and_indentation(tmp_path):
    """`[device:CC` is unterminated so it is not a header; `   [device:DD:EE]` is.

    The first has no "=" either, so it vanishes and `location` stays with the
    block that was already open. The second only works because the line is
    stripped before the `[device:` test.
    """
    path = tmp_path / "devices.conf"
    path.write_text(
        "[device:AA:BB]\n"
        "name = Real\n"
        "[device:CC\n"
        "location = Hall\n"
        "   [device:DD:EE]\n"
        "name = Indented\n",
        encoding="utf-8",
    )
    assert template.load_devices_config(str(path)) == {
        "AA:BB": {"name": "Real", "location": "Hall"},
        "DD:EE": {"name": "Indented"},
    }


def test_load_devices_config_empty_mac_header_swallows_its_pairs(tmp_path):
    """PINNED: `[device:]` opens a block keyed "" — and then loses every pair.

    The block itself is created, so it shows up as an empty dict, but the
    `elif current_mac:` guard is falsy for "" so the pairs that follow are
    dropped instead of being stored under it. A guard of `is not None` would
    keep them; today's does not.
    """
    path = tmp_path / "devices.conf"
    path.write_text(
        "[device:]\ntemplate = dropped\n[device:AA:BB]\nname = Real\n",
        encoding="utf-8",
    )
    assert template.load_devices_config(str(path)) == {
        "": {},
        "AA:BB": {"name": "Real"},
    }


def test_load_devices_config_comment_lines_never_become_pairs(tmp_path):
    """Both comments below contain "=" and would be stored as pairs if the
    `#` skip (or the `line.strip()` that precedes it) were removed."""
    path = tmp_path / "devices.conf"
    path.write_text(
        "[device:AA:BB]\n"
        "# template = poisoned\n"
        "   # location = also poisoned\n"
        "name = Real\n",
        encoding="utf-8",
    )
    assert template.load_devices_config(str(path)) == {"AA:BB": {"name": "Real"}}


def test_load_devices_config_reads_non_ascii_values_as_utf8(tmp_path):
    """The config reader decodes UTF-8, not the platform default (cp1252 here).

    Written as raw bytes so the fixture's encoding is unambiguous; read back
    through the module's own open(), which must not fall back to the locale.
    """
    path = tmp_path / "devices.conf"
    path.write_bytes("[device:AA:BB]\nname = Küche µg/m³\n".encode())
    assert template.load_devices_config(str(path)) == {
        "AA:BB": {"name": "Küche µg/m³"}
    }


def test_load_devices_config_matches_the_shipped_example():
    """The committed devices.conf.example must still parse into something sane."""
    example = REPO_ROOT / "devices.conf.example"
    if not example.exists():
        pytest.skip("devices.conf.example not present")
    devices = template.load_devices_config(str(example))
    assert devices, "the example should define at least one device"
    for mac, settings in devices.items():
        assert ":" in mac
        assert "template" in settings


# ---------------------------------------------------------------------------
# 8. TemplateRegistry
# ---------------------------------------------------------------------------

MINIMAL_TEMPLATE = {
    "template_version": 1,
    "name": "Widget",
    "manufacturer": "Acme",
    "model": "W1",
    "fields": [{"key": "a", "name": "A", "start_byte": 0, "end_byte": 0, "type": "uint8"}],
}


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_registry_creates_a_missing_template_dir(tmp_path):
    target = tmp_path / "does_not_exist"
    registry = template.TemplateRegistry(str(target))
    assert target.is_dir()
    assert registry.templates == {}
    assert registry.list_templates() == []


def test_registry_load_all_skips_index_json(tmp_path):
    """index.json is skipped by name, not merely rejected for being malformed.

    The fixture below is deliberately *also* a valid template — top-level
    "name" and "fields" — so that dropping the `path.name == "index.json"`
    guard would produce a template called "index" rather than a KeyError that
    the warning path would have swallowed anyway.
    """
    index = {"name": "Index", "fields": [], "widget": {"confirmed": True}}
    write_json(tmp_path / "index.json", index)
    write_json(tmp_path / "widget.json", MINIMAL_TEMPLATE)
    registry = template.TemplateRegistry(str(tmp_path))
    assert template.Template(index).name == "Index"  # it really would load
    assert set(registry.templates) == {"widget"}
    assert "index" not in registry.templates
    assert registry.index == index


def test_registry_warns_about_malformed_json_instead_of_raising(tmp_path, capsys):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    write_json(tmp_path / "widget.json", MINIMAL_TEMPLATE)
    registry = template.TemplateRegistry(str(tmp_path))
    assert set(registry.templates) == {"widget"}
    out = capsys.readouterr().out
    assert "Warning: failed to load template" in out
    assert "broken.json" in out


def test_registry_warns_about_a_template_missing_a_required_key(tmp_path, capsys):
    """A KeyError (no "name", or a field with no "key") is caught the same way."""
    write_json(tmp_path / "nameless.json", {"manufacturer": "Acme"})
    write_json(tmp_path / "keyless.json", {"name": "X", "fields": [{"start_byte": 0}]})
    write_json(tmp_path / "widget.json", MINIMAL_TEMPLATE)
    registry = template.TemplateRegistry(str(tmp_path))
    assert set(registry.templates) == {"widget"}
    out = capsys.readouterr().out
    assert "nameless.json" in out
    assert "keyless.json" in out


def test_registry_warns_about_a_non_utf8_template_instead_of_raising(tmp_path, capsys):
    """REGRESSION: one file that is not UTF-8 must not take out the registry.

    The loader opens template files with an explicit ``encoding="utf-8"``, so a
    file written in cp1252 raises UnicodeDecodeError rather than JSONDecodeError.
    That has to be caught per-file like every other bad template: warn, skip it,
    keep loading the rest. Before the caught tuple was widened, this exception
    escaped ``load_all`` and every other template was lost with it.
    """
    (tmp_path / "cp1252.json").write_bytes('{"name": "Café", "fields": []}'.encode("cp1252"))
    write_json(tmp_path / "widget.json", MINIMAL_TEMPLATE)
    registry = template.TemplateRegistry(str(tmp_path))
    assert set(registry.templates) == {"widget"}
    out = capsys.readouterr().out
    assert "Warning: failed to load template" in out
    assert "cp1252.json" in out


def test_registry_reads_shipped_unicode_units_as_utf8():
    """The shipped units are non-ASCII, so the loader's own open() is pinned here.

    The round-trip tests open the JSON themselves with an explicit encoding, so
    they never exercise ``load_all``'s open(); and ``save_template`` writes pure
    ASCII because json.dump defaults to ensure_ascii=True. This is the one test
    that would break if the reader fell back to the platform default (cp1252 on
    this machine, which would decode "µg/m³" as "Âµg/mÂ³").
    """
    registry = template.TemplateRegistry(str(TEMPLATES_DIR))
    units = {f.key: f.unit for f in registry.get("temtop_p2").fields}
    assert units["pm25"] == "µg/m³"
    assert units["pm10"] == "µg/m³"
    assert units["temp"] == "°C"
    assert units["humidity"] == "%"
    assert units["co2"] == "ppm"


def test_registry_reads_a_non_ascii_index_as_utf8(tmp_path):
    """index.json is opened separately from the templates; pin its encoding too."""
    description = "Café µg/m³"
    (tmp_path / "index.json").write_bytes(
        json.dumps({"widget": {"description": description}}, ensure_ascii=False).encode()
    )
    write_json(tmp_path / "widget.json", MINIMAL_TEMPLATE)
    registry = template.TemplateRegistry(str(tmp_path))
    assert registry.index["widget"]["description"] == description
    (entry,) = registry.list_templates()
    assert entry["description"] == description


def test_registry_get_returns_none_for_an_unknown_id(tmp_path):
    write_json(tmp_path / "widget.json", MINIMAL_TEMPLATE)
    registry = template.TemplateRegistry(str(tmp_path))
    assert registry.get("widget") is not None
    assert registry.get("nope") is None
    assert registry.get("index") is None


def test_registry_list_templates_merges_index_over_the_base_entry(tmp_path):
    write_json(
        tmp_path / "index.json",
        {
            "widget": {
                "name": "Widget (from index)",
                "description": "Only in the index",
                "confirmed": True,
            }
        },
    )
    write_json(tmp_path / "widget.json", MINIMAL_TEMPLATE)
    registry = template.TemplateRegistry(str(tmp_path))
    (entry,) = registry.list_templates()
    assert entry == {
        "id": "widget",
        "name": "Widget (from index)",  # index wins over the template's own name
        "manufacturer": "Acme",
        "model": "W1",
        "field_count": 1,
        "description": "Only in the index",
        "confirmed": True,
    }


def test_registry_list_templates_without_an_index_entry(tmp_path):
    write_json(tmp_path / "widget.json", MINIMAL_TEMPLATE)
    registry = template.TemplateRegistry(str(tmp_path))
    assert registry.list_templates() == [
        {
            "id": "widget",
            "name": "Widget",
            "manufacturer": "Acme",
            "model": "W1",
            "field_count": 1,
        }
    ]


def test_registry_save_template_writes_a_file_that_reloads_equal(tmp_path):
    registry = template.TemplateRegistry(str(tmp_path))
    tmpl = template.Template(MINIMAL_TEMPLATE)
    registry.save_template("widget", tmpl)

    written = tmp_path / "widget.json"
    assert written.exists()
    assert json.loads(written.read_text(encoding="utf-8")) == tmpl.to_dict()
    assert registry.get("widget") is tmpl

    reloaded = template.TemplateRegistry(str(tmp_path))
    assert reloaded.get("widget").to_dict() == tmpl.to_dict()


def test_registry_save_template_creates_the_dir_and_round_trips_a_real_template(tmp_path):
    with open(TEMPLATES_DIR / "temtop_p2.json", encoding="utf-8") as f:
        shipped = json.load(f)
    target = tmp_path / "fresh"
    registry = template.TemplateRegistry(str(target))
    registry.save_template("temtop_p2", template.Template(shipped))
    reloaded = template.TemplateRegistry(str(target))
    assert reloaded.get("temtop_p2").to_dict() == shipped


def test_registry_load_all_is_idempotent(tmp_path):
    write_json(tmp_path / "widget.json", MINIMAL_TEMPLATE)
    registry = template.TemplateRegistry(str(tmp_path))
    before = registry.list_templates()
    registry.load_all()
    assert registry.list_templates() == before


def test_registry_load_all_forgets_a_template_deleted_from_disk(tmp_path):
    """load_all() is a reload, not a merge: it clears before it re-globs.

    Idempotence alone cannot show this — nothing is ever removed there — so a
    file is deleted between the two loads and the registry has to notice.
    """
    write_json(tmp_path / "widget.json", MINIMAL_TEMPLATE)
    write_json(tmp_path / "gadget.json", {**MINIMAL_TEMPLATE, "name": "Gadget"})
    registry = template.TemplateRegistry(str(tmp_path))
    assert set(registry.templates) == {"widget", "gadget"}

    (tmp_path / "gadget.json").unlink()
    registry.load_all()
    assert set(registry.templates) == {"widget"}
    assert registry.get("gadget") is None
    assert [e["id"] for e in registry.list_templates()] == ["widget"]


# govee_h5075 is deliberately NOT confirmed: its `characteristic` is the Temtop
# vendor UUID, byte-identical to temtop_p2's, and the H5075 is a broadcast device
# that never exposes that notify characteristic. It was flagged unverified in the
# 2026-09-09 audit and cannot be resolved without the hardware. Keep it listed
# here rather than skipping it, so that confirming it becomes a deliberate edit.
UNCONFIRMED_IDS = {"govee_h5075"}


def test_registry_reads_the_repos_own_templates():
    registry = template.TemplateRegistry(str(TEMPLATES_DIR))
    assert sorted(registry.templates) == SHIPPED_IDS
    for entry in registry.list_templates():
        # every shipped template is described in index.json
        assert entry["confirmed"] is (entry["id"] not in UNCONFIRMED_IDS)
        assert entry["description"]


# ---------------------------------------------------------------------------
# 9. PINNED: the parked short-read behaviour
# ---------------------------------------------------------------------------


def test_short_read_of_an_oversized_byte_range_is_pinned_not_desired():
    """PINNED deliberately (2026-09-09 audit) — this is NOT desired behaviour.

    A uint16_be field declared start_byte=0, end_byte=3 slices four bytes but
    ``struct.unpack(">H", raw[:2])`` only consumes the first two. The extra
    bytes are silently discarded: no error, no warning, no truncation notice.

    Adding a check that ``end_byte - start_byte + 1`` matches the type width
    was deliberately parked, so today's behaviour must survive. If this test
    starts failing because a length check was added, that is a real change of
    contract — update the test with the change, do not weaken it here.
    """
    field = make_field(type="uint16_be", start_byte=0, end_byte=3)
    assert field.decode(b"\x01\x2c\xff\xff") == 300
    # The value is completely insensitive to the bytes that were over-sliced.
    assert field.decode(b"\x01\x2c\x00\x00") == 300


def test_short_read_also_applies_to_the_narrow_types():
    """Same silent discard for uint8/int8/float32, in the other direction."""
    # uint8 over 4 bytes reads only byte 0
    assert make_field(type="uint8", end_byte=3).decode(b"\x2a\xff\xff\xff") == 42
    # int8 over 4 bytes reads only byte 0
    assert make_field(type="int8", end_byte=3).decode(b"\xff\x00\x00\x00") == -1
    # float32_be over 8 bytes reads only the first 4
    assert make_field(type="float32_be", end_byte=7).decode(b"\x3f\xc0\x00\x00ZZZZ") == 1.5
    # bool over 4 bytes reads only byte 0
    assert make_field(type="bool", end_byte=3).decode(b"\x00\xff\xff\xff") is False


def test_undersized_byte_range_is_a_struct_error_not_a_short_value():
    """The inverse: too FEW bytes does raise, and Template.decode maps it to None."""
    field = make_field(type="uint32_be", end_byte=3)
    with pytest.raises(struct.error):
        field.decode(b"\x01\x02")
