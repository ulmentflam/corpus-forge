#!/usr/bin/env bash
# Stop script for corpus-forge daemon

set -euo pipefail

echo "Stopping corpus-forge daemon..."

# Reverse-DNS prefix for the launchd label.
# Must match the value used at install time. Override via env var.
REVERSE_DNS="${REVERSE_DNS:-com.${USER}}"
LABEL="${REVERSE_DNS}.corpus-forge"

# Stop launchd service
launchctl kill SIGTERM "gui/$(id -u)/${LABEL}" 2>/dev/null || true
launchctl unload "${HOME}/Library/LaunchAgents/${LABEL}.plist" 2>/dev/null || true

# Also stop any running uv processes (in case daemon is running in foreground)
pkill -f "uv run corpus-forge daemon" 2>/dev/null || true
pkill -f "corpus-forge daemon" 2>/dev/null || true

echo "Corpus-forge daemon stopped."
