"""F-02..F-04 coverage repair — MCP server write-tool registration + enrichment.

Unit tests that exercise the new paths in ``corpus_forge/mcp/server.py``
introduced across F-02 / F-03 / F-04:

- ``writes_enabled`` gate (omit vs expose the 8 write tools)
- All 8 write-tool dispatcher callbacks via in-process MCP tool/call dispatch
- Read-side enrichment on ``search`` + ``get_chunk``
  (labels / description / recent_feedback toggles, parent rollup)
- ``_labels_to_wire`` both dict and tuple input paths

These tests use an in-memory SQLite backend and the LexicalRetriever stub so
no ML model is loaded and no container is needed.

Run command:
    uv run pytest tests/unit/test_mcp_server_enrichment.py -v
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
from typing import Any

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.mcp.server import build_server

# ---------------------------------------------------------------------------
# Helpers: SQLite in-memory backend + minimal seeding
# ---------------------------------------------------------------------------

_CHUNK_TEXT = "The eigenvalue of the Hamiltonian operator is quantized."
_DOC_TEXT = "Quantum mechanics foundational text body with band structure theory."


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _seed_db(backend: SQLiteBackend) -> dict[str, int]:
    """Insert one dataset, document, chunk, and conversation.  Returns ids."""
    with backend._get_connection() as conn:
        dataset_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("unit-test-ds", "text", "unit enrichment tests"),
        ).fetchone()[0]

        document_id = conn.execute(
            "INSERT INTO documents (dataset_id, source_uri, content_hash, title, text, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (
                dataset_id,
                "vault://unit/doc.md",
                _content_hash(_DOC_TEXT),
                "Quantum Mechanics",
                _DOC_TEXT,
                "{}",
            ),
        ).fetchone()[0]

        chunk_id = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, content_hash, metadata)"
            " VALUES (?, ?, ?, ?, ?) RETURNING id",
            (document_id, 0, _CHUNK_TEXT, _content_hash(_CHUNK_TEXT), "{}"),
        ).fetchone()[0]

        # Seed FTS5 table so search_lexical finds the chunk.
        with contextlib.suppress(Exception):
            conn.execute(
                "INSERT INTO chunks_fts (rowid, text) VALUES (?, ?)",
                (chunk_id, _CHUNK_TEXT),
            )

        conversation_id = conn.execute(
            "INSERT INTO conversations"
            " (dataset_id, source_uri, content_hash, title, message_count, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (dataset_id, "test://conv/1", "conv-hash-1", "Conv 1", 0, "{}"),
        ).fetchone()[0]

        message_id = conn.execute(
            "INSERT INTO messages"
            " (conversation_id, turn_index, role, content, metadata)"
            " VALUES (?, ?, ?, ?, ?) RETURNING id",
            (conversation_id, 0, "user", "Hi!", "{}"),
        ).fetchone()[0]

        conn.commit()

    return {
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "dataset_name": "unit-test-ds",
    }


# ---------------------------------------------------------------------------
# Minimal lexical-only retriever (no embedder, no ML)
# ---------------------------------------------------------------------------


class _LexicalRetriever:
    """Thin wrapper around backend.search_lexical — no dense embedder."""

    def __init__(self, backend: SQLiteBackend) -> None:
        self.backend = backend

    def search(self, query: str, options: Any) -> list[Any]:
        k = getattr(options, "k", 10)
        return self.backend.search_lexical(query, k=k)


# ---------------------------------------------------------------------------
# MCP call helper
# ---------------------------------------------------------------------------


def _call_tool(server: Any, name: str, arguments: dict) -> dict:
    """Drive an MCP tool call synchronously and return the structured payload."""

    async def _run() -> dict:
        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = server.request_handlers.get(CallToolRequest)
        assert handler is not None, "No CallToolRequest handler registered on server"

        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments),
        )
        wrapper = await handler(request)
        root = wrapper.root
        if getattr(root, "isError", False):
            text = "".join(getattr(b, "text", "") for b in getattr(root, "content", []))
            raise AssertionError(f"MCP tool {name!r} returned isError=True: {text}")
        if getattr(root, "structuredContent", None) is not None:
            return dict(root.structuredContent)
        text_blocks = [getattr(b, "text", "") for b in getattr(root, "content", [])]
        return json.loads("".join(text_blocks))

    return asyncio.run(_run())


def _call_tool_raw(server: Any, name: str, arguments: dict) -> Any:
    """Return the raw CallToolResult root without raising on isError."""

    async def _run() -> Any:
        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = server.request_handlers.get(CallToolRequest)
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=name, arguments=arguments),
        )
        wrapper = await handler(request)
        return wrapper.root

    return asyncio.run(_run())


def _list_tools(server: Any) -> list[str]:
    """Return the list of registered tool names."""

    async def _run() -> list[str]:
        from mcp.types import ListToolsRequest

        handler = server.request_handlers.get(ListToolsRequest)
        request = ListToolsRequest(method="tools/list")
        result = await handler(request)
        root = result.root if hasattr(result, "root") else result
        return [t.name for t in root.tools]

    return asyncio.run(_run())


def _build_server(backend: SQLiteBackend, writes_enabled: bool = True) -> Any:
    retriever = _LexicalRetriever(backend)
    return build_server(retriever_builder=lambda: retriever, writes_enabled=writes_enabled)


def _search_first_hit(server: Any, query: str, **extra: Any) -> dict:
    args: dict = {"query": query, "k": 5}
    args.update(extra)
    result = _call_tool(server, "search", args)
    hits = result.get("hits", [])
    assert hits, f"Expected at least one hit for query {query!r}; got 0"
    return hits[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> SQLiteBackend:
    return _make_backend()


@pytest.fixture
def seeded(backend: SQLiteBackend) -> dict[str, int]:
    return _seed_db(backend)


@pytest.fixture
def server_writes(backend: SQLiteBackend, seeded: dict[str, int]) -> Any:
    """Server with writes_enabled=True + seeded data."""
    return _build_server(backend, writes_enabled=True)


@pytest.fixture
def server_readonly(backend: SQLiteBackend, seeded: dict[str, int]) -> Any:
    """Server with writes_enabled=False + seeded data."""
    return _build_server(backend, writes_enabled=False)


# ---------------------------------------------------------------------------
# 1. writes_enabled gate
# ---------------------------------------------------------------------------


class TestWritesEnabledGate:
    def test_writes_disabled_exposes_only_3_tools(
        self, backend: SQLiteBackend, seeded: dict
    ) -> None:
        """When writes_enabled=False, only read tools are registered.

        G-03: render_conversation + list_chat_templates are always-available
        read tools, so the set is now 5 (not 3). J1 adds estimate_sync_size
        (also always-available, read-only) bumping the count to 6. J4 adds
        next_curation_target and next_curation_batch (both read-only,
        always available) → 8. Phase M Wave 3 adds list_ignore + validate_ignore
        → 10. Phase O Wave 4 adds analyze_corpus + find_duplicates +
        cluster_topics + score_quality → 14.
        """
        server = _build_server(backend, writes_enabled=False)
        tools = _list_tools(server)
        assert set(tools) == {
            "search",
            "get_chunk",
            "list_datasets",
            "render_conversation",
            "list_chat_templates",
            "estimate_sync_size",
            "next_curation_target",
            "next_curation_batch",
            # Phase M Wave 3 — .corpusignore read tools
            "list_ignore",
            "validate_ignore",
            # Phase O Wave 4 — analyze read tools
            "analyze_corpus",
            "find_duplicates",
            "cluster_topics",
            "score_quality",
        }, f"Expected exactly 14 read tools; got: {sorted(tools)}"

    def test_writes_enabled_exposes_all_11_tools(
        self, backend: SQLiteBackend, seeded: dict
    ) -> None:
        """When writes_enabled=True, all tools (read + write) are registered.

        H-02 adds register_session (write-gated) = 15 total.
        G-03 adds register_template (write-gated) + render_conversation +
        list_chat_templates (read, always available) = 14 total before H-02.
        J1 adds estimate_sync_size (read, always available) = 16 total.
        J4 adds next_curation_target + next_curation_batch (read) and
        commit_curation (write) = 19 total.
        Phase O Wave 4 adds 4 analyze read tools = 29 total.
        """
        server = _build_server(backend, writes_enabled=True)
        tools = _list_tools(server)
        expected = {
            "search",
            "get_chunk",
            "list_datasets",
            # G-03 read tools
            "render_conversation",
            "list_chat_templates",
            # J1 read tool
            "estimate_sync_size",
            # J4 read tools
            "next_curation_target",
            "next_curation_batch",
            # Phase M Wave 3 read tools
            "list_ignore",
            "validate_ignore",
            # Phase O Wave 4 analyze read tools
            "analyze_corpus",
            "find_duplicates",
            "cluster_topics",
            "score_quality",
            # F-03 write tools
            "add_label",
            "remove_label",
            "set_metadata",
            "set_description",
            "list_labels",
            "append_conversation",
            "append_message",
            "add_feedback",
            # G-03 write tool
            "register_template",
            # H-02 write tool
            "register_session",
            # J4 write tool
            "commit_curation",
            # Phase M Wave 3 write tools
            "add_ignore_pattern",
            "remove_ignore_pattern",
            "sync_ignore",
            # Phase M Wave 4 write tool
            "zotero_sync",
        }
        assert set(tools) == expected, (
            f"Expected 29 tools; missing={expected - set(tools)}, extra={set(tools) - expected}"
        )

    def test_unknown_tool_returns_error_when_writes_disabled(
        self, backend: SQLiteBackend, seeded: dict
    ) -> None:
        """Calling 'add_label' when writes_enabled=False returns isError=True (not a crash)."""
        server = _build_server(backend, writes_enabled=False)
        result = _call_tool_raw(
            server,
            "add_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "ns",
                "value": "v",
            },
        )
        assert getattr(result, "isError", False), (
            "Calling a write tool with writes_enabled=False must return isError=True"
        )


# ---------------------------------------------------------------------------
# 2. Write-tool callbacks via in-process MCP dispatch
# ---------------------------------------------------------------------------


class TestAddLabelViaMCP:
    def test_happy_path_returns_label_id_and_created(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """add_label via MCP dispatch returns {label_id, created, audit_id}."""
        result = _call_tool(
            server_writes,
            "add_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "topic",
                "value": "quantum",
            },
        )
        assert isinstance(result.get("label_id"), int), f"Unexpected result: {result}"
        assert result.get("created") is True
        assert isinstance(result.get("audit_id"), int)

    def test_duplicate_returns_created_false(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """Second add_label for same ns/value returns created=False."""
        args = {
            "entity_type": "chunk",
            "entity_id": seeded["chunk_id"],
            "namespace": "topic",
            "value": "quantum",
        }
        _call_tool(server_writes, "add_label", args)
        result = _call_tool(server_writes, "add_label", args)
        assert result.get("created") is False

    def test_dry_run_returns_none_label_id(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """dry_run=True → label_id is None, nothing persisted."""
        result = _call_tool(
            server_writes,
            "add_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "ghost",
                "value": "val",
                "dry_run": True,
            },
        )
        assert result.get("label_id") is None
        # Nothing in junction table
        with backend._get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM chunk_labels").fetchone()[0]
        assert count == 0


class TestRemoveLabelViaMCP:
    def test_happy_path_returns_removed_true(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """remove_label after add_label returns removed=True + audit_id."""
        _call_tool(
            server_writes,
            "add_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "topic",
                "value": "ml",
            },
        )
        result = _call_tool(
            server_writes,
            "remove_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "topic",
                "value": "ml",
            },
        )
        assert result.get("removed") is True
        assert isinstance(result.get("audit_id"), int)

    def test_nonexistent_label_returns_removed_false(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """Removing a never-applied label returns removed=False."""
        result = _call_tool(
            server_writes,
            "remove_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "ghost",
                "value": "ns",
            },
        )
        assert result.get("removed") is False


class TestSetMetadataViaMCP:
    def test_happy_path_returns_before_after(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """set_metadata returns {before, after, audit_id}; before is empty on first call."""
        result = _call_tool(
            server_writes,
            "set_metadata",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "key": "quality",
                "value": "high",
            },
        )
        assert isinstance(result.get("before"), dict)
        assert isinstance(result.get("after"), dict)
        assert result["after"].get("quality") == "high"
        assert isinstance(result.get("audit_id"), int)

    def test_before_reflects_previous_state(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """Second set_metadata call has before={"k": old} and after={"k": new}."""
        _call_tool(
            server_writes,
            "set_metadata",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "key": "k",
                "value": "old",
            },
        )
        result = _call_tool(
            server_writes,
            "set_metadata",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "key": "k",
                "value": "new",
            },
        )
        assert result["before"].get("k") == "old"
        assert result["after"].get("k") == "new"


class TestSetDescriptionViaMCP:
    def test_happy_path_sets_description(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """set_description returns {before, after, audit_id}; before=None on first call."""
        result = _call_tool(
            server_writes,
            "set_description",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "text": "A key passage about eigenvalues.",
            },
        )
        assert result.get("before") is None
        assert result.get("after") == "A key passage about eigenvalues."
        assert isinstance(result.get("audit_id"), int)

    def test_clear_description_with_null(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """Setting text=None clears the description; after is None."""
        _call_tool(
            server_writes,
            "set_description",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "text": "Original text.",
            },
        )
        result = _call_tool(
            server_writes,
            "set_description",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "text": None,
            },
        )
        assert result.get("before") == "Original text."
        assert result.get("after") is None


class TestListLabelsViaMCP:
    def test_returns_labels_key(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """list_labels returns a dict with 'labels' key (list)."""
        result = _call_tool(server_writes, "list_labels", {})
        assert "labels" in result
        assert isinstance(result["labels"], list)

    def test_returns_applied_labels(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """Labels applied via add_label appear in list_labels output."""
        _call_tool(
            server_writes,
            "add_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "topic",
                "value": "ml",
            },
        )
        result = _call_tool(server_writes, "list_labels", {})
        namespaces = [item["namespace"] for item in result["labels"]]
        assert "topic" in namespaces

    def test_filter_by_entity_type(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """entity_type filter narrows list_labels results."""
        _call_tool(
            server_writes,
            "add_label",
            {
                "entity_type": "document",
                "entity_id": seeded["document_id"],
                "namespace": "ns",
                "value": "v",
            },
        )
        result = _call_tool(server_writes, "list_labels", {"entity_type": "document"})
        # All items must be document-scope
        for item in result["labels"]:
            assert item.get("entity_type", "document") == "document"


class TestAppendConversationViaMCP:
    def test_happy_path_returns_conversation_id(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """append_conversation creates a conversation and returns its id + message_count."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        result = _call_tool(
            server_writes,
            "append_conversation",
            {
                "dataset": seeded["dataset_name"],
                "title": "Test conversation",
                "messages": messages,
            },
        )
        assert isinstance(result.get("conversation_id"), int)
        assert result.get("message_count") == 2
        assert isinstance(result.get("audit_id"), int)

    def test_dry_run_returns_none_conversation_id(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """dry_run=True returns conversation_id=None and does not persist."""
        messages = [{"role": "user", "content": "dry"}]
        result = _call_tool(
            server_writes,
            "append_conversation",
            {
                "dataset": seeded["dataset_name"],
                "title": "Dry run",
                "messages": messages,
                "dry_run": True,
            },
        )
        assert result.get("conversation_id") is None
        assert result.get("message_count") == 1

    def test_messages_turn_indexes_are_sequential(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """Messages inside the appended conversation get turn_index 0..N-1."""
        messages = [
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
            {"role": "user", "content": "C"},
        ]
        result = _call_tool(
            server_writes,
            "append_conversation",
            {
                "dataset": seeded["dataset_name"],
                "title": "Turn test",
                "messages": messages,
            },
        )
        conv_id = result["conversation_id"]
        with backend._get_connection() as conn:
            rows = conn.execute(
                "SELECT turn_index FROM messages WHERE conversation_id = ? ORDER BY turn_index",
                (conv_id,),
            ).fetchall()
        assert [r["turn_index"] for r in rows] == [0, 1, 2]


class TestAppendMessageViaMCP:
    def test_happy_path_returns_message_id_and_turn_index(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """append_message returns {message_id, turn_index, audit_id}."""
        result = _call_tool(
            server_writes,
            "append_message",
            {
                "conversation_id": seeded["conversation_id"],
                "role": "assistant",
                "content": "Hello back!",
            },
        )
        assert isinstance(result.get("message_id"), int)
        # seeded conversation already has one message at turn_index 0
        assert result.get("turn_index") == 1
        assert isinstance(result.get("audit_id"), int)

    def test_turn_index_advances_monotonically(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """Successive append_message calls produce strictly increasing turn_index values."""
        r1 = _call_tool(
            server_writes,
            "append_message",
            {
                "conversation_id": seeded["conversation_id"],
                "role": "assistant",
                "content": "One",
            },
        )
        r2 = _call_tool(
            server_writes,
            "append_message",
            {
                "conversation_id": seeded["conversation_id"],
                "role": "user",
                "content": "Two",
            },
        )
        assert r1["turn_index"] < r2["turn_index"]

    def test_dry_run_returns_none_message_id(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """dry_run=True returns message_id=None and does not persist the message."""
        with backend._get_connection() as conn:
            count_before = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                (seeded["conversation_id"],),
            ).fetchone()[0]

        result = _call_tool(
            server_writes,
            "append_message",
            {
                "conversation_id": seeded["conversation_id"],
                "role": "user",
                "content": "ephemeral",
                "dry_run": True,
            },
        )
        assert result.get("message_id") is None

        with backend._get_connection() as conn:
            count_after = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                (seeded["conversation_id"],),
            ).fetchone()[0]
        assert count_after == count_before


class TestAddFeedbackViaMCP:
    def test_happy_path_returns_feedback_id(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """add_feedback returns {feedback_id, audit_id} on success."""
        result = _call_tool(
            server_writes,
            "add_feedback",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "kind": "thumbs",
                "rating": 1,
                "text": "Very helpful!",
            },
        )
        assert isinstance(result.get("feedback_id"), int)
        assert result.get("feedback_id", 0) > 0
        assert isinstance(result.get("audit_id"), int)

    def test_dry_run_returns_none_feedback_id(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """dry_run=True returns feedback_id=None and does not persist feedback."""
        result = _call_tool(
            server_writes,
            "add_feedback",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "kind": "flag",
                "rating": 0,
                "dry_run": True,
            },
        )
        assert result.get("feedback_id") is None
        with backend._get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        assert count == 0

    def test_rating_optional(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """add_feedback with text but no rating is accepted."""
        result = _call_tool(
            server_writes,
            "add_feedback",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "kind": "comment",
                "text": "Needs clarification.",
            },
        )
        assert isinstance(result.get("feedback_id"), int)


# ---------------------------------------------------------------------------
# 3. Read-side enrichment in search
# ---------------------------------------------------------------------------


class TestSearchEnrichment:
    def test_search_response_includes_labels_when_enabled(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """search() hit has a 'labels' list containing the seeded label dict."""
        _call_tool(
            server_writes,
            "add_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "topic",
                "value": "quantum",
            },
        )
        hit = _search_first_hit(server_writes, "eigenvalue Hamiltonian")
        assert "labels" in hit, f"Expected 'labels' key in hit; got keys={list(hit)}"
        labels = hit["labels"]
        assert len(labels) > 0, "Expected non-empty labels"
        label = labels[0]
        assert label.get("namespace") == "topic"
        assert label.get("value") == "quantum"
        # Pinned wire format must include source + confidence
        assert "source" in label
        assert "confidence" in label

    def test_search_response_omits_labels_when_disabled(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """search(include_labels=False) → 'labels' key must be absent from each hit."""
        _call_tool(
            server_writes,
            "add_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "topic",
                "value": "quantum",
            },
        )
        hit = _search_first_hit(server_writes, "eigenvalue Hamiltonian", include_labels=False)
        assert "labels" not in hit, (
            f"Expected 'labels' absent when include_labels=False; got keys={list(hit)}"
        )

    def test_search_response_includes_description(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """search() hit has 'description' matching what was set."""
        _call_tool(
            server_writes,
            "set_description",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "text": "Key passage about quantization.",
            },
        )
        hit = _search_first_hit(server_writes, "eigenvalue Hamiltonian")
        assert "description" in hit, f"Expected 'description' in hit; got keys={list(hit)}"
        assert hit["description"] == "Key passage about quantization."

    def test_search_response_omits_description_when_disabled(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """search(include_description=False) → 'description' key is absent."""
        _call_tool(
            server_writes,
            "set_description",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "text": "Some desc.",
            },
        )
        hit = _search_first_hit(server_writes, "eigenvalue Hamiltonian", include_description=False)
        assert "description" not in hit, (
            f"Expected 'description' absent when include_description=False; got keys={list(hit)}"
        )

    def test_search_response_includes_recent_feedback(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """search() hit has 'recent_feedback' with the seeded feedback entries."""
        _call_tool(
            server_writes,
            "add_feedback",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "kind": "thumbs",
                "rating": 1,
                "text": "Very helpful.",
            },
        )
        hit = _search_first_hit(server_writes, "eigenvalue Hamiltonian")
        assert "recent_feedback" in hit, f"Expected 'recent_feedback' in hit; got keys={list(hit)}"
        fb = hit["recent_feedback"]
        assert len(fb) >= 1, f"Expected at least 1 feedback entry; got {fb}"

    def test_search_response_omits_feedback_when_disabled(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """search(include_feedback=False) → 'recent_feedback' key is absent."""
        _call_tool(
            server_writes,
            "add_feedback",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "kind": "thumbs",
                "rating": 1,
            },
        )
        hit = _search_first_hit(server_writes, "eigenvalue Hamiltonian", include_feedback=False)
        assert "recent_feedback" not in hit, (
            f"Expected 'recent_feedback' absent when include_feedback=False; got keys={list(hit)}"
        )

    def test_search_no_enrichment_when_no_backend(
        self, backend: SQLiteBackend, seeded: dict
    ) -> None:
        """Retriever without a backend attribute does not raise; enrichment is skipped."""
        from unittest.mock import MagicMock

        # Retriever that has no .backend attribute
        fake_retriever = MagicMock()
        del fake_retriever.backend  # make getattr return None via MagicMock quirk
        fake_retriever.backend = None

        from corpus_forge.retrieval.types import Hit

        hit = Hit(
            chunk_id=seeded["chunk_id"],
            score=0.9,
            text=_CHUNK_TEXT,
            document_id=seeded["document_id"],
            source_uri="fake://doc.md",
            title="Fake",
            dataset_id=seeded["dataset_id"],
            metadata={},
            source="lexical",
        )
        fake_retriever.search.return_value = [hit]
        server = build_server(retriever_builder=lambda: fake_retriever, writes_enabled=False)
        # Should not raise
        result = _call_tool(server, "search", {"query": "eigenvalue"})
        assert "hits" in result


# ---------------------------------------------------------------------------
# 4. Read-side enrichment in get_chunk
# ---------------------------------------------------------------------------


class TestGetChunkEnrichment:
    def test_get_chunk_includes_description(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """get_chunk result includes 'description' field after set_description."""
        _call_tool(
            server_writes,
            "set_description",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "text": "This chunk discusses quantization.",
            },
        )
        result = _call_tool(server_writes, "get_chunk", {"chunk_id": seeded["chunk_id"]})
        assert "description" in result, (
            f"Expected 'description' in get_chunk result; got keys={list(result)}"
        )
        assert result["description"] == "This chunk discusses quantization."

    def test_get_chunk_includes_labels(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """get_chunk result includes 'labels' after add_label."""
        _call_tool(
            server_writes,
            "add_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "quality",
                "value": "high",
            },
        )
        result = _call_tool(server_writes, "get_chunk", {"chunk_id": seeded["chunk_id"]})
        assert "labels" in result, f"Expected 'labels' in get_chunk; got keys={list(result)}"
        assert len(result["labels"]) > 0
        assert result["labels"][0].get("namespace") == "quality"

    def test_get_chunk_includes_recent_feedback(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """get_chunk result includes 'recent_feedback' after add_feedback."""
        _call_tool(
            server_writes,
            "add_feedback",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "kind": "thumbs",
                "rating": 1,
            },
        )
        result = _call_tool(server_writes, "get_chunk", {"chunk_id": seeded["chunk_id"]})
        assert "recent_feedback" in result, (
            f"Expected 'recent_feedback' in get_chunk; got keys={list(result)}"
        )
        assert len(result["recent_feedback"]) >= 1

    def test_get_chunk_omits_labels_when_disabled(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """get_chunk(include_labels=False) → 'labels' key absent."""
        _call_tool(
            server_writes,
            "add_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "ns",
                "value": "v",
            },
        )
        result = _call_tool(
            server_writes,
            "get_chunk",
            {"chunk_id": seeded["chunk_id"], "include_labels": False},
        )
        assert "labels" not in result, (
            f"Expected 'labels' absent when include_labels=False; got keys={list(result)}"
        )

    def test_get_chunk_returns_error_for_missing_chunk(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """get_chunk with unknown chunk_id returns isError=True."""
        result = _call_tool_raw(server_writes, "get_chunk", {"chunk_id": 999999})
        assert getattr(result, "isError", False), "Unknown chunk_id must yield isError=True"


# ---------------------------------------------------------------------------
# 5. Parent rollup for chunk hits
# ---------------------------------------------------------------------------


class TestSearchParentRollup:
    def test_search_chunk_hit_includes_parent_rollup(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """Chunk hit with a document_id includes a 'parent' dict with doc enrichment."""
        # Set description on the DOCUMENT, not the chunk
        _call_tool(
            server_writes,
            "set_description",
            {
                "entity_type": "document",
                "entity_id": seeded["document_id"],
                "text": "Parent document about quantum band structure.",
            },
        )
        _call_tool(
            server_writes,
            "add_label",
            {
                "entity_type": "document",
                "entity_id": seeded["document_id"],
                "namespace": "domain",
                "value": "physics",
            },
        )
        hit = _search_first_hit(server_writes, "eigenvalue Hamiltonian")
        assert "parent" in hit, f"Expected 'parent' rollup in chunk hit; got keys={list(hit)}"
        parent = hit["parent"]
        assert isinstance(parent, dict)
        assert parent.get("description") == "Parent document about quantum band structure."
        parent_labels = parent.get("labels", [])
        assert any(
            lbl.get("namespace") == "domain" and lbl.get("value") == "physics"
            for lbl in parent_labels
        ), f"Expected domain/physics in parent labels; got: {parent_labels}"
        # recent_feedback key must be present (even if empty list)
        assert "recent_feedback" in parent

    def test_get_chunk_parent_rollup(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """get_chunk result includes 'parent' dict with document enrichment."""
        _call_tool(
            server_writes,
            "set_description",
            {
                "entity_type": "document",
                "entity_id": seeded["document_id"],
                "text": "Parent doc desc for get_chunk.",
            },
        )
        result = _call_tool(server_writes, "get_chunk", {"chunk_id": seeded["chunk_id"]})
        assert "parent" in result, f"Expected 'parent' in get_chunk result; got keys={list(result)}"
        parent = result["parent"]
        assert parent.get("description") == "Parent doc desc for get_chunk."


# ---------------------------------------------------------------------------
# 6. _labels_to_wire helper — both dict and tuple input paths
# ---------------------------------------------------------------------------


class TestLabelsToWire:
    """Exercise the internal _labels_to_wire helper via enriched search results."""

    def test_dict_label_items_wire_correctly(
        self, backend: SQLiteBackend, seeded: dict, server_writes: Any
    ) -> None:
        """Labels stored as dicts come through with source + confidence fields."""
        _call_tool(
            server_writes,
            "add_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "wire-test",
                "value": "dict-path",
                "confidence": 0.95,
            },
        )
        hit = _search_first_hit(server_writes, "eigenvalue Hamiltonian")
        labels = hit.get("labels", [])
        assert len(labels) > 0
        label = labels[0]
        # Wire format contract
        assert set(label.keys()) >= {"namespace", "value", "source", "confidence"}
        assert label["namespace"] == "wire-test"
        assert label["value"] == "dict-path"

    def test_labels_to_wire_direct_tuple_path(self) -> None:
        """_labels_to_wire handles raw (namespace, value) tuples correctly."""
        from corpus_forge.mcp.server import _labels_to_wire

        raw = [("topic", "ml"), ("lang", "en")]
        result = _labels_to_wire(raw)
        assert len(result) == 2
        assert result[0]["namespace"] == "topic"
        assert result[0]["value"] == "ml"
        assert result[0]["source"] == "user"
        assert result[0]["confidence"] is None

    def test_labels_to_wire_direct_dict_path(self) -> None:
        """_labels_to_wire handles dict items with all wire fields."""
        from corpus_forge.mcp.server import _labels_to_wire

        raw = [
            {"namespace": "quality", "value": "high", "source": "model", "confidence": 0.9},
        ]
        result = _labels_to_wire(raw)
        assert len(result) == 1
        assert result[0]["source"] == "model"
        assert abs(result[0]["confidence"] - 0.9) < 1e-6

    def test_labels_to_wire_empty_list(self) -> None:
        """_labels_to_wire([]) returns []."""
        from corpus_forge.mcp.server import _labels_to_wire

        assert _labels_to_wire([]) == []


# ---------------------------------------------------------------------------
# 7. G-03 — render_conversation via in-process MCP server
# ---------------------------------------------------------------------------


def _seed_conversation_via_backend(backend: SQLiteBackend, n_messages: int = 3) -> dict[str, int]:
    """Seed a dataset + conversation with n_messages messages using backend.append_conversation.

    Returns {dataset_id, conversation_id}.
    """
    with backend._get_connection() as conn:
        ds_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("conv-ds", "chat", "conversation dataset"),
        ).fetchone()[0]
        conn.commit()

    roles = ["user", "assistant", "user"]
    messages = [
        {"role": roles[i % len(roles)], "content": f"Message {i}"} for i in range(n_messages)
    ]
    conv_id, _ = backend.append_conversation(
        dataset_id=ds_id,
        title="Test conv",
        started_at=None,
        messages=messages,
    )
    return {"dataset_id": ds_id, "conversation_id": conv_id}


class TestRenderConversationViaMCP:
    def test_happy_path_with_builtin_template(self, backend: SQLiteBackend) -> None:
        """render_conversation: chatml text, message_count=3, truncated=False."""
        seeded = _seed_conversation_via_backend(backend, n_messages=3)
        server = _build_server(backend, writes_enabled=True)
        result = _call_tool(
            server,
            "render_conversation",
            {
                "conversation_id": seeded["conversation_id"],
                "template": "chatml",
            },
        )
        assert "<|im_start|>" in result["text"], (
            f"Expected chatml markers in text; got: {result['text'][:200]}"
        )
        assert result["message_count"] == 3
        assert result["template"] == "chatml"
        assert result["truncated"] is False

    def test_with_custom_jinja(self, backend: SQLiteBackend) -> None:
        """render_conversation with custom_jinja renders the Jinja string directly."""
        seeded = _seed_conversation_via_backend(backend, n_messages=3)
        server = _build_server(backend, writes_enabled=True)
        result = _call_tool(
            server,
            "render_conversation",
            {
                "conversation_id": seeded["conversation_id"],
                "template": "chatml",
                "custom_jinja": "{{ messages|length }}",
            },
        )
        assert result["text"] == "3", (
            f"custom_jinja '{{{{ messages|length }}}}' must render '3'; got: {result['text']!r}"
        )

    def test_nonexistent_conversation_returns_error(self, backend: SQLiteBackend) -> None:
        """render_conversation with a non-existent conversation_id returns isError=True."""
        server = _build_server(backend, writes_enabled=True)
        result = _call_tool_raw(
            server,
            "render_conversation",
            {"conversation_id": 99999, "template": "chatml"},
        )
        assert getattr(result, "isError", False), (
            "render_conversation for missing conversation must return isError=True"
        )


# ---------------------------------------------------------------------------
# 8. G-03 — list_chat_templates via in-process MCP server
# ---------------------------------------------------------------------------


class TestListChatTemplatesViaMCP:
    def test_empty_corpus_returns_empty_list(self, backend: SQLiteBackend) -> None:
        """Fresh DB returns {'templates': []} — no built-ins are auto-inserted."""
        server = _build_server(backend, writes_enabled=True)
        result = _call_tool(server, "list_chat_templates", {})
        assert result == {"templates": []}, (
            f"Expected empty templates list on fresh DB; got: {result}"
        )

    def test_returns_registered_templates(self, backend: SQLiteBackend) -> None:
        """Templates registered via backend.register_chat_template appear in the listing."""
        backend.register_chat_template(
            name="alpha",
            source="custom",
            jinja="{{ messages|length }}",
            host="test",
        )
        backend.register_chat_template(
            name="beta",
            source="custom",
            jinja="{{ messages[0].role }}",
            host="test",
        )
        server = _build_server(backend, writes_enabled=True)
        result = _call_tool(server, "list_chat_templates", {})
        names = [t["name"] for t in result["templates"]]
        assert "alpha" in names, f"Expected 'alpha' in templates; got: {names}"
        assert "beta" in names, f"Expected 'beta' in templates; got: {names}"
        assert len(result["templates"]) == 2


# ---------------------------------------------------------------------------
# 9. G-03 — register_template via in-process MCP server
# ---------------------------------------------------------------------------


class TestRegisterTemplateViaMCP:
    def test_creates_row_and_returns_audit_id(self, backend: SQLiteBackend) -> None:
        """register_template inserts a chat_templates row and emits an mcp_audit entry."""
        server = _build_server(backend, writes_enabled=True)
        result = _call_tool(
            server,
            "register_template",
            {
                "name": "my-template",
                "jinja": "{% for m in messages %}{{ m.role }}: {{ m.content }}\n{% endfor %}",
                "description": "simple role:content format",
            },
        )
        assert isinstance(result.get("template_id"), int), (
            f"Expected integer template_id; got: {result}"
        )
        assert isinstance(result.get("audit_id"), int), f"Expected integer audit_id; got: {result}"
        # Verify DB row exists.
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT id, name FROM chat_templates WHERE name = ?", ("my-template",)
            ).fetchone()
        assert row is not None, "chat_templates row must be present after register_template"
        # Verify audit row exists.
        with backend._get_connection() as conn:
            audit_rows = conn.execute("SELECT * FROM mcp_audit ORDER BY id").fetchall()
        assert len(audit_rows) >= 1, "mcp_audit must have at least one entry"

    def test_dry_run_does_not_persist(self, backend: SQLiteBackend) -> None:
        """dry_run=True emits an audit row but does NOT insert a chat_templates row."""
        server = _build_server(backend, writes_enabled=True)
        result = _call_tool(
            server,
            "register_template",
            {
                "name": "ephemeral-template",
                "jinja": "{{ messages|length }}",
                "dry_run": True,
            },
        )
        # template_id must be None on dry run.
        assert result.get("template_id") is None, (
            f"dry_run=True must return template_id=None; got: {result}"
        )
        # audit_id must be present.
        assert isinstance(result.get("audit_id"), int)
        # No chat_templates row.
        with backend._get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM chat_templates").fetchone()[0]
        assert count == 0, f"dry_run=True must not persist a chat_templates row; count={count}"
        # But an audit row IS created.
        with backend._get_connection() as conn:
            audit_count = conn.execute("SELECT COUNT(*) FROM mcp_audit").fetchone()[0]
        assert audit_count >= 1, "dry_run=True must still emit an audit row"

    def test_writes_disabled_omits_register_template(self, backend: SQLiteBackend) -> None:
        """When writes_enabled=False, register_template is NOT in the tools list."""
        server = _build_server(backend, writes_enabled=False)
        tools = _list_tools(server)
        assert "register_template" not in tools, (
            f"register_template must not appear when writes_enabled=False; tools={tools}"
        )


# ---------------------------------------------------------------------------
# 10. G-03 — get_chunk with template= argument via in-process MCP server
# ---------------------------------------------------------------------------


class TestGetChunkWithTemplate:
    def test_message_chunk_gets_templated_text(self, backend: SQLiteBackend) -> None:
        """get_chunk with template='chatml' on a message chunk returns templated_text."""
        seeded = _seed_conversation_via_backend(backend, n_messages=3)
        # Retrieve a chunk that is linked to a message (message_id IS NOT NULL).
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT id FROM chunks"
                " WHERE conversation_id = ? AND message_id IS NOT NULL LIMIT 1",
                (seeded["conversation_id"],),
            ).fetchone()
        assert row is not None, "append_conversation must have created at least one message chunk"
        chunk_id = row[0]

        server = _build_server(backend, writes_enabled=True)
        result = _call_tool(
            server,
            "get_chunk",
            {"chunk_id": chunk_id, "template": "chatml"},
        )
        assert "templated_text" in result, (
            f"Expected 'templated_text' in get_chunk result; got keys={list(result)}"
        )
        assert result["templated_text"] is not None
        assert "<|im_start|>" in result["templated_text"], (
            f"Expected chatml markers in templated_text; got: {result['templated_text']!r}"
        )

    def test_document_chunk_templated_text_is_none(self, backend: SQLiteBackend) -> None:
        """get_chunk with template= on a document chunk returns templated_text=None."""
        # Use the standard seeded document chunk (no message_id).
        seeded = _seed_db(backend)
        server = _build_server(backend, writes_enabled=True)
        result = _call_tool(
            server,
            "get_chunk",
            {"chunk_id": seeded["chunk_id"], "template": "chatml"},
        )
        assert "templated_text" in result, (
            f"Expected 'templated_text' key in result; got keys={list(result)}"
        )
        assert result["templated_text"] is None, (
            f"Document chunk must have templated_text=None; got: {result['templated_text']!r}"
        )
