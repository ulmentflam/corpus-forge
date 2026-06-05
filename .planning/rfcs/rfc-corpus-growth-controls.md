# RFC: Corpus-growth controls — decay, importance sampling, budget gates

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P1
**Depends on**: none

## Context

The user's hard constraint: **no exponential data growth**. Today
corpus-forge has:

- **Hash dedup at embed time**: `corpus_forge/backends/postgres.py:1223`
  reuses embeddings when a chunk's `(content_hash, embedder_id)`
  matches an existing row. Good, but only stops duplicate *embeddings*;
  duplicate *chunks* still get inserted.
- **Freshness decay in curation scoring**:
  `corpus_forge/curation/selector.py` linearly decays a freshness
  signal to 0 over 180 days. Used to rank curation candidates; not
  used for pruning.
- **No row deletion path**: every successful ingest is permanent
  unless the user manually drops a dataset.
- **No per-source budget**: a chatty source (e.g., a HF dataset import)
  can swamp the corpus.
- **No pre-ingest preview**: `corpus_forge/estimate.py` predicts
  Postgres footprint at sync time but doesn't gate the run.

Combined with the new sources from PR #29 (`gemini_cli`, `codex_cli`,
`chatgpt_export`, `jsonl_chat`, prompt-history) and the inbound HF
plugin proposed in `rfc-hf-dataset-inbound-source.md`, corpus growth
will be the limiting factor on usable retrieval quality.

## Goals

- A `corpus-forge prune` admin verb that scores every chunk on a
  pluggable rubric (curation signals × age × duplicate-rate × low
  feedback) and **deletes** the bottom N% (default 10%, configurable),
  emitting a summary of what went.
- `DatasetSourceConfig.max_rows` (and `max_bytes`) caps that the
  scanner enforces — when a source would push the dataset over its
  cap, the lowest-scoring existing rows from that source get evicted
  to make room (LRU + score).
- `corpus-forge estimate sync` pre-flight that names the cost of an
  upcoming ingest (rows added, embeddings to compute, disk delta) and
  exits non-zero if the projected delta exceeds a configurable
  ceiling.
- All three operations are **dry-run-default**: `--apply` must be
  passed to actually delete or block ingest. Safe by default.

## Non-goals

- No automatic TTL deletion. Pruning happens on user-invoked verbs and
  on cap-eviction during scan; no background reaper.
- No cross-dataset eviction. Each dataset is bounded by its own caps.
- No re-embedding decisions; an existing row that survives pruning
  keeps its embedding. Re-embed is a separate concern (separate RFC if
  pursued later).

## Approach

### `corpus-forge prune` admin verb

New file `corpus_forge/admin/prune.py`. Reuse
`corpus_forge/curation/selector.py`'s weighted-signal evaluator
(`confidence_deficit`, `missing_metadata`, `ranker_elevation`,
`freshness`) and add two new signals tuned for pruning:

- **`duplicate_density`**: 1.0 minus the MinHash-Jaccard distance to
  the chunk's nearest neighbour in the same dataset. Near-duplicates
  score low (= prune candidates). Depends on the MinHash work in
  `rfc-nlp-data-quality-signals.md`; without that, this signal is
  skipped and the rubric continues with the four existing ones.
- **`feedback_drag`**: chunks with `recent_feedback.kind == "rejected"`
  or `rating < 0` score low.

Combine signals via a configurable weights dict
(`prune.weights = {confidence_deficit=0.2, ...}`); compute a final
score per chunk; sort ascending; drop the bottom `--percentile`
(default 10).

CLI shape:

```
corpus-forge prune --dataset <name> [--percentile 10] [--apply]
                   [--dry-run-json out.json]
```

Default is dry-run + summary table. `--apply` performs the deletion
inside a single transaction and emits a `proposals/prune-<timestamp>.md`
under `.nightly/runs/...` so the run is auditable.

### Per-source caps

Extend `DatasetSourceConfig` (in `corpus_forge/config.py`) with two
optional fields:

- `max_rows: int | None = None`
- `max_bytes: int | None = None`

`ingest.ingest_once` checks the per-source row count after each
`upsert_document` / `upsert_conversation`. If the projected count
would exceed the cap, evict the lowest-scoring rows from this source
*before* inserting (same scoring rubric as `prune`).

### `estimate sync` pre-flight

Extend `corpus_forge/estimate.py` with a `predict_sync_delta(config,
source_config)` that returns
`{rows_added: int, bytes_added: int, embeddings_to_compute: int}` by
walking the source's `discover()` output without parsing. CLI:

```
corpus-forge estimate sync [--dataset <name>] [--apply-cap <bytes>]
```

If the predicted delta exceeds `--apply-cap` (or
`growth.sync_cap_bytes` in config), exit non-zero with a clear
message.

### Config

Add a `[growth]` block to `config.toml`:

```toml
[growth]
prune_percentile_default = 10
sync_cap_bytes = "10G"          # human-readable allowed
per_source_cap_default_rows = 100000
```

Validate via Pydantic `GrowthConfig` in `corpus_forge/config.py`.

## Tasks

- [x] `corpus_forge/admin/prune.py`: new module with `prune_dataset(...)`. — PR #50
- [x] Extend `corpus_forge/curation/selector.py` with
      `score_for_pruning(chunk, ...)` that exposes the same weighted
      stack with prune-tuned weights. — PR #51
- [x] `corpus_forge/cli.py`: register `corpus-forge prune` verb. — PR #52
- [x] Extend `DatasetSourceConfig` with `max_rows` / `max_bytes`. — PR #42 (already open from prior Nightly run)
- [x] `corpus_forge/ingest.py::ingest_once`: enforce caps after each
      insert, evict as needed. — PR #53 (per-source-once enforcement; see PR body for trade-off)
- [x] Extend `corpus_forge/estimate.py` with `predict_sync_delta`. — local proposal (branch `nightly/predict-sync-delta-180526Z`, commit `06393d3`); push blocked on 11 pre-existing test failures (see task 0002 uncertainty.md)
- [x] `corpus-forge estimate sync` CLI verb. — local proposal (branch `nightly/estimate-sync-cli-183310Z`, commit `22bf688`); push blocked on pre-push hook (see task 0003 uncertainty.md)
- [x] New `GrowthConfig` block in `corpus_forge/config.py`. — PR #37 (already open from prior Nightly run)
- [x] Tests:
  - [x] `tests/unit/test_prune_scorer.py` — score ordering invariants. — PR #50
  - [x] `tests/unit/test_source_caps.py` (renamed `test_dataset_source_caps.py`) — `max_rows` triggers eviction. — PR #42 (storage layer; eviction-runtime tests in `test_source_caps_enforcement.py`, PR #53)
  - [ ] `tests/integration/test_prune_e2e.py` — round-trip a dataset
        through prune; row count drops by the expected percentile;
        retained rows are the top-scoring ones. (Deferred: needs PR #50 / #52 merged for a real e2e round-trip)
  - [x] `tests/unit/test_estimate_sync.py` — predicted vs actual delta within a documented tolerance. — covered by `test_cli_estimate_sync.py` (19 tests, local proposal in task 0003) and `test_predict_sync_delta.py` (9 tests, local proposal in task 0002)
- [x] CHANGELOG entry. — bullets landed in every PR (#50, #51, #52, #53) and the two local proposals.

## Verification

- `corpus-forge prune --dataset claude-code --dry-run` prints a table
  of the bottom-10% chunks with their scores and reasons; nothing is
  deleted.
- `corpus-forge prune --dataset claude-code --apply` deletes those
  rows; row count drops by ~10%; subsequent `--dry-run` shows a new
  bottom-10% from the remaining set.
- `corpus-forge estimate sync` against a fresh source predicts a row
  count and disk delta within 10% of what `corpus-forge ingest --once`
  actually adds.
- Configuring `max_rows = 100` on a source then ingesting a 500-row
  fixture results in exactly 100 rows surviving, all with the highest
  scores.

## References

- Curation scorer: `corpus_forge/curation/selector.py`.
- Existing embedding reuse: `corpus_forge/backends/postgres.py:1223`.
- Estimate path: `corpus_forge/estimate.py`.
- Ignore lifecycle (analogous CRUD verb pattern):
  `corpus_forge/admin/ignore.py`.
- Source config: `corpus_forge/config.py::DatasetSourceConfig`.
- Echo cache TTL precedent (TTL-aware structures already exist):
  `corpus_forge/sync/echo.py`.
