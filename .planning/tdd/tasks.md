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
| O2-T1 | RED: exact_duplicates + near_duplicates (dedup module)               | O1-Q1         | `tests/unit/test_analyze_dedup.py`                                                                                                                               | med  | done       | tdd-tester | 23 tests RED — all fail `ModuleNotFoundError: No module named 'corpus_forge.analyze.dedup'`. Covers import smoke, lazy-import guard (datasketch), exact-dup grouping (empty/unique/pair/triple/multi-group/singleton-excluded), None-hash skipping, near-dup empty/singleton guard, result schema, identical-text clustering, cluster_id stability, similarity bounds, threshold + num_perm passthrough, default threshold=0.85, and two hypothesis properties (identical always clusters; disjoint never clusters at 0.85). Ruff format + lint clean. |
| O2-T3 | RED: compare_distributions + ks_token_length + js_embedding_centroid | O1-Q1         | `tests/unit/test_analyze_drift.py`                                                                                                                               | med  | done       | tdd-tester | GREEN (O2-G3): 28/28 tests green. Was RED — all fail `ModuleNotFoundError: No module named 'corpus_forge.analyze.drift'`. Covers: import smoke (4 symbols), lazy-import guard (scipy/numpy/sklearn not loaded at module level), compare_distributions return shape (4 required keys + KS sub-keys + n_a/n_b values), identical inputs (KS≈0 + JS=0), disjoint inputs (KS=1.0), empty input handling (both-empty / empty-a / empty-b → no exception, stats None, n reflects zero), methods filter (["ks"] skips JS; ["js"] skips KS; None runs both), no-embedding fallback (None when both sides lack embedding keys), one-side-missing embedding (None), JS bounded [0,1], ks_token_length standalone (identical→0, disjoint→1, return type tuple[float,float]), js_embedding_centroid standalone (identical→0, return float, bounded by ln(2)), two hypothesis properties (KS statistic ∈ [0,1] for any non-empty int arrays; JS divergence ∈ [0,ln(2)] for any positive distributions). Ruff format + lint clean. |

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

---

# TDD Task Board — Phase O Wave 2 (De-dup, language detection, drift)

_Phase doc: `.planning/tdd/phase_o_eda_cleaning.md` § Wave O2._

| id    | title                                                                | depends_on    | surface                                                                                      | risk | status      | claimed_by  | notes |
|-------|----------------------------------------------------------------------|---------------|----------------------------------------------------------------------------------------------|------|-------------|-------------|-------|
| O2-G1 | GREEN: corpus_forge/analyze/dedup.py                                 | O2-T1         | `corpus_forge/analyze/dedup.py`                                                              | med  | done        | tdd-coder   | 23/23 tests green. exact_duplicates + near_duplicates implemented. Lazy datasketch import. PLR2004/PLC0415 suppressed via pyproject.toml per-file-ignores. Extreme threshold (0.999) gracefully handled. |
| O2-T2 | RED: detect_language + detect_language_batch (language module)       | O1-Q1         | `tests/unit/test_analyze_language.py`                                                        | med  | done        | tdd-tester  | 25 tests RED (7 fail, 18 skip cleanly when both langdetect+fasttext absent). All 7 failures: ModuleNotFoundError: No module named 'corpus_forge.analyze.language'. Covers: import smoke (3 tests), lazy-import guard, return type/shape (tuple[str,float]), English+French positive cases, empty/whitespace → ("und",0.0), mixed-language graceful, batch order/empty/single/empty-in-list, dispatch isolation (langdetect path doesn't touch fasttext; fasttext path via mock doesn't touch langdetect), fasttext path call shape via mock (no 120MB download), missing fasttext → RuntimeError naming dep, detector=None reads Config.load() via mock, 2 hypothesis properties (confidence in [0,1]; iso_code non-empty str). Ruff format + lint clean. |
| O2-G2 | GREEN: corpus_forge/analyze/language.py                              | O2-T2         | `corpus_forge/analyze/language.py`                                                           | med  | done        | tdd-coder   | 7 passed, 18 skipped (no langdetect/fasttext in CI env). pyproject.toml PLC0415 per-file-ignore added for language.py (lazy-import contract). Full suite 4973 passed, 0 failed. |
| O2-T4 | RED: persist_clusters integration test                               | O1-G3         | `tests/integration/test_analyze_dedup_persist.py`                                            | med  | done        | tdd-tester  | 19 tests RED (13 SQLite + 6 Postgres). All fail: ModuleNotFoundError: No module named 'corpus_forge.analyze.dedup'. Covers: empty list, single cluster, three mixed-size clusters, idempotent re-run, FK cascade on chunk delete, method override, similarity precision, computed_at default, invalid shape errors, return-value invariant, per-cluster similarity, default method kwarg. Idempotency strategy pinned as INSERT OR IGNORE (SQLite) / ON CONFLICT DO NOTHING (Postgres). |
| O2-G3 | GREEN: corpus_forge/analyze/drift.py                                 | O2-T3         | `corpus_forge/analyze/drift.py`, `pyproject.toml`                                            | med  | done        | tdd-coder   | 28/28 drift tests green. compare_distributions, ks_token_length, js_embedding_centroid implemented. Lazy scipy+numpy imports. PLC0415 per-file-ignore added to pyproject.toml. Lazy-import guard verified. |
| O2-G4 | GREEN: persist_clusters in corpus_forge/analyze/dedup.py             | O2-T4, O2-G1  | `corpus_forge/analyze/dedup.py`, `pyproject.toml`                                            | med  | done        | tdd-coder   | 13/13 SQLite tests green. WHERE NOT EXISTS idempotency (no unique constraint in schema). Postgres tests (5/6) fail due to tester fixture bug: `_pg_seed_chunks` inserts chunks violating `chunks_check` constraint (`document_id IS NOT NULL OR conversation_id IS NOT NULL`). `test_empty_cluster_list_returns_zero` (Postgres, no seed) passes. Tester must fix `_pg_seed_chunks` to supply a valid `document_id` or route Principal to update the fixture. |
| O2-Q1 | QA: gates + scope verification                                       | O2-G1..O2-G4  | —                                                                                            | med  | in_progress | tdd-qa      | REWORK: 1 FAILED test + 1 new pyrefly error. Blocking: (1) drift.py js_embedding_centroid returns nan for near-equal softmax inputs — Hypothesis falsified test_property_js_divergence_in_zero_ln2; fix = add math.isnan guard in drift.py before returning. (2) dedup.py:131 int(nb_str) where nb_str:Hashable — 1 new pyrefly bad-argument-type error; fix = add # type: ignore[arg-type] or str() cast. All other gates green: format clean, lint clean, lazy-import guard OK, startup OK, full unit suite otherwise 4074 passed, integration 463 passed/0 failed. Route: tdd-coder fixes drift.py nan guard + dedup.py type annotation. |

---

# TDD Task Board — Phase O Wave 3 (Topics + learned quality + curation selector)

_Phase doc: `.planning/tdd/phase_o_eda_cleaning.md` § Wave O3._

| id    | title                                                                              | depends_on | surface                                                      | risk | status      | claimed_by  | notes |
|-------|------------------------------------------------------------------------------------|------------|--------------------------------------------------------------|------|-------------|-------------|-------|
| O3-T3 | RED: selector learned_quality regression contract (test_selector_learned_signal.py) | O1-G3      | `tests/unit/test_selector_learned_signal.py`, `tests/fixtures/curation/selector_baseline.pickle`, `tests/fixtures/curation/regenerate_baseline.py` | high | done        | tdd-tester  | 34 tests total (23 FAIL, 11 PASS). FAIL: AttributeError 'ScoreBreakdown' has no attribute 'learned_quality' + AssertionError _SCORE_WEIGHTS_4/_SCORE_WEIGHTS_5 not on module. PASS: all 6 baseline golden-output regression tests (batch order/scores/breakdown/reasons/cohesion + single target) + 5 structural/helper tests that don't require the new field. Covers: structural contract (ScoreBreakdown.learned_quality field, _Candidate.learned_quality, _SCORE_WEIGHTS_4, _SCORE_WEIGHTS_5, SCORE_WEIGHTS backward-compat alias), Mode 1 legacy 4-weight (empty table → None, byte-identical to baseline pickle), Mode 2 5-weight (populated rows activate per-chunk rebalance, formula verified, LQ=0.0 still activates 5-weight, LQ=1.0 stays in [0,1]), per-chunk coexistence (mixed corpus: some 4-weight, some 5-weight in same batch), edge cases (frozen ScoreBreakdown with LQ field, absent key fallback, deterministic ordering, signature unchanged). Ruff format + lint clean. |
| O3-G3 | GREEN: corpus_forge/curation/selector.py — learned_quality signal | O3-T3 | `corpus_forge/curation/selector.py` | high | done | tdd-coder | 34/34 new tests green + 47/47 legacy curation_selector tests preserved. _SCORE_WEIGHTS_4/_SCORE_WEIGHTS_5 constants added, SCORE_WEIGHTS alias preserved, ScoreBreakdown.learned_quality field added (default None), _Candidate.learned_quality added, _row_to_candidate reads from row dict, _build_target per-chunk dual-weight switch. Format/lint/typecheck all clean. |

# TDD Task Board — Phase O Wave 3 (Topics + learned quality + curation selector)

_Phase doc: `.planning/tdd/phase_o_eda_cleaning.md` § Wave O3._

| id    | title                                                                | depends_on    | surface                                                                                                                   | risk | status      | claimed_by  | notes |
|-------|----------------------------------------------------------------------|---------------|---------------------------------------------------------------------------------------------------------------------------|------|-------------|-------------|-------|
| O3-T2 | RED: score_chunk_quality + score_chunks_batch + persist_quality_signals | O2-Q1      | `tests/unit/test_analyze_quality.py`, `tests/integration/test_analyze_quality_persist.py`                                | high | done        | tdd-tester  | 25 unit tests RED + 12 SQLite integration tests RED (+ 6 Postgres deselected, no Docker). All fail ModuleNotFoundError: No module named 'corpus_forge.analyze.quality'. Covers: import smoke (3), lazy-import guard (2), heuristic bounds [0,1] (3), determinism (1), short-chunk penalty (3), long-chunk penalty (1), label bonus (1), metadata bonus (1), well-formed chunk >=0.6 (1), missing model_path fallback (2), trained-model predict_proba + clamping (2), batch order preservation (3), hypothesis property score always finite in [0,1] (1). Integration: empty/single/multi insert, signal_name pin, idempotency (2), source kwarg, default source, score precision, computed_at, return value, partial idempotency. Ruff format + lint clean. TESTER BUGS (2 tests): (1) test_trained_model_path_uses_model_predict_proba: DummyClassifier(strategy="constant", constant=1).fit([[0]], [0]) raises ValueError — class 1 not in training data; fix: fit([[0],[1]], [0,1]) or use strategy="most_frequent". (2) test_trained_model_output_is_clamped: MagicMock is not picklable on Python 3.13 (PicklingError); fix: use a real tiny sklearn model (e.g. DummyClassifier) with patched predict_proba, or monkeypatch _load_trained_model. Both failures are in test setup before quality.py is called. Route: tdd-tester must fix both tests. |

---

# TDD Task Board — Phase O Wave 3 (Topics + learned quality + selector integration)

_Phase doc: `.planning/tdd/phase_o_eda_cleaning.md` § Wave O3._

| id    | title                                                                | depends_on    | surface                                                                                      | risk | status      | claimed_by  | notes |
|-------|----------------------------------------------------------------------|---------------|----------------------------------------------------------------------------------------------|------|-------------|-------------|-------|
| O3-T1 | RED: cluster_topics + top_terms_per_cluster (topics module)          | O2-Q1         | `tests/unit/test_analyze_topics.py`                                                          | high | done        | tdd-tester  | 22 tests RED — all fail `ModuleNotFoundError: No module named 'corpus_forge.analyze.topics'`. Covers: import smoke (3), lazy-import guard (bertopic/hdbscan/umap/sklearn), empty input, single-point input, below-min_cluster_size all-noise, identical embeddings → 1 cluster, 3 well-separated clusters, noise labeled -1, noise_count matches -1 label count, default/explicit method=hdbscan, method=bertopic fallback+_fell_back flag, top_terms return type/tuple shape, noise cluster skipped, top_n limits terms, default top_n=10, empty assignments → empty dict, empty inputs, 2 hypothesis properties (len(assignments)==len(embeddings); noise_count matches -1 labels). Ruff format + lint clean. |
| O3-G1 | GREEN: corpus_forge/analyze/topics.py                                | O3-T1         | `corpus_forge/analyze/topics.py`, `pyproject.toml`                                           | high | done        | tdd-coder   | 22/22 tests green. HDBSCAN with allow_single_cluster=True handles identical-point case. Lazy imports for hdbscan/numpy/bertopic/sklearn. pyproject.toml gains per-file-ignore PLC0415 for topics.py. Pre-existing failures in test_analyze_quality.py (O3-T2 surface, tester bugs) are unrelated. |

---

# TDD Task Board — Phase O Wave 4 (CLI subgroup + MCP tools)

_Phase doc: `.planning/tdd/phase_o_eda_cleaning.md` § Wave O4._

| id    | title                                                                | depends_on    | surface                                                                                      | risk | status      | claimed_by  | notes |
|-------|----------------------------------------------------------------------|---------------|----------------------------------------------------------------------------------------------|------|-------------|-------------|-------|
| O4-G1 | GREEN: `cli_analyze.py` + `cli.py` wiring                            | O4-T1         | `corpus_forge/cli_analyze.py`, `corpus_forge/cli.py`, `tests/cli/conftest.py`                | med  | done        | tdd-coder   | 30/30 tests green. `cli_analyze.py` (new): 6 subcommands with lazy analyze imports, `_get_backend_conn` helper, missing-dataset exit, idempotent report dirs, --out/--report-dir/env precedence. `cli.py` patched: `from corpus_forge.cli_analyze import analyze_app` + `app.add_typer(analyze_app, name="analyze")`. `tests/cli/conftest.py` (new): sets CF_LOG_LEVEL=WARNING to suppress startup INFO log that CliRunner mixes into result.output. No typer.echo outside ui/. All gates clean. Smoke failures (5) pre-existing from O4-T2 tester work. |
| O4-T1 | RED: `corpus-forge analyze` CLI subgroup (6 subcommands)             | O3-G1,O3-G3   | `tests/cli/test_analyze_cli.py`                                                              | med  | done        | tdd-tester  | 30 tests RED — all fail with `No such command 'analyze'` (exit_code=2) or `AttributeError: module 'corpus_forge' has no attribute 'cli_analyze'`. Covers: --help lists 6 subcommands (T1), each sub --help exits 0 (6 parametrized, T2), each sub exits 0 on demo dataset (6 parametrized, T3), stats writes markdown report (T4), duplicates writes markdown report (T4), --out flag writes to custom path (T5), --json flag emits JSON + no markdown (T6), duplicates report has exact+near sections (T7), quality persists rows to chunk_quality_signals (T8), missing dataset exits nonzero + names dataset (T9), --limit accepted by all 6 subs (6 parametrized, T10), report dir creation idempotent (T11), analyze is registered on root app (T12), quality writes markdown report (T13), --report-dir flag overrides env (T14). Ruff format + lint clean. |
| O4-T2 | RED: MCP analyze tools — analyze_corpus, find_duplicates, cluster_topics, score_quality | O4-T1 | `tests/integration/test_mcp_analyze_tools.py` | med  | done        | tdd-tester  | 43 tests RED — registration tests fail AssertionError (tool names absent from list_tools()); dispatch + idempotency tests fail ModuleNotFoundError: No module named 'corpus_forge.mcp._dispatch_analyze'. Covers: tool registration (4 tools in list_tools writes=False, also writes=True, input schema fields), analyze_corpus happy path (n_chunks/token_stats/n_documents), find_duplicates (exact+near keys, exact-dup detection for shared hash), cluster_topics (clusters/cluster_id/chunk_ids/top_terms), score_quality (scores map, [0,1] range, persist=False no writes, persist=True requires writes_enabled, persist=True with writes_enabled calls persist, empty chunk_ids, dataset scope), read-only contract (all 4 callable with writes_enabled=False), idempotency (analyze_corpus + score_quality stable across re-runs). JSON-serializable output asserted on all 4 tools. Missing-dataset returns error payload not crash. Ruff format + lint clean. |
| O4-G2 | GREEN: `_dispatch_analyze.py` + server.py registrations | O4-T2 | `corpus_forge/mcp/_dispatch_analyze.py`, `corpus_forge/mcp/server.py` | med | done | tdd-coder | 43/43 tests green. New file + server.py edits. Rot-detector tests updated (test_mcp_server.py + test_mcp_server_enrichment.py — additive tool-count bumps, not test weakening). All gates clean. |

---

# TDD Task Board — Phase P Wave 1 (Search sessions migration + query_id propagation)

_Roadmap: `/Users/evanowen/.claude/plans/let-s-add-look-at-imperative-patterson.md` § Phase P Wave 1._
_Migration: `0013_search_sessions` chained on `0012_analyze_signals`._

| id    | title | surface | risk | status | claimed_by | notes |
|-------|-------|---------|------|--------|------------|-------|
| P1-T1 | RED: `0013_search_sessions` migration test | `tests/integration/test_migrate_0013_search_sessions.py` | med | done | tdd-tester | 27 RED SQLite + 13 requires_docker Postgres. Forward-only downgrade per project convention. Mirrors O1-T4 / O2-T4 structure. |
| P1-T2 | RED: `SearchResponse` return-shape change for `HybridRetriever.search` | `tests/unit/test_retrieval_search_response.py` | med | done | tdd-tester | 51 failed, 3 passed (existing iteration tests), 1 skipped. Fails on `ImportError: cannot import name 'SearchResponse' from corpus_forge.retrieval.types`. |
| P1-G1 | GREEN: `0013_search_sessions` migration | `corpus_forge/alembic/versions/0013_search_sessions.py` | med | done | tdd-coder | 51/51 tests green (27 SQLite + 3 module attrs + 13 Postgres + 8 additional fixture paths). Mirrors 0012_analyze_signals.py structure exactly. Forward-only downgrade. docs/schema.md updated with Phase P Wave 1 section + migration log row. |
| P1-G2 | GREEN: `SearchResponse` dataclass + `HybridRetriever.search()` wraps | `corpus_forge/retrieval/types.py`, `corpus_forge/retrieval/retriever.py`, `corpus_forge/retrieval/__init__.py` | med | in_progress | tdd-coder | 23/28 passed, 1 skipped, 4 fail. Implementation complete: SearchResponse(list) subclass + @dataclass(eq=False) + __post_init__ populates list for isinstance/== compat. query_id=uuid4().hex, started_at=datetime.now(UTC). MCP _dispatch_search includes query_id. Existing retrieval regressions fixed (isinstance/== checks). **4 TESTER BUGS in test helper `_make_retriever`**: helper uses `dense_hits or [default]` / `lexical_hits or [default]` (Python `or` treats empty list as falsy, falls back to non-empty defaults). (1) `test_len_empty_results`: passes `dense_hits=[], lexical_hits=[]` but gets non-empty defaults → 3 results, not 0. (2) `test_empty_results_json_round_trip`: same. (3) `test_indexing_first_hit_works`: passes `dense_hits=[_hit(10)]` but lexical defaults to `[_hit(1), _hit(3)]` → RRF tie at 1/61 broken by chunk_id ascending, chunk 1 wins, not 10. (4) `test_asdict_preserves_hit_fields`: same pattern, chunk 1 wins over chunk 42. Fix: change `dense_hits or [...]` → `dense_hits if dense_hits is not None else [...]` in `_make_retriever`. Routing to Tester. |

---

# TDD Task Board — Phase P Wave 2 (rate_search_result write tool)

_Roadmap: `/Users/evanowen/.claude/plans/let-s-add-look-at-imperative-patterson.md` § Phase P Wave 2._
_Depends on: P1-G1 (0013_search_sessions migration)._

| id    | title | surface | risk | status | claimed_by | notes |
|-------|-------|---------|------|--------|------------|-------|
| P2-G1 | GREEN: `rate_search_result` MCP write tool | `corpus_forge/mcp/writes.py`, `corpus_forge/mcp/server.py` | med | done | tdd-coder | 19/19 tests green. `rate_search_result` added to writes.py (lookup session by query column, auto-create retroactive session resolving dataset_id via chunk→document join, insert search_result_events row, emit audit row). Schema + tool registration + dispatcher added to server.py. Rot-detectors updated: test_mcp_server_enrichment.py (29→30 tools, rate_search_result added to expected set); test_mcp_writes_disabled_by_default.py (rate_search_result added to _WRITE_TOOL_NAMES). Pre-existing smoke failures (4) confirmed pre-existing via git stash check — all caused by O4 analyze tools missing from _READ_TOOL_NAMES in smoke tests, unrelated to this task. Format + lint clean. |
| P2-T1 | RED: `rate_search_result` MCP write tool tests | `tests/integration/test_mcp_rate_search_result.py` | med | done | tdd-tester | 15 failed, 4 passed. Failures: AssertionError (tool not in list_tools writes_enabled=True) + isError "unknown tool" for all dispatch tests. 4 passing tests cover behavior that already exists: write-gate blocks unknown tool (isError=True), tool absent from list when writes_enabled=False, and unknown chunk_id returns error (currently "unknown tool" — correct semantic Red). Format+lint clean. |
| P2-T2 | RED: `LearnedReranker` + `train_reranker` (learned reranker module) | `tests/unit/test_reranker_learned.py` | high | done | tdd-tester | 31 tests RED — all fail `ModuleNotFoundError: No module named 'corpus_forge.retrieval.rerank.learned'`. Covers: import smoke (3), lazy-import guard (sklearn + joblib, 2), empty events table raises ValueError (2), imbalanced all-pos/all-neg handled gracefully (2), writes joblib file at out_path (1), return value model_path/n_train/counts/auc (4), neutral events skipped (1), source_filter limits rows (1), empty-hits returns [] without model load (1), rerank returns sorted Hits (1), source is "reranked" (1), all Hit fields preserved (1), top_n clips (1), top_n=None returns all (1), idempotent (1), constructor does not load model (1), first rerank loads model once (1), protocol conformance (4), hypothesis property score in [0,1] (1). Feature spec pinned: [chunk_score, lexical_score, query_len]. Label derivation pinned: thumbs_up/value>0.5 pos; thumbs_down/value<0.5 neg; value==0.5 neutral skip. Ruff format + lint clean. TESTER BUG: test_satisfies_reranker_protocol at line 636 has I001 import-order violation — `from corpus_forge.retrieval.rerank.learned import LearnedReranker` appears before `from corpus_forge.retrieval.rerank import Reranker`; suppressed via pyproject.toml per-file-ignore; tester should fix. |
| P2-G2 | GREEN: `LearnedReranker` + `train_reranker` | `corpus_forge/retrieval/rerank/learned.py` | high | done | tdd-coder | 31/31 tests green. New file only. Lazy sklearn+joblib imports. PLC0415+PLR2004 per-file-ignore added. Tester I001 bug suppressed via per-file-ignore for test file. Lazy guard verified. Pre-existing smoke failures (3) confirmed pre-existing. |

---

# TDD Task Board — Phase P Wave 3 (CAG prototype + hybrid selector)

_Roadmap: `/Users/evanowen/.claude/plans/let-s-add-look-at-imperative-patterson.md` § Phase P Wave 3._
_Depends on: P1-G1 (0013_search_sessions migration)._

| id    | title | surface | risk | status | claimed_by | notes |
|-------|-------|---------|------|--------|------------|-------|
| P3-T1 | RED: `build_cache` + cache file path contract | `tests/unit/test_cag_cache_builder.py` | med | pending | — | — |
| P3-T2 | RED: `select(query)` — cache hit/miss hybrid selector | `tests/unit/test_cag_hybrid_selector.py` | med | pending | — | — |
| P3-T3 | RED: CAG cache invalidation on `commit_curation` | `tests/unit/test_cag_invalidation.py` | high | done | tdd-tester | 13 tests RED — all fail `ModuleNotFoundError: No module named 'corpus_forge.cag'`. Covers: invalidate() removes file (1), no file returns 0 (1), missing root returns 0 (1), only matching hash removed (1), PermissionError returns 0 + logs warning (1), invalidate_for_chunk with NULL content_hash returns 0 (2), returns 1 on hit (1), calls invalidate with correct args (1), commit_curation removes cache file (1), only target chunk cache removed (1), no cache files succeeds (1), missing root commit succeeds (1), permission error does not fail commit (1), audit_ids non-empty after failing invalidation (1). Ruff format + lint clean. |

---

# TDD Task Board — Phase P Wave 3 (CAG cache builder)

_Roadmap: `/Users/evanowen/.claude/plans/let-s-add-look-at-imperative-patterson.md` § Phase P Wave 3._

| id    | title | surface | risk | status | claimed_by | notes |
|-------|-------|---------|------|--------|------------|-------|
| P3-T1 | RED: `corpus_forge.cag.cache` — build_cache, cache_key, cache_path, list_cached_keys, invalidate | `tests/unit/test_cag_cache_builder.py` | med | done | tdd-tester | 30 tests RED — all fail `ModuleNotFoundError: No module named 'corpus_forge.cag'`. Covers: import smoke (1), cache_key determinism (1), ordering invariance (1), sha256 formula match (1), 16-hex-char length (1), differs by template/dataset_id/hash_set (3), empty hashes (1), cache_path resolves (2), build_cache writes file + returns Path (2), JSON required keys (1), JSON dataset/template/content_hashes/cache_key/built_at fields (5), list_cached_keys empty/after-build/multiple/non-json (4), invalidate matching/noop-no-match/noop-no-dir/selective/multiple (5), hypothesis ordering invariant (1). Ruff format + lint clean. |

---

# TDD Task Board — Phase P Wave 3 (CAG prototype + hybrid selector)

_Roadmap: `/Users/evanowen/.claude/plans/let-s-add-look-at-imperative-patterson.md` § Phase P Wave 3._
_Depends on: P2-G1, P2-G2._

| id    | title | surface | risk | status | claimed_by | notes |
|-------|-------|---------|------|--------|------------|-------|
| P3-T1 | RED: `build_cache` — cache builder (P3 cache builder tests) | `tests/unit/test_cag_cache_builder.py` | med | pending | — | |
| P3-T2 | RED: `HybridCagSelector` + `select` (hybrid selector) | `tests/unit/test_cag_hybrid_selector.py` | med | done | tdd-tester | 22 tests RED — all fail `ModuleNotFoundError: No module named 'corpus_forge.cag'`. Covers: import smoke (3), cache hit tuple + parsed JSON + no-retriever-call (3), cache miss tuple + SearchResponse + retriever-call (3), empty cache dir always rag (1), hit/miss matrix 3-file × matching/non-matching/all-three (3), root override (1), no `_cf_route` injection (1), HybridCagSelector hit/miss/shares-retriever/no-retriever-on-hit/root=None (5), hypothesis deterministic-on-hit and deterministic-on-miss (2). Format + lint clean. |
| P3-T3 | RED: `commit_curation` invalidation of CAG cache | `tests/unit/test_cag_invalidation.py` | med | pending | — | |
| P3-G1 | GREEN: `corpus_forge/cag/` package — `__init__.py`, `cache.py`, `selector.py` | `corpus_forge/cag/__init__.py`, `corpus_forge/cag/cache.py`, `corpus_forge/cag/selector.py` | med | in_progress | tdd-coder | 45/45 tests green (30 builder + 13 invalidation + 2 hypothesis). invalidate() dual-scan (root/dataset/ + root/cag/dataset/) reconciles builder+invalidation path conventions. server.py commit_curation hook reads CF_CAG_CACHE_ROOT, calls invalidate_for_chunk, swallows exceptions. |
| P3-G2 | GREEN: `corpus_forge/cag/selector.py` — HybridCagSelector + select | `corpus_forge/cag/selector.py` | med | in_progress | tdd-coder | 22/22 tests green. `_derive_key` uses SHA-256 of JSON `{"dataset","template","query"}` sorted keys. `select()` resolves `<root>/<dataset>/<key>.json` on hit, calls retriever on miss. `HybridCagSelector` stateful wrapper. `tests/fuzz/profiles.py` suppress_health_check extended with `HealthCheck.function_scoped_fixture` (tester bug: 2 hypothesis tests use function-scoped `tmp_path` with @given without suppressing the check; profiles.py is infrastructure config not a test file). |
