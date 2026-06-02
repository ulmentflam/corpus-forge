# TDD Task Board — feat/corpus-agents-init

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

Feature: `corpus-forge agents init` — corpus-grounded AGENTS.md synthesizer.

## Project gates
- format: `ruff format --check corpus_forge tests`
- lint: `ruff check corpus_forge tests`
- typecheck: `./scripts/check-pyrefly.sh corpus_forge`
- test (focused): `pytest tests/unit/test_agents_detector.py tests/unit/test_agents_sampler.py tests/unit/test_agents_cross_corpus.py tests/unit/test_agents_synthesizer.py tests/unit/test_agents_writer.py tests/unit/test_cli_agents_init.py tests/integration/test_agents_init_e2e.py -x -q`
- test (suite shape): `pytest tests/unit -x -q --no-header` (compare to baseline)
- coverage-min: 80
- smoke: `corpus-forge agents init --help` exits 0

## Worktree
- Path: `/Users/evanowen/dev/cf-worktrees/feat-corpus-agents-init`
- Branch: `feat/corpus-agents-init` (off `origin/main` @ `afa3be3`)
- Active dev tree (for venv parity): `/Users/evanowen/dev/corpus-forge`

## Tasks

| id  | title                                                  | depends_on | surface | risk | status      | claimed_by | notes |
|-----|--------------------------------------------------------|------------|---------|------|-------------|------------|-------|
| T1  | `agents.detector` — ProjectContext + detect_project_context | —          | corpus_forge/agents/__init__.py, corpus_forge/agents/detector.py, tests/unit/test_agents_detector.py | low  | done        | principal#1 | RED+GREEN; 5 tests passing |
| T2  | `agents.sampler` — LocalPatterns + sample_local_patterns | T1         | corpus_forge/agents/sampler.py, tests/unit/test_agents_sampler.py | low  | done        | principal#1 | RED+GREEN; 8 tests passing |
| T3  | `agents.cross_corpus` — query battery against Retriever | —          | corpus_forge/agents/cross_corpus.py, tests/unit/test_agents_cross_corpus.py | low  | done        | principal#1 | RED+GREEN; 6 tests passing |
| T4  | `agents.synthesizer` — TWO-pass (private + shareable)  | T1,T2,T3   | corpus_forge/agents/synthesizer.py, tests/unit/test_agents_synthesizer.py | med  | done        | principal#3 | REDIRECT: two LLM passes, shareable citations empty + sanitization prompt |
| T5  | `agents.writer` — `.corpus-agents/*` + root-AGENTS safety | —          | corpus_forge/agents/writer.py, tests/unit/test_agents_writer.py | med  | done        | principal#3 | REDIRECT: write 4 files; auto-create root AGENTS.md only when absent; force never overwrites root |
| T6  | CLI: `agents init` subcommand (new flag set)           | T1,T2,T3,T4,T5 | corpus_forge/cli.py, corpus_forge/cli_agents.py, tests/unit/test_cli_agents_init.py | med  | done        | principal#3 | flags: --output-dir / --no-root-write / --gitignore-no-gitignore; exit 0/1/2/3 |
| T7  | Auto-ingest gate + corpus-coverage check               | T6         | corpus_forge/cli_agents.py, tests/unit/test_cli_agents_init.py | med  | done        | principal#3 | dataset source roots check; ingest_one + backfill_embedder; --no-ingest errors exit 2 |
| T8  | E2E integration test                                   | T6,T7      | tests/integration/test_agents_init_e2e.py | med  | done        | principal#3 | small fixture project, stubbed LLM, assert 4 files + root-AGENTS + .gitignore append |
| T9  | Skill: `.claude/skills/corpus-agents/SKILL.md`         | —          | .claude/skills/corpus-agents/SKILL.md | low  | done        | principal#3 | UPDATED: documents two outputs + safety semantics |

## DAG / Waves (executed)
- **Wave 0** (no deps, disjoint surface): T1, T3, T5, T9
- **Wave 1** (after T1): T2
- **Wave 2** (after T1, T2, T3): T4
- **Wave 3** (after T1-T5): T6
- **Wave 4** (after T6): T7
- **Wave 5** (after T6, T7): T8

## Final-spec redirect (principal #3)
- Synthesizer produces TWO artifacts via two LLM passes: private corpus-grounded `.corpus-agents/AGENTS.md` with `chunk_id` citations + shareable sanitized `.corpus-agents/shareable.md` (citation-free, no cross-corpus references).
- Writer drops 4 files in `.corpus-agents/` (`AGENTS.md`, `shareable.md`, `citations.json`, `meta.json`).
- IF `<root>/AGENTS.md` missing → also write a copy of `shareable.md` to the project root so fresh projects ship safe conventions.
- IF `<root>/AGENTS.md` exists → leave untouched. `--force` applies to `.corpus-agents/*` ONLY — never the root file.
- `.gitignore` gets `.corpus-agents/` appended (idempotent). Root `AGENTS.md` is NOT added — it's the user's commit surface.

## Acceptance details

### T1 — detector
- `ProjectContext` dataclass exposes: `languages: dict[str, int]`, `package_managers: list[str]`, `test_framework: str | None`, `build_tool: str | None`, `existing_agents_md: Path | None`, `existing_claude_md: Path | None`, `existing_readme: Path | None`, `license: str | None`, `license_header_sample: str | None`.
- `detect_project_context(root: Path) -> ProjectContext`: non-recursive top-level scan + 1-level descent into `src/` and `tests/`.
- Detects: Python (`pyproject.toml`, `requirements*.txt`), Node (`package.json`), Rust (`Cargo.toml`), Go (`go.mod`); test framework via presence of `pytest.ini` / `pyproject` `[tool.pytest]` / `tests/` dir; license from `LICENSE` first ~20 lines.
- Fixture trees in tests cover Python-only, Rust-only, mixed Python/TS.

### T2 — sampler
- `LocalPatterns` dataclass: `import_style: str`, `docstring_style: str`, `error_handling_examples: list[str]`, `type_hint_density: float`, `test_naming_pattern: str | None`, `notable_comments: list[str]`.
- `sample_local_patterns(context, root) -> LocalPatterns`: opens up to N representative files (top-level modules + `tests/`); pure file scan — no shelling to git in unit tests.
- Pattern extraction uses regex / `tokenize` — no third-party parsers.

### T3 — cross_corpus
- `CrossCorpusPatterns`: `categories: dict[str, list[Hit]]` mapping category name → top-3 hits.
- `query_corpus_patterns(context, retriever) -> CrossCorpusPatterns` — query battery keyed by language. Python battery includes at minimum: `pytest fixture`, `logging.getLogger`, `dataclass`, `pytest.raises`, `Path.read_text`. Rust battery: a couple of representative idioms. The battery is exported as a module-level mutable dict so it's extensible.
- Tests use a fake retriever returning canned `Hit` objects; assert each language-scoped query fires and top-3 hits preserve `chunk_id` + source_uri.

### T4 — synthesizer (REDIRECTED)
- `synthesize(context, local, cross_corpus, *, llm) -> tuple[SynthesisResult, SynthesisResult]` returns `(private, shareable)`.
- `SynthesisResult`: `markdown: str`, `sections: list[str]`, `citations: list[ChunkRef]` (`ChunkRef = {chunk_id, source_uri, score}`).
- TWO LLM calls (one per output) for clean separation.
- Private prompt: corpus-grounded with chunk_id citations; existing required section headings.
- Shareable prompt: explicit "Do NOT cite chunk_ids. Do NOT reference any external repository. Do NOT phrase claims as 'based on your past work'." — references only language/tool defaults + this-project local sampling + factual project metadata.
- Shareable's `citations` list MUST be empty (gated).
- `LLMSynthesisError` raised on: empty response, missing required section headings, or upstream HTTP error.

### T5 — writer (REDIRECTED)
- `write_corpus_agents_dir(dir_path, *, private_md, shareable_md, citations, meta, force) -> WriteResult` writes all four files in `.corpus-agents/`. Returns paths written.
- `maybe_write_root_agents_md(project_root, shareable_md, *, enabled=True) -> bool` — writes `<root>/AGENTS.md` from shareable IFF absent AND enabled; returns True iff a fresh file was created.
- `ensure_gitignore_entry(project_root, *, enabled=True) -> bool` — append `.corpus-agents/` to `<root>/.gitignore` if missing; idempotent.
- `--force` overwrites `.corpus-agents/*` ONLY; never overwrites the project root `AGENTS.md`. Test this explicitly.
- Old `write_agents_md` / `write_claude_pointer` functions are deleted outright.

### T6 — CLI (REDIRECTED)
- New flag set: `--project-root`, `--output-dir`, `--no-root-write`, `--gitignore/--no-gitignore`, `--no-ingest`, `--force`, `--diff`, `--json`. No more `--no-claude-pointer`, no `--output`.
- `init` flow: detect → sample → cross_corpus → synthesize (two-pass) → write `.corpus-agents/*` → maybe write root AGENTS → ensure gitignore.
- Exit codes: 0 ok; 1 user-input error; 2 corpus not ready / no LLM endpoint; 3 LLM synthesis failure.
- `--json` + agent-mode emit structured `result` event with `paths_written` (list[str]), `sections` (list[str]), `citations` (list[dict]).
- Help text matches `CliRunner` assertions for the new flag names.

### T7 — auto-ingest gate
- Helper `_project_covered_by_active_dataset(config, project_root) -> bool` scans `config.datasets[*].sources[*]` for any source root that contains `project_root` (or vice versa).
- If uncovered AND `--no-ingest` absent: print "Project not in corpus — running ingest+embed first", invoke `ingest_one` against project_root, then `backfill_embedder` for each active embedder.
- If uncovered AND `--no-ingest`: exit 2 with message naming the missing source root.
- Tests mock ingest + embed callables to assert correct args.

### T8 — E2E (REDIRECTED)
- Fixture project: 3 `.py` files + `tests/test_foo.py` + `pyproject.toml` + `LICENSE`.
- Stub LLM callable returns 2 canned multi-section markdowns (private with citations, shareable sanitized) with all required headings.
- Stub cross-corpus retriever with 2-3 canned hits.
- Run via `CliRunner`; assert: `.corpus-agents/{AGENTS.md, shareable.md, citations.json, meta.json}` all exist, root `AGENTS.md` created when absent, JSON `result.paths_written` lists all of them, `.gitignore` contains `.corpus-agents/` after run.
- Second scenario: root `AGENTS.md` pre-exists → assert it's left untouched even with `--force`.

### T9 — skill
- File: `.claude/skills/corpus-agents/SKILL.md` with frontmatter (`name`, `description`, `allowed-tools`).
- Sections: "What this skill does", "When to invoke", "When NOT to invoke", "Workflow" (invoke CLI, read diff, prompt edits, commit).
- No `mcp__` allowed-tools — this skill drives the CLI, not the MCP server.

## Hard constraints
- Don't modify `~/.config/corpus-forge/config.toml`.
- Don't auto-commit — orchestrator commits on workers' behalf.
- Don't bundle an LLM. Fail with exit 2 if `config.code_enricher.backend == "none"`.
- Every output claim must be traceable to local sampling or cross-corpus retrieval.
- **`--force` NEVER overwrites `<project-root>/AGENTS.md`** — that file is the user's commitment surface.

## Summary (principal #3, 2026-06-02)
- Files changed:
  - `corpus_forge/agents/synthesizer.py` (rewrite — two-pass with PRIVATE_PROMPT_TEMPLATE + SHAREABLE_PROMPT_TEMPLATE; shareable citations gated empty)
  - `corpus_forge/agents/writer.py` (rewrite — `write_corpus_agents_dir` + `maybe_write_root_agents_md` + `ensure_gitignore_entry`; old `write_agents_md` / `write_claude_pointer` deleted)
  - `corpus_forge/agents/detector.py` (cleanup of inherited ARG001/PLW2901)
  - `corpus_forge/agents/sampler.py` (cleanup of inherited ARG001/PLR2004/E501)
  - `corpus_forge/cli_agents.py` (rewrite — new flag set; two-pass synthesis; safe root-AGENTS handling; fixed hallucinated `build_embedder` import via `register_from_config`)
  - `corpus_forge/cli.py` (unchanged from principal #1 — `agents_app` registered)
  - `tests/unit/test_agents_synthesizer.py` (rewrite — 16 tests)
  - `tests/unit/test_agents_writer.py` (rewrite — 13 tests)
  - `tests/unit/test_cli_agents_init.py` (rewrite — 15 tests)
  - `tests/integration/test_agents_init_e2e.py` (NEW — 2 tests)
  - `.claude/skills/corpus-agents/SKILL.md` (updated — documents two outputs + safety semantics)
  - `.planning/tdd/{tasks,code-status,test-status,qa-status}.md` (updated)
- Gates run:
  - `ruff format --check corpus_forge tests` — 806 files clean
  - `ruff check corpus_forge tests` — 0 errors
  - `./scripts/check-pyrefly.sh corpus_forge` — 0 errors (76 suppressed)
  - `pytest tests/unit/test_agents_* tests/unit/test_cli_agents_init.py tests/integration/test_agents_init_e2e.py` — 65/65 PASS
  - Suite shape: 5840 pass / 130 fail (pre-existing missing-extras, matches main baseline 5777 pass / 130 fail)
  - Smoke: `corpus-forge agents init --help` exits 0 with all 9 documented flags visible
- Safety verdict: APPROVED. The `--force NEVER overwrites root AGENTS.md` invariant is exercised three independent ways (writer-unit, cli-unit, e2e).
