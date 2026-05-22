---
status: done
slug: 0001-fix-install-migrate-bugs
task_number: 1
created: 2026-05-21T02:14:07Z
updated: 2026-05-21T02:14:07Z
---
# Task 0001 — fix/install-migrate-bugs

## Source

User-seeded follow-up to the priority list: "Send the following to nightly as
well: fix/install-migrate-bugs."

CLAUDE.md §6 and the priority-list item #7 both claim that `install.sh`
hands off to `corpus-forge setup` *and* `corpus-forge migrate` after install.
Today the POSIX installer (`install.sh`) and the PowerShell installer
(`install.ps1`) only call `corpus-forge setup`. `corpus-forge migrate` is
never run, so a fresh install leaves the user with an un-migrated DB and
their first `ingest` / `embed` crash is the diagnostic.

Per the priority list, the desired behaviour when Postgres isn't reachable
at install time is: warn but exit 0 (don't fail the install).

## Success criteria

1. `install.sh` runs `corpus-forge migrate` after the setup wizard returns
   successfully, and the migrate failure path warns + exits 0 (does not
   break the installer).
2. `install.ps1` matches: invokes `corpus-forge migrate` after setup,
   warns on failure without aborting the script.
3. The migrate handoff respects `CF_NON_INTERACTIVE` (no prompts in CI)
   and `CF_CONFIG` if the user set it during setup.
4. A short unit / shell test asserts that `install.sh` calls migrate on
   the happy path and tolerates a non-zero migrate exit (warns, exits 0).
5. CLAUDE.md / docs aren't lying about the behaviour any more — if needed,
   nudge wording to match what the script actually does.

## File scope

- `install.sh`
- `install.ps1`
- `tests/scripts/test_install_sh.py` (or whatever pattern matches the
  existing scripts test suite — discover during ISOLATE)
- `CLAUDE.md` (only if its description of the installer diverges from the
  new behaviour)
- `CHANGELOG.md` (if the project keeps unreleased notes there)

Anything outside this list is out-of-scope; touching it would be scope
creep under the refusal policy.

## Known risks

- `corpus-forge migrate` may interactively prompt when the DSN is missing.
  We need a non-interactive code path or a pre-flight check before calling
  it, or it'll hang the installer.
- `set -e` is on in `install.sh`. The migrate call needs `|| warn ...` to
  swallow non-zero exits without killing the script.
- On Windows, the equivalent in `install.ps1` is `$ErrorActionPreference =
  "Stop"`. Wrap migrate in a `try/catch` (or use `2>$null; if ($LASTEXITCODE
  -ne 0)`).
- The integration test for the install scripts already exists at
  `tests/scripts/`. Find the harness and reuse it; don't add a new top-level
  test runner.

## Uncertainty

- Whether `corpus-forge migrate` already has a `--non-interactive` flag or
  whether it always picks up the DSN from `~/.config/corpus-forge/config.toml`.
  Verify during SCOPE before writing the implementer prompt.
- Whether there is also a `setup-corpus-forge.sh` (contributor clone-and-run)
  that should mirror the change. Decision: out of scope — that script is for
  contributors, not end users, and CLAUDE.md doesn't claim migrate happens
  there.
