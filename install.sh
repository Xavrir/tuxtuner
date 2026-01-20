#!/bin/bash
# TuxTuner Installation Script

set -e

PREFIX="${PREFIX:-/usr/local}"
BINDIR="$PREFIX/bin"
LIBEXECDIR="$PREFIX/libexec"

echo "TuxTuner Installer"
echo "=================="
echo ""

# Check dependencies
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

# Check Python GTK bindings
python3 -c "import gi; gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')" 2>/dev/null || {
    echo "ERROR: python-gobject and libadwaita are required."
    echo "Install with: sudo pacman -S python-gobject libadwaita"
    exit 1
}

echo "Dependencies OK"
echo ""

# Install files
echo "Installing to $PREFIX..."

sudo mkdir -p "$BINDIR" "$LIBEXECDIR"

# Main application
sudo cp src/tuxtuner.py "$BINDIR/tuxtuner"
sudo chmod +x "$BINDIR/tuxtuner"

# Helper script (privileged operations)
sudo cp src/tuxtuner-helper "$LIBEXECDIR/tuxtuner-helper"
sudo chmod +x "$LIBEXECDIR/tuxtuner-helper"

# Update helper path in main script
sudo sed -i "s|HELPER_PATH = .*|HELPER_PATH = \"$LIBEXECDIR/tuxtuner-helper\"|" "$BINDIR/tuxtuner"

echo ""
echo "Installation complete!"
echo ""
echo "Run with: tuxtuner"
echo ""
echo "Optional: For supergfxctl GPU switching, install asusctl/supergfxctl"
