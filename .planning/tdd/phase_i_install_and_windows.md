# Phase I — Easy installer, Windows portability, and update story

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

## Status

**Phase H** complete (2026-05-15). **Phase I** (this file): **PROPOSED** (2026-05-16).

## Goal

Make corpus-forge install in one minute on macOS, Linux, and Windows
with an interactive question tree that lights up the right extras +
config blocks. Fix Windows so it's a first-class supported platform
(restore to CI matrix, add NSSM-based daemon supervisor). Give users
a single `corpus-forge update` command that delegates to whatever
package manager installed them, plus a daily version-check ping so
stale installs surface their staleness.

## Why now

- Audit found **no Homebrew tap, no scoop manifest, no Docker image,
  no `pipx`/`uv tool install` recipe documented, no `corpus-forge
  update` command**. Today users copy-paste 12 pip-extra names from
  README and hand-edit `config.example.toml` to wire backends.
- Windows is dropped from the CI matrix and has no install script —
  README's "use NSSM manually" path is the only documentation. The
  actual blockers are tiny (4 files; ~10 LOC of real fixes).
- Phase H landed a uniform local-or-remote URL+api-key shape across
  every model integration. The installer is the natural surface to
  *use* that — paste a hosted-Ollama URL once during setup, get all
  six backends wired with one bearer token.

## Architecture

### Installers (3 entry points, 1 shared question tree)

```
                   ┌──────────────────────────────┐
                   │  question tree (TOML)        │
                   │  .planning/install/questions  │
                   └──────────────┬───────────────┘
                                  │
        ┌─────────────────────────┼─────────────────────────┐
        │                         │                         │
   install.sh                install.ps1            setup-corpus-forge.sh
   (end-user, POSIX)       (end-user, Windows)      (contributor / source)
   curl-pipe-bash          iwr | iex                git clone then run
        │                         │                         │
        └───────────┬─────────────┴─────────────────────────┘
                    ▼
        corpus-forge setup (Python wizard)
        - validates endpoints (ping Ollama, check API keys)
        - writes ~/.config/corpus-forge/config.toml
        - drops secrets.env template
        - optional: writes MCP server config to Claude Desktop/Code
        - optional: registers daemon supervisor (systemd / launchd / NSSM)
```

**Why three shells, not one Python wizard:** the *bootstrap* step
needs to provision `uv` / `python` first; you can't `corpus-forge
setup` until the binary is on PATH. The shells do the minimal
provision-and-install, then hand off to the rich Python wizard.

**Why a shared question tree (TOML, not hard-coded):** keeps the
three shells in lock-step. The shells render it; the Python wizard
re-uses the same definitions for the post-install reconfigure path
(`corpus-forge setup --reconfigure`).

### CLI surface additions

| Command | Purpose | Notes |
|---|---|---|
| `corpus-forge setup` | Interactive config wizard (post-install or re-run) | Walks the question tree; validates each endpoint; writes config.toml |
| `corpus-forge setup --reconfigure` | Re-run wizard against an existing config | Preserves answers, only asks for unset values + diffs the config write |
| `corpus-forge setup --non-interactive` | CI / unattended mode | Reads answers from env vars (`CF_BACKEND=sqlite`, `CF_EXTRAS=mcp,hf,multi-format`, `CF_VLM=ollama`, …) |
| `corpus-forge update` | Self-update | Detects install channel (uv tool / pipx / pip / brew / docker / source), runs the matching upgrade, then runs `migrate` + `doctor` |
| `corpus-forge update --check` | Check-only | Pings GitHub Releases; prints "v0.2.0 available" or "up to date"; exit 0/1 for scripting |
| `corpus-forge doctor` | Post-install diagnostic | Checks: Python version, extras, system deps (poppler/ffmpeg), Ollama daemon reachable, config validity, schema migrations applied, daemon supervisor active |

### Windows portability fixes

Concrete failure points (from audit):

1. `corpus_forge/config.py:565` — `secrets_path.open()` no encoding kwarg → cp1252 on Windows. Add `encoding="utf-8"`.
2. `tests/unit/test_phase_ci3_scripts.py:201` — `os.uname().sysname != "Darwin"`. `os.uname` doesn't exist on Windows. Switch to `sys.platform == "darwin"`.
3. `tests/unit/test_sync_echo.py:226` + `tests/unit/test_sync_cloud.py:216` — `symlink_to` calls. Add `@pytest.mark.requires_unix` (marker plumbing already exists in `tests/conftest.py:150-155`).
4. `corpus_forge/sync/cloud.py:18-22` — iCloud detection matches macOS-only paths. Add Windows iCloud Drive (`%USERPROFILE%\iCloudDrive`) detection branch.
5. Daemon supervisor (`Makefile:99-114`) has no Windows branch — falls through to "unsupported OS" echo. Add NSSM-based registration in `scripts/windows/install.ps1` (NSSM is the de-facto Windows service shim; no system install of NSSM needed — we vendor a download step).

### Update detection logic (`corpus-forge update`)

```
detect_channel() → one of:
  uv-tool   — sys.executable under ~/.local/share/uv/tools/corpus-forge/
  pipx      — sys.executable under ~/.local/pipx/venvs/corpus-forge/
  brew      — sys.executable under /opt/homebrew/Cellar/ or /usr/local/Cellar/
  docker    — /run/.containerenv exists, or os.environ.get("DOCKER_CONTAINER")
  pip       — fallback (any other prefix)
  source    — `git rev-parse` succeeds from sys.executable's grandparent

run_upgrade(channel):
  uv-tool   → uv tool upgrade corpus-forge
  pipx      → pipx upgrade corpus-forge
  brew      → brew upgrade corpus-forge
  docker    → print "docker pull ulmentflam/corpus-forge:latest" (we can't self-upgrade an image)
  pip       → pip install -U corpus-forge (with the original extras preserved)
  source    → git pull + uv sync --extra all --locked
```

After upgrade: `corpus-forge migrate` (schema), then `corpus-forge
doctor` (sanity).

### Daily version-check ping

Lightweight, opt-out-able (`CF_NO_VERSION_CHECK=1`):

- Cached at `~/.cache/corpus-forge/version-check.json` for 24h.
- Async on daemon start; sync in `corpus-forge --version` when stale.
- Fetches `https://pypi.org/pypi/corpus-forge/json` (no auth, public).
- Prints a single line if newer: `note: corpus-forge v0.2.0 is
  available (you have v0.1.0b1). Run \`corpus-forge update\`.`
- Failure is silent (offline / DNS / 5xx).

### Plugin discovery

Audit found: "plugins" today = extras (pip) + config selectors. No
`entry_points` mechanism. **Out of scope for Phase I** to introduce a
real plugin discovery system; the installer prompts cover all 15
user-facing surfaces from the audit (storage, sources, multi-format,
code, OCR, whisper, classifier, code enricher, embedders, retrieval,
MCP, HF export, daemon supervisor, endpoint URLs, secrets). A
later phase can introduce `entry_points = "corpus_forge.sources"` for
third-party source plugins; the question tree is forward-compatible.

## Task table

| id | title | depends_on | surface | risk | status |
|----|-------|------------|---------|------|--------|
| I-01 | **Windows portability fixes** — cp1252 encoding, os.uname → sys.platform, requires_unix markers on symlink tests, iCloud-on-Windows detection branch | — | `corpus_forge/config.py`, `corpus_forge/sync/cloud.py`, `tests/unit/test_phase_ci3_scripts.py`, `tests/unit/test_sync_echo.py`, `tests/unit/test_sync_cloud.py` | low | proposed |
| I-02 | **Restore windows-2022 to CI matrix** — flip `os: [ubuntu-22.04, macos-14]` → `[ubuntu-22.04, macos-14, windows-2022]` in `.github/workflows/ci.yml`. Confirm `make test-unit` exit-0 on Windows runner. Add `CI_NO_DOCKER=1` skip for the testcontainer suite. | I-01 | `.github/workflows/ci.yml`, `tests/conftest.py` (CI_NO_DOCKER plumbing) | med | proposed |
| I-03 | **Question tree definition** — single source of truth for the 15 surfaces. TOML schema with: question id, prompt text, type (yes/no, choice, free-text), default, extras-mapped-to, config-block-edited, follow-up questions (conditional branches) | — | `packaging/install/questions.toml`, `tests/unit/test_install_questions_schema.py` | low | proposed |
| I-04 | **`install.sh`** — curl-pipe-bash for POSIX (macOS + Linux + WSL). Provisions uv, parses `questions.toml`, runs prompts, invokes `uv tool install corpus-forge[<extras>]`, drops `secrets.env` template, handoff to `corpus-forge setup` | I-03 | `install.sh` (repo root), `tests/unit/test_install_sh_lint.py` (shellcheck) | high | proposed |
| I-05 | **`install.ps1`** — PowerShell mirror for Windows. Same question tree. Provisions uv via `irm https://astral.sh/uv/install.ps1 \| iex`, runs prompts via `Read-Host`, `uv tool install`, drops secrets template | I-03, I-01 | `install.ps1`, CI smoke test on windows-2022 | high | proposed |
| I-06 | **`setup-corpus-forge.sh`** — contributor clone-and-run. `uv sync --extra all --group dev --locked`, runs pre-commit install, symlinks dev binary into `~/.local/bin`. Reuses the question-tree prompt code from install.sh via sourced shell library | I-03, I-04 | `setup-corpus-forge.sh`, README contributor section | med | proposed |
| I-07 | **`corpus-forge setup` Python wizard** — interactive config builder. Reads `questions.toml`, validates endpoints (pings Ollama, tests OPENAI_API_KEY against `/v1/models`), writes `config.toml`, drops `secrets.env`, optional MCP config write | I-03 | `corpus_forge/cli.py::setup`, `corpus_forge/setup/{wizard,endpoint_probe,mcp_config}.py`, `tests/unit/test_setup_wizard.py` | high | proposed |
| I-08 | **`corpus-forge setup --non-interactive`** — env-var-driven mode. Same code path; reads from `CF_*` env vars; fails loud on missing required answers. CI smoke test exercises every backend selector | I-07 | `corpus_forge/setup/wizard.py`, `tests/unit/test_setup_non_interactive.py`, `.github/workflows/install-smoke.yml` (new) | med | proposed |
| I-09 | **`corpus-forge update` subcommand** — channel detection + delegation. After upgrade: chain `migrate` + `doctor` | — | `corpus_forge/cli.py::update`, `corpus_forge/update/{detect,channels}.py`, `tests/unit/test_update_channel_detect.py` (mock sys.executable paths) | med | proposed |
| I-10 | **`corpus-forge doctor` subcommand** — extracts the diagnostic-emission code already scattered through warmup() paths into a single command. Checks: Python version, extras, poppler, ffmpeg, Ollama reachable, config valid, migrations current, supervisor active | — | `corpus_forge/cli.py::doctor`, `corpus_forge/doctor/{checks,report}.py`, `tests/unit/test_doctor_checks.py` | med | proposed |
| I-11 | **Version-check ping** — daily cache, opt-out env var, async-on-daemon, sync-on-`--version` when stale | — | `corpus_forge/cli.py`, `corpus_forge/update/version_check.py`, `tests/unit/test_version_check_cache.py` | low | proposed |
| I-12 | **Homebrew tap** — `ulmentflam/homebrew-corpus-forge` repo with `Formula/corpus-forge.rb`. Release-CI step that updates the formula's SHA + URL on each tag | — | `.github/workflows/release.yml` (update step), separate `ulmentflam/homebrew-tap` repo | med | proposed |
| I-13 | **Scoop manifest** — `bucket/corpus-forge.json` for Windows. Release-CI keeps it in sync | I-05 | `.github/workflows/release.yml`, separate `ulmentflam/scoop-corpus-forge` bucket | low | proposed |
| I-14 | **Docker image** — multi-stage Dockerfile; small `[sqlite,mcp,hf]` default + `corpus-forge:full` tag with `[all]`. Publishes to GHCR | — | `Dockerfile`, `.github/workflows/release.yml` (docker build/push step), `docs/install.md` | med | proposed |
| I-15 | **Windows daemon supervisor** — `scripts/windows/install.ps1` registers a NSSM service for `corpus-forge daemon`. Downloads NSSM on first run (cached). Mirrors `scripts/{linux,macos}/install.sh` shape | I-05 | `scripts/windows/install.ps1`, `packaging/corpus-forge.nssm.template` | med | proposed |
| I-16 | **README install section rewrite** — replace the 12-extra copy-paste table with the one-liner installer + a "what does it install?" appendix. License/AGPL warning surfaced inside the installer prompt (not just docs) | I-04, I-05, I-12, I-13, I-14 | `README.md`, `docs/install.md` (new), `docs/install-update.md` (new) | low | proposed |
| I-17 | **E2E smoke** — new workflow `install-smoke.yml`: matrix over `{ubuntu-22.04, macos-14, windows-2022}` × `{install.sh, install.ps1}`. Runs the installer in non-interactive mode, then `corpus-forge doctor`, then a 1-doc ingest+query roundtrip | I-04, I-05, I-07, I-08 | `.github/workflows/install-smoke.yml` | med | proposed |
| I-18 | **P0 gate** — `make ci` + install-smoke green on all 3 OS matrix cells; manual cross-channel update test (`uv tool` → `brew` → `pipx` ladder); docs published | I-17 | — | gate | proposed |

## Definition of Done

- [ ] One-liner install works on macOS / Linux / Windows: a fresh user
      pastes a single curl/iwr command, answers ≤ 10 prompts, has a
      working corpus-forge install.
- [ ] `corpus-forge setup --non-interactive` works in CI; smoke test
      proves every backend selector wires correctly.
- [ ] `corpus-forge update` detects all 6 install channels (uv-tool,
      pipx, pip, brew, docker, source) on the three OS matrix cells.
- [ ] `corpus-forge doctor` exit-0 implies "your install is healthy"
      (every check the daemon's first-call paths perform).
- [ ] Windows is back in the CI matrix; `make test-unit` exit-0 on
      windows-2022. iCloud Drive on Windows is detected.
- [ ] Homebrew tap published; `brew install ulmentflam/tap/corpus-forge`
      works.
- [ ] Scoop bucket published; `scoop bucket add corpus-forge && scoop
      install corpus-forge` works.
- [ ] Docker images published to GHCR; `docker run -it
      ghcr.io/ulmentflam/corpus-forge:latest --help` works.
- [ ] Version-check ping pulls `https://pypi.org/pypi/corpus-forge/json`,
      caches for 24h, opt-out via `CF_NO_VERSION_CHECK=1`.
- [ ] README's install section is a one-liner per platform plus an
      "advanced: pick your extras" expandable; AGPL warning surfaces
      in the installer's `[multi-format]` prompt.

## Out of scope (defer to a later phase)

- Real plugin discovery (`entry_points` for third-party sources /
  backends / chunkers). The question tree is forward-compatible; if/
  when a `corpus-forge plugin install <name>` story lands, the wizard
  can be extended.
- `MSIX` / `winget` publication on Windows (Scoop is enough for the
  beta).
- `Flatpak` / `Snap` on Linux (apt/dnf/yum packages too — the curl
  installer covers all of these).
- Auto-update on daemon startup (only manual `update` + version-check
  ping; no auto-pull).
- Cross-machine config sync (already a separate sync feature).
- Rollback / version-pin in the update flow (`uv tool install
  corpus-forge==0.1.0b1` is the documented escape hatch for now).

## Reuse (do not reinvent)

- `scripts/{linux,macos}/install.sh` shape — keep the service-only
  scripts (called by I-15 + I-09 update flow for re-registering on
  channel migration).
- `packaging/corpus-forge.{service,plist}.template` — wire the new
  installer to render them so we don't drift.
- `tests/conftest.py:150-155` `requires_unix` marker — apply to the
  two un-marked symlink tests.
- `corpus_forge/_http.py` — endpoint probing in `corpus-forge doctor`
  uses the same `request_json` helper with `health_check=True`.
- `corpus_forge/config.py::_validate_env_var_name` — re-use for
  validating `CF_*_API_KEY_ENV` env-var names in non-interactive mode.
- `config.example.toml` — the canonical source of truth for default
  values; the question tree pulls defaults from it.
- The hermes `setup-hermes.sh` shape (clone-and-run, ~600 LOC) for
  the I-06 contributor script structure. Adopt their multi-tier
  fallback (`uv sync --locked` → `uv pip install [all]` → safe
  curated set → base install) and `_BROKEN_EXTRAS` quarantine
  pattern.

## Locked decisions (formerly open questions)

1. **Curl one-liner URL**: `https://raw.githubusercontent.com/ulmentflam/corpus-forge/main/install.sh`. Zero-cost, public, versionable by branch/tag. Revisit when v1.0 ships.
2. **Homebrew tap**: existing repo `https://github.com/ulmentflam/homebrew-tap`. Install command is `brew install ulmentflam/tap/corpus-forge`. Release CI's I-12 step PRs into that repo.
3. **NSSM bundling**: vendor the NSSM binary. `install.ps1` downloads it from nssm.cc on first run (cached under `%LOCALAPPDATA%\corpus-forge\nssm\`), SHA256-verified. No dependency on `scoop install nssm`.
4. **Version-check telemetry**: strictly anonymous. User-Agent string is `corpus-forge/<version>`; no install-id, no headers carrying machine-identifiable info. Plain GET to `https://pypi.org/pypi/corpus-forge/json`.
5. **`corpus-forge plugin install`**: omit entirely. The `questions.toml` schema is forward-compatible if/when a real plugin discovery story lands in a later phase.

## Phasing recommendation

Land in **3 PRs** to keep review tractable and CI feedback fast:

- **PR 1 (I-01, I-02)**: Windows portability fixes + restore to CI
  matrix. Low risk, immediate value. Roughly 1 day of work.
- **PR 2 (I-03–I-08, I-15, I-17)**: Installers + setup wizard + Windows
  daemon supervisor + install-smoke workflow. The core deliverable.
  Roughly 4-5 days.
- **PR 3 (I-09–I-14, I-16, I-18)**: Update story + distribution
  channels (brew tap, scoop, docker) + README rewrite + final gate.
  Roughly 3-4 days, mostly mechanical once the installer exists.
