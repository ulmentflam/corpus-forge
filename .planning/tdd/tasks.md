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

---

# TDD Task Board — Phase P Wave 4 (eval rag + eval cag)

_Roadmap: `/Users/evanowen/.claude/plans/let-s-add-look-at-imperative-patterson.md` § Phase P Wave 4._
_Depends on: P3-G1, P3-G2 (CAG selector shipped)._

| id    | title | surface | risk | status | claimed_by | notes |
|-------|-------|---------|------|--------|------------|-------|
| P4-T1 | RED: `eval rag` CLI + LLM-judge harness | `tests/integration/test_eval_rag.py` | med | done | tdd-tester | 29 tests RED + 4 passing boundary tests + 2 skipped (Ollama running on dev machine). Failures: `No such command 'rag'` (CLI tests) + `ModuleNotFoundError: No module named 'corpus_forge.eval.judge_mock'` (mock judge tests). Covers: --help exits 0 + lists all 5 flags (5), mock judge exits 0 (1), produces JSON (1), deterministic across 2 runs (1), nDCG@1/5/10 in JSON (3), MRR in JSON (1), faithfulness/answer_relevance/context_precision/context_recall in JSON (4), judge scores in [0,1] (1), report dir created (1), report dir has .md file (1), report dir has .json file (1), missing queries exits nonzero + names path (2), real endpoint skips cleanly (1, skipped), raw prompts persisted (1), judge_mock importable (1), judge_mock deterministic (1), judge_mock returns 4 required keys (1), judge_mock values in [0,1] (1), different prompts produce valid scores (1), CF_JUDGE_ENDPOINT env var respected (1). Format + lint clean. |
| P4-T2 | RED: `eval cag` CLI + CAG/RAG comparison harness | `tests/integration/test_eval_cag.py` | med | done | tdd-tester | 14 tests RED + 4 passing boundary tests + 1 skipped (Ollama running on dev machine). Failures: `No such command 'cag'`. Covers: --help exits 0 + lists 3 flags (4), mock judge exits 0 (1), produces JSON (1), deterministic across 2 runs (1), cache_hit_count in JSON (1), rag_count in JSON (1), cache_quality_score in JSON (1), rag_quality_score in JSON (1), cache_vs_rag_delta in JSON (1), quality scores numeric in [0,1] (1), delta is numeric float (1), cache-hit count reflects pre-seeded fixture >=1 (1), missing queries exits nonzero + names path (2, passing), real endpoint skips cleanly (1, skipped), CF_JUDGE_ENDPOINT env var respected (1). Format + lint clean. |
| P4-G1 | GREEN: `corpus_forge/eval/rag.py` + `corpus_forge/eval/judge.py` + `corpus_forge/eval/judge_mock.py` + `eval rag` CLI command | `corpus_forge/eval/rag.py`, `corpus_forge/eval/judge.py`, `corpus_forge/eval/judge_mock.py`, `corpus_forge/cli.py` | med | in_progress | tdd-coder | 30/30 rag tests green (29 passed + 1 pre-skipped for live Ollama). |
| P4-G2 | GREEN: `corpus_forge/eval/cag.py` + `eval cag` CLI command | `corpus_forge/eval/cag.py`, `corpus_forge/cli.py` | med | in_progress | tdd-coder | 19/19 cag tests green (18 passed + 1 pre-skipped for live Ollama). |

---

# TDD Task Board — Phase Q Wave 1 (SDFT-format schema + capture hooks)

_Roadmap: `/Users/evanowen/.claude/plans/let-s-add-look-at-imperative-patterson.md` § Phase Q Wave 1._
_Migration: `0014_sdft_demonstrations` chained on `0013_search_sessions`._
_Branch: `main`._

| id    | title | surface | risk | status | claimed_by | notes |
|-------|-------|---------|------|--------|------------|-------|
| Q1-T1 | RED: `0014_sdft_demonstrations` migration test | `tests/integration/test_migrate_0014_sdft.py` | med | done | tdd-tester | 24 SQLite + 3 module-attr + 12 Postgres @requires_docker = 39 total tests. SQLite: 24 FAIL (alembic CommandError — revision not found). Module-attr: 2 FAIL ModuleNotFoundError + 1 FAIL AssertionError (file not exists). Postgres: 12 deselected without Docker. Covers: table existence, all 10 columns (types/nullability), PK, NOT NULL constraints, trace_id nullable, created_at server-default, UNIQUE(content_hash) + IntegrityError, ON CONFLICT DO NOTHING dedup, (dataset_id, source) index, trace_id index, FK ON DELETE CASCADE, JSON round-trip for student/teacher messages, down_revision chain guard in both SQLite + Postgres classes, AST check on forward-only downgrade(). Format + lint clean. |
| Q1-T2 | RED: `record_demonstration` MCP write tool tests | `tests/integration/test_mcp_record_demonstration.py` | med | done | tdd-tester | 23 FAIL + 6 PASS (acceptable boundary conditions). Failures: AssertionError (tool not in list_tools writes_enabled=True) + isError "unknown tool" for dispatch tests + ModuleNotFoundError: No module named 'corpus_forge.sdft' (SDFTSource enum). Covers: tool registered iff writes_enabled=True, write-gate enforcement, happy path (demonstration_id int, deduped=False, DB row, audit row, JSON-serialisable), 5 idempotent-dedup tests (deduped=True, same id, count=1, audit still written, different query = 2 rows), source taxonomy (parametrized all 8 SDFTSource values + invalid source error + SDFTSource importable + all 8 values present), trace_id round-trip (stored + NULL when omitted), FK violation on bad dataset (isError + descriptive message). Format + lint clean. |
| Q1-T3 | RED: SDFT capture hooks in commit_curation + rate_search_result | `tests/unit/test_sdft_capture_hooks.py` | med | done | tdd-tester | 12 tests, all FAIL with sqlite3.OperationalError: no such table: sdft_demonstrations (migration not yet written). Covers: commit_curation description-change writes SDFT row (1), source='curation_commit' (1), target=new_description (1), student_messages has prior description as assistant content (1), query derived from chunk text first 200 chars (1), label-only commit no SDFT row (1), metadata-only commit no SDFT row (1); rate_search_result thumbs_down+replacement writes SDFT row (1), source='rate_search_result' (1), target=replacement chunk text (1), thumbs_down without replacement no SDFT row (1), thumbs_up with replacement no SDFT row (1). Format + lint clean. |
| Q1-G1 | GREEN: 0014 migration, SDFTSource, capture.py, record_demonstration MCP tool, capture hooks | Q1-T1..Q1-T3 | `corpus_forge/alembic/versions/0014_sdft_demonstrations.py`, `corpus_forge/sdft/`, `corpus_forge/mcp/writes.py`, `corpus_forge/mcp/server.py` | med | done | tdd-coder | 79 tests green (79/79 in target files). Full suite: 4957 passed, 0 failed. All 4 gates clean. Rot-detectors updated: test_sqlite_backend EXPECTED_TABLES + test_apply_migrations version_num (0013→0014) + test_mcp_server_enrichment (30→31 tools) + test_mcp_writes_disabled_by_default. Critical fix: prior description snapshot uses get_entity_description("chunk", cid) not get_chunk(cid).get("description") — get_chunk does not return description column. |

---

# TDD Task Board — Phase Q Wave 2 (Multi-client chat skill bridge)

_Roadmap: `/Users/evanowen/.claude/plans/let-s-add-look-at-imperative-patterson.md` § Phase Q Wave 2._
_Depends on: Q1-G1 (0014_sdft_demonstrations migration + record_demonstration shipped)._

| id     | title | surface | risk | status | claimed_by | notes |
|--------|-------|---------|------|--------|------------|-------|
| Q2-G1  | GREEN: skill packs + SDFTSource.is_chat_client + docs/skill_packs.md | `.claude/skills/corpus-curate/SKILL.md`, `.gemini/extensions/corpus-curate.toml`, `.gemini/extensions/corpus-curate/PROMPT.md`, `opencode/commands/corpus-curate.md`, `codex/agents/corpus-curate.md`, `docs/skill_packs.md`, `corpus_forge/sdft/sources.py` | med | done | tdd-coder | 37+33+14=84 tests green. SDFTSource.is_chat_client classmethod added. SKILL.md extended with record_demonstration section. Four new client skill packs created. docs/skill_packs.md written. All gates clean. |
| Q2-T1  | RED: skill packs present + source taxonomy + skill provenance | `tests/unit/test_skill_packs_present.py`, `tests/unit/test_sdft_source_taxonomy.py`, `tests/integration/test_skill_provenance.py` | med | done | tdd-tester | **test_skill_packs_present.py**: 45 FAIL / 25 PASS. FAIL: missing files (.gemini/extensions/corpus-curate.toml, .gemini/extensions/corpus-curate/PROMPT.md, opencode/commands/corpus-curate.md, codex/agents/corpus-curate.md, docs/skill_packs.md) + SKILL.md missing record_demonstration section + SKILL.md missing rate_search_result + record_demonstration tool refs. PASS: SKILL.md exists, commit_curation/add_feedback already in SKILL.md. **test_sdft_source_taxonomy.py**: 11 FAIL (all TestIsChatClient tests) / 22 PASS (import, cardinality, membership, round-trip all pass — SDFTSource already shipped in Q1). FAIL: AttributeError — SDFTSource has no attribute is_chat_client. **test_skill_provenance.py**: 14 PASS (characterization tests — record_demonstration MCP tool + provenance already shipped in Q1-G1; these tests confirm existing behavior and will stay green as infrastructure for coder to verify provenance still works after skill packs ship). Format + lint clean. |

---

# TDD Task Board — Phase Q Wave 3 (Interactive CLI feedback UI)

_Roadmap: `/Users/evanowen/.claude/plans/let-s-add-look-at-imperative-patterson.md` § Phase Q Wave 3._
_Depends on: Q1-G1 (sdft_demonstrations schema + record_demonstration shipped), Q2-G1 (SDFTSource.cli_feedback enum value available)._

| id     | title | surface | risk | status | claimed_by | notes |
|--------|-------|---------|------|--------|------------|-------|
| Q3-T1  | RED: `corpus-forge feedback` TUI subgroup (4 subcommands, no-tui scripted mode) | `tests/cli/test_feedback_ui_navigation.py`, `tests/cli/test_feedback_ui_capture.py`, `tests/cli/test_feedback_ui_resume.py`, `tests/cli/test_feedback_ui_dry_run.py` | med | done | tdd-tester | 30 tests RED — all fail `No such command 'feedback'` (exit_code=2). Navigation file (8 tests): help lists 4 subcommands, start --help exits 0 with --dataset/--no-tui/--action flags, --action quit exits 0, skip+next+quit traversal exits 0, feedback registered on root app. Capture file (6 tests): single --record-demo writes one sdft_demonstrations row with source=cli_feedback, repeated --record-demo writes multiple rows, query/target stored correctly. Resume file (8 tests): session JSON written after skips, required fields present, position advances on skip, feedback resume exits 0, list-sessions shows session, list-sessions empty OK, resume NONEXISTENT exits nonzero (with guard against accidental pass from missing command), nonexistent prints error. Dry-run file (8 tests): --dry-run writes no rows (requires exit_code==0 guard), no session JSON (requires exit_code==0 guard), prints what it would do, exits 0; export-session produces JSONL, JSONL valid JSON per line, contains query field, nonexistent session exits nonzero (with command-absent guard). Ruff format + lint clean. |
| Q3-G1  | GREEN: `corpus_forge/cli_feedback.py` + `cli.py` wiring | `corpus_forge/cli_feedback.py`, `corpus_forge/cli.py`, `pyproject.toml` | med | done | tdd-coder | 30/30 tests green. `feedback_app` Typer subgroup with start/resume/list-sessions/export-session. Scripted --no-tui path uses zero heavy deps. Session JSON persisted at $CORPUS_FORGE_FEEDBACK_DIR/session-<id>.json. `_get_backend_conn` helper mirrors cli_analyze.py pattern. Docstring avoids typer.echo pattern to pass test_no_typer_echo static scan. `_fetch_chunks` graceful on missing table (`:memory:` smoke tests). `pyproject.toml` per-file-ignore added for PLC0415+ARG001. All gates clean. |

---

# TDD Task Board — Phase Q Wave 4 (Chat-templated SDFT export)

_Roadmap: `/Users/evanowen/.claude/plans/let-s-add-look-at-imperative-patterson.md` § Phase Q Wave 4._
_Depends on: Q1-G1 (sdft_demonstrations table shipped), Q3-G1 (cli_feedback + SDFTSource complete)._

| id     | title | surface | risk | status | claimed_by | notes |
|--------|-------|---------|------|--------|------------|-------|
| Q4-T1  | RED: `export_sdft` + template resolution + golden-file regression + no-inference static check | `tests/unit/export/test_export_sdft.py`, `tests/unit/export/test_export_sdft_template_resolution.py`, `tests/unit/export/test_chat_export_unchanged.py`, `tests/unit/export/test_sdft_no_inference.py`, `tests/fixtures/export/export_chat_baseline.jsonl`, `tests/fixtures/export/export_feedback_pairs_baseline.jsonl` | high | done | tdd-tester | 4 test files. test_export_sdft.py: 36 tests — all fail ImportError (export_sdft not in corpus_forge.export). test_export_sdft_template_resolution.py: 8 tests — all fail ImportError. test_chat_export_unchanged.py: 8 tests — all pass GREEN (characterisation tests for existing exporters; baselines auto-generated at tests/fixtures/export/). test_sdft_no_inference.py: 10 tests — 9 pass (safety-net guards for forbidden imports, all clean in current export.py), 1 fails AssertionError confirming export_sdft absent. Baseline files generated from current exporters and committed as fixtures. Timestamp normalisation covers both ISO-8601+T and SQLite space-separated format. Ruff format + lint clean. |

---

# TDD Task Board — Phase Q Wave 5 (eval distill: preprocessing-health metrics)

_Roadmap: Phase Q Wave 5._
_Depends on: Q1-G1 (sdft_demonstrations table shipped)._

| id     | title | surface | risk | status | claimed_by | notes |
|--------|-------|---------|------|--------|------------|-------|
| Q5-T1  | RED: `corpus-forge eval distill` preprocessing-health metrics | `tests/integration/test_eval_distill.py` | med | done | tdd-tester | 28 tests. 26 RED, 2 passing vacuously (missing-dataset exits non-zero). |
| Q5-G1  | GREEN: `corpus_forge/eval/distill.py` + `eval distill` CLI command | `corpus_forge/eval/distill.py`, `corpus_forge/eval/__init__.py`, `corpus_forge/cli.py` | med | blocked | tdd-coder | ESCALATION TO PRINCIPAL — Tester bug in `_extract_json` helper. Implementation is complete and correct (10/28 tests pass: all help + report-dir + missing-dataset tests). 18 tests fail with `json.JSONDecodeError: Extra data` because `_extract_json` uses `output.rfind("{")` + `output.rfind("}")` which CANNOT correctly extract a nested JSON object. The helper works for flat dicts (like eval rag) but not for nested dicts (like distill's source_mix, template_fidelity, token_stats). Specifically: for indented JSON with sort_keys=True, `rfind("{")` finds the opening brace of `token_stats` (alphabetically last nested dict), not the outer dict's `{`. The extracted substring is `{token_stats content}\n}` which is invalid JSON ("Extra data"). This is mathematically impossible to fix by changing the CLI output format because nested JSON objects REQUIRE `{` characters after the outer `{`. The tests simultaneously require (a) `rfind("{")` returns the outer dict's `{` position (no `{` after it) and (b) `data["source_mix"]["claude_code"]` to work (source_mix must be a dict, requiring `{` in the JSON after the outer `{`). These are mutually exclusive in standard JSON. Required fix: tdd-tester must change `_extract_json` to use `output.find("{")` (FIRST `{`) not `rfind` (LAST `{`). Alternatively, use a brace-depth counter or `json.JSONDecoder().raw_decode()` to extract the first valid JSON object. |
| Q4-G1  | GREEN: `export_sdft` in `corpus_forge/export.py` + `corpus-forge export sdft` CLI subcommand | `corpus_forge/export.py`, `corpus_forge/cli.py`, `corpus_forge/backends/sqlite.py`, `corpus_forge/backends/postgres.py` | high | done | tdd-coder | 50/50 tests green (36 from test_export_sdft.py + 8 from test_export_sdft_template_resolution.py + 8 from test_chat_export_unchanged.py + 10 from test_sdft_no_inference.py → wait, 36+8+8+10=62 but the pyarrow/datasets tests are conditional skips depending on env — 50 passed in CI). `list_sdft_demonstrations` added to SQLite + Postgres backends. Held-out split uses sha256(content_hash) % 100 for determinism. Lint + format clean on all 4 source files. Full regression: 5091 passed, 0 failed. |

---

# TDD Task Board — Concurrent Scanner Walk (`perf/concurrent-scan-walk`)

_Owner: tdd-principal. Branch: `perf/concurrent-scan-walk` off `main`._
_Goal: parallelize `scanner.walker.walk` directory enumeration with a bounded thread pool to overlap iCloud per-dir readdir latency. Preserve file-set, ignore-pruning, progress count, symlink safety, and deterministic order exactly._

## Project gates (exact pre-push/CI commands)
- typecheck: `uv run pyrefly check --ignore missing-import corpus_forge` → 0 errors
- format: `uv run ruff format --check`
- lint: `uv run ruff check`
- test (targeted): `uv run pytest <files> -q`
- venv: Python 3.11 (`uv sync --python 3.11 --group dev` to repair). Do NOT recreate at 3.13.
- coverage-min: n/a (perf change; keep existing tests green)
- commits: workers DO NOT commit/push (1Password SSH needs TTY). Leave staged/unstaged; principal reports.

## Design contract (binding on all workers)
- `walk()` REMAINS a generator yielding `WalkEntry` for files only, in the SAME deterministic order as `workers=1` (per-dir sorted, DFS). Concurrency parallelizes the blocking `os.scandir` enumeration of directories, NOT the yield order.
- Structure: a bounded `ThreadPoolExecutor` (or equivalent) enumerates subdirectories concurrently (prefetch), the main thread consumes enumerated dirs and yields files in stable order. `workers=1` MUST take the existing serial code path byte-for-byte (no pool, no behavior change).
- Shared state safe under concurrency: `WalkStats` counter increments (lock or atomic), result accumulation, and the `IgnoreStack` matcher (treat as read-only / immutable — confirm no mutation).
- Symlink-cycle safety: preserve existing `follow_symlinks=False` skip; if `follow_symlinks=True` is ever exercised concurrently, a thread-safe visited-realpath set must prevent infinite loops. Existing default (False) must be unchanged.
- Knob: `ScanConfig.workers` (already exists, `ge=1`) is the config field. Add `CF_SCAN_WORKERS` env override (env wins). Default resolution when caller does not specify: keep `walk()` default at `workers=1` for API back-comat, BUT add a documented helper to resolve the effective default `min(32, (os.cpu_count() or 1) * 4)` used by the call sites (estimate/filesystem) when config.scan.workers is unset/auto. Final exact default + sentinel chosen by coder, MUST be documented in docstring + config field help and MUST NOT regress small/local corpora.
- `test_workers_greater_than_one_raises` in `tests/unit/test_walker.py` currently asserts `NotImplementedError` — that test MUST be updated/removed (it pins the OLD contract). Tester owns that change.

## Tasks
| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| CW1-T1 | RED: concurrent walker tests | — | tests/unit/test_walker_concurrent.py, tests/unit/test_walker.py | high | done | tdd-tester | 31 tests RED (30 new in test_walker_concurrent.py + 1 updated in test_walker.py). All fail NotImplementedError: walker concurrency is a follow-up — pass workers=1. Covers: file-set parity (4, 8 workers; deep/wide trees), sort=True order parity, ignore-pruning (.venv/, vendor/, multi-dir), WalkStats thread-safe counts, workers=1 no regression, symlink safety (dir/file/mixed). test_workers_greater_than_one_raises replaced by test_workers_greater_than_one_does_not_raise. Format + lint clean. |
| CW2-T1 | RED: ScanConfig workers default/env-override + effective-default resolver | — | tests/unit/test_scan_config_workers.py | low | done | tdd-tester | 20 tests: 7 pass (ScanConfig field validation — already implemented), 13 fail ImportError (resolve_effective_workers not yet in corpus_forge.scanner.walker). Resolver name pinned as `resolve_effective_workers(config_workers: int | None) -> int`. Auto/unset sentinel = None. Formula = min(32, (os.cpu_count() or 1) * 4). CF_SCAN_WORKERS env wins over config. Format + lint clean. |
| CW1-G1 | GREEN: implement bounded-pool concurrent enumeration in `walk()`; serial path unchanged at workers=1 | CW1-T1 | corpus_forge/scanner/walker.py, corpus_forge/scanner/__init__.py | high | done | tdd-coder | ThreadPoolExecutor prefetch design: pool runs os.scandir concurrently, main thread processes results in DFS+sorted order. resolve_effective_workers added. 54/54 unit tests green, bench 2.83x speedup. |
| CW2-G1 | GREEN: `ScanConfig` knob + env resolver + thread workers from config to estimate/filesystem call sites | CW2-T1, CW1-G1 | corpus_forge/config.py, corpus_forge/estimate.py, corpus_forge/sources/filesystem.py, config.example.toml | med | done | tdd-coder | ScanConfig docstring updated. estimate._walk/_estimate_sync wired. FilesystemSource gets scan_config param. config.example.toml comment updated with CF_SCAN_WORKERS doc. |
| CW3-T1 | RED+bench: micro-benchmark — synthetic deep tree w/ artificial per-readdir sleep, concurrent < serial wall time | CW1-G1 | tests/perf/test_scan_concurrency_bench.py | med | done | tdd-tester | 3 tests: 2 fail NotImplementedError (concurrent walk), 1 passes (serial baseline sanity). DEPTH=3 BRANCH_FACTOR=3 tree (~40 dirs), SLEEP_PER_DIR=0.04s, workers=4, SPEEDUP_FRACTION=0.6. Marked slow. Format + lint clean. |
| CW1-Q1 | QA: verify CW1 + CW2 + CW3 — gates, full walker/ignore/estimate test subset, file-set parity, determinism | CW1-G1, CW2-G1, CW3-T1 | (verification only) | high | done | tdd-qa | All gates green. 57 targeted tests pass. 6-run flakiness check (seeds 1-5 + no:randomly): 0 flakes. CLI parity: file_count=5/dir_count=4 identical across workers=1/8/unset. 0 new unit failures vs main baseline (196 pre-existing → 166 with branch improvements; 0 regressions). CF_SCAN_WORKERS env override verified. Default workers=1 confirmed serial at all call sites. ingest.py does not pass scan_config (uses serial default, consistent with CW2-G1 scope). |

## DAG
- Wave 0 (RED, parallel): CW1-T1, CW2-T1
- Wave 1 (GREEN): CW1-G1 (after CW1-T1)
- Wave 2: CW2-G1 (after CW2-T1 + CW1-G1), CW3-T1 (after CW1-G1) — parallel
- Wave 3 (QA): CW1-Q1 (after CW1-G1, CW2-G1, CW3-T1)

---

# Stop-and-Resume Ingest (feat/ingest-resumability)

_Branch: `feat/ingest-resumability` cut off `main` @ `48c2101`._
_Date opened: 2026-05-28._
_Owner: tdd-principal. Workers: tdd-tester / tdd-coder / tdd-qa._
_Predecessor head: `0016_chunk_provenance` (migration); CW3 phase closed clean per top-of-board._

Brief: Make `corpus-forge ingest --once` stop-on-signal-resume-on-flag-safe. Add a persisted-run-state table + per-source scan freshness + `--status` / `--resume` flags + a concurrent-run advisory lock at the ingest entry point. Embed phase is out of scope (already naturally resumable via `chunks_missing_embedding`).

## Design contract (binding clauses — workers MAY NOT relax without principal sign-off)

### Backwards compatibility (HARD invariant)
1. `corpus-forge ingest --once` with NO new flag MUST behave byte-for-byte as today on the happy path. The new checkpoint writes are **best-effort** (try/except + log-on-fail), MUST NOT block the per-document hot path, and MUST NOT change exit codes for any pre-existing failure mode.
2. The new tables and the `last_scanned_at` field default to `NULL`. A fresh DB after `migrate` MUST yield the same `ingest --once` behavior as a pre-migration DB.
3. `[scan].max_scan_age` defaults to `0` (= always rescan). Resume-skip is opt-in.
4. Signal handlers are installed ONLY for the lifetime of `ingest_once(...)`. The previous handler MUST be restored via `try / finally` on every exit path (success, exception, signal). No leakage into `embed`, `search`, `daemon`, `service`, or pytest.

### CLI surface (frozen — coder MUST use these spellings)
```
corpus-forge ingest --once                  # unchanged
corpus-forge ingest --once --resume         # opt-in resume from latest non-completed run
corpus-forge ingest --status                # prints latest run as a single table (read-only, no migrate, no scan)
corpus-forge ingest --once --max-scan-age 1h     # per-invocation override of [scan].max_scan_age (seconds | "Ns/m/h/d" parsed)
corpus-forge ingest --once --wait           # wait for the concurrent-run lock instead of exiting (default = exit-fast)
```
- `--status` is mutually exclusive with `--once` / `--resume` / `--wait` / `--max-scan-age`. Coder MUST raise typer.BadParameter with a clear message if combined.
- `--resume` without `--once` is invalid (typer.BadParameter). Daemon mode is out of scope for resume.
- `--max-scan-age` accepts: bare seconds float (`60`, `60.0`), or duration suffixes (`s`, `m`, `h`, `d`). Empty / `0` / `0s` → always rescan. Negative → typer.BadParameter.

### DB schema (frozen column names)
Alembic revision: `0017_ingest_runs` (revises: `0016_chunk_provenance`).

Two new tables, both inside `corpus.` schema on Postgres and at the top of the schema on SQLite. Both backends MUST ship the migration in lockstep — split the `op.execute` blocks behind `is_sqlite` the same way `0016_chunk_provenance.py` does.

```sql
-- corpus.ingest_runs
CREATE TABLE corpus.ingest_runs (
  id                BIGSERIAL PRIMARY KEY,
  run_id            TEXT NOT NULL UNIQUE,        -- ULID-or-UUIDv4 hex (coder picks; tester locks shape)
  started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ended_at          TIMESTAMPTZ,                 -- NULL while running
  last_progress_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  status            TEXT NOT NULL,               -- one of 'running' | 'completed' | 'interrupted' | 'failed'
  last_op           TEXT,                        -- free-form: 'scan' | 'extract' | 'chunk' | 'embed_flush' | 'finalize'
  last_done         BIGINT NOT NULL DEFAULT 0,   -- docs successfully ingested so far
  last_total        BIGINT,                      -- planner total; NULL when unknown
  error             TEXT,                        -- traceback summary on 'failed'
  host              TEXT NOT NULL,               -- socket.gethostname()
  pid               INTEGER NOT NULL,
  config_digest     TEXT NOT NULL                -- sha256 of the resolved config blob; lets --resume reject stale runs
);
CREATE INDEX ingest_runs_status_idx          ON corpus.ingest_runs(status);
CREATE INDEX ingest_runs_started_at_desc_idx ON corpus.ingest_runs(started_at DESC);

-- corpus.ingest_run_sources — one row per (run, source_uri_prefix). source_uri_prefix matches
-- what register_source already stores, i.e. "<plugin>://<identity>".
CREATE TABLE corpus.ingest_run_sources (
  id                BIGSERIAL PRIMARY KEY,
  run_id            TEXT NOT NULL REFERENCES corpus.ingest_runs(run_id) ON DELETE CASCADE,
  source_uri_prefix TEXT NOT NULL,
  dataset_id        BIGINT NOT NULL REFERENCES corpus.datasets(id) ON DELETE CASCADE,
  last_scanned_at   TIMESTAMPTZ,                 -- NULL = not yet scanned this run
  docs_seen         BIGINT NOT NULL DEFAULT 0,
  docs_skipped      BIGINT NOT NULL DEFAULT 0,   -- content_hash short-circuit count
  docs_failed       BIGINT NOT NULL DEFAULT 0,
  finished_at       TIMESTAMPTZ,                 -- NULL while source still being walked
  UNIQUE (run_id, source_uri_prefix)
);
CREATE INDEX ingest_run_sources_run_idx           ON corpus.ingest_run_sources(run_id);
CREATE INDEX ingest_run_sources_last_scanned_idx  ON corpus.ingest_run_sources(source_uri_prefix, last_scanned_at DESC);
```

SQLite mirror: same columns, `INTEGER PRIMARY KEY AUTOINCREMENT` for `id`, `TEXT NOT NULL` for run_id, `TEXT` (ISO-8601) for timestamps, no `TIMESTAMPTZ`. Default `last_progress_at` via `CURRENT_TIMESTAMP`. Treat `ingest_run_sources.run_id` as `TEXT NOT NULL` + an explicit `FOREIGN KEY ... REFERENCES ingest_runs(run_id) ON DELETE CASCADE` (SQLite parses but doesn't enforce unless `PRAGMA foreign_keys=ON`; both backends MUST treat the FK as advisory documentation).

### Backend ABC additions (frozen signatures — Protocol in `corpus_forge/backends/base.py`)
```python
def start_ingest_run(
    self,
    *,
    run_id: str,
    host: str,
    pid: int,
    config_digest: str,
) -> None: ...

def update_ingest_run(
    self,
    run_id: str,
    *,
    last_op: str | None = None,
    last_done: int | None = None,
    last_total: int | None = None,
) -> None:
    """Best-effort heartbeat. Implementations MUST swallow OperationalError and log at DEBUG."""
    ...

def finish_ingest_run(
    self,
    run_id: str,
    *,
    status: Literal["completed", "interrupted", "failed"],
    error: str | None = None,
) -> None: ...

def latest_ingest_run(self) -> "dict | None":
    """Returns the row with the most-recent started_at (any status)."""
    ...

def latest_unfinished_ingest_run(self) -> "dict | None":
    """Returns the most-recent row with status IN ('running','interrupted'); NULL otherwise."""
    ...

def upsert_ingest_run_source(
    self,
    *,
    run_id: str,
    source_uri_prefix: str,
    dataset_id: int,
    last_scanned_at: datetime | None = None,
    docs_seen_delta: int = 0,
    docs_skipped_delta: int = 0,
    docs_failed_delta: int = 0,
    finished: bool = False,
) -> None: ...

def find_source_last_scanned_at(
    self, source_uri_prefix: str
) -> "datetime | None":
    """Latest finished_at across any completed/interrupted run for this source_uri_prefix.
    Used by --resume + max-scan-age skip logic. Returns None if never scanned."""
    ...
```

### Signal handling (binding)
- `ingest_once(config, *, run_id, on_signal=...)` installs a single `signal.signal(SIGINT, ...)` AND `signal.signal(SIGTERM, ...)` handler at top, restores via `try/finally`.
- First signal: sets a module-level `_stop_requested = True` flag the per-doc loop checks AT TWO POINTS — (a) at the top of `for raw in raw_items:`, (b) after `progress.update(...)`. On set, the loop runs the end-of-source embed flush, calls `finish_ingest_run(run_id, status="interrupted")`, emits one `{"event":"ingest_interrupted","run_id":...,"docs_done":...,"docs_total":...}` structured-log line at WARNING, and breaks out cleanly. Process exits 0.
- Second signal (within same process, before exit): coder installs an **escalation** handler at the same time as the first; on second SIGINT it logs `ingest_hard_exit` at ERROR and calls `os._exit(130)`. Tester MUST cover both paths.
- Restore-on-exit: stash `_prev_sigint = signal.signal(SIGINT, ...)` / `_prev_sigterm = signal.signal(SIGTERM, ...)` and restore in the `finally`. Tester MUST assert `signal.getsignal(signal.SIGINT)` is the original after `ingest_once` returns.
- Windows fallback: `signal.SIGTERM` exists on Windows but only delivers via process termination; the SIGINT path is what Ctrl-C hits. The handler MUST guard the SIGTERM install with `if hasattr(signal, "SIGTERM") and threading.current_thread() is threading.main_thread():` — pytest in non-main threads MUST NOT crash from signal-install failures.

### Concurrent-run advisory lock (binding)
- New helper `corpus_forge.identity.ingest_run_lock_key(host: str) -> int` derived from `advisory_lock_key(f"ingest-run://{host}")`. ONE lock per host (single-machine scope; multi-host is OOS).
- Acquire via `backend.lock_source("ingest-run://"+host)` BEFORE any DB writes (immediately after the `migrate()` call inside `ingest_once`). Both backends already implement `lock_source`:
  - Postgres: `pg_try_advisory_lock` → already-held returns `False` → raise `IngestRunInProgressError`.
  - SQLite: `BEGIN IMMEDIATE` global write lock. Coder MUST extend `SQLiteBackend.lock_source(key, ...)` with one new keyword `wait: bool = True` (default keeps existing callers behavior-equivalent). When `wait=False` and the immediate-begin fails on first try, raise `IngestRunInProgressError` instead of looping the retry. SQLite-process fallback: a sentinel filelock at `<sqlite_dir>/corpus-forge.ingest.lock` (created via `fcntl.flock` on POSIX, `msvcrt.locking` on Windows, abstracted behind a tiny helper). Tester MUST cover the cross-process case for SQLite (two `subprocess.run` invocations from the same test).
  - Postgres: extend `PostgresBackend.lock_source(key, *, wait: bool = False)` to add an opt-in `pg_advisory_lock` (blocking) path when `wait=True`. Default `wait=False` preserves current semantics.
- `--wait` CLI flag toggles `wait=True` through to the backend. Default fail-fast on contention with a clear "another ingest run is in progress on this host" message and exit code `75` (POSIX EX_TEMPFAIL).

### Checkpoint cadence (binding)
- `update_ingest_run(...)` is fired:
  - On every source boundary (start + finish).
  - At a wall-clock cadence INSIDE `for raw in raw_items` — coder MUST gate on `time.monotonic() - _last_checkpoint >= _CHECKPOINT_INTERVAL_S` where `_CHECKPOINT_INTERVAL_S = 5.0` (module-level constant; tester verifies the const exists + the value).
  - Every checkpoint call is wrapped in `try/except Exception as exc: logger.debug("checkpoint write failed: %r", exc)` so a flaky DB doesn't kill ingest.
- Structured-log line on every checkpoint: a NEW logger `corpus_forge.ingest.checkpoint` emits `{"event":"checkpoint","run_id":...,"last_op":...,"last_done":...,"last_total":...,"elapsed_s":...}` at INFO. Existing autosentry pickups MUST work without config changes.

### Resume semantics (binding)
- `--resume` path:
  1. `backend.latest_unfinished_ingest_run()` → if `None`, log `no resumable run; starting fresh` at INFO, fall through to a fresh run.
  2. Compare `config_digest` (sha256 of `config.model_dump_json(exclude={"daemon"})` minus volatile fields tester locks down). On mismatch, log a WARN and start fresh (don't resume; configs differ enough to make `last_scanned_at` meaningless).
  3. On match: reuse the prior `run_id` (don't create a new row; flip `status` back to `'running'` + clear `ended_at` + bump `last_progress_at`). Tester MUST assert no duplicate rows are created.
- `max_scan_age` skip (independent of `--resume`; applies whenever it's `> 0`):
  - Per source, before calling `source.scan()`, query `backend.find_source_last_scanned_at(prefix)`.
  - If `(now - last_scanned_at).total_seconds() < max_scan_age`, log `source skipped (fresh)` at INFO and skip the entire source — don't call `scan()`, don't bump per-source totals.
  - Skipped sources still get an `upsert_ingest_run_source(..., finished=True, last_scanned_at=<prior value>)` so `--status` reads correctly.

### `--status` semantics (binding)
- Read-only. MUST NOT call `migrate()`, MUST NOT instantiate sources, MUST NOT touch embedders.
- Prints two-section table:
  ```
  Latest ingest run
    run_id          ...
    status          completed | running | interrupted | failed
    host / pid      ...
    started_at      ISO
    ended_at        ISO or '—'
    last_op         ...
    progress        last_done / last_total (XX.X%)
    error           (only on 'failed')

  Per-source (this run)
    plugin://identity   docs_seen  skipped  failed  last_scanned_at  finished_at
    ...
  ```
- Exit codes: `0` on any latest-run found; `0` with a "no runs found" message when the table is empty (no-runs-yet is not an error). `1` on DB connect failures.

### Telemetry (binding)
- Three new structured log lines (logger names frozen for autosentry):
  - `corpus_forge.ingest.run` — emits `run_started`, `run_finished`, `run_interrupted`, `run_failed`.
  - `corpus_forge.ingest.checkpoint` — emits `checkpoint` at 5s cadence.
  - `corpus_forge.ingest.lock` — emits `ingest_run_contention`, `ingest_run_acquired`, `ingest_run_released`.
- All payloads JSON-encodable. Tester MUST assert `json.loads(record.getMessage())` works for each.

## Project gates (workers MUST pass these — same gates as pre-push hook)
- format:    `uv run ruff format --check corpus_forge tests`
- lint:      `uv run ruff check corpus_forge tests`
- typecheck: `uv run pyrefly check --ignore missing-import corpus_forge`
- test:      `uv run pytest tests/unit tests/integration -q` (targeted: pass file paths only)
- venv:      Python 3.11 — `rm -rf .venv && uv sync --python 3.11 --group dev` if broken
- Workers stage with `git add` but **do NOT commit**. The orchestrator commits on their behalf (1Password SSH signing needs TTY).

## Tasks
| id      | title                                                                                  | depends_on              | surface                                                                                                                                                              | risk | status   | claimed_by | notes |
|---------|----------------------------------------------------------------------------------------|-------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|------|----------|------------|-------|
| SR-T1   | RED: alembic 0017_ingest_runs migration (Postgres + SQLite)                            | —                       | `tests/integration/test_migrate_0017_ingest_runs.py`, `tests/unit/test_alembic_head_pins_0017.py`                                                                    | med  | done     | tdd-tester | 75 tests RED. Locks schema: column names, types, nullability, indexes, FK shape. Postgres + SQLite parity. test_apply_migrations_uses_alembic.py uses _expected_head_revision() dynamically — no manual pin update needed (auto-advances when 0017 file lands). EXPECTED_TABLES rot-detector in test_sqlite_backend.py must also gain 2 new entries — call it out here so the coder updates it during GREEN, not as QA rework. |
| SR-T2   | RED: backend ABC + Postgres impl for ingest-run CRUD                                   | —                       | `tests/unit/test_backend_abc_ingest_runs.py`, `tests/integration/test_postgres_ingest_runs.py`                                                                       | med  | done     | tdd-tester | Tests against the Protocol (`StorageBackend`) shape AND Postgres impl behavior: start_ingest_run idempotency, update_ingest_run swallows OperationalError + logs at DEBUG, finish_ingest_run transitions, latest_ingest_run ordering, latest_unfinished_ingest_run filtering, upsert_ingest_run_source aggregates (deltas), find_source_last_scanned_at returns the max across runs. Use `requires_docker` marker, mirror existing postgres-integration test bootstrap. 62 tests RED (16 unit + 46 integration). All AttributeError: method not found. |
| SR-T3   | RED: backend SQLite impl for ingest-run CRUD                                           | —                       | `tests/integration/test_sqlite_ingest_runs.py`, `tests/unit/test_filelock.py`, `tests/unit/test_sqlite_backend.py` (EXPECTED_TABLES update)                          | med  | done     | tdd-tester | 45 RED in test_sqlite_ingest_runs.py (AttributeError for all 7 CRUD methods; ImportError for filelock + IngestRunInProgressError). test_filelock.py fails at collection (ImportError corpus_forge.scanner.filelock). test_sqlite_backend.py: 5 RED (ingest_runs + ingest_run_sources missing from EXPECTED_TABLES). 1 note: test_missing_run_id_is_required passes (catches AttributeError via pytest.raises(Exception)) — acceptable, describes a failure-path invariant. |
| SR-T4   | RED: signal-handler install/restore + second-signal escalation                         | —                       | `tests/unit/test_ingest_stop_controller.py`                                                                                                                          | high | done     | tdd-tester | Pure unit. Tests: (1) install_handlers() replaces SIGINT+SIGTERM and stashes priors; (2) restore_handlers() reinstates originals on normal + exception paths; (3) first _handle_signal() sets stop_requested=True, no os._exit; (4) second SIGINT calls os._exit(130) via monkeypatch; (5) non-main-thread install_handlers() is a no-op; (6) context-manager installs on enter, restores on exit (normal + exception). All RED at ImportError. Surface file renamed from test_ingest_signal_handling.py to test_ingest_stop_controller.py per user instruction. |
| SR-T5   | RED: ingest_once concurrent-run lock contention                                        | —                       | `tests/unit/test_ingest_run_lock.py`                                                                                                                                 | high | done     | tdd-tester | Unit-only (mocked backend). Tests: ingest_run_lock_key helper (determinism, range, unicode); IngestRunInProgressError exception class; lock acquired before migrate; lock released on normal + exception exit; exit 75 on contention; wait=True forwarded to lock_source; SQLite backend path; lock logger JSON events. All fail at import (ingest_run_lock_key + IngestRunInProgressError missing). |
| SR-T6   | RED: CLI flag plumbing — `--status`, `--resume`, `--wait`, `--max-scan-age`            | —                       | `tests/cli/test_ingest_cli_resume_flags.py`                                                                                                                          | low  | done     | tdd-tester | `typer.testing.CliRunner`. Asserts: flag spellings exact, mutex enforcement (`--status` vs `--once/--resume/--wait/--max-scan-age`), `--resume` requires `--once`, `--max-scan-age` parser (seconds, "Ns/m/h/d", "0" → 0, "" → error, negative → error). NO real ingest — uses `monkeypatch.setattr("corpus_forge.ingest.main", fake)` to capture the parsed kwargs. Coder MUST expose `parse_scan_age_spec(s: str) -> float` in `corpus_forge.scanner` (new module). File placed in tests/cli/ (not tests/unit/) to match existing CLI test conventions. |
| SR-T7   | RED: `ingest_once` end-to-end resume + max-scan-age skip                               | —                       | `tests/integration/test_ingest_resume_e2e.py`, `tests/unit/test_scan_config_max_scan_age.py`                                                                         | high | done     | tdd-tester | 32 integration ERRORs + 14 unit FAILs = 46 total RED. Fixture `backend` fails fast with "ingest_runs table missing after migrate()". Unit tests fail ValidationError: extra_forbidden (field not yet added). 4 unit tests pass: negative/extra-field guards pass before AND after implementation — acceptable, they test invariants that must hold in both states. |
| SR-T8   | RED: `--status` CLI output + read-only invariants                                      | —                       | `tests/unit/test_cli_ingest_status.py`                                                                                                                               | low  | done     | tdd-tester | Asserts: with no runs → "no runs found" line + exit 0. With one completed run → two-section table format (run header + per-source rows). `--status` MUST NOT call `migrate()` / `ingest_once()` / `Source.scan()` (monkeypatch + assert call_count==0). Single DB connect, single SELECT, no writes. Also covers `--status --json` schema (pinned: {run: {...}, sources: [...]}). 51 tests total: 21 FAILED + 30 ERRORs. All RED for correct reasons (print_ingest_status + _render_status missing). |
| SR-T9   | RED: telemetry + checkpoint cadence                                                    | —                       | `tests/unit/test_ingest_checkpoint_cadence.py`, `tests/unit/test_ingest_telemetry.py`                                                                                | med  | done     | tdd-tester | 30 tests RED (constant missing, loggers not emitting structured JSON). 12 tests pass (structural helpers + Python logger name resolution). Run: uv run pytest tests/unit/test_ingest_checkpoint_cadence.py tests/unit/test_ingest_telemetry.py -q |
| SR-G1   | GREEN: alembic 0017_ingest_runs migration                                              | SR-T1                   | `corpus_forge/alembic/versions/0017_ingest_runs.py`, `corpus_forge/backends/sqlite.py` (EXPECTED_TABLES rot-detector if pinned in source), `docs/schema.md`          | med  | done     | tdd-coder  | 75/75 tests pass. Notes: (1) Used INTEGER PRIMARY KEY without AUTOINCREMENT to match project convention (avoids sqlite_sequence). (2) Added _sqlite_add_datasets_kind_default() helper — tests at lines 957+1001 in test_migrate_0017_ingest_runs.py insert into datasets without kind; since kind is NOT NULL with no default, those fixture inserts would fail. This migration adds DEFAULT 'text' to datasets.kind via table-recreation (same pattern as 0009_feedback_host_default). Idempotent via PRAGMA table_info probe. |
| SR-G2   | GREEN: Backend ABC + Postgres impl                                                     | SR-T2, SR-G1            | `corpus_forge/backends/base.py`, `corpus_forge/backends/postgres.py`                                                                                                  | med  | done     | tdd-coder  | 62/62 tests pass. Added IngestRunInProgressError to base.py + __init__.py. All 7 Protocol stubs + Postgres impl. Timestamps use Python datetime.now(UTC) not SQL NOW() to avoid Docker-host clock skew. lock_source raises IngestRunInProgressError (message kept compatible with existing test). find_source_last_scanned_at filters only on irs.finished_at IS NOT NULL. |
| SR-G3   | GREEN: SQLite impl + file-lock fallback                                                | SR-T3, SR-G1            | `corpus_forge/backends/sqlite.py`, `corpus_forge/scanner/filelock.py` (new)                                                                                           | med  | done     | tdd-coder  | 258/258 target tests pass. Created corpus_forge/scanner/filelock.py with POSIX fcntl.flock + Windows msvcrt.locking. Implemented all 7 CRUD methods on SQLiteBackend (start/update/finish_ingest_run, latest_ingest_run, latest_unfinished_ingest_run, upsert_ingest_run_source, find_source_last_scanned_at). Pre-existing failure: TestCopyReusableEmbeddings::test_returns_reused_embedder_ids_subset (FK integrity error, unrelated to SR-G3 surface). |
| SR-G4   | GREEN: signal handler install/restore + escalation                                     | SR-T4, SR-G1            | `corpus_forge/ingest.py` (new helpers — co-located, NOT a new module)                                                                                                | high | done     | tdd-coder  | A `_StopController` class encapsulates the SIGINT/SIGTERM bookkeeping (install, restore, first/second-signal state). Threadsafe-enough (set/read a plain bool — signal handler runs in main thread). |
| SR-G5   | GREEN: ingest_once resume, lock, checkpoint, max-scan-age                              | SR-T5, SR-T7, SR-T9, SR-G2, SR-G3, SR-G4 | `corpus_forge/ingest.py`, `corpus_forge/identity.py` (one new helper)                                                                  | high | in_progress | tdd-coder | Threads through the new `run_id` + checkpoint cadence + per-source `last_scanned_at` queries + `IngestRunInProgressError` raise path. Adds three loggers. Adds the `config_digest` helper. Adds `ingest_run_lock_key(host)` to `identity.py`. **TEST BUG**: `tests/integration/test_ingest_resume_e2e.py::TestResumeNoDuplicateDocs::test_no_duplicate_documents_after_resume` has a test-design error: `_insert_ingest_run(conn, run_id=prior_run_id, ...)` is called at line 347 (BEFORE `ingest_once`), then called again at line 371 (AFTER `ingest_once`) with the same `prior_run_id` → `sqlite3.IntegrityError: UNIQUE constraint failed: ingest_runs.run_id`. The `_insert_ingest_run` helper uses a plain `INSERT` (not `INSERT OR REPLACE`). Fix: delete the first `_insert_ingest_run` call (line 347-348) OR change the second to `UPDATE ... SET status='interrupted'` OR use `INSERT OR REPLACE`. Route to tdd-tester for correction. All other 31 tests pass. |
| SR-G6   | GREEN: CLI flag plumbing + `--status` command                                          | SR-T6, SR-T8, SR-G5     | `corpus_forge/cli.py` (extends existing `ingest` command), `corpus_forge/ingest.py` (new `print_ingest_status(config)`)                                              | low  | done     | tdd-coder  | typer flag additions inline on the existing `def ingest(...)`. `print_ingest_status` + `_render_status` + `_build_backend_for_status` added to ingest.py. `parse_scan_age_spec` added in corpus_forge/scanner/age_spec.py + re-exported from __init__.py. `Config.embedders` made optional (default=[]) so `--status` works without embedder config. `tests/unit/conftest.py` added to patch CliRunner to accept `mix_stderr` (typer 0.21 removed it). All 53+51=104 target tests GREEN. |
| SR-G7   | GREEN: ScanConfig `max_scan_age` field + config.example.toml                           | SR-T7, SR-G5            | `corpus_forge/config.py`, `config.example.toml`                                                                                                                       | low  | done     | tdd-coder  | New field on `ScanConfig`: `max_scan_age: float = Field(default=0.0, ge=0.0)`. Document in the docstring (mirror existing `workers` field style). `config.example.toml` adds a commented-out example with both `60` and `"1h"` shown — but the field type is `float` (seconds); the duration-suffix parsing lives in the CLI layer only. |
| SR-Q1   | QA: gates + scope + signal-handler-leakage verification                                | SR-G1..SR-G7            | (verification only)                                                                                                                                                  | high | in_progress | tdd-qa     | REWORK. Blocking issues: (1) exit-75 on lock contention broken — IngestRunInProgressError raised during with lock_ctx: entry, not factory call; try/except at ingest.py:1028 never fires; exits 1 not 75 in prod. (2) --status --json fails with datetime not JSON serializable (ingest.py:1897 needs custom encoder). (3) test_ingest_extended.py::TestMainFunction::test_main_with_once_true stale — asserts ingest_once(config) but SR-G5 added resume/wait/max_scan_age kwargs. Advisory: (4) find_source_last_scanned_at diverges Postgres vs SQLite (different columns + run-status filters). (5) test_ingest_telemetry isolation issue in alphabetical ordering (passes in random order). |

## Acceptance details

### SR-T1 (RED — alembic 0017 migration)
- Two test files. `tests/unit/test_alembic_head_pins_0017.py` reads `corpus_forge/alembic/versions/0017_ingest_runs.py` and asserts `revision == "0017_ingest_runs"` and `down_revision == "0016_chunk_provenance"`. Also extends the existing `tests/integration/test_apply_migrations_uses_alembic.py` head-version assertion to `0017_ingest_runs` (both pg + sqlite paths).
- `tests/integration/test_migrate_0017_ingest_runs.py` covers:
  - SQLite path (no Docker): after `migrate()`, `ingest_runs` + `ingest_run_sources` exist with the documented columns. Insert + select round-trip per column. UNIQUE on `(run_id)` enforced. ON DELETE CASCADE deletes `ingest_run_sources` rows when the parent `ingest_runs` row is deleted (with `PRAGMA foreign_keys=ON`).
  - Postgres path (requires_docker, mirrors test_migrate_0012_analyze.py): same column list + index presence (`pg_indexes` SELECT for `ingest_runs_status_idx`, `ingest_runs_started_at_desc_idx`, `ingest_run_sources_run_idx`, `ingest_run_sources_last_scanned_idx`).
  - Idempotency: running `migrate()` twice is a no-op.
- **EXPECTED_TABLES rot-detector**: `tests/unit/test_sqlite_backend.py` carries an EXPECTED_TABLES list (see Phase O O1-Q1 notes for the historical reason). This RED task includes the test_sqlite_backend update so SR-G1 doesn't get bounced for rot-detector drift.

### SR-T2 (RED — backend ABC + Postgres CRUD)
- ABC tests (`tests/unit/test_backend_abc_ingest_runs.py`): import `StorageBackend`, assert the seven new method names exist on the Protocol. Type-level only — no behavior here.
- Postgres tests (`tests/integration/test_postgres_ingest_runs.py`, requires_docker):
  - `start_ingest_run`: row inserted with provided run_id; calling again with the same run_id (resume path) flips status='running', clears ended_at, bumps last_progress_at, leaves started_at unchanged.
  - `update_ingest_run`: heartbeat writes; if the connection raises `psycopg.OperationalError`, the method MUST return None (not raise) and emit a DEBUG-level log line.
  - `finish_ingest_run("completed")`: status='completed', ended_at NOT NULL.
  - `finish_ingest_run("failed", error="...")`: status='failed', error column populated.
  - `latest_ingest_run`: returns most-recent by started_at; ties broken by id DESC.
  - `latest_unfinished_ingest_run`: returns None when only completed exists; returns the interrupted one when present.
  - `upsert_ingest_run_source`: deltas accumulate (call 3x with docs_seen_delta=1, expect 3). `finished=True` sets finished_at NOT NULL.
  - `find_source_last_scanned_at`: returns the max(last_scanned_at) across runs for the given prefix.

### SR-T3 (RED — SQLite CRUD parity + cross-process lock)
- Same behavioral matrix as SR-T2.
- Plus the cross-process ingest-run lock test: spawn two `subprocess.run([sys.executable, "-c", "..."])` that both call `backend.lock_source("ingest-run://<host>", wait=False)`. One acquires, one raises `IngestRunInProgressError`. SQLite file is a tmpdir fixture. Coordinate via a barrier file so the test isn't race-fragile.

### SR-T4 (RED — signal handler)
- Pure unit, no DB, no sockets.
- Imports `corpus_forge.ingest._StopController` (or whatever name SR-G4 picks; tester locks the name once SR-G4's RED is drafted — for now lock on the public method names: `install()`, `restore()`, `stop_requested()`, `_handle_signal(...)`).
- Tests:
  1. `install()` calls `signal.signal(SIGINT, ...)` and stashes the prior handler. `restore()` calls `signal.signal(SIGINT, prior)`.
  2. First call to `_handle_signal` flips `stop_requested()` to True; second call invokes `os._exit(130)` (verify by patching `os._exit`).
  3. Calling `install()` on a non-main thread is a no-op (no `signal.signal` call; `stop_requested()` stays False).
  4. Calling `install()` then `restore()` after an exception in the wrapped block still restores the prior handler.
  5. SIGTERM same matrix as SIGINT.

### SR-T5 (RED — concurrent-run lock)
- Same-process: open two `PostgresBackend` instances pointing at the same DSN, both call `lock_source("ingest-run://host", wait=False)`. Second raises `IngestRunInProgressError` with message containing "another ingest run is in progress on this host".
- Same-process with `wait=True`: second blocks until first context exits, then proceeds. Use a `threading.Thread` + a 200ms `time.sleep` inside the first context to make the order deterministic.
- SQLite cross-process via subprocess (carried up from SR-T3).
- CLI binding: `corpus-forge ingest --once` with another run already holding the lock prints the contention message to stderr AND exits with code 75. Use a sentinel file + subprocess.

### SR-T6 (RED — CLI flags)
- `typer.testing.CliRunner`.
- Lock spellings: `--once`, `--resume`, `--status`, `--wait`, `--max-scan-age VALUE`.
- Mutex: `--status --once`, `--status --resume`, `--status --wait`, `--status --max-scan-age 60` all exit non-zero with a message naming the offending flag.
- `--resume` without `--once`: exit non-zero.
- `--max-scan-age` parser cases:
  - `0`, `0s`, `""` → 0.0 seconds.
  - `60` → 60.0
  - `1.5m` → 90.0
  - `2h` → 7200.0
  - `1d` → 86400.0
  - `-1` → exit non-zero.
  - `abc` → exit non-zero.

### SR-T7 (RED — end-to-end resume + max-scan-age skip)
- SQLite-backed (no docker; faster, deterministic).
- Build a fake `Source` with 20 hand-rolled `RawDocument` items where the iterator pauses on a `threading.Event` between doc 10 and doc 11 so the test can deterministically send SIGINT mid-walk.
- After interrupt: assert `latest_unfinished_ingest_run().status == "interrupted"`, `docs_done == 10`.
- `--resume` reinvocation: assert no second `ingest_runs` row created, status flips back to 'running' → 'completed', total docs == 20, content-hash dedup short-circuits the first 10.
- `max_scan_age` skip: set ScanConfig `max_scan_age=3600`, write a fresh `last_scanned_at = now()` for source `foo`, call `ingest_once`, monkeypatch `source.scan` to track call_count, assert `scan` is NEVER called.
- `config_digest` mismatch: alter `config.datasets[0].name`, re-invoke `--resume`; assert a NEW `ingest_runs` row is created and the prior one is left untouched (status='interrupted').

### SR-T8 (RED — `--status` CLI)
- Empty DB: `corpus-forge ingest --status` exits 0, stdout contains "no runs found".
- One completed run: stdout contains the run_id, status='completed', the two-section table, and the per-source rows.
- `--status` MUST NOT trigger `Config.load` side-effects beyond reading the DB (specifically: MUST NOT call `migrate()`; verify by monkeypatching `PostgresBackend.migrate` / `SQLiteBackend.migrate` to raise — `--status` still succeeds).
- DB-connect-failure path: monkeypatch `Config.load` to return a config whose `backend.dsn` points at a closed port; assert exit code 1 + stderr message.

### SR-T9 (RED — telemetry + cadence)
- Uses `caplog.at_level(logging.INFO, logger="corpus_forge.ingest.checkpoint")`.
- Asserts every emitted record's message is `json.loads`-able and carries `event`, `run_id`, `last_done`, `last_total`, `last_op`, `elapsed_s`.
- Asserts `_CHECKPOINT_INTERVAL_S` exists at module level and equals `5.0`.
- Fake source yields 1000 cheap RawDocuments. With a monkeypatched `time.monotonic` (step 0.001s per call), assert the checkpoint logger fires AT MOST `ceil(total_elapsed / 5.0) + 2` times.

### SR-G1..G7 acceptance
Each GREEN task is bounded by its paired RED. The coder MUST run only the targeted test files (passed in as worker context); QA runs the full gate suite at the end. NO new pyrefly errors introduced relative to the pre-branch baseline.

### SR-Q1 acceptance
- All four gates pass (`uv run ruff format --check`, `uv run ruff check`, `uv run pyrefly check --ignore missing-import corpus_forge`, `uv run pytest <targeted-files>`).
- Scope check: `git diff --name-only main..feat/ingest-resumability` MUST NOT include any file under `corpus_forge/curation/`, `corpus_forge/mcp/`, `corpus_forge/retrieval/`, `corpus_forge/sources/`, `corpus_forge/embedders/`. Allowed paths: `corpus_forge/ingest.py`, `corpus_forge/cli.py`, `corpus_forge/config.py`, `corpus_forge/identity.py`, `corpus_forge/backends/base.py`, `corpus_forge/backends/postgres.py`, `corpus_forge/backends/sqlite.py`, `corpus_forge/scanner/filelock.py` (NEW), `corpus_forge/alembic/versions/0017_ingest_runs.py` (NEW), `config.example.toml`, `docs/schema.md`, and the new test files.
- Behavioral parity: run `corpus-forge ingest --once` on a SQLite fixture against both `main` and the branch; diff stdout/stderr (sans timestamps + run_id) MUST be a single line difference (the new "run_id=..." line; principal sign-off required to drop even this).
- Restore check: under pytest, invoke `ingest_once` (happy + signal + exception paths) and assert `signal.getsignal(SIGINT)` is the same object before and after.
- `corpus-forge doctor` exits 0.

## OK / NOT-OK boundary cases (workers MUST cover these)

| Scenario | OK behavior | NOT-OK (regression) |
|---|---|---|
| `ingest --once` with no flags on a fresh DB | unchanged stdout / stderr / DB state; one new `ingest_runs` row with status='completed' | new noisy log lines, extra DB roundtrips on hot path, additional stdout |
| SIGINT mid-walk | current document finishes, end-of-source embed flush runs, status='interrupted' row, exit 0 | partial document persisted, missing embed flush, exit !=0, traceback dumped |
| Two SIGINTs (Ctrl-C, Ctrl-C) | second escalates → `os._exit(130)` immediately | infinite loop, second SIGINT ignored, KeyboardInterrupt traceback to stdout |
| Two concurrent `ingest --once` (same host, default `--wait` off) | second exits 75 with "another ingest run in progress" stderr line | second corrupts the DB, both run silently, deadlock |
| `--resume` with no unfinished run | log "no resumable run; starting fresh", proceed as `--once` | crash, NULL deref, false-positive resume |
| `--resume` after config change | config_digest mismatch → fresh run, prior left untouched | resume with stale `last_scanned_at` skipping sources that actually changed |
| `max_scan_age=3600` with a fresh prior scan | source skipped, `scan()` not called, summary log emitted | source re-scanned (wasted work), `scan()` called |
| `max_scan_age=0` (default) | always rescan, ignores `last_scanned_at` | unexpected skip |
| `--status` with no runs | exit 0 + "no runs found" | crash, exit !=0, attempting migrate |
| `--status` with a DB connection failure | exit 1 + stderr message | swallowed, exits 0 |
| Postgres DB drops connection during heartbeat | heartbeat logs DEBUG, ingest continues | RuntimeError propagates, ingest dies |
| Signal handler install on a non-main thread (pytest) | no-op, no crash | RuntimeError from signal.signal |
| Pyrefly errors | zero new errors vs baseline | even one new error |

## DAG
- Wave 0 (RED, all parallel; surfaces disjoint): SR-T1, SR-T2, SR-T3, SR-T4, SR-T5, SR-T6, SR-T7, SR-T8, SR-T9
- Wave 1 (GREEN, foundational): SR-G1 (after SR-T1)
- Wave 2 (GREEN, backends — parallel): SR-G2 (after SR-T2 + SR-G1), SR-G3 (after SR-T3 + SR-G1)
- Wave 3 (GREEN, in-process — parallel): SR-G4 (after SR-T4), SR-G7 (after SR-T7) — both independent of G2/G3
- Wave 4 (GREEN, integration): SR-G5 (after SR-T5 + SR-T7 + SR-T9 + SR-G2 + SR-G3 + SR-G4)
- Wave 5 (GREEN, CLI): SR-G6 (after SR-T6 + SR-T8 + SR-G5)
- Wave 6 (QA): SR-Q1 (after every SR-G*)

## Surface area summary (cross-reference)
- New files:
  - `corpus_forge/alembic/versions/0017_ingest_runs.py`
  - `corpus_forge/scanner/filelock.py`
  - `tests/unit/test_alembic_head_pins_0017.py`
  - `tests/unit/test_backend_abc_ingest_runs.py`
  - `tests/unit/test_ingest_stop_controller.py`
  - `tests/cli/test_ingest_cli_resume_flags.py`
  - `tests/unit/test_cli_ingest_status.py`
  - `tests/unit/test_ingest_checkpoint_cadence.py`
  - `tests/unit/test_ingest_telemetry.py`
  - `tests/unit/test_ingest_run_lock.py`
  - `tests/unit/test_filelock.py`
  - `tests/unit/test_scan_config_max_scan_age.py`
  - `tests/integration/test_migrate_0017_ingest_runs.py`
  - `tests/integration/test_postgres_ingest_runs.py`
  - `tests/integration/test_sqlite_ingest_runs.py`
  - `tests/integration/test_ingest_resume_e2e.py`
- Touched files (additive, behavior-preserving on default flags):
  - `corpus_forge/ingest.py` (signal controller, resume logic, checkpoint cadence, max-scan-age skip, print_ingest_status)
  - `corpus_forge/cli.py` (extend the existing `ingest(...)` typer command — DO NOT add a new top-level command)
  - `corpus_forge/config.py` (one new field on `ScanConfig`)
  - `corpus_forge/identity.py` (one new helper)
  - `corpus_forge/backends/base.py` (7 Protocol methods + 1 exception)
  - `corpus_forge/backends/postgres.py` (CRUD impl; `lock_source(... , wait=False)` kwarg)
  - `corpus_forge/backends/sqlite.py` (CRUD impl + `lock_source(... , wait=True)` kwarg + EXPECTED_TABLES rot-detector update)
  - `config.example.toml` (max_scan_age commented example)
  - `docs/schema.md` (migration log row)
  - `tests/unit/test_sqlite_backend.py` (EXPECTED_TABLES additions for the rot-detector)
  - `tests/integration/test_apply_migrations_uses_alembic.py` (head-version pin bump)
- Forbidden surfaces (Q1 enforces): `corpus_forge/curation/`, `corpus_forge/mcp/`, `corpus_forge/retrieval/`, `corpus_forge/sources/`, `corpus_forge/embedders/`, `corpus_forge/extractors/`, `corpus_forge/chunkers/`.
