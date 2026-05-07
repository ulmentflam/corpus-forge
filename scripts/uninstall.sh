#!/usr/bin/env bash
# Uninstallation script for corpus-forge

set -euo pipefail

echo "Uninstalling corpus-forge..."

# Reverse-DNS prefix for the launchd label.
# Must match the value used at install time. Override via env var.
REVERSE_DNS="${REVERSE_DNS:-com.${USER}}"
LABEL="${REVERSE_DNS}.corpus-forge"

# Stop and remove launchd service
echo "Removing launchd service (${LABEL})..."
launchctl unload "${HOME}/Library/LaunchAgents/${LABEL}.plist" 2>/dev/null || true
rm -f "${HOME}/Library/LaunchAgents/${LABEL}.plist"

# Stop any running processes
echo "Stopping running processes..."
pkill -f "uv run corpus-forge" 2>/dev/null || true
pkill -f "corpus-forge" 2>/dev/null || true

# Remove Python virtual environment and caches
echo "Cleaning up Python environments and caches..."
rm -rf .venv
rm -rf .pytest_cache .ruff_cache .coverage htmlcov
rm -rf dist build site
find . -type d -name __pycache__ -exec rm -rf {} +

echo "Uninstallation complete!"
