# Validation environment repair

Validated on 2026-09-26 after unrestricted access was restored.
The earlier unrestricted run passed 9,152 tests. The later failures came from
several independent causes; moving the environment alone does not resolve them.

## Environment location

The checkout's `.venv` symlink resolves to
`/Users/evanowen/Local/venvs/corpus-forge`. Python's `sys.prefix` and
site-packages resolve there too. Packages are outside iCloud Drive.

`make install` and `make dev` now share `_venv-link`. It repairs stale links,
handles paths with spaces, and refuses to overwrite a real `.venv` directory.
Every Makefile `uv` command receives `UV_PROJECT_ENVIRONMENT` from `VENV`.
Export a different absolute `VENV` for another checkout.
An existing `UV_PROJECT_ENVIRONMENT` supplies the default for `VENV`. CI selects
one environment under `RUNNER_TEMP` before syncing dependencies, and every later
Make invocation reuses it. A regression test executes the composite action's
selection and sync commands and checks the environment received by Make.

The first release gate exposed a mismatch: CI populated `.venv`, while Make
selected a fresh external environment without the optional dependencies. The
`0.1.0b19` tag was not published. Version `0.1.0b20` corrects this shared setup.
The `0.1.0b20` gate passed Linux, macOS, and integration checks but exposed an
invalid Windows Git config in the new isolation test. A backslash-containing
path reproduced the failure on macOS too. Writing that fixture through
`git config --file` fixes escaping; ordinary paths, spaces, and backslashes are
all covered. Version `0.1.0b21` includes this correction.

## Findings and fixes

| Cause | Fix and regression coverage |
| --- | --- |
| SQLite sync validation omitted dataset names | Report every sync-enabled dataset while retaining the existing guidance. Replace the expected failure with single- and multiple-dataset tests that exclude local-only datasets. |
| CLI startup failed when the user log file was unwritable | Keep stderr and memory logging when directory creation or file opening raises `OSError`. Tests cover both failures, MCP stream silence, and recovery on reinitialization. |
| CLI tests wrote to the developer's log directory | Give each test an isolated `CF_LOG_DIR`, inherited by child processes. |
| Retry plugin opened a socket without any requested retries | Disable automatic loading in the default pytest options and isolated pytester runs. No test in this repository requests retries. |
| Test Git commits inherited signing and hooks | Disable signing and use an empty hooks directory in temporary fixture repos. A regression test supplies hostile global signing/hook settings and verifies fixture commits still work. User and checkout Git configuration are untouched. |
| Presentation and bundle tests ran live health probes | Supply a deterministic doctor report for those tests. Keep serialization, display, and error-path assertions. The daemon-activity registration test verifies registration before isolating unrelated probes. |
| Sync logging tests started native file watchers | Stub the observer in bookend tests; retain assertions against real pipeline log output. |
| Ingest flag tests contacted the configured database | Isolate the drift preflight in the argument-plumbing module. Keep validation and forwarded-argument assertions. All 53 tests in that module pass. |

## Latest validation

- Formatting, lint, type checks, and `git diff --check` pass.
- Full suite for `0.1.0b21`: **9,172 passed, 28 skipped**, with no failures or setup errors.
- Coverage: **92.25%**, above the unchanged 89% gate.
- No expected failures remain in this run.
- After the release version bump, all 31 wheel-metadata checks pass, including
  the opt-in fresh-environment installation and CLI startup test.
- The prior restricted run had 29 errors in the shared wheel-metadata build
  fixture because its isolated environment could not download `hatchling>=1.25`.
  Those tests pass with network access restored.
- Docker-backed integration tests pass with the sandbox restriction removed.
  Remaining skips include unavailable PowerShell, API credentials, seed corpora,
  opt-in model benchmarks, and pre-existing test guards.

The full-suite log is preserved at
`~/Workspace/scripts/corpus-forge-cleanup-20260925/pytest-b21-final.log`.

The restricted run is in `.pytest_cache/validation-current.log`. Earlier failure
reports remain in `.pytest_cache/validation-sync-gate.log` and
`.pytest_cache/validation-environment-fixes.log` for comparison. No changes have
been committed or pushed at the time those failure reports were captured.

## iCloud conflict cleanup

Archived 216 additional numbered conflict copies outside iCloud, including
22 alternate Git indexes. Hydrated 178 cloud-only placeholders before archiving.
The last two index copies reappeared after the first clean scan and were archived
in a follow-up pass.
Each copy was verified by SHA-256 before removal. Canonical files and tracked
paths were preserved; the final rescan found no remaining conflict candidates.
Archives and manifests live under `~/Local/archives/corpus-forge/`; the cleanup
script and restoration notes remain in `~/Workspace/scripts/corpus-forge-cleanup-20260925/`.
The discarded `.venv-trash-*` directories are now ignored so source builds do
not traverse or package their nested dependency test suites.
A directory-name audit then archived 63 whole conflict directories containing
735 files, including two empty directories. The combined cleanup preserved
951 file copies. Both file-name and directory-name inventories were empty after
the final pass; restoration notes are in the script directory above.
