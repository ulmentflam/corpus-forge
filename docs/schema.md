# Schema Reference

Authoritative database reference for corpus-forge as it ships today (Phase H tip).
The schema is defined by Alembic revisions under `corpus_forge/alembic/versions/`
and applied identically on Postgres and SQLite by a per-revision dialect switch
inside each migration's `upgrade()` function.

- **Postgres** uses a dedicated `corpus` schema with qualified table names
  (`corpus.documents`, `corpus.chunks`, …) and `pgvector` for ANN search.
- **SQLite** drops the schema qualifier and uses `sqlite-vec`'s `vec0` virtual
  tables (with a plain-BLOB fallback when `[sqlite]` is not installed).

Apply migrations with `corpus-forge migrate`; see `migrate history` for the
currently applied head.

## Core tables (revision `0001_core`)

| Table | What it holds | Key columns |
|---|---|---|
| `datasets` | Top-level collections (one per `[[datasets]]` block in `config.toml`). | `id`, `name UNIQUE`, `kind ∈ {"text","chat"}`, `description`, `created_at` |
| `sources` | Per-host registration of a source plugin against a dataset. | `id`, `dataset_id`, `plugin`, `identity`, `host`, `config JSONB`, `last_seen_at` |
| `documents` | Text-side ingest unit (one row per Markdown / PDF / HTML / EPUB / Office / notebook / CSV / code / image / audio / video file). | `id`, `dataset_id`, `source_uri`, `content_hash`, `title`, `text`, `modified_at`, `metadata JSONB` |
| `conversations` | Chat-side ingest unit (one row per Claude Code / OpenCode / generic-JSONL session). | `id`, `dataset_id`, `source_uri`, `external_id`, `title`, `started_at`, `ended_at`, `message_count`, `content_hash`, `metadata JSONB` |
| `messages` | Individual turns within a conversation. | `id`, `conversation_id`, `external_uuid`, `parent_uuid`, `turn_index`, `role`, `content`, `tool_calls JSONB`, `tool_results JSONB`, `ts`, `metadata JSONB` |
| `chunks` | Atomic embedding unit. Belongs to exactly one of `(document_id, conversation_id)` via a Postgres `CHECK` (upheld in code on SQLite). | `id`, `document_id`/`conversation_id`/`message_id`, `chunk_index`, `text`, `heading`, `role`, `token_count`, `metadata JSONB` |
| `embedders` | Registry of embedder configurations. One row per `[[embedders]]` entry. | `id`, `name UNIQUE`, `provider`, `model_id`, `dimension`, `normalized`, `distance`, `active`, `table_name UNIQUE`, `config JSONB`, `image BOOLEAN` (added in `0011`) |
| `labels` | Two-column taxonomy of `(namespace, value)` pairs (e.g. `("class","code")`, `("format","markdown")`). | `id`, `namespace`, `value`, `UNIQUE (namespace, value)` |
| `document_labels` | Many-to-many between `documents` and `labels`. | `document_id`, `label_id`, `source`, `confidence REAL` (added in `0010`) |
| `chunk_labels` | Many-to-many between `chunks` and `labels`. | `chunk_id`, `label_id`, `confidence REAL`, `source` |
| `conversation_labels` | Many-to-many between `conversations` and `labels`. | `conversation_id`, `label_id`, `source` |

## Dynamic per-embedder tables

Two table families are created at runtime by the backend's
`register_embedder` / `register_multimodal_embedder` helpers (NOT by Alembic):

- `embeddings_<name>` — one row per `chunk × text-embedder`. Created on first
  `[[embedders]]` registration. Postgres uses a `vector(N)` column with an HNSW
  cosine index; SQLite uses a `vec0` virtual table when `sqlite-vec` is
  installed, otherwise a plain `BLOB` table (no ANN search).
- `image_embeddings_<name>` — added in Phase G P1 (revision `0011`). Same shape
  as the text family but populated by `corpus-forge embed -e <name> --image`
  routes via the multi-modal embedder protocol. Phase G P1 ships the
  `clip-ViT-B-32` local backend (512 d) and a `ClipRemoteEmbedder` against any
  OpenAI-compatible `/v1/embeddings` endpoint.

The naming uses the embedder name with `[^A-Za-z0-9_]` replaced by `_`. Adding
a new embedder requires no migration — register it in `config.toml` and re-run
`corpus-forge embed` (or `--image`).

## Metadata columns added across phases

These JSONB columns (TEXT on SQLite) accumulate well-defined keys that callers
read by name. Each is additive — older rows simply lack the key.

| Table.column | Key | Added in | Purpose |
|---|---|---|---|
| `documents.metadata` | `chunker_hint` | Phase D Wave 0 | Picks the per-document chunker (`markdown`, `passthrough`, `code`, `cdc`). |
| `documents.metadata` | `tier`, `pages_ocr_count`, `ocr_backend`, `ocr_model`, `ocr_escalation_attempted`, `ocr_escalation_failed_reason`, `sparse_text_layer` | Phase D Wave 5 | PDF Tier 1 / Tier 2 OCR provenance. |
| `chunks.metadata` | `kind`, `name`, `language`, `byte_range` | Phase D Wave 0 (`CodeChunker`) | AST node identity for code chunks. |
| `chunks.metadata` | `cdc_fingerprint`, `byte_range` | Phase F (`CDCChunker`) | FastCDC rolling-hash fingerprint of the chunk bytes; future cross-doc dedup signal. |
| `chunks.metadata` | `enrichment` | Phase H | LLM-synthesised `{docstring, summary, symbols[], model, confidence}` for `class=code` chunks (see Phase H below). |
| `chunks.metadata` | `image_path`, `image_b64` | Phase G P1 | Source pointer for image chunks; consumed by `corpus-forge embed --image`. |

## Phase C — Chunk-level content hash + cross-host sync

Revision `0002_chunk_content_hash` added `chunks.content_hash TEXT` with a
B-tree index. `upsert_document` stamps the hash on insert; when a document is
re-ingested the backend copies forward existing embeddings for any chunk whose
content_hash matches a prior row — eliminating wasted re-embedding work
(see `_copy_reusable_embeddings`).

Revision `0004_sync` added the cross-host sync surface:

- `document_revisions` table — append-only revision log keyed on
  `(document_id, revision_number)`. Each row records the author host, parent
  revision, content hash, and tombstone flag.
- `documents.tombstoned_at TIMESTAMPTZ` — soft-delete marker honoured by the
  pull pipeline.
- `sources.last_pulled_revision_id BIGINT` — high-water mark for the puller.
- `sources.sync_enabled BOOLEAN DEFAULT FALSE` — per-source feature flag.
  Rejected at config-load by `Config.validate_sync_gate` when paired with
  `backend.kind = "sqlite"`.

## Full-text search + retrieval (revisions `0005_fts`, `0003_views`)

- `0003_views` creates the `corpus_text_export` and `corpus_chat_export`
  Postgres views consumed by `corpus-forge export`.
- `0005_fts` adds the lexical half of hybrid retrieval. On Postgres, a generated
  `tsvector` column + GIN index on `chunks.text`. On SQLite, a `chunks_fts`
  FTS5 virtual table.

## Phase R — MCP + feedback (revisions `0006`–`0009`)

- `0006_writes_and_feedback` adds description columns, the `mcp_audit` table
  (audit log of MCP tool invocations), and the original `feedback` table.
- `0007_chat_templates` adds the `chat_templates` table used by
  `corpus-forge export chat`.
- `0008_feedback_sessions` adds `feedback_sessions` + `feedback_events`
  tables for in-session feedback capture.
- `0009_feedback_host_default` rebuilds the SQLite `feedback` table with a
  `host TEXT NOT NULL DEFAULT 'localhost'` so bare inserts succeed without an
  explicit host (Postgres already handles this in the application layer via
  `socket.gethostname()`).

## Phase E + G — Classification + multi-modal (revisions `0010`, `0011`)

- `0010_document_label_confidence` adds an optional `document_labels.confidence
  REAL` column so document-level classifier outputs (Phase E) mirror the
  existing `chunk_labels.confidence` shape. NULL-default preserves backward
  compatibility.
- `0011_image_embeddings` adds the `embedders.image BOOLEAN` column. The
  per-embedder `image_embeddings_<name>` tables themselves are created at
  runtime (same pattern as the text `embeddings_<name>` family).

## Phase O Wave 1 — EDA / quality signals (revision `0012`)

- `0012_analyze_signals` adds two append-friendly tables that back the new
  `corpus-forge analyze` surface:
  - `chunk_quality_signals(chunk_id, signal_name, signal_value, source,
    computed_at)` — per-chunk learned-quality readouts, joined by the curation
    selector when the table is populated (otherwise the selector falls back to
    its existing 4-weight scheme).
  - `near_duplicate_clusters(cluster_id, chunk_id, similarity, method,
    computed_at)` — MinHash LSH groupings produced by `corpus-forge analyze
    duplicates`.
- Both `chunk_id` FKs cascade on delete so removing a chunk reaps its analyze
  rows automatically. Forward-only downgrade per project convention.

## Phase P Wave 1 — Search sessions (revision `0013`)

- `0013_search_sessions` adds two tables for search-session telemetry:
  - `search_sessions(query, dataset_id, started_at, client, host)` — one row
    per `HybridRetriever.search()` call.  `dataset_id` FKs to `datasets(id)`
    ON DELETE CASCADE.  A composite index on `(dataset_id, started_at)`
    supports time-windowed per-dataset queries.
  - `search_result_events(session_id, chunk_id, signal, value, source,
    created_at, replacement_chunk_id)` — one row per result item returned in
    a session.  `session_id` and `chunk_id` FKs both cascade on delete.
    `replacement_chunk_id` is a nullable weak FK to `chunks(id)` (set when
    a signal records a curation suggestion; the event is retained even if the
    referenced replacement chunk is later removed).
- Forward-only downgrade per project convention.

## Phase Q Wave 1 — SDFT demonstrations (revision `0014`)

- `0014_sdft_demonstrations` adds one table for SDFT (Supervised Demo
  Fine-Tuning) capture:
  - `sdft_demonstrations(dataset_id, query, student_messages, teacher_messages,
    target, source, trace_id, content_hash, created_at)` — one row per captured
    teacher→student demonstration pair.  `dataset_id` FKs to `datasets(id)`
    ON DELETE CASCADE.  `content_hash` has a UNIQUE constraint so duplicate
    demonstrations are deduplicated via `INSERT ... ON CONFLICT DO NOTHING`.
    `student_messages` and `teacher_messages` are JSONB on Postgres, TEXT on
    SQLite (JSON serialised).  `source` must be one of the `SDFTSource` enum
    values: `curation_commit`, `rate_search_result`, `record_demonstration`,
    `cli_feedback`, `claude_code`, `gemini`, `opencode`, `codex`.
  - Indexes: `(dataset_id, source)` for per-dataset source queries;
    `(trace_id)` for cross-system trace lookup.
- Capture hooks fire automatically from `commit_curation` (description change →
  `source="curation_commit"`) and `rate_search_result` (thumbs_down +
  replacement → `source="rate_search_result"`).
- Forward-only downgrade per project convention.

## Indexing strategy

| Query path | Index |
|---|---|
| Content-hash dedup on `documents` / `conversations` / `chunks` | B-tree on `content_hash` (added `0001` for docs/convs, `0002` for chunks). |
| `embeddings_<name>` dense search (Postgres) | HNSW cosine via `pgvector`, one per dynamic table. |
| `embeddings_<name>` dense search (SQLite) | `vec0` virtual table when `sqlite-vec` is installed; plain BLOB otherwise (no ANN; lexical only). |
| `chunks.text` lexical | Postgres: `tsvector` GIN. SQLite: `chunks_fts` FTS5. |
| `iter_documents_for_classification` (Phase E) | JOIN path from `documents` → `document_labels` filtered on `(namespace, value)='class'` and `source LIKE 'classifier:%'`; relies on existing PK + label-id index. |
| `iter_code_chunks_for_enrichment` (Phase H) | JOIN path from `chunks` → `documents` → `document_labels` filtered on `class=code`, with a JSON-field predicate against `metadata->'enrichment'->>'model'` to elide already-enriched chunks (idempotency). |
| Cross-host sync polling | `document_revisions(document_id)` and `document_revisions(parent_revision_id)` (both added `0004_sync`). |

## SQLite dialect adaptations

Every Alembic migration dispatches on `op.get_bind().dialect.name` so a single
revision file drives both backends. The mechanical translations applied to the
SQLite half:

| Postgres | SQLite |
| --- | --- |
| `BIGSERIAL PRIMARY KEY` | `INTEGER PRIMARY KEY` (aliases the rowid; matches the legacy `_execute` AUTOINCREMENT strip). |
| `BIGINT` foreign-key columns | `INTEGER` (SQLite collapses integer affinities). |
| `JSONB` columns and `JSONB NOT NULL DEFAULT '{}'::jsonb` | `TEXT` columns with default `'{}'`; values are produced via `json.dumps(...)` on the Python side. |
| `TIMESTAMPTZ` (with `NOW()` defaults) | `TEXT` storing ISO-8601 UTC strings; defaults use `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')`. |
| `BOOLEAN` (`TRUE` / `FALSE`) | `INTEGER` (`1` / `0`). |
| `vector(N)` (`pgvector`) with `USING hnsw` index | Per-embedder `vec0` virtual table (`embeddings_<name>`) when `sqlite-vec` is installed; otherwise a plain table with `embedding BLOB NOT NULL` (no ANN search). See `SQLiteBackend.register_embedder`. |
| `pg_try_advisory_lock(key)` | Per-instance `threading.Lock` plus `BEGIN IMMEDIATE` on a dedicated connection with exponential back-off (see `SQLiteBackend.lock_source`); the `key` is accepted for protocol parity but ignored. |
| `RETURNING id` | Supported on both — the project's SQLite floor is 3.35. |
| Schema prefix `corpus.<table>` | No schema namespacing; tables are referenced unqualified. |
| FK enforcement | Always on in Postgres; in SQLite requires `PRAGMA foreign_keys = ON` per connection, which `_get_connection` / `_open_connection` set automatically. |
| `IF NOT EXISTS` guards on every DDL statement | Preserved verbatim (idempotent migrations). |

Two further details worth flagging:

- `ALTER TABLE … ADD COLUMN IF NOT EXISTS …` is not valid SQLite. The revisions
  that need to add columns (`0002`, `0004`, `0010`, `0011`) use a `try/except`
  around an `ADD COLUMN` and swallow the `duplicate column name` error so
  re-running a migration stays idempotent.
- The Postgres-only `chunks` XOR check (`(document_id IS NOT NULL)::int +
  (conversation_id IS NOT NULL)::int = 1`) is dropped in the SQLite branch; the
  XOR invariant is upheld by the calling code in `upsert_document` /
  `upsert_conversation`.

## Migration log

Apply with `corpus-forge migrate` (idempotent). Each revision file lives at
`corpus_forge/alembic/versions/<revision>.py` and contains a dialect-dispatching
`upgrade()`.

| Revision | One-line purpose |
|---|---|
| `0001_core` | All Phase A core tables (datasets, sources, documents, conversations, messages, chunks, embedders, labels, document_labels, chunk_labels, conversation_labels). |
| `0002_chunk_content_hash` | Adds `chunks.content_hash` (Phase C P0) — unlocks chunk-level embedding reuse on document re-ingest. |
| `0003_views` | Creates `corpus_text_export` + `corpus_chat_export` views (Postgres-only; SQLite generates them inline at export time). |
| `0004_sync` | `document_revisions` table + `documents.tombstoned_at` + `sources.last_pulled_revision_id` + `sources.sync_enabled` (Phase C P1 cross-host sync). |
| `0005_fts` | Full-text search half of hybrid retrieval — Postgres `tsvector`+GIN; SQLite FTS5 virtual table. |
| `0006_writes_and_feedback` | Description columns, `mcp_audit` audit log, original `feedback` table (Phase R / MCP). |
| `0007_chat_templates` | `chat_templates` table consumed by `corpus-forge export chat`. |
| `0008_feedback_sessions` | `feedback_sessions` + `feedback_events` for in-session feedback capture. |
| `0009_feedback_host_default` | SQLite-only: rebuild `feedback` with `host TEXT NOT NULL DEFAULT 'localhost'` so bare inserts succeed. |
| `0010_document_label_confidence` | Adds `document_labels.confidence REAL` (Phase E classifier output). |
| `0011_image_embeddings` | Adds `embedders.image BOOLEAN` column + reserves the dynamic `image_embeddings_<name>` family namespace (Phase G P1). |
| `0012_analyze_signals` | Adds `chunk_quality_signals` (per-chunk learned-quality readouts) + `near_duplicate_clusters` (MinHash LSH groupings) for Phase O EDA / cleaning. Both tables FK to `chunks(id)` with `ON DELETE CASCADE`. |
| `0013_search_sessions` | Adds `search_sessions` (one row per search call, FK to `datasets(id)` ON DELETE CASCADE) + `search_result_events` (per-result telemetry, FK to `search_sessions(id)` and `chunks(id)` ON DELETE CASCADE) for Phase P search-session tracking. |
| `0014_sdft_demonstrations` | Adds `sdft_demonstrations` (one row per captured teacher→student demonstration) for Phase Q Wave 1 SDFT (Supervised Demo Fine-Tuning). Stores query context, prior model output, curated correction prompt, corrected target, source signal, optional trace_id, and content_hash for dedup. |
| `0015_halfvec_index_for_wide_embedders` | Adapts every `embeddings_<name>` table's HNSW index to match its embedder's dimension. `dim <= 2000` keeps `USING hnsw (embedding vector_cosine_ops)`; `dim > 2000` rebuilds as `USING hnsw ((embedding::halfvec(min(dim,4000))) halfvec_cosine_ops)` because pgvector's standard `vector` HNSW caps at 2000 and `halfvec` HNSW caps at 4000. Idempotent — re-running is a no-op when indexes already match. Postgres-only; SQLite (sqlite-vec) is a no-op. |

## Design rationale

### Per-embedder tables (instead of one giant table)

The text + image embedding stores each create a dedicated table per registered
embedder (`embeddings_qwen3_8b`, `embeddings_openai_3l`, `image_embeddings_clip_local`, …).
This:

- Keeps each `pgvector` column at the embedder's fixed dimension (HNSW indexes
  require fixed-dimension columns; padding to MAX_DIM would waste disk).
- Isolates different embedding spaces — cosine over `text-embedding-3-large`
  shares no math with cosine over `Qwen3-Embedding-8B`.
- Makes backfill cheap: adding an embedder creates a new table, leaves the
  existing ones untouched, and `corpus-forge embed` only writes the new one.

### Content addressing throughout

Every "primary" row carries a SHA-256 content hash:

- `documents.content_hash` / `conversations.content_hash` — coarse dedup,
  cross-host sync key.
- `chunks.content_hash` — fine dedup; the Phase C embedding-reuse path keys
  on this column.

### HF-Datasets compatibility

The `corpus_text_export` and `corpus_chat_export` views project exactly the
columns the HuggingFace `Dataset.from_dict()` constructor expects, including the
ShareGPT-shaped `messages` array on the chat view. Labels are preserved as
arrays so downstream filtering ("give me only `class=code` rows") is a single
predicate.
