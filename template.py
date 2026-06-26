"""Template system for BLE packet decoding."""

import json
import struct
import os
from pathlib import Path
from typing import Any, Optional


SUPPORTED_TYPES = {
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

HA_DEVICE_CLASSES = {
    "pm25",
    "pm10",
    "temperature",
    "humidity",
    "carbon_dioxide",
    "aqi",
    "pressure",
    "battery",
    "signal_strength",
    "energy",
    "power",
    "voltage",
    "current",
    "illuminance",
    "moisture",
}


class TemplateField:
    """A single field definition from a template."""

    def __init__(self, definition: dict):
        self.key = definition["key"]
        self.name = definition.get("name", self.key)
        self.start_byte = definition["start_byte"]
        self.end_byte = definition.get("end_byte", self.start_byte)
        self.field_type = definition.get("type", "uint8")
        self.scale = definition.get("scale", 1)
        self.unit = definition.get("unit", "")
        self.device_class = definition.get("device_class", "")
        self.state_class = definition.get("state_class", "")
        self.icon = definition.get("icon", "")
        self.fahrenheit_convert = definition.get("fahrenheit_convert", False)

    def decode(self, data: bytes, fahrenheit: bool = False) -> Any:
        """Decode this field from raw packet bytes.

        ``fahrenheit_convert`` marks a field as a Celsius temperature that may be
        converted; the conversion only happens when the caller passes
        ``fahrenheit=True`` (driven by the global bridge setting).
        """
        raw = data[self.start_byte : self.end_byte + 1]
        value = self._decode_raw(raw)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = value * self.scale
            if self.fahrenheit_convert and fahrenheit:
                value = value * 9 / 5 + 32
            if isinstance(value, float):
                value = round(value, 2)
        return value

    def _decode_raw(self, raw: bytes) -> Any:
        length = len(raw)
        if self.field_type == "uint8":
            return raw[0]
        elif self.field_type == "int8":
            return struct.unpack("b", raw[:1])[0]
        elif self.field_type == "bool":
            return raw[0] != 0
        elif self.field_type == "ascii":
            return raw.decode("ascii", errors="replace").strip("\x00")
        elif self.field_type == "uint16_be":
            return struct.unpack(">H", raw[:2])[0]
        elif self.field_type == "uint16_le":
            return struct.unpack("<H", raw[:2])[0]
        elif self.field_type == "int16_be":
            return struct.unpack(">h", raw[:2])[0]
        elif self.field_type == "int16_le":
            return struct.unpack("<h", raw[:2])[0]
        elif self.field_type == "uint32_be":
            return struct.unpack(">I", raw[:4])[0]
        elif self.field_type == "uint32_le":
            return struct.unpack("<I", raw[:4])[0]
        elif self.field_type == "float32_be":
            return struct.unpack(">f", raw[:4])[0]
        elif self.field_type == "float32_le":
            return struct.unpack("<f", raw[:4])[0]
        else:
            raise ValueError(f"Unsupported field type: {self.field_type}")

    def to_dict(self) -> dict:
        d = {
            "key": self.key,
            "name": self.name,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "type": self.field_type,
        }
        if self.scale != 1:
            d["scale"] = self.scale
        if self.unit:
            d["unit"] = self.unit
        if self.device_class:
            d["device_class"] = self.device_class
        if self.state_class:
            d["state_class"] = self.state_class
        if self.icon:
            d["icon"] = self.icon
        if self.fahrenheit_convert:
            d["fahrenheit_convert"] = True
        return d


class Template:
    """A complete device template."""

    def __init__(self, definition: dict):
        self.template_version = definition.get("template_version", 1)
        self.name = definition["name"]
        self.manufacturer = definition.get("manufacturer", "")
        self.model = definition.get("model", "")
        self.ble_name_prefix = definition.get("ble_name_prefix", "")
        self.packet_length = definition.get("packet_length")
        self.characteristic = definition.get("characteristic", "")
        self.fields = [TemplateField(f) for f in definition.get("fields", [])]

    def decode(self, data: bytes, fahrenheit: bool = False) -> dict:
        """Decode a full packet, returning {key: value} dict."""
        result = {}
        for field in self.fields:
            try:
                result[field.key] = field.decode(data, fahrenheit=fahrenheit)
            except (IndexError, struct.error):
                result[field.key] = None
        return result

    def to_dict(self) -> dict:
        d = {
            "template_version": self.template_version,
            "name": self.name,
            "manufacturer": self.manufacturer,
            "model": self.model,
        }
        if self.ble_name_prefix:
            d["ble_name_prefix"] = self.ble_name_prefix
        if self.packet_length:
            d["packet_length"] = self.packet_length
        if self.characteristic:
            d["characteristic"] = self.characteristic
        d["fields"] = [f.to_dict() for f in self.fields]
        return d


class TemplateRegistry:
    """Loads and manages all available templates."""

    def __init__(self, template_dir: str = "templates"):
        self.template_dir = Path(template_dir)
        self.templates: dict[str, Template] = {}
        self.index: dict[str, dict] = {}
        self.load_all()

    def load_all(self):
        """Load all templates from the template directory."""
        self.templates.clear()
        if not self.template_dir.exists():
            self.template_dir.mkdir(parents=True, exist_ok=True)
            return

        # Load index if exists
        index_path = self.template_dir / "index.json"
        if index_path.exists():
            with open(index_path) as f:
                self.index = json.load(f)

        # Load each template file
        for path in self.template_dir.glob("*.json"):
            if path.name == "index.json":
                continue
            try:
                with open(path) as f:
                    data = json.load(f)
                template_id = path.stem
                self.templates[template_id] = Template(data)
            except (json.JSONDecodeError, KeyError) as e:
                print(f"Warning: failed to load template {path}: {e}")

    def get(self, template_id: str) -> Optional[Template]:
        return self.templates.get(template_id)

    def list_templates(self) -> list[dict]:
        """Return list of template metadata."""
        result = []
        for tid, tmpl in self.templates.items():
            entry = {
                "id": tid,
                "name": tmpl.name,
                "manufacturer": tmpl.manufacturer,
                "model": tmpl.model,
                "field_count": len(tmpl.fields),
            }
            if tid in self.index:
                entry.update(self.index[tid])
            result.append(entry)
        return result

    def find_by_ble_name(self, name: str) -> Optional[Template]:
        """Find a template matching a BLE device name."""
        name_lower = name.lower()
        for tmpl in self.templates.values():
            if tmpl.ble_name_prefix and tmpl.ble_name_prefix.lower() in name_lower:
                return tmpl
        return None

    def save_template(self, template_id: str, template: Template):
        """Save a template to disk."""
        self.template_dir.mkdir(parents=True, exist_ok=True)
        path = self.template_dir / f"{template_id}.json"
        with open(path, "w") as f:
            json.dump(template.to_dict(), f, indent=2)
        self.templates[template_id] = template


def load_devices_config(path: str = "devices.conf") -> dict:
    """Parse devices.conf into {mac: {template, name, location}} dict."""
    devices = {}
    current_mac = None
    if not os.path.exists(path):
        return devices
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[device:") and line.endswith("]"):
                current_mac = line[8:-1]
                devices[current_mac] = {}
            elif current_mac and "=" in line:
                key, val = line.split("=", 1)
                devices[current_mac][key.strip()] = val.strip()
    return devices


def load_bridge_config(path: str = "bridge.conf") -> dict:
    """Parse bridge.conf into nested dict."""
    config = {
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
    if not os.path.exists(path):
        return config
    section = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
            elif section and "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if section in config:
                    # Type coercion
                    if val.lower() in ("true", "yes"):
                        val = True
                    elif val.lower() in ("false", "no"):
                        val = False
                    elif val.isdigit():
                        val = int(val)
                    config[section][key] = val
    return config
