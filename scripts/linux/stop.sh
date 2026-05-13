#!/usr/bin/env bash
# Stop the corpus-forge systemd user unit on Linux.

set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-corpus-forge}"

echo "Stopping ${SERVICE_NAME}.service..."
systemctl --user stop "${SERVICE_NAME}.service" || true

# Also kill any foreground instances (e.g. `corpus-forge daemon` started by hand).
pkill -f "corpus-forge daemon" 2>/dev/null || true

echo "corpus-forge daemon stopped."
