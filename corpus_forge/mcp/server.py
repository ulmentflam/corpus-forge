"""Phase R5 — corpus-forge MCP server (in-process, stdio transport).

This module exposes :func:`build_server`, a pure factory that returns a
fully-configured :class:`mcp.server.Server` with three tools:

- ``search`` — hybrid dense+lexical retrieval (R2/R4 stack).
- ``get_chunk`` — chunk lookup by primary id.
- ``list_datasets`` — backend catalogue enumeration.

Discipline carried over from R4
-------------------------------

- **Default-off reranker**: ``rerank=False`` is the default on every
  tool call.  The reranker builder is *never* invoked unless
  ``rerank=True`` flows through — and even then, only once per server
  lifetime (memoized).
- **Lazy retriever**: the retriever builder is invoked on the FIRST
  tool dispatch, not at ``build_server`` time.  Constructing a
  retriever pulls a backend + embedder model load; we want
  ``corpus-forge mcp serve`` to start fast.
- **Side-effect-free import**: importing this module pulls in the
  third-party ``mcp`` package but does NOT construct retrievers,
  embedders, or rerankers.  Suitable for ``--help`` paths.

The ``serve_stdio`` entry point (kept for backwards-compat with the
Wave 1 scaffold) now wires through ``build_server`` and the real CLI
defaults (``_build_retriever_for_eval`` + ``_build_reranker_from_config``).
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server import Server


# ── JSON schemas for the three tools ─────────────────────────────────────


_SEARCH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Natural-language search query.",
        },
        "k": {
            "type": "integer",
            "description": "Number of hits to return (default: 10).",
            "minimum": 1,
        },
        "dataset": {
            "type": "string",
            "description": "Optional dataset name filter; omitted = search all.",
        },
        "fusion": {
            "type": "string",
            "enum": ["rrf", "alpha"],
            "description": "Fusion strategy for dense + lexical hits (default: rrf).",
        },
        "alpha": {
            "type": "number",
            "description": (
                "Alpha-fusion weight in [0, 1] (default: 0.5; only used when fusion=alpha)."
            ),
        },
        "rerank": {
            "type": "boolean",
            "description": "Enable cross-encoder rerank (default: false; load is lazy).",
        },
        "rerank_top_n": {
            "type": "integer",
            "description": "Fused pool size passed to the reranker (default: 50).",
            "minimum": 1,
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


_GET_CHUNK_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chunk_id": {
            "type": "integer",
            "description": "Primary key of the chunk to retrieve.",
        },
    },
    "required": ["chunk_id"],
    "additionalProperties": False,
}


_LIST_DATASETS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


# ── JSON schemas for the eight write tools ────────────────────────────────

_ADD_LABEL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entity_type": {"type": "string", "enum": ["chunk", "document", "conversation"]},
        "entity_id": {"type": "integer"},
        "namespace": {"type": "string"},
        "value": {"type": "string"},
        "confidence": {"type": "number"},
        "dry_run": {"type": "boolean"},
    },
    "required": ["entity_type", "entity_id", "namespace", "value"],
    "additionalProperties": False,
}

_REMOVE_LABEL_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entity_type": {"type": "string", "enum": ["chunk", "document", "conversation"]},
        "entity_id": {"type": "integer"},
        "namespace": {"type": "string"},
        "value": {"type": "string"},
        "dry_run": {"type": "boolean"},
    },
    "required": ["entity_type", "entity_id", "namespace", "value"],
    "additionalProperties": False,
}

_SET_METADATA_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entity_type": {"type": "string", "enum": ["chunk", "document", "conversation"]},
        "entity_id": {"type": "integer"},
        "key": {"type": "string"},
        "value": {},
        "dry_run": {"type": "boolean"},
    },
    "required": ["entity_type", "entity_id", "key", "value"],
    "additionalProperties": False,
}

_SET_DESCRIPTION_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entity_type": {"type": "string", "enum": ["chunk", "document", "conversation"]},
        "entity_id": {"type": "integer"},
        "text": {"type": ["string", "null"]},
        "dry_run": {"type": "boolean"},
    },
    "required": ["entity_type", "entity_id", "text"],
    "additionalProperties": False,
}

_LIST_LABELS_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entity_type": {
            "type": "string",
            "enum": ["chunk", "document", "conversation"],
        },
        "namespace": {"type": "string"},
    },
    "additionalProperties": False,
}

_APPEND_CONVERSATION_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "dataset": {"type": "string"},
        "title": {"type": "string"},
        "messages": {"type": "array", "items": {"type": "object"}},
        "started_at": {"type": "string"},
        "metadata": {"type": "object"},
        "labels": {"type": "array"},
        "dry_run": {"type": "boolean"},
    },
    "required": ["dataset", "title", "messages"],
    "additionalProperties": False,
}

_APPEND_MESSAGE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "conversation_id": {"type": "integer"},
        "role": {"type": "string"},
        "content": {"type": "string"},
        "tool_calls": {"type": "array"},
        "tool_results": {"type": "array"},
        "ts": {"type": "string"},
        "metadata": {"type": "object"},
        "dry_run": {"type": "boolean"},
    },
    "required": ["conversation_id", "role", "content"],
    "additionalProperties": False,
}

_ADD_FEEDBACK_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entity_type": {
            "type": "string",
            "enum": ["chunk", "document", "conversation", "message"],
        },
        "entity_id": {"type": "integer"},
        "kind": {"type": "string"},
        "rating": {"type": "integer"},
        "text": {"type": "string"},
        "metadata": {"type": "object"},
        "dry_run": {"type": "boolean"},
    },
    "required": ["entity_type", "entity_id", "kind"],
    "additionalProperties": False,
}


# ── Helpers ──────────────────────────────────────────────────────────────


def _hit_to_dict(hit: Any) -> dict[str, Any]:
    """Serialize a retrieval ``Hit`` (or stand-in) to a JSON-safe dict.

    The MCP wire format requires plain JSON; we cannot ship the frozen
    dataclass directly.  We pluck the load-bearing fields so the
    response shape stays stable even if ``Hit`` grows columns.
    """
    return {
        "chunk_id": int(hit.chunk_id),
        "score": float(hit.score),
        "text": hit.text,
        "document_id": getattr(hit, "document_id", None),
        "conversation_id": getattr(hit, "conversation_id", None),
        "message_id": getattr(hit, "message_id", None),
        "source_uri": getattr(hit, "source_uri", None),
        "title": getattr(hit, "title", None),
        "dataset_id": int(hit.dataset_id),
        "metadata": dict(getattr(hit, "metadata", {}) or {}),
        "source": getattr(hit, "source", "fused"),
    }


def _error_result(message: str) -> Any:
    """Build a ``CallToolResult`` flagged ``isError=True`` with a text block."""
    from mcp import types as mt  # local import — heavy module

    return mt.CallToolResult(
        content=[mt.TextContent(type="text", text=message)],
        isError=True,
    )


# ── Public factory ───────────────────────────────────────────────────────


def build_server(
    *,
    retriever_builder: Callable[[], Any],
    reranker_builder: Callable[[], Any] | None = None,
    default_dataset: str | None = None,
    writes_enabled: bool = False,
) -> Server[Any]:
    """Construct a fully-configured MCP server.

    Args:
        retriever_builder: zero-arg callable returning a ``Retriever``.
            Invoked lazily on the first tool dispatch (NOT at build time).
            Memoized after the first call.
        reranker_builder: optional zero-arg callable returning a
            ``Reranker``.  Invoked lazily on the first tool call with
            ``rerank=True`` (memoized).  When ``rerank=False`` flows
            through, the builder is never called — preserving the
            default-off rerank discipline from R4.
        default_dataset: optional default dataset name.  When the caller
            does not supply ``dataset`` in a ``search`` call, this value
            is used.  ``None`` = no default filter.
        writes_enabled: when ``True``, the 8 write tools (add_label,
            remove_label, set_metadata, set_description, list_labels,
            append_conversation, append_message, add_feedback) are
            registered alongside the 3 read tools.  Defaults to
            ``False`` (production-safe: write tools are never exposed
            unless explicitly opted in).

    Returns:
        ``mcp.server.Server`` instance with name ``"corpus-forge"`` and
        ``search`` / ``get_chunk`` / ``list_datasets`` tools registered
        (plus 8 write tools when ``writes_enabled=True``).
    """
    from mcp import types as mt
    from mcp.server import Server

    from corpus_forge.retrieval.types import SearchOptions

    server: Server[Any] = Server("corpus-forge")

    # Lazy / memoized state.  We keep these in closure-scoped containers
    # so the inner async handlers can mutate without `nonlocal` boilerplate.
    state: dict[str, Any] = {"retriever": None, "reranker": None}

    def _get_retriever() -> Any:
        if state["retriever"] is None:
            state["retriever"] = retriever_builder()
        return state["retriever"]

    def _get_reranker() -> Any | None:
        if reranker_builder is None:
            return None
        if state["reranker"] is None:
            state["reranker"] = reranker_builder()
        return state["reranker"]

    # ── tool registration ────────────────────────────────────────────

    @server.list_tools()
    async def _list_tools() -> list[mt.Tool]:
        tools = [
            mt.Tool(
                name="search",
                description=(
                    "Hybrid dense + lexical retrieval over the configured corpus. "
                    "Returns ranked chunks with text, score, and source metadata. "
                    "Reranker is OFF by default; set rerank=true to enable."
                ),
                inputSchema=_SEARCH_INPUT_SCHEMA,
            ),
            mt.Tool(
                name="get_chunk",
                description=(
                    "Fetch a single chunk by primary id.  Returns the chunk's "
                    "text, content hash, dataset, and metadata."
                ),
                inputSchema=_GET_CHUNK_INPUT_SCHEMA,
            ),
            mt.Tool(
                name="list_datasets",
                description=(
                    "Enumerate datasets known to the backend.  Returns name, "
                    "kind, description, and document/chunk counts."
                ),
                inputSchema=_LIST_DATASETS_INPUT_SCHEMA,
            ),
        ]
        if writes_enabled:
            tools += [
                mt.Tool(
                    name="add_label",
                    description="Attach a label to an entity (chunk, document, or conversation).",
                    inputSchema=_ADD_LABEL_INPUT_SCHEMA,
                ),
                mt.Tool(
                    name="remove_label",
                    description="Remove a label from an entity.",
                    inputSchema=_REMOVE_LABEL_INPUT_SCHEMA,
                ),
                mt.Tool(
                    name="set_metadata",
                    description="Merge a single key into an entity's metadata JSON.",
                    inputSchema=_SET_METADATA_INPUT_SCHEMA,
                ),
                mt.Tool(
                    name="set_description",
                    description="Set or clear the description of an entity.",
                    inputSchema=_SET_DESCRIPTION_INPUT_SCHEMA,
                ),
                mt.Tool(
                    name="list_labels",
                    description=(
                        "List applied labels with optional entity_type / namespace filters."
                    ),
                    inputSchema=_LIST_LABELS_INPUT_SCHEMA,
                ),
                mt.Tool(
                    name="append_conversation",
                    description="Create a new conversation with messages in the named dataset.",
                    inputSchema=_APPEND_CONVERSATION_INPUT_SCHEMA,
                ),
                mt.Tool(
                    name="append_message",
                    description="Append a single message to an existing conversation.",
                    inputSchema=_APPEND_MESSAGE_INPUT_SCHEMA,
                ),
                mt.Tool(
                    name="add_feedback",
                    description="Record user feedback (rating or text) on an entity.",
                    inputSchema=_ADD_FEEDBACK_INPUT_SCHEMA,
                ),
            ]
        return tools

    @server.call_tool(validate_input=True)
    async def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
        if name == "search":
            return await _dispatch_search(arguments)
        if name == "get_chunk":
            return await _dispatch_get_chunk(arguments)
        if name == "list_datasets":
            return await _dispatch_list_datasets(arguments)
        if writes_enabled:
            if name == "add_label":
                return await _dispatch_add_label(arguments)
            if name == "remove_label":
                return await _dispatch_remove_label(arguments)
            if name == "set_metadata":
                return await _dispatch_set_metadata(arguments)
            if name == "set_description":
                return await _dispatch_set_description(arguments)
            if name == "list_labels":
                return await _dispatch_list_labels(arguments)
            if name == "append_conversation":
                return await _dispatch_append_conversation(arguments)
            if name == "append_message":
                return await _dispatch_append_message(arguments)
            if name == "add_feedback":
                return await _dispatch_add_feedback(arguments)
        return _error_result(f"unknown tool: {name!r}")

    # ── dispatchers (closures share `_get_retriever` / `_get_reranker`)

    async def _dispatch_search(arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments["query"]
        # Resolve knobs with sensible defaults; the per-call SearchOptions
        # default to k=10, fusion=rrf, alpha=0.5, rerank=False.
        k = int(arguments.get("k", 10))
        dataset = arguments.get("dataset", default_dataset)
        fusion = arguments.get("fusion", "rrf")
        alpha = float(arguments.get("alpha", 0.5))
        rerank = bool(arguments.get("rerank", False))
        rerank_top_n = int(arguments.get("rerank_top_n", 50))

        retriever = _get_retriever()

        # Default-off rerank: only build (and attach) the reranker when
        # rerank=True actually flows through.  We attach lazily to the
        # retriever's ``.reranker`` attribute so HybridRetriever's R4
        # rerank path can pick it up.
        if rerank:
            reranker = _get_reranker()
            if reranker is not None and getattr(retriever, "reranker", None) is None:
                # Some retriever stand-ins (e.g. test fakes) may not accept
                # attribute assignment; the test path doesn't require it,
                # only that the builder fires.
                with contextlib.suppress(AttributeError):
                    retriever.reranker = reranker

        options = SearchOptions(
            k=k,
            dataset=dataset,
            fusion=fusion,
            alpha=alpha,
            rerank=rerank,
            rerank_top_n=rerank_top_n,
        )

        hits = retriever.search(query, options)
        return {"hits": [_hit_to_dict(h) for h in hits]}

    async def _dispatch_get_chunk(arguments: dict[str, Any]) -> Any:
        chunk_id = int(arguments["chunk_id"])
        retriever = _get_retriever()
        backend = getattr(retriever, "backend", None)
        if backend is None:
            return _error_result("retriever has no backend; cannot fetch chunk")
        chunk = backend.get_chunk(chunk_id)
        if chunk is None:
            return _error_result(f"chunk_id={chunk_id} not found")
        # Normalize: backend.get_chunk may return a Mapping; ensure JSON-safe.
        return dict(chunk)

    async def _dispatch_list_datasets(_arguments: dict[str, Any]) -> dict[str, Any]:
        retriever = _get_retriever()
        backend = getattr(retriever, "backend", None)
        if backend is None:
            return {"datasets": []}
        catalogue = backend.list_datasets()
        return {"datasets": [dict(d) for d in catalogue]}

    # ── write dispatchers (only reached when writes_enabled=True) ────────

    def _make_write_ctx() -> Any:
        """Build a minimal WriteContext from a placeholder host identity."""
        from corpus_forge.mcp.writes import WriteContext

        return WriteContext(host="mcp-server", client=None, session_id=None)

    def _get_write_backend() -> Any:
        retriever = _get_retriever()
        return getattr(retriever, "backend", None)

    async def _dispatch_add_label(arguments: dict[str, Any]) -> Any:
        from corpus_forge.mcp import writes

        backend = _get_write_backend()
        if backend is None:
            return _error_result("retriever has no backend; cannot write labels")
        ctx = _make_write_ctx()
        result = writes.add_label(
            backend,
            ctx,
            arguments["entity_type"],
            int(arguments["entity_id"]),
            arguments["namespace"],
            arguments["value"],
            confidence=arguments.get("confidence"),
            dry_run=bool(arguments.get("dry_run", False)),
        )
        return result

    async def _dispatch_remove_label(arguments: dict[str, Any]) -> Any:
        from corpus_forge.mcp import writes

        backend = _get_write_backend()
        if backend is None:
            return _error_result("retriever has no backend; cannot remove labels")
        ctx = _make_write_ctx()
        result = writes.remove_label(
            backend,
            ctx,
            arguments["entity_type"],
            int(arguments["entity_id"]),
            arguments["namespace"],
            arguments["value"],
            dry_run=bool(arguments.get("dry_run", False)),
        )
        return result

    async def _dispatch_set_metadata(arguments: dict[str, Any]) -> Any:
        from corpus_forge.mcp import writes

        backend = _get_write_backend()
        if backend is None:
            return _error_result("retriever has no backend; cannot set metadata")
        ctx = _make_write_ctx()
        result = writes.set_metadata(
            backend,
            ctx,
            arguments["entity_type"],
            int(arguments["entity_id"]),
            arguments["key"],
            arguments["value"],
            dry_run=bool(arguments.get("dry_run", False)),
        )
        return result

    async def _dispatch_set_description(arguments: dict[str, Any]) -> Any:
        from corpus_forge.mcp import writes

        backend = _get_write_backend()
        if backend is None:
            return _error_result("retriever has no backend; cannot set description")
        ctx = _make_write_ctx()
        result = writes.set_description(
            backend,
            ctx,
            arguments["entity_type"],
            int(arguments["entity_id"]),
            arguments.get("text"),
            dry_run=bool(arguments.get("dry_run", False)),
        )
        return result

    async def _dispatch_list_labels(arguments: dict[str, Any]) -> Any:
        from corpus_forge.mcp import writes

        backend = _get_write_backend()
        if backend is None:
            return {"labels": []}
        ctx = _make_write_ctx()
        return writes.list_labels(
            backend,
            ctx,
            entity_type=arguments.get("entity_type"),
            namespace=arguments.get("namespace"),
        )

    async def _dispatch_append_conversation(arguments: dict[str, Any]) -> Any:
        from corpus_forge.mcp import writes

        backend = _get_write_backend()
        if backend is None:
            return _error_result("retriever has no backend; cannot append conversation")
        ctx = _make_write_ctx()
        result = writes.append_conversation(
            backend,
            ctx,
            dataset=arguments["dataset"],
            title=arguments["title"],
            messages=arguments["messages"],
            started_at=arguments.get("started_at"),
            metadata=arguments.get("metadata"),
            labels=arguments.get("labels"),
            dry_run=bool(arguments.get("dry_run", False)),
        )
        return result

    async def _dispatch_append_message(arguments: dict[str, Any]) -> Any:
        from corpus_forge.mcp import writes

        backend = _get_write_backend()
        if backend is None:
            return _error_result("retriever has no backend; cannot append message")
        ctx = _make_write_ctx()
        result = writes.append_message(
            backend,
            ctx,
            int(arguments["conversation_id"]),
            arguments["role"],
            arguments["content"],
            tool_calls=arguments.get("tool_calls"),
            tool_results=arguments.get("tool_results"),
            ts=arguments.get("ts"),
            metadata=arguments.get("metadata"),
            dry_run=bool(arguments.get("dry_run", False)),
        )
        return result

    async def _dispatch_add_feedback(arguments: dict[str, Any]) -> Any:
        from corpus_forge.mcp import writes

        backend = _get_write_backend()
        if backend is None:
            return _error_result("retriever has no backend; cannot add feedback")
        ctx = _make_write_ctx()
        result = writes.add_feedback(
            backend,
            ctx,
            arguments["entity_type"],
            int(arguments["entity_id"]),
            arguments["kind"],
            rating=arguments.get("rating"),
            text=arguments.get("text"),
            metadata=arguments.get("metadata"),
            dry_run=bool(arguments.get("dry_run", False)),
        )
        return result

    return server


# ── stdio entry point (used by `corpus-forge mcp serve`) ─────────────────


def serve_stdio(*, default_dataset: str | None = None) -> None:
    """Run the MCP server over stdio.

    Wires the real ``_build_retriever_for_eval`` / ``_build_reranker_from_config``
    helpers from the CLI module into :func:`build_server`, then drives the
    stdio loop.  This is the entry point :func:`corpus_forge.cli.mcp_serve`
    dispatches to.
    """
    import asyncio

    asyncio.run(_serve_stdio_async(default_dataset=default_dataset))


async def _serve_stdio_async(*, default_dataset: str | None) -> None:
    from mcp.server.stdio import stdio_server

    # Lazy imports so module import stays cheap.
    from corpus_forge.cli import (
        _build_reranker_from_config,
        _build_retriever_for_eval,
    )
    from corpus_forge.config import Config

    def _retriever_builder() -> Any:
        return _build_retriever_for_eval()

    def _reranker_builder() -> Any:
        return _build_reranker_from_config(Config.load())

    server = build_server(
        retriever_builder=_retriever_builder,
        reranker_builder=_reranker_builder,
        default_dataset=default_dataset,
    )

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


__all__ = ["build_server", "serve_stdio"]
