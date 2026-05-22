#!/usr/bin/env bash
# corpus-forge installer for POSIX (macOS / Linux / WSL / Termux-friendly).
#
# Typical install:
#
#     curl -sSf https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.sh | sh
#
# Or, after `git clone`:
#
#     ./install.sh
#
# This script:
#
#   1. Provisions ``uv`` if not already on PATH.
#   2. Walks ``packaging/install/questions.toml`` and prompts the user
#      (or reads ``CF_*`` env vars in non-interactive mode).
#   3. Installs ``corpus-forge`` via ``uv tool install`` with the
#      selected pip extras.
#   4. Hands off to ``corpus-forge setup`` (Python wizard) to render
#      ``~/.config/corpus-forge/config.toml`` + ``secrets.env``.
#
# Non-interactive (CI) mode:
#
#     CF_NON_INTERACTIVE=1 CF_BACKEND=sqlite CF_MULTI_FORMAT=yes \
#         CF_MCP=yes CF_HF=yes ./install.sh
#
# All ``CF_*`` env vars are documented in ``packaging/install/questions.toml``.

set -euo pipefail

# ---------------------------------------------------------------------------
# Colour output (only when stdout is a TTY).
# ---------------------------------------------------------------------------

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    CYAN='\033[0;36m'
    RED='\033[0;31m'
    BOLD='\033[1m'
    NC='\033[0m'
else
    GREEN='' YELLOW='' CYAN='' RED='' BOLD='' NC=''
fi

info()  { printf '%b\n' "${CYAN}→${NC} $*"; }
ok()    { printf '%b\n' "${GREEN}✓${NC} $*"; }
warn()  { printf '%b\n' "${YELLOW}⚠${NC} $*"; }
fail()  { printf '%b\n' "${RED}✗${NC} $*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# Locate the question tree.  Two cases:
#
#   - curl-pipe-bash: questions.toml isn't on disk; we fetch it from the
#     same release tag the install.sh was served from.
#   - cloned-repo:   questions.toml is at ./packaging/install/questions.toml.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd 2>/dev/null || pwd)"
QUESTIONS_REMOTE="${CF_QUESTIONS_URL:-https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/corpus_forge/setup/questions.toml}"

if [ -f "$SCRIPT_DIR/corpus_forge/setup/questions.toml" ]; then
    QUESTIONS_PATH="$SCRIPT_DIR/corpus_forge/setup/questions.toml"
    info "Using local question tree: $QUESTIONS_PATH"
else
    QUESTIONS_PATH="$(mktemp -t corpus-forge-questions.XXXXXX.toml 2>/dev/null || mktemp)"
    info "Fetching question tree from $QUESTIONS_REMOTE"
    if ! curl -fsSL "$QUESTIONS_REMOTE" -o "$QUESTIONS_PATH"; then
        fail "Failed to download $QUESTIONS_REMOTE — check your network and retry."
    fi
fi

# ---------------------------------------------------------------------------
# Provision uv.
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
    info "Installing uv (Astral) — official one-liner"
    # Capture installer output so a failure surfaces the actual reason
    # (network / glibc / disk full / missing curl) instead of an opaque
    # "✗ Failed to install uv".
    uv_log="$(mktemp 2>/dev/null || echo "/tmp/corpus-forge-uv-install.$$.log")"
    uv_installer="$(mktemp 2>/dev/null || echo "/tmp/corpus-forge-uv-installer.$$.sh")"
    if ! curl -LsSf https://astral.sh/uv/install.sh -o "$uv_installer" 2>"$uv_log"; then
        sed 's/^/    /' "$uv_log" >&2
        rm -f "$uv_log" "$uv_installer"
        fail "Failed to download uv installer. Install manually: https://docs.astral.sh/uv/"
    fi
    if ! sh "$uv_installer" >>"$uv_log" 2>&1; then
        sed 's/^/    /' "$uv_log" >&2
        rm -f "$uv_log" "$uv_installer"
        fail "uv installer exited non-zero. Output above."
    fi
    rm -f "$uv_installer"
    for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv"; do
        if [ -x "$candidate" ]; then
            UV_CMD="$candidate"
            break
        fi
    done
    [ -n "$UV_CMD" ] || {
        sed 's/^/    /' "$uv_log" >&2
        rm -f "$uv_log"
        fail "uv installer reported success but binary not found. Add ~/.local/bin to PATH and retry."
    }
    rm -f "$uv_log"
    ok "uv installed ($("$UV_CMD" --version 2>/dev/null))"
fi

# ---------------------------------------------------------------------------
# Question-tree parser (POSIX awk).  Reads TOML's [[question]] blocks
# and emits ID|TYPE|DEFAULT|EXTRAS|ENV|DEPENDS_ON|PROMPT|WARN lines.
#
# This is a deliberately narrow TOML subset — enough for our schema and
# nothing more.  The Python wizard re-uses ``tomllib`` for the same
# input so the schema is rich-tooling-friendly.
# ---------------------------------------------------------------------------

parse_questions() {
    awk '
    function strip_quotes(s) {
        gsub(/^"|"$/, "", s)
        return s
    }
    function value_of(line) {
        sub(/^[a-zA-Z_]+[[:space:]]*=[[:space:]]*/, "", line)
        sub(/[[:space:]]*$/, "", line)
        return strip_quotes(line)
    }
    BEGIN { in_block = 0 }
    /^\[\[question\]\]/ {
        if (in_block) {
            print id "|" type "|" default_v "|" extras_v "|" env_v "|" depends_v "|" prompt_v "|" warn_v
        }
        in_block = 1
        id = ""; type = ""; default_v = ""; extras_v = ""; env_v = ""
        depends_v = ""; prompt_v = ""; warn_v = ""
        next
    }
    /^\[/ {
        if (in_block) {
            print id "|" type "|" default_v "|" extras_v "|" env_v "|" depends_v "|" prompt_v "|" warn_v
            in_block = 0
        }
        next
    }
    in_block && /^id[[:space:]]*=/         { id = value_of($0); next }
    in_block && /^type[[:space:]]*=/       { type = value_of($0); next }
    in_block && /^default[[:space:]]*=/    { default_v = value_of($0); next }
    in_block && /^env[[:space:]]*=/        { env_v = value_of($0); next }
    in_block && /^depends_on[[:space:]]*=/ { depends_v = value_of($0); next }
    in_block && /^prompt[[:space:]]*=/     { prompt_v = value_of($0); next }
    in_block && /^warn[[:space:]]*=/       { warn_v = value_of($0); next }
    in_block && /^extras[[:space:]]*=/ {
        line = $0
        sub(/^extras[[:space:]]*=[[:space:]]*\[/, "", line)
        sub(/\][[:space:]]*$/, "", line)
        gsub(/[" ]/, "", line)
        extras_v = line
        next
    }
    END {
        if (in_block) {
            print id "|" type "|" default_v "|" extras_v "|" env_v "|" depends_v "|" prompt_v "|" warn_v
        }
    }
    ' "$1"
}

# ---------------------------------------------------------------------------
# Answer collection.  Stored as associative-ish ``answer__<id>=value``
# shell vars so the answers can be referenced by other questions
# (``depends_on``) and threaded into the Python wizard via env vars.
# ---------------------------------------------------------------------------

get_answer() {
    eval "printf '%s' \"\${answer__$1:-}\""
}

set_answer() {
    eval "answer__$1=\"\$2\""
}

# Predicate: ``depends_on = "id=value"``.  True when the referenced
# answer equals ``value`` (or when the dep is empty).
dep_satisfied() {
    dep="$1"
    [ -z "$dep" ] && return 0
    dep_id="${dep%%=*}"
    dep_val="${dep#*=}"
    actual="$(get_answer "$dep_id")"
    [ "$actual" = "$dep_val" ]
}

prompt_one() {
    id="$1"; type="$2"; default_v="$3"; env_v="$4"; prompt_v="$5"; warn_v="$6"
    extras_v="$7"
    choices_hint=""
    case "$type" in
        yes_no)
            choices_hint=" [Y/n]"
            [ "$default_v" = "no" ] && choices_hint=" [y/N]"
            ;;
    esac

    # Non-interactive: pull from CF_* env var, fall back to default.
    if [ "${CF_NON_INTERACTIVE:-0}" = "1" ]; then
        eval "answer=\"\${$env_v:-}\""
        if [ -z "${answer:-}" ]; then
            answer="$default_v"
        fi
        set_answer "$id" "$answer"
        info "$id = $answer (from \$$env_v)"
        return
    fi

    [ -n "$warn_v" ] && warn "$warn_v"

    while :; do
        printf '%b' "${BOLD}${prompt_v}${NC}${choices_hint}"
        printf ' (default: %s) ' "$default_v"
        # ``IFS=`` + ``-r`` keeps backslashes / whitespace literal.
        IFS= read -r answer </dev/tty || answer=""
        if [ -z "$answer" ]; then
            answer="$default_v"
        fi
        # Normalise yes/no answers so the rest of the script doesn't
        # need to know about y/Y/yeah/etc.
        case "$type" in
            yes_no)
                case "$answer" in
                    y|Y|yes|YES|Yes) answer="yes" ;;
                    n|N|no|NO|No)    answer="no" ;;
                    *) warn "Please answer y or n."; continue ;;
                esac
                ;;
        esac
        set_answer "$id" "$answer"
        break
    done

    # Hint about the unused extras_v / env_v args — referenced via the
    # outer ``answers`` map for the Python wizard handoff. shellcheck
    # otherwise flags them as set-but-not-used.
    : "$extras_v" "$env_v"
}

# ---------------------------------------------------------------------------
# Walk the question tree.
# ---------------------------------------------------------------------------

echo
printf '%b\n' "${BOLD}corpus-forge installer${NC}"
echo

# Collected pip extras (uniqued at install time).
all_extras=""

# Bash 3.2-compatible loop: hand the parsed lines to read via heredoc.
parsed_questions="$(parse_questions "$QUESTIONS_PATH")"
while IFS='|' read -r id type default_v extras_v env_v depends_v prompt_v warn_v; do
    [ -z "$id" ] && continue
    if ! dep_satisfied "$depends_v"; then
        continue
    fi
    prompt_one "$id" "$type" "$default_v" "$env_v" "$prompt_v" "$warn_v" "$extras_v"
    answer="$(get_answer "$id")"
    # Collect extras only when the answer is yes / a non-"none" choice /
    # non-empty text. ``extras_v`` may be a comma-separated list.
    if [ -n "$extras_v" ] && [ "$answer" != "no" ] && [ "$answer" != "none" ] && [ -n "$answer" ]; then
        all_extras="$all_extras,$extras_v"
    fi
done <<EOF
$parsed_questions
EOF

# ---------------------------------------------------------------------------
# Always pull in the [sqlite] extra when backend=sqlite (the answer
# itself doesn't carry the extra in the question tree — backend is a
# choice, not yes/no).
# ---------------------------------------------------------------------------

if [ "$(get_answer backend)" = "sqlite" ]; then
    all_extras="$all_extras,sqlite"
fi

# Dedup + strip leading comma so ``uv tool install corpus-forge[...]``
# is well-formed even when no extras land.
extras_clean="$(printf '%s' "$all_extras" | tr ',' '\n' | sort -u | grep -v '^$' | paste -sd, -)"

echo
ok "Selected pip extras: ${extras_clean:-<none>}"
echo

# ---------------------------------------------------------------------------
# Install via uv tool.
# ---------------------------------------------------------------------------

# ``CF_INSTALL_FROM`` lets the install-smoke E2E workflow point at the
# checked-out source tree so the installer is exercised against the
# current branch (the package isn't on PyPI yet for un-released
# commits).  Default empty → install ``corpus-forge`` from PyPI.
#
# uv's CLI: ``uv tool install '<path>[extras]'`` installs the local
# package with its extras; ``--from`` is for the cross-name case
# (install foo's CLI from bar's package) and conflicts when the
# install spec names a package.
if [ -n "${CF_INSTALL_FROM:-}" ]; then
    info "Installing from local source: $CF_INSTALL_FROM"
    if [ -n "$extras_clean" ]; then
        pkg_spec="${CF_INSTALL_FROM}[$extras_clean]"
    else
        pkg_spec="$CF_INSTALL_FROM"
    fi
else
    if [ -n "$extras_clean" ]; then
        pkg_spec="corpus-forge[$extras_clean]"
    else
        pkg_spec="corpus-forge"
    fi
fi

# corpus-forge requires Python >=3.11,<3.14. Pin a compatible interpreter
# explicitly so uv doesn't pick whatever system Python happens to be
# default (Ubuntu 22.04's default is 3.10, which fails resolution).
# ``CF_PYTHON`` overrides the default if the user wants a specific
# version (e.g. ``3.12`` on a host with multiple installed).
pin_python="${CF_PYTHON:-3.11}"

info "Running: $UV_CMD tool install --python $pin_python '$pkg_spec' --upgrade"
"$UV_CMD" tool install --python "$pin_python" "$pkg_spec" --upgrade

ok "corpus-forge installed"

# ---------------------------------------------------------------------------
# Hand off to the Python wizard for config.toml + secrets.env rendering.
# All collected answers are forwarded as CF_* env vars so the wizard
# can re-validate them and (in --non-interactive mode) skip re-prompting.
# ---------------------------------------------------------------------------

export_vars=""
while IFS='|' read -r id type default_v extras_v env_v depends_v prompt_v warn_v; do
    [ -z "$id" ] && continue
    val="$(get_answer "$id")"
    if [ -n "$val" ]; then
        eval "export $env_v=\"\$val\""
        export_vars="$export_vars $env_v"
    fi
    : "$type" "$default_v" "$extras_v" "$depends_v" "$prompt_v" "$warn_v"
done <<EOF
$parsed_questions
EOF

# Use the freshly-installed entry-point. ``uv tool`` symlinks into
# ``~/.local/bin`` by default; add it to PATH for the same shell so
# the wizard handoff Just Works.
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
esac

# Post-install handoff. Wrapped in a function so tests/scripts/test_install_sh.py
# can source and exercise this block without re-running uv provisioning.
__cf_post_install_handoff() {
    info "Launching the post-install setup wizard"
    if command -v corpus-forge >/dev/null 2>&1; then
        # ALWAYS pass --non-interactive. The CF_* env vars are already
        # populated above (interactive prompts feed into them via
        # ``set_answer``; non-interactive flow inherits them directly from
        # the caller's environment). If we omit ``--non-interactive``, the
        # wizard reprompts on the same stdin that this script has already
        # consumed (or was never a TTY when piped via ``curl | sh``), so
        # the prompts get empty replies and silently take defaults —
        # discarding every answer the user just typed.  See PR fixing
        # "install.sh post-install wizard ignores collected answers".
        corpus-forge setup --non-interactive

        # Run schema migrations now so first-run `ingest`/`embed` doesn't
        # fail on an empty DB. Tolerate failure (e.g. Postgres unreachable
        # at install time) — the installer prints a warning and exits 0
        # instead of leaving the user with a half-installed CLI.
        local cf_migrate_log
        cf_migrate_log="$(mktemp -t corpus-forge-migrate.XXXXXX.log 2>/dev/null || mktemp)"
        if ! corpus-forge migrate >"$cf_migrate_log" 2>&1; then
            warn "corpus-forge migrate failed — see $cf_migrate_log for details. Re-run \`corpus-forge migrate\` once your database is reachable."
        else
            rm -f "$cf_migrate_log"
        fi
    else
        warn "corpus-forge not on PATH yet. Open a new shell and run \`corpus-forge setup && corpus-forge migrate\`."
    fi

    echo
    ok "Done. Run \`corpus-forge --help\` to get started."
}
# END __cf_post_install_handoff

__cf_post_install_handoff
