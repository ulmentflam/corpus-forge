# RFC: Cheap NLP data-quality signals

status: accepted
**Owner**: nightly (open for any agent to claim)
**Priority**: P1
**Depends on**: none (lightly coupled to `rfc-corpus-growth-controls.md` — they consume the same signals)

## Context

`corpus_forge/curation/selector.py` ranks curation candidates by a
weighted stack of four signals (confidence_deficit, missing_metadata,
ranker_elevation, freshness). All four are *meta*-signals about the
schema / classifier, not about the *content* itself. As corpus-forge
grows it needs cheap content-quality signals to:

- Drive better pruning (RFC `rfc-corpus-growth-controls.md`).
- Score new ingests at scan time so low-quality chunks don't even get
  embedded.
- Surface "this content is suspicious" to the curator UI.

We already have most of the deps installed under the `[analyze]`
extra:

- `fasttext-langdetect` / `langdetect` — language detection
- `datasketch` — MinHash for near-dedup
- `scikit-learn`, `hdbscan`, `umap-learn`, `bertopic` — clustering

…but no module wires them into the data path.

## Goals

- **Language detection**: every text/conversation chunk gets a `lang`
  metadata field (ISO 639-1 code). Curator can filter by language.
- **MinHash near-dedup**: every chunk computes a MinHash signature on
  ingest; near-duplicates within a dataset get tagged with
  `dup_cluster_id` and a `dup_jaccard` to the cluster centroid.
- **Heuristic perplexity / quality estimator**: a small LLM-free
  scorer (token-rate / punctuation balance / repetition ratio) gives
  each chunk a `quality_score in [0,1]`. Cheap, no model calls.
- **Boilerplate detector**: rule-based first pass (Stack Overflow
  edit-history templates, GitHub PR boilerplate, license headers),
  with optional LLM fallback gated by env var.

All four feed `curation/selector.py` as new optional signals — they
extend the existing weighted stack, they don't replace it.

## Non-goals

- No true neural perplexity. The cheap scorer is good enough for
  pruning; if a deeper score is needed later it's a separate
  enricher.
- No translation. We detect, we don't translate.
- No semantic dedup at neural-embedding distance — that's what the
  retrieval index is for. MinHash here is for character-level
  near-duplicates that shouldn't even reach the embedder.

## Approach

### Plugin shape

`corpus_forge/enrichers/` already hosts the enricher plugin pattern
(see existing files there). Add four new enrichers:

- `corpus_forge/enrichers/lang.py` — `LangDetectEnricher`.
- `corpus_forge/enrichers/minhash.py` — `MinHashDedupEnricher`.
- `corpus_forge/enrichers/quality_heuristic.py` —
  `HeuristicQualityEnricher`.
- `corpus_forge/enrichers/boilerplate.py` — `BoilerplateEnricher`.

Each enricher receives a chunk + neighbours-from-dataset and emits a
metadata dict. They run after chunking, before embedding.

### Curation hookup

Extend `corpus_forge/curation/selector.py`'s weighted stack with:

- `lang_mismatch` — if the dataset declares an expected language and
  the chunk's `lang` differs, the candidate scores higher (= more
  curator attention).
- `dup_density` — chunks in a high-dup_cluster_id cluster score
  higher for pruning, lower for curation (since the user has
  duplicates of them already).
- `low_quality` — uses the heuristic score; below-threshold chunks
  score higher for curation/pruning.
- `boilerplate_flag` — boilerplate scores high for pruning, low for
  retrieval.

Weights configurable via `[curation.weights]` in `config.toml`.

### MinHash store

Persist MinHash sigs on the chunk row (new column `minhash BYTEA NULL`
or in metadata if column add is too heavy). Build a per-dataset LSH
index lazily; rebuild on prune.

### Boilerplate rules

Start with a hand-curated YAML at
`corpus_forge/enrichers/boilerplate_patterns.yaml` (Apache-2 headers,
"This file is part of …", GH PR templates, Markdown TOC stubs).
Pattern match first; only call an LLM if env var
`CF_BOILERPLATE_LLM=1` and a model is configured.

## Tasks

- [ ] `corpus_forge/enrichers/lang.py`: `LangDetectEnricher`
      (preferred backend `fasttext-langdetect`; fallback
      `langdetect`).
- [ ] `corpus_forge/enrichers/minhash.py`: `MinHashDedupEnricher`
      using `datasketch.MinHash` (`num_perm=128` default). Compute
      sig on ingest; LSH lookup against dataset for nearest neighbour;
      assign `dup_cluster_id` via union-find on `>=0.85` Jaccard.
- [x] `corpus_forge/enrichers/quality_heuristic.py`:
      `HeuristicQualityEnricher` — composite of token-rate
      (chars/whitespace), punctuation-balance,
      repetition-ratio (longest repeated n-gram fraction),
      shouting-ratio (uppercase fraction). Output a single
      `quality_score`. **Landed at `corpus_forge/quality/heuristic.py`**
      (new package distinct from `corpus_forge/enrichers/` — that
      directory is the Phase H code-enricher pipeline whose
      `CodeChunkEnrichment` shape doesn't match a quality-scoring
      enricher). Pure-Python, deterministic, dependency-free; weighted
      geometric mean of four signals on `[0, 1]`. 36 unit tests pinning
      the RFC's "known good > known bad" acceptance criterion + per-
      signal contracts.
- [ ] `corpus_forge/enrichers/boilerplate.py`: rule-based first
      pass; emit `is_boilerplate: bool` + `boilerplate_kind:
      str | None`. Pattern file
      `corpus_forge/enrichers/boilerplate_patterns.yaml`.
- [ ] Schema: nullable `minhash BYTEA`, `quality_score REAL`,
      `lang TEXT`, `dup_cluster_id BIGINT` (alembic revision).
- [ ] Extend `corpus_forge/curation/selector.py` with the four new
      signals; new weights in `[curation.weights]`.
- [ ] Tests:
  - [ ] `tests/unit/test_enricher_lang.py` — known-language fixtures
        in 5 languages.
  - [ ] `tests/unit/test_enricher_minhash.py` — exact-dup → Jaccard 1.0;
        no overlap → Jaccard 0; threshold clusters correct.
  - [ ] `tests/unit/test_enricher_quality.py` — known good vs known
        bad chunk orderings.
  - [ ] `tests/unit/test_enricher_boilerplate.py` — known Apache
        header detected; known prose not flagged.
  - [ ] `tests/integration/test_quality_signals_e2e.py` — ingest a
        small fixture, assert all four columns populated, assert
        curation ranking changes when weights shift.
- [ ] CHANGELOG entry.

## Verification

- After ingesting a small fixture, `SELECT lang, quality_score,
  dup_cluster_id FROM chunks WHERE dataset_id = ?` returns non-null
  values for all rows.
- A pre/post comparison on a known-noisy fixture (e.g., 100 chunks
  including 30 exact dupes + 10 boilerplate headers) shows the dupes
  cluster correctly and the boilerplate is flagged.
- `corpus-forge prune` (from the growth-controls RFC) now uses the
  new signals and produces visibly better pruning decisions on a
  curated regression fixture.

## References

- Existing enricher pattern: `corpus_forge/enrichers/` (current
  modules — read one to see the plugin shape).
- Curation scorer: `corpus_forge/curation/selector.py`.
- Optional deps: `[analyze]` extra in `pyproject.toml`.
- Schema migrations: `corpus_forge/alembic/versions/`.
- Backend insert paths:
  `corpus_forge/backends/{sqlite,postgres}.py::upsert_document`.
