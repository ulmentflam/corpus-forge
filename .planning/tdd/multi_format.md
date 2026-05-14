# Phase D — Multi-Format Corpus (PDF/Image OCR + Universal Code Ingest)

_Owner: tdd-principal. Workers: read freely. Edit only your claimed row's `status` and `claimed_by`._

Source plan (full prose, rationale, research): `/Users/evanowen/.claude/plans/let-s-solidify-the-next-wiggly-yao.md`

## Status

**Phase A/C** (Active Directory Sync): complete.
**Phase B** (SQLite backend): in flight at `.planning/tdd/sqlite_backend.md`.
**Phase D** (this file): **pending kickoff**.

## Goal

Lift corpus-forge from "markdown vault + chat history" to a true universal
text corpus: every human-readable file on disk, normalized to Markdown for
prose / tree-sitter-segmented text for code, fed through the existing
chunker → embedder → storage pipeline with no churn to those layers.

OCR (local Ollama VLM, remote Mistral fallback) ships in the same milestone
so PDFs and images aren't second-class.

## Architecture seam

Today `Source.parse(path) -> RawDocument(text=...)`. Single chunker per
source declared in config.

New layer: `Extractor` is dispatched by file extension. An extractor
returns `ExtractedDocument(text, chunker_hint, language?, metadata)` and
the chunker is picked **per-document** from `chunker_hint`
(`"markdown" | "code" | "passthrough"`). A new `FilesystemSource` walks
heterogeneous trees and dispatches through the extractor registry. Chunker
dispatch happens inside `ingest_one`.

This makes every concrete extractor a leaf plugin — adding a new format
later is one new file under `corpus_forge/extractors/` plus a registry
entry. Embedder + storage layers are untouched.

## Phase split (P0/P1, hard-gated)

### P0 — Text & code extractors, no model required

Ships standalone value (no Ollama, no API keys). Hard gate before P1.

**Extractor matrix (P0)**

| ext family | extractor | strategy | chunker_hint | Python dep |
|---|---|---|---|---|
| `.md` `.markdown` | `PassthroughMarkdownExtractor` | read text | `markdown` | none |
| `.txt` `.log` `.rst` `.org` `.tex` `.adoc` | `PlainTextExtractor` | read text | `passthrough` | none |
| `.pdf` (digital only in P0) | `PdfDigitalExtractor` | pymupdf4llm → markdown | `markdown` | `pymupdf4llm` (AGPL) |
| `.html` `.htm` `.xhtml` | `HtmlExtractor` | readability → markdownify | `markdown` | `readability-lxml`, `markdownify` |
| `.epub` | `EpubExtractor` | ebooklib → markdownify | `markdown` | `ebooklib`, `markdownify` |
| `.docx` `.pptx` `.xlsx` | `OfficeExtractor` | Docling (markdown) | `markdown` | `docling` |
| `.ipynb` | `NotebookExtractor` | jupytext py:percent + cell→markdown | `markdown` | `jupytext` |
| `.csv` `.tsv` | `CsvExtractor` | pandas → markdown table (size-bounded) | `markdown` | `pandas` |
| `.json` `.yaml` `.yml` `.toml` | `StructuredDataExtractor` | pretty-print fenced | `passthrough` | stdlib |
| `.srt` `.vtt` | `SubtitleExtractor` | strip timing → flat text | `passthrough` | none |
| `.py .js .ts .tsx .jsx .go .rs .java .kt .scala .rb .ex .exs .erl .hrl .pl .hs .ml .clj .cljs .lisp .scm .sh .bash .zsh .fish .sql .css .scss .lua .zig .nim .cr .r .jl .swift .dart .nix .c .h .cc .cpp .hpp .cxx .m .mm` | `CodeExtractor` | tree-sitter-language-pack → tagged regions | `code` | `tree-sitter`, `tree-sitter-language-pack` |
| `Makefile` `Dockerfile` `.gitignore` `.editorconfig` (filename fallback) | `CodeExtractor` long-tail | line/brace-aware | `code` | — |

**License posture**: AGPL allowed for this milestone (user decision —
2026-05-14). `pymupdf4llm` (AGPL-3.0) used for digital PDFs;
`markdownify` (MIT) for HTML→MD over `html2text` (GPL-3.0). `marker-pdf`
(GPL) and `MinerU` (AGPL) avoided — Docling (MIT) covers the same niches.
The `[multi-format]` extra effectively AGPL-binds an installed corpus-forge;
pure-core install stays Apache-2.0. README "Distribution / licensing"
section to surface this.

**New `CodeChunker`** (`corpus_forge/chunkers/code.py`): tree-sitter AST
walk, one `TextChunk` per top-level construct
(function/class/method/module-level block), each annotated with
`metadata={"kind", "name", "language", "byte_range"}`. Oversize constructs
sub-chunked at next-level AST boundary with overlap. Undersize constructs
coalesced up to `max_chars`. Header `# <relative path> :: <kind> <name>`
prepended to every chunk so embedder sees retrieval-ready context.
Long-tail fallback: byte-budget line chunker preferring blank-line /
brace-depth boundaries when no grammar exists.

**Chunker dispatch** (`corpus_forge/ingest.py`): `ChunkerDispatcher` keyed
on `RawDocument.metadata["chunker_hint"]`, backwards-compatible — no hint
=> use source-level chunker as today. Existing `markdown_vault`,
`claude_code`, `opencode` sources keep working unchanged.

**New `FilesystemSource`** (`corpus_forge/sources/filesystem.py`): generic
walker over `WatchedSource`. `discover()` walks every file under `root`
matching registered extractor extensions; `parse()` delegates to
`ExtractorRegistry.get_for(path)` and wraps in `RawDocument`. One source
plugin handles a heterogeneous tree — no per-format Source proliferation.

**Config** — `ExtractionConfig` pydantic model attached to
`DatasetSourceConfig`. `[[datasets.sources.extraction]]` block in
`config.example.toml` with `enable_pdf`, `enable_office`, `enable_code`,
`code_chunker_config`.

### P1 — Vision/OCR (local Ollama + remote fallback)

**New protocol** (`corpus_forge/vlm/base.py`) mirrors embedder layer
pattern. Methods: `describe_image(image, *, prompt) -> str`,
`extract_page(image, *, page_number) -> str`, `warmup()`.

**Backends**:
- `vlm/ollama.py::OllamaVLM` — `POST /api/generate` against a local Ollama
  daemon. Default model **`qwen2.5vl:7b`** (Apache-2.0, native Ollama tag,
  ~5GB, DocVQA 95.7 — throughput/quality sweet spot on M-series 64GB). User
  can override to `qwen2.5vl:32b` for higher quality.
- `vlm/mistral.py::MistralOCR` — Mistral OCR API ($2/1k pages, native
  Markdown). Remote fallback for OOM / accuracy-critical / batch.
- `vlm/registry.py` — same shape as `embedders/registry.py`.

**Image extractor** (`corpus_forge/extractors/image.py`): extensions
`.png .jpg .jpeg .tif .tiff .bmp .webp .heic`. Active VLM with a
"transcribe text + describe content" prompt. Markdown output,
`chunker_hint = "markdown"`.

**PDF extractor escalation** (`corpus_forge/extractors/pdf.py`):

1. `pymupdf4llm` text-layer pass.
2. If text length below `min_chars_per_page` (default 100) → escalate.
3. Rasterize each page via `pdf2image` (poppler) → active VLM backend.
4. Concatenate per-page markdown; `metadata.pages_ocr_count`,
   `metadata.ocr_backend`.

**Config**:

```toml
[vlm]
backend             = "ollama"   # "ollama" | "mistral" | "none"
ollama_model        = "qwen2.5vl:7b"
ollama_url          = "http://localhost:11434"
mistral_api_key_env = "MISTRAL_API_KEY"   # secrets.env

[datasets.sources.extraction]
ocr_enabled            = true
ocr_min_chars_per_page = 100
ocr_image_extensions   = ["png","jpg","jpeg","tif","tiff","bmp","webp","heic"]
```

## Fixture tree — `tests/fixtures/multi_format_corpus/`

**Single source of truth for integration tests and manual smoke.** Small
(~5 MB), in-repo, diverse enough to hit every extractor's happy path and
the two most important edge cases per format. Built by
`scripts/build_fixture_corpus.py` so contributors can regenerate from
upstream-licensed sources.

```
tests/fixtures/multi_format_corpus/
├── README.md                            # what's here and why
├── prose/
│   ├── intro.md
│   ├── notes.txt
│   ├── frontmatter.md
│   └── tex-snippet.tex
├── pdf/
│   ├── digital-single-col.pdf
│   ├── digital-two-col-equations.pdf
│   └── (P1) scanned-paper.pdf
├── html/
│   ├── simple-article.html
│   └── nav-and-ads.html
├── epub/small-book.epub
├── office/{report.docx, slides.pptx, tiny-sheet.xlsx}
├── notebook/analysis.ipynb
├── data/{records.csv, config.toml, manifest.json, transcript.srt}
├── images/                              # P1 only
│   ├── screenshot.png
│   ├── photo-of-receipt.jpg
│   └── diagram.webp
└── code/
    ├── python/{module.py, package/__init__.py, package/util.py}
    ├── cpp/{main.cpp, util.h, util.cpp}
    ├── c/{lib.c, lib.h}
    ├── js-ts/{app.ts, react.tsx, server.js}
    ├── go/{main.go, internal/handler.go}
    ├── rust/{main.rs, lib.rs}
    ├── java/App.java
    ├── kotlin-scala/{App.kt, App.scala}
    ├── ruby/app.rb
    ├── beam/{hello.ex, mod.erl, mod.hrl}
    ├── prolog/rules.pl                  # exercises tree-sitter long-tail
    ├── haskell-ocaml/{Main.hs, main.ml}
    ├── lisp-clj/{core.clj, demo.lisp, demo.scm}
    ├── shell/{install.sh, deploy.bash, fish.fish}
    ├── web/{styles.css, page.html, query.sql}
    ├── exotic/{tiny.zig, mod.nim, app.cr, plot.r, run.jl, app.swift, ui.dart, default.nix}
    └── build/{Makefile, Dockerfile, .gitignore, .editorconfig}
```

Used by:
- `tests/integration/test_multi_format_ingest_e2e.py` (P0 gate)
- Manual smoke: `corpus-forge ingest --once` against this directory, then
  check `corpus.chunks` counts per extension family.
- `tests/integration/test_ocr_local_e2e.py` (P1, marker-gated, scanned/
  images subdirs only).

## Project gates (unchanged from Phase A/C)

- lint: `uv run ruff check corpus_forge tests`
- format: `uv run ruff format --check corpus_forge tests`
- typecheck: `uv run pyrefly check corpus_forge` (strict)
- unit: `uv run pytest tests/unit -v --cov=corpus_forge --cov-fail-under=85`
- integration: `uv run pytest tests/integration -v`
- ci: `make ci`

New pytest markers (additions to `pyproject.toml:185`):
- `requires_ollama` — skip when no Ollama daemon at `ollama_url`.
- `requires_mistral_api` — skip when `MISTRAL_API_KEY` unset.

## Reused primitives (do not reinvent)

- `WatchedSource.discover()` / `parse()` split (`sources/base.py:63-107`).
- `Chunker.chunk()` size-bound + overlap loop (`chunkers/base.py:32-78`).
- `identity.file_content_hash` for `RawDocument.content_hash`.
- `backend.upsert_document` short-circuit on unchanged `content_hash`
  (`postgres.py:294`) — extractor output isn't recomputed if file bytes
  unchanged.
- Embedder warmup-on-first-use pattern (`embedders/sentence_transformers.py`).
- Embedder remote/local split + lazy import (`embedders/openai.py`).
- Embedder registry shape (`embedders/registry.py`).

## Waves

Conflict-detection rule: tasks share a wave only if their `surface` file
lists are disjoint.

### Wave 0 — Foundation (parallel, disjoint files)

| id | surface | why this wave |
|----|---------|---------------|
| D-01 | `extractors/__init__.py` + `extractors/base.py` + `extractors/registry.py` (new) | Protocol + registry, no deps. |
| D-02 | `chunkers/code.py` (new) + `chunkers/__init__.py` export | New chunker, depends only on tree-sitter. |
| D-03 | `extractors/passthrough.py` + `extractors/plaintext.py` (new) | Pure stdlib leaf extractors. |
| D-04 | `extractors/structured.py` + `extractors/subtitle.py` (new) | Pure stdlib leaf extractors. |
| D-05 | `ingest.py::ChunkerDispatcher` (additive) + `tests/unit/test_chunker_dispatch.py` | Additive — old chunker path stays default when no hint. |
| D-06 | `config.py::ExtractionConfig` (additive) + `tests/unit/test_config_multi_format.py` | Additive pydantic; existing tests stay green. |

### Wave 1 — Document extractors (parallel; each its own file)

| id | depends on | surface | why now |
|----|------------|---------|---------|
| D-07 | D-01 | `extractors/pdf.py` (digital-only) + `tests/unit/test_extractor_pdf_digital.py` | pymupdf4llm-only path; no VLM yet. |
| D-08 | D-01 | `extractors/html.py` + `tests/unit/test_extractor_html.py` | |
| D-09 | D-01 | `extractors/epub.py` + `tests/unit/test_extractor_epub.py` | |
| D-10 | D-01 | `extractors/office.py` + `tests/unit/test_extractor_office.py` | Docling. |
| D-11 | D-01 | `extractors/notebook.py` + `tests/unit/test_extractor_notebook.py` | |
| D-12 | D-01 | `extractors/csv.py` + `tests/unit/test_extractor_csv.py` | |
| D-13 | D-01, D-02 | `extractors/code.py` + `tests/unit/test_extractor_code.py` | Wraps `CodeChunker` (D-02). |

### Wave 2 — Source integration (serialized on `ingest.py` / `config.py`)

| id | depends on | surface | why now |
|----|------------|---------|---------|
| D-14 | D-01..D-13 | `sources/filesystem.py` + `tests/unit/test_filesystem_source.py` | Generic walker; depends on full extractor registry. |
| D-15 | D-14, D-06 | `ingest.py::_instantiate_source` branch + `tests/unit/test_ingest_filesystem.py` | Same file as D-05 — Wave 2 because D-05 must land first. |
| D-16 | D-06, D-15 | `config.example.toml` (new `knowledge-base` dataset block) | Doc-only after schema is settled. |

### Wave 3 — P0 gate

| id | depends on | surface | why now |
|----|------------|---------|---------|
| D-17 | D-01..D-16 | `tests/fixtures/multi_format_corpus/` + `scripts/build_fixture_corpus.py` (new) | Fixture corpus + generation script. |
| D-18 | D-17 | `tests/integration/test_multi_format_ingest_e2e.py` (new) | E2E against fixture tree. |
| D-19 | D-18 | `pyproject.toml` extras (`[multi-format]`, `[code]`) + `README.md` distribution-licensing note + `docs/architecture.md` extractor section | Docs + extras. |
| D-20 | D-19 | `make ci` green at ≥85% coverage — **P0 gate**. | Hard stop before P1 dispatch. |

### Wave 4 — VLM foundation (parallel)

| id | depends on | surface | why now |
|----|------------|---------|---------|
| E-01 | D-20 | `vlm/__init__.py` + `vlm/base.py` + `vlm/registry.py` (new) | Protocol + registry, no deps. |
| E-02 | E-01 | `vlm/ollama.py` + `tests/unit/test_vlm_ollama.py` | HTTP client (mocked in unit). |
| E-03 | E-01 | `vlm/mistral.py` + `tests/unit/test_vlm_mistral.py` | HTTP client (mocked in unit). |
| E-04 | D-20 | `config.py::VLMConfig` + `tests/unit/test_config_vlm.py` | Additive pydantic. |

### Wave 5 — OCR integration (serialized on `extractors/pdf.py`)

| id | depends on | surface | why now |
|----|------------|---------|---------|
| E-05 | E-02, E-03, E-04 | `extractors/pdf.py` escalation upgrade + `tests/unit/test_pdf_extractor_escalation.py` | Same file as D-07 — serialized after Wave 4. |
| E-06 | E-02, E-03, E-04 | `extractors/image.py` (new) + `tests/unit/test_extractor_image.py` | Parallel-safe (new file). |

### Wave 6 — P1 gate

| id | depends on | surface | why now |
|----|------------|---------|---------|
| E-07 | E-05 | `tests/integration/test_ocr_local_e2e.py` + `tests/fixtures/multi_format_corpus/pdf/scanned-paper.pdf` + `images/*` | `requires_ollama` marker; CI skips when daemon absent. |
| E-08 | E-05 | `tests/integration/test_ocr_remote_e2e.py` | `requires_mistral_api` marker. |
| E-09 | E-07, E-08 | `Makefile` (`make test-ocr` target) + `secrets.env.example` + `docs/architecture.md` VLM section | |
| E-10 | E-09 | Manual cross-backend smoke (scanned PDF + 1 image: Ollama vs Mistral) — **P1 gate**. | |

## Definition of Done

### P0 (gate D-20)

- [ ] `corpus_forge/extractors/` package with all 11 P0 extractors + `base.py` + `registry.py`.
- [ ] `corpus_forge/chunkers/code.py::CodeChunker` covers every extension in the P0 matrix or falls back cleanly to byte-line chunker.
- [ ] `corpus_forge/sources/filesystem.py::FilesystemSource` exported and registered in `ingest.py::_instantiate_source`.
- [ ] `corpus_forge/ingest.py::ChunkerDispatcher` resolves chunker from `RawDocument.metadata["chunker_hint"]`; old sources keep working.
- [ ] `corpus_forge/config.py::ExtractionConfig` accepted; `config.example.toml` updated.
- [ ] `tests/fixtures/multi_format_corpus/` present with the layout above.
- [ ] `make test-unit` green, coverage ≥85%.
- [ ] `make test-integration` green including `test_multi_format_ingest_e2e.py`.
- [ ] `make ci` green.
- [ ] Manual smoke: point a dataset at `tests/fixtures/multi_format_corpus/`, run `corpus-forge ingest --once`, confirm per-extension chunk counts in DB.

### P1 (gate E-10)

- [ ] `corpus_forge/vlm/` package with `base.py`, `ollama.py`, `mistral.py`, `registry.py`.
- [ ] `corpus_forge/extractors/pdf.py` escalates correctly when text-layer is empty (unit test with synthetic scanned PDF).
- [ ] `corpus_forge/extractors/image.py` produces non-empty markdown for fixture images via Ollama.
- [ ] `Makefile` exposes `make test-ocr`.
- [ ] `secrets.env.example` includes `MISTRAL_API_KEY=`.
- [ ] `make ci` green; `requires_ollama` / `requires_mistral_api` markers do not break CI without daemon/API.
- [ ] Manual smoke: `pdf/scanned-paper.pdf` → reasonable Markdown via local Ollama; same PDF via Mistral OCR; results compared. One image under `images/` round-tripped through Ollama.

## Out of scope (P2 backlog)

- Code-aware LLM pass (docstring generation, semantic chunk summaries). Revisit only after retrieval evals show a gap. Future pick: Qwen2.5-Coder-32B local.
- Audio/video transcription (`.mp3 .wav .mp4`) via Whisper-style local model.
- True content-defined chunking for prose (replacing positional MarkdownChunker).
- Granite-Docling-258M as in-process VLM via Docling's MLX pipeline — investigate if Ollama latency becomes a problem.
- Multi-modal embeddings (text + image into the same vector space).

## Risks / open issues

- **Docling install footprint** pulls `torch` transitively. Mitigated by `[multi-format]` extra; `[code]`-only install stays light.
- **tree-sitter Prolog grammar** is community-maintained, quality variable. CodeChunker long-tail fallback covers misses.
- **Apple Silicon Ollama VLM throughput** — 7B sweet spot (~1–2s/page on M-series), 32B feasible but slow (3–5s/page). Mistral remote path is the batch-job escape hatch.
- **AGPL surface** from `pymupdf4llm`. Documented in README "Distribution / licensing". Closed-distribution path would require swapping in Docling's digital-PDF reader.
- **Chunker-config invalidation** — switching CodeChunker config (`min_chars`, etc.) invalidates `chunks.content_hash` reuse from Phase C P0. Same caveat as `active_directory_sync.md:299`.

## Task table

| id | title | depends_on | surface | risk | status | claimed_by | notes |
|----|-------|------------|---------|------|--------|------------|-------|
| D-01 | `Extractor` protocol + `ExtractorRegistry` | — | `corpus_forge/extractors/{__init__,base,registry}.py`, `tests/unit/test_extractor_registry.py` | low | pending | — | Protocol + ext→Extractor dict + feature-flag-aware registration. |
| D-02 | `CodeChunker` via tree-sitter-language-pack | — | `corpus_forge/chunkers/code.py`, `corpus_forge/chunkers/__init__.py`, `tests/unit/test_code_chunker.py` | med | pending | — | AST walk + oversize sub-split + undersize coalesce + long-tail line-chunk fallback. |
| D-03 | `PassthroughMarkdownExtractor` + `PlainTextExtractor` | D-01 | `corpus_forge/extractors/{passthrough,plaintext}.py`, `tests/unit/test_extractor_passthrough.py`, `tests/unit/test_extractor_plaintext.py` | low | pending | — | Stdlib only. |
| D-04 | `StructuredDataExtractor` + `SubtitleExtractor` | D-01 | `corpus_forge/extractors/{structured,subtitle}.py`, `tests/unit/test_extractor_structured.py`, `tests/unit/test_extractor_subtitle.py` | low | pending | — | Pretty-print fenced JSON/YAML/TOML; SRT/VTT timing strip. |
| D-05 | `ChunkerDispatcher` in ingest | D-01 | `corpus_forge/ingest.py`, `tests/unit/test_chunker_dispatch.py` | low | pending | — | Additive; old sources keep working when no `chunker_hint`. |
| D-06 | `ExtractionConfig` pydantic | — | `corpus_forge/config.py`, `tests/unit/test_config_multi_format.py` | low | pending | — | Additive; default `enable_*` all true. |
| D-07 | `PdfDigitalExtractor` (pymupdf4llm) | D-01 | `corpus_forge/extractors/pdf.py`, `tests/unit/test_extractor_pdf_digital.py` | med | pending | — | P0 path only. Tier-2 escalation in E-05. |
| D-08 | `HtmlExtractor` (readability + markdownify) | D-01 | `corpus_forge/extractors/html.py`, `tests/unit/test_extractor_html.py` | low | pending | — | |
| D-09 | `EpubExtractor` (ebooklib + markdownify) | D-01 | `corpus_forge/extractors/epub.py`, `tests/unit/test_extractor_epub.py` | low | pending | — | |
| D-10 | `OfficeExtractor` (Docling) | D-01 | `corpus_forge/extractors/office.py`, `tests/unit/test_extractor_office.py` | med | pending | — | `.docx .pptx .xlsx`. |
| D-11 | `NotebookExtractor` (jupytext) | D-01 | `corpus_forge/extractors/notebook.py`, `tests/unit/test_extractor_notebook.py` | low | pending | — | Mixed code+markdown cells. |
| D-12 | `CsvExtractor` (pandas → md table) | D-01 | `corpus_forge/extractors/csv.py`, `tests/unit/test_extractor_csv.py` | low | pending | — | Size-bounded; large tables sampled with row-count metadata. |
| D-13 | `CodeExtractor` (wraps `CodeChunker`) | D-01, D-02 | `corpus_forge/extractors/code.py`, `tests/unit/test_extractor_code.py` | med | pending | — | Returns `chunker_hint="code"` + `language` + per-file labels. |
| D-14 | `FilesystemSource` | D-01..D-13 | `corpus_forge/sources/filesystem.py`, `tests/unit/test_filesystem_source.py` | med | pending | — | Generic walker; reuses `WatchedSource`. |
| D-15 | `_instantiate_source` branch | D-14, D-06 | `corpus_forge/ingest.py`, `tests/unit/test_ingest_filesystem.py` | low | pending | — | One new `elif` branch matching `plugin == "filesystem"`. |
| D-16 | `config.example.toml` update | D-06, D-15 | `config.example.toml` | low | pending | — | New `knowledge-base` dataset block. |
| D-17 | Fixture corpus + generation script | D-01..D-16 | `tests/fixtures/multi_format_corpus/` (tree), `scripts/build_fixture_corpus.py`, `tests/fixtures/multi_format_corpus/README.md` | med | pending | — | ~5MB total; checked in; generation script for refreshes. |
| D-18 | E2E integration test | D-17 | `tests/integration/test_multi_format_ingest_e2e.py` | med | pending | — | Testcontainers Postgres; expects per-extension chunk counts. |
| D-19 | pyproject extras + README + architecture docs | D-18 | `pyproject.toml`, `README.md`, `docs/architecture.md` | low | pending | — | `[multi-format]`, `[code]` extras; AGPL note in README distribution section. |
| D-20 | **P0 gate** — `make ci` green | D-19 | — | gate | pending | — | Hard stop before P1 dispatch. |
| E-01 | `VLMBackend` protocol + registry | D-20 | `corpus_forge/vlm/{__init__,base,registry}.py`, `tests/unit/test_vlm_registry.py` | low | pending | — | Mirrors `embedders/registry.py`. |
| E-02 | `OllamaVLM` (HTTP) | E-01 | `corpus_forge/vlm/ollama.py`, `tests/unit/test_vlm_ollama.py` | med | pending | — | `POST /api/generate`; warmup health-checks daemon. |
| E-03 | `MistralOCR` (HTTP) | E-01 | `corpus_forge/vlm/mistral.py`, `tests/unit/test_vlm_mistral.py` | med | pending | — | API key from env via secrets.env. |
| E-04 | `VLMConfig` pydantic | D-20 | `corpus_forge/config.py`, `tests/unit/test_config_vlm.py` | low | pending | — | `[vlm]` table parsing. |
| E-05 | `PdfExtractor` escalation upgrade | E-02, E-03, E-04 | `corpus_forge/extractors/pdf.py`, `tests/unit/test_pdf_extractor_escalation.py` | high | pending | — | Same file as D-07 — serialized. Adds `pdf2image` rasterization + VLM dispatch. |
| E-06 | `ImageExtractor` | E-02, E-03, E-04 | `corpus_forge/extractors/image.py`, `tests/unit/test_extractor_image.py` | med | pending | — | Parallel-safe (new file). |
| E-07 | OCR local E2E test | E-05 | `tests/integration/test_ocr_local_e2e.py`, fixture scanned PDF + images | med | pending | — | `requires_ollama` marker. |
| E-08 | OCR remote E2E test | E-05 | `tests/integration/test_ocr_remote_e2e.py` | med | pending | — | `requires_mistral_api` marker. |
| E-09 | Makefile + secrets.env + docs | E-07, E-08 | `Makefile`, `secrets.env.example`, `docs/architecture.md` | low | pending | — | `make test-ocr` target. |
| E-10 | **P1 gate** — manual cross-backend smoke | E-09 | — | gate | pending | — | Hard close. |
