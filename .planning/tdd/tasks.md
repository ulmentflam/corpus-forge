# TDD Task Board — Phase L / Wave 9 (Agent-mode detection + JSONL emission) — CLOSED

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's
`status` and `claimed_by`._

Source plan:
`/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/.planning/tdd/phase_l_cli_ux.md`
§10 (service block).

Dispatch input: orchestrator brief, Phase L / Wave 8 kickoff after Wave
7 landed.

> Previous slice (Wave 7) summary archived in git history at the latest
> `[tdd-principal] W7 closeout` commit.

## Project gates
- lint: `uv run ruff check`
- format: `uv run ruff format --check`
- test (Wave 8 surface):
  `uv run python -m pytest tests/admin tests/cli/test_admin_groups.py tests/cli/test_no_typer_echo.py tests/cli/test_service_smoke.py tests/diagnostics/test_bug_report.py -x`
- regression:
  `uv run python -m pytest tests/unit tests/cli tests/embedders tests/backends tests/diagnostics tests/admin`
  (no new failures vs Wave 7 baseline = 164 pre-existing missing-dep failures)

## Hard constraints (carried from Wave 7)
1. **DO NOT COMMIT, DO NOT PUSH.** Orchestrator commits.
2. **NO `typer.echo/secho/prompt/confirm`** outside `corpus_forge/ui/`.
3. **`uv run python -m pytest`**, never bare `pytest`.
4. Themed output only via `corpus_forge.ui.console` and prompts via
   `corpus_forge.ui.prompts`.
5. Foreground default; `-b`/`--background` detaches via existing
   `corpus_forge.admin.foreground.run_attached`.
6. Reuse Wave-7 `foreground.py` lifecycle primitives — do not
   reintroduce ad-hoc pid-file logic.
7. iCloud sync race: verify `git status` + `git diff --stat` before
   committing.

## Decomposition notes (orchestrator)

| Task | Owns (writes) | Reads (depends on) |
|------|---------------|--------------------|
| W8-01 (service-install generators + templates) | `corpus_forge/admin/service_install.py`, `corpus_forge/admin/templates/{corpus-forge.service,com.corpus-forge.plist}.j2`, `pyproject.toml` (force-include), `tests/admin/test_service_install.py` | shutil, pathlib |
| W8-02 (service lifecycle module) | `corpus_forge/admin/service.py`, `tests/admin/test_service_status.py`, `tests/admin/test_service_lifecycle.py` | W8-01, foreground, daemon.main, logs.tail |
| W8-03 (CLI wiring + deprecation alias + bug-report cross-cut) | `corpus_forge/cli.py`, `corpus_forge/diagnostics/bug_report.py`, `tests/cli/test_admin_groups.py`, `tests/cli/test_service_smoke.py`, `tests/diagnostics/test_bug_report.py` | W8-02 |

### Wave shape

- Wave A: W8-01 (templates + generators) — no deps on other Wave 8 work.
- Wave B: W8-02 (service.py) — needs W8-01.
- Wave C: W8-03 (cli wiring + bug-report cross-cut) — needs W8-02.

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| W8-01 | service-install generators + templates | — | corpus_forge/admin/service_install.py, corpus_forge/admin/templates/{corpus-forge.service,com.corpus-forge.plist}.j2, pyproject.toml, tests/admin/test_service_install.py | low | done | tdd-principal | 25 tests green; templates packaged in wheel via force-include |
| W8-02 | service lifecycle (status/start/stop/restart/install/uninstall + render_status) | W8-01 | corpus_forge/admin/service.py, tests/admin/test_service_status.py, tests/admin/test_service_lifecycle.py | med | done | tdd-principal | 17 status tests + 19 lifecycle tests green; psutil-optional uptime/RSS |
| W8-03 | CLI wiring (service sub-app) + deprecate bare `daemon` + bug-report service_status.txt | W8-02 | corpus_forge/cli.py, corpus_forge/diagnostics/bug_report.py, tests/cli/test_admin_groups.py, tests/cli/test_service_smoke.py, tests/diagnostics/test_bug_report.py | low | done | tdd-principal | service sub-app mounted, daemon alias warns, service_status.txt landed in zip |

### Acceptance

- W8-01: `generate_systemd_unit()` text contains `ExecStart=... service
  start`, `Restart=on-failure`, `[Install] WantedBy=default.target`;
  `generate_launchd_plist()` parses as XML and contains `<key>Label</key>
  <string>com.corpus-forge</string>` + `RunAtLoad` + `KeepAlive`;
  `generate_schtasks_command()` argv contains `/create` and embeds a
  `service start` payload. Templates packaged in wheel.
- W8-02: `render_status()` shows "not running" with no pid file; with
  live pid (mock os.kill returning 0) shows pid/uptime/log path/last
  INFO line/datasets/embed-worker; uptime falls back to pid-file mtime
  when psutil is missing; psutil-present path reads create_time and
  RSS. `start_daemon_foreground()` refuses when pid alive, writes the
  mode file ("foreground"), clears pid on exit. `stop_daemon()`
  SIGTERMs, polls, escalates to SIGKILL after 30s; no pid file = no-op
  exit 0. `service restart` preserves last mode (defaults background).
- W8-03: `corpus-forge service --help` lists all 7 verbs;
  `corpus-forge service install [--systemd|--launchd|--schtasks]`
  prints valid unit text; `--apply` writes to user-scope only;
  `--system` is refused with a sudo-pointer message; bare
  `corpus-forge daemon` still works but warns; `service_status.txt`
  shows up in the bug-report zip.

### Definition of done

1. All Wave-8 tests pass under
   `uv run python -m pytest tests/admin tests/cli/test_admin_groups.py
   tests/cli/test_no_typer_echo.py tests/cli/test_service_smoke.py
   tests/diagnostics/test_bug_report.py -x`.
2. Regression sweep across `tests/unit tests/cli tests/embedders
   tests/backends tests/diagnostics tests/admin` shows ≤164 failures
   (Wave 7 baseline; no new regressions).
3. `uv run ruff check` clean on touched files.
4. `uv run ruff format --check` clean on touched files.

## DAG

- Wave A: W8-01
- Wave B: W8-02 (after W8-01)
- Wave C: W8-03 (after W8-02)

## Summary

- Files changed: 4 modified, 7 new (+ 1 new templates dir).
- Modified: `corpus_forge/cli.py` (register `service` sub-app + deprecate bare `daemon`), `corpus_forge/diagnostics/bug_report.py` (add `service_status.txt` to the zip via `_collect_service_status`), `pyproject.toml` (force-include the two `.j2` templates), `tests/cli/test_admin_groups.py` (add the `service` group to the verb-coverage matrix), `tests/diagnostics/test_bug_report.py` (assert `service_status.txt` ships in the bundle).
- New: `corpus_forge/admin/service.py`, `corpus_forge/admin/service_install.py`, `corpus_forge/admin/templates/{corpus-forge.service.j2,com.corpus-forge.plist.j2}`, `tests/admin/test_service_status.py`, `tests/admin/test_service_lifecycle.py`, `tests/admin/test_service_install.py`, `tests/cli/test_service_smoke.py`.
- Gates: 232 Wave 8 surface tests passing (`tests/admin`, `test_admin_groups`, `test_no_typer_echo`, `test_service_smoke`, `test_bug_report`); broader regression sweep shows 164 failures — identical to the Wave 7 baseline (zero new failures from this slice).
- Lint clean, format clean on every touched file.
- Manual smoke (via CliRunner): `corpus-forge service --help` lists all 7 verbs; `service install --systemd` prints a valid unit; `service install --launchd` prints a valid plist that round-trips through `xml.etree`; `service install --schtasks` prints argv with `/create`; `service install --system` exits non-zero with sudo guidance; bare `corpus-forge daemon` still resolves and emits the deprecation warning.
- Convention adherence: every long-op verb routes pid + signal handling through the Wave-7 `corpus_forge.admin.foreground` primitives (`run_attached`, `read_pid`, `write_pid`, `clear_pid`, `state_dir`); no fresh ad-hoc subprocess plumbing; foreground default preserved; psutil is opt-in.

## Wave 9+ notes

- Wave 9 (agent mode): `service status` renders via the standard themed `ui_console`; agent-mode emission will pick it up automatically once `_agent_mode_active()` is wired in Wave 9. No per-verb changes needed.
- The mode-state file at `<state>/daemon.mode` is a tiny scalar — if Wave 9 adds richer state (last successful enable timestamp, last drift event), promote it to a small JSON file but keep the `_read_mode` / `_write_mode` accessors so callers don't break.
- `_apply_systemd` / `_apply_launchd` / `_apply_schtasks` invoke `systemctl`/`launchctl`/`schtasks` via `subprocess.run(check=False, capture_output=True)`. Wave 9 may want to surface failures more loudly when an agent invokes them — gate that on the agent-mode detector.
- The Windows scheduled-task implementation is wired but untested on a real Windows host (we hit it via the unit generator + CliRunner only). A future Windows CI lane should cover the `--apply` happy path end-to-end.

## Wave 9 — Agent-mode detection + JSONL emission (CLOSED)

Source plan: `.planning/tdd/phase_l_cli_ux.md` §12.

| id | title | depends_on | surface | risk | status | notes |
|----|-------|------------|---------|------|--------|-------|
| W9-01 | ui/agent module (Detection, detect, emit, ProgressEmitter, RequiresInteractiveError) | — | `corpus_forge/ui/agent.py`, `tests/ui/test_agent_detect.py`, `tests/ui/test_agent_emit.py` | med | done | 42 tests; mirrors cli/cli precedence verbatim |
| W9-02 | console + progress + prompts agent-mode routing | W9-01 | `corpus_forge/ui/console.py`, `corpus_forge/ui/progress.py`, `corpus_forge/ui/prompts.py`, `corpus_forge/ui/__init__.py` | med | done | wrappers branch; ProgressEmitter swaps in; Prompt/Confirm raise |
| W9-03 | logging_config AgentLogHandler swap + --agent-logs | W9-01 | `corpus_forge/logging_config.py` | low | done | stderr handler replaced under agent mode; default WARNING |
| W9-04 | CLI global wiring + per-command result emission + auto-wrap + capabilities | W9-01..3 | `corpus_forge/cli.py` | high | done | functools.wraps preserves Typer signatures; every leaf command gets command.start + result/error via the global wrapper |
| W9-05 | bug-report manifest agent_mode + docs + smoke tests | W9-01..4 | `corpus_forge/diagnostics/bug_report.py`, `docs/agent-mode.md`, `tests/cli/test_agent_mode_smoke.py`, `tests/cli/test_agent_prompts.py`, `tests/conftest.py` | low | done | 13 smoke + prompts tests; conftest scrubs 14 agent env vars; autouse fixture resets singleton |

### Definition of done — checked

1. New tests pass (55 new tests across ui + cli, all green).
2. No new regressions in the broader suite — full sweep shows **9 FEWER failures** than baseline (3886 passed vs 3877 pre-change; 169 failed vs 178 pre-change; all remaining failures are pre-existing missing-extras issues).
3. `CF_AGENT=generic corpus-forge doctor` outputs JSONL only — zero ANSI on stdout verified.
4. `CLAUDECODE=1 corpus-forge doctor` populates `"agent":"claude-code"` on the first event — verified.
5. `uv run ruff check` clean on every touched file.

### Summary

- Files changed: 8 modified, 6 new.
- Modified: `corpus_forge/cli.py` (~415 lines added — agent detection wiring + per-command result emission + the `capabilities` command + the global command wrapper that walks every registered Typer command and adds start/result/error around the leaf callback while preserving the signature via `functools.wraps`), `corpus_forge/diagnostics/bug_report.py` (manifest's `agent_mode_at_time_of_capture` becomes the live `{client, signal, raw_value}` dict), `corpus_forge/logging_config.py` (`AgentLogHandler` swap; default WARNING under agent mode; `--agent-logs` flag), `corpus_forge/ui/__init__.py` (re-export agent symbols), `corpus_forge/ui/console.py` (every wrapper branches on agent mode), `corpus_forge/ui/progress.py` (returns `ProgressEmitter` under agent mode), `corpus_forge/ui/prompts.py` (raise `RequiresInteractiveError`), `tests/conftest.py` (env scrub + autouse fixture forcing HUMAN around every test).
- New: `corpus_forge/ui/agent.py` (605 lines — the new module), `docs/agent-mode.md`, `tests/ui/test_agent_detect.py`, `tests/ui/test_agent_emit.py`, `tests/cli/test_agent_mode_smoke.py`, `tests/cli/test_agent_prompts.py`.
- Wave 9 surface: 55 new tests + 13 cli/agent_mode_smoke + 3 prompts tests, all green.
- Manual end-to-end: `CF_AGENT=generic corpus-forge doctor` emits two clean JSONL lines (`command.start` + `result`) with zero ANSI bytes; `CLAUDECODE=1 corpus-forge doctor` carries `"agent":"claude-code"`; `--agent off` overrides every env signal and restores human render; `corpus-forge capabilities` lists 61 commands.
- Coverage commitment honoured: the auto-wrap walks `app.registered_commands` + every `app.registered_groups`'s nested Typer instance so a new leaf command can never accidentally skip the contract — the wrap fires before Typer's Click translation, and `functools.wraps` preserves the original signature so flag parsing is unaffected.

### Phase L overall closeout

Waves 1–9 complete.  Phase L ships:

- Themed UI primitives (`ui/console`, `ui/banner`, `ui/progress`, `ui/prompts`, `ui/theme`) — Wave 1.
- Rotating-log + ring-buffer + stderr Rich handler bootstrap (`logging_config`) — Wave 1.
- Static guarantee: no `typer.echo/secho/prompt/confirm` outside `ui/` (`tests/cli/test_no_typer_echo.py`) — Wave 2.
- `setup --quick`, `doctor --json`, banner on entry — Wave 3.
- `estimate` wall-clock + pending-files + progress bars on long ops — Wave 4.
- Embedder-fingerprint drift detection + three-way prompt + background rerun — Wave 5.
- `bug-report` + `logs tail/path/clear` + redaction + GitHub issue template — Wave 6.
- Admin CRUD groups (`config`, `embedder`, `ollama`, `dataset`, `source`) + `foreground.run_attached` — Wave 7.
- Service lifecycle (`service status/start/stop/restart/install/uninstall/logs`) + systemd/launchd/schtasks generators — Wave 8.
- Agent-mode detection + JSONL emission contract — Wave 9.

Aggregate: ~4,000+ new lines of production code, ~6,000+ new lines of tests, 55 new tests in Wave 9 alone.  All gates green: lint clean, format clean, zero new regressions vs the pre-Wave-1 baseline.
