# Phase N — Retrieval Quality (semble technique extraction)

**Motivation:** Phase M Wave 5's semble investigation spike (`.planning/tdd/phase_m_wave5_semble.md`) measured MinishLab/semble against corpus-forge's `HybridRetriever` and recommended **extract techniques**, not swap. Three concrete porting candidates were named:

1. Adaptive lexical-weight bump on symbol-shaped queries
2. Definition boosts (re-rank `class Foo:` chunks above mere references)
3. Static-embedding fast tier via `model2vec` + `potion-code-16M`

The Wave 5 numbers (production config, rerank on, this repo, 25 queries):

| metric | semble | HybridRetriever |
|---|---|---|
| MRR@10 | 0.374 | **0.460** |
| Recall@5 | 0.480 | **0.640** |
| p50 latency | **1.37 ms** | 1,210 ms |
| p95 latency | **9.16 ms** | 1,270 ms |

Category breakdown (MRR@10): semble crushes identifier searches (0.85 vs 0.40), HybridRetriever wins concept (0.75 vs 0.42), error (0.55 vs 0.30), callsite (0.38 vs 0.20). Both struggle on config-block searches.

**Target release:** `0.1.0b6`.

**Status:** planning → execution. Workflow: tdd-principal owns it; orchestrator (this session) commits on workers' behalf.

## Decisions locked with the user

- One phase document, four waves (Wave 0 + the three techniques).
- **Bench broadens first.** Before any technique lands, vendor a second OSS code corpus and grow the query set to 50–75. Insurance against overfitting to corpus-forge-shaped code.
- **Pareto wave gate.** Each wave must improve the targeted category's MRR@10 AND must not regress any other category beyond a threshold. Forces a config-gated A/B knob so a regressing technique can be disabled.

## Wave overview

| Wave | Scope | Critical files |
|---|---|---|
| 0 | Broaden bench corpus + query set; capture baseline | `tests/perf/data/semble_queries.jsonl`, new fixture vendoring, `tests/perf/test_semble_bench.py` |
| 1 | Adaptive lexical-weight bump on symbol queries | modify `corpus_forge/retrieval/retriever.py`, `corpus_forge/config.py` |
| 2 | Definition boosts on retrieval | modify `corpus_forge/chunkers/code.py`, `corpus_forge/retrieval/retriever.py` |
| 3 | Static-tier fast path (model2vec) | new `corpus_forge/embedders/model2vec.py`; modify `corpus_forge/retrieval/{retriever.py,types.py}`, `corpus_forge/config.py` |

Each wave: RED → GREEN → wave gate (orchestrator stages + commits per `feedback_tdd_worker_commits.md`).

## Verified facts from Phase N exploration

**Wave 1 surface:**
- `HybridRetriever.search` lives at `corpus_forge/retrieval/retriever.py:111–220`. Fusion at lines 166–176 (RRF default; alpha-blend with min-max normalization at 174–176).
- `RetrievalConfig` at `corpus_forge/config.py:258–280`. `alpha: float = 0.5`, `fusion: Literal["rrf","alpha"] = "rrf"`.
- **No existing query-shape detection** — `is_symbol_shaped()` must be new.
- Reranker (at `corpus_forge/retrieval/rerank/base.py`) sees fused Hit objects but **discards upstream fused scores and emits its own**, so a fusion-stage lexical bump survives downstream rerank.
- Existing tests at `tests/unit/test_retrieval_retriever.py` pin RRF default, alpha monotonicity, reranker dispatch — additive Wave 1 changes must add new tests, not modify these.

**Wave 2 surface — important correction to the Wave 5 doc:**
- The code chunker (`corpus_forge/chunkers/code.py`) does **NOT** currently emit `is_definition`. The Wave 5 doc was optimistic.
- However: every chunk the AST-walk emits **IS** a definition by construction (the walker only captures structural items — `Function`, `Class`, `Method`, `Block`). Metadata today: `kind`, `name`, `language`, `byte_range`.
- Phase H enrichment adds nested `metadata.enrichment` but no definition tagging.
- `Hit.metadata` flows through retrieval end-to-end at zero overhead (`corpus_forge/backends/sqlite.py:~70–120` + `retriever.py:188–204`). **No new backend column needed.**

**Wave 3 surface:**
- Embedder protocol at `corpus_forge/embedders/base.py:9–26` — sync, batched, `np.ndarray` return.
- `model2vec.StaticModel.from_pretrained("minishlab/potion-code-16M")` is plug-compatible with the existing `SentenceTransformer.encode()` shape. potion-code-16M: 256-dim, MIT, ~16 MB, code-focused.
- Backend already provisions one table + HNSW index per embedder (`backends/postgres.py:344–358`, `backends/sqlite.py:539–661`) — adding a fast-tier embedder needs no schema migration.
- Fingerprint drift detection (`embedders/fingerprint.py:155–195`) skips brand-new embedders (no row in DB), so adding the fast tier doesn't trip drift on the main embedder.
- Architectural precedent: `SearchOptions.rerank: bool = False` + `self.reranker` wired at HybridRetriever construction — the fast tier should mirror this opt-in pattern.

---

## Wave 0 — Broaden bench corpus + query set

### Goal

Lock the baseline before any technique lands. The current bench is 25 queries against this repo only — too narrow to confidently judge "does Wave 1 improve identifier MRR by X%" without overfitting risk.

### Red

- `tests/perf/test_semble_queries.py` — assert the queries file has ≥50 entries, ≥10 per category, every entry has at least one ground-truth chunk by byte offset.
- `tests/perf/test_semble_bench.py` (modify) — assert the bench iterates BOTH corpora (current repo + vendored) and emits per-corpus + aggregated metrics in the JSON output.

### Green

- Vendor a second corpus under `tests/fixtures/external/pydantic-snapshot/` (or similar — pick a Python OSS repo with 10k+ files, MIT-or-similar license, pinned commit hash). Snapshot script under `tests/fixtures/external/build_snapshots.py`.
- Grow `tests/perf/data/semble_queries.jsonl` from 25 → 50–75 entries. Distribution: ≥10 identifier, ≥10 concept, ≥10 callsite, ≥10 error, ≥10 config-block. Half against this repo, half against the vendored corpus.
- Modify `tests/perf/test_semble_bench.py` to:
  - Accept `--corpus={current,vendored,all}` flag (default `all`).
  - Emit `tests/perf/out/semble_bench_<ISO>.json` with `by_corpus` + `aggregated` sections.
- Run Wave 5's bench against the broadened set. Commit the resulting JSON as the **Phase N baseline** under `tests/perf/out/phase_n_baseline.json`. This is the number each subsequent wave must beat.

### Risk / open question

- License screening on the vendored corpus is the riskiest step. If the chosen repo isn't MIT/Apache/BSD, fall back to another candidate.
- 50–75 hand-authored queries is ~4–8 hours of work. Quality matters more than quantity — bad ground truth poisons every downstream wave.

---

## Wave 1 — Adaptive lexical-weight bump on symbol queries

### Red

- `tests/unit/test_retrieval_symbol_detection.py` (new) — `is_symbol_shaped(query)` truth table:
  - `"HybridRetriever.search"` → True (dotted accessor)
  - `"Foo::bar"` → True (C++ scope operator)
  - `"_private_helper"` → True (leading underscore + snake_case)
  - `"how does the watch debounce work"` → False (natural language)
  - `"managed block sentinels not found"` → False (error string)
  - Empty / whitespace → False
  - Edge cases: single tokens like `"setup"` (False — common word), `"setUp"` (True — camelCase)
- `tests/unit/test_retrieval_adaptive_alpha.py` (new) — with `adaptive_lexical_weight=True`, a symbol-shaped query lowers the effective alpha to `symbol_query_alpha` (default 0.3); a non-symbol query keeps `alpha`. Verified by intercepting the `alpha_blend` call.
- `tests/unit/test_config_retrieval.py` (additions) — `RetrievalConfig.adaptive_lexical_weight: bool = False`, `symbol_query_alpha: float = 0.3` (validated to [0,1]). Defaults preserve current behavior.
- `tests/perf/test_phase_n_wave1_gate.py` (new) — RUN-GATED via `CF_PHASE_N_GATE=1`. Loads `tests/perf/out/phase_n_baseline.json` and the current bench output; asserts:
  - Identifier-category MRR@10 ≥ baseline + 0.10 (or another threshold the doc locks)
  - No other category's MRR@10 < baseline − 0.05
  - No other category's Recall@5 < baseline − 0.05

### Green

- Add `is_symbol_shaped(query: str) -> bool` to `corpus_forge/retrieval/query_shape.py` (new module). Heuristic:
  - Contains `.`, `::`, `->`, or `/` (member/scope/path accessors) — symbol-shaped
  - Matches CamelCase / snake_case identifier shape on the whole query (no whitespace) — symbol-shaped
  - Leading `_` + identifier chars only — symbol-shaped
  - Else — natural language
- Add to `RetrievalConfig` (`corpus_forge/config.py:258`):
  - `adaptive_lexical_weight: bool = False`
  - `symbol_query_alpha: float = Field(default=0.3, ge=0.0, le=1.0)`
- Modify `HybridRetriever.search` (`corpus_forge/retrieval/retriever.py:111`) — before fusion (line ~166):
  ```python
  effective_alpha = options.alpha
  if self.config.adaptive_lexical_weight and is_symbol_shaped(query):
      effective_alpha = self.config.symbol_query_alpha
  ```
  Pass `effective_alpha` to `alpha_blend`. Skip the bump on RRF path (rank-only, no scores).
- Update `RetrievalConfig` docs section in `config.example.toml`.

### Gate

- Wave 0 baseline already captured.
- Run `CF_PHASE_N_GATE=1 uv run pytest tests/perf/test_phase_n_wave1_gate.py -v` on the broadened bench. Pareto rule must pass.
- If the gate fails: tune the heuristic OR `symbol_query_alpha`; do not relax the gate.

### Risk / open question

- The heuristic may misclassify "natural language query containing one symbol" (e.g., `"how does HybridRetriever.search dispatch fusion"`). Conservative bias: only fire when the WHOLE query looks symbol-shaped (no whitespace). Document the false-negative path.
- `symbol_query_alpha = 0.3` is a starting guess. The wave gate's threshold is the real arbiter.

---

## Wave 2 — Definition boosts on retrieval

### Red

- `tests/unit/test_code_chunker.py` (additions) — every chunk emitted by the AST walk has `metadata["is_definition"] == True` and `metadata["definition_kind"]` ∈ {`"Function"`, `"Class"`, `"Method"`, `"Block"`}. Byte-line fallback chunks have neither key.
- `tests/unit/test_retrieval_definition_boost.py` (new) — given hits where:
  - chunk A is a definition with `metadata.name == "directory_pruned"`
  - chunk B is a reference (no `is_definition` tag) mentioning `directory_pruned`
  - query is `"directory_pruned"`
  
  With `RetrievalConfig.definition_boost_enabled=True`, A's score is multiplied by `definition_boost_factor` (default 1.5) BEFORE the top-k truncation, pushing A above B. Verified by intercepting the boost step.
- `tests/unit/test_config_retrieval.py` (additions) — `definition_boost_enabled: bool = False`, `definition_boost_factor: float = Field(default=1.5, ge=1.0, le=5.0)`.
- `tests/perf/test_phase_n_wave2_gate.py` (new) — same Pareto rule as Wave 1, applied to the post-Wave-1 baseline (so the gain on identifier queries must be on top of Wave 1's bump).

### Green

- Modify `corpus_forge/chunkers/code.py` — when emitting a `_StructItem`-derived chunk (lines 215–222 single, 228–234 coalesced), add:
  ```python
  md["is_definition"] = True
  md["definition_kind"] = item.kind  # Function / Class / Method / Block
  ```
- Modify `HybridRetriever.search` to apply the boost AFTER fusion, BEFORE top-k truncation:
  ```python
  if self.config.definition_boost_enabled:
      query_tokens = _tokenize_for_boost(query)
      for hit in fused_hits:
          md = hit.metadata or {}
          if md.get("is_definition") and (md.get("name") or "").lower() in query_tokens:
              hit.score *= self.config.definition_boost_factor
  ```
  Internal helper `_tokenize_for_boost(query) -> set[str]` lower-cases + splits on non-identifier chars; small + private (don't ship as a public API).
- Add to `RetrievalConfig`: `definition_boost_enabled: bool = False`, `definition_boost_factor: float = 1.5`.

### Wave-2 dependency on Wave 1

The boost composes with Wave 1's lexical bump: a symbol-shaped query gets BOTH a lower alpha (more lexical contribution) AND a definition-chunk multiplier. The Wave 2 gate test must run with Wave 1 ON (default OFF in config, ON in the test fixture) so we measure the marginal contribution.

### Risk / open question

- Reranker may flatten the boost — the cross-encoder emits its own scores and discards upstream. Mitigation: apply the boost POST-rerank as well, on the reranked hits, with a smaller factor (e.g. 1.2). Decide during RED based on bench numbers.
- Backfilling `is_definition` on existing chunks requires a re-ingest. Document this clearly: the boost only fires on freshly chunked corpora until re-ingest sweeps everything. Acceptable for an opt-in flag.

---

## Wave 3 — Static-embedding fast tier (model2vec + potion-code-16M)

### Red

- `tests/unit/test_embedder_model2vec.py` (new) — `Model2VecEmbedder` conforms to the `Embedder` protocol: `encode`, `encode_query`, `warmup`, `name`, `provider="model2vec"`, `dimension=256`, `normalized=True`, `distance="cosine"`. Uses a tiny test model from disk; no network in unit tests.
- `tests/unit/test_embedder_registry.py` (additions) — `EmbedderProvider` enum gains `"model2vec"`; `build_embedder({"provider": "model2vec", "model_id": "minishlab/potion-code-16M", ...})` constructs the right class.
- `tests/unit/test_retrieval_fast_tier.py` (new):
  - `SearchOptions.fast_tier_mode = "only"` → only the fast embedder is queried; no lexical, no rerank.
  - `fast_tier_mode = "shortcut"` → fast embedder runs first, its top-N seed the candidate set for lexical fusion + reranker. Verified by spying on backend calls.
  - `fast_tier_mode = "skip"` (default) → current behavior; fast embedder is never called even when wired.
  - Missing fast embedder at construction: `fast_tier_mode != "skip"` raises a clear error.
- `tests/unit/test_config_retrieval.py` (additions) — `RetrievalConfig.fast_tier_embedder_name: str | None = None`; validated to match an entry in `Config.embedders` at load time.
- `tests/integration/test_fast_tier_index.py` — second backend table is created when the fast embedder is registered; both indexes coexist with the main embedder's index.
- `tests/perf/test_phase_n_wave3_gate.py` — RUN-GATED. Fast-tier-only mode: p50 latency ≤ 10 ms on identifier queries; quality Pareto preserved (MRR@10 not worse than baseline − 0.05 on any category).

### Green

- New `corpus_forge/embedders/model2vec.py` — class `Model2VecEmbedder(BaseEmbedder)`. Lazy-load `model2vec.StaticModel.from_pretrained(self.model_id)` on first encode. `encode_query == encode` (symmetric — that's the model's promise). `warmup()` loads + does a single dummy encode.
- Register `"model2vec"` in `corpus_forge/embedders/registry.py:18–42`. Add to the `EmbedderProvider` literal type in `corpus_forge/config.py`.
- Modify `SearchOptions` (`corpus_forge/retrieval/types.py:58–71`) — add `fast_tier_mode: Literal["skip","shortcut","only"] = "skip"`.
- Modify `HybridRetriever.__init__` to accept an optional `fast_embedder: Embedder | None = None`.
- Modify `HybridRetriever.search` — branch on `options.fast_tier_mode` BEFORE the current dense+lexical fan-out:
  - `"only"` → query the fast embedder, run dense search on its index, skip lexical + rerank, return top-k.
  - `"shortcut"` → query fast embedder, take its top-`fast_tier_top_n` (configurable; default 200) as the candidate set; pass `chunk_id IN (...)` to dense+lexical backends so the main embedder's expensive cross-encoder runs only on those candidates.
  - `"skip"` → current behavior.
- Add deps to `pyproject.toml`: `model2vec>=0.5` as a NEW optional extra `[fast-tier]`. Don't bloat the core install — opt-in like `[ocr]` / `[whisper]`.

### Wave-3 dependency on backend

The "shortcut" mode requires `backend.search_dense(..., chunk_ids=frozenset[int])` filtering — verify whether this is already supported. If not, that's a small backend addition (one extra WHERE clause in the SQL). Surface during RED.

### Gate

- Run `CF_PHASE_N_GATE=1 uv run pytest tests/perf/test_phase_n_wave3_gate.py -v` against the broadened bench. Latency target (p50 ≤ 10 ms for "only" mode) plus Pareto on quality.

### Risk / open question

- 256-dim vs typical 1024-dim main embedder: dimension mismatch is **safe** (each embedder gets its own table). RRF fusion is rank-only and handles different scales fine; alpha-blend already normalizes per-list.
- First-run downloads ~16 MB from HF — gate behind a setup-time download or a `corpus-forge fast-tier prefetch` subcommand?
- "shortcut" mode's correctness depends on the fast tier's recall: if it misses a chunk that the main embedder would have surfaced, that chunk is unreachable via shortcut. Set `fast_tier_top_n` conservatively high (default 200) so recall stays > 0.95 in practice. Bench's Recall@5 metric captures this regression directly.

---

## Reuse map

- `corpus_forge.retrieval.retriever.HybridRetriever` — Wave 1 + 2 + 3 all extend this class without subclassing.
- `corpus_forge.retrieval.types.SearchOptions` — Wave 3 adds the new flag; Wave 1 + 2 ride on existing `alpha`.
- `corpus_forge.embedders.base.Embedder` protocol — Wave 3 adds a new provider that conforms.
- `corpus_forge.embedders.fingerprint` — already handles new embedders without false-positive drift.
- `corpus_forge.backends.{postgres,sqlite}.search_dense` — already keyed on `embedder_id`, so two embedders = two queries with no protocol changes (assuming `chunk_ids` filter for shortcut mode is feasible; otherwise small addition).
- `tests/perf/metrics.py` — MRR@10 / Recall@5 / p50 / p95 helpers from Wave 5; the wave gates depend on them.

## TDD wave gate sequencing

Per the project convention (`feedback_workflow_tdd.md`):

1. Wave 0 RED → GREEN → orchestrator commits.
2. Wave 1 RED (with Wave 0 baseline JSON in place) → GREEN → wave gate (`CF_PHASE_N_GATE=1` bench pass).
3. Wave 2 RED → GREEN → wave gate (run with Wave 1 ON; measure marginal lift).
4. Wave 3 RED → GREEN → wave gate (latency + quality Pareto).

## Verification (whole phase)

- `uv run pytest tests/unit tests/integration tests/admin tests/mcp tests/smoke -x` green throughout.
- `CF_PHASE_N_GATE=1 uv run pytest tests/perf -m slow` — every wave's gate file passes.
- `tests/perf/out/phase_n_<wave>_<ISO>.json` — committed per-wave. Decision doc cross-references the numbers.
- `uv run corpus-forge search "<symbol>" --k 10 --json` — sanity-check end-to-end on a configured dataset; verify the new config flags actually affect the live result ordering.
- Phase doc closing block lists final per-category MRR@10 / Recall@5 / p50 / p95 vs baseline, plus the config defaults at release.

## Release shape

- `0.1.0b6` — three new config flags default OFF; user opts in. Documentation in `config.example.toml` shows how to enable + tune. CHANGELOG describes the rationale (carry-over from Phase M Wave 5 semble decision doc) and the wave-gate numbers.
- Follow-up phase candidate: ON-by-default flip once a broader user dogfood confirms no surprises in production data shapes.
