"""Ingestion orchestrator for corpus-forge."""

import hashlib
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Any

from .admin.source_caps import enforce_source_caps
from .backends.base import IngestRunInProgressError, StorageBackend
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

# SR-G5: Three structured-log loggers (names frozen for autosentry).
_run_logger = logging.getLogger("corpus_forge.ingest.run")
_checkpoint_logger = logging.getLogger("corpus_forge.ingest.checkpoint")
_lock_logger = logging.getLogger("corpus_forge.ingest.lock")

# SR-G5: Checkpoint cadence — wall-clock seconds between update_ingest_run calls
# inside the per-doc loop. Source-boundary calls are unconditional.
_CHECKPOINT_INTERVAL_S: float = 5.0


class _BackendClassProxy:
    """Proxy for a backend class that always reads through its home module.

    This is the key to satisfying three conflicting test constraints
    simultaneously:

    1. **B-13 no-eager-import**: importing ``corpus_forge.ingest`` must not
       cause ``corpus_forge.backends.sqlite`` to be imported as a side-effect.
       → The proxy is returned from ``__getattr__``, which is only invoked on
         attribute access, *not* at module load time.

    2. **SR-T9 patchability**: ``monkeypatch.setattr("corpus_forge.ingest.
       SQLiteBackend", lambda **_kw: mock)`` must cause ``ingest_once`` to use
       the mock.
       → When monkeypatch calls ``setattr(ingest_module, "SQLiteBackend", mock)``
         the lambda replaces the proxy in ``__dict__``.  ``_ingest_mod.SQLiteBackend``
         then returns the lambda, not the proxy.

    3. **B-13 patch-backends**: ``patch("corpus_forge.backends.sqlite.SQLiteBackend")``
       must still work even after monkeypatch teardown restored the proxy to
       ``__dict__``.
       → The proxy's ``__call__`` always does a fresh
         ``getattr(sys.modules[module], name)`` at call time, so it sees whatever
         the test has patched on the *backends* module.
    """

    def __init__(self, module_name: str, cls_name: str) -> None:
        self._module_name = module_name
        self._cls_name = cls_name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        import importlib  # noqa: PLC0415

        mod = sys.modules.get(self._module_name) or importlib.import_module(self._module_name)
        cls = getattr(mod, self._cls_name)
        return cls(*args, **kwargs)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<_BackendClassProxy {self._module_name}.{self._cls_name}>"


_SQLITE_BACKEND_PROXY = _BackendClassProxy("corpus_forge.backends.sqlite", "SQLiteBackend")
_POSTGRES_BACKEND_PROXY = _BackendClassProxy("corpus_forge.backends.postgres", "PostgresBackend")


def __getattr__(name: str) -> Any:
    """Lazy-load backend proxies so that:

    1. Importing ``corpus_forge.ingest`` does *not* eagerly import the backends
       (B-13 lazy-import contract).
    2. Tests can patch ``corpus_forge.ingest.SQLiteBackend`` or
       ``corpus_forge.ingest.PostgresBackend`` (SR-T9 patchability contract).
    3. Pre-SR-G5 tests that patch ``corpus_forge.backends.sqlite.SQLiteBackend``
       still work through the proxy's delegating ``__call__``.
    """
    if name == "SQLiteBackend":
        return _SQLITE_BACKEND_PROXY
    if name == "PostgresBackend":
        return _POSTGRES_BACKEND_PROXY
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class _StopController:
    """Signal-aware stop controller for ``ingest_once``.

    Installs SIGINT and SIGTERM handlers for the lifetime of an ingest run so
    that a graceful shutdown flag (``stop_requested``) can be polled by the
    ingest loop.

    Escalation counter choice: SIGINT-only.  Only Ctrl-C (SIGINT) double-taps
    escalate to ``os._exit(130)``; SIGTERM is always a polite one-shot request.
    This matches POSIX convention where SIGTERM is sent by supervisors expecting
    orderly shutdown and SIGKILL is their hard-stop fallback.

    Usage::

        with _StopController() as ctl:
            for doc in walk():
                if ctl.stop_requested:
                    break
                ingest(doc)
    """

    def __init__(self) -> None:
        self.stop_requested: bool = False
        self._installed: bool = False
        self._sigint_count: int = 0  # SIGINT-only escalation counter
        self._prev_sigint: Callable[..., Any] | signal.Handlers | int | None = None
        self._prev_sigterm: Callable[..., Any] | signal.Handlers | int | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def install_handlers(self) -> None:
        """Install SIGINT and SIGTERM handlers.

        Idempotent: a second call on the same instance is a no-op.
        No-op (no error) when called from a non-main thread, because CPython
        raises ``ValueError`` if ``signal.signal()`` is called outside the
        main thread.
        """
        if threading.current_thread() is not threading.main_thread():
            return
        if self._installed:
            return
        self._prev_sigint = signal.signal(signal.SIGINT, self._handle_signal)
        self._prev_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)
        self._installed = True

    def restore_handlers(self) -> None:
        """Restore the signal handlers that were in place before ``install_handlers()``.

        No-op if ``install_handlers()`` was never called (defensive guard for
        double-restore on exception paths).
        """
        if not self._installed:
            return
        if self._prev_sigint is not None:
            signal.signal(signal.SIGINT, self._prev_sigint)
        if self._prev_sigterm is not None:
            signal.signal(signal.SIGTERM, self._prev_sigterm)
        self._installed = False

    _SIGINT_ESCALATE_THRESHOLD: int = 2
    _SIGINT_EXIT_CODE: int = 130

    def _handle_signal(self, signum: int, frame: FrameType | None) -> None:  # noqa: ARG002  # frame is required by signal handler API
        """Signal handler installed by ``install_handlers()``.

        First call (any signal) — sets ``stop_requested = True``.
        Second SIGINT — calls ``os._exit(130)`` (POSIX Ctrl-C convention).
        Repeated SIGTERM — polite; no escalation.
        """
        self.stop_requested = True
        if signum == signal.SIGINT:
            self._sigint_count += 1
            if self._sigint_count >= self._SIGINT_ESCALATE_THRESHOLD:
                os._exit(self._SIGINT_EXIT_CODE)

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> "_StopController":
        self.install_handlers()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self.restore_handlers()


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


def _compute_config_digest(config: Config) -> str:
    """Compute SHA-256 digest of the config blob (excluding volatile daemon section).

    Used by the resume path to detect config changes between runs.
    """
    try:
        blob = config.model_dump_json(exclude={"daemon"})
        if not isinstance(blob, str):
            blob = str(blob)
        return hashlib.sha256(blob.encode()).hexdigest()
    except Exception:
        # Fallback for mock/partial configs (e.g. in unit tests).
        return hashlib.sha256(repr(config).encode()).hexdigest()


def _new_run_id() -> str:
    """Generate a fresh run ID (UUID4 hex, 32 chars)."""
    return uuid.uuid4().hex


def _source_uri_prefix_for(source: Any) -> str:
    """Derive the ingest_run_sources.source_uri_prefix for *source*.

    Convention: filesystem-rooted sources (those with a ``.root`` Path
    attribute) use ``"filesystem://<root.resolve().as_posix()>"`` so the
    prefix is unique across any two roots that share the same basename
    (e.g. ``/Users/me/Notes`` vs ``/Users/me/Archive/Notes``).
    API-based sources fall back to ``"<source.name>://<identity>"``.

    The "filesystem://" scheme is intentional: it matches the URI scheme
    used by ``FilesystemSource`` document URIs and is the convention the
    tests seed when verifying the max-scan-age skip path.

    NOTE: legacy rows written by earlier versions of corpus-forge used
    ``"filesystem://<root.name>"`` (basename only).  The
    ``find_source_last_scanned_at`` backends accept BOTH formats via a
    secondary OR clause so existing rows continue to match.
    """
    logical_name = getattr(source, "logical_name", None)
    if logical_name:  # non-None AND non-empty string (defensive)
        return f"filesystem://logical/{logical_name}"
    root: Path | None = getattr(source, "root", None)
    if root is not None:
        # Normalise to Path so callers can pass either a str or a Path.
        if not isinstance(root, Path):
            root = Path(root)
        return f"filesystem://{root.resolve().as_posix()}"
    return f"{source.name}://{source.identity()}"


def _legacy_source_uri_prefix_for(source: Any) -> str | None:
    """Return the legacy basename-only prefix for *source*, or None if N/A.

    Used as the compatibility fallback in find_source_last_scanned_at so
    old rows (written by earlier corpus-forge versions) still match after
    the _source_uri_prefix_for change to full-path URIs.
    """
    root: Path | None = getattr(source, "root", None)
    if root is not None:
        if not isinstance(root, Path):
            root = Path(root)
        return f"filesystem://{root.name}"
    return None


def ingest_once(
    config: Config,
    *,
    resume: bool = False,
    wait: bool = False,
    max_scan_age: float | None = None,
) -> None:
    """Run one-shot ingestion pass.

    Args:
        config:        Fully-resolved Config.
        resume:        If True, look for a prior interrupted/running run with the
                       same config_digest and reuse its run_id.  Falls back to a
                       fresh run if no matching unfinished run exists.
        wait:          If True, block until the concurrent-run advisory lock is
                       released instead of exiting immediately with code 75.
        max_scan_age:  Optional override for ``config.scan.max_scan_age`` (seconds).
                       Sources whose ``last_scanned_at`` is within this window are
                       skipped.  ``0`` or ``None`` → always rescan (backwards compat).
                       Negative values raise ``ValueError``.
    """
    if max_scan_age is not None and max_scan_age < 0:
        raise ValueError(
            f"max_scan_age must be >= 0 (got {max_scan_age!r}). Use 0 or None to always rescan."
        )

    logger.info("Starting one-shot ingestion pass")
    # Walk every filesystem-rooted source up front to compute the ETA
    # AND capture per-source file counts that drive the live progress
    # bar totals below. The walk cost was already paid by the previous
    # ETA-only call; threading the result avoids walking the tree a
    # second time inside ``source.scan()``.
    per_source_totals = _plan_ingest(config)

    # Setup backend.
    # We resolve backend classes through this module's __dict__ at call time so
    # that:
    #   • monkeypatch.setattr("corpus_forge.ingest.SQLiteBackend", …) works
    #     (SR-T9 patchability contract — the attribute is set into __dict__ and
    #     takes priority over the lazy __getattr__ resolver).
    #   • patch("corpus_forge.backends.sqlite.SQLiteBackend") also works (B-13
    #     lazy-import contract — __getattr__ does a fresh from-import each call,
    #     reading whatever the backends module currently exposes).
    #   • Importing corpus_forge.ingest does NOT eagerly import the backends
    #     modules (B-13 no-eager-import contract).
    _ingest_mod = sys.modules[__name__]
    backend_config = config.backend
    if backend_config.kind == "postgres":
        backend = _ingest_mod.PostgresBackend(dsn=backend_config.dsn, schema=backend_config.schema)
    elif backend_config.kind == "sqlite":
        # `backend_config.dsn` doubles as the SQLite file path
        # (e.g. "~/Library/Application Support/corpus-forge/corpus.db").
        backend = _ingest_mod.SQLiteBackend(path=backend_config.dsn, schema=backend_config.schema)
    else:
        raise ValueError(f"Unsupported backend kind: {backend_config.kind}")

    # SR-G5: Acquire the ingest-run advisory lock BEFORE any DB writes.
    # On contention (wait=False default): log + exit 75 (POSIX EX_TEMPFAIL).
    # On wait=True: block until the lock is released.
    #
    # D1 FIX: ``backend.lock_source(...)`` is a ``@contextlib.contextmanager``
    # — calling it only *creates* the generator; it does NOT acquire the lock.
    # The ``IngestRunInProgressError`` is raised INSIDE the generator body when
    # ``with lock_ctx:`` is entered (i.e. when the generator advances to its
    # first ``yield``).  Wrapping the factory call in try/except would never
    # catch anything; the try/except MUST wrap the ``with`` statement itself.
    host = socket.gethostname()
    lock_key = f"ingest-run://{host}"

    # SR-G5 / D1 FIX: Both the factory call AND the ``with lock_ctx:`` entry
    # are wrapped in the same try/except so that unit tests that set
    # ``backend.lock_source.side_effect = IngestRunInProgressError(...)``
    # (which raises at factory-call time) AND production code paths where the
    # error fires inside the @contextmanager body at ``yield`` time both route
    # to the same contention handler.
    #
    # ``@contextmanager`` semantics: calling the function only creates the
    # generator; it does NOT run the body.  ``IngestRunInProgressError``
    # fires DURING ``with lock_ctx:`` entry in production.  Unit-test mocks
    # may raise it on the call itself — wrapping both sites is the minimal
    # fix that works for both.
    _exc_to_reraise: BaseException | None = None
    try:
        lock_ctx = backend.lock_source(lock_key, wait=wait)
        with lock_ctx:
            _lock_logger.info(
                json.dumps({"event": "ingest_run_acquired", "host": host, "lock_key": lock_key})
            )
            try:
                # Apply migrations inside the lock so schema changes are serialised.
                backend.migrate()

                # DR-G6: Mark stale runs AFTER migrate (columns must exist) and
                # BEFORE latest_unfinished_ingest_run (so ghost runs are cleared
                # before the resume lookup). Always-call: the backend owns the
                # threshold<=0 no-op short-circuit (C5).
                _stale_threshold = config.scan.stale_run_threshold
                _stale_raw = backend.mark_stale_runs(_stale_threshold, host=host)
                try:
                    _stale_count = int(_stale_raw) if _stale_raw is not None else 0
                except (TypeError, ValueError):
                    _stale_count = 0
                if _stale_count > 0:
                    logger.info("marked %d stale ingest run(s) as failed", _stale_count)

                # Get active embedders
                embedders = get_active_embedders(config)
                logger.info(f"Active embedders: {[e.name for e in embedders]}")

                # SR-G5: Config digest + run-id management.
                config_digest = _compute_config_digest(config)
                run_id: str | None = None

                if resume:
                    prior = backend.latest_unfinished_ingest_run(host=host)
                    if prior is not None:
                        if prior["config_digest"] == config_digest:
                            # Resume: reuse the existing run_id.
                            run_id = prior["run_id"]
                            logger.info("Resuming prior interrupted run (run_id=%s)", run_id)
                        else:
                            logger.warning(
                                "Prior interrupted run found (run_id=%s) but config_digest "
                                "mismatch (stored=%s current=%s) — starting fresh.",
                                prior["run_id"],
                                prior["config_digest"][:12],
                                config_digest[:12],
                            )
                    else:
                        logger.info(
                            "No resumable run found (empty table or all completed)"
                            " — starting fresh."
                        )

                if run_id is None:
                    run_id = _new_run_id()

                pid = os.getpid()
                backend.start_ingest_run(
                    run_id=run_id,
                    host=host,
                    pid=pid,
                    config_digest=config_digest,
                )

                _run_logger.info(
                    json.dumps(
                        {
                            "event": "run_started",
                            "run_id": run_id,
                            "host": host,
                            "pid": pid,
                            "resume": resume,
                            "config_digest": config_digest[:16],
                        }
                    )
                )

                # Determine effective max_scan_age: kwarg overrides config field.
                if max_scan_age is not None:
                    effective_max_scan_age = float(max_scan_age)
                else:
                    # Try ScanConfig.max_scan_age if present; default 0.0.
                    # Guard against MagicMock / non-float values (unit tests).
                    scan_cfg = getattr(config, "scan", None)
                    raw_age = (
                        getattr(scan_cfg, "max_scan_age", 0.0) if scan_cfg is not None else 0.0
                    )
                    try:
                        effective_max_scan_age = float(raw_age)
                    except (TypeError, ValueError):
                        effective_max_scan_age = 0.0

                run_error: str | None = None
                run_status = "completed"

                with _StopController() as stop_ctl:
                    try:
                        # SR-G5 ingest loop — inlined (not a separate function) so that
                        # ``inspect.getsource(ingest_once)`` sees the progress-update
                        # finally blocks (required by test_ingest_helpers structural checks).
                        #
                        # Single Rich ``Progress`` instance with TWO live tasks:
                        #   1. A persistent global task (overall %).
                        #   2. A transient per-source task (current source slice).
                        global_total = sum(per_source_totals.values()) or None
                        run_start_t = time.monotonic()

                        with make_progress(
                            "Ingest (all sources)",
                            total=global_total,
                            logger=scan_logger,
                        ) as progress:
                            global_task = progress.add_task("Total", total=global_total)

                            # Process each dataset
                            for dataset in config.datasets:
                                if stop_ctl.stop_requested:
                                    break
                                logger.info(f"Processing dataset: {dataset.name} ({dataset.kind})")

                                # Get or create dataset record
                                dataset_id = _get_or_create_dataset(backend, dataset)

                                # Process each source in dataset
                                for source_config in dataset.sources:
                                    if stop_ctl.stop_requested:
                                        break
                                    scan_logger.info(
                                        "Scanning source: plugin=%s dataset=%s",
                                        source_config.plugin,
                                        dataset.name,
                                    )

                                    # Instantiate source
                                    source = _instantiate_source(source_config, config=config)

                                    # Register source in the DB (idempotent)
                                    backend.register_source(
                                        dataset_id,
                                        source.name,
                                        source.identity(),
                                        socket.gethostname(),
                                    )

                                    # Get chunker for this source
                                    chunker = get_chunker_for_source(source, config)

                                    # SR-G5: max_scan_age skip — check source freshness.
                                    source_prefix = _source_uri_prefix_for(source)
                                    should_skip = False
                                    prior_scanned_at: datetime | None = None
                                    if effective_max_scan_age > 0:
                                        try:
                                            _raw_scanned = backend.find_source_last_scanned_at(
                                                source_prefix
                                            )
                                            # Accept only real datetime objects.
                                            if isinstance(_raw_scanned, datetime):
                                                prior_scanned_at = _raw_scanned
                                            # Compatibility: fall back to legacy basename-only
                                            # prefix for rows written by older corpus-forge
                                            # versions that used "filesystem://<root.name>".
                                            if prior_scanned_at is None:
                                                _legacy_prefix = _legacy_source_uri_prefix_for(
                                                    source
                                                )
                                                if _legacy_prefix is not None:
                                                    _raw_legacy = (
                                                        backend.find_source_last_scanned_at(
                                                            _legacy_prefix
                                                        )
                                                    )
                                                    if isinstance(_raw_legacy, datetime):
                                                        prior_scanned_at = _raw_legacy
                                        except Exception as exc:
                                            logger.debug(
                                                "find_source_last_scanned_at failed for "
                                                "%s: %r — not skipping",
                                                source_prefix,
                                                exc,
                                            )
                                        if prior_scanned_at is not None:
                                            now_ts = datetime.now(UTC)
                                            # Ensure timezone-aware comparison.
                                            if prior_scanned_at.tzinfo is None:
                                                prior_scanned_at = prior_scanned_at.replace(
                                                    tzinfo=UTC
                                                )
                                            try:
                                                elapsed_s = (
                                                    now_ts - prior_scanned_at
                                                ).total_seconds()
                                            except (TypeError, ValueError):
                                                elapsed_s = float("inf")
                                            if elapsed_s < effective_max_scan_age:
                                                should_skip = True
                                                scan_logger.info(
                                                    "Skipping source %s (scanned %.0fs ago, "
                                                    "max_scan_age=%.0fs)",
                                                    source_prefix,
                                                    elapsed_s,
                                                    effective_max_scan_age,
                                                )

                                    if should_skip:
                                        # Record row with finished=True for --status.
                                        try:
                                            backend.upsert_ingest_run_source(
                                                run_id=run_id,
                                                source_uri_prefix=source_prefix,
                                                dataset_id=dataset_id,
                                                last_scanned_at=prior_scanned_at,
                                                finished=True,
                                            )
                                        except Exception as exc:
                                            logger.debug(
                                                "upsert_ingest_run_source (skip) failed: %r",
                                                exc,
                                            )
                                        continue

                                    # SR-G5: checkpoint state tracking
                                    last_checkpoint_at = time.monotonic()
                                    docs_done = 0
                                    source_total = per_source_totals.get(id(source_config))

                                    # Start-of-source checkpoint (boundary, unconditional).
                                    try:
                                        backend.update_ingest_run(
                                            run_id,
                                            last_op="scan",
                                            last_done=docs_done,
                                            last_total=source_total,
                                        )
                                    except Exception as exc:
                                        logger.debug(
                                            "checkpoint write failed (source start): %r", exc
                                        )

                                    # Per-source task for the duration of this source.
                                    raw_items = source.scan()
                                    docs_chunked = 0
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

                                    # Outer try/finally guarantees end-of-source embed
                                    # flush runs even when the source iterator raises.
                                    try:
                                        for raw in raw_items:
                                            if stop_ctl.stop_requested:
                                                break
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
                                                docs_done += 1
                                                if docs_chunked % 100 == 0:
                                                    chunk_logger.info(
                                                        "Chunked %d documents so far",
                                                        docs_chunked,
                                                    )
                                                # Batched embed flush.
                                                if (
                                                    docs_chunked % _FLUSH_EMBEDDINGS_EVERY_N_FILES
                                                    == 0
                                                ):
                                                    try:
                                                        _flush_all_pending_embeddings(
                                                            backend, embedders
                                                        )
                                                    except EmbedderWedged:
                                                        raise
                                                    except Exception as flush_exc:
                                                        logger.warning(
                                                            "Embed flush failed at file "
                                                            "%d: %r — next flush will "
                                                            "retry the backlog",
                                                            docs_chunked,
                                                            flush_exc,
                                                        )
                                            except EmbedderWedged:
                                                raise
                                            except Exception as e:
                                                _classify_and_log_ingest_error(raw, e)
                                            finally:
                                                progress.update(source_task, advance=1)
                                                progress.update(global_task, advance=1)

                                            # SR-G5: cadence-gated checkpoint.
                                            now_t = time.monotonic()
                                            if now_t - last_checkpoint_at >= _CHECKPOINT_INTERVAL_S:
                                                elapsed_s = now_t - run_start_t
                                                try:
                                                    backend.update_ingest_run(
                                                        run_id,
                                                        last_op="scan",
                                                        last_done=docs_done,
                                                        last_total=source_total,
                                                    )
                                                except Exception as exc:
                                                    logger.debug("checkpoint write failed: %r", exc)
                                                _checkpoint_logger.info(
                                                    json.dumps(
                                                        {
                                                            "event": "checkpoint",
                                                            "run_id": run_id,
                                                            "last_op": "scan",
                                                            "last_done": int(docs_done),
                                                            "last_total": source_total,
                                                            "elapsed_s": elapsed_s,
                                                        }
                                                    )
                                                )
                                                last_checkpoint_at = now_t

                                            if stop_ctl.stop_requested:
                                                break

                                    finally:
                                        # End-of-source embed flush.
                                        try:
                                            _flush_all_pending_embeddings(backend, embedders)
                                        except EmbedderWedged:
                                            raise
                                        except Exception as flush_exc:
                                            logger.warning(
                                                "End-of-source flush failed: %r — "
                                                "trailing chunks stay pending for "
                                                "next ingest pass",
                                                flush_exc,
                                            )

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

                                    # SR-G5: end-of-source boundary checkpoint (unconditional).
                                    now_t = time.monotonic()
                                    elapsed_s = now_t - run_start_t
                                    try:
                                        backend.update_ingest_run(
                                            run_id,
                                            last_op="finalize",
                                            last_done=docs_done,
                                            last_total=source_total,
                                        )
                                    except Exception as exc:
                                        logger.debug(
                                            "checkpoint write failed (source finish): %r", exc
                                        )
                                    _checkpoint_logger.info(
                                        json.dumps(
                                            {
                                                "event": "checkpoint",
                                                "run_id": run_id,
                                                "last_op": "finalize",
                                                "last_done": int(docs_done),
                                                "last_total": source_total,
                                                "elapsed_s": elapsed_s,
                                            }
                                        )
                                    )

                                    # SR-G5: record per-source scan completion.
                                    now_dt = datetime.now(UTC)
                                    try:
                                        backend.upsert_ingest_run_source(
                                            run_id=run_id,
                                            source_uri_prefix=source_prefix,
                                            dataset_id=dataset_id,
                                            last_scanned_at=now_dt,
                                            docs_seen_delta=docs_done,
                                            finished=True,
                                        )
                                    except Exception as exc:
                                        logger.debug(
                                            "upsert_ingest_run_source (finish) failed: %r", exc
                                        )

                                    # RFC rfc-corpus-growth-controls: per-source cap.
                                    if (
                                        getattr(source_config, "max_rows", None) is not None
                                        or getattr(source_config, "max_bytes", None) is not None
                                    ):
                                        try:
                                            cap_report = enforce_source_caps(
                                                backend, dataset_id, source_config
                                            )
                                            if cap_report.rows_evicted:
                                                logger.info(
                                                    "cap enforcement: evicted %d rows "
                                                    "(%d bytes) from %s "
                                                    "(cap_max_rows=%s cap_max_bytes=%s "
                                                    "reason=%s)",
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

                                    # Remove per-source task before next source.
                                    progress.remove_task(source_task)

                        if stop_ctl.stop_requested:
                            run_status = "interrupted"
                    except Exception as exc:
                        run_status = "failed"
                        run_error = f"{type(exc).__name__}: {exc}"
                        raise
                    finally:
                        backend.finish_ingest_run(run_id, status=run_status, error=run_error)  # type: ignore[arg-type]
                        _run_logger.info(
                            json.dumps(
                                {
                                    "event": "run_finished",
                                    "run_id": run_id,
                                    "status": run_status,
                                    "error": run_error,
                                }
                            )
                        )
            except (
                BaseException
            ) as _ingest_exc:  # deliberate catch-and-defer: see SR-G5 lock-release comment above
                # Capture the exception so ``with lock_ctx:`` can exit normally,
                # guaranteeing the lock's teardown code (``@contextmanager`` bodies
                # after ``yield``) always executes. Re-raised immediately after.
                _exc_to_reraise = _ingest_exc

    except IngestRunInProgressError:
        # D1 FIX: IngestRunInProgressError is raised DURING ``with lock_ctx:``
        # entry (inside the @contextmanager body before yield), not during the
        # factory call.  The try/except must wrap the ``with`` statement.
        _lock_logger.warning(
            json.dumps(
                {
                    "event": "ingest_run_contention",
                    "host": host,
                    "message": "another ingest run is in progress on this host",
                }
            )
        )
        logger.warning(
            "Another ingest run is in progress on this host (%s) — "
            "exiting with code 75 (EX_TEMPFAIL). "
            "Use --wait to block until the running ingest finishes.",
            host,
        )
        sys.exit(75)

    # Lock released here — ``with lock_ctx:`` has exited normally.
    _lock_logger.info(
        json.dumps({"event": "ingest_run_released", "host": host, "lock_key": lock_key})
    )
    if _exc_to_reraise is not None:
        raise _exc_to_reraise


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
            scan_config=config.scan if config is not None else None,
            vlm=vlm,
            whisper=whisper,
            debounce=2.0,
            # Multi-machine ingest: propagate the per-source logical_name
            # (None when unset) so ``_source_uri_prefix_for`` can emit
            # ``filesystem://logical/<name>`` and converge cross-host rows
            # in ``ingest_run_sources``. Without this propagation,
            # ``DatasetSourceConfig.logical_name`` is silently dropped at
            # source-construction time and the feature is a no-op end-to-end.
            logical_name=getattr(source_config, "logical_name", None),
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


def main(
    once: bool = False,
    *,
    resume: bool = False,
    wait: bool = False,
    max_scan_age: float | None = None,
) -> None:
    """Main entry point for ingestion.

    Args:
        once:          Run one-shot ingestion pass; without it the process stays
                       resident and watches for filesystem changes.
        resume:        If True, attempt to resume from the latest non-completed run
                       (forwarded to :func:`ingest_once`).  Requires ``once=True``.
        wait:          If True, block on lock contention rather than exiting fast
                       (forwarded to :func:`ingest_once`).  Requires ``once=True``.
        max_scan_age:  Per-invocation override for ``config.scan.max_scan_age``
                       (seconds).  ``None`` means use the config value (default).
                       ``0.0`` means always rescan (overrides config).  Forwarded
                       to :func:`ingest_once` directly without conversion.
    """

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
            ingest_once(
                config,
                resume=resume,
                wait=wait,
                max_scan_age=max_scan_age,
            )
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


# ---------------------------------------------------------------------------
# SR-G6: --status read-only helpers
# ---------------------------------------------------------------------------


def _build_backend_for_status(config: Config) -> StorageBackend:
    """Build the appropriate backend for a read-only ``--status`` query.

    Deliberately does NOT call ``backend.migrate()`` — status is read-only.
    """
    _ingest_mod = sys.modules[__name__]
    backend_config = config.backend
    if backend_config.kind == "postgres":
        return _ingest_mod.PostgresBackend(  # type: ignore[attr-defined]
            dsn=backend_config.dsn, schema=backend_config.schema
        )
    elif backend_config.kind == "sqlite":
        return _ingest_mod.SQLiteBackend(  # type: ignore[attr-defined]
            path=backend_config.dsn, schema=backend_config.schema
        )
    else:
        raise ValueError(f"Unsupported backend kind: {backend_config.kind!r}")


def _render_status(
    run: dict[str, Any],
    sources: list[dict[str, Any]],
    *,
    stale_threshold: float = 0.0,
) -> str:
    """Render a human-readable two-section status table.

    Args:
        run:              A single ``ingest_runs`` row as a dict.
        sources:          Zero or more ``ingest_run_sources`` rows as dicts.
        stale_threshold:  Seconds after which a running row with no heartbeat
                          is considered STALE. ``0.0`` disables the inference.

    Returns:
        A multi-line string suitable for printing to stdout.
    """
    lines: list[str] = []

    # ── Section 1: Latest ingest run ──────────────────────────────────────
    lines.append("=== Latest Ingest Run ===")

    run_id = run.get("run_id", "?")
    status = str(run.get("status", "?"))
    host = run.get("host", "?")
    pid = run.get("pid", "?")
    started_at = run.get("started_at", "?")
    ended_at = run.get("ended_at")
    last_op = run.get("last_op", "?")
    last_done = run.get("last_done", 0) or 0
    last_total = run.get("last_total")
    error = run.get("error")
    last_progress_at = run.get("last_progress_at")

    # DR-G6 §C7: STALE inference — read-only, strict > (not >=).
    # Only applies to running rows; threshold=0.0 disables entirely.
    is_stale = False
    stale_minutes: int = 0
    if status == "running" and stale_threshold > 0.0 and last_progress_at is not None:
        now_utc = datetime.now(UTC)
        elapsed = (now_utc - last_progress_at).total_seconds()
        if elapsed > stale_threshold:
            is_stale = True
            stale_minutes = int(elapsed // 60)

    # Format progress
    if last_total is None:
        progress_str = f"{last_done}/?"
    elif last_total == 0:
        progress_str = f"{last_done}/{last_total} (0.0%)"
    else:
        pct = last_done / last_total * 100
        progress_str = f"{last_done}/{last_total} ({pct:.1f}%)"

    # Format ended_at: use em-dash for NULL
    ended_str = str(ended_at) if ended_at is not None else "—"

    status_label = status.upper()
    if is_stale:
        status_label = f"{status_label} (STALE — last progress {stale_minutes} min ago)"

    lines.append(f"  run_id:     {run_id}")
    lines.append(f"  status:     {status_label}")
    lines.append(f"  host:       {host}")
    lines.append(f"  pid:        {pid}")
    lines.append(f"  started_at: {started_at}")
    lines.append(f"  ended_at:   {ended_str}")
    lines.append(f"  last_op:    {last_op}")
    lines.append(f"  progress:   {progress_str}")

    if status == "interrupted":
        lines.append("")
        lines.append("  ** Run was INTERRUPTED. Use --resume to continue. **")

    if status == "failed" and error:
        lines.append("")
        lines.append(f"  error: {error}")

    # ── Section 2: Per-source rows ─────────────────────────────────────────
    lines.append("")
    lines.append("=== Sources ===")
    if not sources:
        lines.append("  (no per-source data)")
    else:
        for src in sources:
            uri = src.get("source_uri_prefix", "?")
            docs_seen = src.get("docs_seen", 0)
            docs_skipped = src.get("docs_skipped", 0)
            docs_failed = src.get("docs_failed", 0)
            last_scanned = src.get("last_scanned_at", "—") or "—"
            finished = src.get("finished_at", "—") or "—"
            lines.append(f"  {uri}")
            lines.append(
                f"    seen={docs_seen}  skipped={docs_skipped}  failed={docs_failed}"
                f"  last_scanned={last_scanned}  finished={finished}"
            )

    return "\n".join(lines)


def print_ingest_status(
    config: Config | None,
    *,
    json_output: bool = False,
    stale_threshold: float | None = None,
) -> None:
    """Print the latest ingest run status to stdout.

    Read-only: never calls ``migrate()`` or any write method on the backend.

    Args:
        config:           Fully-resolved Config, or ``None`` when no config file
                          exists yet (no-setup state).  When ``None`` the function
                          emits a "no runs found" response identical to an empty DB
                          so that ``--status`` remains useful before ``setup`` is run.
        json_output:      If True, emit a single JSON document instead of the
                          human-readable two-section table.
        stale_threshold:  Seconds after which a running row with no heartbeat is
                          considered STALE.  ``None`` → read from
                          ``config.scan.stale_run_threshold`` (or 900.0 if no
                          config).  ``0.0`` disables the inference entirely.
    """
    if config is None:
        # No config file — treat identically to an empty DB.
        if json_output:
            print(json.dumps({"run": None, "sources": []}))
        else:
            print("no runs found")
        return

    # DR-G6 §C8: Resolve the effective stale threshold.
    if stale_threshold is None:
        try:
            effective_stale_threshold = float(config.scan.stale_run_threshold)
        except (AttributeError, TypeError, ValueError):
            effective_stale_threshold = 900.0
    else:
        effective_stale_threshold = stale_threshold

    backend = _build_backend_for_status(config)

    run: dict[str, Any] | None = backend.latest_ingest_run()
    sources: list[dict[str, Any]] = []
    if run is not None:
        # Accept either plausible method name for source listing.
        if hasattr(backend, "list_ingest_run_sources"):
            sources = backend.list_ingest_run_sources(run["run_id"]) or []
        elif hasattr(backend, "get_ingest_run_sources"):
            sources = backend.get_ingest_run_sources(run["run_id"]) or []

    if json_output:
        # SQLiteBackend.latest_ingest_run() returns datetime objects for
        # timestamp fields; json.dumps needs a custom encoder to serialize them.
        # We use isoformat() for datetime/date and str() as a safe fallback for
        # any other non-serializable type (e.g. Decimal, UUID).
        def _json_default(obj: object) -> str:
            if hasattr(obj, "isoformat"):
                return obj.isoformat()  # type: ignore[union-attr]
            return str(obj)

        # DR-G6 §C7: Add "stale": true to run object when predicate fires.
        # OMIT the key entirely when not stale (never emit "stale": false).
        run_payload: dict[str, Any] | None = run
        if run is not None:
            run_status = str(run.get("status", ""))
            run_payload = dict(run)
            if run_status == "running" and effective_stale_threshold > 0.0:
                lpa = run.get("last_progress_at")
                if lpa is not None:
                    now_utc = datetime.now(UTC)
                    elapsed = (now_utc - lpa).total_seconds()
                    if elapsed > effective_stale_threshold:
                        run_payload["stale"] = True

        print(json.dumps({"run": run_payload, "sources": sources}, default=_json_default))
        return

    if run is None:
        print("no runs found")
        return

    print(_render_status(run, sources, stale_threshold=effective_stale_threshold))


if __name__ == "__main__":
    main()
