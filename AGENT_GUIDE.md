# Agent Guide — Onboard a BLE device & deploy, with an AI assistant

**This is the intended way to use this project.** Reverse-engineering a BLE
device and standing the bridge up on a Pi is fiddly, per-device, and interactive
— exactly the kind of work an AI coding agent is good at. Hand this file and the
repo to an agent (Claude Code, or any agent with a shell), give it access to a
Linux box with a Bluetooth adapter (a Raspberry Pi is ideal), and let it drive.

You stay in the loop for the physical steps it can't do: pressing the device's
button, moving a probe into ice water, reading a reference thermometer. The agent
does the scanning, capturing, decoding, template-writing, deploying, and
verifying.

> If you are that agent: read this top to bottom, then work the numbered method.
> Confirm each decode against ground truth — do not guess byte meanings from a
> single capture. Ask the human for the physical actions you need.

---

## What you need

- A Linux host with a working BLE adapter and BlueZ (a Raspberry Pi running this
  bridge is perfect). `python3` with `bleak` and `paho-mqtt` available.
- The device, powered and within ~10 m.
- An MQTT broker reachable from the host (for the verify + deploy steps).
- SSH access to the target Pi if deploying remotely.

The agent typically works over SSH to the Pi and runs short Python/`bleak`
scripts there to scan, connect, and capture.

---

## The method

### 1. Identify the device
Scan and list nearby devices with names, RSSI, and manufacturer data. If the
advertised name isn't obviously your device (it usually isn't — the BLE name
rarely matches the box), **isolate it by toggling**:

1. Snapshot a scan with the device **off**.
2. Turn it **on**; scan again.
3. Diff the address sets. Ignore Apple/phone MACs (company id `0x004C`, rotating
   random addresses) — they're noise. Your device is the new, stable MAC.
4. Confirm by turning it back off and watching that MAC disappear.

### 2. Determine the transport
Connect and enumerate GATT services/characteristics. Look for a **notify**
characteristic — often a vendor service exposing `ff01` (notify) / `ff02`
(write) / `ff03` (read), or a Temtop-style `…2b10`. That's the
connect-and-notify model this bridge supports.

If the only data is in the **advertisement** (manufacturer/service data) and
there's no usable notify characteristic, it's a broadcast device. Note that —
advertisement decoding is a separate mode and may need a code change.

### 3. Capture packets
Subscribe to the notify characteristic and log raw hex. Many devices stream as
soon as you subscribe; some need an "enable" byte sequence written to the write
characteristic first. Reading the read characteristic often returns firmware or
current state (e.g. `ff03` → ASCII `V1.6.3`). Collect a baseline.

### 4. Decode with controlled experiments — **the important part**
Never map bytes from one capture. Change **one physical quantity at a time** and
watch which byte moves. Use known fixed points so you don't even need a perfect
reference:

- **Ice-water slurry = 0 °C / 32 °F** (exact, free anchor).
- **Boiling water = 100 °C / 212 °F** (adjust for altitude).
- Otherwise a trusted reference thermometer, read **at the moment you capture**
  (account for the thing cooling/heating between the reading and the capture).

Then:
- The byte that tracks the sensor you changed is that sensor's value.
- Solve **scale** and **offset** from ≥2 known points; confirm with a third.
- Watch for multi-byte values (`uint16` LE/BE) and single-byte saturation.
  Real bug from this repo: the Temtop temperature is `uint16_be` over bytes
  24–25 (×0.1 °C); an earlier decoder read only byte 25 and silently broke above
  25.5 °C. Always sanity-check that values stay physical across the full range.

### 5. Write the template
Add `templates/<id>.json`. Each field:

```json
{
  "key": "temp", "name": "Temperature",
  "start_byte": 24, "end_byte": 25,
  "type": "uint16_be", "scale": 0.1,
  "unit": "°C", "device_class": "temperature",
  "state_class": "measurement", "icon": "mdi:thermometer",
  "fahrenheit_convert": true
}
```

Supported types: `uint8/int8`, `uint16_be/le`, `int16_be/le`, `uint32_be/le`,
`float32_be/le`, `bool`, `ascii`. The decoded value is `raw * scale + offset`
(use `offset` for sensors that don't read 0 at raw 0 — e.g. the ThermoPro probe
below). Set `characteristic` to the notify UUID and `packet_length` to the
expected size. Mark temperature fields
`fahrenheit_convert: true` (the bridge converts only when `[bridge] fahrenheit =
true`). Validate by decoding your captured packets and checking every value is
physically sane.

### 6. Add the device & verify
Add a block to `devices.conf`:

```ini
[device:DC:8E:95:34:3B:02]
template = thermopro_tp960
name     = ThermoPro Probe
location = Kitchen
```

Restart the bridge, watch logs for `connected and streaming`, then subscribe to
the broker (`ble/<mac>/...` state, `homeassistant/sensor/.../config` discovery)
and confirm the values. If you changed topic/unique-id schemes during iteration,
clear stale **retained** discovery configs by publishing an empty retained
payload to the old `…/config` topics so Home Assistant drops the ghosts.

### 7. Contribute it back
Open a PR adding the template (and any code, if a new field type or broadcast
mode was needed). One JSON file per device.

---

## Deploying to a Raspberry Pi

Point the agent at the Pi over SSH and have it:

1. `git clone` this repo on the Pi (or `git pull` an existing checkout).
2. `sudo bash install.sh install` — installs to `/opt/ble-mqtt`, creates the
   `ble-mqtt` service user + venv, and lays down systemd units.
3. Write `/opt/ble-mqtt/bridge.conf` (broker host/port, `fahrenheit`) and
   `/opt/ble-mqtt/devices.conf`; `chown ble-mqtt:ble-mqtt`.
4. `systemctl enable --now ble-mqtt-bridge` (disable `-ui` / `-decoder` if you
   want headless).
5. Verify: logs show both/all devices streaming, and state lands on the broker.

---

## Gotchas worth knowing up front

- **Bluetooth RF-kill.** Pis often boot with the adapter soft-blocked, so the
  bridge fails with "no powered adapters". The service unblocks it via a root
  `ExecStartPre` (`rfkill unblock bluetooth`, sysfs fallback, `hciconfig hci0
  up`). If scanning fails, check `cat /sys/class/rfkill/rfkill*/soft`.
- **Phantom connections.** After lots of connect/disconnect churn, BlueZ may
  report `Connected: yes` on a dead link — and a scan can't see a device it
  thinks is connected, so connects fail with "not found". Clear it with
  `bluetoothctl power off; power on` (or reboot).
- **Single-connection devices.** Many sensors accept only one BLE connection at a
  time. If your phone app is connected, the bridge can't connect, and vice-versa.
- **journald bloat.** On small SD cards, cap it: drop a
  `/etc/systemd/journald.conf.d/00-cap.conf` with `SystemMaxUse=100M`.
- **DKMS / apt.** A repurposed Pi may have leftover DKMS modules from old HATs
  that break kernel/apt operations — clear them if `apt` fails.

---

## Worked examples in this repo

- **Temtop P2** (`templates/temtop_p2.json`): vendor notify char `…2b10`, 49-byte
  packets; PM2.5/PM10/AQI/temperature/humidity/CO2. Temp is `uint16_be` bytes
  24–25 ×0.1 °C.
- **ThermoPro TP960R** (`templates/thermopro_tp960.json`): vendor service
  `93bb7cab-…`, `ff01` notify, 8-byte packets shaped `10 00 [probe] 00 [..] 0a
  [..] 00`. The probe is byte 2 (`uint8`); calibrated against two equilibrium
  fixed points — ice water (b2=37 → 0 °C) and a rolling boil (b2=130 → 100 °C) —
  giving `scale 1.07527, offset -39.785`. A textbook controlled-decode. (Note:
  use *equilibrium* points, not transient mid-heating readings — the probe and a
  reference thermometer lag and stratify while the water is heating.)
