#!/usr/bin/env bash
# Linux installer for corpus-forge — registers a systemd user unit.
#
# Idempotent: rendering and `daemon-reload + enable --now` are safe to
# re-run. Override defaults by exporting:
#   CORPUS_FORGE_BIN  – absolute path to the corpus-forge entrypoint
#                       (default: ~/.local/bin/corpus-forge)
#   SERVICE_NAME      – unit name (default: corpus-forge)

set -euo pipefail

echo "Installing corpus-forge (Linux / systemd user unit)..."

# Change to repo root (scripts/linux/install.sh → repo root is two levels up).
cd "$(dirname "$0")/../.."

SERVICE_NAME="${SERVICE_NAME:-corpus-forge}"
CORPUS_FORGE_BIN="${CORPUS_FORGE_BIN:-${HOME}/.local/bin/corpus-forge}"
SERVICE_TEMPLATE="packaging/corpus-forge.service.template"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_TARGET="${SERVICE_DIR}/${SERVICE_NAME}.service"

if [[ ! -f "$SERVICE_TEMPLATE" ]]; then
    echo "Error: service template not found at ${SERVICE_TEMPLATE}" >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "Error: systemctl not on PATH; this installer requires systemd." >&2
    echo "On non-systemd distros, run 'corpus-forge daemon' under your supervisor of choice." >&2
    exit 2
fi

mkdir -p "$SERVICE_DIR"

# Render the unit file. The template uses systemd's `%h` substitution for
# $HOME natively, so we only swap the ExecStart binary path if the caller
# overrode it.
echo "Rendering systemd user unit to ${SERVICE_TARGET}..."
if [[ "$CORPUS_FORGE_BIN" == "${HOME}/.local/bin/corpus-forge" ]]; then
    # Default — keep %h/.local/bin/corpus-forge intact so systemd resolves it.
    cp "$SERVICE_TEMPLATE" "$SERVICE_TARGET"
else
    sed -e "s|%h/.local/bin/corpus-forge|${CORPUS_FORGE_BIN}|g" \
        "$SERVICE_TEMPLATE" > "$SERVICE_TARGET"
fi

echo "Reloading systemd user daemon..."
systemctl --user daemon-reload

echo "Enabling and starting ${SERVICE_NAME}.service..."
systemctl --user enable --now "${SERVICE_NAME}.service"

echo ""
echo "Installation complete!"
echo ""
echo "Useful commands:"
echo "  systemctl --user status ${SERVICE_NAME}.service"
echo "  journalctl --user -u ${SERVICE_NAME}.service -f"
echo "  systemctl --user restart ${SERVICE_NAME}.service"
echo ""
echo "Next steps:"
echo "1. Copy config.example.toml to ~/.config/corpus-forge/config.toml and edit it"
echo "2. Copy secrets.env.example to ~/.config/corpus-forge/secrets.env and fill in values"
echo "3. Run 'corpus-forge migrate' to apply database schema"
