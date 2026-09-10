"""Standalone Decoder Wizard - run independently of the bridge.

Usage: python3 decoder_standalone.py [--port 5001]
"""

import argparse
from flask import Flask, render_template_string, jsonify

from ui_fragments import HEAD_END, HEAD_START, PAGE_END, SCRIPTS_START, WIZARD_PANELS

app = Flask(__name__, static_folder="static")

# Assembled by concatenation, not formatting -- see ui_fragments for why.
HTML = (
    HEAD_START
    + "  <title>BLE Decoder Wizard</title>\n"
    + HEAD_END
    + r"""
<header>
  <h1>BLE <span>Decoder</span> Wizard</h1>
  <div>Standalone Mode</div>
</header>

<div style="padding: 24px; max-width: 1200px; margin: 0 auto;">

  <div class="card">
    <h3>Load Capture</h3>
    <p style="color: var(--text2); margin-bottom: 12px;">
      Load a JSON capture file to begin mapping byte fields.
    </p>
    <button class="btn" onclick="loadCaptureFromFile()">Load Capture File</button>
    <button class="btn secondary" onclick="loadSample()">Load Sample Data</button>
  </div>

"""
    + WIZARD_PANELS
    + r"""
</div>

"""
    + SCRIPTS_START
    + r"""  window._templates = [];

  const SAMPLE_CHAR = '00010203-0405-0607-0809-0a0b0c0d2b10';
  const SAMPLE_PACKET_LENGTH = 49;

  // Every multi-byte field in templates/temtop_p2.json is uint16_be.
  function putU16BE(buf, at, raw) {
    buf[at] = (raw >> 8) & 0xff;
    buf[at + 1] = raw & 0xff;
  }

  // Sample packets for the demo, laid out to match templates/temtop_p2.json
  // exactly: every offset below is that template's own start_byte/end_byte, and
  // tests/test_web.py asserts the two still agree. An offset that drifts from
  // the template teaches whoever clicks "Load Sample Data" a layout that does
  // not exist.
  function loadSample() {
    const packets = [];
    for (let i = 0; i < 10; i++) {
      const bytes = new Array(SAMPLE_PACKET_LENGTH).fill(0);
      // pm25      bytes 22-23  uint16_be  scale 0.1  ->  15.0 .. 33.0 ug/m3
      putU16BE(bytes, 22, 150 + i * 20);
      // temp      bytes 24-25  uint16_be  scale 0.1  ->  24.8 .. 27.5 C
      // Deliberately crosses 25.5 C: from i = 3 the raw value is 257 or more,
      // so the high byte is non-zero. That is precisely the case the decoder
      // scar in AGENT_GUIDE.md got wrong by reading byte 25 on its own, and
      // sample data that never leaves the low byte teaches the bug rather than
      // catching it.
      putU16BE(bytes, 24, 248 + i * 3);
      // humidity  bytes 26-27  uint16_be  scale 0.1  ->  41.5 .. 46.0 %
      putU16BE(bytes, 26, 415 + i * 5);
      // aqi       bytes 29-29  uint8      no scale   ->  42 .. 60
      bytes[29] = 42 + i * 2;
      // co2       bytes 30-31  uint16_be  no scale   ->  620 .. 935 ppm
      putU16BE(bytes, 30, 620 + i * 35);
      // pm10      bytes 34-35  uint16_be  scale 0.1  ->  21.0 .. 43.5 ug/m3
      putU16BE(bytes, 34, 210 + i * 25);

      packets.push({
        ts: (i * 3).toFixed(3),
        char: SAMPLE_CHAR,
        hex: bytes.map(b => b.toString(16).padStart(2, '0')).join(''),
        dec: bytes,
      });
    }
    loadCapture({ mac: 'AA:BB:CC:DD:EE:FF', ble_name: 'SAMPLE', packets: packets });
    toast('Sample data loaded', 'success');
  }
"""
    + PAGE_END
)


@app.route("/")
def index():
    return render_template_string(HTML)


@app.route("/api/templates", methods=["GET"])
def get_templates():
    return jsonify({"templates": []})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Standalone BLE Decoder Wizard")
    parser.add_argument("--port", type=int, default=5001)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    print(f"Decoder Wizard running at http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=True)
