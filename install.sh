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

# Probed once at startup; every caller reads $PLATFORM instead of re-running it.
PLATFORM="$(detect_platform)"

install_system_deps() {
    log "Installing system dependencies..."
    apt-get update -qq

    local pkgs=(python3 python3-pip python3-venv bluetooth bluez libbluetooth-dev)

    if [[ "$PLATFORM" == "wsl" ]]; then
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

    # Copy application files
    for f in template.py ble_scanner.py mqtt_publisher.py \
             ble_mqtt_bridge.py bridge_ui.py ui_fragments.py decoder_standalone.py \
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

write_unit() {
    # Everything the three units share lives here. The four varying fields are
    # positional; the three optional trailing arguments cover the only places a
    # single unit differs, and emit nothing at all when left empty:
    #   $5  extra [Unit] lines           — bridge only (Wants=)
    #   $6  extra [Service] lines        — bridge only (the ExecStartPre= block)
    #   $7  comment above the hardening block — bridge and ui only
    local unit_name="$1" description="$2" after="$3" exec_start="$4"
    local unit_extra="${5:-}" service_extra="${6:-}" hardening_comment="${7:-}"

    {
        printf '[Unit]\n'
        printf 'Description=%s\n' "$description"
        printf 'After=%s\n' "$after"
        if [[ -n "$unit_extra" ]]; then printf '%s\n' "$unit_extra"; fi
        printf '\n[Service]\n'
        printf 'Type=simple\n'
        printf 'User=%s\n' "$SERVICE_USER"
        printf 'Group=%s\n' "$SERVICE_USER"
        printf 'WorkingDirectory=%s\n' "$APP_DIR"
        if [[ -n "$service_extra" ]]; then printf '%s\n' "$service_extra"; fi
        printf 'ExecStart=%s\n' "$exec_start"
        printf 'Restart=on-failure\n'
        printf 'RestartSec=10\n'
        printf 'StandardOutput=journal\n'
        printf 'StandardError=journal\n'
        printf '\n'
        if [[ -n "$hardening_comment" ]]; then printf '%s\n' "$hardening_comment"; fi
        printf 'NoNewPrivileges=true\n'
        printf 'ProtectSystem=strict\n'
        printf 'ProtectHome=true\n'
        printf 'ReadWritePaths=%s\n' "$APP_DIR"
        printf 'PrivateTmp=true\n'
        printf '\n[Install]\n'
        printf 'WantedBy=multi-user.target\n'
    } > "/etc/systemd/system/$unit_name"
}

install_systemd_units() {
    if [[ "$PLATFORM" == "wsl" ]]; then
        warn "WSL detected — systemd may not be available"
        warn "Use 'wsl.conf' with systemd=true, or run manually:"
        warn "  $APP_DIR/venv/bin/python $APP_DIR/ble_mqtt_bridge.py"
        warn "  $APP_DIR/venv/bin/python $APP_DIR/bridge_ui.py"
        install_wsl_scripts
        return
    fi

    log "Installing systemd service units..."

    # Quoted heredoc delimiter: the body is taken literally, so the '$' signs and
    # the nested quoting in the shell one-liner reach the unit file untouched.
    local bridge_service_extra
    bridge_service_extra=$(cat <<'PRE'
# Unblock + power Bluetooth before start (some hosts bring it up RF-killed).
# Leading '+' runs as root, bypassing User= and the sandbox for this command.
ExecStartPre=+/bin/sh -c 'rfkill unblock bluetooth 2>/dev/null || for d in /sys/class/rfkill/rfkill*; do [ "$(cat "$d/type")" = bluetooth ] && echo 0 > "$d/soft"; done; hciconfig hci0 up 2>/dev/null || true'
PRE
)

    write_unit "${APP_NAME}.service" \
        "BLE to MQTT Bridge" \
        "network.target bluetooth.service" \
        "$APP_DIR/venv/bin/python $APP_DIR/ble_mqtt_bridge.py" \
        "Wants=network.target bluetooth.service" \
        "$bridge_service_extra" \
        "# Hardening"

    write_unit "${APP_NAME}-ui.service" \
        "BLE to MQTT Bridge Web UI" \
        "network.target ${APP_NAME}.service" \
        "$APP_DIR/venv/bin/python $APP_DIR/bridge_ui.py" \
        "" \
        "" \
        "# Hardening"

    write_unit "${APP_NAME}-decoder.service" \
        "BLE Decoder Wizard (Standalone)" \
        "network.target" \
        "$APP_DIR/venv/bin/python $APP_DIR/decoder_standalone.py --port 5001"

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
    if [[ "$PLATFORM" == "wsl" ]]; then
        return
    fi

    log "Enabling services..."
    systemctl enable "${APP_NAME}.service"
    systemctl enable "${APP_NAME}-ui.service"
    # Decoder is optional, not enabled by default
    log "Services enabled (bridge + ui)"
}

print_summary() {
    echo ""
    echo "========================================="
    echo "  BLE→MQTT Bridge — Install Complete"
    echo "========================================="
    echo ""
    echo "  Platform:  $PLATFORM"
    echo "  Install:   $APP_DIR"
    echo "  Config:    $APP_DIR/bridge.conf"
    echo "  Devices:   $APP_DIR/devices.conf"
    echo "  Templates: $APP_DIR/templates/"
    echo ""

    if [[ "$PLATFORM" == "wsl" ]]; then
        echo "  WSL mode — run manually:"
        echo "    $APP_DIR/start-bridge.sh"
        echo "    $APP_DIR/start-ui.sh"
        echo "    $APP_DIR/start-decoder.sh"
    else
        echo "  Service commands:"
        echo "    systemctl start ${APP_NAME}        # Bridge"
        echo "    systemctl start ${APP_NAME}-ui      # Web UI (port 5000)"
        echo "    systemctl start ${APP_NAME}-decoder  # Decoder (port 5001, not enabled at boot)"
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

    local unit
    for unit in "${APP_NAME}.service" "${APP_NAME}-ui.service" "${APP_NAME}-decoder.service"; do
        systemctl stop "$unit" 2>/dev/null || true
        systemctl disable "$unit" 2>/dev/null || true
        rm -f "/etc/systemd/system/$unit"
    done
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
    echo "Platform: $PLATFORM"
    echo ""

    if [[ "$PLATFORM" != "wsl" ]]; then
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
