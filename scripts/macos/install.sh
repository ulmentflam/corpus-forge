#!/usr/bin/env bash
# Installation script for corpus-forge

set -euo pipefail

echo "Installing corpus-forge..."

# Change to repo root (scripts/macos/install.sh → repo root is two levels up).
cd "$(dirname "$0")/../.."

# Reverse-DNS prefix for the launchd label.
# Override by exporting REVERSE_DNS before running this script.
# Example: REVERSE_DNS=org.example ./scripts/macos/install.sh
REVERSE_DNS="${REVERSE_DNS:-com.${USER}}"
LABEL="${REVERSE_DNS}.corpus-forge"

echo "Using launchd label: ${LABEL}"

# Install dependencies with uv
echo "Installing dependencies..."
uv sync --all-extras --group dev

# Install pre-commit hooks
echo "Installing pre-commit hooks..."
uv run pre-commit install

# Create launchd service directory if it doesn't exist
mkdir -p ~/Library/LaunchAgents

# Render launchd plist from the generic template
PLIST_TEMPLATE="packaging/corpus-forge.plist.template"
PLIST_TARGET="${HOME}/Library/LaunchAgents/${LABEL}.plist"

if [[ -f "$PLIST_TEMPLATE" ]]; then
    echo "Rendering launchd plist..."
    sed \
      -e "s|__REVERSE_DNS__|${REVERSE_DNS}|g" \
      -e "s|__EXECUTABLE_PATH__|$(pwd)|g" \
      -e "s|__HOME__|${HOME}|g" \
      "$PLIST_TEMPLATE" > "$PLIST_TARGET"

    echo "Launchd plist created at ${PLIST_TARGET}"
    echo "To start the service, run: launchctl load ${PLIST_TARGET}"
    echo "To start it now, run: launchctl kickstart -k gui/$(id -u)/${LABEL}"
else
    echo "Warning: Launchd plist template not found at ${PLIST_TEMPLATE}"
fi

echo ""
echo "Installation complete!"
echo ""
echo "Next steps:"
echo "1. Copy config.example.toml to ~/.config/corpus-forge/config.toml and edit it"
echo "2. Copy secrets.env.example to ~/.config/corpus-forge/secrets.env and fill in values"
echo "3. Run 'make migrate' to apply database schema"
echo "4. Run 'make ingest --once' to test ingestion"
echo "5. Start the daemon with 'make daemon' or load the launchd service"
