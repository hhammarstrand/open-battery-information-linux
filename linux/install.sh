#!/usr/bin/env bash
# Install OBI Linux on Pop!_OS / Ubuntu / Debian-based distros.
#
# Run from the directory containing the unpacked binary, or from a checkout of
# the repository. Re-running the script is safe; it overwrites previous installs.

set -euo pipefail

APP_NAME="obi-linux"

PREFIX="${PREFIX:-/usr/local}"
BIN_DIR="$PREFIX/bin"
SHARE_DIR="$PREFIX/share/$APP_NAME"
APPS_DIR="$PREFIX/share/applications"
ICONS_DIR="$PREFIX/share/icons/hicolor/512x512/apps"
UDEV_DIR="/etc/udev/rules.d"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

resolve_asset() {
    local name="$1"
    if [[ -f "$SCRIPT_DIR/$name" ]]; then
        echo "$SCRIPT_DIR/$name"
    elif [[ -f "$SCRIPT_DIR/../$name" ]]; then
        echo "$SCRIPT_DIR/../$name"
    elif [[ -f "$SCRIPT_DIR/../linux/$name" ]]; then
        echo "$SCRIPT_DIR/../linux/$name"
    elif [[ -f "$SCRIPT_DIR/../OpenBatteryInformation/$name" ]]; then
        echo "$SCRIPT_DIR/../OpenBatteryInformation/$name"
    elif [[ -f "$SCRIPT_DIR/../OpenBatteryInformation/dist/$name" ]]; then
        echo "$SCRIPT_DIR/../OpenBatteryInformation/dist/$name"
    else
        echo ""
    fi
}

require_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "This step needs root privileges. Re-running with sudo..."
        exec sudo --preserve-env=PREFIX "$0" "$@"
    fi
}

BINARY="$(resolve_asset "$APP_NAME")"
ICON="$(resolve_asset icon.png)"
DESKTOP="$(resolve_asset "$APP_NAME.desktop")"
UDEV="$(resolve_asset 90-arduino-obi.rules)"

if [[ -z "$BINARY" ]]; then
    echo "Error: cannot find the '$APP_NAME' binary next to this script." >&2
    echo "Run install.sh from the unpacked release directory, or build it first" >&2
    echo "with: cd OpenBatteryInformation && pyinstaller obi-linux.spec" >&2
    exit 1
fi

require_root "$@"

echo "Installing OBI Linux to $PREFIX"
install -d "$BIN_DIR" "$SHARE_DIR" "$APPS_DIR" "$ICONS_DIR"

install -m 0755 "$BINARY" "$BIN_DIR/$APP_NAME"

if [[ -n "$ICON" ]]; then
    install -m 0644 "$ICON" "$ICONS_DIR/$APP_NAME.png"
fi

if [[ -n "$DESKTOP" ]]; then
    install -m 0644 "$DESKTOP" "$APPS_DIR/$APP_NAME.desktop"
fi

if [[ -n "$UDEV" ]]; then
    install -m 0644 "$UDEV" "$UDEV_DIR/90-arduino-obi.rules"
    udevadm control --reload-rules
    udevadm trigger
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS_DIR" || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$PREFIX/share/icons/hicolor" || true
fi

REAL_USER="${SUDO_USER:-$USER}"
if ! id -nG "$REAL_USER" | grep -qw plugdev; then
    echo "Adding user '$REAL_USER' to the plugdev group for serial port access..."
    usermod -aG plugdev "$REAL_USER" || true
    echo "Log out and back in for the group change to take effect."
fi

echo "Done. Launch from your application menu or run: $APP_NAME"
