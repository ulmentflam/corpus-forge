"""Ingestion orchestrator for corpus-forge."""

import logging
import socket
import time
from pathlib import Path
from typing import Any

from .admin.source_caps import enforce_source_caps
from .backends.base import StorageBackend
from .chunkers.base import Chunker, PassthroughChunker, TextChunk
from .config import Config
from .embedders.base import Embedder
from .embedders.openai import EmbedderWedged
from .embedders.registry import registry
from .logging_config import init_logging
from .sources.base import RawConversation, RawDocument, Source
from .ui.progress import make_progress

logger = logging.getLogger(__name__)

# Phase L Wave 4 — taxonomy loggers documented in
# ``.planning/tdd/phase_l_cli_ux.md`` §2. Greppable surface for
# bug-report attachments and ``corpus-forge logs tail``.
scan_logger = logging.getLogger("corpus_forge.ingest.scan")
extract_logger = logging.getLogger("corpus_forge.ingest.extract")
chunk_logger = logging.getLogger("corpus_forge.ingest.chunk")


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
    """Get list of active embedders from config.

    Per-provider kwarg pass-through:

    - ``sentence_transformers`` accepts ``device`` (auto-resolves
      ``"auto"`` to mps / cuda / cpu via ``resolve_device``).
    - ``openai`` does NOT accept ``device`` — its underlying HTTP
      transport has no local-accelerator concept. Passing it raised
      ``TypeError: OpenAIEmbedder.__init__() got an unexpected keyword
      argument 'device'`` on every first-run ingest against an
      Ollama-backed OpenAI-compatible endpoint.
    - ``model2vec`` is CPU-only (static embeddings, no inference loop);
      same story.

    Each branch adds only the kwargs the corresponding embedder class
    actually accepts.
    """
    # The provider-specific kwarg gating lives in
    # ``embedders.registry.register_from_config`` so every call site
    # (ingest here, search via cli._build_retriever_for_eval, admin
    # smoke-test via admin.embedder.run_embedder_smoke) shares one
    # source of truth. Drift between the three was responsible for
    # both the original "openai device kwarg" TypeError and the
    # later "API key not found in environment variable OPENAI_API_KEY"
    # crash on dense search against a local Ollama endpoint.
    from .embedders.registry import register_from_config  # noqa: PLC0415

    embedders = []
    for embedder_config in config.embedders:
        if not embedder_config.active:
            continue
        embedder = register_from_config(registry, embedder_config)
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


def _calibration_key_for(raw: "RawDocument | RawConversation") -> str:
    """Best-effort extractor-class label for runtime-profile calibration.

    Mirrors :func:`corpus_forge.estimate._classify_extension` so the
    profile keys captured during a real ingest line up with the keys
    consulted by :func:`corpus_forge.time_estimate.estimate_time` on the
    next estimate pass. Falls back to ``"unknown"`` for sources that
    don't surface a recognised extension.
    """
    # Prefer an explicit metadata hint when the source has stamped one.
    metadata = getattr(raw, "metadata", None)
    if isinstance(metadata, dict):
        hint = metadata.get("extractor_class") or metadata.get("class_hint")
        if isinstance(hint, str) and hint:
            return hint
    # Fall back to extension classification on the source URI.
    uri = getattr(raw, "source_uri", "") or ""
    last_slash = max(uri.rfind("/"), uri.rfind("\\"))
    name = uri[last_slash + 1 :] if last_slash >= 0 else uri
    last_dot = name.rfind(".")
    if last_dot > 0:
        from corpus_forge.estimate import _classify_extension  # noqa: PLC0415

        cls = _classify_extension(name[last_dot:].lower())
        if cls is not None:
            return cls
    return "unknown"


#: How many files ``ingest_once`` ingests between embedding flushes.
#: Larger = more amortization of the
#: ``chunks_missing_embedding`` query cost + fewer Ollama round-trips;
#: smaller = embeddings become searchable sooner. 32 is conservative —
#: even on chatty corpora a flush every 32 files keeps the embedder
#: working in batches well-matched to its ``batch_size=256`` setting.
_FLUSH_EMBEDDINGS_EVERY_N_FILES = 32


def ingest_one(
    backend: StorageBackend,
    raw: RawDocument | RawConversation,
    chunker: Chunker,
    embedders: list[Embedder],
    dataset_id: int,
    source: Source | None = None,
    *,
    flush_embeddings: bool = True,
) -> None:
    """Ingest a single raw document or conversation.

    ``flush_embeddings=True`` (the default, for standalone callers)
    runs the per-embedder write loop immediately after the file's
    chunks land in the DB. ``flush_embeddings=False`` skips that
    work — used by :func:`ingest_once` which batches the flush
    across :data:`_FLUSH_EMBEDDINGS_EVERY_N_FILES` files to amortize
    the ``chunks_missing_embedding`` query cost.
    """
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

        # Process based on type — instrument chunk + db_write timings for
        # the wall-clock calibration profile. Per-document timings are
        # noisy but the EWMA in ``runtime_profile.record`` smooths them
        # out over the course of a real ingest pass.
        cal_key = _calibration_key_for(raw)
        if isinstance(raw, RawDocument):
            # Process document
            _t0 = time.perf_counter()
            chunk_data = _process_document(raw, effective_chunker)
            _chunk_elapsed = time.perf_counter() - _t0

            _t1 = time.perf_counter()
            backend.upsert_document(dataset_id, raw, chunk_data, embedder_ids=embedder_ids)
            _write_elapsed = time.perf_counter() - _t1

            n_chunks = len(chunk_data)
            if n_chunks > 0:
                try:
                    from corpus_forge.runtime_profile import record as _record  # noqa: PLC0415

                    _record(
                        "chunk",
                        units=n_chunks,
                        seconds=_chunk_elapsed,
                        key=cal_key,
                    )
                    _record("db_write", units=n_chunks, seconds=_write_elapsed)
                except Exception as exc:  # pragma: no cover — defensive
                    logger.debug("ingest: calibration write failed: %s", exc)
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

        # Generate embeddings for each active embedder. ``ingest_once``
        # passes ``flush_embeddings=False`` so it can batch the flush
        # across N files (see ``_FLUSH_EMBEDDINGS_EVERY_N_FILES``) —
        # the per-file ``chunks_missing_embedding`` query was the
        # dominant cost in the 2026-05-27 ingest profile (~209ms/file
        # over Tailscale-PG vs ~25ms/chunk for Ollama encode). External
        # callers default to ``flush_embeddings=True`` so the existing
        # per-file contract is preserved.
        if flush_embeddings:
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


def _flush_all_pending_embeddings(
    backend: StorageBackend,
    embedders: list[Embedder],
) -> None:
    """Drain ``chunks_missing_embedding`` for every active embedder in
    sized batches until ``_write_embeddings_for_chunks`` reports zero
    work done.

    Called periodically by :func:`ingest_once` (every
    ``_FLUSH_EMBEDDINGS_EVERY_N_FILES`` files) and once more at the
    end of each source. Each call to
    :func:`_write_embeddings_for_chunks` fetches up to ``limit=1024``
    pending chunks; we loop until it returns 0 so a single flush
    invocation clears the whole backlog accumulated since the
    previous flush.

    Internal-loop safety: ``_write_embeddings_for_chunks`` returns
    early on an empty fetch (``0`` pairs written), so this loop
    terminates naturally. Same contract on the wedge path: the
    bisecting embedder raises :class:`EmbedderWedged` and we let it
    propagate — same as the per-file path.
    """
    for embedder in embedders:
        embedder_id = backend.register_embedder(embedder)
        while _write_embeddings_for_chunks(backend, embedder_id, embedder) > 0:
            pass


def _write_embeddings_for_chunks(
    backend: StorageBackend,
    embedder_id: int,
    embedder: Embedder,
) -> int:
    """Write embeddings for chunks. Returns the number of embeddings
    persisted (0 when there's nothing pending or every chunk was
    bisected out), which lets :func:`_flush_all_pending_embeddings`
    loop until the queue is fully drained without an extra
    ``count_chunks_missing_embedding`` round-trip per iteration.
    """
    # Get texts for chunks that need embeddings
    chunks_needing_embedding = list(backend.chunks_missing_embedding(embedder_id))

    if not chunks_needing_embedding:
        logger.debug(f"No chunks need embedding for {embedder.name}")
        return 0

    chunk_ids_needing, texts = (
        zip(*chunks_needing_embedding, strict=True) if chunks_needing_embedding else ([], [])
    )

    # Generate embeddings. The OpenAI embedder bisects-and-skips on
    # NaN-shaped responses (PR #49); the indices it skipped are read
    # back via ``getattr`` so non-bisecting embedders (sentence-
    # transformers, model2vec) keep their existing contract.
    logger.info(f"Generating {embedder.name} embeddings for {len(texts)} chunks")
    embeddings = embedder.encode(texts)
    failed_indices: set[int] = set(getattr(embedder, "last_failed_indices", []))

    if failed_indices:
        # Keep only chunk_ids whose embeddings actually came back.
        # The skipped chunks stay in chunks_missing_embedding so a
        # future ingest pass retries them after the model recovers.
        chunk_ids_for_pairs = [
            cid for i, cid in enumerate(chunk_ids_needing) if i not in failed_indices
        ]
        logger.warning(
            "Embedder %s skipped %d/%d chunks (NaN-shaped response or 5xx); "
            "they stay pending for the next ingest pass. Sample skipped "
            "indices: %s",
            embedder.name,
            len(failed_indices),
            len(texts),
            sorted(failed_indices)[:5],
        )
    else:
        chunk_ids_for_pairs = list(chunk_ids_needing)

    pairs = list(zip(chunk_ids_for_pairs, embeddings, strict=True))
    backend.write_embeddings(embedder_id, pairs)
    logger.info(f"Written {len(pairs)} embeddings for {embedder.name}")
    return len(pairs)


#: Candidate config-field names whose value (if set) is a filesystem
#: root we can hand to the storage/time estimator. Order matches the
#: priority we want for sources that expose more than one (e.g. zotero,
#: which has a library root and an attachments root — library wins).
_SOURCE_ROOT_FIELDS: tuple[str, ...] = (
    "root",
    "vault_root",
    "projects_root",
    "storage_root",
    "chats_root",
    "sessions_root",
    "export_root",
    "history_path",
    "path",
)


def _source_root(source_config: Any) -> Path | None:
    """Resolve a filesystem root from a dataset source config, if any.

    Wall-clock-ETA helper for :func:`ingest_once`. Returns ``None`` for
    sources whose work is API-driven (e.g. Zotero web library) and so
    cannot be modelled with the disk-bound estimator. Best-effort —
    blank-but-set fields are treated as missing.
    """
    for field_name in _SOURCE_ROOT_FIELDS:
        value = getattr(source_config, field_name, None)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        candidate = Path(text).expanduser()
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


def _classify_and_log_ingest_error(raw: Any, exc: BaseException) -> None:
    """Log a per-document ingest failure on the right taxonomy logger.

    The previous catch-all "Extractor failed on X" message
    mis-attributed every recoverable failure to the extractor,
    including Ollama 500s with ``"unsupported value: NaN"`` (the
    embedder produced a non-finite vector for some chunk). That made
    it look like the file itself was malformed rather than the model.

    Heuristic classification on the exception message:

    - "unsupported value: NaN" / "NaN" → embedder produced a NaN
      vector. Log at WARNING with an actionable hint.
    - HTTP 5xx from any *embedder* call → embedder API failure.
    - Anything else → extractor failure (unchanged taxonomy).
    """
    source_uri = getattr(raw, "source_uri", "unknown")
    msg = str(exc)
    lowered = msg.lower()

    if "unsupported value: nan" in lowered or "json: unsupported value: nan" in lowered:
        logger.warning(
            "Embedder produced NaN for %s — skipping. Likely a quirk of the active "
            "embedding model on this chunk's text (try a different embedder, or "
            "filter empty / near-empty chunks). Original error: %s",
            source_uri,
            msg,
        )
        return
    if "error code: 5" in lowered and ("embed" in lowered or "embedding" in lowered):
        logger.warning(
            "Embedder API 5xx on %s — skipping. Original error: %s",
            source_uri,
            msg,
        )
        return

    # Default classification — same wording as before to keep grep
    # patterns / dashboards working.
    extract_logger.info(
        "Extractor failed on %s: %s",
        source_uri,
        msg,
    )


def _plan_ingest(config: Config) -> dict[int, int]:
    """Compute the wall-clock ETA AND per-source file counts.

    Walks every filesystem-rooted source once via
    :func:`corpus_forge.estimate.estimate_sync`, sums the per-source
    :class:`~corpus_forge.time_estimate.TimeEstimate`s, logs one summary
    INFO line, and returns a mapping ``id(source_config) -> file_count``
    so :func:`ingest_once` can hand each source's progress bar a real
    total (live percentage + ETA via Rich's ``TimeRemainingColumn``)
    instead of leaving it in unbounded mode.

    This replaces the previous "walk twice" cost (estimate then re-walk
    in ``source.scan()``) for filesystem-rooted sources — the count
    captured here is the same one the live bar would show, just paid
    up front.

    Best-effort: any exception is swallowed (logged at DEBUG) so a
    broken planner can never block ingest. Returns ``{}`` on failure
    and ``ingest_once`` falls back to unbounded progress bars.
    """
    per_source_totals: dict[int, int] = {}
    try:
        from corpus_forge.estimate import estimate_sync  # noqa: PLC0415
        from corpus_forge.runtime_profile import load as _load_profile  # noqa: PLC0415
        from corpus_forge.time_estimate import (  # noqa: PLC0415
            estimate_time,
            format_duration,
        )

        profile = _load_profile()
        total_seconds = 0.0
        per_phase: dict[str, float] = {}
        roots_seen = 0
        roots_skipped = 0
        any_calibrated = False

        for dataset in config.datasets:
            for source_config in dataset.sources:
                root = _source_root(source_config)
                if root is None:
                    roots_skipped += 1
                    logger.debug(
                        "ETA: skipping source plugin=%s (no resolvable filesystem root)",
                        getattr(source_config, "plugin", "unknown"),
                    )
                    continue
                try:
                    sync = estimate_sync(root, config)
                except (FileNotFoundError, NotADirectoryError, ValueError) as exc:
                    logger.debug("ETA: estimate_sync failed for %s: %s", root, exc)
                    continue
                te = estimate_time(sync, config, profile=profile)
                total_seconds += te.total_seconds
                for phase in te.phases:
                    per_phase[phase.name] = per_phase.get(phase.name, 0.0) + phase.seconds
                roots_seen += 1
                per_source_totals[id(source_config)] = sync.file_count
                if te.calibration in ("calibrated", "hybrid"):
                    any_calibrated = True

        if roots_seen == 0:
            logger.info(
                "ETA: no filesystem-rooted sources detected — wall-clock prediction skipped"
            )
            return per_source_totals

        breakdown = " / ".join(
            f"{name} {format_duration(per_phase.get(name, 0.0))}"
            for name in ("scan", "extract", "chunk", "embed", "db_write")
        )
        calibration_note = (
            "calibrated" if any_calibrated else "heuristic (uncalibrated on this host)"
        )
        skipped_note = f" (+{roots_skipped} API-only source(s) excluded)" if roots_skipped else ""
        logger.info(
            "ETA ~%s [%s] across %d filesystem root(s)%s — %s",
            format_duration(total_seconds),
            breakdown,
            roots_seen,
            skipped_note,
            calibration_note,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("ETA computation failed: %s", exc)
    return per_source_totals


def ingest_once(config: Config) -> None:
    """Run one-shot ingestion pass."""
    logger.info("Starting one-shot ingestion pass")
    # Walk every filesystem-rooted source up front to compute the ETA
    # AND capture per-source file counts that drive the live progress
    # bar totals below. The walk cost was already paid by the previous
    # ETA-only call; threading the result avoids walking the tree a
    # second time inside ``source.scan()``.
    per_source_totals = _plan_ingest(config)

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

    # Single Rich ``Progress`` instance with TWO live tasks:
    #
    #   1. A persistent global task — total = sum of every source's
    #      file count, so users see overall percentage + remaining-time
    #      ETA across the entire run.
    #   2. A transient per-source task — added when each source starts,
    #      removed when it finishes, so the display always shows the
    #      currently-running source's slice underneath the global bar.
    #
    # Two nested ``make_progress`` contexts would fight over the
    # console (Rich's ``Live`` is single-active), so the per-source
    # progress now lives as a child task of the outer ``Progress``.
    global_total = sum(per_source_totals.values()) or None
    with make_progress(
        "Ingest (all sources)",
        total=global_total,
        logger=scan_logger,
    ) as progress:
        global_task = progress.add_task("Total", total=global_total)

        # Process each dataset
        for dataset in config.datasets:
            logger.info(f"Processing dataset: {dataset.name} ({dataset.kind})")

            # Get or create dataset record
            dataset_id = _get_or_create_dataset(backend, dataset)

            # Process each source in dataset
            for source_config in dataset.sources:
                scan_logger.info(
                    "Scanning source: plugin=%s dataset=%s",
                    source_config.plugin,
                    dataset.name,
                )

                # Instantiate source
                source = _instantiate_source(source_config, config=config)

                # Register source in the DB (idempotent) so the sources
                # table tracks which plugin/identity/host contributed
                # to this dataset.
                backend.register_source(
                    dataset_id,
                    source.name,
                    source.identity(),
                    socket.gethostname(),
                )

                # Get chunker for this source
                chunker = get_chunker_for_source(source, config)

                # Per-source task lives only for the duration of this
                # source. ``total=None`` (API-only sources we couldn't
                # count up front) renders as an indeterminate bar; the
                # global task is still useful as long as at least one
                # source contributed a count.
                raw_items = source.scan()
                docs_chunked = 0
                source_total = per_source_totals.get(id(source_config))
                source_task = progress.add_task(
                    f"  {source.name}",
                    total=source_total,
                )
                scan_logger.info(
                    "Ingest (%s) started: %s items",
                    source.name,
                    source_total if source_total is not None else "unbounded",
                )
                source_started = time.perf_counter()

                for raw in raw_items:
                    try:
                        ingest_one(
                            backend,
                            raw,
                            chunker,
                            embedders,
                            dataset_id,
                            flush_embeddings=False,
                        )
                        docs_chunked += 1
                        if docs_chunked % 100 == 0:
                            chunk_logger.info("Chunked %d documents so far", docs_chunked)
                        # Batched embed flush. Without this, every file
                        # triggered a ``chunks_missing_embedding`` query
                        # (the LEFT-JOIN-anti-join), which was the
                        # dominant cost in the 2026-05-27 ingest profile.
                        # Flushing every N files (default 32) amortizes
                        # that cost while keeping the embed batches
                        # well-matched to the embedder's internal
                        # ``batch_size=256``.
                        if docs_chunked % _FLUSH_EMBEDDINGS_EVERY_N_FILES == 0:
                            _flush_all_pending_embeddings(backend, embedders)
                    except EmbedderWedged:
                        # Systemic failure, not a per-file one. The
                        # bisection-with-skip recovery tripped its
                        # circuit-breaker — every chunk is failing
                        # which means the upstream model is wedged.
                        # Re-raise so the outer ``ingest_once`` handler
                        # surfaces a clean error + recovery hint
                        # instead of catching it here and continuing
                        # to fail every subsequent file the same way.
                        raise
                    except Exception as e:
                        # Per-file failures are recoverable. Categorise
                        # the message so users can tell extractor
                        # crashes apart from embedder/API failures —
                        # the previous "Extractor failed on X" wording
                        # mis-attributed Ollama 500s (NaN-in-response,
                        # rate limits) to the extractor, which makes
                        # the model-selection vs. file-content
                        # question harder to answer.
                        _classify_and_log_ingest_error(raw, e)
                    finally:
                        # Advance both bars on EVERY iteration, success
                        # or failure. Planner totals come from
                        # ``estimate_sync`` which counts every file
                        # regardless of whether ingest will succeed —
                        # skipping the advance on failures would leave
                        # both bars permanently below 100% whenever any
                        # file fails (e.g. one Ollama-NaN 5xx is enough
                        # to strand the global bar forever).
                        progress.update(source_task, advance=1)
                        progress.update(global_task, advance=1)

                # End-of-source embed flush. The per-file loop only
                # flushes on ``docs_chunked % N == 0`` boundaries, so
                # the trailing files in this source (between the last
                # boundary and the end) need an explicit flush to
                # finish embedding. Cheap when nothing is pending.
                _flush_all_pending_embeddings(backend, embedders)

                elapsed = time.perf_counter() - source_started
                rate = (docs_chunked / elapsed) if elapsed > 0 else 0.0
                scan_logger.info(
                    "Ingest (%s) complete: %d documents in %.1fs (rate %.0f/s)",
                    source.name,
                    docs_chunked,
                    elapsed,
                    rate,
                )
                scan_logger.info(
                    "Scan complete: %d documents (plugin=%s)",
                    docs_chunked,
                    source_config.plugin,
                )

                # RFC ``rfc-corpus-growth-controls`` — per-source cap
                # enforcement. Runs once per source per ingest cycle
                # (NOT inside the per-file loop — scoring every
                # candidate after every file would dominate ingest
                # cost). When caps aren't set, this is a fast-return
                # no-op. Failures are isolated: a misbehaving cap
                # check must not break ingest itself.
                if (
                    getattr(source_config, "max_rows", None) is not None
                    or getattr(source_config, "max_bytes", None) is not None
                ):
                    try:
                        cap_report = enforce_source_caps(backend, dataset_id, source_config)
                        if cap_report.rows_evicted:
                            logger.info(
                                "cap enforcement: evicted %d rows (%d bytes) from %s "
                                "(cap_max_rows=%s cap_max_bytes=%s reason=%s)",
                                cap_report.rows_evicted,
                                cap_report.bytes_evicted,
                                cap_report.source_uri_prefix,
                                cap_report.cap_max_rows,
                                cap_report.cap_max_bytes,
                                cap_report.reason,
                            )
                    except Exception as cap_exc:
                        logger.warning(
                            "cap enforcement failed for %s: %r",
                            source_config.plugin,
                            cap_exc,
                        )

                # Remove the per-source task so the next source's task
                # appears underneath the global bar instead of stacking.
                progress.remove_task(source_task)


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
            # ``getattr`` keeps this branch tolerant of legacy MockSourceConfig
            # shapes in the unit suite that pre-date the ``history_path`` field.
            history_path=getattr(source_config, "history_path", None),
            debounce=2.0,
        )
    elif source_config.plugin == "opencode":
        # Import here to avoid circular dependencies
        from .sources.opencode import OpenCodeSource  # noqa: PLC0415

        return OpenCodeSource(storage_root=source_config.storage_root, debounce=2.0)
    elif source_config.plugin == "gemini_cli":
        from pathlib import Path as _Path  # noqa: PLC0415

        from .sources.gemini_cli import GeminiCLISource  # noqa: PLC0415

        # ``ExpandedPath`` is a typed str, so an unset field is None but a
        # field set to "" / "   " in TOML slips past type validation and
        # would resolve to CWD via ``Path("")``. Treat blank as missing.
        if not (source_config.chats_root and str(source_config.chats_root).strip()):
            raise ValueError(
                "DatasetSourceConfig.plugin = 'gemini_cli' requires `chats_root` "
                "(typically `~/.gemini/tmp`)."
            )
        return GeminiCLISource(projects_root=_Path(source_config.chats_root), debounce=2.0)
    elif source_config.plugin == "codex_cli":
        from pathlib import Path as _Path  # noqa: PLC0415

        from .sources.codex_cli import CodexCLISource  # noqa: PLC0415

        if not (source_config.sessions_root and str(source_config.sessions_root).strip()):
            raise ValueError(
                "DatasetSourceConfig.plugin = 'codex_cli' requires `sessions_root` "
                "(typically `~/.codex/sessions`)."
            )
        return CodexCLISource(sessions_root=_Path(source_config.sessions_root), debounce=2.0)
    elif source_config.plugin == "chatgpt_export":
        from pathlib import Path as _Path  # noqa: PLC0415

        from .sources.chatgpt_export import ChatGPTExportSource  # noqa: PLC0415

        if not (source_config.export_root and str(source_config.export_root).strip()):
            raise ValueError(
                "DatasetSourceConfig.plugin = 'chatgpt_export' requires `export_root` "
                "(directory containing `conversations.json`)."
            )
        return ChatGPTExportSource(export_root=_Path(source_config.export_root), debounce=2.0)
    elif source_config.plugin == "jsonl_chat":
        from pathlib import Path as _Path  # noqa: PLC0415

        from .sources.jsonl_chat import JSONLChatSource  # noqa: PLC0415

        if not (source_config.path and str(source_config.path).strip()):
            raise ValueError(
                "DatasetSourceConfig.plugin = 'jsonl_chat' requires `path` "
                "(a directory of *.jsonl files or a single file)."
            )
        return JSONLChatSource(path=_Path(source_config.path), debounce=2.0)
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
    elif source_config.plugin == "zotero":
        # Phase M Wave 4 — Zotero library connector. Threads VLM/Whisper
        # exactly like the filesystem branch so the OCR Tier 2 escalation
        # works for scanned Zotero PDFs.
        from .sources.zotero import ZoteroSource  # noqa: PLC0415

        zcfg = source_config.zotero
        if zcfg is None:
            raise ValueError(
                "DatasetSourceConfig.plugin = 'zotero' requires a "
                "[datasets.sources.zotero] config block."
            )
        vlm = None
        whisper = None
        if config is not None:
            try:
                from .vlm.registry import get_active_vlm  # noqa: PLC0415

                vlm = get_active_vlm(config)
            except Exception as exc:
                logger.warning(
                    "VLM resolution failed (%s) — Zotero PDFs will use the digital-only path.",
                    exc,
                )
                vlm = None
            try:
                from .whisper.registry import get_active_whisper  # noqa: PLC0415

                whisper = get_active_whisper(config)
            except Exception as exc:
                logger.warning(
                    "Whisper resolution failed (%s) — audio attachments "
                    "would be skipped (Zotero does not currently emit audio).",
                    exc,
                )
                whisper = None
        return ZoteroSource(
            mode=zcfg.mode,
            library_path=zcfg.library_path,
            user_id=zcfg.user_id,
            api_key_env=zcfg.api_key_env,
            library_type=zcfg.library_type,
            group_id=zcfg.group_id,
            base_url=str(zcfg.base_url),
            include_attachments=zcfg.include_attachments,
            include_collections=zcfg.include_collections,
            exclude_collections=zcfg.exclude_collections,
            cache_dir=zcfg.cache_dir,
            debounce=2.0,
            vlm=vlm,
            whisper=whisper,
        )
    else:
        raise ValueError(f"Unknown source plugin: {source_config.plugin}")


# Maps source_uri scheme prefixes to feedback_sessions client names.
_SOURCE_URI_TO_CLIENT: dict[str, str] = {
    "claude-code://": "claude-code",
    "claude-code-history://": "claude-code",
    "opencode://": "opencode",
    "gemini-cli://": "gemini-cli",
    "codex-cli://": "codex-cli",
    "chatgpt-export://": "chatgpt-export",
    "jsonl-chat://": "jsonl-chat",
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
        try:
            ingest_once(config)
        except EmbedderWedged as exc:
            # Translate the circuit-breaker exception into a clean
            # ERROR log line before re-raising. Without this, the CLI
            # would dump a full traceback for what is really a clear
            # operational message — and the message itself already
            # carries the recovery hint ("re-run once the upstream
            # recovers"). Re-raise so the process exit code reflects
            # the failure.
            logger.error("Embedder circuit-breaker tripped: %s", exc)
            raise
    else:
        # Run daemon mode (would use asyncio/watchdog in real implementation)
        logger.info("Daemon mode not fully implemented in this scaffold")
        # In real implementation, we'd set up watchdog observers for each source
        # and run an event loop


if __name__ == "__main__":
    main()
