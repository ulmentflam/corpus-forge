# TDD Task Board — Phase L / Wave 8 (Service lifecycle: status/start/stop/restart/install)

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
