#!/usr/bin/env bash
# Install OBI Linux on Pop!_OS / Ubuntu / Mint / Debian.
#
# Run it from the unpacked release directory, or from a checkout after
# building the binaries with `make binary`. Re-running is safe.
#
#   sudo ./install.sh              install (or upgrade)
#   sudo ./install.sh --uninstall  remove everything it installed
#
# PREFIX=/opt sudo -E ./install.sh    install somewhere else

set -euo pipefail

APP_NAME="obi-linux"
CLI_NAME="obi-log"

PREFIX="${PREFIX:-/usr/local}"
BIN_DIR="$PREFIX/bin"
APPS_DIR="$PREFIX/share/applications"
ICONS_DIR="$PREFIX/share/icons/hicolor/512x512/apps"
UDEV_DIR="${UDEV_DIR:-/etc/udev/rules.d}"
UDEV_RULE="90-arduino-obi.rules"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
    sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

resolve_asset() {
    local name="$1" candidate
    for candidate in \
        "$SCRIPT_DIR/$name" \
        "$SCRIPT_DIR/../$name" \
        "$SCRIPT_DIR/../linux/$name" \
        "$SCRIPT_DIR/../OpenBatteryInformation/$name" \
        "$SCRIPT_DIR/../OpenBatteryInformation/dist/$name"
    do
        if [[ -f "$candidate" ]]; then
            echo "$candidate"
            return
        fi
    done
    echo ""
}

require_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        echo "This needs root privileges. Re-running with sudo..."
        exec sudo --preserve-env=PREFIX,UDEV_DIR "$0" "$@"
    fi
}

refresh_caches() {
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APPS_DIR" || true
    fi
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "$PREFIX/share/icons/hicolor" || true
    fi
}

uninstall() {
    require_root "$@"
    echo "Removing OBI Linux from $PREFIX"
    rm -f "$BIN_DIR/$APP_NAME" "$BIN_DIR/$CLI_NAME"
    rm -f "$APPS_DIR/$APP_NAME.desktop"
    rm -f "$ICONS_DIR/$APP_NAME.png"
    rm -f "$UDEV_DIR/$UDEV_RULE"
    if command -v udevadm >/dev/null 2>&1; then
        udevadm control --reload-rules || true
        udevadm trigger || true
    fi
    refresh_caches
    echo "Done. Your logs in ~/.local/share/obi-linux were left untouched."
}

install_all() {
    local binary icon desktop udev cli
    binary="$(resolve_asset "$APP_NAME")"
    cli="$(resolve_asset "$CLI_NAME")"
    icon="$(resolve_asset icon.png)"
    desktop="$(resolve_asset "$APP_NAME.desktop")"
    udev="$(resolve_asset "$UDEV_RULE")"

    if [[ -z "$binary" && -z "$cli" ]]; then
        echo "Error: found neither '$APP_NAME' nor '$CLI_NAME' next to this script." >&2
        echo "Unpack the release tarball and run install.sh from inside it, or build" >&2
        echo "them first from a checkout:" >&2
        echo "    cd OpenBatteryInformation" >&2
        echo "    pyinstaller obi-linux.spec && pyinstaller obi-log.spec" >&2
        exit 1
    fi

    require_root "$@"

    echo "Installing OBI Linux to $PREFIX"
    install -d "$BIN_DIR" "$APPS_DIR" "$ICONS_DIR"

    if [[ -n "$binary" ]]; then
        install -m 0755 "$binary" "$BIN_DIR/$APP_NAME"
        echo "  $BIN_DIR/$APP_NAME"
    fi
    if [[ -n "$cli" ]]; then
        install -m 0755 "$cli" "$BIN_DIR/$CLI_NAME"
        echo "  $BIN_DIR/$CLI_NAME"
    fi
    if [[ -n "$icon" ]]; then
        install -m 0644 "$icon" "$ICONS_DIR/$APP_NAME.png"
    fi
    if [[ -n "$desktop" ]]; then
        install -m 0644 "$desktop" "$APPS_DIR/$APP_NAME.desktop"
    fi

    if [[ -n "$udev" ]]; then
        install -m 0644 "$udev" "$UDEV_DIR/$UDEV_RULE"
        echo "  $UDEV_DIR/$UDEV_RULE"
        if command -v udevadm >/dev/null 2>&1; then
            udevadm control --reload-rules || true
            udevadm trigger || true
        fi
    else
        echo "Warning: $UDEV_RULE not found; you may need to add yourself to the" >&2
        echo "'dialout' group to access the serial port." >&2
    fi

    refresh_caches

    local real_user
    real_user="${SUDO_USER:-${USER:-root}}"
    if getent group plugdev >/dev/null 2>&1; then
        if ! id -nG "$real_user" 2>/dev/null | tr ' ' '\n' | grep -qx plugdev; then
            echo "Adding user '$real_user' to the plugdev group for serial port access..."
            usermod -aG plugdev "$real_user" || true
            echo "  -> log out and back in for this to take effect"
        fi
    fi

    echo
    echo "Done."
    echo "  Launch from the application menu, or run: $APP_NAME"
    echo "  Log a battery over time with:             $CLI_NAME --interval 60"
}

case "${1:-}" in
    -h|--help)   usage ;;
    --uninstall) uninstall "$@" ;;
    "")          install_all ;;
    *)           echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
esac
