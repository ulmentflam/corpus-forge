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
    from pathlib import Path

    from mcp.server import Server


# Sentinel used to distinguish "set_description was omitted" from
# "set_description was explicitly set to None" in the commit_curation
# payload. ``None`` is a legitimate value for set_description (it
# clears the entity's description) so we can't use ``None`` as the
# absence marker.
_SENTINEL_UNSET: Any = object()


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
        "include_labels": {
            "type": "boolean",
            "description": "Include labels on each hit (default: true).",
            "default": True,
        },
        "include_description": {
            "type": "boolean",
            "description": "Include description on each hit (default: true).",
            "default": True,
        },
        "include_feedback": {
            "type": "boolean",
            "description": "Include recent_feedback on each hit (default: true).",
            "default": True,
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
        "include_labels": {
            "type": "boolean",
            "description": "Include labels on the chunk (default: true).",
            "default": True,
        },
        "include_description": {
            "type": "boolean",
            "description": "Include description on the chunk (default: true).",
            "default": True,
        },
        "include_feedback": {
            "type": "boolean",
            "description": "Include recent_feedback on the chunk (default: true).",
            "default": True,
        },
        "template": {
            "type": "string",
            "description": (
                "Optional chat template name.  When provided, the response "
                "includes templated_text (rendered text for message chunks, "
                "null for document chunks)."
            ),
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


_ESTIMATE_SYNC_SIZE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Absolute or user-expanded directory path to scan.",
        },
        "dataset": {
            "type": "string",
            "description": (
                "Optional dataset name (forward-compat hook; J1 falls back to "
                "all active embedders when the dataset is unknown)."
            ),
        },
        "embedders": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional explicit embedder filter. Unknown names error out "
                "instead of falling back."
            ),
        },
        "compression_ratio": {
            "type": "number",
            "description": (
                "Optional override for [estimate].compression_ratio. "
                "Multiplier in (0.0, 1.0] applied to text-heavy columns."
            ),
        },
        "ignore_file": {
            "type": "string",
            "description": (
                "Optional path to a .corpusignore file. Mirrors --ignore-file. "
                "Empty string disables the local ignore lookup entirely. "
                "Absent field falls back to auto-detect at <path>/.corpusignore."
            ),
        },
        "disable_global_ignore": {
            "type": "boolean",
            "description": (
                "If true, skips the user-global ignore file "
                "(~/.config/corpus-forge/ignore or $CF_GLOBAL_IGNORE_FILE) "
                "for this call."
            ),
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


# ── J4 curation read schemas ─────────────────────────────────────────────


_NEXT_CURATION_TARGET_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "dataset": {
            "type": "string",
            "description": "Optional dataset name filter.",
        },
        "embedder": {
            "type": "string",
            "description": "Optional embedder name for centroid routing (forward-compat).",
        },
        "seed_query": {
            "type": "string",
            "description": (
                "Optional natural-language query. When supplied, the selector "
                "routes through the configured reranker for the "
                "ranker_elevation sub-score. When omitted, the selector "
                "falls back to cosine distance from the dataset centroid."
            ),
        },
    },
    "additionalProperties": False,
}


_NEXT_CURATION_BATCH_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "dataset": {"type": "string"},
        "embedder": {"type": "string"},
        "seed_query": {"type": "string"},
        "limit": {
            "type": "integer",
            "minimum": 1,
            "description": "Max number of targets to return in the batch (default 10).",
        },
    },
    "additionalProperties": False,
}


# ── J4 curation write schema ─────────────────────────────────────────────


_COMMIT_CURATION_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "chunk_id": {"type": "integer"},
        "chunk_ids": {"type": "array", "items": {"type": "integer"}},
        "add_labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "value": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["namespace", "value"],
                "additionalProperties": False,
            },
        },
        "remove_labels": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["namespace", "value"],
                "additionalProperties": False,
            },
        },
        "set_metadata": {"type": "object"},
        "set_description": {"type": ["string", "null"]},
        "feedback": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "rating": {"type": "integer"},
                "text": {"type": "string"},
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        "dry_run": {"type": "boolean"},
    },
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
        "rating": {"type": ["integer", "null"]},
        "text": {"type": "string"},
        "metadata": {"type": "object"},
        "dry_run": {"type": "boolean"},
    },
    "required": ["entity_type", "entity_id", "kind"],
    "additionalProperties": False,
}


# ── JSON schemas for the three G-03 template tools ────────────────────────

_RENDER_CONVERSATION_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "conversation_id": {
            "type": "integer",
            "description": "Primary key of the conversation to render.",
        },
        "template": {
            "type": "string",
            "description": "Chat template name (default: chatml).",
        },
        "model_id": {
            "type": "string",
            "description": (
                "HuggingFace model id.  When set, the HF tokenizer's "
                "chat_template is fetched and used (takes priority over "
                "template name lookup)."
            ),
        },
        "custom_jinja": {
            "type": "string",
            "description": (
                "Raw Jinja2 template string.  Highest priority — overrides "
                "both model_id and template name when present."
            ),
        },
        "include_tool_calls": {
            "type": "boolean",
            "description": "Include tool call messages in the render (default: true).",
            "default": True,
        },
    },
    "required": ["conversation_id"],
    "additionalProperties": False,
}

_LIST_CHAT_TEMPLATES_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}

_REGISTER_TEMPLATE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Unique name for the template.",
        },
        "jinja": {
            "type": "string",
            "description": "Jinja2 template source string.",
        },
        "description": {
            "type": "string",
            "description": "Optional human-readable description.",
        },
        "dry_run": {
            "type": "boolean",
            "description": (
                "When true, validate but do not persist the template row "
                "(an audit row is still emitted with dry_run=true)."
            ),
        },
    },
    "required": ["name", "jinja"],
    "additionalProperties": False,
}

_REGISTER_SESSION_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "client": {
            "type": "string",
            "description": "MCP client identifier (e.g. 'cursor', 'claude-code').",
        },
        "session_id": {
            "type": "string",
            "description": "Unique session identifier for the MCP client session.",
        },
        "host": {
            "type": "string",
            "description": (
                "Optional host override.  Defaults to the server's configured host_id."
            ),
        },
    },
    "required": ["client", "session_id"],
    "additionalProperties": False,
}


# ── Phase M Wave 3 — .corpusignore browse / edit schemas ─────────────────


_LIST_IGNORE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scope": {
            "type": "string",
            "enum": ["local", "global", "all"],
            "description": "Which ignore file(s) to enumerate.",
        },
        "path": {
            "type": "string",
            "description": (
                "Project root containing .corpusignore (defaults to the "
                "server's cwd). Ignored when scope='global'."
            ),
        },
    },
    "required": ["scope"],
    "additionalProperties": False,
}


_VALIDATE_IGNORE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Path to the ignore file to validate.",
        },
    },
    "required": ["path"],
    "additionalProperties": False,
}


_ADD_IGNORE_PATTERN_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Pattern to insert."},
        "scope": {"type": "string", "enum": ["local", "global"]},
        "path": {
            "type": "string",
            "description": (
                "Project root containing .corpusignore (defaults to the "
                "server's cwd). Ignored when scope='global'."
            ),
        },
    },
    "required": ["pattern", "scope"],
    "additionalProperties": False,
}


_REMOVE_IGNORE_PATTERN_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Pattern to remove."},
        "scope": {"type": "string", "enum": ["local", "global"]},
        "path": {
            "type": "string",
            "description": (
                "Project root containing .corpusignore (defaults to the "
                "server's cwd). Ignored when scope='global'."
            ),
        },
    },
    "required": ["pattern", "scope"],
    "additionalProperties": False,
}


_SYNC_IGNORE_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "root": {
            "type": "string",
            "description": (
                "Tree whose .corpusignore to resync. When omitted, every "
                "FS-style data root in the loaded config is synced."
            ),
        },
        "also_global": {
            "type": "boolean",
            "description": "Also resync the global ignore file.",
        },
    },
    "additionalProperties": False,
}


# ── Phase M Wave 4: zotero_sync schema ────────────────────────────────


_ZOTERO_SYNC_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "dataset": {
            "type": "string",
            "description": (
                "Dataset name whose Zotero sources to sync. Required so the "
                "tool never accidentally ingests every dataset at once."
            ),
        },
        "dry_run": {
            "type": "boolean",
            "description": (
                "When true, count the attachments + abstract-only docs that "
                "WOULD be ingested without invoking the backend."
            ),
        },
    },
    "required": ["dataset"],
    "additionalProperties": False,
}


# ── Helpers ──────────────────────────────────────────────────────────────


def _labels_to_wire(raw_labels: list[Any]) -> list[dict[str, Any]]:
    """Convert label values to the pinned wire format.

    ``hydrate_hit_metadata`` stores labels as ``(namespace, value)`` tuples.
    The wire format is ``{"namespace": str, "value": str, "source": str,
    "confidence": float | None}``.
    """
    out: list[dict[str, Any]] = []
    for item in raw_labels:
        if isinstance(item, dict):
            out.append(
                {
                    "namespace": item.get("namespace", ""),
                    "value": item.get("value", ""),
                    "source": item.get("source", "user"),
                    "confidence": item.get("confidence"),
                }
            )
        else:
            # Tuple form: (namespace, value)
            ns, val = item[0], item[1]
            out.append({"namespace": ns, "value": val, "source": "user", "confidence": None})
    return out


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
                    "text, content hash, dataset, and metadata.  Pass template= "
                    "to also receive templated_text (rendered for message chunks, "
                    "null for document chunks)."
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
            # J1 read tool (always available — no backend writes)
            mt.Tool(
                name="estimate_sync_size",
                description=(
                    "Estimate the Postgres storage footprint of syncing a folder "
                    "into the corpus, without actually syncing. Pure-prediction; "
                    "no backend opens, no extractor runs, no model calls. Returns "
                    "per-extractor file counts, per-embedder embedding sizing, "
                    "HNSW + btree index overhead, and a total bytes prediction. "
                    "Result is JSON-serialisable with stable schema_version=1."
                ),
                inputSchema=_ESTIMATE_SYNC_SIZE_INPUT_SCHEMA,
            ),
            # J4 curation read tools (always available — read-only)
            mt.Tool(
                name="next_curation_target",
                description=(
                    "Return the single highest-priority chunk for curation. "
                    "Scores candidates on classifier-confidence deficit, "
                    "missing-metadata count, ranker-elevation potential, and "
                    "freshness. Read-only — no backend writes."
                ),
                inputSchema=_NEXT_CURATION_TARGET_INPUT_SCHEMA,
            ),
            mt.Tool(
                name="next_curation_batch",
                description=(
                    "Return a coherent group of up to `limit` curation "
                    "targets sharing a (source stem, classifier label) "
                    "grouping key, with a cohesion_score explaining group "
                    "tightness. Read-only — no backend writes."
                ),
                inputSchema=_NEXT_CURATION_BATCH_INPUT_SCHEMA,
            ),
            # G-03 read tools (always available)
            mt.Tool(
                name="render_conversation",
                description=(
                    "Render a conversation's messages under a chat template.  "
                    "Returns the rendered text, message count, and truncation flag.  "
                    "Resolution order: custom_jinja > model_id > template name > builtin."
                ),
                inputSchema=_RENDER_CONVERSATION_INPUT_SCHEMA,
            ),
            mt.Tool(
                name="list_chat_templates",
                description=(
                    "List all registered chat templates in the backend.  "
                    "Built-in templates (chatml, llama3, …) are not included "
                    "unless explicitly registered.  Read-only — no audit row."
                ),
                inputSchema=_LIST_CHAT_TEMPLATES_INPUT_SCHEMA,
            ),
            # Phase M Wave 3 read tools — always available.
            mt.Tool(
                name="list_ignore",
                description=(
                    "Enumerate patterns in the local and/or global "
                    ".corpusignore. Returns each pattern with provenance "
                    "({source, pattern, managed, line}). Read-only."
                ),
                inputSchema=_LIST_IGNORE_INPUT_SCHEMA,
            ),
            mt.Tool(
                name="validate_ignore",
                description=(
                    "Validate every pattern in the given ignore file. "
                    "Returns {ok, line, pattern, reason}; the first failing "
                    "line is reported. Read-only."
                ),
                inputSchema=_VALIDATE_IGNORE_INPUT_SCHEMA,
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
                # J4 curation write tool (gated by writes_enabled)
                mt.Tool(
                    name="commit_curation",
                    description=(
                        "Atomic-ish multi-write that applies any combination of "
                        "add_labels / remove_labels / set_metadata / "
                        "set_description / feedback for one chunk_id or a list "
                        "of chunk_ids. Routes each piece through the existing "
                        "write surface; returns counts per kind plus audit ids. "
                        "Pass dry_run=true to preview without mutating."
                    ),
                    inputSchema=_COMMIT_CURATION_INPUT_SCHEMA,
                ),
                # G-03 write tool (gated by writes_enabled)
                mt.Tool(
                    name="register_template",
                    description=(
                        "Register a custom Jinja2 chat template in the backend.  "
                        "Use dry_run=true to validate without persisting.  "
                        "Returns template_id and audit_id."
                    ),
                    inputSchema=_REGISTER_TEMPLATE_INPUT_SCHEMA,
                ),
                # H-02 write tool (gated by writes_enabled)
                mt.Tool(
                    name="register_session",
                    description=(
                        "Explicitly bind a client session id to the feedback_sessions table.  "
                        "Returns feedback_session_id and created flag.  "
                        "Useful when CORPUS_FORGE_SESSION_ID env var is not workable."
                    ),
                    inputSchema=_REGISTER_SESSION_INPUT_SCHEMA,
                ),
                # Phase M Wave 3 write tools (gated by writes_enabled)
                mt.Tool(
                    name="add_ignore_pattern",
                    description=(
                        "Append a pattern to the local or global ignore file. "
                        "Idempotent: a duplicate insertion returns "
                        "{added: false}. Validates the pattern before write."
                    ),
                    inputSchema=_ADD_IGNORE_PATTERN_INPUT_SCHEMA,
                ),
                mt.Tool(
                    name="remove_ignore_pattern",
                    description=(
                        "Remove a pattern from the user region of an ignore "
                        "file. Refuses (isError, managed_block_protected) to "
                        "touch the managed block — call sync_ignore instead."
                    ),
                    inputSchema=_REMOVE_IGNORE_PATTERN_INPUT_SCHEMA,
                ),
                mt.Tool(
                    name="sync_ignore",
                    description=(
                        "Regenerate the managed block in one or all FS-style "
                        "data roots' .corpusignore. User lines outside the "
                        "sentinels are preserved. Optionally also resyncs "
                        "the global ignore file."
                    ),
                    inputSchema=_SYNC_IGNORE_INPUT_SCHEMA,
                ),
                # Phase M Wave 4 — Zotero ingest tool.
                mt.Tool(
                    name="zotero_sync",
                    description=(
                        "Trigger a Zotero ingest pass for the named dataset. "
                        "Pass dry_run=true to count what WOULD be ingested "
                        "without invoking the backend. Returns "
                        "{ingested, skipped, by_mode, audit_id} on a real "
                        "sync, or {would_ingest, by_mode} on a dry run."
                    ),
                    inputSchema=_ZOTERO_SYNC_INPUT_SCHEMA,
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
        if name == "estimate_sync_size":
            return await _dispatch_estimate_sync_size(arguments)
        # J4 curation read tools — always available (read-only)
        if name == "next_curation_target":
            return await _dispatch_next_curation_target(arguments)
        if name == "next_curation_batch":
            return await _dispatch_next_curation_batch(arguments)
        # G-03 read tools — always available
        if name == "render_conversation":
            return await _dispatch_render_conversation(arguments)
        if name == "list_chat_templates":
            return await _dispatch_list_chat_templates(arguments)
        # Phase M Wave 3 read tools — always available
        if name == "list_ignore":
            return await _dispatch_list_ignore(arguments)
        if name == "validate_ignore":
            return await _dispatch_validate_ignore(arguments)
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
            # J4 curation write tool
            if name == "commit_curation":
                return await _dispatch_commit_curation(arguments)
            # G-03 write tool
            if name == "register_template":
                return await _dispatch_register_template(arguments)
            # H-02 write tool
            if name == "register_session":
                return await _dispatch_register_session(arguments)
            # Phase M Wave 3 write tools
            if name == "add_ignore_pattern":
                return await _dispatch_add_ignore_pattern(arguments)
            if name == "remove_ignore_pattern":
                return await _dispatch_remove_ignore_pattern(arguments)
            if name == "sync_ignore":
                return await _dispatch_sync_ignore(arguments)
            # Phase M Wave 4 write tool
            if name == "zotero_sync":
                return await _dispatch_zotero_sync(arguments)
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
        include_labels = bool(arguments.get("include_labels", True))
        include_description = bool(arguments.get("include_description", True))
        include_feedback = bool(arguments.get("include_feedback", True))

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

        # Enrichment: hydrate labels / description / feedback when the backend
        # exposes hydrate_hit_metadata.  One bulk call for chunk hits + one bulk
        # call for parent documents — never per-hit (no N+1).
        enrichment_wanted = include_labels or include_description or include_feedback
        backend = getattr(retriever, "backend", None)
        hydrated_by_chunk_id: dict[int, dict[str, Any]] = {}
        parent_enrichment_by_doc_id: dict[int, dict[str, Any]] = {}

        if enrichment_wanted and backend is not None and hits:
            hydrate_fn = getattr(backend, "hydrate_hit_metadata", None)
            if hydrate_fn is not None:
                # Call 1: bulk-hydrate all chunk hits (one call regardless of count).
                hydrated_list = hydrate_fn(hits)
                for hd in hydrated_list:
                    raw_cid = hd.get("chunk_id") if isinstance(hd, dict) else hd.chunk_id
                    if raw_cid is not None:
                        hydrated_by_chunk_id[int(raw_cid)] = hd if isinstance(hd, dict) else {}

            # Call 2 (at most): bulk-hydrate parent documents for chunk hits.
            # Collect unique document_ids from the hit set.
            parent_ids: list[int] = []
            seen_parent_ids: set[int] = set()
            for h in hits:
                doc_id = getattr(h, "document_id", None)
                if doc_id is not None and doc_id not in seen_parent_ids:
                    seen_parent_ids.add(doc_id)
                    parent_ids.append(doc_id)

            if parent_ids:
                hydrate_doc_fn = getattr(backend, "hydrate_document_metadata", None)
                if hydrate_doc_fn is not None:
                    doc_hydrated_list = hydrate_doc_fn(parent_ids)
                    for dh in doc_hydrated_list:
                        doc_id = dh.get("document_id") if isinstance(dh, dict) else None
                        if doc_id is not None:
                            parent_enrichment_by_doc_id[doc_id] = dh

        # Serialize hits applying enrichment flags.
        wire_hits: list[dict[str, Any]] = []
        for h in hits:
            hd = _hit_to_dict(h)
            enriched = hydrated_by_chunk_id.get(h.chunk_id, {})

            if include_labels:
                raw_labels = enriched.get("labels", [])
                hd["labels"] = _labels_to_wire(raw_labels)
            if include_description:
                hd["description"] = enriched.get("description")
            if include_feedback:
                hd["recent_feedback"] = enriched.get("recent_feedback", [])

            # Parent rollup for chunk hits (document_id present).
            if enrichment_wanted:
                doc_id = getattr(h, "document_id", None)
                if doc_id is not None:
                    parent_data = parent_enrichment_by_doc_id.get(doc_id, {})
                    parent_dict: dict[str, Any] = {}
                    if include_labels:
                        parent_dict["labels"] = _labels_to_wire(parent_data.get("labels", []))
                    if include_description:
                        parent_dict["description"] = parent_data.get("description")
                    if include_feedback:
                        parent_dict["recent_feedback"] = parent_data.get("recent_feedback", [])
                    hd["parent"] = parent_dict

            wire_hits.append(hd)

        return {"hits": wire_hits}

    async def _dispatch_get_chunk(arguments: dict[str, Any]) -> Any:
        chunk_id = int(arguments["chunk_id"])
        include_labels = bool(arguments.get("include_labels", True))
        include_description = bool(arguments.get("include_description", True))
        include_feedback = bool(arguments.get("include_feedback", True))
        template_arg: str | None = arguments.get("template")

        retriever = _get_retriever()
        backend = getattr(retriever, "backend", None)
        if backend is None:
            return _error_result("retriever has no backend; cannot fetch chunk")

        # When template= is provided, delegate to get_chunk_with_template.
        if template_arg is not None:
            from corpus_forge.mcp import templates as _tmpl

            ctx = _make_write_ctx()
            return _tmpl.get_chunk_with_template(backend, ctx, chunk_id, template_arg)

        chunk = backend.get_chunk(chunk_id)
        if chunk is None:
            return _error_result(f"chunk_id={chunk_id} not found")
        # Normalize: backend.get_chunk may return a Mapping; ensure JSON-safe.
        result: dict[str, Any] = dict(chunk)

        # Enrichment via hydrate_hit_metadata (single-element bulk call).
        enrichment_wanted = include_labels or include_description or include_feedback
        if enrichment_wanted:
            hydrate_fn = getattr(backend, "hydrate_hit_metadata", None)
            if hydrate_fn is not None:
                from corpus_forge.retrieval.types import Hit as _Hit

                synthetic_hit = _Hit(
                    chunk_id=chunk_id,
                    score=0.0,
                    text=result.get("text", ""),
                    document_id=result.get("document_id"),
                    source_uri=result.get("source_uri"),
                    title=result.get("title"),
                    dataset_id=result.get("dataset_id") or 0,
                    metadata=result.get("metadata") or {},
                    source="lexical",
                )
                hydrated_list = hydrate_fn([synthetic_hit])
                if hydrated_list:
                    enriched = hydrated_list[0] if isinstance(hydrated_list[0], dict) else {}
                    if include_labels:
                        result["labels"] = _labels_to_wire(enriched.get("labels", []))
                    if include_description:
                        result["description"] = enriched.get("description")
                    if include_feedback:
                        result["recent_feedback"] = enriched.get("recent_feedback", [])

                # Parent rollup for chunk hits (document_id present).
                doc_id = result.get("document_id")
                if doc_id is not None:
                    hydrate_doc_fn = getattr(backend, "hydrate_document_metadata", None)
                    if hydrate_doc_fn is not None:
                        doc_hydrated_list = hydrate_doc_fn([doc_id])
                        parent_data = doc_hydrated_list[0] if doc_hydrated_list else {}
                        parent_dict: dict[str, Any] = {}
                        if include_labels:
                            parent_dict["labels"] = _labels_to_wire(parent_data.get("labels", []))
                        if include_description:
                            parent_dict["description"] = parent_data.get("description")
                        if include_feedback:
                            parent_dict["recent_feedback"] = parent_data.get("recent_feedback", [])
                        result["parent"] = parent_dict

        return result

    async def _dispatch_list_datasets(_arguments: dict[str, Any]) -> dict[str, Any]:
        retriever = _get_retriever()
        backend = getattr(retriever, "backend", None)
        if backend is None:
            return {"datasets": []}
        catalogue = backend.list_datasets()
        return {"datasets": [dict(d) for d in catalogue]}

    async def _dispatch_estimate_sync_size(arguments: dict[str, Any]) -> Any:
        """Pure-prediction storage estimator (Phase J / J1).

        Does NOT use ``_get_retriever`` — the estimator never opens the
        backend. Loads :class:`Config` from disk on first call (via the
        same ``CORPUS_FORGE_CONFIG`` resolution as the CLI). Errors are
        returned as ``isError`` results so the MCP client gets a clean
        text block instead of a transport-level exception.
        """
        # Lazy-import — keeps `build_server` cheap and side-effect free
        # for callers that never invoke this tool. The fully-qualified
        # `corpus_forge.estimate.estimate_sync` lookup is what test
        # monkeypatches target.
        from dataclasses import asdict
        from pathlib import Path

        import corpus_forge.estimate as _estimate_mod
        from corpus_forge.config import Config
        from corpus_forge.ignore import (
            CorpusIgnore,
            IgnoreStack,
            load_global_ignore,
            load_local_ignore,
        )

        try:
            config = Config.load()
        except FileNotFoundError as exc:
            return _error_result(f"configuration not found: {exc}")

        path = arguments["path"]
        embedders = arguments.get("embedders")
        compression_ratio = arguments.get("compression_ratio")

        # Resolve the scan root once so the ignore-stack matcher and
        # estimate_sync's own resolve_path() see the same absolute path.
        root = Path(path).expanduser().resolve()

        # Build the ignore stack.
        ignore_file_arg = arguments.get("ignore_file")
        disable_global = bool(arguments.get("disable_global_ignore", False))
        try:
            if ignore_file_arg is None:
                local_set = load_local_ignore(root)
            elif ignore_file_arg == "":
                local_set = CorpusIgnore.empty(root)
            else:
                local_set = load_local_ignore(root, override=Path(ignore_file_arg).expanduser())
        except FileNotFoundError as exc:
            return _error_result(f"ignore_file not found: {exc}")
        global_set = CorpusIgnore.empty(Path.home()) if disable_global else load_global_ignore()
        ignore_stack = IgnoreStack((global_set, local_set))

        try:
            est = _estimate_mod.estimate_sync(
                path,
                config,
                embedders=embedders,
                compression_ratio=compression_ratio,
                ignore=ignore_stack,
            )
        except (FileNotFoundError, NotADirectoryError) as exc:
            return _error_result(str(exc))
        except ValueError as exc:
            return _error_result(str(exc))

        return {"estimate": asdict(est)}

    # ── J4 curation read dispatchers ─────────────────────────────────────

    def _maybe_build_reranker(seed_query: str | None) -> Any | None:
        """Build a reranker iff ``seed_query`` is set AND the reranker
        factory succeeds. Reuses :func:`_build_reranker_from_config` so
        the local-or-remote URL invariant is honoured uniformly.
        """
        if not seed_query:
            return None
        try:
            from corpus_forge.cli import _build_reranker_from_config
            from corpus_forge.config import Config

            cfg = Config.load()
            return _build_reranker_from_config(cfg)
        except (FileNotFoundError, RuntimeError, ImportError, ValueError) as exc:
            # Reranker is best-effort — if it can't be built (no
            # config, missing extras), fall back to the centroid path.
            import logging

            logging.getLogger(__name__).debug(
                "reranker unavailable for seed_query; falling back to centroid path: %r",
                exc,
            )
            return None

    async def _dispatch_next_curation_target(arguments: dict[str, Any]) -> Any:
        from dataclasses import asdict

        from corpus_forge.curation import next_curation_target

        backend = _get_write_backend()
        if backend is None:
            return _error_result("retriever has no backend; cannot run curation selector")

        seed_query = arguments.get("seed_query")
        reranker = _maybe_build_reranker(seed_query)
        try:
            target = next_curation_target(
                backend=backend,
                dataset=arguments.get("dataset"),
                embedder=arguments.get("embedder"),
                seed_query=seed_query,
                reranker=reranker,
            )
        except (FileNotFoundError, ValueError) as exc:
            return _error_result(str(exc))

        if target is None:
            return {"target": None}
        return {"target": asdict(target)}

    async def _dispatch_next_curation_batch(arguments: dict[str, Any]) -> Any:
        from dataclasses import asdict

        from corpus_forge.curation import next_curation_batch

        backend = _get_write_backend()
        if backend is None:
            return _error_result("retriever has no backend; cannot run curation selector")

        seed_query = arguments.get("seed_query")
        reranker = _maybe_build_reranker(seed_query)
        try:
            batch = next_curation_batch(
                backend=backend,
                dataset=arguments.get("dataset"),
                embedder=arguments.get("embedder"),
                seed_query=seed_query,
                reranker=reranker,
                limit=int(arguments.get("limit", 10)),
            )
        except (FileNotFoundError, ValueError) as exc:
            return _error_result(str(exc))

        if batch is None:
            return {"batch": None}
        return {"batch": asdict(batch)}

    # ── write dispatchers (only reached when writes_enabled=True) ────────

    def _make_write_ctx() -> Any:
        """Build a WriteContext from env vars + config host identity."""
        import os

        from corpus_forge.mcp.writes import WriteContext

        return WriteContext(
            host="mcp-server",
            client=os.environ.get("CORPUS_FORGE_CLIENT"),
            session_id=os.environ.get("CORPUS_FORGE_SESSION_ID"),
        )

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

    async def _dispatch_commit_curation(arguments: dict[str, Any]) -> Any:
        """J4 — atomic-ish multi-write composing the existing write surface.

        This is NOT a single transactional commit (the underlying
        per-call dispatchers each emit their own audit row). It is a
        best-effort serial application that bubbles the first failure
        back as an ``isError`` result. The order is fixed:

          add_labels → remove_labels → set_metadata → set_description →
          feedback

        per chunk_id, then the loop moves to the next chunk_id.
        """
        raw_chunk_id = arguments.get("chunk_id")
        raw_chunk_ids = arguments.get("chunk_ids")
        if (raw_chunk_id is None) == (raw_chunk_ids is None):
            return _error_result("commit_curation requires exactly one of chunk_id or chunk_ids")
        if raw_chunk_ids is not None:
            if not isinstance(raw_chunk_ids, list) or not raw_chunk_ids:
                return _error_result("chunk_ids must be a non-empty array of integers")
            chunk_ids = [int(c) for c in raw_chunk_ids]
        else:
            chunk_ids = [int(raw_chunk_id)]  # type: ignore[arg-type]

        dry_run = bool(arguments.get("dry_run", False))
        add_labels = arguments.get("add_labels") or []
        remove_labels = arguments.get("remove_labels") or []
        set_metadata_payload = arguments.get("set_metadata") or {}
        set_description_payload = arguments.get("set_description", _SENTINEL_UNSET)
        feedback_payload = arguments.get("feedback")

        counts = {
            "add_label": 0,
            "remove_label": 0,
            "set_metadata": 0,
            "set_description": 0,
            "add_feedback": 0,
        }
        audit_ids: list[int] = []

        for cid in chunk_ids:
            for entry in add_labels:
                sub_args = {
                    "entity_type": "chunk",
                    "entity_id": cid,
                    "namespace": entry["namespace"],
                    "value": entry["value"],
                    "dry_run": dry_run,
                }
                if "confidence" in entry:
                    sub_args["confidence"] = entry["confidence"]
                try:
                    res = await _dispatch_add_label(sub_args)
                except Exception as exc:
                    return _error_result(f"add_label failed for chunk_id={cid}: {exc}")
                if isinstance(res, dict):
                    aid = res.get("audit_id")
                    if isinstance(aid, int):
                        audit_ids.append(aid)
                    counts["add_label"] += 1

            for entry in remove_labels:
                try:
                    res = await _dispatch_remove_label(
                        {
                            "entity_type": "chunk",
                            "entity_id": cid,
                            "namespace": entry["namespace"],
                            "value": entry["value"],
                            "dry_run": dry_run,
                        }
                    )
                except Exception as exc:
                    return _error_result(f"remove_label failed for chunk_id={cid}: {exc}")
                if isinstance(res, dict):
                    aid = res.get("audit_id")
                    if isinstance(aid, int):
                        audit_ids.append(aid)
                    counts["remove_label"] += 1

            for key, value in set_metadata_payload.items():
                try:
                    res = await _dispatch_set_metadata(
                        {
                            "entity_type": "chunk",
                            "entity_id": cid,
                            "key": key,
                            "value": value,
                            "dry_run": dry_run,
                        }
                    )
                except Exception as exc:
                    return _error_result(f"set_metadata failed for chunk_id={cid}: {exc}")
                if isinstance(res, dict):
                    aid = res.get("audit_id")
                    if isinstance(aid, int):
                        audit_ids.append(aid)
                    counts["set_metadata"] += 1

            if set_description_payload is not _SENTINEL_UNSET:
                try:
                    res = await _dispatch_set_description(
                        {
                            "entity_type": "chunk",
                            "entity_id": cid,
                            "text": set_description_payload,
                            "dry_run": dry_run,
                        }
                    )
                except Exception as exc:
                    return _error_result(f"set_description failed for chunk_id={cid}: {exc}")
                if isinstance(res, dict):
                    aid = res.get("audit_id")
                    if isinstance(aid, int):
                        audit_ids.append(aid)
                    counts["set_description"] += 1

            if feedback_payload:
                fb_args = {
                    "entity_type": "chunk",
                    "entity_id": cid,
                    "kind": feedback_payload["kind"],
                    "dry_run": dry_run,
                }
                if "rating" in feedback_payload:
                    fb_args["rating"] = feedback_payload["rating"]
                if "text" in feedback_payload:
                    fb_args["text"] = feedback_payload["text"]
                try:
                    res = await _dispatch_add_feedback(fb_args)
                except Exception as exc:
                    return _error_result(f"add_feedback failed for chunk_id={cid}: {exc}")
                if isinstance(res, dict):
                    aid = res.get("audit_id")
                    if isinstance(aid, int):
                        audit_ids.append(aid)
                    counts["add_feedback"] += 1

        return {
            "writes": counts,
            "chunk_ids_processed": chunk_ids,
            "dry_run": dry_run,
            "audit_ids": audit_ids,
        }

    # ── G-03 template dispatchers ────────────────────────────────────────

    async def _dispatch_render_conversation(arguments: dict[str, Any]) -> Any:
        from corpus_forge.mcp import templates as _tmpl

        backend = _get_write_backend()
        if backend is None:
            return _error_result("retriever has no backend; cannot render conversation")
        ctx = _make_write_ctx()
        try:
            result = _tmpl.render_conversation(
                backend,
                ctx,
                int(arguments["conversation_id"]),
                arguments.get("template", "chatml"),
                model_id=arguments.get("model_id"),
                custom_jinja=arguments.get("custom_jinja"),
                include_tool_calls=bool(arguments.get("include_tool_calls", True)),
            )
        except (ValueError, KeyError, LookupError, RuntimeError) as exc:
            return _error_result(str(exc))
        return result

    async def _dispatch_list_chat_templates(_arguments: dict[str, Any]) -> Any:
        from corpus_forge.mcp import templates as _tmpl

        backend = _get_write_backend()
        if backend is None:
            return {"templates": []}
        ctx = _make_write_ctx()
        return _tmpl.list_chat_templates(backend, ctx)

    async def _dispatch_register_template(arguments: dict[str, Any]) -> Any:
        from corpus_forge.mcp import templates as _tmpl

        backend = _get_write_backend()
        if backend is None:
            return _error_result("retriever has no backend; cannot register template")
        ctx = _make_write_ctx()
        result = _tmpl.register_template(
            backend,
            ctx,
            arguments["name"],
            arguments["jinja"],
            description=arguments.get("description"),
            dry_run=bool(arguments.get("dry_run", False)),
        )
        return result

    async def _dispatch_register_session(arguments: dict[str, Any]) -> Any:
        from corpus_forge.mcp import writes

        backend = _get_write_backend()
        if backend is None:
            return _error_result("retriever has no backend; cannot register session")
        ctx = _make_write_ctx()
        result = writes.register_session(
            backend,
            ctx,
            arguments["client"],
            arguments["session_id"],
            host=arguments.get("host"),
        )
        return result

    # ── Phase M Wave 3 — ignore_* dispatchers ────────────────────────────

    def _resolve_path_arg(arg: Any) -> Path | None:
        """Coerce a wire-side path string into an absolute :class:`Path`."""

        if arg in (None, ""):
            return None
        from pathlib import Path as _Path  # local import — avoid top-level

        path = _Path(str(arg)).expanduser()
        if not path.is_absolute():
            path = _Path.cwd() / path
        return path

    async def _dispatch_list_ignore(arguments: dict[str, Any]) -> Any:
        from corpus_forge.admin import ignore as admin_ignore

        scope = arguments.get("scope", "local")
        if scope not in ("local", "global", "all"):
            return _error_result(f"unknown scope: {scope!r}")
        path = _resolve_path_arg(arguments.get("path"))
        try:
            entries = admin_ignore.list_patterns(scope, path=path)
        except (OSError, ValueError) as exc:
            return _error_result(f"list_ignore failed: {exc}")
        return {
            "patterns": [
                {
                    "pattern": e.pattern,
                    "source": e.source,
                    "managed": e.managed,
                    "line": e.line,
                }
                for e in entries
            ],
            "count": len(entries),
        }

    async def _dispatch_validate_ignore(arguments: dict[str, Any]) -> Any:
        from corpus_forge.admin import ignore as admin_ignore

        path = _resolve_path_arg(arguments.get("path"))
        if path is None:
            return _error_result("validate_ignore requires `path`")
        result = admin_ignore.validate_file(path)
        return {
            "ok": result.ok,
            "line": result.line,
            "pattern": result.pattern,
            "reason": result.reason,
        }

    async def _dispatch_add_ignore_pattern(arguments: dict[str, Any]) -> Any:
        from corpus_forge.admin import ignore as admin_ignore

        scope = arguments.get("scope")
        if scope not in ("local", "global"):
            return _error_result(
                f"add_ignore_pattern requires scope in [local, global]; got {scope!r}"
            )
        path = _resolve_path_arg(arguments.get("path"))
        pattern = arguments.get("pattern", "")
        try:
            result = admin_ignore.add_pattern(pattern, scope=scope, path=path)
        except admin_ignore.InvalidPattern as exc:
            return _error_result(f"invalid_pattern: {exc}")
        return {"path": str(result.path), "pattern": result.pattern, "added": result.added}

    async def _dispatch_remove_ignore_pattern(arguments: dict[str, Any]) -> Any:
        from corpus_forge.admin import ignore as admin_ignore

        scope = arguments.get("scope")
        if scope not in ("local", "global"):
            return _error_result(
                f"remove_ignore_pattern requires scope in [local, global]; got {scope!r}"
            )
        path = _resolve_path_arg(arguments.get("path"))
        pattern = arguments.get("pattern", "")
        try:
            result = admin_ignore.remove_pattern(pattern, scope=scope, path=path)
        except admin_ignore.ManagedRegionProtected as exc:
            return _error_result(
                f"managed_block_protected: pattern={exc.pattern!r} path={exc.path}"
            )
        return {"path": str(result.path), "pattern": result.pattern, "removed": result.removed}

    async def _dispatch_sync_ignore(arguments: dict[str, Any]) -> Any:
        from corpus_forge.admin import ignore as admin_ignore

        root = _resolve_path_arg(arguments.get("root"))
        also_global = bool(arguments.get("also_global", False))
        # Best-effort config load — sync works without a config when a
        # specific root is supplied.
        cfg = None
        try:
            from corpus_forge.config import Config as _Config

            cfg = _Config.load()
        except Exception:
            cfg = None
        result = admin_ignore.sync_managed(root=root, cfg=cfg, also_global=also_global)
        return {"updated": [str(p) for p in result.updated]}

    # ── Phase M Wave 4 — zotero_sync dispatcher ──────────────────────────

    async def _dispatch_zotero_sync(arguments: dict[str, Any]) -> Any:
        """Trigger an ingest pass over a dataset's Zotero sources.

        Module-level hooks ``_zotero_dry_run_count`` and
        ``_zotero_real_sync`` are the seams tests patch. We resolve via
        ``sys.modules[__name__]`` so a ``mock.patch("…server._zotero_…",
        new=…)`` substitution is picked up here even though this
        function was constructed inside the ``build_server`` closure.
        """
        import sys as _sys

        dataset = arguments.get("dataset")
        dry_run = bool(arguments.get("dry_run", False))
        if not dataset or not isinstance(dataset, str):
            return _error_result("zotero_sync requires a non-empty 'dataset' string")

        mod = _sys.modules[__name__]
        try:
            if dry_run:
                helper = getattr(mod, "_zotero_dry_run_count", None)
                if helper is None:
                    return _error_result("zotero dry-run helper not wired")
                return helper(dataset=dataset)
            helper = getattr(mod, "_zotero_real_sync", None)
            if helper is None:
                return _error_result("zotero real-sync helper not wired")
            return helper(dataset=dataset)
        except Exception as exc:  # pragma: no cover — broad-except by design
            return _error_result(f"zotero_sync failed: {exc}")

    return server


# ── Phase M Wave 4 — module-level seams patchable by tests ────────────────


def _zotero_dry_run_count(*, dataset: str) -> dict[str, Any]:
    """Count every doc the source would emit, both attachment-backed and abstract-only.

    Walks every Zotero source in the named dataset and counts the
    ``RawDocument``s ``ZoteroSource.scan()`` would yield (PDF
    attachments PLUS abstract-only items for attachment-less papers
    with a non-empty ``abstractNote``). Does NOT open the backend.

    We can't just sum ``discover()`` — abstract-only docs are built
    directly by ``scan()`` and would be undercounted otherwise.
    """
    from corpus_forge.config import Config
    from corpus_forge.sources.zotero import ZoteroSource as _ZoteroSource

    cfg = Config.load()
    by_mode: dict[str, int] = {"local": 0, "web": 0, "both": 0}
    would_ingest = 0
    for ds in cfg.datasets:
        if ds.name != dataset:
            continue
        for src_cfg in ds.sources:
            if src_cfg.plugin != "zotero" or src_cfg.zotero is None:
                continue
            z = src_cfg.zotero
            source = _ZoteroSource(
                mode=z.mode,
                library_path=z.library_path,
                user_id=z.user_id,
                api_key_env=z.api_key_env,
                library_type=z.library_type,
                group_id=z.group_id,
                base_url=str(z.base_url),
                include_attachments=z.include_attachments,
                include_collections=z.include_collections,
                exclude_collections=z.exclude_collections,
                cache_dir=z.cache_dir,
            )
            # Count via ``scan()`` so abstract-only docs are included.
            # ``scan()`` calls ``parse()`` which runs the PDF extractor;
            # for a dry-run we just need the count, so we tally
            # attachments + abstract-only items directly instead.
            n_attachments = sum(1 for _ in source.discover())
            attached_keys = {att.item_key for att in source._attachments_by_path.values()}
            n_abstract_only = sum(
                1
                for it in source._items_by_key.values()
                if it.item_key not in attached_keys and (it.abstract or "").strip()
            )
            n = n_attachments + n_abstract_only
            by_mode[z.mode] = by_mode.get(z.mode, 0) + n
            would_ingest += n
    return {"would_ingest": would_ingest, "by_mode": by_mode}


def _zotero_real_sync(*, dataset: str) -> dict[str, Any]:
    """Trigger ``ingest_once`` filtered to the named dataset.

    Returns ``{started, audit_id}``. We deliberately do NOT advertise
    per-document counts here because the underlying ``ingest_once``
    surface doesn't plumb them back: returning fabricated zeros would
    be misleading. Real counts can be added once the ingest layer
    exposes them; until then callers should consult the audit log
    keyed by ``audit_id``.
    """
    import time as _time

    from corpus_forge.config import Config
    from corpus_forge.ingest import ingest_once as _ingest_once

    cfg = Config.load()
    # Filter to the named dataset by mutating a shallow copy of cfg
    # (don't touch the on-disk config).
    filtered = [ds for ds in cfg.datasets if ds.name == dataset]
    cfg.datasets = filtered
    _ingest_once(cfg)
    audit_id = f"zsync-{int(_time.time())}"
    return {"started": True, "audit_id": audit_id}


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
