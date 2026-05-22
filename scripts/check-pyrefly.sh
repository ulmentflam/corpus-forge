#!/usr/bin/env bash
# check-pyrefly.sh — wrapper around `pyrefly check` that actually fails
# when pyrefly reports errors.
#
# pyrefly itself always exits 0, which means `make typecheck` (and the
# pre-commit hook) silently swallowed every type error before this
# wrapper landed. This script:
#
#   1. Runs `pyrefly check` with `--ignore missing-import` so users
#      who haven't installed every optional extra (pdf2image, hdbscan,
#      …) aren't blocked by missing-stub noise.
#   2. Prints pyrefly's output verbatim (errors go to stderr, ours
#      passes them through).
#   3. Greps the output for the "INFO N errors" line and exits 1 if
#      N > 0.
#
# Args:
#   $@ — optional file/dir args forwarded to pyrefly. When omitted,
#        pyrefly checks the configured project (corpus_forge).

set -euo pipefail

# Capture stderr+stdout. pyrefly prints results to stderr but we want
# to surface both streams; the variable holds them for the regex match.
#
# ``|| status=$?`` defeats ``set -e`` only on the failure path AND
# captures the real exit code on the same line. ``status=0`` initialises
# the variable for the success path so ``set -u`` doesn't trip on the
# later read.
status=0
output=$(uv run pyrefly check --ignore missing-import "$@" 2>&1) || status=$?

# Always show what pyrefly said so users see the actual errors.
printf '%s\n' "$output"

# If pyrefly itself crashed (config missing, etc.) propagate that.
if [ "$status" -ne 0 ]; then
    exit "$status"
fi

# Match "INFO N error(s)" where N > 0. pyrefly uses the singular form
# for exactly one error ("INFO 1 error") and plural otherwise; the
# ``s?`` covers both. The "INFO 0 errors" line is the clean-baseline
# signal and must not fail the build.
if printf '%s\n' "$output" | grep -qE 'INFO [1-9][0-9]* errors?'; then
    echo
    echo "pyrefly reported errors above — failing the check." >&2
    exit 1
fi

exit 0
