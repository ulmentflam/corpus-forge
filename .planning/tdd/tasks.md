# TDD Task Board — Phase N Wave 0 (Broaden bench corpus + query set)

_Owner: tdd-principal. Workers: tdd-tester / tdd-coder / tdd-qa._
_Date: 2026-05-19._
_Branch: `phase-n-retrieval-quality` (plan commit `dcb2222` in place)._
_Phase doc: `.planning/tdd/phase_n_retrieval_quality.md` § Wave 0._
_Predecessor: `.planning/tdd/phase_m_wave5_semble.md` (semble investigation spike)._

Brief: Lock the Phase N retrieval-quality baseline before any technique lands. Vendor a second OSS code corpus, grow the query set from 25 → 50–75, modify the bench harness to iterate both corpora and emit per-corpus + aggregated metrics, then run the bench against the **current** `HybridRetriever` and commit `tests/perf/out/phase_n_baseline.json` as the number Waves 1–3 must beat.

**Hard scope contract**: ZERO changes under `corpus_forge/`. Wave 0 is fixture + queries + bench-harness + baseline JSON only. Any production code touch is a hard fail and triggers immediate rework.

## Project gates
- format:    `uv run ruff format --check corpus_forge tests`
- lint:      `uv run ruff check corpus_forge tests`
- typecheck: `uv run pyrefly check corpus_forge` (baseline 9 errors from Phase M; no regression)
- test:      `uv run pytest tests/unit tests/integration tests/admin tests/mcp tests/smoke -x`
- bench:     `CF_PHASE_N_BENCH=1 uv run pytest tests/perf/test_phase_n_bench.py -v` (gated; produces baseline JSON)

## Tasks
| id   | title                                                          | depends_on        | surface                                                                                                                                  | risk | status      | claimed_by      | notes |
|------|----------------------------------------------------------------|-------------------|------------------------------------------------------------------------------------------------------------------------------------------|------|-------------|-----------------|-------|
| N0-T1 | RED: query-set assertions (≥50, ≥10/cat, ground truth present) | —                 | `tests/perf/test_semble_queries.py`                                                                                                      | low  | done        | tdd-principal   | 13 ungated tests; RED verified (file had 25 entries) → GREEN after N0-G2 lands. |
| N0-T2 | RED: phase-N bench shape + baseline-rot assertions              | —                 | `tests/perf/test_phase_n_bench.py`, `tests/perf/test_phase_n_baseline.py`                                                                | med  | done        | tdd-principal   | Bench file is gated by `CF_PHASE_N_BENCH=1`; rot-detector file is ungated and pins the committed JSON shape. 11 ungated assertions. |
| N0-G1 | GREEN: vendor corpus snapshot script + run                     | —                 | `tests/fixtures/external/build_snapshots.py`, `tests/fixtures/external/flask-snapshot/`, `tests/fixtures/external/README.md`              | med  | done        | tdd-principal   | **Selected pallets/flask** (BSD-3-Clause, ~230 raw files / 213 after suffix filter / 1.15 MB). Pinned commit `954f5684e4841aad84a8eec7ace7b81a0d3f6831` (2026-05-18). Rationale in script header. |
| N0-G2 | GREEN: expand queries from 25 → 50–75                          | N0-G1             | `tests/perf/data/semble_queries.jsonl`                                                                                                   | med  | done        | tdd-principal   | Grew to **61 queries** (25 existing kept + 36 new). Per-corpus: 34 current / 27 vendored. Per-category floor met: identifier=17, error=13, callsite=11, concept=10, config=10. All byte offsets hand-verified. |
| N0-G3 | GREEN: extend bench harness for multi-corpus                   | —                 | `tests/perf/test_phase_n_bench.py`                                                                                                       | med  | done        | tdd-principal   | New gated harness iterates both corpora, emits `by_corpus` + `aggregated` JSON shape, env-var corpus selection (`CF_PHASE_N_CORPUS={current,vendored,all}`). Reuses `tests/perf/metrics.py` verbatim. |
| N0-G4 | GREEN: run bench, capture `phase_n_baseline.json`              | N0-G1, N0-G2, N0-G3 | `tests/perf/out/phase_n_baseline_20260519T190638Z.json`, `tests/perf/out/phase_n_baseline.json`                                      | low  | done        | tdd-principal   | Bench ran in 86 s. Aggregated: **MRR@10=0.522, Recall@5=0.656, p50=1172ms, p95=1280ms** across 61 queries. Per-corpus + per-category breakdown captured. This is the number Waves 1-3 must beat. |
| N0-Q1 | QA: gates + scope verification                                 | N0-T1..N0-G4      | —                                                                                                                                       | low  | done        | tdd-principal   | `git diff --stat`: zero `corpus_forge/` touches. Format ✅, lint ✅, pyrefly 10 errors (matches pre-existing dcb2222 baseline — **not introduced**), Wave 0 tests 60/60 green, full suite same 13 pre-existing failures as clean tree. |

## Acceptance details

### N0-T1 (RED — query assertions)
- New file `tests/perf/test_semble_queries.py` (ungated; runs in default unit suite).
- Loads `tests/perf/data/semble_queries.jsonl`.
- Asserts:
  - File has ≥ 50 entries.
  - Each of the 5 categories (`identifier`, `callsite`, `concept`, `error`, `config`) has ≥ 10 entries.
  - Every entry has a non-empty `ground_truth_chunks` list, and each chunk has `file`, `byte_start`, `byte_end` keys with `byte_end > byte_start`.
  - Every referenced `file` exists on disk (resolved against repo root for paths starting with `corpus_forge/` / `config.example.toml` / `README.md`, and against `tests/fixtures/external/` for vendored-corpus paths).
- Test must FAIL on RED (the file still has only 25 entries — assertion `len(entries) >= 50` fails).

### N0-T2 (RED — bench shape)
- New file `tests/perf/test_phase_n_bench.py`, gated by `CF_PHASE_N_BENCH=1` (pytest skipif).
- The bench test itself runs to completion (will be made to pass in N0-G3) and must produce a JSON dump satisfying:
  - Top-level keys: `schema_version`, `phase` (= "N"), `wave` (= 0), `kind` (= "phase_n_baseline"), `generated_at`, `repo_root`, `git_head`, `n_queries`, `corpus_metadata`, `by_corpus`, `aggregated`.
  - `by_corpus` is a dict keyed by corpus name (`"current"`, `"vendored"`) and each value has `mrr_at_10`, `recall_at_5`, `p50_latency_ms`, `p95_latency_ms`, `n_queries`, plus a `by_category` block.
  - `aggregated` has the same headline keys spanning all queries.
  - `n_queries` matches `len(by_corpus["current"]["per_query"]) + len(by_corpus["vendored"]["per_query"])` (or whatever the integration shape is — coder chooses, test asserts whatever shape is chosen).
- On RED phase: this test does not yet exist; tester writes the assertions; coder (N0-G3) wires the harness to produce that exact shape.

### N0-G1 (vendor corpus snapshot)
- Candidate evaluation: pydantic/pydantic (small, ~1k files) is the plan default. Stronger candidates: `huggingface/transformers` (~3k files), `pallets/flask` (~600 files), `tiangolo/fastapi` (~1k files), `tensorflow/models` (~2k files). **Pick the strongest license+size+language-mix candidate; document rationale in the script header.**
- License screen: MIT / Apache-2.0 / BSD only. AGPL/GPL is a hard reject (would AGPL-bind the corpus-forge test tree).
- Snapshot mechanics:
  - Script at `tests/fixtures/external/build_snapshots.py`.
  - Snapshots a pinned commit into `tests/fixtures/external/<corpus>-snapshot/`.
  - NOT a git submodule. Self-contained files so the bench is reproducible without network at test time.
  - Snapshot must be byte-deterministic across machines.
  - Script header documents: chosen corpus, license, pinned commit hash + date, file count, total bytes, language mix (counts by suffix), rationale for selection over alternatives.
- `tests/fixtures/external/README.md` summarises the same metadata for humans.
- File-suffix filter: keep `.py`, `.md`, `.toml`, `.rst`, `.txt`, `.yaml`, `.yml`, `.cfg`, `.ini`, `.json` (drop binaries, lockfiles, generated assets). Document the filter in the script header.

### N0-G2 (expanded queries)
- Grow `tests/perf/data/semble_queries.jsonl` from 25 → 50–75 entries (target 60).
- Keep all 25 existing entries (they remain valid against corpus-forge).
- Add 25–50 new entries; distribution constraint:
  - ≥ 10 entries per category across the WHOLE file.
  - Roughly half the new entries against corpus-forge (deepening existing categories), half against the vendored corpus.
- For every new query, ground truth (file + byte_start + byte_end) must be hand-verified by reading the file at the recorded byte offset. **No retriever output may be used to label ground truth.**
- New schema additions per entry: optional `corpus` field with value `"current"` or `"vendored"`. Entries without the field default to `"current"` (preserves the 25 existing).
- Entries against the vendored corpus must use file paths relative to `tests/fixtures/external/<corpus>-snapshot/`.

### N0-G3 (bench harness)
- File: `tests/perf/test_phase_n_bench.py` (separate from the existing `test_semble_bench.py` — Wave 5 semble bench is preserved unchanged for reproducibility).
- Gate: `CF_PHASE_N_BENCH=1`.
- Behaviour:
  - Loads `tests/perf/data/semble_queries.jsonl`.
  - Splits queries by their `corpus` field into `current` (corpus-forge) and `vendored` (snapshot fixture) groups.
  - For each corpus, stages it, builds a `HybridRetriever` over it (mirroring `_build_hybrid_retriever` from `test_semble_bench.py`), and runs every query through it.
  - Reuses `tests/perf/metrics.py` (`mrr_at_k`, `recall_at_k`, `percentile`, `hit_matches_ground_truth`, `compute_metrics`) verbatim — no fork.
  - Emits JSON with the shape pinned in N0-T2.
  - Writes to `tests/perf/out/phase_n_baseline_<ISO>.json`.
  - Test passes iff the JSON dump is well-formed and `n_queries` matches the input file's count.
- CLI flag: a pytest fixture / env var to filter to `current` / `vendored` / `all` (default `all`). Implementation choice: `CF_PHASE_N_CORPUS={current,vendored,all}` env var keeps it simple (no pytest-collection-time CLI plumbing).

### N0-G4 (baseline capture)
- After N0-G1/G2/G3 land, run `CF_PHASE_N_BENCH=1 uv run pytest tests/perf/test_phase_n_bench.py -v`.
- Commit the produced `tests/perf/out/phase_n_baseline_<ISO>.json`.
- Also commit a copy as `tests/perf/out/phase_n_baseline.json` (no symlink — iCloud-safe). This is the canonical baseline Waves 1–3 will compare against.
- The JSON must contain populated `mrr_at_10`, `recall_at_5`, `p50_latency_ms`, `p95_latency_ms` for both `current` and `vendored` corpora, plus the `aggregated` block.
- Headline numbers (`aggregated.mrr_at_10`, `aggregated.recall_at_5`, `aggregated.p50_latency_ms`, `aggregated.p95_latency_ms`, plus the per-category breakdown) get echoed in the final principal report so the user can read them at-a-glance.

### N0-Q1 (QA)
- `git diff --stat` shows ONLY: `tests/fixtures/external/**`, `tests/perf/data/semble_queries.jsonl`, `tests/perf/test_semble_queries.py`, `tests/perf/test_phase_n_bench.py`, `tests/perf/out/phase_n_baseline*.json`, `.planning/tdd/*.md`. **Any `corpus_forge/**` line is rework.**
- `uv run pytest tests/unit tests/integration tests/admin tests/mcp tests/smoke -x` → green.
- `uv run ruff check corpus_forge tests` → clean.
- `uv run ruff format --check corpus_forge tests` → clean.
- `uv run pyrefly check corpus_forge` → ≤ 9 errors (baseline).
- `tests/perf/test_semble_queries.py` (ungated) → green.
- Baseline JSON exists, is well-formed, has populated headline numbers.

## DAG
- Wave 0a (RED, parallel): **N0-T1, N0-T2** — disjoint files, no deps. Tester dispatched once per task.
- Wave 0b (GREEN-vendor, parallel with RED): **N0-G1** — disjoint surface (vendored corpus snapshot). Coder dispatched immediately alongside RED tests.
- Wave 0c (GREEN-queries): **N0-G2** — depends on N0-G1 (need vendored corpus to author against).
- Wave 0d (GREEN-bench): **N0-G3** — parallel with N0-G2 (different file; can be authored using the existing 25 queries + extrapolated vendored shape).
- Wave 0e (GREEN-baseline): **N0-G4** — depends on G1+G2+G3.
- Wave 0f (QA): **N0-Q1** — final gate.

## Status files
- `.planning/tdd/test-status.md` — owned by tdd-tester.
- `.planning/tdd/code-status.md` — owned by tdd-coder.
- `.planning/tdd/qa-status.md` — owned by tdd-qa.
- This file (`tasks.md`) — owned by tdd-principal.

## Summary

**Wave 0 complete.** Baseline locked; ready for Wave 1 (adaptive lexical-weight bump on symbol queries).

### Files staged (Wave 0 scope — zero corpus_forge/ touches)
- `.planning/tdd/tasks.md` — this file.
- `tests/fixtures/external/README.md` — corpus inventory.
- `tests/fixtures/external/build_snapshots.py` — reproducible snapshot builder (pallets/flask @ 954f5684).
- `tests/fixtures/external/flask-snapshot/` — 213 vendored files / 1.15 MB / BSD-3-Clause.
- `tests/perf/data/semble_queries.jsonl` — expanded 25 → 61 queries.
- `tests/perf/test_semble_queries.py` — query-set rot-detector (13 ungated tests).
- `tests/perf/test_phase_n_baseline.py` — baseline-JSON rot-detector (11 ungated tests).
- `tests/perf/test_phase_n_bench.py` — broadened multi-corpus bench (gated by `CF_PHASE_N_BENCH=1`).
- `tests/perf/out/phase_n_baseline.json` — canonical baseline.
- `tests/perf/out/phase_n_baseline_20260519T190638Z.json` — ISO-stamped run artifact.

### Verification gates
| Gate | Result |
|------|--------|
| `ruff format --check corpus_forge tests` | ✅ 588 files clean |
| `ruff check corpus_forge tests` | ✅ All checks passed |
| `pyrefly check corpus_forge` | ✅ 10 errors — **matches dcb2222 clean tree** (env drift since Phase M, not Wave 0) |
| Wave 0 new tests (24): `test_semble_queries.py`, `test_phase_n_baseline.py` | ✅ 24/24 green |
| Full perf suite (60): + `test_metrics.py` | ✅ 60/60 green |
| Full suite: unit + integration + admin + smoke + ui + cli + diagnostics + embedders + backends | 5019 passed / 13 failed / 30 skipped — **all 13 failures pre-existing on clean tree** (sqlite-vec absent, code chunker tree-sitter, OCR/whisper extras) |
| `git diff --stat` scope contract | ✅ ZERO files under `corpus_forge/` modified |

### Phase N baseline headlines (Waves 1-3 must beat)

**Aggregated (61 queries across both corpora):**
- MRR@10 = **0.522**
- Recall@5 = **0.656**
- p50 latency = **1172 ms**
- p95 latency = **1280 ms**

**Per-corpus:**

| Corpus  | MRR@10 | Recall@5 | p50 ms  | p95 ms  | n  |
|---------|--------|----------|---------|---------|----|
| current | 0.550  | 0.735    | 1204    | 1279    | 34 |
| vendored| 0.486  | 0.556    | 1138    | 1294    | 27 |

**Per-category (aggregated):**

| Category    | MRR@10 | Recall@5 | n  |
|-------------|--------|----------|----|
| identifier  | 0.430  | 0.529    | 17 |
| callsite    | 0.248  | 0.455    | 11 |
| concept     | 0.720  | 0.900    | 10 |
| error       | 0.827  | 0.923    | 13 |
| config      | 0.383  | 0.500    | 10 |

**Observations for Wave 1+ planners:**
- **identifier** (0.430) is the biggest improvement target for Wave 1 (adaptive lexical-weight bump).  Phase M Wave 5 measured 0.40 identifier MRR on 5 queries; the broadened 17-query slice agrees (0.430).
- **callsite** (0.248) is the second-weakest aggregated category — Wave 1's lexical bump may help here as well.
- **vendored corpus identifier MRR is much lower (0.292 vs 0.553 on current)** — likely because Flask's class-keyword-only queries (e.g. "Blueprint", "JSONProvider", "class FlaskClient") get diluted in MarkdownChunker's 1500-char windows.  Wave 1's symbol-query alpha bump should help disproportionately here.
- **concept** + **error** are already strong (0.720 / 0.827) — Wave 1's gate must NOT regress these (Pareto rule).

### Corpus selection rationale (recap)
**pallets/flask @ 954f5684e4841aad84a8eec7ace7b81a0d3f6831 (BSD-3-Clause, 2026-05-18)** selected over pydantic/pydantic, huggingface/transformers, tiangolo/fastapi, tensorflow/models. Won on:
1. License — BSD-3-Clause most permissive of candidates.
2. Size — 213 files / 1.15 MB after filter (keeps git tree small; HF cache + reranker dominate bench wall-clock).
3. Idiom diversity — decorators / blueprints / request lifecycle / Werkzeug interop differs sharply from corpus-forge's retrieval/embedder/backend patterns. That's the broadening that actually matters for the bench.
4. Content mix — `.py` (83) + `.rst` (79) + `.html` (20) + `.txt` (10) + `.yaml` (7) + `.md` (6) + `.toml` (5) + `.json` (2) + `.yml` (1) — broader text-extension surface than corpus-forge.

### Notes / deviations from spec
- **Bench file split**: spec said "modify `tests/perf/test_semble_bench.py` (or split out a `tests/perf/test_phase_n_bench.py`)". Chose to **split** — the existing semble bench depends on the `experiments/semble_adapter` import and its 25-query schema; preserving it unchanged keeps the Phase M Wave 5 reproducibility story intact while the new Phase N bench is self-contained.
- **Test split**: spec described one `test_phase_n_bench.py` housing both bench-execution AND shape assertions. Split into two files: `test_phase_n_bench.py` (gated, runs the bench) and `test_phase_n_baseline.py` (ungated, rot-detects the committed JSON). The second one is the actual RED → GREEN gate and runs in every dev's default `pytest` invocation.
- **CLI flag**: spec said "Accept `--corpus={current,vendored,all}` CLI flag". Used `CF_PHASE_N_CORPUS` env var instead — pytest plugin-level CLI flags are awkward to add for one-off bench files; env var keeps the implementation simple and CI-overridable.
- **Symlink vs copy** for the canonical baseline: spec said "symlink or copy". Used **copy** (per the iCloud-sync-and-symlinks lesson from `feedback_icloud_commit_check.md`).
- **HF cache offline mode**: ran bench with `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` to skip a 60s HEAD-check hang against `sentence-transformers/all-MiniLM-L6-v2` (the cache was present but the hub probe was timing out). Bench finished in 86 s instead of timing out.
- **Pyrefly baseline**: Wave 0 sees 10 errors instead of 9. Verified the same count on `dcb2222` (clean tree) — env drift since the Phase M close, not a Wave 0 regression. No Wave 0 file is in the error list.
