#!/usr/bin/env bash
# corpus-forge installer for POSIX (macOS / Linux / WSL / Termux-friendly).
#
# Typical install:
#
#     curl -sSf https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.sh | bash
#
# (Pipe to ``bash``, not ``sh`` — on Ubuntu/Debian ``/bin/sh`` is
# ``dash`` which doesn't support ``pipefail`` and the other bashisms
# this script relies on.  The shebang above only takes effect when
# the script is invoked as a file, not when streamed through ``sh``.)
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
#
# Join an existing fleet (one-line onboarding — RFC fleet-3 item 6):
#
#     curl -sSf https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.sh \
#         | bash -s -- --join postgresql://primary.fleet:5432/corpus
#
# Or, equivalently:
#
#     CF_JOIN_DSN=postgresql://primary.fleet:5432/corpus ./install.sh
#
# In join mode the installer SKIPS the question tree (shared scope is
# pulled from the fleet's primary), hands off to
# ``corpus-forge setup --non-interactive --join <dsn>``, then runs
# ``corpus-forge doctor`` as a smoke check.  It explicitly does NOT run
# ``corpus-forge migrate`` — the primary owns schema lifecycle.
#
# Pick the llama-cpp-python accelerator wheel (RFC fleet-7):
#
#     ./install.sh --llama-backend cuda      # force a CUDA wheel
#     CF_LLAMA_BACKEND=cpu ./install.sh      # force the CPU wheel
#
# ``--llama-backend {auto|cuda|cudaNNN|metal|cpu|none}`` (default
# ``auto``) selects which prebuilt ``llama-cpp-python`` wheel the
# installer fetches.  ``auto`` detects the host accelerator
# (``nvidia-smi`` → CUDA, Apple-Silicon → Metal, else CPU); ``none``
# skips the ``[llama-cpp]`` extra entirely.  ``$CF_LLAMA_BACKEND`` is
# the env-var equivalent; both thread through the ``--join`` one-liner,
# so a GPU box joins the fleet AND gets a CUDA wheel in one command.

# Fail loudly + early when streamed through a non-bash shell (Ubuntu /
# Debian's ``/bin/sh`` is dash, which rejects ``set -o pipefail`` with
# the cryptic ``sh: 29: set: Illegal option -o pipefail``).  Detect by
# checking for ``BASH_VERSION`` (set by bash, unset by dash / ash /
# POSIX sh) and direct the user to the right command.
if [ -z "${BASH_VERSION:-}" ]; then
    printf '%s\n' \
        "Error: install.sh requires bash (uses pipefail, [[ ]], local vars)." \
        "Re-run with bash instead of sh:" \
        "" \
        "    curl -sSf https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.sh | bash" \
        "" \
        "Or, after \`git clone\`:  bash install.sh" >&2
    exit 1
fi

set -euo pipefail

# ---------------------------------------------------------------------------
# CLI args.  We parse ``--join <dsn>`` / ``--join=<dsn>`` (RFC fleet-3)
# and ``--llama-backend <val>`` / ``--llama-backend=<val>`` (RFC
# fleet-7); unknown flags pass through untouched so future flags can be
# added without revisiting this loop.  Each lands in a ``CF_*`` env var
# (``CF_JOIN_DSN`` / ``CF_LLAMA_BACKEND``) so the flag and env-var entry
# points share one code path downstream — and so they thread through the
# ``--join`` one-liner together (a GPU box joins the fleet AND gets the
# CUDA llama-cpp wheel in a single command).
# ---------------------------------------------------------------------------

# RFC fleet-3 item 6 / fleet-7 item 3 — installer one-liner pass-through.
_passthrough_args=()
while [ $# -gt 0 ]; do
    case "$1" in
        --join)
            shift
            if [ $# -eq 0 ]; then
                printf '%s\n' "Error: --join requires a DSN argument" >&2
                exit 1
            fi
            CF_JOIN_DSN="$1"
            export CF_JOIN_DSN
            shift
            ;;
        --join=*)
            CF_JOIN_DSN="${1#--join=}"
            export CF_JOIN_DSN
            shift
            ;;
        --llama-backend)
            shift
            if [ $# -eq 0 ]; then
                printf '%s\n' "Error: --llama-backend requires a value (auto|cuda|cudaNNN|metal|cpu|none)" >&2
                exit 1
            fi
            CF_LLAMA_BACKEND="$1"
            export CF_LLAMA_BACKEND
            shift
            ;;
        --llama-backend=*)
            CF_LLAMA_BACKEND="${1#--llama-backend=}"
            export CF_LLAMA_BACKEND
            shift
            ;;
        *)
            _passthrough_args+=("$1")
            shift
            ;;
    esac
done
# Restore any unknown flags as positional args so future feature work can
# add its own parsing without colliding with this one.
if [ ${#_passthrough_args[@]} -gt 0 ]; then
    set -- "${_passthrough_args[@]}"
else
    set --
fi

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

# RFC fleet-3 item 6 — in join mode the question tree is SKIPPED. The
# fleet's primary owns the shared scope (embedder choices, retrieval
# tuning, classifier chains, …); the wizard pulls all of that via
# ``setup --join <dsn>`` rather than asking the operator on a fresh
# joiner. We still install ``corpus-forge`` (plain, no extras — the
# operator can opt in to ``[hf]`` / ``[mcp]`` / etc. later).
if [ -n "${CF_JOIN_DSN:-}" ]; then
    info "Join mode — skipping question tree (shared scope comes from primary)."
else
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
fi

# ---------------------------------------------------------------------------
# Always pull in the [sqlite] extra when backend=sqlite (the answer
# itself doesn't carry the extra in the question tree — backend is a
# choice, not yes/no).
# ---------------------------------------------------------------------------

if [ "$(get_answer backend)" = "sqlite" ]; then
    all_extras="$all_extras,sqlite"
fi

# Dedup + strip leading comma so ``uv tool install corpus-forge[...]``
# is well-formed even when no extras land.  ``grep -v '^$' || true``
# tolerates an empty pipeline (join mode skips the question tree, so
# ``all_extras`` is empty and grep exits 1 under ``set -o pipefail``).
extras_clean="$(printf '%s' "$all_extras" | tr ',' '\n' | sort -u | { grep -v '^$' || true; } | paste -sd, -)"

# ---------------------------------------------------------------------------
# RFC fleet-7 — select the llama-cpp-python wheel backend.
#
# A plain ``uv tool install`` takes whatever ``llama-cpp-python`` wheel
# resolves: CPU-only unless fetched against an accelerator backend. So a
# CUDA box silently embeds on the CPU while its GPU idles. ``llama-cpp-python``
# publishes prebuilt accelerated wheels behind per-backend extra-index URLs
# (cpu / metal / cuXXX); we detect the host accelerator with the SAME signals
# ``corpus_forge/acceleration.py`` uses at runtime (``nvidia-smi`` for CUDA +
# its reported version; Apple-Silicon ``uname`` for Metal; else CPU) so the
# install-time wheel and the runtime probe agree, then point ``uv`` at the
# matching index.  CPU always resolves (no surprise source build); an
# accelerated-fetch failure falls back to CPU + WARN.  Override with
# ``--llama-backend`` / ``$CF_LLAMA_BACKEND``.
# ---------------------------------------------------------------------------

CF_LLAMA_INDEX_BASE="${CF_LLAMA_INDEX_BASE:-https://abetlen.github.io/llama-cpp-python/whl}"

# BEGIN __cf_llama_backend_helpers
# Probe nvidia-smi for the driver's reported CUDA runtime version. Echoes
# "MAJOR.MINOR" (e.g. "12.4") on success, empty otherwise. The plain
# ``nvidia-smi`` text header prints "CUDA Version: 12.4" — parse that.
__cf_detect_cuda_version() {
    command -v nvidia-smi >/dev/null 2>&1 || return 0
    local out
    out="$(nvidia-smi 2>/dev/null)" || return 0
    printf '%s\n' "$out" \
        | sed -n 's/.*CUDA Version: \([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p' \
        | head -n1
}

# Detect the accelerator KIND: echoes cuda|metal|cpu. CUDA wins (matches
# acceleration.detect_accelerator's CUDA→MPS→CPU priority).
__cf_detect_accelerator() {
    if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
        printf 'cuda\n'; return 0
    fi
    if [ "$(uname -s 2>/dev/null)" = "Darwin" ] && [ "$(uname -m 2>/dev/null)" = "arm64" ]; then
        printf 'metal\n'; return 0
    fi
    printf 'cpu\n'
}

# Map a CUDA version "MAJOR.MINOR" to the closest published
# llama-cpp-python wheel variant (cuXXX). The abetlen index publishes
# cu118 + cu121..cu125; clamp into that range. Empty/unparsed input or an
# unsupported major echoes the documented default (cu121) so a parse miss
# still lands a working CUDA wheel rather than silently dropping to CPU.
__cf_cuda_variant() {
    local ver="$1" major minor
    major="${ver%%.*}"
    minor="${ver#*.}"; minor="${minor%%.*}"
    case "$major" in
        12)
            case "$minor" in
                ''|0|1) printf 'cu121\n' ;;
                2)      printf 'cu122\n' ;;
                3)      printf 'cu123\n' ;;
                4)      printf 'cu124\n' ;;
                *)      printf 'cu125\n' ;;
            esac
            ;;
        11) printf 'cu118\n' ;;
        *)  printf 'cu121\n' ;;
    esac
}
# END __cf_llama_backend_helpers

# Resolve the requested backend → a wheel variant (cpu|metal|cuXXX) or
# empty for "no llama-cpp". ``llama_explicit_accel=1`` records that the
# operator forced an accelerator value (so we install llama-cpp even if
# the question tree didn't ask for it).
llama_backend="${CF_LLAMA_BACKEND:-auto}"
llama_variant=""
llama_explicit_accel=0
case "$llama_backend" in
    none)  llama_variant="" ;;
    cpu)   llama_variant="cpu";   llama_explicit_accel=1 ;;
    metal) llama_variant="metal"; llama_explicit_accel=1 ;;
    cuda)  llama_variant="$(__cf_cuda_variant "$(__cf_detect_cuda_version)")"; llama_explicit_accel=1 ;;
    cuda[0-9]*) llama_variant="cu${llama_backend#cuda}"; llama_explicit_accel=1 ;;
    auto|*)
        if [ "$llama_backend" != "auto" ]; then
            warn "Unknown --llama-backend '$llama_backend' — falling back to auto-detect."
            llama_backend="auto"
        fi
        case "$(__cf_detect_accelerator)" in
            cuda)  llama_variant="$(__cf_cuda_variant "$(__cf_detect_cuda_version)")" ;;
            metal) llama_variant="metal" ;;
            *)     llama_variant="cpu" ;;
        esac
        ;;
esac

# Decide whether llama-cpp is installed at all. It is when: already in the
# resolved extras; an accelerator backend was forced; the recommended
# ``embedder=auto`` lane was chosen (acceleration.recommend_embedder_preset
# is always a llama-cpp lane, so auto installs need the extra — closes a
# latent gap); OR a fleet joiner (no question tree) auto-detected a GPU and
# joined for it. ``none`` always wins (drops the extra).
llama_in_extras=0
case ",$extras_clean," in *,llama-cpp,*) llama_in_extras=1 ;; esac
want_llama=0
if [ "$llama_backend" = "none" ]; then
    want_llama=0
elif [ "$llama_in_extras" -eq 1 ] || [ "$llama_explicit_accel" -eq 1 ]; then
    want_llama=1
elif [ "$(get_answer embedder)" = "auto" ]; then
    want_llama=1
elif [ -n "${CF_JOIN_DSN:-}" ] && [ -n "$llama_variant" ] && [ "$llama_variant" != "cpu" ]; then
    # Join + auto + a GPU (metal/cuXXX): the box joined the fleet for its
    # accelerator — give it in-process GPU embedding by default.
    want_llama=1
fi

# Apply the decision to the extras list.
if [ "$want_llama" -eq 1 ] && [ "$llama_in_extras" -eq 0 ]; then
    # ``printf '%s\n'`` (not ``%s``) so the trailing element keeps its
    # newline — otherwise ``echo llama-cpp`` would concatenate onto it
    # (``…,tokensllama-cpp``) instead of becoming a fresh list entry.
    extras_clean="$( { printf '%s\n' "$extras_clean" | tr ',' '\n'; echo 'llama-cpp'; } | sort -u | { grep -v '^$' || true; } | paste -sd, -)"
elif [ "$want_llama" -eq 0 ] && [ "$llama_in_extras" -eq 1 ]; then
    extras_clean="$(printf '%s\n' "$extras_clean" | tr ',' '\n' | { grep -vx 'llama-cpp' || true; } | { grep -v '^$' || true; } | paste -sd, -)"
fi

# Announce the choice + build the uv extra-index args.
llama_index_args=()
if [ "$want_llama" -eq 1 ] && [ -n "$llama_variant" ]; then
    llama_index_args=(--extra-index-url "$CF_LLAMA_INDEX_BASE/$llama_variant" --index-strategy unsafe-best-match)
    case "$llama_variant" in
        cu*)   info "llama-cpp backend: NVIDIA CUDA → CUDA-enabled llama-cpp-python ($llama_variant wheel) [override: --llama-backend]" ;;
        metal) info "llama-cpp backend: Apple Silicon → Metal-enabled llama-cpp-python [override: --llama-backend]" ;;
        cpu)   info "llama-cpp backend: no accelerator detected → CPU llama-cpp-python wheel [override: --llama-backend]" ;;
    esac
elif [ "$llama_backend" = "none" ]; then
    info "llama-cpp backend: skipped (--llama-backend none)"
fi

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

if [ ${#llama_index_args[@]} -gt 0 ]; then
    info "Running: $UV_CMD tool install --python $pin_python '$pkg_spec' --upgrade ${llama_index_args[*]}"
    # Accelerated/explicit llama-cpp wheel: try the selected index, and on
    # failure fall back to the CPU wheel + WARN (offline index, unsupported
    # CUDA version, arch with no prebuilt wheel) — never hard-fail the
    # accelerator step.  ``if !`` guards the failure so ``set -e`` doesn't
    # abort before the fallback runs.
    if ! "$UV_CMD" tool install --python "$pin_python" "$pkg_spec" --upgrade "${llama_index_args[@]}"; then
        if [ "$llama_variant" != "cpu" ]; then
            warn "Accelerated llama-cpp-python wheel ($llama_variant) could not be installed — retrying with the CPU wheel. Re-run with \`--llama-backend $llama_backend\` once the accelerated index is reachable."
            "$UV_CMD" tool install --python "$pin_python" "$pkg_spec" --upgrade \
                --extra-index-url "$CF_LLAMA_INDEX_BASE/cpu" --index-strategy unsafe-best-match
        else
            fail "uv tool install failed for the CPU llama-cpp wheel. See output above."
        fi
    fi
else
    info "Running: $UV_CMD tool install --python $pin_python '$pkg_spec' --upgrade"
    "$UV_CMD" tool install --python "$pin_python" "$pkg_spec" --upgrade
fi

ok "corpus-forge installed"

# ---------------------------------------------------------------------------
# Hand off to the Python wizard for config.toml + secrets.env rendering.
# All collected answers are forwarded as CF_* env vars so the wizard
# can re-validate them and (in --non-interactive mode) skip re-prompting.
# ---------------------------------------------------------------------------

export_vars=""
# In join mode the question tree was skipped, so there are no answers to
# forward as CF_* env vars — the wizard pulls everything from the
# fleet's published shared scope via ``--join``.
if [ -z "${CF_JOIN_DSN:-}" ]; then
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
fi

# Use the freshly-installed entry-point. ``uv tool`` symlinks into
# ``~/.local/bin`` by default; add it to PATH for the same shell so
# the wizard handoff Just Works.
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
esac

# Post-install handoff. Wrapped in a function so tests/scripts/test_install_sh.py
# can source and exercise this block without re-running uv provisioning.
#
# Branches on ``CF_JOIN_DSN``:
#   - non-join (default): ``setup --non-interactive`` then ``migrate``.
#   - join (RFC fleet-3 item 6): ``setup --non-interactive --join <dsn>``
#     then ``doctor`` (tolerant of failure) — explicitly NO ``migrate``,
#     because the fleet's primary owns the schema lifecycle.
__cf_post_install_handoff() {
    info "Launching the post-install setup wizard"
    if command -v corpus-forge >/dev/null 2>&1; then
        if [ -n "${CF_JOIN_DSN:-}" ]; then
            # Join mode — onboarding a new host onto an existing fleet.
            # The wizard connects to the shared Postgres, verifies the
            # corpus schema is present, registers this host in
            # ``corpus.hosts``, and renders a local config pre-loaded
            # with the fleet's published shared scope.
            corpus-forge setup --non-interactive --join "$CF_JOIN_DSN"

            # Run ``doctor`` as a smoke check (DSN reachability, embedder
            # config sanity, host-id stability). Tolerate failure for
            # the same reason ``migrate`` is tolerated on the non-join
            # path: a transient network blip shouldn't leave the
            # operator with a half-installed CLI.
            local cf_doctor_log
            cf_doctor_log="$(mktemp -t corpus-forge-doctor.XXXXXX.log 2>/dev/null || mktemp)"
            if ! corpus-forge doctor >"$cf_doctor_log" 2>&1; then
                warn "corpus-forge doctor reported issues — see $cf_doctor_log for details. Re-run \`corpus-forge doctor\` once the fleet primary is reachable."
            else
                rm -f "$cf_doctor_log"
            fi

            echo
            ok "Joined fleet at $CF_JOIN_DSN."
            info "Next: \`corpus-forge bench embed --all\` (record this host's throughput), then \`corpus-forge service install\` (run the daemon)."
        else
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

            echo
            ok "Done. Run \`corpus-forge --help\` to get started."
        fi
    else
        if [ -n "${CF_JOIN_DSN:-}" ]; then
            warn "corpus-forge not on PATH yet. Open a new shell and run \`corpus-forge setup --join $CF_JOIN_DSN\`."
        else
            warn "corpus-forge not on PATH yet. Open a new shell and run \`corpus-forge setup && corpus-forge migrate\`."
        fi
        echo
        ok "Done. Run \`corpus-forge --help\` to get started."
    fi
}
# END __cf_post_install_handoff

__cf_post_install_handoff
