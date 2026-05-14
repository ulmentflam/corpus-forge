"""F-05 — Live chat → corpus → search round-trip and cross-host visibility.

Two integration scenarios:

1. ``test_live_chat_round_trip`` — append a 6-message conversation with a
   unique phrase in message 4, then search for that phrase.  The MCP server
   must return a hit pointing at the conversation.

2. ``test_append_conversation_cross_host_visible`` — Host A (one
   PostgresBackend instance) calls ``append_conversation``; Host B (a second
   PostgresBackend pointing at the same DSN) calls ``search``.  The hit
   must be visible to Host B.  This is the self-distillation loop pin.

Run command:
    .venv/bin/python -m pytest tests/integration/test_append_conversation_e2e.py -v

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.mcp.server import build_server

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Unique content anchor used across both tests
# ---------------------------------------------------------------------------

_UNIQUE_PHRASE = "xylophone-gradient-eigenvalue-42"  # unlikely to collide

# ---------------------------------------------------------------------------
# Helpers (mirror test_mcp_writes_postgres.py pattern)
# ---------------------------------------------------------------------------


class _PgLexicalRetriever:
    """Minimal retriever backed by PostgresBackend.search_lexical — no ML."""

    def __init__(self, backend: PostgresBackend) -> None:
        self.backend = backend

    def search(self, query: str, options: Any) -> list[Any]:
        k = getattr(options, "k", 10)
        return self.backend.search_lexical(query, k=k)


def _make_pg_backend(pg_dsn: str) -> PostgresBackend:
    b = PostgresBackend(dsn=pg_dsn, schema="corpus")
    b.migrate()
    return b


def _build_pg_server(backend: PostgresBackend, writes_enabled: bool = True) -> Any:
    retriever = _PgLexicalRetriever(backend)
    return build_server(retriever_builder=lambda: retriever, writes_enabled=writes_enabled)


def _call_tool(server: Any, name: str, arguments: dict) -> dict:
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


def _ensure_dataset(backend: PostgresBackend, name: str) -> int:
    """Create dataset if it doesn't exist, return its id."""
    existing = backend._execute("SELECT id FROM corpus.datasets WHERE name = %s", (name,))
    if existing:
        return existing[0]["id"]
    result = backend._execute(
        "INSERT INTO corpus.datasets (name, kind, description) VALUES (%s, %s, %s) RETURNING id",
        (name, "chat", "F-05 e2e test dataset"),
    )
    return result[0]["id"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pg_backend(pg_dsn: str) -> PostgresBackend:
    return _make_pg_backend(pg_dsn)


@pytest.fixture
def server(pg_backend: PostgresBackend) -> Any:
    return _build_pg_server(pg_backend, writes_enabled=True)


# ---------------------------------------------------------------------------
# Test 1: live chat round-trip
# ---------------------------------------------------------------------------


def test_live_chat_round_trip(pg_backend: PostgresBackend, server: Any) -> None:
    """Append a 6-msg conversation; search for unique phrase from msg 4 finds a hit.

    The self-distillation loop: a live chat session is indexed (via
    append_conversation) and immediately becomes retrievable by the same MCP
    server.  The hit must reference the appended conversation.
    """
    _ensure_dataset(pg_backend, "live-chat-test")

    messages = [
        {"role": "user", "content": "Can you explain eigenvalue decomposition?"},
        {"role": "assistant", "content": "Sure! Eigenvalue decomposition factors a matrix."},
        {"role": "user", "content": "How does it relate to PCA?"},
        {
            "role": "assistant",
            "content": (
                f"Great question. The {_UNIQUE_PHRASE} connection lies in the "
                "covariance matrix spectral analysis."
            ),
        },
        {"role": "user", "content": "Can you give a concrete example?"},
        {
            "role": "assistant",
            "content": "Consider a 2x2 covariance matrix with off-diagonal correlation.",
        },
    ]

    append_result = _call_tool(
        server,
        "append_conversation",
        {
            "dataset": "live-chat-test",
            "title": "Eigenvalue decomposition session",
            "messages": messages,
        },
    )
    conv_id = append_result.get("conversation_id")
    assert isinstance(conv_id, int), f"Expected int conv_id; got {append_result}"
    assert append_result.get("message_count") == 6

    # Now search for the unique phrase; should return a hit in this conversation
    search_result = _call_tool(server, "search", {"query": _UNIQUE_PHRASE, "k": 5})
    hits = search_result.get("hits", [])
    assert hits, (
        f"Expected at least one hit for unique phrase {_UNIQUE_PHRASE!r}; got 0. "
        f"conversation_id={conv_id}"
    )

    # At least one hit should be attributable to the appended conversation
    # Hits may come back as conversation-level chunks (message chunks)
    hit_conv_ids = [h.get("conversation_id") for h in hits]
    assert conv_id in hit_conv_ids or any(str(conv_id) in str(h) for h in hits), (
        f"Expected a hit referencing conversation_id={conv_id}; "
        f"hit conversation_ids={hit_conv_ids}, full hits={hits}"
    )


# ---------------------------------------------------------------------------
# Test 2: cross-host visibility (self-distillation loop pin)
# ---------------------------------------------------------------------------


def test_append_conversation_cross_host_visible(pg_dsn: str) -> None:
    """Host A writes; Host B reads — proves PG write-side visibility across connections.

    This is the critical self-distillation loop pin: when Host A (one MCP
    server instance / one PostgresBackend) appends a conversation, Host B
    (a distinct PostgresBackend pointing at the same DSN) must be able to
    find it via search.  This exercises both the PG commit isolation boundary
    and the search_lexical path on a separate connection.
    """
    # Host A: write side
    backend_a = _make_pg_backend(pg_dsn)
    server_a = _build_pg_server(backend_a, writes_enabled=True)

    _ensure_dataset(backend_a, "cross-host-test")

    unique_content = f"cross-host-{_UNIQUE_PHRASE}-omega-7"
    messages = [
        {"role": "user", "content": f"Discussion about {unique_content} patterns."},
        {"role": "assistant", "content": f"The {unique_content} is a key concept here."},
    ]

    append_result = _call_tool(
        server_a,
        "append_conversation",
        {
            "dataset": "cross-host-test",
            "title": "Cross-host visibility test",
            "messages": messages,
        },
    )
    conv_id = append_result.get("conversation_id")
    assert isinstance(conv_id, int), f"Host A append_conversation failed; got {append_result}"

    # Host B: completely independent backend + server pointing at same DSN
    backend_b = PostgresBackend(dsn=pg_dsn, schema="corpus")
    # No migrate() call — schema already exists from Host A
    server_b = _build_pg_server(backend_b, writes_enabled=False)

    search_result = _call_tool(server_b, "search", {"query": unique_content, "k": 5})
    hits = search_result.get("hits", [])

    # CRITICAL assertion: cross-host visibility
    assert hits, (
        f"CROSS-HOST VISIBILITY FAIL: Host B could not find content written by Host A. "
        f"Searched for {unique_content!r}; conv_id={conv_id}; got 0 hits."
    )

    hit_conv_ids = [h.get("conversation_id") for h in hits]
    assert conv_id in hit_conv_ids or any(str(conv_id) in str(h) for h in hits), (
        f"CROSS-HOST VISIBILITY FAIL: hits found but none reference conv_id={conv_id}. "
        f"hit_conv_ids={hit_conv_ids}"
    )
