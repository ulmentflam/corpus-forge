# forge_self — bundled gold set provenance

This document records exactly how `forge_self.jsonl` was built so the
gold set can be regenerated when the source corpus moves, the chunker
changes, or chunk ids drift.

## Source corpus

- **Repo**: corpus-forge at commit `dcd07d9` (Phase R3 mid-flight).
- **Files ingested**: all markdown under the repo root, filtered by
  `vectorize_repo_sqlite.py`'s `DEFAULT_EXCLUDES` (`.git/**`, `.venv/**`,
  `__pycache__/**`, `node_modules/**`, `.pytest_cache/**`, `.ruff_cache/**`,
  `htmlcov/**`, `dist/**`, `build/**`, `*.egg-info/**`).
- **Documents**: 16
- **Chunks**: 499

## Chunker config

- Plugin: `markdown_vault` → `markdown` chunker
- `max_chars = 1500`
- `overlap = 200`

Chunk ids are not stable across re-ingests if the chunker config or
source files change.  The bundled gold set therefore records each
relevant chunk's `content_hash` alongside its id — the runner's
content-hash fallback (R3-05) tolerates id drift transparently.

## Embedder

- `sentence-transformers/all-MiniLM-L6-v2`
- 384-dimensional, normalised, cosine distance
- `device = "cpu"`, `batch_size = 32`
- Logical name: `minilm`

## Curation method

Hand-curated query list of 25 prompts spanning the corpus breadth
(architecture, schema, sync, sqlite backend, retrieval, eval, daemon,
embedders, chunkers, licensing).  For each query:

1. Tokenised the prompt to alnum runs (the FTS5 MATCH parser is
   fiddly about punctuation and column-name collisions; the build
   script joins tokens with `OR`).
2. Ran `HybridRetriever.search(query, SearchOptions(k=3, fusion="rrf"))`
   against the seeded corpus.
3. Captured the top-3 chunk ids as `relevant_chunk_ids`.
4. Captured each chunk's `content_hash` alongside, parallel to the ids.

The script that built the set lives at `/tmp/build_forge_self_gold.py`
(disposable — re-create from the snippet below when needed).

## How to rebuild

```bash
# 1. Seed the test corpus (idempotent; wipes /tmp/corpus-forge-test.db
#    unless --keep-existing).
uv run python scripts/vectorize_repo_sqlite.py

# 2. Run the gold-set builder (see /tmp/build_forge_self_gold.py for the
#    canonical snippet; ~50 LoC).  This regenerates
#    corpus_forge/eval/datasets/forge_self.jsonl with fresh chunk ids
#    and content_hashes pinned to the current corpus shape.
uv run python /tmp/build_forge_self_gold.py

# 3. Sanity-check the result:
uv run corpus-forge eval retrieval --dataset forge_self --k 10,20
```

## Notes on coverage

The auto-built gold set is biased toward the retriever it was built
WITH (HybridRetriever + RRF + minilm).  This means the bundled baseline
is a **non-regression floor**, not an absolute quality target — its
purpose is to fail loudly when a retrieval-quality regression sneaks in,
not to encode an absolute ground truth.

When the team wants a stronger gold set:
- Replace the auto-curated `relevant_chunk_ids` with hand-picked ids
  after inspecting the chunk text for each query.
- Add a `graded` block per row to upgrade from binary to graded NDCG.
- Expand to ≥50 queries with hand-checked ground truth.

The R3-05 content-hash fallback survives all of the above so long as
each gold row also carries its `content_hashes`.
