# Architecture

## Overview

Corpus-forge is designed around three core protocols that define the extension points:
- `Source`: Defines how to ingest data from various origins
- `Embedder`: Defines how to convert text to vector embeddings
- `StorageBackend`: Defines how to persist data and embeddings

These protocols are implemented by concrete classes that handle specific sources (markdown vault, Claude Code, OpenCode), embedders (Sentence Transformers, OpenAI), and backends (PostgreSQL with pgvector).

## Core Components

### Protocols

The system is built around three Python Protocols (similar to interfaces) that define contracts:

1. **Source Protocol** (`corpus_forge/sources/base.py`)
   - Defines how to scan for and parse data sources
   - Returns `RawDocument` or `RawConversation` objects
   - Includes `watch()` method for file system monitoring

2. **Embedder Protocol** (`corpus_forge/embedders/base.py`)
   - Defines how to encode text into vector embeddings
   - Includes `warmup()` method for model initialization
   - Properties: name, provider, model_id, dimension, normalized, distance

3. **StorageBackend Protocol** (`corpus_forge/backends/base.py`)
   - Defines how to persist data to storage
   - Includes methods for migration, upserting documents/conversations
   - Handles embedding storage and retrieval
   - Provides advisory locking for concurrent access

### Base Classes

To avoid repetition, the system provides base classes that implement common functionality:

- `WatchedSource`: Handles file watching, debouncing, and identity management
- `ChunkerBase`: Implements size-bounding with overlap for text chunking
- `BaseEmbedder`: Provides common embedder functionality
- `PostgresBackend`: Implements the StorageBackend protocol for PostgreSQL

### Data Flow

1. **Ingestion**: Sources scan for files and parse them into raw objects
2. **Chunking**: Raw objects are split into chunks appropriate for embedding
3. **Storage**: Chunks are persisted to the database with deduplication via content hashes
4. **Embedding**: Embedders generate vectors for chunks and store them in embedder-specific tables
5. **Querying**: Views provide HF-Datasets-compatible exports

### Extension Points

To add a new source:
1. Implement the Source protocol (or subclass WatchedSource)
2. Override `discover()` and `parse()` methods
3. Register in configuration

To add a new embedder:
1. Implement the Embedder protocol
2. Register in configuration
3. The system will automatically create the needed database table

To add a new backend:
1. Implement the StorageBackend protocol
2. Update configuration to use the new backend

## Backends

Corpus-forge ships with two concrete storage backends behind a single `StorageBackend` protocol (`corpus_forge/backends/base.py`): `PostgresBackend` (`backends/postgres.py`) for networked deployments and `SQLiteBackend` (`backends/sqlite.py`) for single-machine use. Users pick at config time via `[backend].kind = "postgres" | "sqlite"`; `ingest.py` and `embed.py` dispatch on that value when constructing the backend.

The two implementations expose the same method surface but differ in deployment model, vector storage, locking strategy, and what they support. Sync (`sync_enabled = true` on a dataset) is the most important asymmetry: it is rejected at config-construction time by `Config.validate_sync_gate` when paired with `kind = "sqlite"`.

| Aspect | postgres | sqlite |
| --- | --- | --- |
| Deployment | Networked Postgres + `pgvector` extension | Local file (e.g. `~/Library/Application Support/corpus-forge/corpus.db`) or `:memory:` |
| Host topology | Multi-host (cross-host sync supported) | Single-host only |
| Sync (`sync_enabled`) | Supported | Rejected at config-load by `validate_sync_gate` (see B-14) |
| Setup cost | Requires PG server + `pgvector` extension | Zero — `sqlite3` is in the stdlib; `sqlite-vec` is an optional extra (`pip install corpus-forge[sqlite]`) |
| Vector store | `pgvector` column with HNSW cosine index per embedder | `sqlite-vec` `vec0` virtual table when available, BLOB fallback (no ANN search) otherwise |
| Schema isolation | Dedicated `corpus` schema; tables qualified as `corpus.<name>` | Single namespace; `schema` arg accepted for protocol parity but ignored at query time |
| Concurrency | `pg_try_advisory_lock` (cross-process, cross-host, per-key) | Per-instance `threading.Lock` + `BEGIN IMMEDIATE` on a dedicated connection with exponential back-off; `key` accepted for protocol parity but ignored |
| Best for | Production deployments, team usage, sync between machines | Personal / local use, single machine, fast bootstrap, tests |

### Choosing a backend

- Use **sqlite** if you are running on one machine and want zero infrastructure setup.
- Use **sqlite** for fast unit/integration runs and ephemeral throw-away corpora (`:memory:` is supported).
- Use **postgres** if you need cross-host sync (`sync_enabled = true` on any dataset).
- Use **postgres** if multiple processes on multiple hosts will write concurrently — its per-key advisory locks are finer-grained than SQLite's global write lock.
- Use **postgres** when you want approximate-nearest-neighbour search at scale; the SQLite path gains ANN only when `sqlite-vec` is installed, and the BLOB fallback is write-only.

## Multi-format extractor layer

Phase D introduced a new layer between the existing `Source` and `Chunker` protocols so corpus-forge can ingest arbitrary file formats without proliferating per-format `Source` classes. A `FilesystemSource` walks a heterogeneous directory tree, dispatches every file through an `ExtractorRegistry` to an `Extractor`, and emits a `RawDocument` whose `metadata.chunker_hint` selects the chunker downstream.

```
┌──────────────────┐    ┌────────────────────┐    ┌─────────────┐
│ FilesystemSource │───▶│ ExtractorRegistry  │───▶│  Extractor  │
└──────────────────┘    └────────────────────┘    └──────┬──────┘
                                                         │
                                                         ▼
                                            ┌────────────────────────┐
                                            │ ExtractedDocument      │
                                            │  text, chunker_hint,   │
                                            │  language?, metadata,  │
                                            │  labels                │
                                            └────────────┬───────────┘
                                                         │
                                                         ▼
                                              ┌─────────────────────┐
                                              │  RawDocument        │
                                              │  (source-layer)     │
                                              └──────────┬──────────┘
                                                         │
                                                         ▼
                                            ┌────────────────────────┐
                                            │  ChunkerDispatcher     │
                                            │  on metadata.          │
                                            │  chunker_hint          │
                                            └────────────┬───────────┘
                                                         │
                                                         ▼
                                                  StorageBackend
                                            (Postgres / SQLite + pgvector)
```

This makes every concrete extractor a leaf plugin — adding a new format is one new file under `corpus_forge/extractors/` plus a one-line registration in `register_default_extractors`. The embedder and storage layers are untouched.

### Extractor matrix (P0)

| Extension(s) | Extractor | Strategy | `chunker_hint` | License of backend |
|---|---|---|---|---|
| `.md` `.markdown` | `PassthroughMarkdownExtractor` | read text | `markdown` | stdlib |
| `.txt` `.log` `.rst` `.org` `.tex` `.adoc` | `PlainTextExtractor` | read text | `passthrough` | stdlib |
| `.pdf` (digital) | `PdfDigitalExtractor` | `pymupdf4llm` → markdown | `markdown` | AGPL-3.0 |
| `.html` `.htm` `.xhtml` | `HtmlExtractor` | `readability-lxml` → `markdownify` | `markdown` | LGPL-3.0 / MIT |
| `.epub` | `EpubExtractor` | `ebooklib` → `markdownify` | `markdown` | AGPL-3.0 / MIT |
| `.docx` `.pptx` `.xlsx` | `OfficeExtractor` | Docling | `markdown` | MIT |
| `.ipynb` | `NotebookExtractor` | `jupytext` py-percent + cell→markdown | `markdown` | MIT |
| `.csv` `.tsv` | `CsvExtractor` | pandas → markdown table (size-bounded) | `markdown` | BSD-3 |
| `.json` `.yaml` `.yml` `.toml` | `StructuredDataExtractor` | pretty-print fenced | `passthrough` | stdlib + MIT (`PyYAML`) |
| `.srt` `.vtt` | `SubtitleExtractor` | strip timing → flat text | `passthrough` | stdlib |
| 45+ source-code extensions (`.py .js .ts .tsx .go .rs .java .kt .rb .ex .erl .pl .hs .ml .clj .lisp .scm .sh .sql .css .lua .zig .nim .cr .r .jl .swift .dart .nix .c .h .cpp .hpp .m .mm .scala …`) | `CodeExtractor` | `tree-sitter-language-pack` → tagged regions | `code` | Apache-2.0 / MIT |

`CodeExtractor` also handles the extension-less long-tail (`Makefile`, `Dockerfile`, `.gitignore`, `.editorconfig`) via the registry's second-pass `supported_filenames` lookup. When a tree-sitter grammar is not available locally, the extractor calls `pack.download([language])` lazily on first encounter — failures fall back to `CodeChunker`'s byte-line splitter, so no document is ever dropped.

### P1 / OCR layer (deferred to Wave 5–6)

`PdfExtractor` will gain a text-density check; pages with sparse text layers escalate to a `VLMBackend` (`OllamaVLM` or `MistralOCR`). A separate `ImageExtractor` will run the same VLM against `.png` / `.jpg` / `.heic` / etc. Both paths are documented in the milestone plan and gated behind a future `[ocr]` extra.

For the wave-by-wave history of how this layer was built, see [`.planning/tdd/multi_format.md`](../.planning/tdd/multi_format.md).