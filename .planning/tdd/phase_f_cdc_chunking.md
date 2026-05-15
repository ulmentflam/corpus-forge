# Phase F — True Content-Defined Chunking (FastCDC)

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

## Status

**Phase E** complete (`cbd3bf8`). **Phase F P0** complete — see Task table below. `make ci` exit 0 at 90.53% coverage (above the 90% gate); 2886 unit + 396 integration + 15 fuzz + 30 smoke tests pass.

## Goal

Replace the positional `MarkdownChunker` / `PassthroughChunker` size-bound chunking with **content-defined chunking** (FastCDC rolling-hash boundaries) for prose classes. Mid-document edits no longer shift every downstream chunk's bytes — chunks become genuinely content-addressed and the Phase C P0 `chunks.content_hash` embedding-reuse path achieves its design potential (most chunks survive small edits).

`class=code` documents keep tree-sitter (`CodeChunker` unchanged). `class=chat` keeps `ConversationChunker`. `class=reference` (structured data) stays passthrough. Everything else (`book`, `textbook`, `paper`, `article`, `note`, `other`) routes to the new `CDCChunker`.

## Architecture

- `corpus_forge/chunkers/cdc.py::CDCChunker(min_size=256, avg_size=1024, max_size=4096)` using a FastCDC-style rolling Gear hash. Configurable polynomial + boundaries.
- `ChunkerDispatcher` upgraded to consider `RawDocument.metadata.class_hint` (NEW — populated by the post-classification pass) AND existing `chunker_hint`. Resolution order: `class_hint` → `chunker_hint` → source-level fallback chunker.
- Class → chunker mapping:
  - `code` → `CodeChunker`
  - `chat` → `ConversationChunker`
  - `reference` → `PassthroughChunker`
  - `book` / `textbook` / `paper` / `article` / `note` / `other` → `CDCChunker`
- New `corpus-forge rechunk` CLI command: walks documents with stored `class=*` labels and re-runs the chunker pass (replacing existing chunks atomically via `upsert_document` — Phase C BUG-3 path preserves embeddings where content_hash matches).
- New dep: `fastcdc>=1.6` (Apache-2.0). Add to `[multi-format]` extra.

## Local-or-remote constraint

Not applicable — FastCDC is a pure-Python algorithm, no model. The principle remains for future model integrations.

## Task table

| id | title | depends_on | surface | risk | status |
|----|-------|------------|---------|------|--------|
| F-01 | `CDCChunker` (FastCDC rolling hash) | — | `corpus_forge/chunkers/cdc.py` (new), `tests/unit/test_cdc_chunker.py` | med | done |
| F-02 | `ChunkerDispatcher` class-aware routing | F-01 | `corpus_forge/ingest.py` (extend dispatcher), `tests/unit/test_chunker_dispatch.py` (extend) | low | done |
| F-03 | `class_hint` propagation at rechunk time | F-02 | `corpus_forge/cli.py` (rechunk reads `class=*` labels, threads via `for_class`) | low | done |
| F-04 | `corpus-forge rechunk` CLI command | F-02 | `corpus_forge/cli.py` (additive), `corpus_forge/backends/{postgres,sqlite,base}.py` (`replace_document_chunks`, `get_document_chunk_texts`, `get_document_chunk_metadatas`), `tests/unit/test_cli_rechunk.py`, `tests/integration/test_rechunk_e2e.py` | med | done |
| F-05 | `pyproject.toml` `fastcdc` in `[multi-format]` extra | F-01 | `pyproject.toml`, `uv.lock` | low | done |
| F-06 | Stability invariant tests | F-01..F-05 | `tests/unit/test_cdc_stability.py` (append stability, mid-edit reuse-floor — hypothesis-driven) | med | done |
| F-07 | **P0 gate** — `make ci` green | F-06 | — | gate | done |

## Definition of Done

- [x] `CDCChunker` produces deterministic chunks for given seed + boundaries
- [x] Mid-document insertion of N bytes preserves ≥ 1 chunk fingerprint (hypothesis-pinned reuse-floor; on real prose the actual reuse is much higher — concrete smoke test asserts ≤ 4 fingerprints differ on the realistic-prose fixture)
- [x] Append at end of document leaves the prefix chunks byte-identical (only the last original chunk may change)
- [x] `ChunkerDispatcher` correctly routes `class=book/textbook/paper/article/note/other` to CDC; `class=code` stays on CodeChunker; `class=chat` stays on ConversationChunker; `class=reference` stays on PassthroughChunker
- [x] `corpus-forge rechunk` is idempotent — text-equality + chunker-signature-presence dual check (small docs where positional and CDC happen to produce the same chunks no longer false-skip)
- [x] Phase C chunk-reuse path still works (re-chunk with same content → embeddings reused via `replace_document_chunks`)
- [x] `make ci` exit 0 at ≥ 90% coverage (actual: 90.53%)
- [ ] Integration test against fixture corpus shows ≥ 70% chunk reuse after a small append edit — P2 follow-up (the wave's E2E test verifies the rechunk plumbing + idempotency; the % chunk-reuse metric is a separate landing test)

## Out of scope (P2 / later)

- Per-class boundary tuning (CDC params from config) — fixed defaults for now
- Adaptive avg_size based on doc length
- Cross-document deduplication via CDC fingerprint match

## Reuse (do not reinvent)

- `Chunker` base + `TextChunk` (`corpus_forge/chunkers/base.py`)
- `ChunkerDispatcher` from Phase D HK-1 (`corpus_forge/ingest.py`)
- `upsert_document` BUG-3 chunk-reuse path (`corpus_forge/backends/postgres.py`, `corpus_forge/backends/sqlite.py`)
- `apply_label` + `iter_documents_for_classification` from Phase E
