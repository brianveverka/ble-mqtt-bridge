"""Tests for the two Flask front ends: ``bridge_ui`` and ``decoder_standalone``.

Three things are covered here.

*The REST surface of the bridge UI* -- all eight endpoints, happy path and error
path, plus a device round trip that goes through the real ``_save_devices`` /
``load_devices_config`` pair rather than a stub, because the on-disk format is
the actual contract with the bridge process.

*The shared markup* -- ``bridge_ui`` and ``decoder_standalone`` used to carry two
byte-identical copies of the decoder wizard, and an edit to one silently
diverged from the other. Both now render it from ``ui_fragments``; the tests
below assert the same constants appear in both rendered pages, so a future
"quick fix" applied to one page cannot quietly stop applying to the other.

*The standalone wizard's sample data* -- "Load Sample Data" is the first thing a
newcomer clicks, so its packet layout is a teaching document. The tests parse
the generator straight out of ``decoder_standalone.py`` and check every offset
against the real ``templates/temtop_p2.json``, then decode the result through
the real ``TemplateField``. In particular the temperature samples must climb
past 25.5 C, which is where the decoder scar recorded in ``AGENT_GUIDE.md``
(reading one byte of a ``uint16_be``) starts producing nonsense.
"""

import ast
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import bridge_ui
import decoder_standalone
import ui_fragments
from template import Template, TemplateField, load_devices_config

REPO_ROOT = Path(__file__).resolve().parent.parent
DECODER_SRC = (REPO_ROOT / "decoder_standalone.py").read_text(encoding="utf-8")
APP_JS = (REPO_ROOT / "static" / "app.js").read_text(encoding="utf-8")
TEMTOP_P2 = json.loads((REPO_ROOT / "templates" / "temtop_p2.json").read_text(encoding="utf-8"))
TEMTOP_FIELDS = {f["key"]: f for f in TEMTOP_P2["fields"]}

_NODE = shutil.which("node")
HAVE_BLEAK = importlib.util.find_spec("bleak") is not None
needs_no_bleak = pytest.mark.skipif(
    HAVE_BLEAK, reason="the 501 path only exists while bleak is absent"
)


# ── fixtures ──


@pytest.fixture
def devices_file(tmp_path, monkeypatch):
    """Point bridge_ui at a throwaway devices.conf so no test writes into the repo."""
    path = tmp_path / "devices.conf"
    monkeypatch.setattr(bridge_ui, "DEVICES_FILE", str(path))
    return path


@pytest.fixture
def client(devices_file):  # noqa: ARG001 - requested for its monkeypatch side effect
    bridge_ui.app.config["TESTING"] = True
    return bridge_ui.app.test_client()


@pytest.fixture
def decoder_client():
    decoder_standalone.app.config["TESTING"] = True
    return decoder_standalone.app.test_client()


@pytest.fixture
def tmp_registry(tmp_path):
    """Redirect template writes to tmp_path, then put the real registry back.

    ``bridge_ui.registry`` is a module-level singleton, so the teardown has to
    reload it as well as restore the directory -- monkeypatch would restore the
    attribute but leave the in-memory templates polluted.
    """
    target = tmp_path / "templates"
    target.mkdir()
    original = bridge_ui.registry.template_dir
    bridge_ui.registry.template_dir = target
    yield target
    bridge_ui.registry.template_dir = original
    bridge_ui.registry.load_all()


# ── GET / ──


def test_index_renders_the_wizard(client):
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    for marker in ('class="wizard-panel"', 'id="packet-view"', 'id="field-table-body"'):
        assert marker in html


def test_index_no_longer_renders_the_fake_mqtt_status(client):
    """The dot was hard-coded offline whatever the bridge was doing (see change log).

    HEAD rendered ``<span class="status-dot offline">`` and, beside it, the
    literal text ``MQTT Disconnected``. Asserting on the rendered words as well
    as the class means a reinstated fake status cannot slip back in under a
    different class name.
    """
    html = client.get("/").get_data(as_text=True)
    assert "status-dot" not in html
    assert "MQTT Disconnected" not in html
    assert "MQTT Connected" not in html


# ── /api/devices ──


def test_get_devices_empty(client):
    resp = client.get("/api/devices")
    assert resp.status_code == 200
    assert resp.get_json() == {"devices": {}}


def test_add_device_without_mac_is_400(client):
    resp = client.post("/api/devices", json={"name": "nameless"})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "MAC required"}


def test_device_round_trip(client, devices_file):
    add = client.post(
        "/api/devices",
        json={
            "mac": "aa:bb:cc:dd:ee:ff",
            "name": "Living Room",
            "location": "Lounge",
            "template": "temtop_p2",
        },
    )
    assert add.status_code == 200
    assert add.get_json() == {"ok": True}

    listed = client.get("/api/devices").get_json()["devices"]
    assert listed == {
        "AA:BB:CC:DD:EE:FF": {
            "template": "temtop_p2",
            "name": "Living Room",
            "location": "Lounge",
        }
    }

    updated = client.put("/api/devices/aa:bb:cc:dd:ee:ff", json={"name": "Hallway"})
    assert updated.status_code == 200
    after = client.get("/api/devices").get_json()["devices"]
    assert after["AA:BB:CC:DD:EE:FF"]["name"] == "Hallway"
    assert after["AA:BB:CC:DD:EE:FF"]["template"] == "temtop_p2"

    removed = client.delete("/api/devices/AA:BB:CC:DD:EE:FF")
    assert removed.status_code == 200
    assert client.get("/api/devices").get_json() == {"devices": {}}
    assert devices_file.exists()


def test_delete_normalises_the_mac_like_post_and_put(client):
    """DELETE upper-cases too; without it a lowercase URL is a silent no-op."""
    client.post("/api/devices", json={"mac": "AA:BB:CC:DD:EE:FF", "name": "One"})
    resp = client.delete("/api/devices/aa:bb:cc:dd:ee:ff")
    assert resp.status_code == 200
    assert client.get("/api/devices").get_json() == {"devices": {}}


def test_non_ascii_device_fields_survive_the_disk_round_trip(client, devices_file):
    """devices.conf is written by _save_devices and read back as UTF-8.

    On a cp1252 box an unqualified ``open(..., "w")`` writes cp1252, which
    ``load_devices_config`` (UTF-8) then refuses -- so every later request 500s.
    The byte assertion pins the codec itself rather than the platform default.
    """
    name = "Sensor \u00b5g/m\u00b3"
    location = "K\u00fcche \u2014 \u4e0a\u6d77"
    resp = client.post(
        "/api/devices",
        json={"mac": "aa:bb:cc:dd:ee:ff", "name": name, "location": location},
    )
    assert resp.status_code == 200

    written = devices_file.read_bytes()
    assert name.encode("utf-8") in written
    assert location.encode("utf-8") in written

    reloaded = load_devices_config(str(devices_file))
    assert reloaded["AA:BB:CC:DD:EE:FF"]["name"] == name
    assert reloaded["AA:BB:CC:DD:EE:FF"]["location"] == location

    listed = client.get("/api/devices").get_json()["devices"]
    assert listed["AA:BB:CC:DD:EE:FF"]["name"] == name
    assert listed["AA:BB:CC:DD:EE:FF"]["location"] == location


def test_update_unknown_device_is_404(client):
    resp = client.put("/api/devices/11:22:33:44:55:66", json={"name": "ghost"})
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not found"}


def test_delete_unknown_device_is_a_no_op(client):
    resp = client.delete("/api/devices/11:22:33:44:55:66")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


def test_save_devices_output_reparses_to_the_same_dict(devices_file):
    devices = {
        "AA:BB:CC:DD:EE:FF": {"template": "temtop_p2", "name": "One", "location": "Lounge"},
        "11:22:33:44:55:66": {"template": "govee_h5075", "name": "Two", "location": "Shed"},
    }
    bridge_ui._save_devices(devices)
    assert load_devices_config(str(devices_file)) == devices


def test_save_devices_writes_the_documented_format(devices_file):
    bridge_ui._save_devices({"AA:BB:CC:DD:EE:FF": {"template": "temtop_p2", "name": "One"}})
    assert devices_file.read_text(encoding="utf-8").splitlines() == [
        "[device:AA:BB:CC:DD:EE:FF]",
        "template = temtop_p2",
        "name = One",
        "",
    ]


# ── /api/templates ──


def test_get_templates_returns_a_list(client):
    resp = client.get("/api/templates")
    assert resp.status_code == 200
    assert isinstance(resp.get_json()["templates"], list)


def test_save_template_without_id_is_400(client):
    resp = client.post("/api/templates", json={"template": {"name": "X"}})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "id required"}


def test_save_then_get_template(client, tmp_registry):
    body = {
        "id": "w3_probe",
        "template": {
            "template_version": 1,
            "name": "W3 Probe",
            "fields": [
                {"key": "x", "name": "X", "start_byte": 0, "end_byte": 0, "type": "uint8"}
            ],
        },
    }
    saved = client.post("/api/templates", json=body)
    assert saved.status_code == 200
    assert saved.get_json() == {"ok": True}
    assert (tmp_registry / "w3_probe.json").exists()

    fetched = client.get("/api/templates/w3_probe")
    assert fetched.status_code == 200
    assert fetched.get_json()["name"] == "W3 Probe"

    ids = [t["id"] for t in client.get("/api/templates").get_json()["templates"]]
    assert "w3_probe" in ids


def test_get_unknown_template_is_404(client):
    resp = client.get("/api/templates/does_not_exist")
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not found"}


def test_delete_unknown_template_is_a_no_op(client):
    # A real id would delete a real file out of templates/, so this deliberately
    # exercises the miss branch only.
    resp = client.delete("/api/templates/does_not_exist")
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}


# ── /api/scan and /api/capture ──


@needs_no_bleak
def test_scan_without_bleak_is_501(client):
    resp = client.post("/api/scan", json={"timeout": 1})
    assert resp.status_code == 501
    assert resp.get_json() == {"error": "bleak not installed", "devices": []}


def test_capture_without_address_is_400(client):
    resp = client.post("/api/capture", json={"characteristic": "0000-abcd"})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "address and characteristic required"}


def test_capture_without_characteristic_is_400(client):
    resp = client.post("/api/capture", json={"address": "AA:BB:CC:DD:EE:FF"})
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "address and characteristic required"}


@needs_no_bleak
def test_capture_without_bleak_is_501(client):
    resp = client.post(
        "/api/capture",
        json={
            "address": "AA:BB:CC:DD:EE:FF",
            "characteristic": "00010203-0405-0607-0809-0a0b0c0d2b10",
            "duration": 1,
            "max_packets": 1,
        },
    )
    assert resp.status_code == 501
    assert resp.get_json() == {"error": "bleak not installed"}


# ── the duplication regression ──


SHARED_FRAGMENTS = ["HEAD_START", "HEAD_END", "WIZARD_PANELS", "SCRIPTS_START", "PAGE_END"]


@pytest.mark.parametrize("name", SHARED_FRAGMENTS)
def test_both_pages_render_the_same_shared_fragment(client, decoder_client, name):
    """If either page stops matching, the two front ends have diverged again."""
    fragment = getattr(ui_fragments, name).rstrip("\n")
    assert fragment, name
    assert fragment in client.get("/").get_data(as_text=True), name
    assert fragment in decoder_client.get("/").get_data(as_text=True), name


# The test above compares a page against the very constant it was rendered from,
# so editing the constant moves both sides at once. Everything below asserts on
# literal markup instead, which is what actually catches a fragment being
# hollowed out.

ASSET_TAGS = [
    '<link rel="stylesheet" href="/static/style.css">',
    '<script src="/static/app.js"></script>',
]


@pytest.mark.parametrize("tag", ASSET_TAGS)
def test_both_pages_carry_the_literal_asset_tags(client, decoder_client, tag):
    """Lose the stylesheet and both pages render unstyled; lose the script tag

    and every onclick in the wizard becomes a ReferenceError -- the whole UI is
    dead, and no fragment-versus-fragment comparison would notice either.
    """
    assert tag in client.get("/").get_data(as_text=True)
    assert tag in decoder_client.get("/").get_data(as_text=True)


def test_wizard_fragment_is_substantial():
    """Guards against the fragment being hollowed out to make the test above pass.

    Headings and button labels, not a line count: a line count passes happily
    while the panel it was counting is replaced with blanks.
    """
    panels = ui_fragments.WIZARD_PANELS
    for heading in (
        "<h3>Packet View</h3>",
        "<h3>Byte Diff (changes highlighted)</h3>",
        "<h3>Field Definitions</h3>",
        "<h3>Test Decode Results</h3>",
        "<h3>Generate Template</h3>",
    ):
        assert heading in panels, heading
    for button in (
        'onclick="prevPacket()">\u25c0 Prev</button>',
        'onclick="nextPacket()">Next \u25b6</button>',
        'onclick="addField()">+ Add Field</button>',
        'onclick="testDecode()">Test Decode</button>',
        'onclick="generateTemplate()">Download Template JSON</button>',
    ):
        assert button in panels, button
    for element_id in ("packet-counter", "packet-view", "diff-view", "field-table-body",
                       "test-results", "tpl-id", "tpl-name", "tpl-manufacturer",
                       "tpl-model", "tpl-characteristic"):
        assert f'id="{element_id}"' in panels


def test_ui_fragments_imports_nothing():
    """It must stay trivially importable -- no Flask, no template, no anything."""
    tree = ast.parse((REPO_ROOT / "ui_fragments.py").read_text(encoding="utf-8"))
    found = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert found == []


# ── the dead /static route ──


def test_decoder_registers_no_static_route_of_its_own():
    rules = {r.rule: r.endpoint for r in decoder_standalone.app.url_map.iter_rules()}
    # Flask's own static handler, from Flask(__name__, static_folder="static").
    assert rules.get("/static/<path:filename>") == "static"


def test_decoder_does_not_reference_send_from_directory():
    """It was never imported, so the old route was a NameError waiting to be hit."""
    assert "send_from_directory" not in DECODER_SRC


def test_decoder_still_serves_api_templates(decoder_client):
    """app.js fetches this on DOMContentLoaded; removing it turns a 200 into a 404."""
    resp = decoder_client.get("/api/templates")
    assert resp.status_code == 200
    assert resp.get_json() == {"templates": []}


# ── the sample-data regression ──

# Offsets and value expressions are scraped out of the module source; the
# big-endian split itself is *reimplemented* in _sample_packets() below, so
# these assertions do not pin the byte order the page ships. That is done
# separately by test_put_u16_be_writes_the_high_byte_first (on the two shipped
# lines) and, where node is installed, by
# test_sample_packets_match_the_shipped_javascript (by running them). The JS
# arithmetic ("150 + i * 20") is valid Python, which is what makes the
# reimplementation cheap enough to be worth cross-checking.
#
# The value group has to tolerate parentheses -- a Math.min(...) argument used
# to slip past the pattern entirely, and _sample_writes() only ever asserted
# that *something* matched, so a dropped field would have passed unnoticed.
_U16_WRITE = re.compile(r"putU16BE\(bytes, (\d+), (.+)\);")
_U8_WRITE = re.compile(r"^\s*bytes\[(\d+)\] = ([^;]+);", re.MULTILINE)
_OFFSET_COMMENT = re.compile(r"//\s+(\w+)\s+bytes (\d+)-(\d+)\s+(\w+)\s")
_PACKET_LENGTH = re.compile(r"const SAMPLE_PACKET_LENGTH = (\d+);")
_CHARACTERISTIC = re.compile(r"const SAMPLE_CHAR = '([0-9a-f-]+)';")


def _sample_writes():
    """[(start_byte, width, js_expression)] for every byte the generator writes."""
    writes = [(int(at), 2, expr) for at, expr in _U16_WRITE.findall(DECODER_SRC)]
    writes += [(int(at), 1, expr) for at, expr in _U8_WRITE.findall(DECODER_SRC)]
    assert writes, "the sample-data generator was not found in decoder_standalone.py"
    return writes


def _sample_generator_js():
    """The generator, lifted verbatim out of the page's inline <script>."""
    start = DECODER_SRC.index("  const SAMPLE_CHAR")
    end = DECODER_SRC.index('"""', start)
    return DECODER_SRC[start:end]


_NODE_HARNESS = """
let captured = null;
function toast() {}
function loadCapture(capture) { captured = capture; }
loadSample();
console.log(JSON.stringify(captured.packets));
"""


def _sample_packets():
    length = int(_PACKET_LENGTH.search(DECODER_SRC).group(1))
    packets = []
    for i in range(10):
        buf = bytearray(length)
        for at, width, expr in _sample_writes():
            raw = int(eval(expr, {"__builtins__": {}}, {"i": i}))
            if width == 2:
                buf[at] = (raw >> 8) & 0xFF
                buf[at + 1] = raw & 0xFF
            else:
                buf[at] = raw & 0xFF
        packets.append(bytes(buf))
    return packets


def test_put_u16_be_writes_the_high_byte_first():
    """The byte-order scar in AGENT_GUIDE.md, pinned on the shipped source.

    _sample_packets() reimplements this split, so every other sample-data test
    below stays green if the page starts writing little-endian. This one does
    not: it reads the two lines the browser actually runs.
    """
    body = re.search(
        r"function putU16BE\(buf, at, raw\) \{\n(.*?)\n  \}", DECODER_SRC, re.S
    )
    assert body, "putU16BE is no longer in decoder_standalone.py"
    assert [line.strip() for line in body.group(1).splitlines()] == [
        "buf[at] = (raw >> 8) & 0xff;",
        "buf[at + 1] = raw & 0xff;",
    ]


def test_the_sample_generator_writes_every_field_exactly_once():
    """A dropped write used to be invisible: _sample_writes() only checked the

    list was non-empty, and the fields it forgot simply decoded as zero.
    """
    writes = _sample_writes()
    expected = [f for f in TEMTOP_P2["fields"] if f["type"] != "ascii"]
    assert len(writes) == len(expected)
    assert len({start for start, _width, _expr in writes}) == len(writes)


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_sample_packets_match_the_shipped_javascript(tmp_path):
    """Run the real generator under node and diff it against _sample_packets().

    This is what makes the reimplementation above trustworthy rather than a
    second, independently-wrong copy.
    """
    script = tmp_path / "sample.js"
    script.write_text(_sample_generator_js() + _NODE_HARNESS, encoding="utf-8")
    proc = subprocess.run(
        [_NODE, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    shipped = json.loads(proc.stdout)
    assert [bytes(p["dec"]) for p in shipped] == _sample_packets()
    assert [p["hex"] for p in shipped] == [p.hex() for p in _sample_packets()]
    assert {p["char"] for p in shipped} == {TEMTOP_P2["characteristic"]}


def test_sample_packet_length_matches_the_template():
    length = int(_PACKET_LENGTH.search(DECODER_SRC).group(1))
    assert length == TEMTOP_P2["packet_length"]


def test_sample_characteristic_matches_the_template():
    char = _CHARACTERISTIC.search(DECODER_SRC).group(1)
    assert char == TEMTOP_P2["characteristic"]


def test_every_sample_offset_is_claimed_by_a_real_template_field():
    spans = {(f["start_byte"], f["end_byte"]) for f in TEMTOP_P2["fields"]}
    for start, width, _expr in _sample_writes():
        assert (start, start + width - 1) in spans, f"bytes {start}..{start + width - 1}"


def test_every_non_ascii_template_field_is_covered_by_the_sample():
    written = {(start, start + width - 1) for start, width, _expr in _sample_writes()}
    for field in TEMTOP_P2["fields"]:
        if field["type"] == "ascii":
            continue
        assert (field["start_byte"], field["end_byte"]) in written, field["key"]


def test_every_offset_comment_matches_the_template():
    """The old comments contradicted the template on four fields out of five."""
    comments = _OFFSET_COMMENT.findall(DECODER_SRC)
    assert len(comments) == len(TEMTOP_P2["fields"])
    for key, start, end, field_type in comments:
        field = TEMTOP_FIELDS[key]
        assert (int(start), int(end), field_type) == (
            field["start_byte"],
            field["end_byte"],
            field["type"],
        )


def test_sample_temperature_decodes_through_the_real_field():
    temp = TemplateField(TEMTOP_FIELDS["temp"])
    values = [temp.decode(p) for p in _sample_packets()]
    assert values == [24.8, 25.1, 25.4, 25.7, 26.0, 26.3, 26.6, 26.9, 27.2, 27.5]


def test_sample_temperature_exercises_the_above_25_5_case():
    """The scar: an earlier decoder read byte 25 alone and broke above 25.5 C.

    Sample data that never leaves the low byte teaches that bug instead of
    catching it, so at least one packet must carry a non-zero high byte.
    """
    packets = _sample_packets()
    temp = TemplateField(TEMTOP_FIELDS["temp"])
    hot = [p for p in packets if temp.decode(p) > 25.5]
    assert hot, "no sample packet exceeds 25.5 C"
    for packet in hot:
        assert packet[24] != 0
        low_byte_only = packet[25] * TEMTOP_FIELDS["temp"]["scale"]
        assert round(low_byte_only, 2) != temp.decode(packet)


def test_sample_values_are_physical_and_vary():
    decoded = [Template(TEMTOP_P2).decode(p) for p in _sample_packets()]
    ranges = {
        "pm25": (15.0, 33.0),
        "pm10": (21.0, 43.5),
        "temp": (24.8, 27.5),
        "humidity": (41.5, 46.0),
        "aqi": (42, 60),
        "co2": (620, 935),
    }
    for key, (low, high) in ranges.items():
        series = [d[key] for d in decoded]
        assert min(series) == low, key
        assert max(series) == high, key
        # the Byte Diff panel has nothing to show if the samples are constant
        assert len(set(series)) > 1, key
    for row in decoded:
        assert row["pm10"] >= row["pm25"], row

# ── the field-preview arithmetic in static/app.js ──

# decodeFieldPreview() is the only piece of decoding logic that exists twice in
# this repo -- once in template.py for the bridge, once in the browser for the
# wizard's Preview column -- and the two have to agree. It touches no DOM, so
# node can run the shipped function directly rather than a Python paraphrase.

_PREVIEW_HARNESS = """
const fs = require('fs');
const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
console.log(JSON.stringify(cases.map(c => decodeFieldPreview(c.bytes, c.field))));
"""

# (raw bytes, field definition, expected preview string). ``expected`` is what
# TemplateField.decode's ``raw * scale + offset`` produces -- including the
# offset-only row, which is exactly what a preview that ignores ``offset``
# gets wrong, and the scale-0 row, where ``?? 1`` must not treat 0 as absent.
PREVIEW_CASES = [
    ([200], {"type": "uint8", "scale": 1}, "200"),
    ([200], {"type": "uint8", "scale": 0.1}, "20.00"),
    ([100], {"type": "uint8", "scale": 1, "offset": -32}, "68.00"),
    ([0x00, 0x64], {"type": "uint16_be", "scale": 1.07527, "offset": -39.785}, "67.74"),
    ([0x01, 0x00], {"type": "uint16_be", "scale": 1}, "256"),
    ([0x01, 0x00], {"type": "uint16_le", "scale": 1}, "1"),
    ([200], {"type": "uint8", "scale": 0}, "0.00"),
]


def _preview_source():
    """decodeFieldPreview() lifted verbatim out of static/app.js."""
    match = re.search(
        r"^function decodeFieldPreview\(bytes, field\) \{$.*?^\}$", APP_JS, re.M | re.S
    )
    assert match, "decodeFieldPreview is no longer in static/app.js"
    return match.group(0)


@pytest.mark.skipif(_NODE is None, reason="node is not installed")
def test_field_preview_matches_template_py_arithmetic(tmp_path):
    """The wizard previews ``raw * scale + offset``, same as the bridge.

    The wizard has no offset input today, so no capture can currently produce a
    field with one -- this pins the arithmetic against the day it can, and
    against a future edit that decides the offset term looks unused.
    """
    script = tmp_path / "preview.js"
    script.write_text(_preview_source() + _PREVIEW_HARNESS, encoding="utf-8")
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            [
                {"bytes": raw, "field": dict(field, start_byte=0, end_byte=len(raw) - 1)}
                for raw, field, _expected in PREVIEW_CASES
            ]
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [_NODE, str(script), str(cases)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(proc.stdout) == [expected for _r, _f, expected in PREVIEW_CASES]
