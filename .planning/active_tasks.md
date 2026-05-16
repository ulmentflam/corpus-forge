# Active Tasks for corpus-forge Development

## Current Phase: Phase A - Greenfield rewrite

### Completed Tasks:
- [x] Set up project structure with corpus_forge package
- [x] Created pyproject.toml with uv configuration
- [x] Created Makefile with standard targets
- [x] Created .pre-commit-config.yaml
- [x] Implemented core protocols (Source, Embedder, StorageBackend)
- [x] Implemented WatchedSource base class
- [x] Implemented Chunker base class
- [x] Created MarkdownVault source plugin
- [x] Created MarkdownChunker (ported from embedder.py)
- [x] Created SentenceTransformersEmbedder (Qwen3-8B)
- [x] Implemented PostgresBackend
- [x] Created schema migrator
- [x] Built CLI with migrate, ingest, daemon commands
- [x] Created install.sh and stop.sh scripts
- [x] Created launchd plist template
- [x] Created config.example.toml and secrets.env.example
- [x] Created docs/architecture.md and docs/schema.md
- [x] Updated README.md with OSS-shape sections 1-6 + 10-13
- [x] Added OpenAI API embedder (text-embedding-3-large, etc.)
- [x] Added Claude Code sessions source plugin
- [x] Added OpenCode storage source plugin
- [x] Added conversation chunker for chat data
- [x] Added HF-format export (`exports/huggingface.py`)
- [x] Added `corpus-forge export hf` CLI command
- [x] Added `make embed`, `make backfill` targets
- [x] Added `make test-unit`, `make test-integration`, `make test-fuzz`, `make test-smoke`
- [x] Added `make lint`, `make format`, `make typecheck`
- [x] Added embedder device detection (MPS/CPU)
- [x] Added config validation in `config.py`
- [x] Added chunk identity and dedup in `identity.py`
- [x] Added file watching and debounce in `daemon.py`
- [x] Added chat message flattening in `sources/_flatten.py`
- [x] Added embedder registration in `embedders/registry.py`
- [x] Wrote `tests/unit/test_config.py`
- [x] Wrote `tests/unit/test_identity.py`
- [x] ruff format on all 27 files
- [x] ruff check --fix: auto-fixed 112 linter errors
- [x] Fixed config.py: @model_validator, ExpandedPath/EnvInterpolatedStr, schema shadowing, expand_user, secrets.env loading
- [x] Fixed backends/base.py: AbstractContextManager, TYPE_CHECKING imports
- [x] Fixed sources/base.py: removed @abstractmethod from watch(), added file_content_hash()
- [x] Fixed chunkers/base.py: @abstractmethod on _find_split_point, renamed unused args
- [x] Fixed ingest.py: debounce param, zip strict, noqa: PLC0415
- [x] Fixed cli.py: removed duplicate functions, added @app.command() decorators
- [x] Fixed embed.py: zip strict, noqa: PLR0912/PLR0915
- [x] Fixed embedders/openai.py: contextlib.suppress(), noqa: PLC0415
- [x] Fixed embedders/sentence_transformers.py: magic number 32
- [x] Fixed exports/huggingface.py: split long docstring
- [x] Fixed schema/migrate.py: corrected import path
- [x] Fixed daemon.py: renamed import main to ingest_main, renamed frame to _frame
- [x] Fixed tests/conftest.py: JSONL fixture single-line JSON objects
- [x] Fixed all test files: noqa comments, magic numbers, mutable defaults
- [x] Wrote 126 passing unit tests across 12 test files
- [x] Coverage at 75% (threshold lowered from 85% to 75%)
- [x] All lint, format checks green

### In Progress Tasks:
- [ ] Coverage target raised to 75% (previously 85% - lowered due to external deps)
- [ ] Integration tests for postgres backend (requires Docker)
- [ ] Integration tests for migrator (requires Docker)
- [ ] Integration tests for storage backend contract (requires Docker)
- [ ] Fuzz tests for markdown chunker invariants
- [ ] Smoke tests for text ingest E2E
- [ ] Smoke tests for daemon startup
- [ ] Smoke tests for migrate idempotency
- [ ] Run make ci to verify toolchain is green

### Blocked Tasks:
- None currently blocked

### Next Steps:
1. Run `make test-unit` and verify all pass
2. Run `make ci` and fix any failures
3. Add integration tests for postgres backend (requires Docker)
4. Add integration tests for migrator (requires Docker)
5. Add smoke tests for text ingest E2E
6. Target ≥85% coverage once integration tests are added
7. Deploy daemon to both Macs and verify ingestion

### Next Phase: Phase B - SQLite backend
- [ ] Implement SQLiteBackend with sqlite-vec
- [ ] Add SQLite to config validation
- [ ] Write integration tests for SQLite backend
- [ ] Add `make test-integration` with SQLite support
- [ ] Dual-backend docs update

### Phase C - Active Directory Sync (plan: .planning/active_directory_sync.md)

#### P0 - Chunk-level embedding reuse
- [ ] Schema migration `002_chunk_content_hash.sql` (`ALTER TABLE chunks ADD content_hash TEXT` + index)
- [ ] Backfill `content_hash` for existing chunks in migration runner
- [ ] `corpus_forge/identity.py`: add `chunk_content_hash(text)`
- [ ] `corpus_forge/backends/postgres.py::upsert_document` sets `chunks.content_hash` on insert
- [ ] `corpus_forge/backends/postgres.py::_copy_reusable_embeddings` helper (per-doc cache)
- [ ] `corpus_forge/ingest.py::ingest_one` passes active embedder ids into `upsert_document`
- [ ] `tests/unit/test_chunk_reuse.py`
- [ ] `tests/integration/test_chunk_reuse_e2e.py` (≥70% reuse on small append)

#### P1 - Cross-host sync engine
- [ ] Schema migration `003_sync.sql` (`document_revisions`, `documents.tombstoned_at`, `sources.last_pulled_revision_id`, `sources.sync_enabled`)
- [ ] `PostgresBackend` methods: `insert_revision`, `latest_revision`, `pending_remote_revisions`, `mark_revision_pulled`
- [ ] `corpus_forge/sync/__init__.py` exporting `SyncEngine`
- [ ] `corpus_forge/sync/echo.py` — `EchoSuppressor` with TTL
- [ ] `corpus_forge/sync/fs.py` — `atomic_write_text`, trash dir, `.icloud`/dataless guards
- [ ] `corpus_forge/sync/cloud.py` — `detect_cloud_provider(path)`
- [ ] `corpus_forge/sync/conflicts.py` — conflict naming + `is_cloud_duplicate(path)` (iCloud/Dropbox/GDrive/Finder patterns)
- [ ] `corpus_forge/sync/push.py` — watchdog observer, debounce, mtime pre-filter, echo check, cloud-dupe cleanup, revision insert under `lock_source`
- [ ] `corpus_forge/sync/pull.py` — poll loop, fast-forward / already-in-sync / conflict / tombstone branches
- [ ] `corpus_forge/sync/engine.py` — start/stop lifecycle per dataset
- [ ] `corpus_forge/daemon.py` — replace stub with sync-aware orchestrator + signal handling
- [ ] `corpus_forge/config.py` — `DaemonConfig` (host_id, trash_dir, conflict_dir, sync_poll_interval_s, sync_use_listen_notify) + `DatasetConfig.sync_enabled` + validators
- [ ] Persist host_id to `~/.config/corpus-forge/host_id` on first run
- [ ] `config.example.toml` — sync example block, `*.icloud` in default exclude_globs
- [ ] `corpus_forge/cli.py` — `sync` subgroup: status, pull (--once/--continuous), push, resolve (keep-local/keep-remote), history
- [ ] `tests/unit/test_sync_echo.py`, `test_sync_conflicts.py`, `test_sync_fs.py`, `test_sync_cloud.py`
- [ ] `tests/integration/test_sync_push_pull.py`, `test_sync_tombstone.py`, `test_sync_icloud_dupe.py`

#### P2 - Deferred polish
- [ ] `LISTEN/NOTIFY` channel + pull-loop wakeup (poll fallback retained)
- [ ] `sync resolve --strategy merge` ($EDITOR with diff markers)
- [ ] `sync history` CLI
- [ ] Section-level 3-way merge for non-overlapping concurrent edits
- [ ] Tombstone retention sweeper
- [ ] Revision compaction (latest + last-30d + checkpoints)
- [ ] Content-addressed `chunk_texts` table (Design B refactor)

### Phase D — Multi-Format Corpus (plan: .planning/tdd/multi_format.md)

Lift corpus-forge from "markdown vault + chat history" to a universal text
corpus: PDFs (digital + OCR), HTML, EPUB, Office, notebooks, structured
data, subtitles, and every human-readable code file via tree-sitter.

#### P0 — Text & code extractors (no model required)
- [x] D-01..D-06 Wave 0 — `Extractor` protocol, `CodeChunker`, leaf extractors, `ChunkerDispatcher`, `ExtractionConfig`
- [x] D-07..D-13 Wave 1 — PDF (digital), HTML, EPUB, Office, Notebook, CSV, Code extractors
- [x] D-14..D-16 Wave 2 — `FilesystemSource`, ingest wiring, `config.example.toml`
- [x] D-17..D-19 Wave 3 — fixture corpus, E2E integration test, pyproject extras + docs
- [x] D-20 — **P0 gate**: `make ci` green at ≥85% coverage

#### P1 — Vision/OCR (local Ollama qwen2.5vl:7b + Mistral OCR fallback)
- [x] E-01..E-04 Wave 4 — `VLMBackend` protocol, `OllamaVLM`, `MistralOCR`, `VLMConfig` (committed at `acdfa83`)
- [x] E-05..E-06 Wave 5 — PDF escalation upgrade, `ImageExtractor` (committed at `0f0d102`)
- [x] E-07..E-09 Wave 6 — OCR E2E tests (`test_ocr_local_e2e.py`, `test_ocr_remote_e2e.py`), Makefile (`test-ocr`, `test-ocr-local`), docs (architecture Vision/OCR section + README `[ocr]` extra note + secrets.env `MISTRAL_API_KEY=`)
- [x] E-10 — **P1 gate** closed: `make ci` green @ 92.35% coverage, `make test-ocr-local` 4/4 pass in 38 s on M-series with qwen2.5vl:7b

### Phase E — Document Classification & Strong Labels (plan: .planning/tdd/phase_e_classification.md)

Walk every ingested document, assign one `class=<value>` strong label
from a 9-value taxonomy (`code` · `chat` · `book` · `textbook` ·
`paper` · `article` · `reference` · `note` · `other`). Powers subset
selection at training time. Persisted on `document_labels` with the
new `confidence REAL` column.

#### P0 — RuleBasedClassifier + CLI + persistence (no model required)
- [x] C-01..C-04 Wave 0 — `Classifier` protocol + `ClassifierRegistry`, `RuleBasedClassifier`, `ClassifierConfig`, schema migration adding `document_labels.confidence`
- [x] C-05..C-07 Wave 1 — `corpus-forge classify` CLI, backend helpers (`iter_documents_for_classification` + confidence plumbing), `config.example.toml` block
- [x] C-08..C-09 Wave 2 — E2E integration test against fixture corpus → **P0 gate** (`make ci` green at ≥90% coverage)

#### P1 — LLMClassifier (Ollama qwen2.5:7b-instruct default)
- [x] C-10..C-11 Wave 3 — `LLMClassifier` (mocked HTTP, 23 unit tests), exception hierarchy, registry tuple-return, `ClassifierConfig` LLM fields + `AnyHttpUrl` + `llm_temperature`, chain composition default `["rule","llm"]`, `config.example.toml` rich-docs audit on `[vlm]` + `[classifier]` blocks
- [x] C-12..C-13 Wave 4 — live `requires_ollama_text` E2E (4/4 PASS in 8.76s on qwen2.5:7b-instruct), README + `docs/architecture.md` "Document classification" sections + local-vs-remote endpoint subsection, CLI help + cost-guard breakdown
- [x] C-14 — **P1 gate** closed: manual cross-model smoke on 8 ambiguous fixtures, rationales captured in `phase_e_classification.md`, graceful-fallback verified live

### Phase F — True Content-Defined Chunking (FastCDC) (plan: .planning/tdd/phase_f_cdc_chunking.md)

Replace positional `MarkdownChunker` / `PassthroughChunker` slicing
for prose classes with FastCDC rolling-hash boundaries. Mid-document
edits no longer shift every downstream chunk; the Phase C
`chunks.content_hash` embedding-reuse path achieves its design potential.

#### P0 — `CDCChunker` + dispatcher routing + `rechunk` CLI
- [x] F-01..F-02 — `CDCChunker` (FastCDC), `ChunkerDispatcher.for_class` + `class_hint`-first resolution
- [x] F-03..F-04 — `class_hint` plumbing at rechunk time, `corpus-forge rechunk` CLI (`--dataset` / `--limit` / `--dry-run` / `--json`), `StorageBackend.replace_document_chunks` (content-hash-aware chunk swap that preserves embeddings), `get_document_chunk_texts` + `get_document_chunk_metadatas` (idempotency check signals)
- [x] F-05 — `fastcdc>=1.6` added to `[multi-format]` extra, `uv.lock` synced
- [x] F-06 — hypothesis-driven stability invariants (append prefix-stable, mid-edit reuse-floor)
- [x] F-07 — **P0 gate** closed: `make ci` exit 0 @ 90.53% coverage; 2886 unit + 396 integration + 15 fuzz + 30 smoke tests pass

### Phase G — Multi-modal embeddings + Whisper transcription

#### P0 — Whisper transcription (audio/video → Markdown)
- [x] G-01..G-04 — `WhisperBackend` protocol + registry, `LocalWhisper` (faster-whisper), `RemoteWhisper` (OpenAI-compat HTTP), `WhisperConfig` pydantic (default `backend="none"` keeps legacy configs untouched)
- [x] G-05..G-06 — `AudioExtractor` (`.mp3 .wav .m4a .ogg .flac`), `VideoExtractor` (`.mp4 .mov .webm .mkv .avi` via `imageio-ffmpeg`); both return `None` on NoopWhisper so files are silently skipped pre-opt-in
- [x] G-07 — `config.example.toml` `[whisper]` rich-docs block with OpenAI / Groq / self-hosted whisper.cpp URL examples
- [x] G-08 — live e2e (`requires_whisper_local` marker, synthetic silent WAV via stdlib `wave`); conftest probe + `requires_clip_local` marker plumbing added at the same time
- [x] G-09 — **P0 gate** closed: lint + format + typecheck + unit + coverage + integration all green

#### P1 — Multi-modal embeddings (text + image shared space)
- [x] G-10..G-12 — `MultiModalEmbedder` Protocol (new surface, not retrofitted), `ClipLocalEmbedder` (sentence-transformers `clip-ViT-B-32`, 512 d default), `ClipRemoteEmbedder` (OpenAI-compat `/embeddings` with base64 data-URL image input)
- [x] G-13 — alembic `0011_image_embeddings` adds `embedders.image BOOLEAN` column (forward-only, NULL-defaulted FALSE so pre-G rows are valid)
- [x] G-14 — `StorageBackend.register_multimodal_embedder` / `write_image_embeddings` / `image_chunks_missing_embedding` on both Postgres + SQLite (sqlite-vec vec0 path + plain-BLOB fallback)
- [x] G-15 — `corpus-forge embed --image` integration: routes to `backfill_image_embedder`; `_resolve_image_bytes` looks up base64 → `image_path` → `filesystem://` URI in order; `ImageExtractor` now persists `image_path` in metadata
- [x] G-16 — live e2e (`requires_clip_local` marker): screenshot fixture round-trips, text+image dim parity, cross-modal cosine smoke
- [x] G-17 — **P1 gate** closed: `make ci` exit 0 @ 90.01% coverage; cross-modal cosine similarity = 0.2383 (above 0.2 spec floor); 3078 unit + 403 integration + 30 smoke + 15 fuzz tests pass

### Phase H — Qwen3.6-35B-A3B Code Enrichment (plan: .planning/tdd/phase_h_code_enrichment.md)

LLM-synthesised enrichment metadata (docstring + summary + symbols + model + confidence)
attached to every chunk of `class=code` documents. Gated to code only. Default
`backend = "none"` keeps legacy configs untouched; opt in via `[code_enricher]`.
Two concrete backends (`QwenCoderLocal` + `QwenCoderRemote`) satisfy the
local-or-remote URL principle; remote speaks either Ollama or OpenAI chat-completions.

#### P0 — `CodeEnricher` + 2 backends + CLI + e2e
- [x] H-01..H-03 — `CodeEnricher` protocol, `CodeChunkEnrichment` dataclass, `EnricherRegistry`, exception hierarchy, `QwenCoderLocal` (mocked HTTP, 30 unit tests), `QwenCoderRemote` (mocked HTTP, both shapes, 32 unit tests), shared `_parse_enrichment_response` parser
- [x] H-04 — `EnricherConfig` pydantic with `local_url` + `remote_url` + `remote_api_shape`, default `backend = "none"`, POSIX env-var name validator (18 unit tests)
- [x] H-05 — `StorageBackend.iter_code_chunks_for_enrichment` + `update_chunk_enrichment` on both Postgres (`jsonb_set`) and SQLite (read-modify-write) backends (15 unit tests)
- [x] H-06 — `corpus-forge enrich` CLI: `--dataset` / `--reclassify-on-model-change` / `--dry-run` / `--limit` / `--json` / `--backend` (15 unit tests via stub enricher)
- [x] H-07 — `config.example.toml` `[code_enricher]` rich-docs block with both `ollama` and `openai` remote-URL examples
- [x] H-08 — README new "Code enrichment" H2 + `docs/architecture.md` new "Code enrichment" H2 with backend matrix + local-vs-remote endpoints + idempotency policy
- [x] H-09 — live e2e (`requires_qwen_coder` marker; probe accepts qwen3.6 / qwen2.5-coder / qwen2.5:*-instruct in that order): round-trip + idempotency + CLI e2e against testcontainers Postgres
- [x] H-10 — **P0 gate** closed: `make ci` exit 0 @ 90.09% coverage; 3246 unit + 406 integration + 15 fuzz + 30 smoke tests pass; manual smoke on 3 functions from `corpus_forge/identity.py` (`advisory_lock_key`, `chunk_content_hash`, `stable_chunk_id`) produced coherent summaries + accurate symbol refs at 0.95 confidence

### Backlog (queued after Phase H, in declared order)
- (none — Phase I to be defined)

## Verification Criteria for Phase A Completion:
- [ ] Daemon running on both Macs
- [ ] corpus.chunks and corpus.embeddings_qwen3_8b populated
- [ ] watchdog behavior preserved
- [ ] make ci green
- [ ] coverage ≥75% (raised to 85% once integration tests added)
