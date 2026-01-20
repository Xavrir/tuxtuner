#!/bin/bash
# TuxTuner Installation Script

set -euo pipefail

PREFIX="${PREFIX:-/usr/local}"

if [[ "$PREFIX" =~ [^a-zA-Z0-9/_-] ]]; then
    echo "ERROR: PREFIX contains invalid characters. Only alphanumeric, '/', '_', '-' allowed."
    exit 1
fi

BINDIR="$PREFIX/bin"
LIBEXECDIR="$PREFIX/libexec"
POLKIT_DIR="/usr/share/polkit-1/actions"

echo "TuxTuner Installer"
echo "=================="
echo ""

check_dep() {
    if ! command -v "$1" &> /dev/null; then
        echo "ERROR: $1 is required but not installed."
        exit 1
    fi
}

echo "Checking dependencies..."
check_dep python3
check_dep pkexec
check_dep hyprctl

python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')" 2>/dev/null || {
    echo "ERROR: python-gobject and libadwaita are required."
    echo "Install with: sudo pacman -S python-gobject libadwaita"
    exit 1
}

echo "Dependencies OK"
echo ""

echo "Installing to $PREFIX..."

sudo mkdir -p "$BINDIR" "$LIBEXECDIR"

sudo cp src/tuxtuner.py "$BINDIR/tuxtuner"
sudo chmod +x "$BINDIR/tuxtuner"

sudo cp src/tuxtuner-helper "$LIBEXECDIR/tuxtuner-helper"
sudo chmod +x "$LIBEXECDIR/tuxtuner-helper"
sudo chmod 755 "$LIBEXECDIR/tuxtuner-helper"

ESCAPED_PATH=$(printf '%s\n' "$LIBEXECDIR/tuxtuner-helper" | sed 's/[&/\]/\\&/g')
sudo sed -i "s|HELPER_PATH = .*|HELPER_PATH = \"$ESCAPED_PATH\"|" "$BINDIR/tuxtuner"

echo "Installing polkit policy..."
sudo mkdir -p "$POLKIT_DIR"

POLICY_FILE="$POLKIT_DIR/com.github.xavrir.tuxtuner.policy"
if [[ -f "data/com.github.xavrir.tuxtuner.policy" ]]; then
    sudo cp "data/com.github.xavrir.tuxtuner.policy" "$POLICY_FILE"
    
    sudo sed -i "s|/usr/local/libexec/tuxtuner-helper|$LIBEXECDIR/tuxtuner-helper|g" "$POLICY_FILE"
    
    echo "Polkit policy installed."
else
    echo "WARNING: Polkit policy file not found in data/. Skipping."
fi

echo ""
echo "Installation complete!"
echo ""
echo "Run with: tuxtuner"
echo ""
echo "Optional: For supergfxctl GPU switching, install asusctl/supergfxctl"
