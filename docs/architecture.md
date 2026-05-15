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

### Vision / OCR pipeline (P1)

Phase D / P1 adds a vision-language-model (VLM) plug-in surface so PDFs with sparse text layers and standalone images are first-class corpus citizens. The pieces sit behind the optional `[ocr]` extra (`requests`, `pdf2image`, `pillow`) and are wired in lazily — installs that do not configure a backend keep the P0 digital-only behaviour unchanged.

#### VLM protocol

Every backend implements the same flat `Protocol` in `corpus_forge/vlm/base.py`:

```python
class VLMBackend(Protocol):
    name: str
    def describe_image(self, image: bytes, *, prompt: str | None = None) -> str: ...
    def extract_page(self, image: bytes, *, page_number: int) -> str: ...
    def warmup(self) -> None: ...
```

`describe_image` is the entry point for the image extractor (transcribe + describe). `extract_page` biases toward verbatim Markdown reproduction of a single PDF page raster. `warmup` is a cheap health check called once per process — it raises `VLMUnavailableError` early when the backend cannot serve traffic, so misconfiguration fails at boot rather than mid-ingest.

#### Backend matrix

| Backend | Module | Endpoint | Default model | Notes |
|---|---|---|---|---|
| Ollama | `corpus_forge.vlm.ollama.OllamaVLM` | `POST /api/generate` on `http://localhost:11434` | `qwen2.5vl:7b` (Apache-2.0, ~5 GB) | Local, default. `warmup()` GETs `/api/tags` and asserts the configured tag is installed. |
| Mistral OCR | `corpus_forge.vlm.mistral.MistralOCR` | `POST /v1/ocr` on `https://api.mistral.ai/v1` | `mistral-ocr-2503` | Remote fallback. Read `MISTRAL_API_KEY` from `secrets.env`. `warmup()` is a no-op (no free health endpoint); the constructor validates that `api_key` is non-empty. |
| Noop | `corpus_forge.vlm.base.NoopVLM` | n/a | n/a | Selected when `config.vlm.backend = "none"` (the default). Every operational method raises `VLMUnavailableError`. The PDF extractor treats a `NoopVLM` like `vlm=None` and short-circuits Tier 2. |

The active backend is resolved by `corpus_forge.vlm.registry.get_active_vlm(config)` using importlib-driven lazy imports, so installing `[ocr]` is only required when `vlm.backend` is set to a non-`"none"` value.

#### PDF Tier 1 → Tier 2 escalation

`PdfDigitalExtractor` is the single PDF entry point and handles both tiers:

1. **Tier 1 — `pymupdf4llm.helpers.pymupdf_rag.to_markdown`.** Reads the embedded text layer and emits Markdown. We deliberately bypass the top-level `pymupdf4llm.to_markdown` because it auto-routes to a Tesseract fallback in 1.27+, which would collide with our VLM-driven escalation.
2. **Sparseness check.** If `len(tier1_text) / page_count < ocr_min_chars_per_page` (default 100), `metadata.sparse_text_layer = True` and the extractor decides whether to escalate.
3. **Tier 2 — VLM OCR.** Only fires when `ocr_enabled=True`, a real VLM is wired in, and the text layer is sparse. The PDF is rasterised page-by-page via `pdf2image.convert_from_path` (poppler under the hood) at `ocr_dpi` (default 200), each PNG is sent to `vlm.extract_page(...)`, and the responses are joined with `\n\n---\n\n` so page boundaries survive. `metadata.tier = "ocr_escalated"`, `pages_ocr_count`, `ocr_backend`, and `ocr_model` are stamped onto the document, and `("ocr", backend.name)` / `("ocr_model", model_tag)` labels are added.

#### Image extractor

`corpus_forge/extractors/image.py::ImageExtractor` is a thin shim: `vlm.describe_image(file_bytes, prompt=...)` for `.png .jpg .jpeg .tif .tiff .bmp .webp .heic`. The default prompt biases toward verbatim transcription with description as a tiebreaker. `chunker_hint = "markdown"`. Registry registration in `register_default_extractors` is gated on a real (non-Noop) VLM plus `ocr_enabled` and `enable_image` — installs that didn't configure a VLM never see this extractor.

#### Failure ladder

Robust by construction — failures degrade, they do not poison the ingest:

- `NoopVLM` short-circuit — escalation never fires, no error logged.
- `VLMUnavailableError` / `VLMResponseError` (daemon down, 5xx, malformed JSON) — graceful Tier 1 fallback. `metadata.ocr_escalation_attempted = True`, `metadata.ocr_escalation_failed_reason = <exc>` records the reason. No `ocr` label is added.
- `VLMTimeoutError` on a single page — that page is replaced with a `<!-- VLM timeout on page N -->` placeholder; the remaining pages continue. Escalation metadata still reports the page count.
- `pdf2image.exceptions.PDFInfoNotInstalledError` (no poppler) — ERROR log + Tier 1 fallback with `ocr_escalation_failed_reason = "poppler-not-installed"`.
- Missing `[ocr]` extra at runtime — `_resolve_pdf2image()` returns `None`, ERROR log + Tier 1 fallback.

#### Marker convention

Live OCR tests carry the `requires_ollama` or `requires_mistral_api` pytest markers (registered in `pyproject.toml`). `tests/integration/conftest.py` auto-skips them at collection time when the dependency is absent — the Ollama path probes `GET /api/tags`, the Mistral path checks `MISTRAL_API_KEY`. `make test-ocr` runs both suites; `make test-ocr-local` is the common-case Ollama-only variant.

For the wave-by-wave history of how this layer was built, see [`.planning/tdd/multi_format.md`](../.planning/tdd/multi_format.md).