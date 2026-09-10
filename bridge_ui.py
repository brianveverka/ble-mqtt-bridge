"""Web UI for BLE→MQTT Bridge - Devices, Templates, Scanner, Decoder Wizard."""

import json
import os
import asyncio
from pathlib import Path
from flask import Flask, render_template_string, jsonify, request

from template import TemplateRegistry, Template, load_devices_config
from ui_fragments import HEAD_END, HEAD_START, PAGE_END, SCRIPTS_START, WIZARD_PANELS

app = Flask(__name__, static_folder="static")
registry = TemplateRegistry("templates")
DEVICES_FILE = "devices.conf"

# Assembled by concatenation, not formatting: the markup below contains both
# Jinja tags and JS template literals, and neither survives an f-string.
HTML = (
    HEAD_START
    + "  <title>BLE→MQTT Bridge</title>\n"
    + HEAD_END
    + r"""
<header>
  <h1>BLE → <span>MQTT</span> Bridge</h1>
</header>

<nav class="tabs">
  <button class="active" data-tab="devices">Devices</button>
  <button data-tab="templates">Templates</button>
  <button data-tab="scanner">Scanner</button>
  <button data-tab="decoder">Decoder Wizard</button>
</nav>

<!-- DEVICES TAB -->
<div id="devices" class="tab-content active">
  <div class="card">
    <h3>Add Device</h3>
    <div class="form-row">
      <div class="form-group">
        <label>MAC Address</label>
        <input id="new-mac" placeholder="AA:BB:CC:DD:EE:FF">
      </div>
      <div class="form-group">
        <label>Name</label>
        <input id="new-name" placeholder="Living Room Sensor">
      </div>
      <div class="form-group">
        <label>Location</label>
        <input id="new-location" placeholder="Living Room">
      </div>
      <div class="form-group">
        <label>Template</label>
        <select id="new-template"></select>
      </div>
      <div class="form-group">
        <label>&nbsp;</label>
        <button class="btn" onclick="addDevice()">Add Device</button>
      </div>
    </div>
  </div>

  <table>
    <thead>
      <tr><th>MAC</th><th>Name</th><th>Location</th><th>Template</th><th>Actions</th></tr>
    </thead>
    <tbody id="device-list"></tbody>
  </table>
</div>

<!-- TEMPLATES TAB -->
<div id="templates" class="tab-content">
  <div class="card">
    <h3>Template Library</h3>
    <div style="display:flex; gap:8px; margin-bottom:16px;">
      <button class="btn" onclick="document.querySelector('[data-tab=decoder]').click()">
        New Template (Wizard)
      </button>
      <button class="btn secondary" onclick="importTemplate()">Import from File</button>
    </div>
  </div>
  <table>
    <thead>
      <tr><th>Name</th><th>Manufacturer</th><th>Model</th><th>Fields</th><th>Actions</th></tr>
    </thead>
    <tbody id="template-list"></tbody>
  </table>
</div>

<!-- SCANNER TAB -->
<div id="scanner" class="tab-content">
  <div class="card">
    <h3>BLE Scanner</h3>
    <div class="form-row">
      <div class="form-group">
        <label>Scan Timeout (seconds)</label>
        <input id="scan-timeout" type="number" value="10" min="1" max="60">
      </div>
      <div class="form-group">
        <label>&nbsp;</label>
        <button class="btn" id="scan-btn" onclick="scanDevices()">Start Scan</button>
      </div>
    </div>
  </div>
  <table>
    <thead>
      <tr><th>Address</th><th>Name</th><th>RSSI</th><th>Template</th><th>Actions</th></tr>
    </thead>
    <tbody id="scan-results"></tbody>
  </table>
</div>

<!-- DECODER WIZARD TAB -->
<div id="decoder" class="tab-content">
  <div class="card">
    <h3>Decoder Wizard</h3>
    <p style="color: var(--text2); margin-bottom:12px;">
      Load a capture file or use "Capture" from the Scanner tab to begin.
    </p>
    <button class="btn secondary" onclick="loadCaptureFromFile()">Load Capture File</button>
  </div>

"""
    + WIZARD_PANELS
    + "</div>\n\n"
    + SCRIPTS_START
    + r"""  // Pre-populate template list for dropdowns
  window._templates = {{ templates | tojson }};
  // Populate template dropdown in add-device form
  const sel = document.getElementById('new-template');
  sel.innerHTML = '<option value="">-- select --</option>' +
    window._templates.map(t => `<option value="${t.id}">${t.name}</option>`).join('');
"""
    + PAGE_END
)


@app.route("/")
def index():
    templates = registry.list_templates()
    return render_template_string(HTML, templates=templates)


# ── Device API ──


@app.route("/api/devices", methods=["GET"])
def get_devices():
    devices = load_devices_config(DEVICES_FILE)
    return jsonify({"devices": devices})


@app.route("/api/devices", methods=["POST"])
def add_device():
    data = request.json
    mac = data.get("mac", "").upper()
    if not mac:
        return jsonify({"error": "MAC required"}), 400

    name = data.get("name", "")
    location = data.get("location", "")
    template = data.get("template", "")

    devices = load_devices_config(DEVICES_FILE)
    devices[mac] = {"template": template, "name": name, "location": location}
    _save_devices(devices)
    return jsonify({"ok": True})


@app.route("/api/devices/<mac>", methods=["PUT"])
def update_device(mac):
    mac = mac.upper()
    data = request.json
    devices = load_devices_config(DEVICES_FILE)
    if mac not in devices:
        return jsonify({"error": "not found"}), 404
    for key in ("template", "name", "location"):
        if key in data:
            devices[mac][key] = data[key]
    _save_devices(devices)
    return jsonify({"ok": True})


@app.route("/api/devices/<mac>", methods=["DELETE"])
def delete_device(mac):
    mac = mac.upper()
    devices = load_devices_config(DEVICES_FILE)
    devices.pop(mac, None)
    _save_devices(devices)
    return jsonify({"ok": True})


def _save_devices(devices: dict):
    with open(DEVICES_FILE, "w", encoding="utf-8") as f:
        for mac, cfg in devices.items():
            f.write(f"[device:{mac}]\n")
            for k, v in cfg.items():
                f.write(f"{k} = {v}\n")
            f.write("\n")


# ── Template API ──


@app.route("/api/templates", methods=["GET"])
def get_templates():
    return jsonify({"templates": registry.list_templates()})


@app.route("/api/templates/<template_id>", methods=["GET"])
def get_template(template_id):
    tmpl = registry.get(template_id)
    if not tmpl:
        return jsonify({"error": "not found"}), 404
    return jsonify(tmpl.to_dict())


@app.route("/api/templates", methods=["POST"])
def save_template():
    data = request.json
    template_id = data.get("id", "")
    template_data = data.get("template", {})
    if not template_id:
        return jsonify({"error": "id required"}), 400
    tmpl = Template(template_data)
    registry.save_template(template_id, tmpl)
    return jsonify({"ok": True})


@app.route("/api/templates/<template_id>", methods=["DELETE"])
def delete_template(template_id):
    path = Path("templates") / f"{template_id}.json"
    if path.exists():
        path.unlink()
    registry.load_all()
    return jsonify({"ok": True})


# ── Scanner API ──


@app.route("/api/scan", methods=["POST"])
def scan():
    data = request.json or {}
    timeout = data.get("timeout", 10)
    try:
        from ble_scanner import scan_devices

        loop = asyncio.new_event_loop()
        devices = loop.run_until_complete(scan_devices(timeout))
        loop.close()
        return jsonify({"devices": devices})
    except ImportError:
        return jsonify({"error": "bleak not installed", "devices": []}), 501


# ── Capture API ──


@app.route("/api/capture", methods=["POST"])
def capture():
    data = request.json or {}
    address = data.get("address", "")
    characteristic = data.get("characteristic", "")
    duration = data.get("duration", 30)
    max_packets = data.get("max_packets", 25)

    if not address or not characteristic:
        return jsonify({"error": "address and characteristic required"}), 400

    try:
        from ble_scanner import capture_packets

        loop = asyncio.new_event_loop()
        packets = loop.run_until_complete(
            capture_packets(address, characteristic, duration, max_packets)
        )
        loop.close()

        import datetime

        capture_data = {
            "mac": address,
            "ble_name": "",
            "captured": datetime.datetime.now().isoformat(),
            "packets": packets,
        }

        # Save capture file
        os.makedirs("captures", exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        mac_safe = address.replace(":", "")
        filename = f"{mac_safe}_{ts}.json"
        with open(f"captures/{filename}", "w", encoding="utf-8") as f:
            json.dump(capture_data, f, indent=2)

        return jsonify(capture_data)
    except ImportError:
        return jsonify({"error": "bleak not installed"}), 501


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
