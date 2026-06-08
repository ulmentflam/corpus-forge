# TDD Task Board — 0.1.0b17 installer `--join` pass-through + multi-host docs

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

Feature: One-line fleet onboarding. `install.sh --join <dsn>` (POSIX) /
`install.ps1 -Join <dsn>` (Windows) carry the DSN through `uv tool install`
and into `corpus-forge setup --non-interactive --join <dsn>`, skipping the
question tree (shared scope comes from the primary) and `corpus-forge
migrate` (the fleet's primary owns the schema).

Closes RFC fleet-3 items 6 and 7
(`.planning/rfcs/rfc-fleet-3-federated-config-and-setup.md`, the last
two unchecked boxes — installer pass-through + "add a second machine"
docs).

## Worktree

- Path: `/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/.claude/worktrees/agent-a30c30f239a3d6b2b`
- Branch: `worktree-agent-a30c30f239a3d6b2b`
- Base: `origin/main` @ `7f8ec4b` (PR #113 `feat(setup): --join <dsn>`)

## Project gates

- format: `ruff format --check corpus_forge tests`
- lint:   `ruff check corpus_forge tests`
- test (focused): `uv run pytest tests/scripts -x -q`
- test (suite):   `uv run pytest tests/unit tests/scripts -x -q`
- shell-syntax:   `bash -n install.sh` and `pwsh -NoProfile -File install.ps1 -SyntaxCheck` (or `[scriptblock]::Create((Get-Content install.ps1 -Raw))`)
- coverage-min:  90 (on new shell logic, per brief)
- smoke: `bash install.sh --join postgresql://stub@127.0.0.1/x` with a
  stub `corpus-forge` on PATH → expect `setup --non-interactive --join …`
  then `doctor`, NO `migrate`, exit 0.

## Existing surface (DO NOT MODIFY)

- `corpus-forge setup --join <dsn>` works interactively + non-interactively.
- `CF_JOIN_DSN=<dsn>` is the env-var equivalent (already in `cli.py`
  via `envvar="CF_JOIN_DSN"` on the `--join` option, line 474).
- `--embed-lanes a,b` is non-interactive only (cli.py:462).
- A joined host does NOT run `migrate` — the primary owns schema lifecycle.

## In-scope files

- `install.sh` (430 lines today)
- `install.ps1` (348 lines today)
- `tests/scripts/test_install_sh.py` and/or new `test_install_sh_join.py`
- `tests/scripts/test_install_sh_handoff.py` (static cross-check, may
  gain a join assertion)
- `README.md`, `CLAUDE.md`, `AGENTS.md` (docs only)
- `.planning/tdd/fleet_b17_installer_join.md` (this file)

NO changes outside that list (no version bump, no CHANGELOG, no
`corpus_forge/`).

## Tasks

| id  | title                                                  | depends_on | surface | risk | status      | claimed_by | notes |
|-----|--------------------------------------------------------|------------|---------|------|-------------|------------|-------|
| T1  | RED: tests for `install.sh --join` + `install.ps1 -Join` | —          | tests/scripts/test_install_sh_join.py (new) | low  | done        | principal#1 | 13 tests, 11 bash + 2 PS1 (gated on pwsh); initial run 11 failed / 2 skipped — proper RED |
| T2  | GREEN: `install.sh` arg parse + handoff branch + `install.ps1` mirror | T1         | install.sh, install.ps1 | med  | done        | principal#1 | arg parser added before colour block; question-tree + export-vars loops gated on `${CF_JOIN_DSN:-}`; new handoff branch runs `setup --non-interactive --join` + `doctor` (tolerant), explicitly skips `migrate`; `grep -v '^$'` made tolerant of empty input via `\| { grep \|\| true; }` (join mode collects zero extras and would have tripped `pipefail`); usage comments updated on both scripts |
| T3  | DOCS: "Add a second machine" section in README, CLAUDE, AGENTS | T2         | README.md, CLAUDE.md, AGENTS.md | low  | done        | principal#1 | README: after `### Install with Claude`, before `### Upgrade + diagnostics`; CLAUDE: after `### Recovering from HNSW`, before `## 7. Curation`; AGENTS: after §5 sanity, before §6 curation. ~40 lines each, two fenced blocks + 3 next-step bullets. `ts://` cross-ref present. |
| T4  | QA: full suite + shell syntax + manual trace          | T1,T2,T3   | (verification only) | low  | done        | principal#1 | `bash -n install.sh` OK; `tests/scripts` 51 passed / 3 skipped (pwsh-gated); regression baseline of `tests/unit tests/scripts` on origin/main = 167 fails (all pre-existing extras-missing) vs 156 fails with patch = 11 fewer fails (= new green tests), zero new failures; manual stub-`corpus-forge` trace confirmed `setup --non-interactive --join <dsn>` then `doctor`, NO `migrate`, exit 0, for both `--join <dsn>` and `CF_JOIN_DSN=<dsn>` entry points |

## DAG / Waves

- **Wave 0** (RED): T1
- **Wave 1** (GREEN): T2 (after T1)
- **Wave 2** (docs): T3 (after T2)
- **Wave 3** (QA): T4 (after T3)

## Acceptance details

### T1 — RED

Tests must drive **both** install.sh and install.ps1 paths (PS1 gated on
`shutil.which("pwsh")`). Patterns to mirror from
`tests/scripts/test_install_sh.py`:

- Pull the **post-install handoff** body out of `install.sh` between
  `__cf_post_install_handoff() {` and `# END __cf_post_install_handoff`,
  re-wrap into a function, source via `bash -c`, drive with a stub
  `corpus-forge` that logs every subcommand to a file.
- The stub MUST log the FULL `argv` (subcommand + flags), not just `$1`.
  The existing stub only records `$1`; the new tests need to assert
  `setup --non-interactive --join postgresql://...` so extend the stub
  template — preserve the existing tests' single-arg log format by
  splitting the new tests into a parallel stub.

Behaviors to assert:

1. **`--join <dsn>`** (space form) — when invoked through the question-tree
   walk + handoff, the question loop is skipped (no `CF_BACKEND` env
   write, no per-question prompts trigger). The handoff calls
   `corpus-forge setup --non-interactive --join <dsn>` exactly once.
2. **`--join=<dsn>`** (equals form) — parsed identically to #1.
3. **`CF_JOIN_DSN=<dsn>`** in caller env, no `--join` flag — same join
   behavior as #1.
4. **No-flag, no-env regression gate** — existing happy-path test in
   `test_install_sh.py` (`test_happy_path_invokes_setup_and_migrate`)
   must continue to pass UNCHANGED. The byte-equivalence guarantee.
5. **Join mode runs `doctor` after `setup --join`** — stub captures
   `[setup --non-interactive --join <dsn>, doctor]` in order, NO
   `migrate` ever appears.
6. **`doctor` failure tolerance** — when stub `doctor` exits non-zero,
   handoff prints a `WARN` mentioning `doctor` AND installer exits 0
   (mirrors today's `migrate` tolerance).
7. **Missing `corpus-forge` binary** — in join mode, the not-on-PATH
   branch warns and does NOT crash, like the non-join path does today.
8. **Static cross-check** — extend `test_install_sh_handoff.py` (or add
   a sibling) asserting `corpus-forge setup --non-interactive --join` is
   present in `install.sh` and `install.ps1` (cheap reverse-shield).

PS1 mirror (tests skipped when `pwsh` not on PATH): repeat #1, #3, #4
(non-join byte-equivalent path), #5, #6 against an inline rewrite of the
PS1 handoff block, driving a `function corpus-forge` stub.

Use absolute file paths via `Path(__file__).resolve().parents[2]`.

### T2 — GREEN

`install.sh`:

- Add arg-parser between `set -euo pipefail` (line 50) and the colour
  block. Accept `--join <dsn>` and `--join=<dsn>`. Unknown flags pass
  through silently (no error) so future flags don't need this PR.
- If `--join` was seen OR `CF_JOIN_DSN` is non-empty in env, set
  `CF_JOIN_DSN` exported in the script's env (so the question-tree skip
  predicate, the handoff branch, and the eventual `setup` invocation
  all observe the same variable).
- After parsing but before the question-tree walk: if
  `${CF_JOIN_DSN:-}` non-empty, print
  `info "Join mode — skipping question tree (shared scope comes from primary)."`
  and short-circuit the parse-questions loop AND the export-vars loop
  (no `CF_*` answers to forward, no extras to collect — `uv tool
  install corpus-forge` plain, no extras).
- In `__cf_post_install_handoff`, branch on `${CF_JOIN_DSN:-}`:
  - join: `corpus-forge setup --non-interactive --join "$CF_JOIN_DSN"`,
    then `corpus-forge doctor` (tolerant — wrap in the same
    log-and-warn pattern the existing migrate path uses), explicitly
    skip `corpus-forge migrate`.
  - non-join: identical to today (setup → migrate).
- Final "Done" message in join mode names `bench embed --all` and
  `service install` as next ops (per brief).
- Update the top-of-file usage comment (lines 27–32) to document
  `--join` / `CF_JOIN_DSN`.

`install.ps1`:

- Add `param([string]$Join)` at the very top (BEFORE
  `Set-StrictMode`).
- If `$Join` non-empty OR `$env:CF_JOIN_DSN` non-empty, set
  `$env:CF_JOIN_DSN = $Join` (taking precedence over a pre-set env).
- Question-tree walk: early-return analogous to install.sh's
  short-circuit.
- Handoff block: parallel branch. PS1's `try`/`catch` + `$LASTEXITCODE`
  pattern for the migrate-tolerance path is the template to copy for
  the new doctor-tolerance path.
- Update the `.SYNOPSIS` / `.EXAMPLE` comment block (lines 1–23) to
  document `-Join` / `$env:CF_JOIN_DSN`.

Backwards compat: when no `--join`/`-Join`/env, the script paths MUST be
byte-equivalent for the existing tests in `test_install_sh.py`. Easiest
way is to gate every new branch behind an `if [[ -n "$CF_JOIN_DSN" ]]`
guard at the top of the function and only enter the new arms inside.

### T3 — DOCS

Add an **"Add a second machine"** section to each of:

- `README.md` — after the existing install one-liners (between line ~56
  "Install with Claude (copy-paste prompt)" and "Upgrade + diagnostics",
  or as a peer subsection under `## Install`). Lead with a one-paragraph
  framing, then two fenced blocks (bash + pwsh), then a 3-bullet "what
  comes next" list (`doctor`, `bench embed --all`, `service install`).
  Cross-reference `ts://` DSN (RFC fleet-4).
- `CLAUDE.md` — short subsection after `## 6. First-run sanity` framed
  as the assistant-facing path: when the user wants to add a host, this
  is the one-command path; explain why join mode skips `migrate`.
- `AGENTS.md` — mirror the CLAUDE.md content in the AGENTS format.

Keep each ~25–40 lines. No drive-by reorganization of the surrounding
text. Use the worktree's current README/AGENTS heading style as the
template.

### T4 — QA

- `bash -n install.sh` returns 0.
- PowerShell parse: `pwsh -NoProfile -Command "$null = [scriptblock]::Create((Get-Content -Raw install.ps1))"` (skip if pwsh absent).
- `uv run pytest tests/scripts -x -q` green.
- `uv run pytest tests/unit -x -q` green (existing suite, regression
  gate).
- Manual trace: spawn a tmp dir, drop a stub `corpus-forge` that
  appends its argv to a log, run
  `PATH=$tmpdir:/usr/bin:/bin bash install.sh --join "postgresql://stub@127.0.0.1/x"`
  and confirm the log lists
  `setup --non-interactive --join postgresql://stub@127.0.0.1/x` then
  `doctor` — no `migrate`. Repeat with `CF_JOIN_DSN` env-var form.
- Confirm coverage on the new shell branch is ≥ 90% (assertions in
  tests cover each new line).

## Out of scope

- No version bump in `pyproject.toml`, no CHANGELOG churn, no tag.
- No new top-level deps.
- No edits to `corpus_forge/`.
- No changes to non-installer scripts.
- Do NOT commit. Workers stage only — Principal commits on their behalf
  (1Password SSH signing needs a TTY the subagent doesn't have).

## References

- RFC: `.planning/rfcs/rfc-fleet-3-federated-config-and-setup.md`
  (items 6 + 7, last two unchecked boxes)
- Existing `setup --join` CLI: `corpus_forge/cli.py:444-531`
- Existing handoff tests pattern: `tests/scripts/test_install_sh.py`,
  `tests/scripts/test_install_sh_handoff.py`
