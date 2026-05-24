"""F-03 — MCP write dispatch layer.

Eight dispatch functions that sit between the MCP server and the backend
write helpers introduced in F-02.  Each function (except ``list_labels``):

1. Validates ``entity_type`` where applicable.
2. Reads the current state (``before`` snapshot) for the audit row.
3. When ``dry_run=True``: skips the backend write, still emits an audit row
   with ``dry_run=True``, and returns sentinel IDs (``None``) for any
   allocated row that was not actually written.
4. When ``dry_run=False``: calls the backend helper, emits an audit row, and
   returns the result + ``audit_id``.

``list_labels`` is read-only — no audit row is emitted.

``WriteContext`` carries the MCP caller identity that flows into every audit
row.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

# ---------------------------------------------------------------------------
# WriteContext — carrier for MCP caller identity
# ---------------------------------------------------------------------------


@dataclass
class WriteContext:
    """Minimal context object carrying MCP caller identity.

    Tests may use this class directly or define their own dataclass with
    identical fields — duck-typing is sufficient since the dispatch functions
    only access ``ctx.host``, ``ctx.client``, and ``ctx.session_id``.
    """

    host: str
    client: str | None
    session_id: str | None


# ---------------------------------------------------------------------------
# Structural Protocols for the duck-typed backend / context arguments.
# ---------------------------------------------------------------------------


class _MCPContext(Protocol):
    """Structural type for the caller-identity object passed as ``ctx``.

    Matches :class:`WriteContext` and any dataclass test fixture with the
    same three attributes — dispatchers only ever read these.
    """

    host: str
    client: str | None
    session_id: str | None


class _WriteBackend(Protocol):
    """Structural type for the storage backend's MCP-write surface.

    Mirrors the subset of methods used by this module so we don't tie
    callers to a single concrete backend class (both ``SQLiteBackend``
    and ``PostgresBackend`` satisfy it).  Implementations may carry
    extra methods — Protocol matching is structural and width-tolerant.
    """

    # --- audit + session linkage --------------------------------------------
    def audit_event(
        self,
        host: str,
        client: str | None,
        session_id: str | None,
        tool: str,
        entity_type: str,
        entity_id: int,
        before: object,
        after: object,
        dry_run: bool,
    ) -> int: ...

    def upsert_feedback_session(
        self,
        client: str,
        session_id: str,
        host: str,
        started_at: datetime | str,
    ) -> int: ...

    def append_feedback_event(
        self,
        feedback_session_id: int,
        *,
        audit_id: int | None = ...,
        feedback_id: int | None = ...,
        entity_type: str,
        entity_id: int,
    ) -> int: ...

    def get_feedback_session_by_key(self, client: str, session_id: str) -> dict | None: ...

    # --- entity read helpers ------------------------------------------------
    def get_chunk(self, chunk_id: int) -> dict | None: ...

    def get_entity_metadata(self, entity_type: str, entity_id: int) -> dict: ...

    def get_entity_description(self, entity_type: str, entity_id: int) -> str | None: ...

    def count_messages(self, conversation_id: int) -> int: ...

    # --- label + metadata writes -------------------------------------------
    def apply_label(
        self,
        entity_type: str,
        entity_id: int,
        namespace: str,
        value: str,
        *,
        confidence: float | None = ...,
        source: str = ...,
    ) -> tuple[int, bool]: ...

    def revoke_label(
        self,
        entity_type: str,
        entity_id: int,
        namespace: str,
        value: str,
    ) -> bool: ...

    def patch_metadata(
        self,
        entity_type: str,
        entity_id: int,
        key: str,
        value: object,
    ) -> tuple[dict, dict]: ...

    def set_description(
        self,
        entity_type: str,
        entity_id: int,
        text: str | None,
    ) -> tuple[str | None, str | None]: ...

    def list_labels(
        self,
        *,
        entity_type: str | None = ...,
        namespace: str | None = ...,
    ) -> dict: ...

    # --- conversation / message / feedback writes --------------------------
    def find_dataset_id_by_name(self, name: str) -> int | None: ...

    def append_conversation(
        self,
        dataset_id: int,
        title: str,
        started_at: datetime | None,
        messages: list[dict],
        metadata: dict | None = ...,
        labels: list[tuple[str, str]] | None = ...,
    ) -> tuple[int, int]: ...

    def append_message(
        self,
        conversation_id: int,
        role: str,
        content: str,
        *,
        tool_calls: list | None = ...,
        tool_results: list | None = ...,
        ts: datetime | None = ...,
        metadata: dict | None = ...,
    ) -> tuple[int, int]: ...

    def add_feedback(
        self,
        entity_type: str,
        entity_id: int,
        kind: str,
        *,
        rating: int | None = ...,
        text: str | None = ...,
        metadata: dict | None = ...,
    ) -> int: ...

    # --- raw execute / connection (rate_search_result + sdft capture) ------
    def _execute(self, query: str, params: Sequence[object] = ...) -> list[dict]: ...

    def _get_connection(self) -> AbstractContextManager[object]: ...


# ---------------------------------------------------------------------------
# Valid entity-type sets (mirror the backend constants to avoid coupling)
# ---------------------------------------------------------------------------

_LABEL_ENTITY_TYPES: frozenset[str] = frozenset({"chunk", "document", "conversation"})
_FEEDBACK_ENTITY_TYPES: frozenset[str] = frozenset({"chunk", "document", "conversation", "message"})

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _link_to_session(
    backend: _WriteBackend,
    ctx: _MCPContext,
    *,
    audit_id: int,
    entity_type: str,
    entity_id: int,
    feedback_id: int | None = None,
) -> None:
    """If ctx.session_id is set, upsert feedback_sessions + append feedback_events."""
    if ctx.session_id is None:
        return

    started_at = datetime.now(UTC).isoformat()
    feedback_session_id = backend.upsert_feedback_session(
        client=ctx.client or "unknown",
        session_id=ctx.session_id,
        host=ctx.host,
        started_at=started_at,
    )
    backend.append_feedback_event(
        feedback_session_id,
        audit_id=audit_id,
        feedback_id=feedback_id,
        entity_type=entity_type,
        entity_id=entity_id,
    )


def _read_metadata(backend: _WriteBackend, entity_type: str, entity_id: int) -> dict:
    """Read the current metadata dict for an entity without mutating it.

    Delegates to backend.patch_metadata's internal read logic by fetching
    the entity row via the backend's native execute path (no hand-crafted
    placeholders that differ between SQLite and psycopg).
    """
    # Use get_chunk for chunk entities; for others fall back to patch_metadata
    # with a no-op merge (reading before/after gives us the before dict).
    if entity_type == "chunk":
        row = backend.get_chunk(entity_id)
        if row is None:
            return {}
        raw = row.get("metadata")
        if raw is None:
            return {}
        if isinstance(raw, dict):
            return raw
        import json as _json  # noqa: PLC0415

        return _json.loads(raw)

    # For document / conversation: use patch_metadata with a sentinel key
    # that won't collide — actually, the cleanest approach is to call
    # get_entity_metadata which both backends now provide.
    return backend.get_entity_metadata(entity_type, entity_id)


def _read_description(backend: _WriteBackend, entity_type: str, entity_id: int) -> str | None:
    """Read the current description for an entity without mutating it.

    Delegates to each backend's native execute path so psycopg gets %s and
    SQLite gets ?, rather than hand-crafting the SQL here.
    """
    if entity_type == "chunk":
        row = backend.get_chunk(entity_id)
        if row is None:
            return None
        return row.get("description")

    return backend.get_entity_description(entity_type, entity_id)


def _count_messages(backend: _WriteBackend, conversation_id: int) -> int:
    """Return the current message count for a conversation."""
    return backend.count_messages(conversation_id)


# ---------------------------------------------------------------------------
# add_label
# ---------------------------------------------------------------------------


def add_label(
    backend: _WriteBackend,
    ctx: _MCPContext,
    entity_type: str,
    entity_id: int,
    namespace: str,
    value: str,
    *,
    confidence: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Attach a label to an entity.

    Returns ``{"label_id": int | None, "created": bool, "audit_id": int}``.
    When ``dry_run=True``, ``label_id`` is ``None`` and the junction row is
    not written.
    """
    if entity_type not in _LABEL_ENTITY_TYPES:
        raise ValueError(
            f"entity_type {entity_type!r} is not valid for add_label; "
            f"must be one of {sorted(_LABEL_ENTITY_TYPES)}"
        )

    before: dict = {"namespace": namespace, "value": value, "applied": False}
    after: dict = {"namespace": namespace, "value": value, "applied": True}

    if dry_run:
        audit_id = backend.audit_event(
            ctx.host,
            ctx.client,
            ctx.session_id,
            "add_label",
            entity_type,
            entity_id,
            before,
            after,
            True,
        )
        _link_to_session(
            backend, ctx, audit_id=audit_id, entity_type=entity_type, entity_id=entity_id
        )
        return {"label_id": None, "created": True, "audit_id": audit_id}

    label_id, created = backend.apply_label(
        entity_type,
        entity_id,
        namespace,
        value,
        confidence=confidence,
        source="user",
    )
    before["applied"] = not created
    audit_id = backend.audit_event(
        ctx.host,
        ctx.client,
        ctx.session_id,
        "add_label",
        entity_type,
        entity_id,
        before,
        after,
        False,
    )
    _link_to_session(backend, ctx, audit_id=audit_id, entity_type=entity_type, entity_id=entity_id)
    return {"label_id": label_id, "created": created, "audit_id": audit_id}


# ---------------------------------------------------------------------------
# remove_label
# ---------------------------------------------------------------------------


def remove_label(
    backend: _WriteBackend,
    ctx: _MCPContext,
    entity_type: str,
    entity_id: int,
    namespace: str,
    value: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Remove a label from an entity.

    Returns ``{"removed": bool, "audit_id": int}``.
    When ``dry_run=True``, the junction row is not deleted.
    """
    if entity_type not in _LABEL_ENTITY_TYPES:
        raise ValueError(
            f"entity_type {entity_type!r} is not valid for remove_label; "
            f"must be one of {sorted(_LABEL_ENTITY_TYPES)}"
        )

    before: dict = {"namespace": namespace, "value": value, "applied": True}
    after: dict = {"namespace": namespace, "value": value, "applied": False}

    if dry_run:
        audit_id = backend.audit_event(
            ctx.host,
            ctx.client,
            ctx.session_id,
            "remove_label",
            entity_type,
            entity_id,
            before,
            after,
            True,
        )
        _link_to_session(
            backend, ctx, audit_id=audit_id, entity_type=entity_type, entity_id=entity_id
        )
        return {"removed": True, "audit_id": audit_id}

    removed = backend.revoke_label(entity_type, entity_id, namespace, value)
    audit_id = backend.audit_event(
        ctx.host,
        ctx.client,
        ctx.session_id,
        "remove_label",
        entity_type,
        entity_id,
        before,
        after,
        False,
    )
    _link_to_session(backend, ctx, audit_id=audit_id, entity_type=entity_type, entity_id=entity_id)
    return {"removed": removed, "audit_id": audit_id}


# ---------------------------------------------------------------------------
# set_metadata
# ---------------------------------------------------------------------------


def set_metadata(
    backend: _WriteBackend,
    ctx: _MCPContext,
    entity_type: str,
    entity_id: int,
    key: str,
    value: object,
    *,
    dry_run: bool = False,
) -> dict:
    """Merge a single key into an entity's metadata JSON.

    Returns ``{"before": dict, "after": dict, "audit_id": int}``.
    When ``dry_run=True``, the metadata column is not updated.
    """
    if entity_type not in _LABEL_ENTITY_TYPES:
        raise ValueError(
            f"entity_type {entity_type!r} is not valid for set_metadata; "
            f"must be one of {sorted(_LABEL_ENTITY_TYPES)}"
        )

    before = _read_metadata(backend, entity_type, entity_id)
    after = {**before, key: value}

    if dry_run:
        audit_id = backend.audit_event(
            ctx.host,
            ctx.client,
            ctx.session_id,
            "set_metadata",
            entity_type,
            entity_id,
            before,
            after,
            True,
        )
        _link_to_session(
            backend, ctx, audit_id=audit_id, entity_type=entity_type, entity_id=entity_id
        )
        return {"before": before, "after": after, "audit_id": audit_id}

    real_before, real_after = backend.patch_metadata(entity_type, entity_id, key, value)
    audit_id = backend.audit_event(
        ctx.host,
        ctx.client,
        ctx.session_id,
        "set_metadata",
        entity_type,
        entity_id,
        real_before,
        real_after,
        False,
    )
    _link_to_session(backend, ctx, audit_id=audit_id, entity_type=entity_type, entity_id=entity_id)
    return {"before": real_before, "after": real_after, "audit_id": audit_id}


# ---------------------------------------------------------------------------
# set_description
# ---------------------------------------------------------------------------


def set_description(
    backend: _WriteBackend,
    ctx: _MCPContext,
    entity_type: str,
    entity_id: int,
    text: str | None,
    *,
    dry_run: bool = False,
) -> dict:
    """Set or clear the description of an entity.

    Returns ``{"before": str | None, "after": str | None, "audit_id": int}``.
    When ``dry_run=True``, the description column is not updated.
    """
    if entity_type not in _LABEL_ENTITY_TYPES:
        raise ValueError(
            f"entity_type {entity_type!r} is not valid for set_description; "
            f"must be one of {sorted(_LABEL_ENTITY_TYPES)}"
        )

    before = _read_description(backend, entity_type, entity_id)

    if dry_run:
        audit_id = backend.audit_event(
            ctx.host,
            ctx.client,
            ctx.session_id,
            "set_description",
            entity_type,
            entity_id,
            before,
            text,
            True,
        )
        _link_to_session(
            backend, ctx, audit_id=audit_id, entity_type=entity_type, entity_id=entity_id
        )
        return {"before": before, "after": text, "audit_id": audit_id}

    real_before, real_after = backend.set_description(entity_type, entity_id, text)
    audit_id = backend.audit_event(
        ctx.host,
        ctx.client,
        ctx.session_id,
        "set_description",
        entity_type,
        entity_id,
        real_before,
        real_after,
        False,
    )
    _link_to_session(backend, ctx, audit_id=audit_id, entity_type=entity_type, entity_id=entity_id)
    return {"before": real_before, "after": real_after, "audit_id": audit_id}


# ---------------------------------------------------------------------------
# list_labels  (read-only — no audit row)
# ---------------------------------------------------------------------------


def list_labels(
    backend: _WriteBackend,
    ctx: _MCPContext,
    entity_type: str | None = None,
    namespace: str | None = None,
) -> dict:
    """List labels with optional filters.

    Returns ``{"labels": [{"entity_type": str, "namespace": str, "value": str,
    "count": int}, ...]}``.  No audit row is emitted (read tool).

    ``ctx`` is accepted for API symmetry with the audit-emitting tools in
    this module; the read-only list_labels path doesn't write audit rows.
    """
    _ = ctx
    return backend.list_labels(entity_type=entity_type, namespace=namespace)


# ---------------------------------------------------------------------------
# append_conversation
# ---------------------------------------------------------------------------


def append_conversation(
    backend: _WriteBackend,
    ctx: _MCPContext,
    dataset: str,
    title: str,
    messages: list[dict],
    *,
    started_at: str | None = None,
    metadata: dict | None = None,
    labels: list[tuple[str, str]] | None = None,
    dry_run: bool = False,
) -> dict:
    """Create a new conversation (with messages) in the named dataset.

    Returns ``{"conversation_id": int | None, "message_count": int,
    "audit_id": int}``.  When ``dry_run=True``, ``conversation_id`` is
    ``None`` and nothing is written.

    ``dataset`` is resolved to a dataset_id via
    ``backend.find_dataset_id_by_name``.  If the dataset is not found, a
    ``ValueError`` is raised.
    """
    dataset_id = backend.find_dataset_id_by_name(dataset)
    if dataset_id is None:
        raise ValueError(f"dataset {dataset!r} not found")

    message_count = len(messages)
    before = None
    after: dict = {"title": title, "message_count": message_count}

    if dry_run:
        audit_id = backend.audit_event(
            ctx.host,
            ctx.client,
            ctx.session_id,
            "append_conversation",
            "conversation",
            0,
            before,
            after,
            True,
        )
        _link_to_session(backend, ctx, audit_id=audit_id, entity_type="conversation", entity_id=0)
        return {"conversation_id": None, "message_count": message_count, "audit_id": audit_id}

    # Parse started_at string into datetime if provided.
    started_at_dt = None
    if started_at is not None:
        started_at_dt = datetime.fromisoformat(started_at.rstrip("Z"))

    conv_id, msg_count = backend.append_conversation(
        dataset_id,
        title,
        started_at_dt,
        messages,
        metadata,
        labels,
    )
    after["conversation_id"] = conv_id
    audit_id = backend.audit_event(
        ctx.host,
        ctx.client,
        ctx.session_id,
        "append_conversation",
        "conversation",
        conv_id,
        before,
        after,
        False,
    )
    _link_to_session(backend, ctx, audit_id=audit_id, entity_type="conversation", entity_id=conv_id)
    return {"conversation_id": conv_id, "message_count": msg_count, "audit_id": audit_id}


# ---------------------------------------------------------------------------
# append_message
# ---------------------------------------------------------------------------


def append_message(
    backend: _WriteBackend,
    ctx: _MCPContext,
    conversation_id: int,
    role: str,
    content: str,
    *,
    tool_calls: list | None = None,
    tool_results: list | None = None,
    ts: str | None = None,
    metadata: dict | None = None,
    dry_run: bool = False,
) -> dict:
    """Append a single message to an existing conversation.

    Returns ``{"message_id": int | None, "turn_index": int,
    "audit_id": int}``.  When ``dry_run=True``, ``message_id`` is ``None``
    and the message row is not written.
    """
    # Predict the next turn_index regardless of dry_run.
    current_count = _count_messages(backend, conversation_id)
    predicted_turn_index = current_count  # 0-based: next = current count

    before = None
    after: dict = {
        "conversation_id": conversation_id,
        "role": role,
        "turn_index": predicted_turn_index,
    }

    if dry_run:
        audit_id = backend.audit_event(
            ctx.host,
            ctx.client,
            ctx.session_id,
            "append_message",
            "conversation",
            conversation_id,
            before,
            after,
            True,
        )
        _link_to_session(
            backend, ctx, audit_id=audit_id, entity_type="conversation", entity_id=conversation_id
        )
        return {
            "message_id": None,
            "turn_index": predicted_turn_index,
            "audit_id": audit_id,
        }

    # Parse ts string into datetime if provided.
    ts_dt = None
    if ts is not None:
        ts_dt = datetime.fromisoformat(ts.rstrip("Z"))

    message_id, turn_index = backend.append_message(
        conversation_id,
        role,
        content,
        tool_calls=tool_calls,
        tool_results=tool_results,
        ts=ts_dt,
        metadata=metadata,
    )
    after["turn_index"] = turn_index
    audit_id = backend.audit_event(
        ctx.host,
        ctx.client,
        ctx.session_id,
        "append_message",
        "conversation",
        conversation_id,
        before,
        after,
        False,
    )
    _link_to_session(
        backend, ctx, audit_id=audit_id, entity_type="conversation", entity_id=conversation_id
    )
    return {"message_id": message_id, "turn_index": turn_index, "audit_id": audit_id}


# ---------------------------------------------------------------------------
# add_feedback
# ---------------------------------------------------------------------------


def add_feedback(
    backend: _WriteBackend,
    ctx: _MCPContext,
    entity_type: str,
    entity_id: int,
    kind: str,
    *,
    rating: int | None = None,
    text: str | None = None,
    metadata: dict | None = None,
    dry_run: bool = False,
) -> dict:
    """Record user feedback on an entity.

    Returns ``{"feedback_id": int | None, "audit_id": int}``.
    When ``dry_run=True``, ``feedback_id`` is ``None`` and no row is written.
    """
    if entity_type not in _FEEDBACK_ENTITY_TYPES:
        raise ValueError(
            f"entity_type {entity_type!r} is not valid for add_feedback; "
            f"must be one of {sorted(_FEEDBACK_ENTITY_TYPES)}"
        )

    before = None
    after: dict = {"entity_type": entity_type, "entity_id": entity_id, "kind": kind}
    if rating is not None:
        after["rating"] = rating
    if text is not None:
        after["text"] = text

    if dry_run:
        audit_id = backend.audit_event(
            ctx.host,
            ctx.client,
            ctx.session_id,
            "add_feedback",
            entity_type,
            entity_id,
            before,
            after,
            True,
        )
        _link_to_session(
            backend, ctx, audit_id=audit_id, entity_type=entity_type, entity_id=entity_id
        )
        return {"feedback_id": None, "audit_id": audit_id}

    feedback_id = backend.add_feedback(
        entity_type,
        entity_id,
        kind,
        rating=rating,
        text=text,
        metadata=metadata,
    )
    audit_id = backend.audit_event(
        ctx.host,
        ctx.client,
        ctx.session_id,
        "add_feedback",
        entity_type,
        entity_id,
        before,
        after,
        False,
    )
    _link_to_session(
        backend,
        ctx,
        audit_id=audit_id,
        entity_type=entity_type,
        entity_id=entity_id,
        feedback_id=feedback_id,
    )
    return {"feedback_id": feedback_id, "audit_id": audit_id}


# ---------------------------------------------------------------------------
# register_session
# ---------------------------------------------------------------------------


def register_session(
    backend: _WriteBackend,
    ctx: _MCPContext,
    client: str,
    session_id: str,
    *,
    host: str | None = None,
) -> dict:
    """Explicitly bind a session id — useful when env vars aren't workable.

    Returns ``{"feedback_session_id": int, "created": bool}``.
    """
    effective_host = host if host is not None else ctx.host

    started_at = datetime.now(UTC).isoformat()
    before = backend.get_feedback_session_by_key(client, session_id)
    feedback_session_id = backend.upsert_feedback_session(
        client=client,
        session_id=session_id,
        host=effective_host,
        started_at=started_at,
    )
    created = before is None
    return {"feedback_session_id": feedback_session_id, "created": created}


# ---------------------------------------------------------------------------
# rate_search_result
# ---------------------------------------------------------------------------


def _q(backend: _WriteBackend, sql: str) -> str:
    """Translate ``?`` placeholders to ``%s`` for Postgres backends.

    SQLiteBackend uses ``?``; PostgresBackend's psycopg cursor requires
    ``%s``.  Detected via ``type(backend).__module__`` so we don't import
    psycopg eagerly.
    """
    if "postgres" in type(backend).__module__.lower():
        return sql.replace("?", "%s")
    return sql


def rate_search_result(
    backend: _WriteBackend,
    ctx: _MCPContext,
    query_id: str,
    chunk_id: int,
    signal: str,
    value: float | None,
    source: str,
    *,
    replacement_chunk_id: int | None = None,
) -> dict:
    """Record a signal (rating, click, thumbs, …) for a search result.

    Looks up ``search_sessions`` by ``query`` = ``query_id``.  If no row
    exists, auto-creates one with ``query=query_id`` (so subsequent calls
    with the same ``query_id`` reuse the session) and resolves
    ``dataset_id`` from the chunk's parent document.  Inserts one row into
    ``search_result_events`` and emits one ``mcp_audit`` row.

    Returns ``{"event_id": int, "session_id": int}``.
    """
    # Verify chunk_id exists; propagate FK violation as a clean error.
    chunk_rows = backend._execute(
        _q(backend, "SELECT id FROM chunks WHERE id = ?"),
        (chunk_id,),
    )
    if not chunk_rows:
        raise ValueError(f"chunk_id={chunk_id} does not exist in chunks table")

    # Look up or auto-create the search session.
    session_rows = backend._execute(
        _q(backend, "SELECT id FROM search_sessions WHERE query = ? LIMIT 1"),
        (query_id,),
    )
    if session_rows:
        session_id: int = int(session_rows[0]["id"])
    else:
        # Resolve dataset_id via chunk → document.
        doc_rows = backend._execute(
            _q(
                backend,
                "SELECT d.dataset_id FROM chunks c"
                " JOIN documents d ON d.id = c.document_id"
                " WHERE c.id = ? LIMIT 1",
            ),
            (chunk_id,),
        )
        dataset_id: int | None = int(doc_rows[0]["dataset_id"]) if doc_rows else None
        # Use the supplied query_id as the session's query column so that a
        # subsequent rate_search_result with the same query_id finds and
        # reuses this session instead of spawning a duplicate one.
        new_session_rows = backend._execute(
            _q(
                backend,
                "INSERT INTO search_sessions (query, dataset_id) VALUES (?, ?) RETURNING id",
            ),
            (query_id, dataset_id),
        )
        session_id = int(new_session_rows[0]["id"])

    # Insert the event row.
    event_rows = backend._execute(
        _q(
            backend,
            "INSERT INTO search_result_events"
            " (session_id, chunk_id, signal, value, source, replacement_chunk_id)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
        ),
        (session_id, chunk_id, signal, value, source, replacement_chunk_id),
    )
    event_id: int = int(event_rows[0]["id"])

    # Emit audit row (mirrors add_feedback pattern).
    after: dict = {
        "query_id": query_id,
        "chunk_id": chunk_id,
        "signal": signal,
        "value": value,
        "source": source,
        "replacement_chunk_id": replacement_chunk_id,
    }
    backend.audit_event(
        ctx.host,
        ctx.client,
        ctx.session_id,
        "rate_search_result",
        "chunk",
        chunk_id,
        None,
        after,
        False,
    )

    return {"event_id": event_id, "session_id": session_id}


# ---------------------------------------------------------------------------
# record_demonstration
# ---------------------------------------------------------------------------


def record_demonstration(
    backend: _WriteBackend,
    ctx: _MCPContext,
    query: str,
    student_messages: list[dict],
    teacher_messages: list[dict],
    target: str,
    source: str,
    dataset: str | None = None,
    dataset_id: int | None = None,
    trace_id: str | None = None,
) -> dict:
    """Record a supervised demonstration pair in ``sdft_demonstrations``.

    Exactly one of ``dataset`` (name → resolved to id) or ``dataset_id``
    (direct int) must be provided.  ``source`` must be a valid
    :class:`~corpus_forge.sdft.sources.SDFTSource` value.

    Returns ``{"demonstration_id": int, "deduped": bool, "audit_id": int}``.
    """
    from corpus_forge.sdft.capture import record_demonstration as _record  # noqa: PLC0415
    from corpus_forge.sdft.sources import SDFTSource  # noqa: PLC0415

    # Validate source.
    valid_values = {e.value for e in SDFTSource}
    if source not in valid_values:
        raise ValueError(
            f"source {source!r} is not a valid SDFTSource; must be one of {sorted(valid_values)}"
        )

    # Resolve dataset_id. Enforce mutually-exclusive contract: exactly
    # one of dataset / dataset_id is allowed so callers can't accidentally
    # disagree about which dataset the demonstration belongs to.
    if dataset is not None and dataset_id is not None:
        raise ValueError(
            "exactly one of dataset or dataset_id is required; "
            f"got both (dataset={dataset!r}, dataset_id={dataset_id!r})"
        )
    if dataset_id is None:
        if dataset is None:
            raise ValueError("exactly one of dataset or dataset_id is required")
        resolved = backend.find_dataset_id_by_name(dataset)
        if resolved is None:
            raise ValueError(f"dataset {dataset!r} not found")
        dataset_id = resolved

    with backend._get_connection() as conn:
        result = _record(
            conn,
            query=query,
            student_messages=student_messages,
            teacher_messages=teacher_messages,
            target=target,
            source=source,
            dataset_id=dataset_id,
            trace_id=trace_id,
        )

    audit_id = backend.audit_event(
        ctx.host,
        ctx.client,
        ctx.session_id,
        "record_demonstration",
        "chunk",
        result["demonstration_id"],
        None,
        {
            "query": query,
            "source": source,
            "dataset_id": dataset_id,
            "deduped": result["deduped"],
        },
        False,
    )
    return {
        "demonstration_id": result["demonstration_id"],
        "deduped": result["deduped"],
        "audit_id": audit_id,
    }


__all__ = [
    "WriteContext",
    "add_feedback",
    "add_label",
    "append_conversation",
    "append_message",
    "list_labels",
    "rate_search_result",
    "record_demonstration",
    "register_session",
    "remove_label",
    "set_description",
    "set_metadata",
]
