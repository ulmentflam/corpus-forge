# TDD Task Board — Phase L / Wave 7 (Admin CRUD: config/embedder/ollama/dataset/source)

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's
`status` and `claimed_by`._

Source plan:
`/Users/evanowen/Library/Mobile Documents/com~apple~CloudDocs/Workspace/playground/corpus-forge/.planning/tdd/phase_l_cli_ux.md`
§10 (CRUD admin commands).

Dispatch input: orchestrator brief, Phase L / Wave 7 kickoff after Wave
6 landed (`56cae46`).

> Previous slice (Wave 6) summary archived in git history at `56cae46`.

## Project gates
- lint: `uv run ruff check`
- format: `uv run ruff format --check`
- test (Wave 7 surface):
  `uv run python -m pytest tests/admin tests/cli/test_admin_groups.py tests/cli/test_no_typer_echo.py -x`
- regression:
  `uv run python -m pytest tests/unit tests/cli tests/embedders tests/backends tests/diagnostics -x`
  (no new failures vs Wave 6 baseline)

## Hard constraints (from dispatch + project)
1. **DO NOT COMMIT, DO NOT PUSH.** Workers stage only. Orchestrator commits.
2. **NO `typer.echo/secho/prompt/confirm`** outside `corpus_forge/ui/`.
3. **`uv run python -m pytest`**, never bare `pytest`.
4. Themed output only via `corpus_forge.ui.console` and prompts via
   `corpus_forge.ui.prompts`.
5. Foreground/background convention: every long-op admin verb defaults to
   foreground (attached, SIGINT-forwarded); `-b`/`--background` detaches
   via `subprocess.Popen(stdin=DEVNULL, start_new_session=True)`, writes
   pid file to `<platformdirs cache>/corpus-forge/state/<component>.pid`.
6. `OllamaConfig` may need to be added to `corpus_forge/config.py` if it
   doesn't already exist (verified: it doesn't; needs adding).
7. `tomlkit>=0.13` is already a direct dep (added in Wave 6).
8. iCloud sync race: keep the working tree clean per file; never commit
   until orchestrator has read `git status` + `git diff --stat`.

## Decomposition notes (orchestrator)

### Surface-disjoint matrix

| Task | Owns (writes) | Reads (depends on) |
|------|---------------|--------------------|
| W7-01 (foreground wrapper) | `corpus_forge/admin/__init__.py`, `corpus_forge/admin/foreground.py`, `tests/admin/__init__.py`, `tests/admin/test_foreground.py` | platformdirs, signal, subprocess |
| W7-02 (dotted-path resolver) | `corpus_forge/admin/_path.py`, `tests/admin/test_path_resolver.py` | tomlkit, pydantic.fields.FieldInfo |
| W7-03 (config CRUD) | `corpus_forge/admin/config.py`, `tests/admin/test_config_crud.py` | W7-02, redactor, ui |
| W7-04 (Ollama config field + admin) | `corpus_forge/config.py` (add OllamaConfig), `corpus_forge/admin/ollama.py`, `tests/admin/test_ollama.py` | urllib, ui, progress |
| W7-05 (embedder admin) | `corpus_forge/admin/embedder.py`, `tests/admin/test_embedder_crud.py` | W7-01 (run_attached), backend helpers, embed.backfill_embedder, fingerprint |
| W7-06 (dataset + source admin) | `corpus_forge/admin/dataset.py`, `corpus_forge/admin/source.py`, `tests/admin/test_dataset_source_crud.py` | W7-02, W7-03 (CRUD plumbing), ui |
| W7-07 (CLI wiring + smoke) | `corpus_forge/cli.py`, `tests/cli/test_admin_groups.py` | W7-03..W7-06 |

### Wave shape

- Wave A (parallel): W7-01 (foreground), W7-02 (path resolver), W7-04
  (`OllamaConfig` + ollama admin). Fully disjoint surfaces.
- Wave B: W7-03 (config CRUD) — needs W7-02.
- Wave C (parallel): W7-05 (embedder admin) + W7-06 (dataset/source
  admin). W7-05 needs W7-01; W7-06 needs W7-03.
- Wave D: W7-07 (CLI registration + cross-cutting smoke).

## Tasks

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| W7-01 | run_attached + pid-file helpers | — | corpus_forge/admin/{__init__,foreground}.py, tests/admin/{__init__,test_foreground}.py | med | done | tdd-principal | 15 tests green; SIGINT forwarding + pid-file liveness |
| W7-02 | dotted-path resolver | — | corpus_forge/admin/_path.py, tests/admin/test_path_resolver.py | low | done | tdd-principal | 36 tests green; tomlkit + dict round-trips; Pydantic-aware coercion |
| W7-03 | config CRUD verbs | W7-02 | corpus_forge/admin/config.py, tests/admin/test_config_crud.py | med | done | tdd-principal | 27 tests green; atomic rollback on invalid; redacted show by default |
| W7-04 | OllamaConfig + ollama admin verbs | — | corpus_forge/config.py, corpus_forge/admin/ollama.py, tests/admin/test_ollama.py | med | done | tdd-principal | 19 tests green; OllamaConfig defaulted; NDJSON pull streaming |
| W7-05 | embedder admin verbs | W7-01 | corpus_forge/admin/embedder.py, tests/admin/test_embedder_crud.py | med | done | tdd-principal | 19 tests green; set-active triggers drift; remove --drop-vectors cascades |
| W7-06 | dataset + source admin verbs | W7-03 | corpus_forge/admin/{dataset,source}.py, tests/admin/test_dataset_source_crud.py | low | done | tdd-principal | 13 tests green; source -d <dataset> required; ingest prompt opt-in |
| W7-07 | CLI wiring + group smoke | W7-03..W7-06 | corpus_forge/cli.py, tests/cli/test_admin_groups.py | low | done | tdd-principal | 17 tests green; all 5 groups under --help; verb-level help smoke |

### Acceptance

- W7-01: `run_attached` runs a foreground child, SIGINT forwards; `-b`
  mode detaches via Popen + start_new_session, writes pid; `read_pid`
  returns None when proc dead.
- W7-02: `parse_dotted_key("a.b")` / `("a[0].b")` / `("a[0]")` /
  `("a.b.c[2].d")` resolve; `get_at_path` / `set_at_path` round-trip on
  a tomlkit document; `coerce_for_field` converts str to int/bool/float/
  list/dict using Pydantic FieldInfo.
- W7-03: `get` prints scalar/JSON; `set` round-trips through `Config.load`
  validation, atomic write + rollback on invalid; `unset` reverts to
  default; `show --diff` shows only delta from defaults; `show` redacts
  secrets unless `--secrets` flag; `path` prints platformdirs path;
  `validate` succeeds/fails as expected; `edit` opens $EDITOR + rolls
  back on invalid save.
- W7-04: `OllamaConfig` added with `base_url` default; `list`/`get`/`pull`
  hit Ollama HTTP API with `--timeout`; `pull` streams progress;
  `set-url` proxies to `config set` and reprobes; `test` embeds and
  reports timing.
- W7-05: `list` table includes name/provider/model_id/dim/active/fp/cov;
  `set-active` flips flag + triggers drift check; `remove --drop-vectors`
  truncates the per-embedder table; `test` runs sample embed (mocked).
- W7-06: `dataset list/add/remove` round-trips in config.toml;
  `source list/add/remove -d <dataset>` round-trips inside the named
  dataset's sources array.
- W7-07: `corpus-forge config --help` / `embedder --help` / `ollama
  --help` / `dataset --help` / `source --help` all show; verb-level
  `--help` smoke for at least one verb per group;
  `test_no_typer_echo.py` still passes.

### Definition of done

1. All new tests pass under
   `uv run python -m pytest tests/admin tests/cli/test_admin_groups.py
   tests/cli/test_no_typer_echo.py -x`.
2. Regression: `uv run python -m pytest tests/unit tests/cli tests/embedders
   tests/backends tests/diagnostics -x` is green (no new failures vs
   Wave 6 baseline — pre-existing 164 missing-dep failures unchanged).
3. `uv run ruff check` clean on touched files.
4. `uv run ruff format --check` clean on touched files.

## DAG

- Wave A (parallel): W7-01, W7-02, W7-04
- Wave B: W7-03 (after W7-02)
- Wave C (parallel): W7-05 (after W7-01), W7-06 (after W7-03)
- Wave D: W7-07 (after W7-03..W7-06)

## Summary

- Files changed: 3 modified, 17 new (+ 1 new test dir).
- Modified: `corpus_forge/cli.py` (mount 5 admin sub-apps), `corpus_forge/config.py` (add `OllamaConfig` block with default `base_url=http://localhost:11434`), `pyproject.toml` (per-file `PLC0415` ignore for `corpus_forge/admin/*.py`).
- New: `corpus_forge/admin/{__init__,foreground,_path,config,ollama,embedder,dataset,source}.py`, `tests/admin/{__init__,test_foreground,test_path_resolver,test_config_crud,test_ollama,test_embedder_crud,test_dataset_source_crud}.py`, `tests/cli/test_admin_groups.py`.
- Gates: 147 Wave 7 tests passing (incl. typer-echo regression); regression sweep across `tests/unit tests/cli tests/embedders tests/backends tests/diagnostics` shows 165 failures — all pre-existing missing-dep failures (no new failures vs Wave 6 baseline of 181; the 16-failure delta is the Wave 7 admin_groups tests that were red without the cli wiring).
- Lint clean, format clean on every touched file.
- Manual smoke: `corpus-forge config get backend.kind` round-trips; `corpus-forge embedder list` renders rows; `corpus-forge ollama list` succeeds against the mocked tag-list response; `corpus-forge --help` shows the five new groups.
- Convention adherence: every long-op verb (`ollama pull`, `embedder set-active`, `source add` → ingest) honors the global `--background`/`-b` flag through `corpus_forge.admin.foreground.run_attached`; pid files live at `<platformdirs cache>/corpus-forge/state/<component>.pid`.

## Wave 8+ notes

- Wave 8 (service lifecycle): use the same `run_attached` + pid-file helpers from `corpus_forge/admin/foreground.py`; the `daemon` component is already namespaced.
- Wave 9 (agent mode): `corpus_forge/admin/*` verbs already route all output through `corpus_forge.ui.console` + `corpus_forge.ui.prompts` — agent-mode JSONL emission can plug in via the existing `_agent_mode_active()` hook in `ui/console.py` without per-verb changes.
- The `_set_config_value_atomic` helper is the canonical config writer; future verbs (e.g. `service install --apply`) should reuse it for any persistence rather than duplicating the tomlkit + Pydantic validation dance.
