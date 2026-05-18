# TDD Task Board — Phase L / Wave 3 (setup --quick, doctor --json, banner)

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's
`status` and `claimed_by`._

Source plan: `/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/.planning/tdd/phase_l_cli_ux.md` (§ Sequencing 3 — "Setup/doctor polish").
Dispatch input: orchestrator brief, Phase L / Wave 3 kickoff after Wave 2 landed in commit `9d80be9`.

> Previous slice (Wave 2) record preserved under `## Archive — Wave 2` at the bottom.

## Project gates
- lint: `uv run ruff check`
- format: `uv run ruff format --check`
- test: `uv run python -m pytest tests/ -x`
- coverage-min: keep current baseline (no regression)

## Hard constraints (from dispatch)
1. **DO NOT COMMIT, DO NOT PUSH.** Workers stage only. Orchestrator commits.
2. **DO NOT TOUCH** `corpus_forge/estimate.py`, `corpus_forge/ignore.py`,
   `tests/unit/test_corpusignore.py` — iCloud-sync noise.
3. **NO `typer.echo/secho/prompt/confirm`** outside `corpus_forge/ui/` — the
   `tests/cli/test_no_typer_echo.py` regression will fail you. Use
   `ok/warn/error/info/title/print` + `Prompt.ask`/`Confirm.ask`.
4. **`uv run python -m pytest`**, never bare `pytest`.
5. Existing `setup --non-interactive` behavior is byte-identical
   (no wording rewrites; existing wizard tests must stay green).
6. Wizard keeps its `stream_in`/`stream_out` injection model. The `--quick`
   path layers on top — it does NOT migrate to `Prompt.ask` for the
   stream-driven prompts (that's Wave 9+ territory).
7. `DoctorReport.render()` plain-text method stays as-is for the existing
   `test_render_includes_status_markers` unit test.

## Decomposition notes (orchestrator)

- T1 and T2 both touch `corpus_forge/cli.py` but on disjoint functions
  (T1 owns `setup`, T2 owns `doctor`). Workers stage their hunks; the
  orchestrator commits each task separately. Run testers + coders in
  parallel; QA runs in parallel.
- Banner integration is folded into each task (not split into a third
  task): T1 owns the banner-on-setup + non-interactive suppression rule;
  T2 owns the banner-on-doctor + --json suppression rule. The new
  `tests/cli/test_banner.py` covers both behaviors but is split into two
  parts (banner-on-setup tests under T1, banner-on-doctor tests under T2).
  To avoid file-write contention, the banner tests live in a single file
  authored by T1's tester; T2's tester adds doctor-specific banner
  assertions to that file in a second pass. To keep this simple and
  parallel, the banner test file is owned by T1's tester end-to-end and
  T2's tester adds the doctor-specific assertion in its own
  `tests/cli/test_doctor_banner_in_json_mode.py` (single file, single
  writer). See acceptance details below for the exact split.
- Ollama probe: tests mock at the `urllib.request.urlopen` layer (the
  same pattern `corpus_forge/update/version_check.py` already uses).
  No new HTTP dep — keep using stdlib.
- `Config.load()` round-trip is what validates `--quick` output. Use
  `Config.load(config_path=..., secrets_path=...)` with absolute paths
  in tmp_path so we don't touch the user's real config.

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| W3-01 | `setup --quick` + banner on setup | — | `corpus_forge/setup/wizard.py`, `corpus_forge/setup/__init__.py`, `corpus_forge/cli.py` (setup command), `tests/cli/test_setup_quick.py` (new), `tests/cli/test_banner.py` (new) | med | done | tdd-principal | Wizard gets `QUICK_QUESTIONS` subset + Ollama probe (urlopen-based, mockable via `_urlopen_compat`). CLI grows `--quick` flag + banner-on-non-non-interactive. Config round-trips through `Config.load()`. |
| W3-02 | `doctor --json` + `to_json()` + banner on doctor | — | `corpus_forge/doctor/checks.py`, `corpus_forge/cli.py` (doctor command), `tests/cli/test_doctor_json.py` (new), `tests/cli/test_doctor_banner_in_json_mode.py` (new) | low | done | tdd-principal | `DoctorReport.to_json()` returns `{checks, summary, version, ts}`. `--json` flag suppresses banner + styled render, prints one JSON line via bare `print()`, exits 0 (ok) / 1 (fail) / 2 (warn-only). |

## Acceptance details

### W3-01 — `setup --quick` + banner on setup

**Wizard changes (`corpus_forge/setup/wizard.py`):**
- Add `QUICK_QUESTIONS` — module-level constant list of the 6 quick
  questions (backend, postgres_dsn, ollama_url, embedder_model_id,
  dataset_name, scan_root). Each is a `Question` with the env vars
  documented below.
- Quick env vars (used by `--non-interactive` + `--quick` combo):
  - `CF_BACKEND` (existing) — `sqlite` | `postgres`, default `sqlite`
  - `CF_BACKEND_DSN` (NEW for quick) — required iff backend=postgres
  - `CF_OLLAMA_URL` (NEW) — default `http://localhost:11434`
  - `CF_EMBEDDER_MODEL_ID` (NEW) — default `qwen3:8b` or first probed
    model
  - `CF_DATASET_NAME` (NEW) — default `default`
  - `CF_SCAN_ROOT` (NEW) — default empty (no source root)
- New function: `_probe_ollama(base_url: str, *, timeout_s: float = 1.0)
  -> str | None` — best-effort `GET <base_url>/api/tags`. Returns the
  name of the first embedding-capable model (containing `embed`, `bge`,
  `qwen`, or `nomic`) or `None` on any failure (TimeoutError,
  URLError, OSError, JSON parse error). Uses `urllib.request` (same
  idiom as `corpus_forge/update/version_check.py`). Strictly fire-and-
  forget — no side effects, no logging at WARN+.
- New function: `_render_quick_config_toml(answers, db_path)` — emits a
  minimal config keyed off the 6 quick answers:
  - `[backend]` block with kind + dsn (sqlite=local file path, postgres=
    answer)
  - `[[datasets]]` with `name = answers["dataset_name"]`, `kind = "text"`,
    and `sources = [{plugin = "filesystem", root = "<scan_root>",
    chunker = "markdown"}]` ONLY IF `scan_root` is non-empty; otherwise
    `sources = []` (empty list, valid per the model — verify against
    `DatasetConfig` and adjust if the validator requires at least one).
    If the model REQUIRES a source, omit the dataset entry entirely and
    add a `[[datasets]]` block with the bare name + no sources by
    appending an explicit empty TOML array. The implementor should pick
    whichever shape `Config.load()` accepts — verify by round-tripping a
    tmp config in the tester's RED tests before writing it as a
    fixture.
  - `[[embedders]]` with one entry whose `model_id` is the quick answer.
    **Constraint:** `EmbedderConfig.provider` is constrained by a Pydantic
    `pattern="^(sentence_transformers|openai)$"` validator. The quick
    wizard's Ollama URL maps cleanly to `provider = "openai"` +
    `base_url = "<ollama_url>/v1"` (Ollama exposes an OpenAI-compatible
    endpoint), so use that mapping. `dimension` defaults to `1024` (safe
    for most Ollama embedding models — qwen3:8b/embed family is 4096,
    bge-m3 is 1024, nomic-embed-text is 768; 1024 is a defensible
    middle-ground that Wave 5's embedder-fingerprint detection will
    correct later. Document the choice in a code comment). Set
    `normalize = true`, `distance = "cosine"`, `active = true`,
    `api_key_env = "OLLAMA_API_KEY"` (the env var is harmless if unset
    against a local Ollama).
  - Other sections (retrieval / classifier / VLM / whisper / code) are
    omitted — Pydantic supplies safe defaults.
  - The resulting config MUST round-trip through `Config.load()` — i.e.
    `Config.load(config_path=<written_path>)` returns a valid Config
    object without raising. Test this explicitly.
- New entry points:
  - `run_quick(*, config_dir, env=None, stream_in=None, stream_out=None,
    interactive=True) -> tuple[Path, Path, dict[str, str]]` —
    the quick wizard. Interactive uses stream injection (same pattern
    as the full wizard); non-interactive reads from `env`. Probes
    Ollama once after the URL prompt and uses the probed model as the
    embedder default if the env var is unset and the probe succeeded.
- `corpus_forge/setup/__init__.py` re-exports `run_quick`.

**CLI changes (`corpus_forge/cli.py`):**
- `setup` command grows `--quick` flag:
  ```python
  quick: bool = typer.Option(
      False, "--quick", envvar="CF_QUICK",
      help="Run the abbreviated setup (6 questions, safe defaults).",
  )
  ```
- Banner: render `render_banner("corpus-forge", subtitle="Chat with your
  data.")` at the top of the function body UNLESS `non_interactive` is
  True. The banner shows for both full and `--quick` interactive paths.
- Dispatch:
  - `non_interactive=True` + `quick=True` → `run_quick(interactive=False,
    config_dir=..., env=os.environ)`.
  - `non_interactive=True` + `quick=False` → existing `run_non_interactive`.
  - `non_interactive=False` + `quick=True` → `run_quick(interactive=True,
    config_dir=...)`.
  - `non_interactive=False` + `quick=False` → existing `run_wizard`.
- The "Wrote …" `ui_ok` / `ui_info` summary lines stay as-is.
- If `quick=True` and the user did NOT provide a scan root (empty),
  print a one-line `ui_info(...)` hint:
  `"No source root configured — add one later via `corpus-forge config set datasets[0].sources …`."`

**Tests:**

`tests/cli/test_setup_quick.py` — uses `CliRunner` + `tmp_path` +
`monkeypatch.setenv`:

1. `test_quick_non_interactive_sqlite_minimal`:
   - Env: `CF_BACKEND=sqlite`, `CF_EMBEDDER_MODEL_ID=qwen3:8b`.
   - Runs `setup --quick --non-interactive --config-dir <tmp_path>`.
   - Asserts exit 0.
   - Asserts the written config.toml round-trips through
     `Config.load(config_path=...)` cleanly.
   - Asserts the rendered config has `backend.kind == "sqlite"` and one
     embedder named appropriately with `model_id == "qwen3:8b"`.

2. `test_quick_sqlite_does_not_prompt_for_dsn`:
   - In interactive mode (use stream injection): provide `\n` for every
     prompt except the explicit backend answer of `sqlite`. The DSN
     prompt MUST NOT appear in `stream_out.getvalue()` (the postgres
     branch is skipped entirely when backend is sqlite).
   - Drive this by calling `run_quick(interactive=True, ...)` directly
     and inspecting the captured `stream_out` — no need to round-trip
     through Typer.

3. `test_quick_postgres_writes_dsn_through`:
   - Env: `CF_BACKEND=postgres`, `CF_BACKEND_DSN=postgresql://u:p@h/db`,
     `CF_EMBEDDER_MODEL_ID=qwen3:8b`.
   - Runs `setup --quick --non-interactive --config-dir <tmp_path>`.
   - Asserts the written config has `backend.kind == "postgres"` and
     `backend.dsn == "postgresql://u:p@h/db"`.
   - Asserts `Config.load(...)` round-trip succeeds.

4. `test_quick_probes_ollama_and_picks_first_embed_model`:
   - Mock `urllib.request.urlopen` (in the wizard's namespace) to return
     a fake response with body
     `{"models":[{"name":"qwen3:8b"},{"name":"llama3.2"},{"name":"bge-m3"}]}`.
     The probe should pick `qwen3:8b` (first model with `embed/bge/qwen/
     nomic` substring — qwen wins by position).
   - Call `_probe_ollama("http://localhost:11434")` directly and assert
     the result is `"qwen3:8b"`.
   - Add a second case: response with NO embedding-capable model →
     returns `"llama3.2"` (first listed) OR `None` (no fallback).
     Acceptable behavior — pin whichever the implementor chooses.
   - Add a third case: probe raises `URLError`/`TimeoutError`/`OSError`
     → returns `None` without raising.
   - Add a fourth integration-style test: in interactive mode with no
     `CF_EMBEDDER_MODEL_ID` env var, the probed model becomes the
     default offered to the user. (Drive via stream injection +
     mocked urlopen.)

`tests/cli/test_banner.py` — uses `CliRunner`:

1. `test_doctor_renders_banner_by_default`:
   - Runs `runner.invoke(app, ["doctor"])`.
   - Output contains `"corpus-forge"` AND at least one rounded-box
     character (`╭`, `╮`, `╯`, `╰`, `─`, `│`). Conftest sets
     `NO_COLOR=1`/`TERM=dumb` — under that env Rich's `box.ROUNDED`
     still renders box-drawing characters in `force_terminal` paths.
     If `NO_COLOR=1` strips the box characters, assert the banner's
     text content (`"corpus-forge"` + the subtitle `"Chat with your
     data."`) appears in the stderr output instead. The tester should
     pick whichever signal is stable under the conftest's
     ANSI-strip + `TERM=dumb` env.

2. `test_setup_non_interactive_does_not_render_banner`:
   - Runs `setup --non-interactive --config-dir <tmp_path>`.
   - Output does NOT contain the banner subtitle `"Chat with your
     data."`.

3. `test_setup_quick_non_interactive_does_not_render_banner`:
   - Runs `setup --quick --non-interactive --config-dir <tmp_path>`.
   - Output does NOT contain the banner subtitle. (Non-interactive is
     always banner-free, even under `--quick`.)

4. `test_setup_quick_interactive_renders_banner`:
   - Drive `setup --quick` in interactive mode by using stream
     injection (run `run_quick(interactive=True, ...)` directly OR pipe
     input through the runner with `input="\n" * 20`).
   - The captured output (whichever stream the banner is rendered to —
     by default `ui.console.console` which is `stderr=True`) contains
     `"corpus-forge"` + the subtitle.
   - Note: the `setup` command's banner render is in the CLI wrapper,
     not in `run_quick`. So this test goes through the CLI runner
     with stream-feed input.

### W3-02 — `doctor --json` + `to_json()` + banner on doctor

**`corpus_forge/doctor/checks.py` changes:**

- Add `DoctorReport.to_json(self) -> dict[str, object]`:
  ```python
  def to_json(self) -> dict[str, object]:
      return {
          "checks": [
              {"name": r.name, "status": r.status.value, "detail": r.detail}
              for r in self.results
          ],
          "summary": self._summary(),
          "version": __version__,
          "ts": _utc_now_iso(),
      }
  ```
  - `_summary()` is a new private helper on the class returning `"fail"`
    if any check is FAIL, else `"warn"` if any is WARN, else `"ok"`.
    (SKIP and OK both count as ok.)
  - `_utc_now_iso()` is a module-level helper returning
    `datetime.now(UTC).isoformat(timespec="milliseconds")` (or similar
    fixed-precision shape). Pure function, no test injection needed —
    tests assert on shape (`"T"` and `"Z"` or `+00:00` substring) not
    exact value.

**CLI changes (`corpus_forge/cli.py`):**
- `doctor` grows `--json` flag:
  ```python
  json_output: bool = typer.Option(
      False, "--json", help="Emit a single JSON document (suppresses banner).",
  )
  ```
- Banner: render `render_banner("corpus-forge", subtitle="Chat with your
  data.")` at the top UNLESS `json_output` is True.
- When `json_output` is True:
  - Skip the banner.
  - Skip `report.render_styled(...)`.
  - Print `json.dumps(report.to_json(), default=str)` once to **stdout**
    (use bare `print()` — this is a data line, not a status line).
  - Exit code: `0` if `summary == "ok"`, `2` if `summary == "warn"`,
    `1` if `summary == "fail"`. Use `raise typer.Exit(code=...)`.
- When `json_output` is False (existing path): unchanged except for the
  new banner at the top.

**Tests:**

`tests/cli/test_doctor_json.py` — uses `CliRunner` + `tmp_path`:

1. `test_doctor_json_prints_single_json_document`:
   - Patches `run_doctor` to return a known-shape `DoctorReport`
     (one OK check, one WARN check).
   - Runs `runner.invoke(app, ["doctor", "--json"])`.
   - Parses stdout as JSON (one document, single line OR pretty-printed
     — accept either; assert `json.loads(result.output.strip())` works).
   - Asserts keys: `"checks"`, `"summary"`, `"version"`, `"ts"`.

2. `test_doctor_json_summary_shape`:
   - For (all OK + SKIP) → `summary == "ok"`.
   - For (one WARN, no FAIL) → `summary == "warn"`.
   - For (one FAIL, plus other statuses) → `summary == "fail"`.
   - Drive each by patching `run_doctor` to inject the right
     `DoctorReport`.

3. `test_doctor_json_exit_codes`:
   - All-OK → exit 0.
   - One WARN → exit 2.
   - One FAIL → exit 1.

4. `test_doctor_json_does_not_render_banner`:
   - Run `doctor --json`. Assert the subtitle `"Chat with your data."`
     does NOT appear in stdout/stderr.

`tests/cli/test_doctor_banner_in_json_mode.py` — single regression
file (split out because it's a banner-specific assertion paired with
the json path, and keeping it separate from `test_banner.py` avoids
test-write contention between W3-01 and W3-02 workers). One test:

1. `test_doctor_human_render_has_banner_but_json_does_not`:
   - Two `runner.invoke` calls: `doctor` and `doctor --json`.
   - Human run output contains the banner subtitle.
   - JSON run output does NOT contain the banner subtitle.

## DAG
- Wave 0: W3-01 and W3-02 in parallel. Surface overlap on `cli.py` is on
  disjoint functions (setup vs doctor) so the two coders' diffs do not
  conflict in practice — the orchestrator commits each separately.

## Summary

**Files changed (production):**
- `corpus_forge/cli.py` — `setup` command grows `--quick` + banner;
  `doctor` command grows `--json` + banner + exit-code-by-summary.
- `corpus_forge/doctor/checks.py` — adds `DoctorReport._summary()`
  + `DoctorReport.to_json()` (UTC ISO8601 ts).
- `corpus_forge/setup/wizard.py` — adds `QUICK_QUESTIONS`,
  `_probe_ollama`, `_urlopen_compat`, `_render_quick_config_toml`,
  `_collect_quick_answers`, `_write_quick_config`, `run_quick`.
- `corpus_forge/setup/__init__.py` — re-exports `run_quick`.

**Files added (tests):**
- `tests/cli/test_setup_quick.py` (9 tests)
- `tests/cli/test_banner.py` (5 tests)
- `tests/cli/test_doctor_json.py` (9 tests)
- `tests/cli/test_doctor_banner_in_json_mode.py` (1 test)

**Gates:**
- New tests: 24/24 green.
- CLI + doctor + wizard regression: 101/101 green.
- `tests/cli/test_no_typer_echo.py` static regression: still green
  (no new `typer.echo`/`secho`/`prompt`/`confirm` outside `ui/`).
- `uv run ruff check <touched>` — clean.
- `uv run ruff format --check <touched>` — clean.
- Baseline pre-existing failures (`tests/unit/test_cli_rechunk.py`,
  `tests/unit/test_pdf_extractor_escalation.py`,
  `tests/unit/test_extractor_html.py`, etc.) unchanged — not introduced
  by Wave 3.

**Live smoke:**
- `python -m corpus_forge doctor --json` → one parseable JSON line,
  exit 0.
- `python -m corpus_forge doctor` → rounded-box banner + colored OK
  pills on stderr.
- `CF_BACKEND=sqlite CF_EMBEDDER_MODEL_ID=qwen3:8b python -m
  corpus_forge setup --quick --non-interactive --config-dir <tmp>`
  → valid `config.toml` (Config.load round-trip), no banner, info
  hint about empty scan_root.

## Notes for Wave 4+
- The `--quick` config shape is intentionally minimal — Wave 4's
  embedder-fingerprint detection (Wave 5) will need to confirm that
  the quick path's hardcoded embedder name is one Wave 5 can fingerprint
  cleanly. If the chosen `provider = "ollama"` doesn't match an existing
  `EmbedderConfig.provider` literal, the implementor should pick the
  right provider string and document.
- The `to_json()` shape established here is what Wave 6's
  `bug-report` will serialize. The schema (`checks`/`summary`/`version`/
  `ts`) is stable for downstream consumers.
- Banner rendering goes through `ui.render_banner` which already
  consults the Wave 9 `_agent_mode_active()` placeholder — when Wave 9
  ships, agent mode will suppress the banner globally, no further code
  changes needed.

## Archive — Wave 2

Wave 2 task board archived to git history at commit `9d80be9`.
See `git show 9d80be9 -- .planning/tdd/tasks.md` for the full record.
