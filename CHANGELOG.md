# Changelog

All notable changes to **corpus-forge** are recorded here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [PEP 440](https://peps.python.org/pep-0440/)
version numbers (so `0.1.0b1` is the first beta of the `0.1.0` line).

## [Unreleased]

### Added

#### Phase J — Living Corpus
- `corpus-forge estimate <path>` CLI (new `corpus_forge/estimate.py`
  module + Typer command) — predicts the Postgres storage footprint of
  syncing a folder without touching the database. Per-extractor file
  counts and per-embedder embedding-row sizing including pgvector HNSW
  overhead (35 %) and btree-index overhead (~80 B / row). Human output
  by default; `--json` emits the `SyncEstimate` dataclass under stable
  `schema_version = 1`. `--compression-ratio` models LZ4-toasted text
  columns. New `[estimate]` config block with `compression_ratio`
  (default `1.0`; lower it to model TOAST compression on
  `documents.text` / `chunks.text`).
- `estimate_sync_size` MCP tool — same surface as the CLI, available to
  any MCP-connected assistant. Read-only; no `writes_enabled` flag
  required. Args: `{path, dataset?, embedders?, compression_ratio?}`.
  Returns the same `SyncEstimate` JSON shape with `schema_version = 1`.
- `CLAUDE.md`, `GEMINI.md`, and `AGENTS.md` at the repo root — vendor-
  specific (Claude Code / Desktop / API; Gemini CLI / Code Assist) and
  vendor-neutral (OpenCode, Cursor, Zed, Continue, Cline, any MCP-
  speaking client) setup guides covering install → configure →
  migrate → wire MCP → register skills → first-run sanity →
  curation-loop playbook → troubleshooting. README cross-links via a
  new "For AI assistants" section (J3).

#### Phase D — Universal multi-format ingest (waves 0–6)
- `Extractor` protocol (`corpus_forge/extractors/base.py`) + `ExtractorRegistry`
  with a per-extension lookup table and a second-pass `supported_filenames`
  fallback for the extension-less long tail (`Makefile`, `Dockerfile`, …).
- Seven document extractors landed under `corpus_forge/extractors/`:
  `PdfDigitalExtractor` (pymupdf4llm rag-helper), `HtmlExtractor`
  (readability-lxml + markdownify), `EpubExtractor` (ebooklib), `OfficeExtractor`
  (Docling for `.docx`/`.pptx`/`.xlsx`), `NotebookExtractor` (jupytext),
  `CsvExtractor` (pandas → markdown table, row-capped), and a 45+ extension
  `CodeExtractor` (tree-sitter-language-pack).
- `PassthroughMarkdownExtractor`, `PlainTextExtractor`, `StructuredDataExtractor`
  (`.json`/`.yaml`/`.toml`), `SubtitleExtractor` (`.srt`/`.vtt`).
- `FilesystemSource` — heterogeneous-tree walker that dispatches every file
  through the extractor registry. New `[[datasets.sources]]` plugin `filesystem`.
- `ChunkerDispatcher` — picks the per-document chunker from each
  `ExtractedDocument.metadata.chunker_hint`. `CodeChunker` (`chunkers/code.py`):
  tree-sitter AST walk with size-bounding + overlap, falling back to a brace-/
  blank-line byte chunker when the grammar is unavailable.
- New `[code]`, `[multi-format]`, and `[ocr]` optional extras. License posture
  documented in the README's "Distribution / licensing" section — `[multi-format]`
  AGPL-binds; `[code]` and `[ocr]` stay permissive.
- **P1 — Vision/OCR (waves 4–6).** `VLMBackend` protocol + `OllamaVLM`
  (local, `qwen2.5vl:7b` default) + `MistralOCR` (remote, `mistral-ocr-2503`).
  `PdfDigitalExtractor` Tier-1 → Tier-2 escalation on sparse text layers;
  `ImageExtractor` for `.png`/`.jpg`/`.tif`/`.bmp`/`.webp`/`.heic`. Failure
  ladder: missing poppler → ERROR + Tier-1 fallback; VLM timeout on a page →
  placeholder, remaining pages continue. Documented in `docs/architecture.md`.

#### Phase E — Document classification (rule → LLM chain)
- New `Classifier` protocol (`corpus_forge/classifiers/`) + `ClassifierRegistry`
  with ordered dispatch and the `tuple[str, ClassLabel] | None` return shape
  that distinguishes `classifier:rule` from `classifier:llm` on
  `document_labels.source`.
- `RuleBasedClassifier` (stdlib, microseconds/doc) — fast path covering the
  9-value taxonomy (`code`, `chat`, `book`, `textbook`, `paper`, `article`,
  `reference`, `note`, `other`) via format-label + path + body heuristics.
- `LLMClassifier` (Ollama `qwen2.5:7b-instruct` default; `POST /api/generate`
  with `format=json`, head+tail excerpt). Local-or-remote URL via
  `classifier.llm_url`; same swap-the-URL principle as `vlm.ollama_url`.
- New `corpus-forge classify` CLI with cost-guard preflight, `--dry-run`,
  `--limit`, `--json`, `--reclassify`, and `--classifier <name>` (bypass
  the chain).
- Alembic `0010_document_label_confidence` adds the `confidence REAL`
  column to `document_labels`.

#### Phase F — Content-defined chunking (FastCDC)
- New `CDCChunker` (`corpus_forge/chunkers/cdc.py`) — pure-Python FastCDC
  rolling-hash boundaries (MIT). Replaces positional slicing for prose classes
  (`book`/`textbook`/`paper`/`article`/`note`/`other`). Small edits ripple ≤ 2-3
  chunks, proven via Hypothesis property tests.
- `ChunkerDispatcher.for_class` — class-mapped chunker resolution
  (`code → CodeChunker`, `chat → ConversationChunker`,
  `reference → PassthroughChunker`, everything-else → `CDCChunker`).
- New `corpus-forge rechunk` CLI — walks classified documents and re-runs the
  chunker pass. Idempotent on chunk-text + metadata signature; preserves
  embeddings on identical chunks via `StorageBackend.replace_document_chunks`.
- New `StorageBackend.get_document_chunk_texts` + `get_document_chunk_metadatas`
  helpers powering the rechunk idempotency check.
- `[multi-format]` extra picks up `fastcdc>=1.6`.

#### Phase G — Whisper transcription + multi-modal embeddings
- **P0 — Whisper.** `WhisperBackend` protocol + `LocalWhisper` (faster-whisper
  in-process, tiny/base/small/medium/large) + `RemoteWhisper` (OpenAI-compatible
  `/audio/transcriptions`; works against OpenAI, Groq, Replicate, self-hosted
  whisper.cpp via HTTP). `AudioExtractor` for `.mp3`/`.wav`/`.m4a`/`.ogg`/`.flac`;
  `VideoExtractor` for `.mp4`/`.mov`/`.webm`/`.mkv`/`.avi` (uses imageio-ffmpeg).
  `[whisper]` extra; defaults to `backend = "none"` so audio/video files are
  silently skipped pre-opt-in.
- **P1 — Multi-modal embeddings.** New `MultiModalEmbedder` protocol
  (`corpus_forge/embedders/multimodal.py`) — distinct seam from the text
  `Embedder` so both keep clean APIs. `ClipLocalEmbedder` (sentence-transformers
  `clip-ViT-B-32`, 512 d; `jina-clip-v2` 1024 d also accepted) and
  `ClipRemoteEmbedder` (OpenAI-compatible `/v1/embeddings` with base64 data-URL
  image input).
- `corpus-forge embed --image` routes through `backfill_image_embedder`.
  Resolves image bytes from `metadata.image_b64` → `metadata.image_path` →
  the document's `filesystem://` URI in order.
- Alembic `0011_image_embeddings` adds `embedders.image BOOLEAN`; the dynamic
  `image_embeddings_<name>` per-embedder family mirrors the text
  `embeddings_<name>` family.

#### Phase H — Qwen3.6-35B-A3B code-chunk enrichment
- New `CodeEnricher` protocol (`corpus_forge/enrichers/`) + `CodeChunkEnrichment`
  dataclass (`{docstring, summary, symbols[], model, confidence}`) + `EnricherRegistry`.
- Two concrete backends to satisfy the local-or-remote URL principle:
  `QwenCoderLocal` (local Ollama `/api/generate`) and `QwenCoderRemote` —
  speaks either the Ollama shape OR OpenAI chat-completions
  (`response_format=json_object`) via `remote_api_shape`. Bearer auth
  optional on Ollama, required on OpenAI.
- New `corpus-forge enrich` CLI — walks `class=code` chunks only,
  cost-guard preflight, idempotent on `chunks.metadata.enrichment.model`.
  `--backend qwen-local|qwen-remote`, `--reclassify-on-model-change`,
  `--dataset`, `--limit`, `--dry-run`, `--json`.
- `iter_code_chunks_for_enrichment(model_tag)` + `update_chunk_enrichment`
  on both Postgres (`jsonb_set`) and SQLite (read-modify-write) backends.
- Default `[code_enricher].backend = "none"` keeps existing configs untouched.

### Changed

- Configuration: every model-client block (`[vlm]`, `[classifier]`, `[whisper]`,
  `[code_enricher]`) now carries explicit local + remote URL fields and rich
  one-comment-per-field documentation in `config.example.toml`. The
  local-or-remote URL principle is documented as a cross-cutting concern in
  `docs/architecture.md` and the README.
- `Config.backend.kind == "sqlite"` is now validated against multi-host sync
  (`Config.validate_sync_gate`) — `sync_enabled = true` on any dataset is
  rejected at config-load time so the failure is at startup, not on the first
  write.

## [0.1.0b1] - 2026-05-12

First beta release. The project is now feature-complete for the
single-host single-developer workflow described in the README and is
ready for external review.

### Added

#### Phase B — SQLite backend
- `corpus_forge/backends/sqlite_backend.py` — full SQLite + `sqlite-vec`
  storage backend, single-host only (no advisory locks, no cross-host
  sync).
- New `[sqlite]` optional install extra.
- Config-load validation rejects `sync_enabled = true` when
  `backend.kind = "sqlite"` so the failure surface is at startup, not
  on the first write.
- Schema migrations apply identically on PostgreSQL and SQLite (per-
  embedder vector tables, JSON metadata columns, content-hash dedup).

#### Phases CI-1 / CI-2 / CI-3 — release-ready CI/CD
- `.github/workflows/ci.yml` — `workflow_call`-able, `actionlint`
  gate, full lint + format + typecheck + parallel pytest with
  per-test timeout + coverage gate ≥ 85%.
- 3-OS × 3-Python matrix (ubuntu-22.04 / macos-14 / windows-2022 × py
  3.11 / 3.12 / 3.13) with `continue-on-error` on the still-landing
  py3.13 macOS-arm64 + Windows cells.
- `.github/workflows/integration.yml` — Linux + macOS Docker-backed
  pgvector integration runs.
- `.github/workflows/nightly.yml` — full matrix + `HYPOTHESIS_PROFILE=
  nightly` on a cron.
- Apache-2.0 license, PyPI classifiers, `py.typed` marker, per-OS
  installer scripts under `scripts/{linux,macos}/`.

#### Phases R1..R5 — retrieval + MCP surface
- `corpus_forge/retrieval/` — vector search + reranker over
  `chunks.text` (BGE reranker v2-m3 default).
- New `[retrieval]`, `[rerank]`, `[mcp]`, and `[eval]` extras.
- `corpus-forge search` and `corpus-forge mcp serve` CLI commands.
- In-process MCP server (`corpus_forge/mcp/server.py`) exposes
  `search`, `get_chunk`, `list_datasets` tools over stdio.
- Bundled retrieval-evaluation harness (`corpus-forge eval retrieval`)
  with a self-curated gold set under
  `corpus_forge/eval/datasets/forge_self.*`.

#### Phase CS — Claude integration drop-ins
- `examples/mcp-config/` — drop-in `.mcp.json` for Claude Code and
  `claude-desktop.json` for Claude Desktop.
- `.claude/skills/corpus-forge-search/SKILL.md` — Claude Code skill
  that surfaces `mcp__corpus-forge__*` tools with a citation-disciplined
  playbook.
- `.claude/agents/corpus-forge-researcher.md` — Agent SDK subagent
  scoped to the three MCP tools.
- `docs/claude-integration.md` — end-to-end walkthrough.
- Contract test (`tests/smoke/test_skill_tool_contract.py`) pins the
  `mcp__corpus-forge__<tool>` prefix against the server's live
  `tools/list` reply.

#### Phase BR — beta packaging
- `assets/banner.svg`, `assets/banner-dark.svg`, `assets/logo.svg` —
  anvil/forge + dataflow brand assets used in the README banner block.
- `CHANGELOG.md` (this file), `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`
  (Contributor Covenant 2.1), `SECURITY.md`.
- `.github/ISSUE_TEMPLATE/{bug_report,feature_request,config}.yml`,
  `.github/PULL_REQUEST_TEMPLATE.md`, `.github/dependabot.yml`
  (pip + github-actions weekly), `.github/FUNDING.yml`.
- `cliff.toml` — `git-cliff` config used by the release workflow.
- `.github/workflows/release.yml` — tag-triggered release pipeline
  (`gate` → `build` → `publish`); `gate` reuses `ci.yml` via
  `workflow_call`; `publish` uses `softprops/action-gh-release@v2`
  with `prerelease` auto-derived from beta / RC tags.
- Full README rewrite — banner block, shields.io badge row, expanded
  Agent integration (MCP) section, and a slimmer install / quickstart
  flow.

### Changed

- README condensed and reorganised from ~430 lines to ~250 lines; the
  three install scripts are in collapsible `<details>` blocks; the
  HF-export "what you get" section is promoted toward the top.
- The compact 3-bullet MCP pointer landed in CS is replaced by a full
  Agent-integration section with Prerequisites + Wire-up snippets.

### Security

- `SECURITY.md` lists `0.1.x` as the supported beta line and
  `evan@jwo3.io` as the vulnerability-reporting contact.

[Unreleased]: https://github.com/ulmentflam/corpus-forge/compare/v0.1.0b1...HEAD
[0.1.0b1]: https://github.com/ulmentflam/corpus-forge/releases/tag/v0.1.0b1
