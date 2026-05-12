"""Ingestion orchestrator for corpus-forge."""

import logging

from .backends.base import StorageBackend
from .chunkers.base import Chunker
from .config import Config
from .embedders.base import Embedder
from .embedders.registry import registry
from .sources.base import RawConversation, RawDocument, Source

logger = logging.getLogger(__name__)


def get_chunker_for_source(source: Source, config: Config) -> Chunker:
    """Get appropriate chunker for a source based on config."""
    # Find dataset config for this source
    for dataset in config.datasets:
        for source_config in dataset.sources:
            if source_config.plugin == source.name:
                # Found matching source config
                chunker_type = source_config.chunker
                chunker_config = source_config.chunker_config or {}

                if chunker_type == "markdown":
                    # Import here to avoid circular dependencies
                    from .chunkers.markdown import MarkdownChunker  # noqa: PLC0415

                    return MarkdownChunker(**chunker_config)
                elif chunker_type == "conversation":
                    # Import here to avoid circular dependencies
                    from .chunkers.conversation import (  # noqa: PLC0415
                        ConversationChunker,
                    )

                    return ConversationChunker(**chunker_config)
                else:
                    raise ValueError(f"Unknown chunker type: {chunker_type}")

    # Default fallback
    # Import here to avoid circular dependencies
    from .chunkers.markdown import MarkdownChunker  # noqa: PLC0415

    return MarkdownChunker()


def get_active_embedders(config: Config) -> list[Embedder]:
    """Get list of active embedders from config."""
    embedders = []
    for embedder_config in config.embedders:
        if embedder_config.active:
            embedder = registry.register(
                name=embedder_config.name,
                provider=embedder_config.provider,
                model_id=embedder_config.model_id,
                dimension=embedder_config.dimension,
                normalized=embedder_config.normalize,
                distance=embedder_config.distance,
                batch_size=getattr(embedder_config, "batch_size", 32),
                device=getattr(embedder_config, "device", "auto"),
                api_key_env=getattr(embedder_config, "api_key_env", "OPENAI_API_KEY"),
            )
            embedders.append(embedder)
    return embedders


def ingest_one(
    backend: StorageBackend,
    raw: RawDocument | RawConversation,
    chunker: Chunker,
    embedders: list[Embedder],
    dataset_id: int,
) -> None:
    """Ingest a single raw document or conversation."""
    logger.debug(f"Ingesting {raw.source_uri}")

    # Use advisory lock to prevent concurrent processing of same source
    with backend.lock_source(raw.source_uri):
        # Check if content has changed using content hash
        current_hash = backend.get_hash(raw.source_uri) if hasattr(backend, "get_hash") else None
        if current_hash == raw.content_hash:
            logger.debug(f"Content unchanged for {raw.source_uri}, skipping")
            return  # Short-circuit if unchanged

        # Resolve active embedder IDs once at start for bulk embedding copy
        embedder_ids = [backend.register_embedder(e) for e in embedders] or None

        # Process based on type
        if isinstance(raw, RawDocument):
            # Process document
            chunk_data = _process_document(raw, chunker)
            backend.upsert_document(dataset_id, raw, chunk_data, embedder_ids=embedder_ids)
        else:  # RawConversation
            # Process conversation
            chunked_messages = _process_conversation(raw, chunker)
            backend.upsert_conversation(dataset_id, raw, chunked_messages)

        # Generate embeddings for each active embedder
        # This loop remains to handle chunks not covered by bulk copy
        for embedder in embedders:
            embedder_id = backend.register_embedder(embedder)
            _write_embeddings_for_chunks(backend, embedder_id, embedder)


def _process_document(doc: RawDocument, chunker: Chunker) -> list[tuple[str | None, str]]:
    """Process a document into chunks with headings."""
    chunks = chunker.chunk(doc.text)
    return [(chunk.heading, chunk.text) for chunk in chunks]


def _process_conversation(
    conv: RawConversation, chunker: Chunker
) -> list[list[tuple[str | None, str]]]:
    """Process a conversation into chunked messages."""
    # Extract text from each message
    message_texts = [msg.content for msg in conv.messages]

    # Chunk the messages
    if hasattr(chunker, "chunk") and callable(chunker.chunk):
        # Check if it's a conversation chunker (takes list of texts)
        try:
            chunks = chunker.chunk(message_texts)  # type: ignore
            # Group chunks by message - simplified approach
            # In reality, we'd need to track which chunks belong to which message
            # For now, return a list where each element corresponds to a message
            result = []
            chunk_idx = 0
            for _msg_idx, _msg in enumerate(conv.messages):
                msg_chunks = []
                # Simple distribution - in reality this would be more complex
                if chunk_idx < len(chunks):
                    msg_chunks.append((chunks[chunk_idx].heading, chunks[chunk_idx].text))
                    chunk_idx += 1
                result.append(msg_chunks)
            return result
        except Exception:
            # Fallback to per-message chunking
            pass

    # Simple per-message fallback
    result = []
    for msg in conv.messages:
        if msg.content.strip():
            # Create a simple chunker for this message
            from .chunkers.base import Chunker  # noqa: PLC0415

            simple_chunker = Chunker()
            chunks = simple_chunker.chunk(msg.content)
            result.append([(chunk.heading, chunk.text) for chunk in chunks])
        else:
            result.append([])

    return result


def _write_embeddings_for_chunks(
    backend: StorageBackend,
    embedder_id: int,
    embedder: Embedder,
) -> None:
    """Write embeddings for chunks."""
    # Get texts for chunks that need embeddings
    chunks_needing_embedding = list(backend.chunks_missing_embedding(embedder_id))

    if not chunks_needing_embedding:
        logger.debug(f"No chunks need embedding for {embedder.name}")
        return

    chunk_ids_needing, texts = (
        zip(*chunks_needing_embedding, strict=True) if chunks_needing_embedding else ([], [])
    )

    # Generate embeddings
    logger.info(f"Generating {embedder.name} embeddings for {len(texts)} chunks")
    embeddings = embedder.encode(texts)

    # Write embeddings
    pairs = list(zip(chunk_ids_needing, embeddings, strict=True))
    backend.write_embeddings(embedder_id, pairs)
    logger.info(f"Written {len(pairs)} embeddings for {embedder.name}")


def ingest_once(config: Config) -> None:
    """Run one-shot ingestion pass."""
    logger.info("Starting one-shot ingestion pass")

    # Setup backend
    backend_config = config.backend
    if backend_config.kind == "postgres":
        # Import here to avoid circular dependencies
        from .backends.postgres import PostgresBackend  # noqa: PLC0415

        backend = PostgresBackend(dsn=backend_config.dsn, schema=backend_config.schema)
    elif backend_config.kind == "sqlite":
        # `backend_config.dsn` doubles as the SQLite file path
        # (e.g. "~/Library/Application Support/corpus-forge/corpus.db").
        from .backends.sqlite import SQLiteBackend  # noqa: PLC0415

        backend = SQLiteBackend(path=backend_config.dsn, schema=backend_config.schema)
    else:
        raise ValueError(f"Unsupported backend kind: {backend_config.kind}")

    # Apply migrations
    backend.migrate()

    # Get active embedders
    embedders = get_active_embedders(config)
    logger.info(f"Active embedders: {[e.name for e in embedders]}")

    # Process each dataset
    for dataset in config.datasets:
        logger.info(f"Processing dataset: {dataset.name} ({dataset.kind})")

        # Get or create dataset record
        dataset_id = _get_or_create_dataset(backend, dataset)

        # Process each source in dataset
        for source_config in dataset.sources:
            logger.info(f"Processing source: {source_config.plugin}")

            # Instantiate source
            source = _instantiate_source(source_config)

            # Get chunker for this source
            chunker = get_chunker_for_source(source, config)

            # Scan and ingest
            raw_items = source.scan()
            for raw in raw_items:
                try:
                    ingest_one(backend, raw, chunker, embedders, dataset_id)
                except Exception as e:
                    logger.error(f"Error ingesting {getattr(raw, 'source_uri', 'unknown')}: {e}")
                    continue


def _get_or_create_dataset(backend: StorageBackend, dataset_config) -> int:
    """Get or create dataset record, returning dataset ID."""
    # Check if dataset exists
    existing = backend._execute(  # pyrefly: ignore[missing-attribute]  # _execute is PostgresBackend-specific; helper functions are internal
        "SELECT id FROM corpus.datasets WHERE name = %s", (dataset_config.name,)
    )

    if existing:
        return existing[0]["id"]

    # Create new dataset
    result = backend._execute(  # pyrefly: ignore[missing-attribute]  # _execute is PostgresBackend-specific; helper functions are internal
        """
        INSERT INTO corpus.datasets (name, kind, description)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (dataset_config.name, dataset_config.kind, dataset_config.description or ""),
    )
    return result[0]["id"]


def _instantiate_source(source_config):
    """Instantiate a source plugin from config."""
    if source_config.plugin == "markdown_vault":
        # Import here to avoid circular dependencies
        from .sources.markdown_vault import MarkdownVaultSource  # noqa: PLC0415

        return MarkdownVaultSource(
            vault_root=source_config.vault_root,
            exclude_globs=source_config.exclude_globs or [],
            debounce=2.0,  # Would come from config
        )
    elif source_config.plugin == "claude_code":
        # Import here to avoid circular dependencies
        from .sources.claude_code import ClaudeCodeSource  # noqa: PLC0415

        return ClaudeCodeSource(
            projects_root=source_config.projects_root,
            include_subagents=source_config.include_subagents,
            debounce=2.0,
        )
    elif source_config.plugin == "opencode":
        # Import here to avoid circular dependencies
        from .sources.opencode import OpenCodeSource  # noqa: PLC0415

        return OpenCodeSource(storage_root=source_config.storage_root, debounce=2.0)
    else:
        raise ValueError(f"Unknown source plugin: {source_config.plugin}")


def main(once: bool = False) -> None:
    """Main entry point for ingestion."""

    # Load config
    config = Config.load()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, config.daemon.log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if once:
        ingest_once(config)
    else:
        # Run daemon mode (would use asyncio/watchdog in real implementation)
        logger.info("Daemon mode not fully implemented in this scaffold")
        # In real implementation, we'd set up watchdog observers for each source
        # and run an event loop


if __name__ == "__main__":
    main()
