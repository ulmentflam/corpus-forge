# Schema Design

## Core Tables

Corpus-forge uses a schema-first approach with all tables in the `corpus` namespace to avoid conflicts with existing schemas.

### Datasets and Sources

- `datasets`: Collections of related data (text vs chat)
- `sources`: Specific instances within datasets (e.g., a particular vault or project)
- `documents`: Individual text files or web pages
- `conversations`: Chat sessions with metadata
- `messages`: Individual messages within conversations

### Chunks and Embeddings

- `chunks`: The atomic unit of embedding (pieces of documents or messages)
- `embedders`: Registry of embedding models
- `embeddings_*`: Per-embedder tables storing vectors (one table per embedding model)

### Labels

- `labels`: Taxonomy of labels (tags, topics, etc.)
- `*_label`: Junction tables linking labels to documents, conversations, and chunks

## Key Design Decisions

### Per-Embedder Tables

Instead of a single embeddings table, corpus-forge creates a separate table for each embedding model:
- `embeddings_qwen3_8b` for Qwen3-Embedding-8B vectors (4096 dimensions)
- `embeddings_openai_3l` for text-embedding-3-large vectors (3072 dimensions)

This approach ensures:
- Correct HNSW indexing (pgvector requires fixed-dimension columns)
- No wasted space (no need for MAX_DIM padding)
- Easy addition of new embedding models
- Isolation between different embedding spaces

### Content Addressing

All content is addressed by cryptographic hash (SHA256):
- Enables deduplication across sources
- Allows detecting when content has changed
- Provides immutable identifiers for chunks

### HF-Datasets Compatibility

The schema is designed to export cleanly to HuggingFace Datasets:
- Views provide exactly the columns needed
- ShareGPT-style chat format supported
- Labels preserved as arrays
- Metadata stored as JSONB for flexibility

## Migration System

Corpus-forge uses a simple numbered migration system:
- Migration files are named `001_name.sql`, `002_name.sql`, etc.
- All migrations are `CREATE IF NOT EXISTS` and re-runnable
- Applied via `corpus-forge migrate` command
- Tracking table `corpus.schema_migrations` records applied migrations

## Indexing Strategy

- Primary keys on all ID columns
- Foreign key indexes for joins
- Content hash indexes for lookup
- HNSW indexes on each embedding table for similarity search
- Composite indexes for uniqueness constraints

## Future Extensibility

The design supports future additions:
- New embedding models via simple config addition
- New sources via protocol implementation
- New backends via protocol implementation
- Additional metadata fields via JSONB columns
- New label taxonomies without schema changes

## SQLite dialect

The SQLite backend (`corpus_forge/backends/sqlite.py`) targets the same logical schema as the Postgres backend, but a small set of mechanical translations are applied to the DDL. Authoritative DDL lives in `corpus_forge/schema/sqlite/001_core.sql`, `002_chunk_content_hash.sql`, and `003_sync.sql`.

| Postgres | SQLite |
| --- | --- |
| `BIGSERIAL PRIMARY KEY` | `INTEGER PRIMARY KEY AUTOINCREMENT` (the migration runner strips `AUTOINCREMENT` at execute time to avoid creating `sqlite_sequence`; `INTEGER PRIMARY KEY` already aliases the rowid) |
| `BIGINT` foreign-key columns | `INTEGER` (SQLite collapses integer affinities) |
| `JSONB` columns and `JSONB NOT NULL DEFAULT '{}'::jsonb` | `TEXT` columns with default `'{}'`; values are produced via `json.dumps(...)` on the Python side |
| `TIMESTAMPTZ` (with `NOW()` defaults) | `TEXT` storing ISO-8601 UTC strings; defaults use `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')` |
| `BOOLEAN` (`TRUE` / `FALSE`) | `INTEGER` (`1` / `0`) |
| `vector(N)` (`pgvector`) with `USING hnsw` index | Per-embedder `vec0` virtual table (`embeddings_<name>`) when `sqlite-vec` is installed; otherwise a plain table with an `embedding BLOB NOT NULL` column (no ANN search). See `SQLiteBackend.register_embedder` |
| `pg_try_advisory_lock(key)` | Per-instance `threading.Lock` plus `BEGIN IMMEDIATE` on a dedicated connection with exponential back-off (see `SQLiteBackend.lock_source`); the `key` is accepted for protocol parity but ignored |
| `RETURNING id` | Supported on both — the project's SQLite floor is 3.35 |
| Schema prefix `corpus.<table>` | No schema namespacing; tables are referenced unqualified |
| FK enforcement | Always on in Postgres; in SQLite requires `PRAGMA foreign_keys = ON` per connection, which `_get_connection` / `_open_connection` set automatically |
| `IF NOT EXISTS` guards on every DDL statement | Preserved verbatim (idempotent migrations) |

Two further details worth flagging:

- `ALTER TABLE … ADD COLUMN IF NOT EXISTS …` is not valid SQLite. The migration runner rewrites it to `ADD COLUMN` and treats the resulting `duplicate column name` error as a no-op, preserving idempotency for `002_chunk_content_hash.sql` and `003_sync.sql`.
- The `chunks` XOR check (`(document_id IS NOT NULL)::int + (conversation_id IS NOT NULL)::int = 1`) is Postgres-only and is dropped in the SQLite schema; the XOR invariant is upheld by the calling code in `upsert_document` / `upsert_conversation`.

Schema files live under `corpus_forge/schema/` (Postgres, top level) and `corpus_forge/schema/sqlite/` (SQLite). The migration runner (`corpus_forge/schema/migrate.py`) dispatches on a `dialect` argument and the backend's `migrate()` selects which directory to load.