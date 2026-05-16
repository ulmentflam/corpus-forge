# Architecture

This is the "how the system works today" reference. For the historical
phase-by-phase build log, see `.planning/tdd/phase_*.md`.

## Overview

Corpus-forge is a compose-everything pipeline of small plug-in protocols. Each
seam can be implemented anew without touching the rest:

```
Source ─▶ Extractor ─▶ Chunker ─▶ StorageBackend ─▶ per-embedder tables
                                       │
classify (post-ingest) ◀───────────────┤
rechunk (post-classify) ◀──────────────┤
enrich   (post-classify, code only) ◀──┤
            VLM / Whisper feed Extractor at ingest time
```

| Protocol | Module | Purpose | Phase |
|---|---|---|---|
| `Source` | `sources/base.py` | Discover files / sessions / conversations and parse into `RawDocument` / `RawConversation`. | A |
| `Extractor` | `extractors/base.py` | Convert a single file off disk to `ExtractedDocument(text, chunker_hint, metadata, labels)`. | D |
| `Chunker` | `chunkers/base.py` | Split a document or conversation into `TextChunk`s. | A (+D/F additions) |
| `Embedder` | `embedders/base.py` | `Sequence[str] -> np.ndarray`. Symmetric `encode` + asymmetric `encode_query`. | A |
| `MultiModalEmbedder` | `embedders/multimodal.py` | Text **or** image bytes → shared vector space. | G P1 |
| `StorageBackend` | `backends/base.py` | Persist documents, chunks, embeddings, labels, conversations; ANN + FTS search; cross-host sync. | A (+ many) |
| `Classifier` | `classifiers/base.py` | `ClassifiableDocument` → `ClassLabel` (9-value taxonomy). | E |
| `VLMBackend` | `vlm/base.py` | Image → text (OCR + description). | D P1 |
| `WhisperBackend` | `whisper/base.py` | Audio / video → text. | G P0 |
| `CodeEnricher` | `enrichers/base.py` | Code chunk → `{docstring, summary, symbols, model, confidence}`. | H |

### Cross-cutting principle — local-or-remote URL

Every model client in corpus-forge accepts a configurable HTTP URL. The
default is local (`http://localhost:11434` for Ollama-shape clients,
`https://api.openai.com/v1` for OpenAI-shape ones); the same backend code
talks to a hosted endpoint by changing one config field. Audit checklist:

| Surface | Config field | Default | Wire shape |
|---|---|---|---|
| VLM (PDF Tier-2 OCR + image extractor) | `vlm.ollama_url` or `vlm.mistral_base_url` | local Ollama | `/api/generate` or Mistral `/v1/ocr` |
| Classifier LLM | `classifier.llm_url` | local Ollama | `/api/generate` |
| Whisper remote | `whisper.remote_base_url` | OpenAI | `/audio/transcriptions` multipart |
| Multi-modal embedder remote | constructor arg | n/a (off by default) | OpenAI-compat `/v1/embeddings` with base64 data-URL images |
| Code enricher remote | `code_enricher.remote_url` + `code_enricher.remote_api_shape` | local Ollama | Ollama `/api/generate` **or** OpenAI `/chat/completions` |

The local default keeps ingest self-contained; pointing at a remote URL is a
one-line config change with no code edit. The principle is enforced in tests
under `tests/unit/test_*_url_*.py`.

### Base classes (shared machinery)

- `WatchedSource` — file watching, debouncing, identity tracking, content-hash
  short-circuit on unchanged files.
- `ChunkerBase` — size-bounding with overlap and forward-progress invariant.
  Concrete chunkers extend it (`MarkdownChunker`, `ConversationChunker`,
  `PassthroughChunker`, `CodeChunker`, `CDCChunker`).
- `BaseEmbedder` — common embedder lifecycle (`warmup` + `name` + properties).
- `StorageBackend` — abstract base with `migrate`, `upsert_document`,
  `upsert_conversation`, `write_embeddings`, `apply_label`, `lock_source`,
  `search_dense`, `search_lexical`, `iter_documents_for_classification`,
  `iter_code_chunks_for_enrichment`, `replace_document_chunks`,
  `update_chunk_enrichment`. Implementations: `PostgresBackend` (`backends/postgres.py`),
  `SQLiteBackend` (`backends/sqlite.py`).

### Data flow

1. **Discovery** — `Source.discover()` enumerates roots; `WatchedSource` debounces filesystem events.
2. **Extraction** — `Extractor.extract(path)` returns `ExtractedDocument`. The `FilesystemSource` dispatches every file through `ExtractorRegistry.for_path(path)`.
3. **Chunking** — `ChunkerDispatcher` picks the chunker from `ExtractedDocument.metadata.chunker_hint`. Class-aware re-dispatch via `ChunkerDispatcher.for_class(...)` runs at rechunk time.
4. **Persistence** — `StorageBackend.upsert_document` (or `upsert_conversation`) writes the row and its chunks. The Phase C content-hash path reuses existing embeddings for byte-identical chunks.
5. **Embedding** — `corpus-forge embed` walks `chunks` and writes vectors into the dynamic `embeddings_<name>` table.
6. **Classification** — `corpus-forge classify` walks documents, runs the chain, and writes a `class=*` label via `apply_label`.
7. **Rechunking** — `corpus-forge rechunk` (post-classify) re-runs the class-mapped chunker.
8. **Enrichment** — `corpus-forge enrich` (post-classify) attaches LLM-synthesised metadata to every `class=code` chunk.
9. **Query** — `HybridRetriever.search()` runs dense (ANN) + lexical (FTS) fanout, fuses, and optionally reranks. `corpus-forge search`, `corpus-forge eval`, and the MCP server all share the same call surface.
10. **Export** — views (`corpus_text_export`, `corpus_chat_export`) project HF-Datasets-shaped rows; `corpus-forge export chat` adds chat-template rendering.

### Extension points

- **New file format** → drop a class in `corpus_forge/extractors/` implementing `Extractor.supported_extensions` + `extract()`, and add it to `register_default_extractors`.
- **New chunker** → subclass `ChunkerBase` (or write a free function returning `list[TextChunk]`) and register it in `ChunkerDispatcher`.
- **New embedder** → subclass `BaseEmbedder` (or implement `Embedder` directly); add to `[[embedders]]` in `config.toml`. A new dynamic `embeddings_<name>` table is provisioned at first registration.
- **New classifier** → implement `Classifier` (`classify(doc) -> ClassLabel`); add to `_CLASSIFIER_REGISTRY` in `corpus_forge/classifiers/__init__.py`; the `ClassifierConfig.chain` config field accepts the new name.
- **New VLM / Whisper / enricher backend** → implement the matching protocol + add an entry to the per-package registry (`vlm/registry.py`, `whisper/registry.py`, `enrichers/registry.py`).
- **New storage backend** → implement `StorageBackend` and dispatch on `[backend].kind` in `_build_backend_from_config`.

## Backends

Corpus-forge ships with two concrete storage backends behind a single `StorageBackend` protocol (`corpus_forge/backends/base.py`): `PostgresBackend` (`backends/postgres.py`) for networked deployments and `SQLiteBackend` (`backends/sqlite.py`) for single-machine use. Users pick at config time via `[backend].kind = "postgres" | "sqlite"`; `ingest.py` and `embed.py` dispatch on that value when constructing the backend.

The two implementations expose the same method surface but differ in deployment model, vector storage, locking strategy, and what they support. Sync (`sync_enabled = true` on a dataset) is the most important asymmetry: it is rejected at config-construction time by `Config.validate_sync_gate` when paired with `kind = "sqlite"`.

| Aspect | postgres | sqlite |
| --- | --- | --- |
| Deployment | Networked Postgres + `pgvector` extension | Local file (e.g. `~/Library/Application Support/corpus-forge/corpus.db`) or `:memory:` |
| Host topology | Multi-host (cross-host sync supported) | Single-host only |
| Sync (`sync_enabled`) | Supported | Rejected at config-load by `Config.validate_sync_gate` |
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

## Document classification

Phase E sits between the extract/chunk pipeline and the export/training
surface. After ingest writes a document, the classifier walks
`corpus.documents`, reads body + format labels + path, and writes a
`namespace='class', value=<one-of-9>` strong label via
`backend.apply_label(...)`. The label is **post-ingest** and idempotent —
documents that already carry a `classifier:*`-sourced class label are
skipped unless `--reclassify` is set.

```
                          ┌──────────────────────────────────┐
                          │      corpus.documents (rows)      │
                          │  + corpus.document_labels (rows) │
                          └────────────────┬─────────────────┘
                                           │
                  iter_documents_for_classification(...)
                                           │
                                           ▼
                          ┌──────────────────────────────────┐
                          │       ClassifierRegistry         │
                          │     (ordered: rule → llm)        │
                          └────────────────┬─────────────────┘
                                           │
              ┌────────────────────────────┴───────────────────────────┐
              │                                                        │
              ▼                                                        ▼
   ┌──────────────────────┐                              ┌──────────────────────┐
   │  RuleBasedClassifier │                              │     LLMClassifier    │
   │  (stdlib, μs/doc)    │                              │ (Ollama, ~5-10s/doc) │
   │                      │                              │                      │
   │  format-label fast   │   conf >= threshold:         │  POST /api/generate  │
   │  path / file ext /   │   short-circuit             │  format=json, head+  │
   │  body heuristics     │ ───────────────────►         │  tail excerpt, 9-    │
   │                      │   conf <  threshold:         │  enum constraint     │
   │  always emits a      │   escalate                  │                      │
   │  ClassLabel          │ ───────────────────────────► │  invalid output ⇒    │
   └──────────────────────┘                              │  class=other 0.2     │
                                                        └──────────┬─────────┘
                                                                   │
                                  (winner_name, ClassLabel)        │
                                                                   ▼
                                          ┌──────────────────────────────────┐
                                          │    apply_label(                  │
                                          │      "document", doc_id,         │
                                          │      "class", value,             │
                                          │      source=f"classifier:{name}",│
                                          │      confidence=...,             │
                                          │    )                             │
                                          └──────────────────────────────────┘
```

### Class taxonomy

| value | what it covers |
|---|---|
| `code` | source code, scripts, build files (Makefile, Dockerfile), config-as-code (`.nix`, `.tf`) |
| `chat` | conversation transcripts — Claude Code, OpenCode, generic dialogue |
| `book` | long-form non-pedagogical — fiction, memoir, popular non-fiction, biography |
| `textbook` | long-form pedagogical — academic textbooks, course notes, learning material with exercises |
| `paper` | research / academic papers (PDF with abstract+citations pattern) |
| `article` | blog posts, magazine articles, news, opinion writing |
| `reference` | API docs, schema specs, manifests, machine-readable data (JSON/YAML/TOML/CSV) |
| `note` | personal notes — Obsidian vault, markdown jottings, journals |
| `other` | fallback when no signal is strong enough to commit |

### Chain composition

Configured under `[classifier]` in `config.toml`. Default is
`chain = ["rule", "llm"]` with `escalation_threshold = 0.4`. Each name in
`chain` must be a known classifier in
`corpus_forge.classifiers._CLASSIFIER_REGISTRY`:

| name | class | module |
|---|---|---|
| `rule` | `RuleBasedClassifier` | `corpus_forge.classifiers.rule_based` |
| `llm` | `LLMClassifier` | `corpus_forge.classifiers.llm` |

`register_default_classifiers(config)` walks `chain` and lazy-imports each
submodule, forwarding LLM-relevant config fields to `LLMClassifier(...)`
(model, URL, timeout, temperature, excerpt budget).

### Escalation policy

`ClassifierRegistry.classify(doc, threshold)` walks classifiers in
registration order:

1. Each yields `None` (skip) or a `ClassLabel`.
2. The first non-`None` result whose `confidence >= threshold` wins
   outright and the walk short-circuits.
3. If every label is below threshold, the **last** non-`None` label is
   returned so the caller still gets something to act on.
4. If every classifier returns `None`, dispatch returns `None`.

The result is `tuple[str, ClassLabel] | None` — the first element is the
classifier name that produced the label. The CLI uses it to write the
correct `source = "classifier:<winner>"` value on `document_labels`.

The LLM classifier (`LLMClassifier`) follows the documented graceful-
fallback contract: a hallucinating model that returns a `class` outside
the 9-enum, or unparseable inner JSON, is mapped to
`ClassLabel(value="other", confidence=0.2, rationale="invalid LLM output: …")`
with a WARNING log. Transport failures (timeout, connection refused,
4xx/5xx) raise typed exceptions from `corpus_forge.classifiers.base`
(`ClassifierTimeoutError`, `ClassifierUnavailableError`,
`ClassifierResponseError`).

### Local vs remote model endpoints

Every model client in corpus-forge — the VLM (`vlm.ollama_url`), the
classifier LLM (`classifier.llm_url`), and any future remote-model
plug-in — accepts an arbitrary HTTP URL via config. The default is
`http://localhost:11434` (a local Ollama daemon); the same backends work
against any Ollama-compatible endpoint by swapping the URL — no code
change. Useful when classification or OCR should run on a beefier host
than the laptop doing ingest, or when a fleet of machines shares a
single hosted model endpoint.

For the wave-by-wave history, see
[`.planning/tdd/phase_e_classification.md`](../.planning/tdd/phase_e_classification.md).

## Content-defined chunking + rechunk

Phase F replaces positional `MarkdownChunker` / `PassthroughChunker` slicing
for prose classes with FastCDC rolling-hash boundaries. Mid-document edits
ripple ≤ 2-3 chunks instead of shifting every downstream chunk, so the
Phase C `chunks.content_hash` embedding-reuse path achieves its design
potential.

```
classify → "class=*" docs → ChunkerDispatcher.for_class → chunk(text) → replace_document_chunks
                                          ↑                                       ↑
                                  class-aware routing               content-hash-aware swap
                                                                    (preserves embeddings on
                                                                     byte-identical chunks)
```

### Class-mapped chunker

| class | chunker | metadata signature |
|---|---|---|
| `code` | `CodeChunker` (tree-sitter or byte-line fallback) | `kind`, `name`, `language`, `byte_range` |
| `chat` | `ConversationChunker` | conversation-specific |
| `reference` | `PassthroughChunker` | none |
| `book` / `textbook` / `paper` / `article` / `note` / `other` | `CDCChunker` (FastCDC) | `cdc_fingerprint`, `byte_range` |

### CDCChunker

`corpus_forge/chunkers/cdc.py` wraps the MIT-licensed `fastcdc` package. We
feed the UTF-8 byte representation of the input, re-decode each emitted
byte range back to `str`, and rewind any mid-codepoint cuts to the nearest
preceding codepoint start so `UnicodeDecodeError` never surfaces.

Defaults: `min_size = 256`, `avg_size = 1024`, `max_size = 4096`. Each
chunk's bytes are SHA-256-fingerprinted and stamped into
`TextChunk.metadata["cdc_fingerprint"]` — the eventual cross-document dedup
key.

### Idempotency

`corpus-forge rechunk` is idempotent on **text + metadata signature**.
Text-only comparison is insufficient (a small markdown document that fits
in a single positional chunk is also a single CDC chunk — same text but
no `cdc_fingerprint`), so the CLI consults `_expected_metadata_signature(class)`
and only short-circuits when every stored chunk already carries the
expected key.

For the wave-by-wave history, see
[`.planning/tdd/phase_f_cdc_chunking.md`](../.planning/tdd/phase_f_cdc_chunking.md).

## Whisper transcription (audio + video)

Phase G P0 plugs a Whisper-family transcription model behind the existing
extractor layer. `AudioExtractor` claims `.mp3`/`.wav`/`.m4a`/`.ogg`/`.flac`;
`VideoExtractor` claims `.mp4`/`.mov`/`.webm`/`.mkv`/`.avi` and uses
`imageio-ffmpeg` to demux the audio track first.

### Backend matrix

| Backend | Module | Endpoint / runtime | Default model | Notes |
|---|---|---|---|---|
| Local | `corpus_forge.whisper.local.LocalWhisper` | in-process `faster-whisper` | `small` | tiny / base / small / medium / large; precision = `local_compute_type` ∈ `{auto, float16, int8, int8_float16}`. |
| Remote | `corpus_forge.whisper.remote.RemoteWhisper` | `POST /audio/transcriptions` multipart | `whisper-1` | Default base URL: `https://api.openai.com/v1`. Swap to Groq (`whisper-large-v3`), Replicate, or self-hosted whisper.cpp speaking the same multipart contract. |
| Noop | `corpus_forge.whisper.base.NoopWhisper` | n/a | n/a | Selected when `whisper.backend = "none"` (the default). Audio / video files are silently skipped. |

### Failure mode

If a real Whisper backend is wired in but transcription fails (network,
unsupported codec, ffmpeg missing), the extractor returns `None` and the
file is skipped with an ERROR log — never poisoning the ingest pass.

## Multi-modal embeddings

Phase G P1 introduces a **separate** protocol (`MultiModalEmbedder`) instead
of retrofitting the text `Embedder`. Rationale: text embedders take
`Sequence[str]`; multi-modal backends accept either `list[str]` OR
`list[bytes]`, and the dual-write image/text storage paths keep the
existing text pipeline untouched.

### Storage

The dynamic `image_embeddings_<name>` table family mirrors the text
`embeddings_<name>` family. Provisioned at runtime by
`StorageBackend.register_multimodal_embedder` (Postgres `pgvector`
column + HNSW; SQLite `vec0` virtual table or plain BLOB fallback).

Alembic `0011_image_embeddings` reserves the namespace via an
`embedders.image BOOLEAN` column so the runtime can distinguish text
embedders from image embedders when listing registrations.

### Backend matrix

| Backend | Module | Source | Default model | Auth |
|---|---|---|---|---|
| `ClipLocalEmbedder` | `corpus_forge.embedders.clip_local` | sentence-transformers | `clip-ViT-B-32` (512 d, MIT) — `jina-clip-v2` (1024 d) also accepted | none |
| `ClipRemoteEmbedder` | `corpus_forge.embedders.clip_remote` | OpenAI-compatible `/v1/embeddings` | provider-specific | `Authorization: Bearer …` |

### CLI

```
corpus-forge embed -e clip_local --image    # backfill image embeddings only
```

`_resolve_image_bytes` looks up the image payload in three places, in
order: `metadata.image_b64` → `metadata.image_path` → the document's
`filesystem://` URI. The `ImageExtractor` (Phase D P1) writes
`image_path` into metadata so this resolution is automatic for
ingest-discovered images.

For the wave-by-wave history, see
[`.planning/tdd/phase_g_multimodal.md`](../.planning/tdd/phase_g_multimodal.md).

## Code enrichment

Phase H attaches LLM-synthesised metadata to every chunk of a
`class=code` document. The pipeline is gated by Phase E's classifier
output — non-code documents are silently skipped by
`StorageBackend.iter_code_chunks_for_enrichment` — so the LLM cost only
lands where it matters.

```
classify → "class=code" docs → iter_code_chunks_for_enrichment → CodeEnricher → update_chunk_enrichment
                                          ↑                          ↑                 ↑
                                  filters on class=code     enrich(chunk, language)  jsonb_set 'enrichment'
                                  AND missing-or-stale model
```

The enrichment record (`chunks.metadata.enrichment`) carries five keys:

| key | type | purpose |
|---|---|---|
| `docstring` | `str` or `null` | synthesised docstring or `null` when existing one suffices |
| `summary` | `str` | 1-2 sentence semantic summary in domain language |
| `symbols` | `list[str]` | referenced symbol names (flat; P2 reserved for graph storage) |
| `model` | `str` | model tag that produced the record; idempotency key |
| `confidence` | `float` | self-reported `[0.0, 1.0]` |

### Backend matrix

| backend | local-or-remote | API shape | auth |
|---|---|---|---|
| `QwenCoderLocal` | local | Ollama `/api/generate` | none |
| `QwenCoderRemote(api_shape="ollama")` | remote | Ollama `/api/generate` | optional `Authorization: Bearer …` |
| `QwenCoderRemote(api_shape="openai")` | remote | OpenAI `/chat/completions` (`response_format=json_object`) | required `Authorization: Bearer …` |

All three concrete backends share the inner-JSON parser
`corpus_forge.enrichers.base._parse_enrichment_response`. A model that
emits malformed JSON or the wrong shape produces a graceful-fallback
`CodeChunkEnrichment(summary="invalid LLM output", confidence=0.0)`
with a WARNING log — never an exception. Transport failures (timeout,
connection refused, 4xx/5xx, missing envelope keys) raise typed
exceptions from `corpus_forge.enrichers.base`
(`EnricherTimeoutError`, `EnricherUnavailableError`, `EnricherResponseError`).

### Local vs remote endpoints

Phase H ships **two** concrete classes (`QwenCoderLocal` and
`QwenCoderRemote`) plus **two** explicit config fields (`local_url`,
`remote_url`) to satisfy the project's local-or-remote URL principle.
The local backend is wired for the common laptop case (local Ollama
daemon at `http://localhost:11434`); the remote backend handles two
production patterns at once:

- a hosted Ollama on a beefier internal box (`api_shape="ollama"`,
  optional bearer auth);
- any OpenAI-compatible proxy or SaaS — vLLM, TGI, llama.cpp's OpenAI
  shim, Together / DeepInfra / Fireworks (`api_shape="openai"`,
  required bearer auth).

Switching shape is a one-line config change; no code change.

### Idempotency

`iter_code_chunks_for_enrichment(model_tag)` filters out chunks whose
existing `metadata.enrichment.model` already equals `model_tag`. Re-running
`corpus-forge enrich` after the first pass is a near-no-op (the iterator
elides every already-enriched chunk before it touches the LLM); changing
the model tag in config causes the next run to reprocess the corpus
automatically. The `--reclassify-on-model-change` flag forces re-enrichment
even when tags match — used after a prompt-template change where the model
is unchanged but you still want fresh output.

For the wave-by-wave history, see
[`.planning/tdd/phase_h_code_enrichment.md`](../.planning/tdd/phase_h_code_enrichment.md).