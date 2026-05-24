"""G-03 RED — MCP template tools end-to-end integration tests.

Cross-backend integration via real ``build_server(retriever, writes_enabled=True)``.
SQLite in-memory + a minimal LexicalRetriever stub.

All three G-03 MCP tools are exercised through the server's
``request_handlers[CallToolRequest]`` pathway (same pattern as
``test_mcp_server_enrichment.py``).

These tests must fail RED because:
  1. ``render_conversation``, ``list_chat_templates``, and ``register_template``
     are not yet registered in ``build_server``.
  2. ``corpus_forge/mcp/templates.py`` does not exist yet.

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.mcp.server import build_server
from corpus_forge.retrieval.types import Hit, SearchOptions

if TYPE_CHECKING:
    from mcp.server import Server

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers — SQLite in-memory backend + minimal LexicalRetriever stub
# ---------------------------------------------------------------------------


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


class _LexicalRetriever:
    """Thin wrapper — no embedder, no ML."""

    def __init__(self, backend: SQLiteBackend) -> None:
        self.backend = backend

    def search(self, query: str, options: SearchOptions) -> list[Hit]:
        k = getattr(options, "k", 10)
        return self.backend.search_lexical(query, k=k)


def _build_server(backend: SQLiteBackend) -> Server[object]:
    retriever = _LexicalRetriever(backend)
    return build_server(retriever_builder=lambda: retriever, writes_enabled=True)


# ---------------------------------------------------------------------------
# MCP call helpers (mirrors test_mcp_server_enrichment.py pattern)
# ---------------------------------------------------------------------------


def _call_tool(server: Server[object], name: str, arguments: dict) -> dict:
    """Invoke an MCP tool synchronously; returns structured payload or raises."""

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
        structured: dict[str, object] | None = getattr(root, "structuredContent", None)
        if structured is not None:
            return dict(structured)
        text_blocks = [getattr(b, "text", "") for b in getattr(root, "content", [])]
        return json.loads("".join(text_blocks))

    return asyncio.run(_run())


def _list_tools(server: Server[object]) -> list[str]:
    async def _run() -> list[str]:
        from mcp.types import ListToolsRequest

        handler = server.request_handlers.get(ListToolsRequest)
        assert handler is not None, "No ListToolsRequest handler registered on server"
        request = ListToolsRequest(method="tools/list")
        result = await handler(request)
        root = result.root if hasattr(result, "root") else result
        # ``ListToolsRequest`` always dispatches to a ``ListToolsResult`` —
        # the union-typed ``ServerResult`` hides that from the type checker.
        tools = getattr(root, "tools", None)
        assert tools is not None, "Expected ListToolsResult with `tools` attribute"
        return [t.name for t in tools]

    return asyncio.run(_run())


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_conversation(
    backend: SQLiteBackend,
    *,
    n_messages: int = 3,
    dataset_name: str = "g03-int-ds",
) -> dict[str, int]:
    """Insert a dataset + conversation.  Returns {dataset_id, conversation_id}."""
    with backend._get_connection() as conn:
        ds_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            (dataset_name, "chat", "G-03 integration test dataset"),
        ).fetchone()[0]
        conn.commit()

    roles = ["user", "assistant", "user"]
    messages = [
        {"role": roles[i % len(roles)], "content": f"Integration message {i}"}
        for i in range(n_messages)
    ]
    conv_id, _ = backend.append_conversation(
        dataset_id=ds_id,
        title="G-03 integration conversation",
        started_at=None,
        messages=messages,
    )
    return {"dataset_id": ds_id, "conversation_id": conv_id}


def _get_audit_rows(backend: SQLiteBackend) -> list[sqlite3.Row]:
    with backend._get_connection() as conn:
        return conn.execute("SELECT * FROM mcp_audit ORDER BY id").fetchall()


# ---------------------------------------------------------------------------
# Test: new tools are registered when writes_enabled=True
# ---------------------------------------------------------------------------


class TestToolsRegistered:
    def test_render_conversation_registered(self):
        """render_conversation must appear in the tools list."""
        backend = _make_backend()
        server = _build_server(backend)
        tool_names = _list_tools(server)
        assert "render_conversation" in tool_names, (
            f"render_conversation not in tools list: {tool_names}"
        )

    def test_list_chat_templates_registered(self):
        """list_chat_templates must appear in the tools list."""
        backend = _make_backend()
        server = _build_server(backend)
        tool_names = _list_tools(server)
        assert "list_chat_templates" in tool_names

    def test_register_template_registered(self):
        """register_template must appear in the tools list (write-gated)."""
        backend = _make_backend()
        server = _build_server(backend)
        tool_names = _list_tools(server)
        assert "register_template" in tool_names


# ---------------------------------------------------------------------------
# Test 1: render_conversation end-to-end with chatml builtin
# ---------------------------------------------------------------------------


class TestRenderConversationEndToEnd:
    def test_chatml_round_trip_returns_text(self):
        """Full MCP round-trip: seed conversation, render via chatml, check text."""
        backend = _make_backend()
        server = _build_server(backend)
        seeded = _seed_conversation(backend, n_messages=3)

        result = _call_tool(
            server,
            "render_conversation",
            {
                "conversation_id": seeded["conversation_id"],
                "template": "chatml",
            },
        )

        assert "<|im_start|>" in result["text"]
        assert result["message_count"] == 3
        assert result["conversation_id"] == seeded["conversation_id"]
        assert result["truncated"] is False

    def test_render_with_custom_jinja_via_mcp(self):
        """custom_jinja param flows through the MCP layer correctly."""
        backend = _make_backend()
        server = _build_server(backend)
        seeded = _seed_conversation(backend, n_messages=2)

        result = _call_tool(
            server,
            "render_conversation",
            {
                "conversation_id": seeded["conversation_id"],
                "template": "chatml",
                "custom_jinja": "MSG_COUNT={{ messages | length }}",
            },
        )
        assert result["text"] == "MSG_COUNT=2"

    def test_nonexistent_conversation_returns_error(self):
        """render_conversation with invalid conversation_id returns isError=True
        with a message that mentions 'conversation' (not the generic 'unknown tool').
        """

        async def _run() -> tuple[bool, str]:
            from mcp.types import CallToolRequest, CallToolRequestParams

            backend = _make_backend()
            server = _build_server(backend)
            handler = server.request_handlers.get(CallToolRequest)
            assert handler is not None, "No CallToolRequest handler registered on server"
            request = CallToolRequest(
                method="tools/call",
                params=CallToolRequestParams(
                    name="render_conversation",
                    arguments={"conversation_id": 99999, "template": "chatml"},
                ),
            )
            wrapper = await handler(request)
            root = wrapper.root
            is_error = bool(getattr(root, "isError", False))
            text = "".join(getattr(b, "text", "") for b in getattr(root, "content", []))
            return is_error, text

        is_error, text = asyncio.run(_run())
        assert is_error, "Expected isError=True for nonexistent conversation_id"
        # The error message must mention conversation-not-found semantics,
        # NOT the generic 'unknown tool' fallback — proves the tool is registered.
        assert "unknown tool" not in text.lower(), (
            f"Expected a meaningful error about conversation not found, "
            f"got generic 'unknown tool' response: {text!r}"
        )


# ---------------------------------------------------------------------------
# Test 2: register_template then render with that template
# ---------------------------------------------------------------------------


class TestRegisterThenRender:
    def test_register_custom_then_render(self):
        """Register a custom Jinja template, then render a conversation with it."""
        backend = _make_backend()
        server = _build_server(backend)
        seeded = _seed_conversation(backend, n_messages=2)

        # Step 1: register the template via MCP
        reg_result = _call_tool(
            server,
            "register_template",
            {
                "name": "my-integration-tmpl",
                "jinja": "HELLO:{% for m in messages %}{{ m.role }}{% endfor %}",
                "description": "Integration test template",
            },
        )
        assert isinstance(reg_result["template_id"], int)
        assert isinstance(reg_result["audit_id"], int)

        # Step 2: render with that template name
        render_result = _call_tool(
            server,
            "render_conversation",
            {
                "conversation_id": seeded["conversation_id"],
                "template": "my-integration-tmpl",
            },
        )
        assert render_result["text"].startswith("HELLO:")

    def test_dry_run_register_does_not_persist(self):
        """dry_run=True on register_template: template not in list after call."""
        backend = _make_backend()
        server = _build_server(backend)

        _call_tool(
            server,
            "register_template",
            {
                "name": "dry-run-tmpl",
                "jinja": "{{ messages }}",
                "dry_run": True,
            },
        )

        list_result = _call_tool(server, "list_chat_templates", {})
        names = [t["name"] for t in list_result["templates"]]
        assert "dry-run-tmpl" not in names


# ---------------------------------------------------------------------------
# Test 3: list_chat_templates includes registered templates
# ---------------------------------------------------------------------------


class TestListChatTemplatesIntegration:
    def test_list_empty_on_fresh_backend(self):
        """Fresh backend: list_chat_templates returns empty list."""
        backend = _make_backend()
        server = _build_server(backend)
        result = _call_tool(server, "list_chat_templates", {})
        assert result["templates"] == []

    def test_list_includes_registered(self):
        """After registering a template via MCP, list includes it."""
        backend = _make_backend()
        server = _build_server(backend)

        _call_tool(
            server,
            "register_template",
            {"name": "listed-tmpl", "jinja": "{{ messages }}", "description": "listed"},
        )

        result = _call_tool(server, "list_chat_templates", {})
        names = [t["name"] for t in result["templates"]]
        assert "listed-tmpl" in names

    def test_list_entries_have_required_fields(self):
        """Each entry in templates has name, source, model_id, description."""
        backend = _make_backend()
        server = _build_server(backend)

        _call_tool(
            server,
            "register_template",
            {"name": "fields-tmpl", "jinja": "{{ messages }}", "description": "with fields"},
        )

        result = _call_tool(server, "list_chat_templates", {})
        entry = next(t for t in result["templates"] if t["name"] == "fields-tmpl")
        assert "name" in entry
        assert "source" in entry
        assert "model_id" in entry
        assert "description" in entry
