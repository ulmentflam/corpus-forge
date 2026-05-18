"""Ingestion orchestrator for corpus-forge."""

import logging
import socket
from typing import Any

from .backends.base import StorageBackend
from .chunkers.base import Chunker, PassthroughChunker, TextChunk
from .config import Config
from .embedders.base import Embedder
from .embedders.registry import registry
from .logging_config import init_logging
from .sources.base import RawConversation, RawDocument, Source

logger = logging.getLogger(__name__)


#: Maps each class-label value (from ``ALLOWED_CLASS_VALUES``) to the
#: ``chunker_hint`` string handled by :meth:`ChunkerDispatcher.for_hint`.
#: Centralised here so the resolution table is single-sourced — the
#: ``rechunk`` CLI (F-04) re-uses it via :meth:`ChunkerDispatcher.for_class`.
#:
#: ``code``      → tree-sitter-aware ``CodeChunker``.
#: ``chat``      → ``ConversationChunker``.
#: ``reference`` → ``PassthroughChunker`` (structured data, fenced blocks).
#: prose values  → ``CDCChunker`` (Phase F: FastCDC rolling-hash boundaries).
_CLASS_TO_HINT: dict[str, str] = {
    "code": "code",
    "chat": "conversation",
    "reference": "passthrough",
    "book": "cdc",
    "textbook": "cdc",
    "paper": "cdc",
    "article": "cdc",
    "note": "cdc",
    "other": "cdc",
}


class ChunkerDispatcher:
    """Phase D — per-document chunker dispatch (extended in Phase F).

    Selects a :class:`Chunker` from the metadata hint carried in
    ``RawDocument.metadata``. The supported hints (via :meth:`for_hint`) are:

    - ``"markdown"``    → :class:`corpus_forge.chunkers.markdown.MarkdownChunker`
    - ``"conversation"`` → :class:`corpus_forge.chunkers.conversation.ConversationChunker`
    - ``"passthrough"`` → :class:`corpus_forge.chunkers.base.PassthroughChunker`
    - ``"code"``        → :class:`corpus_forge.chunkers.code.CodeChunker` (lazy)
    - ``"cdc"``         → :class:`corpus_forge.chunkers.cdc.CDCChunker` (Phase F, lazy)

    Phase F additions:

    - :meth:`for_class` resolves a content-class label (one of
      ``code`` / ``chat`` / ``reference`` / ``book`` / ``textbook`` /
      ``paper`` / ``article`` / ``note`` / ``other``) to the appropriate
      chunker via :data:`_CLASS_TO_HINT`.
    - :meth:`dispatch_for` now consults
      ``raw.metadata['class_hint']`` BEFORE ``chunker_hint`` —
      classification output is more authoritative than source-level
      format hints.

    Backwards-compatible: when no hint is present, :meth:`dispatch_for`
    returns the caller's existing per-source ``fallback`` chunker so
    sources that pre-date this layer (markdown_vault, claude_code,
    opencode) keep working unchanged.

    The dispatcher is intentionally cheap to construct. Chunkers are
    instantiated on demand and memoised per dispatcher instance so the
    hot path doesn't re-import on every document.
    """

    def __init__(self, code_chunker_config: dict[str, Any] | None = None):
        self._code_chunker_config = code_chunker_config or {}
        self._cache: dict[str, Chunker] = {}

    def for_hint(self, hint: str) -> Chunker:
        """Return a (cached) :class:`Chunker` for the given hint string."""
        cached = self._cache.get(hint)
        if cached is not None:
            return cached

        if hint == "markdown":
            # Import here to avoid circular dependencies and keep startup cheap.
            from .chunkers.markdown import MarkdownChunker  # noqa: PLC0415

            chunker: Chunker = MarkdownChunker()
        elif hint == "conversation":
            from .chunkers.conversation import ConversationChunker  # noqa: PLC0415

            chunker = ConversationChunker()
        elif hint == "passthrough":
            chunker = PassthroughChunker()
        elif hint == "code":
            # Lazy import: tree-sitter is an optional [code] extra.
            from .chunkers.code import CodeChunker  # noqa: PLC0415

            chunker = CodeChunker(**self._code_chunker_config)
        elif hint == "cdc":
            # Phase F (F-01): FastCDC content-defined chunker. Lazy-imports
            # the `fastcdc` package (added to the [multi-format] extra in
            # F-05) on first use so callers without the extra installed
            # see a clean ImportError only when CDC actually fires.
            from .chunkers.cdc import CDCChunker  # noqa: PLC0415

            chunker = CDCChunker()
        else:
            raise ValueError(f"Unknown chunker hint: {hint!r}")

        self._cache[hint] = chunker
        return chunker

    def for_class(self, class_value: str) -> Chunker:
        """Resolve a content-class label to its mapped :class:`Chunker`.

        ``class_value`` is the value of a ``namespace='class'`` label
        (one of :data:`corpus_forge.classifiers.base.ALLOWED_CLASS_VALUES`).
        Raises :class:`ValueError` for unknown classes — the caller (the
        ``rechunk`` CLI, F-04) is expected to skip documents that don't
        carry a recognised class label.
        """
        hint = _CLASS_TO_HINT.get(class_value)
        if hint is None:
            raise ValueError(
                f"Unknown class value {class_value!r}; expected one of {sorted(_CLASS_TO_HINT)}"
            )
        return self.for_hint(hint)

    def dispatch_for(self, raw: Any, fallback: Chunker) -> Chunker:
        """Resolve the chunker for ``raw``.

        Resolution order (Phase F):

        1. ``raw.metadata['class_hint']`` (if set + non-empty) →
           :meth:`for_class`. Populated by the ``rechunk`` CLI (F-04)
           after the user has classified their corpus.
        2. ``raw.metadata['chunker_hint']`` (if set + non-empty) →
           :meth:`for_hint`. Source-level format hint (Phase D HK-1).
        3. ``fallback`` — pre-Phase-D source-level chunker.

        Empty-string values at either layer are treated as "absent",
        not as "unknown" — mirrors the existing ``chunker_hint`` semantics.
        """
        metadata = getattr(raw, "metadata", None) or {}
        if not isinstance(metadata, dict):
            return fallback

        class_hint = metadata.get("class_hint")
        if class_hint:
            return self.for_class(class_hint)

        hint = metadata.get("chunker_hint")
        if not hint:
            return fallback
        return self.for_hint(hint)


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
            kwargs = {
                "name": embedder_config.name,
                "provider": embedder_config.provider,
                "model_id": embedder_config.model_id,
                "dimension": embedder_config.dimension,
                "normalized": embedder_config.normalize,
                "distance": embedder_config.distance,
                "batch_size": getattr(embedder_config, "batch_size", 32),
                "device": getattr(embedder_config, "device", "auto"),
            }
            if embedder_config.provider == "openai":
                kwargs["api_key_env"] = getattr(embedder_config, "api_key_env", "OPENAI_API_KEY")
                base_url = getattr(embedder_config, "base_url", None)
                if base_url is not None:
                    # ``base_url`` may be an AnyHttpUrl from pydantic — cast
                    # to str so the OpenAI SDK accepts it unchanged.
                    kwargs["base_url"] = str(base_url).rstrip("/")
            embedder = registry.register(**kwargs)
            embedders.append(embedder)
    return embedders


# Module-level dispatcher singleton — Phase D housekeeping (HK-1).
#
# ``ChunkerDispatcher`` is cheap to construct and caches per-hint chunker
# instances. Sharing one across ``ingest_one`` calls means the (possibly
# expensive) ``CodeChunker`` is instantiated at most once per process.
# Per-call construction would defeat the cache and re-import tree-sitter
# on every code document.
_DISPATCHER = ChunkerDispatcher()


def ingest_one(
    backend: StorageBackend,
    raw: RawDocument | RawConversation,
    chunker: Chunker,
    embedders: list[Embedder],
    dataset_id: int,
    source: Source | None = None,
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

        # Phase D housekeeping (HK-1) — resolve the per-document chunker
        # from ``raw.metadata["chunker_hint"]`` via the module-level
        # dispatcher. Sources without a hint (markdown_vault, claude_code,
        # opencode) keep their source-level chunker via the fallback —
        # behaviour-preserving.
        effective_chunker = _DISPATCHER.dispatch_for(raw, fallback=chunker)

        # Process based on type
        if isinstance(raw, RawDocument):
            # Process document
            chunk_data = _process_document(raw, effective_chunker)
            backend.upsert_document(dataset_id, raw, chunk_data, embedder_ids=embedder_ids)
        else:  # RawConversation
            # Process conversation
            chunked_messages = _process_conversation(raw, effective_chunker)
            conv_id = backend.upsert_conversation(dataset_id, raw, chunked_messages)
            # If the source is a chat client (claude_code/opencode/gemini), link the session.
            # Prefer explicit _session_link_client on the source object; fall back to
            # deriving the client from the source_uri scheme (e.g. "claude-code://...").
            session_link_client = getattr(source, "_session_link_client", None)
            if session_link_client is None:
                session_link_client = _client_from_source_uri(raw.source_uri)
            if session_link_client is not None and raw.external_id is not None:
                from corpus_forge.sources._session_link import (  # noqa: PLC0415
                    link_session_to_conversation,
                )

                link_session_to_conversation(
                    backend,
                    client=session_link_client,
                    session_id=raw.external_id,
                    conversation_id=conv_id,
                )

        # Generate embeddings for each active embedder
        # This loop remains to handle chunks not covered by bulk copy
        for embedder in embedders:
            embedder_id = backend.register_embedder(embedder)
            _write_embeddings_for_chunks(backend, embedder_id, embedder)


def _process_document(doc: RawDocument, chunker: Chunker) -> list[TextChunk]:
    """Process a document into a list of :class:`TextChunk`.

    Phase D housekeeping (HK-1 + HK-2):

    - Returns ``list[TextChunk]`` (not flattened 2-tuples) so the storage
      layer can persist ``chunk.metadata``, ``chunk.role``, and
      ``chunk.token_count``.
    - For :class:`corpus_forge.chunkers.code.CodeChunker`, threads
      ``language`` (from ``doc.metadata["language"]``) and
      ``relative_path`` (derived from ``doc.source_uri``) into the
      ``chunk()`` call so the AST path can annotate chunks with
      ``kind``/``name``/``byte_range``. Other chunkers receive only
      ``text`` — preserving the legacy ``Chunker.chunk(text)`` shape.
    """
    text = doc.text
    if not text:
        return []

    # Special-case CodeChunker so the AST path (and the byte-line
    # fallback) get the metadata they need. Avoids polluting the base
    # ``Chunker.chunk(text)`` signature.
    try:
        from .chunkers.code import CodeChunker  # noqa: PLC0415
    except ImportError:  # pragma: no cover — only when [code] extra missing
        CodeChunker = None  # type: ignore[assignment]

    if CodeChunker is not None and isinstance(chunker, CodeChunker):
        language = None
        if isinstance(doc.metadata, dict):
            language = doc.metadata.get("language")
        relative_path = _relative_path_from_source_uri(doc.source_uri)
        return chunker.chunk(text, language=language, relative_path=relative_path)

    return chunker.chunk(text)


def _relative_path_from_source_uri(source_uri: str) -> str | None:
    """Best-effort extraction of a display path from a ``filesystem://`` URI.

    Used by :func:`_process_document` to feed
    ``CodeChunker.chunk(relative_path=...)`` so AST-emitted chunks carry
    the ``# <relative path> :: <kind> <name>`` header. Returns ``None``
    for non-filesystem URIs (markdown_vault, claude_code, etc.) — in
    that case the chunker simply skips the header line.
    """
    prefix = "filesystem://"
    if not source_uri.startswith(prefix):
        return None
    body = source_uri[len(prefix) :]
    # ``filesystem://{root.name}/{rel_path}`` — strip the root.name prefix.
    sep = body.find("/")
    if sep == -1:
        return body or None
    return body[sep + 1 :] or None


def _process_conversation(conv: RawConversation, chunker: Chunker) -> list[list[TextChunk]]:
    """Process a conversation into chunked messages.

    Returns ``list[list[TextChunk]]`` (one list per source message) so
    storage backends can persist chunk-level metadata. The conversation
    chunker shape is otherwise unchanged.
    """
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
            result: list[list[TextChunk]] = []
            chunk_idx = 0
            for _msg_idx, _msg in enumerate(conv.messages):
                msg_chunks: list[TextChunk] = []
                # Simple distribution - in reality this would be more complex
                if chunk_idx < len(chunks):
                    msg_chunks.append(chunks[chunk_idx])
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
            result.append(list(chunks))
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
            source = _instantiate_source(source_config, config=config)

            # Register source in the DB (idempotent) so the sources table
            # tracks which plugin/identity/host contributed to this dataset.
            backend.register_source(
                dataset_id,
                source.name,
                source.identity(),
                socket.gethostname(),
            )

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
    return backend.get_or_create_dataset(
        name=dataset_config.name,
        kind=dataset_config.kind,
        description=dataset_config.description or "",
    )


def _instantiate_source(source_config, *, config: Config | None = None):
    """Instantiate a source plugin from config.

    ``config`` is the enclosing :class:`Config` (Wave 5 addition, E-05).
    When supplied, the ``filesystem`` source resolves the active VLM
    via :func:`corpus_forge.vlm.registry.get_active_vlm` and threads it
    into the extractor registry so the PDF Tier 2 escalation + the
    ImageExtractor light up. ``config=None`` (the legacy call shape)
    preserves the pre-Wave-5 digital-only behaviour exactly.
    """
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
    elif source_config.plugin == "filesystem":
        # Phase D / Wave 2 (D-15) — generic walker over heterogeneous
        # directory trees, dispatched per-file through the extractor
        # registry. ``ExtractionConfig`` is optional in config but the
        # source needs a real instance (defaults: all flags True, 50 MB
        # max_bytes).
        #
        # Wave 5 (E-05): when the caller passes a ``Config`` (full
        # top-level model) we resolve the VLM via ``get_active_vlm`` and
        # thread it through. The legacy direct ``DatasetSourceConfig``
        # path (no enclosing Config) gets ``vlm=None`` and degrades to
        # the pre-Wave-5 digital-only behaviour, preserving every
        # existing caller.
        from .config import ExtractionConfig  # noqa: PLC0415
        from .sources.filesystem import FilesystemSource  # noqa: PLC0415

        extraction = source_config.extraction or ExtractionConfig()
        vlm = None
        whisper = None
        if config is not None:
            try:
                from .vlm.registry import get_active_vlm  # noqa: PLC0415

                vlm = get_active_vlm(config)
            except Exception as exc:
                # VLM resolution failures should never block ingest of
                # the non-OCR paths — log + degrade gracefully. The
                # broad except is deliberate: any backend exception (a
                # mistyped Ollama URL, a missing Mistral API key, an
                # ImportError on the [ocr] extra) is recoverable.
                logger.warning(
                    "VLM resolution failed (%s) — falling back to "
                    "digital-only ingest for the filesystem source.",
                    exc,
                )
                vlm = None
            # Phase G (G-05/G-06): same pattern for the Whisper backend.
            # When the user hasn't configured ``[whisper]`` the factory
            # returns NoopWhisper and audio/video files are silently
            # skipped — no extra try needed for that path. We still wrap
            # in try/except so a misconfigured remote URL / missing API
            # key downgrades to "no transcription" rather than blowing
            # up the entire ingest run.
            try:
                from .whisper.registry import get_active_whisper  # noqa: PLC0415

                whisper = get_active_whisper(config)
            except Exception as exc:
                logger.warning(
                    "Whisper resolution failed (%s) — audio/video files "
                    "will be skipped on this ingest pass.",
                    exc,
                )
                whisper = None
        return FilesystemSource(
            root=source_config.root,
            exclude_globs=source_config.exclude_globs or [],
            extraction=extraction,
            vlm=vlm,
            whisper=whisper,
            debounce=2.0,
        )
    else:
        raise ValueError(f"Unknown source plugin: {source_config.plugin}")


# Maps source_uri scheme prefixes to feedback_sessions client names.
_SOURCE_URI_TO_CLIENT: dict[str, str] = {
    "claude-code://": "claude-code",
    "opencode://": "opencode",
    "gemini-cli://": "gemini-cli",
}


def _client_from_source_uri(source_uri: str) -> str | None:
    """Derive the feedback_sessions client string from a source URI scheme.

    Returns None for source URIs that don't correspond to a known chat client
    (e.g. markdown vault URIs).
    """
    for prefix, client in _SOURCE_URI_TO_CLIENT.items():
        if source_uri.startswith(prefix):
            return client
    return None


def main(once: bool = False) -> None:
    """Main entry point for ingestion."""

    # Load config
    config = Config.load()

    # Phase L Wave 1: route through the central ``init_logging`` helper
    # instead of the bare ``logging.basicConfig`` call this site used to
    # carry.  The rotating file lands at ``<cache>/corpus-forge/logs/
    # ingest.log``; the stderr level honors ``config.daemon.log_level``
    # via the ``CF_LOG_LEVEL`` env override path used by the helper.
    init_logging("ingest", verbose=False, quiet=False)

    if once:
        ingest_once(config)
    else:
        # Run daemon mode (would use asyncio/watchdog in real implementation)
        logger.info("Daemon mode not fully implemented in this scaffold")
        # In real implementation, we'd set up watchdog observers for each source
        # and run an event loop


if __name__ == "__main__":
    main()
