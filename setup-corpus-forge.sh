#!/usr/bin/env bash
# Contributor clone-and-run setup for corpus-forge.
#
# Differs from ``install.sh`` (the end-user one-liner) in three ways:
#
#   1. Runs ``uv sync --all-extras --group dev --locked`` for hash-
#      verified install of every transitive plus the dev tool-chain
#      (pre-commit, pytest plug-ins, ruff, pyrefly). Falls back to a
#      curated safe-set when the lockfile sync fails (e.g. a
#      quarantined transitive) so contributors aren't blocked by an
#      unrelated upstream incident.
#   2. Drops a pre-commit hook so commits run the same lint/format
#      gate the CI matrix does.
#   3. Does NOT install via ``uv tool`` — leaves the source repo as
#      the editable install so contributors can iterate.
#
# End users should use ``install.sh`` instead. The two scripts share
# nothing on disk to keep concerns separated.

set -euo pipefail

# ---------------------------------------------------------------------------
# Colours
# ---------------------------------------------------------------------------

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    GREEN='\033[0;32m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'
    RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
else
    GREEN='' YELLOW='' CYAN='' RED='' BOLD='' NC=''
fi

info()  { printf '%b\n' "${CYAN}→${NC} $*"; }
ok()    { printf '%b\n' "${GREEN}✓${NC} $*"; }
warn()  { printf '%b\n' "${YELLOW}⚠${NC} $*"; }
fail()  { printf '%b\n' "${RED}✗${NC} $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
cd "$SCRIPT_DIR"

echo
printf '%b\n' "${BOLD}corpus-forge contributor setup${NC}"
echo

# ---------------------------------------------------------------------------
# Sanity: this is a git clone, not a tarball.
# ---------------------------------------------------------------------------

if [ ! -d ".git" ]; then
    warn ".git not found — proceeding, but pre-commit hook install will be skipped."
fi

if [ ! -f "pyproject.toml" ]; then
    fail "pyproject.toml missing. Did you ``git clone`` the corpus-forge repo?"
fi

# ---------------------------------------------------------------------------
# uv discovery (same logic as install.sh).
# ---------------------------------------------------------------------------

UV_CMD=""
if command -v uv >/dev/null 2>&1; then
    UV_CMD="uv"
elif [ -x "$HOME/.local/bin/uv" ]; then
    UV_CMD="$HOME/.local/bin/uv"
elif [ -x "$HOME/.cargo/bin/uv" ]; then
    UV_CMD="$HOME/.cargo/bin/uv"
fi

if [ -n "$UV_CMD" ]; then
    ok "uv found ($("$UV_CMD" --version 2>/dev/null))"
else
    info "Installing uv (Astral)"
    if ! curl -LsSf https://astral.sh/uv/install.sh | sh; then
        fail "uv installer failed. Install manually: https://docs.astral.sh/uv/"
    fi
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then UV_CMD="$candidate"; break; fi
    done
    [ -n "$UV_CMD" ] || fail "uv installed but binary not found on \$PATH."
    ok "uv installed ($("$UV_CMD" --version 2>/dev/null))"
fi

# ---------------------------------------------------------------------------
# Dependency install — three-tier fallback ladder:
#
#   1. ``uv sync --all-extras --group dev --locked`` — hash-verified
#      install pinned to ``uv.lock``. Preferred. Protects against a
#      compromised transitive (would have a mismatched hash and be
#      REJECTED by uv).
#   2. ``uv sync --all-extras --group dev`` — same set but re-resolved
#      from PyPI. Hash verification skipped; gets you unblocked when
#      ``uv.lock`` is stale or out-of-tree.
#   3. ``uv sync --group dev`` (no extras) — last resort when a single
#      bad transitive in an optional extra blocks everything. Lets
#      contributors at least run the core test suite.
#
# Inspired by Hermes Agent's ``_BROKEN_EXTRAS`` quarantine pattern —
# we don't need a hard-coded list; the cascade self-resolves.
# ---------------------------------------------------------------------------

info "Syncing dependencies (preferred: hash-verified via uv.lock)"
if "$UV_CMD" sync --all-extras --group dev --locked; then
    ok "Dependencies installed (hash-verified)"
elif "$UV_CMD" sync --all-extras --group dev; then
    warn "Lockfile sync failed; transitives re-resolved fresh from PyPI (not hash-verified)"
    ok "Dependencies installed"
elif "$UV_CMD" sync --group dev; then
    warn "Optional extras failed to install; only the core dev tool-chain is available."
    warn "Inspect ``uv sync --all-extras --group dev`` output to debug which extra is the culprit."
    ok "Core dependencies installed"
else
    fail "All three dependency-install tiers failed. Check ``uv sync`` output."
fi

# ---------------------------------------------------------------------------
# pre-commit hook
# ---------------------------------------------------------------------------

if [ -d ".git" ] && [ -f ".pre-commit-config.yaml" ]; then
    info "Installing pre-commit hook"
    if "$UV_CMD" run pre-commit install >/dev/null 2>&1; then
        ok "pre-commit hook installed"
    else
        warn "pre-commit install failed — run ``uv run pre-commit install`` manually"
    fi
fi

# ---------------------------------------------------------------------------
# Done — print next steps.
# ---------------------------------------------------------------------------

echo
ok "Setup complete."
echo
echo "Next steps:"
echo
echo "  - Run the test suite:    ${BOLD}make ci${NC}"
echo "  - Run just unit tests:   ${BOLD}make test-unit${NC}"
echo "  - Open a feature branch: ${BOLD}git checkout -b feat/<topic>${NC}"
echo
echo "The CLI is available as:"
echo "  ${BOLD}$UV_CMD run corpus-forge --help${NC}"
echo
echo "Or, to configure a personal corpus (drops ~/.config/corpus-forge/{config,secrets}.env):"
echo "  ${BOLD}$UV_CMD run corpus-forge setup${NC}"
echo
