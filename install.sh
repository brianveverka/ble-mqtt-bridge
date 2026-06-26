#!/usr/bin/env bash
set -euo pipefail

# BLE→MQTT Bridge Installer
# Supports: Raspberry Pi OS, Ubuntu, Debian, WSL

APP_NAME="ble-mqtt-bridge"
APP_DIR="/opt/ble-mqtt"
SERVICE_USER="ble-mqtt"
PYTHON="python3"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[✗]${NC} $*" >&2; }

require_root() {
    if [[ $EUID -ne 0 ]]; then
        err "This script must be run as root (use sudo)"
        exit 1
    fi
}

detect_platform() {
    if grep -qi microsoft /proc/version 2>/dev/null; then
        echo "wsl"
    elif [[ -f /proc/device-tree/model ]] && grep -qi raspberry /proc/device-tree/model 2>/dev/null; then
        echo "raspberrypi"
    elif [[ -f /etc/os-release ]]; then
        echo "ubuntu"
    else
        echo "unknown"
    fi
}

install_system_deps() {
    log "Installing system dependencies..."
    apt-get update -qq

    local pkgs=(python3 python3-pip python3-venv bluetooth bluez libbluetooth-dev)
    local platform
    platform=$(detect_platform)

    if [[ "$platform" == "wsl" ]]; then
        warn "WSL detected — Bluetooth hardware access requires usbipd or similar"
        warn "BLE scanning may not work inside WSL without USB passthrough"
        # WSL doesn't need bluez but we still install python
        pkgs=(python3 python3-pip python3-venv)
    fi

    apt-get install -y -qq "${pkgs[@]}" >/dev/null 2>&1
    log "System dependencies installed"
}

create_user() {
    if id "$SERVICE_USER" &>/dev/null; then
        log "User $SERVICE_USER already exists"
    else
        useradd --system --shell /usr/sbin/nologin --home-dir "$APP_DIR" "$SERVICE_USER"
        # Add to bluetooth group for BLE access
        usermod -aG bluetooth "$SERVICE_USER" 2>/dev/null || true
        log "Created user $SERVICE_USER"
    fi
}

install_app() {
    local src_dir
    src_dir="$(cd "$(dirname "$0")" && pwd)"

    log "Installing to $APP_DIR..."

    mkdir -p "$APP_DIR"
    mkdir -p "$APP_DIR/templates"
    mkdir -p "$APP_DIR/captures"
    mkdir -p "$APP_DIR/static"
    mkdir -p "$APP_DIR/models"

    # Copy application files
    for f in template.py decoder.py ble_scanner.py mqtt_publisher.py \
             ble_mqtt_bridge.py bridge_ui.py decoder_standalone.py \
             requirements.txt; do
        if [[ -f "$src_dir/$f" ]]; then
            cp "$src_dir/$f" "$APP_DIR/"
        fi
    done

    # Copy templates
    if [[ -d "$src_dir/templates" ]]; then
        cp -r "$src_dir/templates/"* "$APP_DIR/templates/" 2>/dev/null || true
    fi

    # Copy static assets
    if [[ -d "$src_dir/static" ]]; then
        cp -r "$src_dir/static/"* "$APP_DIR/static/" 2>/dev/null || true
    fi

    # Copy config examples if real configs don't exist
    if [[ ! -f "$APP_DIR/bridge.conf" && -f "$src_dir/bridge.conf.example" ]]; then
        cp "$src_dir/bridge.conf.example" "$APP_DIR/bridge.conf"
        log "Created bridge.conf from example — edit before running"
    fi
    if [[ ! -f "$APP_DIR/devices.conf" && -f "$src_dir/devices.conf.example" ]]; then
        cp "$src_dir/devices.conf.example" "$APP_DIR/devices.conf"
        log "Created devices.conf from example — edit before running"
    fi

    # Create venv and install deps
    log "Setting up Python virtual environment..."
    $PYTHON -m venv "$APP_DIR/venv"
    "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
    "$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"

    # Fix ownership
    chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

    log "Application installed"
}

install_systemd_units() {
    local platform
    platform=$(detect_platform)

    if [[ "$platform" == "wsl" ]]; then
        warn "WSL detected — systemd may not be available"
        warn "Use 'wsl.conf' with systemd=true, or run manually:"
        warn "  $APP_DIR/venv/bin/python $APP_DIR/ble_mqtt_bridge.py"
        warn "  $APP_DIR/venv/bin/python $APP_DIR/bridge_ui.py"
        install_wsl_scripts
        return
    fi

    log "Installing systemd service units..."

    cat > /etc/systemd/system/${APP_NAME}.service <<UNIT
[Unit]
Description=BLE to MQTT Bridge
After=network.target bluetooth.service
Wants=network.target bluetooth.service

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP_DIR
# Unblock + power Bluetooth before start (some hosts bring it up RF-killed).
# Leading '+' runs as root, bypassing User= and the sandbox for this command.
ExecStartPre=+/bin/sh -c 'rfkill unblock bluetooth 2>/dev/null || for d in /sys/class/rfkill/rfkill*; do [ "\$(cat "\$d/type")" = bluetooth ] && echo 0 > "\$d/soft"; done; hciconfig hci0 up 2>/dev/null || true'
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/ble_mqtt_bridge.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$APP_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

    cat > /etc/systemd/system/${APP_NAME}-ui.service <<UNIT
[Unit]
Description=BLE to MQTT Bridge Web UI
After=network.target ${APP_NAME}.service

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/bridge_ui.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$APP_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

    cat > /etc/systemd/system/${APP_NAME}-decoder.service <<UNIT
[Unit]
Description=BLE Decoder Wizard (Standalone)
After=network.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $APP_DIR/decoder_standalone.py --port 5001
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$APP_DIR
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

    systemctl daemon-reload
    log "Systemd units installed"
}

install_wsl_scripts() {
    log "Creating startup scripts for WSL..."

    cat > "$APP_DIR/start-bridge.sh" <<'SCRIPT'
#!/bin/bash
cd /opt/ble-mqtt
./venv/bin/python ble_mqtt_bridge.py
SCRIPT

    cat > "$APP_DIR/start-ui.sh" <<'SCRIPT'
#!/bin/bash
cd /opt/ble-mqtt
echo "Web UI: http://localhost:5000"
./venv/bin/python bridge_ui.py
SCRIPT

    cat > "$APP_DIR/start-decoder.sh" <<'SCRIPT'
#!/bin/bash
cd /opt/ble-mqtt
echo "Decoder Wizard: http://localhost:5001"
./venv/bin/python decoder_standalone.py
SCRIPT

    chmod +x "$APP_DIR"/*.sh
    chown "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"/*.sh
    log "Startup scripts created in $APP_DIR/"
}

enable_services() {
    local platform
    platform=$(detect_platform)

    if [[ "$platform" == "wsl" ]]; then
        return
    fi

    log "Enabling services..."
    systemctl enable "${APP_NAME}.service"
    systemctl enable "${APP_NAME}-ui.service"
    # Decoder is optional, not enabled by default
    log "Services enabled (bridge + ui)"
}

print_summary() {
    local platform
    platform=$(detect_platform)

    echo ""
    echo "========================================="
    echo "  BLE→MQTT Bridge — Install Complete"
    echo "========================================="
    echo ""
    echo "  Platform:  $platform"
    echo "  Install:   $APP_DIR"
    echo "  Config:    $APP_DIR/bridge.conf"
    echo "  Devices:   $APP_DIR/devices.conf"
    echo "  Templates: $APP_DIR/templates/"
    echo ""

    if [[ "$platform" == "wsl" ]]; then
        echo "  WSL mode — run manually:"
        echo "    $APP_DIR/start-bridge.sh"
        echo "    $APP_DIR/start-ui.sh"
        echo "    $APP_DIR/start-decoder.sh"
    else
        echo "  Service commands:"
        echo "    systemctl start ${APP_NAME}        # Bridge"
        echo "    systemctl start ${APP_NAME}-ui      # Web UI (port 5000)"
        echo "    systemctl start ${APP_NAME}-decoder  # Decoder (port 5001)"
        echo ""
        echo "    systemctl status ${APP_NAME}"
        echo "    journalctl -u ${APP_NAME} -f         # Logs"
    fi
    echo ""
    echo "  Edit $APP_DIR/bridge.conf before starting!"
    echo ""
}

# ── Uninstall ──

uninstall() {
    require_root
    log "Uninstalling ${APP_NAME}..."

    systemctl stop "${APP_NAME}.service" 2>/dev/null || true
    systemctl stop "${APP_NAME}-ui.service" 2>/dev/null || true
    systemctl stop "${APP_NAME}-decoder.service" 2>/dev/null || true
    systemctl disable "${APP_NAME}.service" 2>/dev/null || true
    systemctl disable "${APP_NAME}-ui.service" 2>/dev/null || true
    systemctl disable "${APP_NAME}-decoder.service" 2>/dev/null || true

    rm -f /etc/systemd/system/${APP_NAME}.service
    rm -f /etc/systemd/system/${APP_NAME}-ui.service
    rm -f /etc/systemd/system/${APP_NAME}-decoder.service
    systemctl daemon-reload

    read -rp "Remove $APP_DIR and all data? [y/N] " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        rm -rf "$APP_DIR"
        userdel "$SERVICE_USER" 2>/dev/null || true
        log "Removed all files and user"
    else
        log "Kept $APP_DIR"
    fi

    log "Uninstalled"
}

# ── Main ──

usage() {
    echo "Usage: $0 {install|uninstall|status}"
    echo ""
    echo "  install    Install services and application"
    echo "  uninstall  Remove services (optionally data)"
    echo "  status     Show service status"
}

status() {
    local platform
    platform=$(detect_platform)
    echo "Platform: $platform"
    echo ""

    if [[ "$platform" != "wsl" ]]; then
        systemctl status "${APP_NAME}.service" --no-pager 2>/dev/null || echo "Bridge: not installed"
        echo ""
        systemctl status "${APP_NAME}-ui.service" --no-pager 2>/dev/null || echo "UI: not installed"
    else
        echo "WSL — check processes manually"
        pgrep -fa "ble_mqtt_bridge" || echo "Bridge: not running"
        pgrep -fa "bridge_ui" || echo "UI: not running"
    fi
}

case "${1:-}" in
    install)
        require_root
        install_system_deps
        create_user
        install_app
        install_systemd_units
        enable_services
        print_summary
        ;;
    uninstall)
        uninstall
        ;;
    status)
        status
        ;;
    *)
        usage
        exit 1
        ;;
esac
