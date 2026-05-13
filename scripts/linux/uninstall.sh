#!/usr/bin/env bash
# Linux uninstaller for corpus-forge — removes the systemd user unit.

set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-corpus-forge}"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_TARGET="${SERVICE_DIR}/${SERVICE_NAME}.service"

echo "Uninstalling ${SERVICE_NAME}.service..."

# Disable + stop the unit (safe if it's not running / not installed).
systemctl --user disable --now "${SERVICE_NAME}.service" 2>/dev/null || true

# Remove the rendered unit file and reload systemd so journalctl forgets it.
rm -f "${SERVICE_TARGET}"
systemctl --user daemon-reload || true

# Kill any orphaned foreground instances.
pkill -f "corpus-forge daemon" 2>/dev/null || true
pkill -f "uv run corpus-forge" 2>/dev/null || true

echo "Uninstallation complete!"
echo ""
echo "Note: this only removes the systemd unit. The corpus-forge package"
echo "      itself is uninstalled with 'pip uninstall corpus-forge'."
