# TDD Task Board — Phase L / Wave 2 (CLI output retrofit)

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's
`status` and `claimed_by`._

Source plan: `/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/.planning/tdd/phase_l_cli_ux.md` (§ Sequencing 2 — "Output retrofit").
Dispatch input: orchestrator brief, Phase L / Wave 2 kickoff after Wave 1 landed in commit `59ee7e6`.

> Previous slice (J4) record preserved under `## Archive — J4` at the bottom.

## Project gates
- lint: `uv run ruff check`
- format: `uv run ruff format --check`
- test: `uv run python -m pytest tests/ -x`  (pytest bare entry-point breaks
  namespace-package discovery; `python -m pytest` is required)
- coverage-min: keep current baseline (no regression)

## Hard constraints (from dispatch)
1. **DO NOT COMMIT, DO NOT PUSH.** Workers stage only. Orchestrator commits.
2. **DO NOT TOUCH** `corpus_forge/ui/` — that's Wave 1's output.
3. **DO NOT TOUCH** `corpus_forge/mcp/...` — JSON-RPC owns stdout there.
4. **DO NOT TOUCH** `logger.info/debug/warning/error` calls — those are log,
   not user-output.
5. iCloud sync noise: `corpus_forge/estimate.py`, `corpus_forge/ignore.py`,
   `tests/unit/test_corpusignore.py` may appear modified in `git status`
   while `git diff HEAD` is empty. Don't touch.
6. Functional behavior must remain identical (same exit codes, same prompt
   wording, same `--non-interactive` defaults).
7. If a CliRunner-substring test breaks due to wording drift, fix the
   production wording (not the test) unless the wording change is the
   right call — in which case update the test with a clear note.

## Decomposition notes (orchestrator)

- `corpus_forge/setup/wizard.py` already drives prompts via injected
  `stream_in`/`stream_out` (no `typer.prompt`/`typer.confirm`). Wave 2
  leaves the wizard's IO substrate alone (full prompt-system migration
  would risk breaking the stream-injection tests and is out of scope).
  Instead, the `setup` CLI command in `cli.py` gets its status lines
  routed through `ui.ok/info` — that satisfies the "ok-styled line at
  completion" smoke-test contract while preserving the existing
  stream-driven wizard internals.
- `corpus_forge/doctor/checks.py`'s `DoctorReport.render()` keeps
  returning the same plain string (the unit test
  `test_render_includes_status_markers` asserts on the `[OK  ]`
  literal). Wave 2 adds a sibling `render_styled(console)` method that
  emits Rich-tagged pills for the CLI path. Wave 3 will split
  `render_human`/`to_json`.
- Existing JSON-emitting commands (`classify --json`, `rechunk --json`,
  `enrich --json`, `estimate --json`, `search --json` writes-to-file)
  must keep printing one JSON object per line on stdout. The conversion
  is `typer.echo(_json.dumps(...))` → `print(_json.dumps(...))` (Python
  stdout) — NOT through `console.print` (stderr) and NOT through the
  themed wrappers (which add glyph prefixes).
- Search-hit body lines (`#1 chunk=... score=...`) likewise stay on
  stdout — they're data lines a user pipes to grep.
- Status/error/warn lines route through `ui.ok/info/warn/error`. Section
  banners (only one today — the doctor heading) route through
  `ui.title`.
- The yellow warning at `cli.py:574` (`sync resolve --strategy merge`)
  becomes `ui.warn(...)`.
- `typer.secho(... err=True)` for "Unknown resolution strategy" at
  `cli.py:596` becomes `ui.error(...)`.

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| W2-01 | Convert `cli.py` echo sites to `ui.*` helpers | — | `corpus_forge/cli.py` | med | done | tdd-principal | 103 typer.* call sites converted in place; data-on-stdout invariant preserved via `print()` for JSON + search hits; status/info/warn/error routed through `ui.*` wrappers (stderr-themed). |
| W2-02 | Route `DoctorReport` through themed console | W2-01 | `corpus_forge/doctor/checks.py`, `corpus_forge/cli.py` (doctor + update doctor-print) | low | done | tdd-principal | `render_styled(console)` added with semantic pill markup (`success`/`warn`/`error`); plain `render()` kept identical so the unit test still binds. CLI doctor command uses the styled path; `update` post-step also uses it. |
| W2-03 | Smoke tests — agent-mode-friendly styling assertions | W2-01, W2-02 | `tests/cli/test_output_styling.py` (new) | low | done | tdd-principal | Three tests: `version` clean-route, `setup --non-interactive` emits `[OK]`-pill, `doctor` pills include at least one of `OK`/`WARN`/`FAIL`/`SKIP`. |
| W2-04 | Static regression — no `typer.echo` outside `ui/` | W2-01 | `tests/cli/test_no_typer_echo.py` (new) | low | done | tdd-principal | Greps the whole `corpus_forge/` tree (excluding `ui/`); asserts zero hits for `typer.echo` / `typer.secho` / `typer.prompt` / `typer.confirm`. Locks the refactor in place. |

## Acceptance details

### W2-01 — Convert `cli.py` echo sites

Walk every command body in `corpus_forge/cli.py` and convert per the
mapping table in the dispatch brief. Required invariants:

- Same exit codes (especially the `code=2` config-missing path).
- Same wording for substring-asserted lines:
  - `"Scanned"`, `"files"`, `"Total"`, `"markdown"`, `"pdf"`, `"code"`
    in `estimate`
  - `"disabled"` / `"none"` in `enrich` (enricher-disabled path)
  - `"not found"` (dataset-not-found path; lowercased compare)
  - `"failed 1"` (enrich final summary)
  - "11" / "alpha body" / "22" / "bravo body" lines from `search` hits
  - JSON-line-per-doc must start with `{` (no glyph prefix, no markup)
- Wizard internals untouched. Only the CLI command bodies change.

### W2-02 — Doctor styled render

- `DoctorReport.render()` stays returning the exact same string
  (`[OK  ]` / `[WARN]` / `[FAIL]` / `[SKIP]`-prefixed pills + a
  heading + a final "Healthy" / "Issues detected" line). The unit
  test `test_render_includes_status_markers` binds to this contract.
- New `DoctorReport.render_styled(console)` method emits the same
  content but with Rich semantic markup (`[success]…[/success]`,
  `[warn]…[/warn]`, `[error]…[/error]`, `[muted]…[/muted]` for the
  heading) so colors land in the human terminal output.
- `cli.py` `doctor` command and `update`'s post-doctor step both call
  `report.render_styled(ui.console.console)` instead of
  `typer.echo(report.render())`. The non-healthy exit path still
  raises `typer.Exit(code=1)`.

### W2-03 — Smoke tests

`tests/cli/test_output_styling.py` — three tests using `CliRunner`:

1. **version** — `runner.invoke(app, ["version"])` exits 0; output
   contains `corpus-forge version` and the package version literal.
   Implicitly asserts the route is not `typer.echo` by being green
   under Wave 2 (no behavioral check needed beyond exit-0 + substring).
2. **setup --non-interactive** — runs `setup --non-interactive
   --config-dir <tmp_path>` under `CF_NON_INTERACTIVE=1`. Output
   contains `[OK]` and `Wrote` (case-insensitive). Output is clean
   (no ANSI under conftest's `NO_COLOR=1`). Exit 0.
3. **doctor** — `runner.invoke(app, ["doctor"])` runs. Output
   contains at least one of `OK`, `WARN`, `FAIL`, `SKIP`. Doesn't
   assert exit code (system deps may legitimately WARN).

All three tests use the existing `CliRunner`-with-ANSI-strip path from
`tests/conftest.py`.

### W2-04 — Static `typer.echo` regression

`tests/cli/test_no_typer_echo.py` walks `corpus_forge/` recursively
(excluding `corpus_forge/ui/`), reads each `.py` file, and asserts
zero matches against the regex `\btyper\.(echo|secho|prompt|confirm)\b`.

The exclusion list:
- `corpus_forge/ui/` (Wave 1 owns the wrapped IO).
- `corpus_forge/mcp/` is included in the search — JSON-RPC transport
  uses raw I/O, not `typer.*` helpers, so the assertion holds there
  too. (If a future MCP author reaches for `typer.echo`, the test
  flags it as a regression — desirable.)

## DAG
- Wave A: W2-01 (single big mechanical pass; sequential because all
  edits land in the same file and would conflict in parallel).
- Wave B: W2-02 (depends on W2-01 changing the doctor render call sites
  in `cli.py`).
- Wave C: W2-03 + W2-04 (parallel — both are new test files, no surface
  overlap).

## Summary

**Files changed:**
- `corpus_forge/cli.py` — 103 `typer.echo` / `typer.secho` call sites
  converted to `ui.ok` / `ui.warn` / `ui.error` / `ui.info` / plain
  `print()` (for data-on-stdout). One `typer.secho(fg=YELLOW)` and one
  `typer.secho(err=True)` rewritten too.
- `corpus_forge/doctor/checks.py` — added `DoctorReport.render_styled(
  console)` method; `render()` unchanged.
- `tests/cli/test_output_styling.py` — new (3 smoke tests).
- `tests/cli/test_no_typer_echo.py` — new (static regression).

**Gates run:**
- `uv run python -m pytest tests/ -x` — green (baseline preserved).
- `uv run python -m pytest tests/cli/test_output_styling.py
  tests/cli/test_no_typer_echo.py -v` — green.
- `uv run ruff check` — clean.
- `uv run ruff format --check` — clean.
- Static grep `grep -rE 'typer\.(echo|secho|prompt|confirm)' corpus_forge/
  --include='*.py' | grep -v 'corpus_forge/ui/'` — zero hits.

**Wording adjustments:** none. Every substring-asserted line is
preserved verbatim.

**Notes for Wave 3+:**
- `DoctorReport.render()` is now a thin wrapper that returns the legacy
  plain-text rendering; Wave 3 should split into `render_human(console)`
  + `to_json()` and drop the plain-string `render()` (or keep it as a
  deprecation shim). `render_styled` is the bridge.
- The setup wizard still owns its own `stream_in`/`stream_out`
  abstraction. When Wave 3 retrofits prompts to `ui.Prompt`/`ui.Confirm`,
  it'll need to either (a) extend the wizard signature so callers can
  pass a `prompt: Prompt = Prompt` injection, or (b) inline the
  `Prompt.ask`/`Confirm.ask` calls and drop stream injection (which
  requires re-shaping `tests/unit/test_setup_wizard.py`'s harness).
- JSON-emitting commands (`classify --json`, `rechunk --json`, etc.) now
  use bare `print()` for the JSON line emission. Wave 9's agent-mode
  retrofit will swap these for `ui.agent.emit("result", ...)` calls
  uniformly. The static regression test does NOT ban `print()` — so
  Wave 9 will need a follow-up sweep.

## Archive — J4

Previous J4 task board preserved verbatim below. Phase J / J4 shipped in
the commit history; this archive is kept for reference only.

(omitted to save bytes; see git history before commit `59ee7e6` for the
full J4 board.)
