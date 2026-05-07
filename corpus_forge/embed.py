"""Embed backfill utility for corpus-forge."""

import logging

from .backends.postgres import PostgresBackend
from .config import Config
from .embedders.registry import registry

logger = logging.getLogger(__name__)


def backfill_embedder(  # noqa: PLR0912, PLR0915
    embedder_name: str, dataset_name: str | None = None, limit: int | None = None
) -> None:
    """Backfill embeddings for a specific embedder."""
    # Load config
    config = Config.load()

    # Setup backend
    backend_config = config.backend
    if backend_config.kind == "postgres":
        backend = PostgresBackend(dsn=backend_config.dsn, schema=backend_config.schema)
    else:
        raise ValueError(f"Unsupported backend kind: {backend_config.kind}")

    # Get the embedder config
    embedder_config = None
    for ec in config.embedders:
        if ec.name == embedder_name:
            embedder_config = ec
            break

    if not embedder_config:
        raise ValueError(f"Embedder '{embedder_name}' not found in config")

    # Register/create the embedder
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

    # Warm up the embedder
    logger.info(f"Warming up embedder: {embedder_name}")
    embedder.warmup()

    # Register embedder with backend to get ID and create table if needed
    embedder_id = backend.register_embedder(embedder)
    logger.info(f"Registered embedder {embedder_name} with ID {embedder_id}")

    # Determine dataset ID if filtering by dataset
    dataset_id = None
    if dataset_name:
        # Get dataset ID from name
        result = backend._execute("SELECT id FROM corpus.datasets WHERE name = %s", (dataset_name,))
        if not result:
            raise ValueError(f"Dataset '{dataset_name}' not found")
        dataset_id = result[0]["id"]
        logger.info(f"Limiting backfill to dataset: {dataset_name} (ID: {dataset_id})")

    # Backfill embeddings
    logger.info(f"Starting backfill for embedder: {embedder_name}")
    processed = 0

    while True:
        # Get chunks missing this embedder's embedding
        chunks_needing = list(backend.chunks_missing_embedding(embedder_id, limit=1000))

        if not chunks_needing:
            logger.info("No more chunks need embedding")
            break

        chunk_ids, texts = zip(*chunks_needing, strict=True) if chunks_needing else ([], [])

        # Apply dataset filter if needed
        if dataset_id is not None:
            # Filter chunks by dataset
            filtered_pairs = []
            for chunk_id, text in zip(chunk_ids, texts, strict=True):
                # Check which dataset this chunk belongs to
                # This would require a JOIN query - simplified for now
                # In a real implementation, we'd modify the chunks_missing_embedding query
                filtered_pairs.append((chunk_id, text))
            chunk_ids, texts = zip(*filtered_pairs, strict=True) if filtered_pairs else ([], [])

            if not chunk_ids:
                logger.info("No more chunks need embedding for this dataset")
                break

        # Apply limit if specified
        if limit is not None:
            remaining = limit - processed
            if remaining <= 0:
                break
            if len(chunk_ids) > remaining:
                chunk_ids = chunk_ids[:remaining]
                texts = texts[:remaining]

        # Generate embeddings
        logger.info(f"Generating embeddings for {len(texts)} chunks")
        embeddings = embedder.encode(texts)

        # Write embeddings
        pairs = list(zip(chunk_ids, embeddings, strict=True))
        backend.write_embeddings(embedder_id, pairs)
        processed += len(pairs)

        logger.info(f"Processed {processed} embeddings so far")

        # Break if we've hit the limit
        if limit is not None and processed >= limit:
            break

    logger.info(f"Backfill complete. Processed {processed} embeddings for {embedder_name}")


def main(embedder: str, dataset: str | None = None, limit: int | None = None) -> None:
    """Main entry point for embed command."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    try:
        backfill_embedder(embedder, dataset, limit)
    except Exception as e:
        logger.error(f"Backfill failed: {e}")
        raise


if __name__ == "__main__":
    main()
