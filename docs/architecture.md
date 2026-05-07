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