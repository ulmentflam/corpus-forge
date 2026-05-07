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