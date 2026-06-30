# BLE→MQTT Bridge Usage Guide

A general-purpose tool that connects to any Bluetooth LE device, decodes its packets using user-defined templates, and publishes to MQTT with full Home Assistant auto-discovery support.

> **Prefer the AI-assisted path.** Adding a new device and deploying to a Pi is
> meant to be driven by an AI coding agent — see [AGENT_GUIDE.md](AGENT_GUIDE.md).
> This document is the manual reference for installation, config, and operation.

---

## Table of Contents

- [Quick Start](#quick-start)
- [Installation](#installation)
  - [Native Installation (RPi/Ubuntu)](#native-installation-raspberry-pi-ubuntu)
  - [Docker Installation](#docker-installation)
  - [WSL Installation](#wsl-installation)
- [Configuration](#configuration)
  - [MQTT Setup](#mqtt-setup)
  - [Adding Devices](#adding-devices)
  - [Templates](#templates)
- [Service Management](#service-management)
- [Web Interface](#web-interface)
- [Decoder Wizard](#decoder-wizard)
  - [Packet Capture](#packet-capture)
  - [Creating Templates](#creating-templates)
  - [Standalone Mode](#standalone-mode)
- [Docker Usage](#docker-usage)
- [Troubleshooting](#troubleshooting)

---

## Quick Start

```bash
# Clone and install
git clone <repo-url>
cd ble-mqtt

# Option 1: Native install (recommended for RPi)
sudo ./install.sh install

# Option 2: Docker
docker-compose up -d

# Access web UI at http://localhost:5000
```

---

## Installation

### Native Installation (Raspberry Pi, Ubuntu, Debian)

The installer automatically detects your platform and sets up systemd services.

```bash
# Run the installer
sudo ./install.sh install

# Check status
sudo ./install.sh status
```

**What the installer does:**
1. Installs Python 3, pip, and Bluetooth dependencies
2. Creates dedicated user `ble-mqtt`
3. Installs to `/opt/ble-mqtt/`
4. Sets up systemd services for bridge + web UI
5. Creates virtual environment with dependencies

**Start services:**
```bash
sudo systemctl start ble-mqtt-bridge      # Bridge service
sudo systemctl start ble-mqtt-bridge-ui   # Web UI (port 5000)
sudo systemctl start ble-mqtt-bridge-decoder  # Decoder wizard (port 5001)
```

**Enable auto-start on boot:**
```bash
sudo systemctl enable ble-mqtt-bridge ble-mqtt-bridge-ui
```

**Uninstall:**
```bash
sudo ./install.sh uninstall
```

### Docker Installation

**Requirements:**
- Docker Engine 20.10+
- Docker Compose 2.0+
- Linux host with Bluetooth (for BLE access)

**Quick start:**
```bash
# Build and start
docker-compose up -d

# View logs
docker-compose logs -f
```

**With custom MQTT broker:**
```bash
export MQTT_HOST=192.168.1.100
export MQTT_PORT=1883
export MQTT_USERNAME=myuser
export MQTT_PASSWORD=mypass

docker-compose up -d
```

**Run specific component:**
```bash
# Bridge only
MODE=bridge docker-compose up -d

# Web UI only
MODE=ui docker-compose up -d

# Decoder wizard only
MODE=decoder docker-compose up -d
```

### WSL Installation

WSL requires special handling since Bluetooth passthrough is limited.

```bash
# Install (creates scripts instead of systemd)
sudo ./install.sh install

# Start manually
/opt/ble-mqtt/start-bridge.sh
/opt/ble-mqtt/start-ui.sh
```

**Note:** BLE scanning in WSL requires:
- Windows 11 with WSL2
- `usbipd` to attach Bluetooth adapter to WSL
- Or use the Web UI on Windows and connect to a remote BLE host
- Also it is liklely completely broken because AI

---

## Configuration

All configuration files are in `/opt/ble-mqtt/` (native) or mounted volumes (Docker).

### MQTT Setup

Edit `bridge.conf`:

```ini
[mqtt]
host = 192.168.1.100      # Your MQTT broker IP
port = 1883
username = mqtt_user      # Leave blank if no auth
password = mqtt_pass
topic_prefix = ble
discovery = true          # Enable HA auto-discovery
discovery_prefix = homeassistant

[bridge]
scan_timeout = 10
reconnect_delay = 5
log_level = INFO
fahrenheit = false        # Convert temperature fields to °F (default Celsius)
```

### Adding Devices

Edit `devices.conf`:

```ini
[device:A4:C1:38:CF:8D:75]
template = temtop_p2
name = Living Room Air Quality
location = Living Room

[device:AA:BB:CC:DD:EE:FF]
template = govee_h5075
name = Bedroom Sensor
location = Bedroom
```

**Via Web UI:**
1. Go to **Devices** tab
2. Enter MAC address, name, location
3. Select template from dropdown
4. Click "Add Device"

### Templates

Templates are JSON files in `/opt/ble-mqtt/templates/`.

**Included templates:**
- `temtop_p2` — Temtop P2 (PM1/2.5/10, temp, humidity)
- `temtop_m10` — Temtop M10 (formaldehyde + PM2.5)
- `govee_h5075` — Govee H5075 (temp, humidity, battery)

**Template format:**
```json
{
  "template_version": 1,
  "name": "My Device",
  "characteristic": "0000xxxx-0000-1000-8000-00805f9b34fb",
  "fields": [
    {
      "key": "temperature",
      "name": "Temperature",
      "start_byte": 0,
      "end_byte": 1,
      "type": "int16_be",
      "scale": 0.01,
      "unit": "°C",
      "device_class": "temperature"
    }
  ]
}
```

**Field types:** `uint8`, `int8`, `uint16_be`, `uint16_le`, `int16_be`, `int16_le`, `uint32_be`, `uint32_le`, `float32_be`, `float32_le`, `bool`, `ascii`

---

## Service Management

### Native systemd (Linux)

```bash
# Status
sudo systemctl status ble-mqtt-bridge
sudo systemctl status ble-mqtt-bridge-ui

# Start/Stop/Restart
sudo systemctl start ble-mqtt-bridge
sudo systemctl stop ble-mqtt-bridge
sudo systemctl restart ble-mqtt-bridge

# View logs
sudo journalctl -u ble-mqtt-bridge -f
sudo journalctl -u ble-mqtt-bridge-ui -f

# Auto-start on boot
sudo systemctl enable ble-mqtt-bridge
```

### Docker

```bash
# Start/stop
docker-compose up -d
docker-compose down

# View logs
docker-compose logs -f ble-mqtt

# Restart single service
docker-compose restart ble-mqtt
```

---

## Web Interface

Access at `http://<host>:5000`

### Devices Tab
- View all configured devices
- Add/remove devices
- Change templates
- See connection status

### Templates Tab
- Browse available templates
- Import/export templates
- View template details

### Scanner Tab
- Scan for BLE devices
- See RSSI and device names
- Assign templates to discovered devices
- Capture packets for unknown devices → Decoder Wizard

### Decoder Wizard Tab
- Visual byte mapping tool
- Load capture files
- Define field positions by clicking hex bytes
- Test decode against all captured packets
- Export template JSON

---

## Decoder Wizard

The Decoder Wizard helps you reverse-engineer unsupported BLE devices.

### Packet Capture

**From Scanner tab:**
1. Scan for devices
2. Find unknown device
3. Click "Capture"
4. Enter characteristic UUID
5. Wait 30 seconds while packets are captured

**Manual capture file format** (`captures/<mac>_<timestamp>.json`):
```json
{
  "mac": "A4:C1:38:CF:8D:75",
  "ble_name": "P2_CF8D75",
  "captured": "2026-03-24T15:42:00",
  "packets": [
    {
      "ts": "15:42:02.540",
      "char": "00010203-0405-0607-0809-0a0b0c0d2b10",
      "hex": "d5c82be15a39...",
      "dec": [213, 200, 43, 225, ...]
    }
  ]
}
```

### Creating Templates

**Step-by-step:**

1. **Load capture** → Upload JSON or use captured data
2. **Examine packets** → Use prev/next to see different values
3. **Identify changing bytes** → Byte diff highlights changes
4. **Add fields**:
   - Click "Add Field"
   - Select field row (click on it)
   - Click start byte in hex view
   - Click end byte in hex view
   - Set type, scale, unit
5. **Test decode** → Verify values look reasonable
6. **Export** → Download template JSON

**Tips:**
- Temperature often has scale 0.1 or 0.01
- PM sensors usually use uint16 with scale 0.1
- Signed values use int16 (negative temps)
- Watch for endianness (BE = big, LE = little)

### Standalone Mode

Run decoder wizard without the full bridge:

```bash
# Via systemd (if installed)
sudo systemctl start ble-mqtt-bridge-decoder

# Or manually
cd /opt/ble-mqtt
python3 decoder_standalone.py --port 5001

# Access at http://localhost:5001
```

**Features:**
- Same visual interface
- Load any capture file
- Generate templates without bridge running
- Includes sample data for testing

---

## Docker Usage

### Build

```bash
docker build -t ble-mqtt .
```

### Run

**Bridge + UI (default):**
```bash
docker run -d \
  --name ble-mqtt \
  --privileged \
  --network host \
  -e MQTT_HOST=192.168.1.100 \
  -e MODE=bridge-ui \
  ble-mqtt
```

**Just the bridge:**
```bash
docker run -d \
  --name ble-mqtt-bridge \
  --privileged \
  --network host \
  -e MODE=bridge \
  -v $(pwd)/bridge.conf:/app/bridge.conf \
  -v $(pwd)/devices.conf:/app/devices.conf \
  ble-mqtt
```

**Just the UI (connects to external bridge):**
```bash
docker run -d \
  --name ble-mqtt-ui \
  -p 5000:5000 \
  -e MODE=ui \
  ble-mqtt
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MODE` | `bridge-ui` | Component: `bridge`, `ui`, `decoder`, `bridge-ui` |
| `MQTT_HOST` | `localhost` | MQTT broker hostname/IP |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USERNAME` | `` | MQTT username (blank = no auth) |
| `MQTT_PASSWORD` | `` | MQTT password |

### Volumes

| Path | Description |
|------|-------------|
| `/app/bridge.conf` | MQTT and bridge settings |
| `/app/devices.conf` | Device configurations |
| `/app/templates` | Custom templates |
| `/app/captures` | Packet capture storage |

### Docker Compose Examples

**With Mosquitto (built-in MQTT):**
```yaml
version: '3.8'
services:
  mqtt:
    image: eclipse-mosquitto:2
    ports:
      - "1883:1883"
    volumes:
      - ./mosquitto.conf:/mosquitto/config/mosquitto.conf

  bridge:
    build: .
    privileged: true
    network_mode: host
    environment:
      - MQTT_HOST=localhost
      - MQTT_PORT=1883
    depends_on:
      - mqtt
```

**Separate containers:**
```yaml
services:
  bridge:
    build: .
    privileged: true
    network_mode: host
    environment:
      - MODE=bridge
    volumes:
      - ./bridge.conf:/app/bridge.conf
      - ./devices.conf:/app/devices.conf

  ui:
    build: .
    ports:
      - "5000:5000"
    environment:
      - MODE=ui

  decoder:
    build: .
    ports:
      - "5001:5001"
    environment:
      - MODE=decoder
```

---

## Troubleshooting

### BLE Connection Issues

**"No such device" or permission denied:**
```bash
# Check Bluetooth service
sudo systemctl status bluetooth
sudo systemctl start bluetooth

# Add user to bluetooth group
sudo usermod -aG bluetooth ble-mqtt
sudo systemctl restart ble-mqtt-bridge
```

**Can't find devices:**
- Ensure device is in pairing mode
- Check if device needs specific characteristic UUID
- Try scanning from `bluetoothctl`: `scan on`

**Docker can't access BLE:**
- Must run with `--privileged --network host` on Linux
- BLE passthrough doesn't work on macOS/Windows Docker Desktop

### MQTT Issues

**"Connection refused":**
- Verify MQTT broker is running: `mosquitto -v`
- Check firewall: `sudo ufw allow 1883`
- Verify credentials in `bridge.conf`

**No HA auto-discovery:**
- Check `discovery = true` in `bridge.conf`
- Verify `discovery_prefix` matches HA setting
- Check HA MQTT integration is configured

### Web UI Issues

**Port already in use:**
```bash
# Find and kill process
sudo lsof -i :5000
sudo kill <PID>

# Or use different port
python3 bridge_ui.py  # Edit to change port
```

**Decoder not loading captures:**
- Verify JSON format matches expected schema
- Check browser console for errors
- Try the sample data in standalone mode

### Logs

**Native:**
```bash
sudo journalctl -u ble-mqtt-bridge -n 100 --no-pager
sudo journalctl -u ble-mqtt-bridge-ui -f
```

**Docker:**
```bash
docker-compose logs -f
docker logs ble-mqtt-bridge
```

---

## Advanced Usage

### Custom Templates

1. Create template file in `templates/mydevice.json`
2. Update `templates/index.json` with metadata
3. Reload: `sudo systemctl restart ble-mqtt-bridge`

### Multiple Bridges

Run multiple bridge instances for different device groups:

```bash
# Bridge 1
cp bridge.conf bridge1.conf
cp devices.conf devices1.conf
# Edit configs, then:
python3 ble_mqtt_bridge.py --config bridge1.conf

# Bridge 2
python3 ble_mqtt_bridge.py --config bridge2.conf
```

### API Access

The web UI exposes a REST API:

```bash
# Get devices
curl http://localhost:5000/api/devices

# Add device
curl -X POST http://localhost:5000/api/devices \
  -H "Content-Type: application/json" \
  -d '{"mac":"AA:BB:CC:DD:EE:FF","name":"Test","template":"temtop_p2"}'

# Scan
curl -X POST http://localhost:5000/api/scan \
  -H "Content-Type: application/json" \
  -d '{"timeout":10}'
```

### Health Checks

**Bridge health:**
```bash
curl -s http://localhost:5000/api/devices | jq .
```

**MQTT health:**
```bash
mosquitto_sub -h localhost -t "ble/+/availability" -v
```

---

## Support

- **Issues:** https://github.com/anomalyco/opencode/issues
- **Documentation:** See README.md for architecture details
- **Help:** `/help` command for CLI usage

---

## License

See LICENSE file for details.
