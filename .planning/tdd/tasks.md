# TDD Task Board — Phase O Wave 1 (EDA foundations)

_Owner: tdd-principal. Workers: tdd-tester / tdd-coder / tdd-qa._
_Date: 2026-05-19._
_Branch: `main` (Phase N closed at `6612a8e` — "0.1.0b6 — Phase N: retrieval quality")._
_Phase doc: `.planning/tdd/phase_o_eda_cleaning.md` § Wave O1._
_Predecessor: `.planning/tdd/phase_n_retrieval_quality.md` (Phase N closed, Phase O begins fresh)._

Brief: Lay the structural foundations for Phase O — a `[analyze]` optional dep extra, an `AnalyzeConfig` Pydantic block defaulted to off, the `0012_analyze_signals` migration provisioning `chunk_quality_signals` + `near_duplicate_clusters`, and a pure-stdlib `corpus_forge.analyze.stats` module with `compute_token_stats` + `compute_length_distribution`. No sklearn / no LSH / no topics in this wave — those land in O2/O3.

**Hard scope contract**: zero touches to `corpus_forge/curation/`, `corpus_forge/mcp/`, `corpus_forge/cli.py`, or `corpus_forge/retrieval/`. Wave O1 stays in foundation territory (`pyproject.toml`, `config.py`, `config.example.toml`, alembic, new `corpus_forge/analyze/`, new tests). Any other production touch is a hard fail and triggers immediate rework.

**Critical rebase from the plan**: the source plan reserved alembic revision `0013` for Phase O. Phase N shipped no migration, so the actual head is `0011_image_embeddings`. **Phase O's migration is `0012_analyze_signals.py`.** Verified against `ls corpus_forge/alembic/versions/` and `0011_image_embeddings.py:33`.

## Project gates
- format:    `uv run ruff format --check corpus_forge tests`
- lint:      `uv run ruff check corpus_forge tests`
- typecheck: `uv run pyrefly check corpus_forge` (baseline 10 errors as of Phase N close; no new errors)
- test:      `uv run pytest tests/unit tests/integration tests/admin tests/mcp tests/smoke -x`
- startup:   `time corpus-forge --help` ≤ current baseline + 100 ms
- doctor:    `uv run corpus-forge doctor` exits 0 with no new warnings

## Tasks
| id    | title                                                                | depends_on    | surface                                                                                                                                                          | risk | status   | claimed_by | notes |
|-------|----------------------------------------------------------------------|---------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------|------|----------|------------|-------|
| O1-T1 | RED: AnalyzeConfig pydantic block + TOML round-trip                 | —             | `tests/unit/test_analyze_config.py`                                                                                                                              | low  | done     | tdd-tester | 46 tests RED — all fail with `ImportError: cannot import name 'AnalyzeConfig' from 'corpus_forge.config'`. Covers defaults, validation (dedup_threshold bounds, topic_min_cluster_size ge=2, language_detector Literal, judge_endpoint AnyHttpUrl, judge_timeout_s gt=0, judge_api_key_env allow-empty POSIX), Config wiring, and TOML round-trip. |
| O1-T2 | RED: stats.py token + length distribution                            | —             | `tests/unit/test_analyze_stats.py`                                                                                                                               | low  | done     | tdd-tester | Pure stdlib spec. Empty / singleton / multi-chunk paths plus a `sum`-invariant property. 27 tests RED (ModuleNotFoundError: No module named 'corpus_forge.analyze'). |
| O1-T3 | RED: pyproject `analyze` extra contents                              | —             | `tests/unit/test_pyproject_extras_analyze.py`                                                                                                                    | low  | done     | tdd-tester | `tomllib`-parse pyproject; assert the seven deps are listed: scikit-learn, hdbscan, umap-learn, bertopic, datasketch, fasttext-langdetect, langdetect. 11/12 tests RED (KeyError: 'analyze'); 1 passes (negative core-dep invariant, correct). |
| O1-T4 | RED: 0012_analyze_signals migration (Postgres + SQLite)              | —             | `tests/integration/test_migrate_0012_analyze.py`                                                                                                                 | med  | done     | tdd-tester | 45 tests RED. TestSQLiteAnalyzeSignals (21 tests, no Docker) + TestPostgresAnalyzeSignals (21 tests, requires_docker) + TestMigrationModuleAttributes (3 tests). All fail: alembic CommandError (can't locate 0012_analyze_signals) or ModuleNotFoundError. |
| O1-G1 | GREEN: `[analyze]` extra in pyproject.toml                           | O1-T3         | `pyproject.toml`                                                                                                                                                  | low  | done     | tdd-coder  | Insert the new extra after `fast-tier` (~line 173). License-posture comment in extra header. fasttext-langdetect canonical name confirmed (no drift). README.md updated per pre-existing docs consistency gate. 12/12 tests green. |
| O1-G2 | GREEN: `AnalyzeConfig` + `Config.analyze` wiring                     | O1-T1         | `corpus_forge/config.py`, `config.example.toml`                                                                                                                  | low  | done     | tdd-coder  | 46/46 tests green. AnalyzeConfig slotted between ScanConfig and Config. Mirror of ClassifierConfig local-or-remote pattern. config.example.toml ships local + remote judge_endpoint examples. |
| O1-G3 | GREEN: 0012_analyze_signals migration                                | O1-T4         | `corpus_forge/alembic/versions/0012_analyze_signals.py`                                                                                                          | med  | in_progress | tdd-coder  | Migration file complete; all 24 SQLite + 3 module tests pass. Tester bug (content→text) was self-fixed in test file. QA rework: coder must update 3 adjacent rot-detectors — (1) test_sqlite_backend.py EXPECTED_TABLES add chunk_quality_signals + near_duplicate_clusters; (2) test_apply_migrations_uses_alembic.py bump hardcoded version_num 0011_image_embeddings→0012_analyze_signals (both pg + sqlite paths); (3) docs/schema.md migration log add 0012 row. |
| O1-G4 | GREEN: `corpus_forge/analyze/` package + stats module                | O1-T2         | `corpus_forge/analyze/__init__.py`, `corpus_forge/analyze/stats.py`                                                                                              | low  | done     | tdd-coder  | Pure stdlib. NO `numpy` import. `__init__.py` is the canonical "what is corpus-forge analyze" entry — names the four MCP tools coming in O4. |
| O1-Q1 | QA: gates + scope verification                                       | O1-G1..O1-G4  | —                                                                                                                                                                | low  | in_progress | tdd-qa     | REWORK: 5 suite regressions + ruff format/lint failures. Core deliverables correct. Blocking: (1) ruff format 4 test files; (2) ruff lint 8 errors in 4 test files; (3) test_sqlite_backend EXPECTED_TABLES missing 2 new tables; (4) test_apply_migrations_uses_alembic version_num hardcoded to 0011; (5) docs/schema.md missing 0012 entry. Route: tdd-tester fixes format/lint on own test files; tdd-coder fixes rot-detectors + schema.md. |

## Acceptance details

### O1-T1 (RED — AnalyzeConfig pydantic block)
- New file `tests/unit/test_analyze_config.py` (ungated; runs in default unit suite).
- Asserts on `Config(...).analyze`:
  - Fields: `enabled: bool = False`, `dedup_threshold: float = 0.85`, `topic_min_cluster_size: int = 10`, `language_detector: Literal["fasttext", "langdetect"] = "langdetect"`, `judge_endpoint: AnyHttpUrl = AnyHttpUrl("http://localhost:11434")`, `judge_model: str = "qwen2.5:7b-instruct"`, `judge_api_key_env: str = ""`, `judge_timeout_s: float = 60.0`.
  - `dedup_threshold` validated to `[0.0, 1.0]` (`ge=0.0`, `le=1.0`).
  - `topic_min_cluster_size` validated to `>= 2`.
  - `judge_timeout_s` validated to `> 0`.
  - `judge_api_key_env` validated as POSIX identifier when non-empty (mirrors `ClassifierConfig._check_llm_api_key_env_name`).
  - `model_config.extra == "forbid"` — unknown keys reject.
- TOML round-trip: dump → parse → equality.
- **Default-factory path**: a minimal valid TOML that OMITS the `[analyze]` block validates and yields the default `AnalyzeConfig()`. This is the backwards-compat invariant.
- RED: the test file is added BEFORE `AnalyzeConfig` exists in `corpus_forge/config.py`, so `from corpus_forge.config import AnalyzeConfig` fails with ImportError → test fails.

### O1-T2 (RED — stats.py)
- New file `tests/unit/test_analyze_stats.py`.
- Asserts on `corpus_forge.analyze.stats.compute_token_stats(chunks)`:
  - Returns a dict with keys `p50`, `p95`, `mean`, `min`, `max`, `token_total`, `n` (`int`s for p50/p95/min/max/token_total/n; `float` for mean).
  - Empty input → all-zero dict (no exception, no ZeroDivisionError).
  - Single chunk → p50 == p95 == min == max == token_count; mean == float(token_count); n == 1.
  - Property: `result["token_total"] == sum(c["token_count"] for c in chunks)` over a Hypothesis-generated chunk list.
- Asserts on `compute_length_distribution(chunks, bins=10)`:
  - Returns dict `{"edges": list[int|float], "counts": list[int]}` with `len(edges) == bins + 1`, `len(counts) == bins`.
  - `sum(counts) == len(chunks)`.
  - Monotonically increasing edges.
  - Bins parameter respected: `bins=5` returns 6 edges + 5 counts.
- **Lazy-import smoke**: `import corpus_forge.analyze.stats; assert "numpy" not in sys.modules` (the wave gate enforces stats.py stays pure-stdlib).
- RED: the module doesn't exist yet; ImportError on `from corpus_forge.analyze.stats import compute_token_stats`.

### O1-T3 (RED — pyproject `analyze` extra)
- New file `tests/unit/test_pyproject_extras_analyze.py`.
- `tomllib.load` the repo root `pyproject.toml`.
- Asserts:
  - `project.optional-dependencies.analyze` is a list.
  - Required entries (each verified by `dep_name in dep_string`): `scikit-learn`, `hdbscan`, `umap-learn`, `bertopic`, `datasketch`, `fasttext-langdetect`, `langdetect`.
  - No entry pulls a known-AGPL dep (negative assertion — defends the existing AGPL boundary at `[multi-format]`).
- RED: `analyze` key absent from `pyproject.toml` → KeyError → test fails.

### O1-T4 (RED — 0012_analyze_signals migration)
- New file `tests/integration/test_migrate_0012_analyze.py`. `pytestmark = [pytest.mark.integration]`.
- Two test classes:
  - **`TestPostgresAnalyzeSignals`** (`@pytest.mark.requires_docker`):
    - After `PostgresBackend(dsn=pg_dsn, schema="corpus").migrate()`:
      - `chunk_quality_signals` table exists with columns: `id BIGSERIAL PK`, `chunk_id BIGINT NOT NULL` (FK to `corpus.chunks(id) ON DELETE CASCADE`), `signal_name TEXT NOT NULL`, `signal_value REAL`, `source TEXT NOT NULL`, `computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
      - Index `chunk_quality_signals_chunk_signal_idx` on `(chunk_id, signal_name)`.
      - `near_duplicate_clusters` table exists with columns: `id BIGSERIAL PK`, `cluster_id TEXT NOT NULL`, `chunk_id BIGINT NOT NULL` (FK to `corpus.chunks(id) ON DELETE CASCADE`), `similarity REAL`, `method TEXT NOT NULL`, `computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
      - Index `near_duplicate_clusters_cluster_idx` on `(cluster_id)`.
    - FK behavior: deleting a chunk cascade-deletes its rows in both new tables.
  - **`TestSQLiteAnalyzeSignals`** (no docker required):
    - Same tables / columns / indexes (INTEGER PK, FK syntax adapted, TEXT timestamps with `DEFAULT (datetime('now'))` per `0008` precedent).
- **Down-revision assertion** (both classes): import the migration module and assert `down_revision == "0011_image_embeddings"`. Catches accidental rebase drift in CI.
- **Forward-only assertion**: the migration's `downgrade()` body is a single `pass` (mirrors `0010` and `0011` project convention; the plan's "Clean downgrade" wording is overridden — see phase doc).
- RED: migration file doesn't exist; `from corpus_forge.alembic.versions.0012_analyze_signals import down_revision` fails.

### O1-G1 (pyproject extra)
- Insert after the `fast-tier` extra (~line 173 of `pyproject.toml`):
  ```toml
  # Phase O — EDA + corpus-cleaning ML stack.  All deps lazy-imported
  # inside corpus_forge/analyze/ function bodies so corpus-forge --help
  # cold-start budget is unaffected.
  #
  # License posture: scikit-learn (BSD-3-Clause), hdbscan (BSD-3-Clause),
  # umap-learn (BSD-3-Clause), bertopic (MIT), datasketch (MIT),
  # fasttext-langdetect (MIT), langdetect (Apache-2.0).  All permissive;
  # does NOT widen the AGPL surface introduced by [multi-format].
  analyze = [
    "scikit-learn>=1.4",
    "hdbscan>=0.8",
    "umap-learn>=0.5",
    "bertopic>=0.16",
    "datasketch>=1.6",
    "fasttext-langdetect>=1.0",
    "langdetect>=1.0",
  ]
  ```
- **Verify the PyPI name** for `fasttext-langdetect` before committing: `pip index versions fasttext-langdetect` (the canonical distribution name has fluctuated; if it's `ft-langdetect` instead, lock the right one and update the test's expected list in `tests/unit/test_pyproject_extras_analyze.py` accordingly).

### O1-G2 (AnalyzeConfig + Config wiring + config.example.toml)
- `corpus_forge/config.py`:
  - Slot `AnalyzeConfig(BaseModel)` between `ScanConfig` (ends ~line 625) and `class Config` (starts at 627).
  - Docstring documents the local-or-remote `judge_endpoint` / `judge_model` pattern, references `ClassifierConfig.llm_url` (line 492) as the proven template.
  - `model_config = ConfigDict(extra="forbid")`.
  - `_check_judge_api_key_env_name` validator mirrors `ClassifierConfig._check_llm_api_key_env_name` (line 501) — allow-empty POSIX-identifier validation.
- `corpus_forge/config.py:627–661` — add `analyze: AnalyzeConfig = Field(default_factory=AnalyzeConfig)` to `Config` with a leading `# Phase O — ...` comment that explicitly notes "defaults to `enabled=False` so existing configs without an `[analyze]` block continue to validate."
- `config.example.toml` — append the `[analyze]` block with BOTH local (default) and remote examples per `project_model_local_or_remote.md`. Use the snippet from the phase doc Wave O1 GREEN section verbatim.

### O1-G3 (0012_analyze_signals migration)
- `corpus_forge/alembic/versions/0012_analyze_signals.py` — follow `0008_feedback_sessions.py:1–86` shape:
  - Header docstring documenting the two tables and the Phase O context.
  - `revision = "0012_analyze_signals"`, `down_revision = "0011_image_embeddings"`.
  - `upgrade()` dispatches on `dialect.name` to `_upgrade_postgres()` / `_upgrade_sqlite()`.
  - `downgrade(): pass` (forward-only per project convention).
  - Postgres path: explicit `op.execute("CREATE TABLE corpus.chunk_quality_signals ...")` with PK / FK / NOT NULL / DEFAULT NOW(); separate `op.execute("CREATE INDEX ...")` for the two indexes; then the same for `near_duplicate_clusters`.
  - SQLite path: same shape with INTEGER PK, `DEFAULT (datetime('now'))` for timestamps.
- Run `uv run alembic upgrade head` against a temp SQLite DB to smoke-verify before staging.

### O1-G4 (`corpus_forge/analyze/` package + stats.py)
- `corpus_forge/analyze/__init__.py`:
  - Module docstring is the canonical "what is corpus-forge analyze" entry. Documents:
    - The four MCP tools Phase O surfaces in Wave O4 (`analyze_corpus`, `find_duplicates`, `cluster_topics`, `score_quality`).
    - The lazy-import contract: heavy deps (sklearn, hdbscan, umap, bertopic, datasketch, fasttext, langdetect) load on first use inside function bodies. Module top is import-cheap.
    - Cross-link to `.planning/tdd/phase_o_eda_cleaning.md`.
  - Re-exports `compute_token_stats`, `compute_length_distribution`.
- `corpus_forge/analyze/stats.py`:
  - Pure stdlib (`statistics`, `bisect`, `math`). **No `numpy` import** even though sklearn brings it transitively — `stats.py` is callable on a fresh `pip install corpus-forge` with no extras at all.
  - `compute_token_stats(chunks: Iterable[dict[str, Any]]) -> dict[str, Any]`:
    - Reads `c["token_count"]` per chunk (treated as authoritative; the module does NOT tokenize).
    - Returns `{"p50": int, "p95": int, "mean": float, "min": int, "max": int, "token_total": int, "n": int}`.
    - Empty input → all-zero dict.
  - `compute_length_distribution(chunks, *, bins: int = 10) -> dict[str, Any]`:
    - Returns `{"edges": list, "counts": list}` with `len(edges) == bins + 1`, `len(counts) == bins`.
    - Bin edges chosen via min/max + uniform width; counts populated via `bisect`.

### O1-Q1 (QA)
- `git diff --cached --stat` (per `feedback_icloud_commit_check.md` — verify the N-files / +X / -Y summary before reporting back to the orchestrator) shows ONLY:
  - `pyproject.toml`
  - `corpus_forge/config.py`
  - `config.example.toml`
  - `corpus_forge/alembic/versions/0012_analyze_signals.py`
  - `corpus_forge/analyze/__init__.py`
  - `corpus_forge/analyze/stats.py`
  - `tests/unit/test_analyze_config.py`
  - `tests/unit/test_analyze_stats.py`
  - `tests/unit/test_pyproject_extras_analyze.py`
  - `tests/integration/test_migrate_0012_analyze.py`
  - `.planning/tdd/phase_o_eda_cleaning.md`
  - `.planning/tdd/tasks.md`
- **Any file under `corpus_forge/curation/`, `corpus_forge/mcp/`, `corpus_forge/retrieval/`, `corpus_forge/cli.py` is rework — Wave O1 stays in foundations territory.**
- `uv run ruff format --check corpus_forge tests` → clean.
- `uv run ruff check corpus_forge tests` → clean.
- `uv run pyrefly check corpus_forge` → ≤ 10 errors (Phase N baseline; no new errors introduced).
- `uv run pytest tests/unit tests/integration tests/admin tests/mcp tests/smoke -x` → all Wave O1 new tests green + no regressions.
- `python -c "from corpus_forge.config import Config; ..."` works without the `[analyze]` extra installed (env without sklearn/hdbscan/etc. — verified via a temp venv or `uv pip install -e .` without the extra).
- `uv run corpus-forge doctor` → exits 0, no new warnings vs. Phase N baseline.
- `time uv run corpus-forge --help` → ≤ baseline + 100 ms. Baseline measured at the start of Wave O1 RED and recorded in the wave gate report.

## DAG
- **Wave 1a (RED, fan out 4-wide)**: **O1-T1, O1-T2, O1-T3, O1-T4** — disjoint surfaces, no deps. Dispatch all four tdd-tester agents in one message.
- **Wave 1b (GREEN, gated on RED)**: once tdd-tester reports red across all four → fan out:
  - **O1-G1** (depends on O1-T3) → tdd-coder.
  - **O1-G2** (depends on O1-T1) → tdd-coder (parallel).
  - **O1-G3** (depends on O1-T4) → tdd-coder (parallel — different file).
  - **O1-G4** (depends on O1-T2) → tdd-coder (parallel — different file).
- **Wave 1c (QA)**: **O1-Q1** — after all four GREEN tasks report green. tdd-qa runs the full gate matrix.
- **Wave gate**: orchestrator stages + commits per `feedback_tdd_worker_commits.md`. Proposed commit message: `"0.1.0b7 — Phase O Wave 1: EDA foundations"` (matches the existing release-cadence pattern: `6612a8e 0.1.0b6 — Phase N: retrieval quality`, `d226c7c 0.1.0b5 — Phase M: …`, etc).

## Status files
- `.planning/tdd/test-status.md` — owned by tdd-tester.
- `.planning/tdd/code-status.md` — owned by tdd-coder.
- `.planning/tdd/qa-status.md` — owned by tdd-qa.
- This file (`tasks.md`) — owned by tdd-principal.

## Open questions for the user (none blocking O1)
- **PyPI name for fasttext-langdetect**: coder verifies during O1-G1; if the canonical distribution name is different (e.g. `ft-langdetect`), update O1-T3's expected list before committing.
- **BERTopic transient deps for O3**: plotly + pandas come in via bertopic 0.16. Whether to pin `plotly>=5.0` explicitly in the `analyze` extra is an O3-time decision (deferred; revisit when O3 RED tests fail on a fresh venv).
