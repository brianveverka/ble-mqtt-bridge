# Project: ble_mqtt — BLE to MQTT bridge

Connects to BLE sensors, decodes their notify packets against a JSON template, and
publishes to MQTT with Home Assistant auto-discovery.

## `AGENT_GUIDE.md` is the procedure — follow it, don't re-derive it

Onboarding a device is the main work here and `AGENT_GUIDE.md` is written for the agent
doing it: identify, determine transport, capture, decode, template, verify, contribute.
It is not background reading. Work the numbered method.

`README.md` and `USAGE.md` are the user-facing docs. Nothing about the decode method
belongs in this file — it is already written down once.

## Tests

`tests/` at the repo root; `python -m pytest` from the repo root runs them.

**They do not need `bleak` or `paho-mqtt` installed.** Both libraries sit behind lazy
import shims — `_get_bleak()` in `ble_scanner.py`, `_get_mqtt()` in `mqtt_publisher.py`
— so importing a module never touches the radio or the broker, and the tests substitute
fakes at those seams. Hoisting either import to module scope makes the whole suite
uninstallable on a machine without a Bluetooth stack. If you add a third-party
dependency to a module the tests import, put it behind the same kind of shim. What the
suite does need is `pytest` and `flask` (`tests/test_web.py` drives the web UI).

## Things that are load-bearing

**A new device is one JSON file, not code.** `templates/<id>.json` plus a block in
`devices.conf`. If a device seems to need a code change, it is a genuinely new capability
(a new field type, or advertisement-broadcast mode rather than connect-and-notify) — say
that out loud before writing it, because the template system is what keeps this repo from
turning into a pile of per-device special cases.

## Backlog

Repo items go in `BACKLOG.md` at the repo root.

## Deploy

`sudo bash install.sh install` on the Pi — installs to `/opt/ble-mqtt` with its own
service user and venv, and lays down the systemd units. Idempotent and rerunnable. Config
(`bridge.conf`, `devices.conf`) is written on the host and owned by `ble-mqtt`; it is not
in the repo.

Verify: logs show every configured device `connected and streaming`, **and** state
actually lands on the broker. A clean start with nothing publishing is the common failure
and it looks like success in `systemctl status`.

Adapter troubles are usually rfkill or a phantom BlueZ connection — both are in
`AGENT_GUIDE.md`'s gotchas rather than here.
