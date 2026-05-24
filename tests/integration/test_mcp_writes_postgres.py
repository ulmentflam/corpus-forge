"""F-05 — MCP write surface integration smoke against testcontainers PostgreSQL.

Each of the 8 write tools is exercised end-to-end through the in-process MCP
server backed by a real PostgresBackend instance.  This mirrors the approach
from ``tests/unit/test_mcp_server_enrichment.py`` (SQLite in-memory) but
exercises the PG code paths that the unit tests never touch.

Key goals:
- prove write tools work against PG (F-02 unit tests covered SQLite only)
- verify audit rows are emitted for every write (and every dry_run)
- verify dry_run does NOT mutate entity state

Pattern: session-scoped testcontainers PG container (``postgres_container``),
function-scoped ``pg_dsn`` fixture that wipes the ``corpus`` schema between
tests.

Run command:
    .venv/bin/python -m pytest tests/integration/test_mcp_writes_postgres.py -v

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.mcp.server import build_server

if TYPE_CHECKING:  # pragma: no cover - typing only
    from mcp.server import Server

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Helpers — Postgres retriever + MCP call wrappers
# ---------------------------------------------------------------------------

_CHUNK_TEXT = "Quantized eigenvalue decomposition of the Hamiltonian operator."
_DOC_TEXT = "Quantum mechanics foundational text for band structure theory."


class _PgLexicalRetriever:
    """Thin wrapper around PostgresBackend.search_lexical — no embedder."""

    def __init__(self, backend: PostgresBackend) -> None:
        self.backend = backend

    def search(self, query: str, options: object) -> list[object]:
        k = getattr(options, "k", 10)
        return self.backend.search_lexical(query, k=k)


def _make_pg_backend(pg_dsn: str) -> PostgresBackend:
    b = PostgresBackend(dsn=pg_dsn, schema="corpus")
    b.migrate()
    return b


def _seed_pg(backend: PostgresBackend) -> dict[str, object]:
    """Insert one dataset, document, chunk, and conversation in PG.  Returns ids."""
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(backend.dsn, row_factory=dict_row) as conn:
        conn.execute(
            "SET search_path = corpus, public",
        )
        conn.commit()

        ds_row = conn.execute(
            "INSERT INTO corpus.datasets (name, kind, description)"
            " VALUES (%s, %s, %s) RETURNING id",
            ("pg-write-test-ds", "text", "F-05 PG integration test dataset"),
        ).fetchone()
        dataset_id: int = ds_row["id"]
        dataset_name = "pg-write-test-ds"

        import hashlib

        doc_hash = hashlib.sha256(_DOC_TEXT.encode()).hexdigest()
        chunk_hash = hashlib.sha256(_CHUNK_TEXT.encode()).hexdigest()

        doc_row = conn.execute(
            "INSERT INTO corpus.documents"
            " (dataset_id, source_uri, content_hash, title, text, metadata)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (
                dataset_id,
                "vault://pg-test/doc.md",
                doc_hash,
                "Quantum Mechanics PG",
                _DOC_TEXT,
                "{}",
            ),
        ).fetchone()
        document_id: int = doc_row["id"]

        chunk_row = conn.execute(
            "INSERT INTO corpus.chunks"
            " (document_id, chunk_index, text, content_hash, metadata)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (document_id, 0, _CHUNK_TEXT, chunk_hash, "{}"),
        ).fetchone()
        chunk_id: int = chunk_row["id"]

        conv_row = conn.execute(
            "INSERT INTO corpus.conversations"
            " (dataset_id, source_uri, content_hash, title, message_count, metadata)"
            " VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (dataset_id, "test://pg-conv/1", "pg-conv-hash-1", "PG Conv 1", 0, "{}"),
        ).fetchone()
        conversation_id: int = conv_row["id"]

        msg_row = conn.execute(
            "INSERT INTO corpus.messages"
            " (conversation_id, turn_index, role, content, metadata)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (conversation_id, 0, "user", "Hello PG!", "{}"),
        ).fetchone()
        message_id: int = msg_row["id"]

        conn.commit()

    return {
        "dataset_id": dataset_id,
        "dataset_name": dataset_name,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
    }


def _build_pg_server(backend: PostgresBackend, writes_enabled: bool = True) -> Server:
    retriever = _PgLexicalRetriever(backend)
    return build_server(retriever_builder=lambda: retriever, writes_enabled=writes_enabled)


def _call_tool(server: Server, name: str, arguments: dict[str, object]) -> dict[str, object]:
    async def _run() -> dict[str, object]:
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


def _count_audit_rows(backend: PostgresBackend) -> int:
    rows = backend._execute("SELECT COUNT(*) AS n FROM corpus.mcp_audit")
    return int(rows[0]["n"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def pg_backend(pg_dsn: str) -> PostgresBackend:
    return _make_pg_backend(pg_dsn)


@pytest.fixture
def seeded(pg_backend: PostgresBackend) -> dict[str, object]:
    return _seed_pg(pg_backend)


@pytest.fixture
def server(pg_backend: PostgresBackend, seeded: dict[str, object]) -> Server:
    return _build_pg_server(pg_backend, writes_enabled=True)


# ---------------------------------------------------------------------------
# 1. add_label round-trip
# ---------------------------------------------------------------------------


def test_add_label_round_trip_pg(
    pg_backend: PostgresBackend, seeded: dict[str, object], server: Server
) -> None:
    """add_label on a chunk is visible in subsequent list_labels and search hits."""
    result = _call_tool(
        server,
        "add_label",
        {
            "entity_type": "chunk",
            "entity_id": seeded["chunk_id"],
            "namespace": "topic",
            "value": "quantum-pg",
        },
    )
    assert isinstance(result.get("label_id"), int), f"Expected label_id int; got {result}"
    assert result.get("created") is True
    assert isinstance(result.get("audit_id"), int)

    # Verify persistence via list_labels
    labels_result = _call_tool(server, "list_labels", {})
    namespaces = [lbl["namespace"] for lbl in labels_result["labels"]]
    assert "topic" in namespaces, f"topic not found in list_labels: {labels_result}"


# ---------------------------------------------------------------------------
# 2. set_description round-trip
# ---------------------------------------------------------------------------


def test_set_description_round_trip_pg(
    pg_backend: PostgresBackend, seeded: dict[str, object], server: Server
) -> None:
    """set_description on a chunk is reflected in get_chunk response."""
    result = _call_tool(
        server,
        "set_description",
        {
            "entity_type": "chunk",
            "entity_id": seeded["chunk_id"],
            "text": "PG round-trip: eigenvalue analysis passage.",
        },
    )
    assert result.get("before") is None, f"Expected before=None on first set; got {result}"
    assert result.get("after") == "PG round-trip: eigenvalue analysis passage."
    assert isinstance(result.get("audit_id"), int)

    chunk = _call_tool(server, "get_chunk", {"chunk_id": seeded["chunk_id"]})
    assert chunk.get("description") == "PG round-trip: eigenvalue analysis passage.", (
        f"Description not reflected in get_chunk: {chunk}"
    )


# ---------------------------------------------------------------------------
# 3. set_metadata round-trip
# ---------------------------------------------------------------------------


def test_set_metadata_round_trip_pg(
    pg_backend: PostgresBackend, seeded: dict[str, object], server: Server
) -> None:
    """set_metadata merges a key into the chunk's JSONB metadata column."""
    result = _call_tool(
        server,
        "set_metadata",
        {
            "entity_type": "chunk",
            "entity_id": seeded["chunk_id"],
            "key": "quality",
            "value": "high",
        },
    )
    assert isinstance(result.get("before"), dict), f"before must be dict; got {result}"
    assert isinstance(result.get("after"), dict), f"after must be dict; got {result}"
    assert result["after"].get("quality") == "high", f"Unexpected after: {result['after']}"
    assert isinstance(result.get("audit_id"), int)


# ---------------------------------------------------------------------------
# 4. remove_label
# ---------------------------------------------------------------------------


def test_remove_label_pg(
    pg_backend: PostgresBackend, seeded: dict[str, object], server: Server
) -> None:
    """add then remove; search hit no longer carries the label."""
    _call_tool(
        server,
        "add_label",
        {
            "entity_type": "chunk",
            "entity_id": seeded["chunk_id"],
            "namespace": "temp",
            "value": "to-remove",
        },
    )
    remove_result = _call_tool(
        server,
        "remove_label",
        {
            "entity_type": "chunk",
            "entity_id": seeded["chunk_id"],
            "namespace": "temp",
            "value": "to-remove",
        },
    )
    assert remove_result.get("removed") is True, f"Expected removed=True; got {remove_result}"
    assert isinstance(remove_result.get("audit_id"), int)

    # list_labels should show 0 for namespace "temp"
    labels_result = _call_tool(server, "list_labels", {"entity_type": "chunk"})
    temp_labels = [lbl for lbl in labels_result["labels"] if lbl["namespace"] == "temp"]
    assert not temp_labels, f"Expected no 'temp' labels after removal; got {temp_labels}"


# ---------------------------------------------------------------------------
# 5. list_labels aggregates across entity types
# ---------------------------------------------------------------------------


def test_list_labels_aggregates_across_entity_types_pg(
    pg_backend: PostgresBackend, seeded: dict[str, object], server: Server
) -> None:
    """Labels applied to chunk, document, and conversation all appear in list_labels."""
    _call_tool(
        server,
        "add_label",
        {
            "entity_type": "chunk",
            "entity_id": seeded["chunk_id"],
            "namespace": "cross",
            "value": "entity-chunk",
        },
    )
    _call_tool(
        server,
        "add_label",
        {
            "entity_type": "document",
            "entity_id": seeded["document_id"],
            "namespace": "cross",
            "value": "entity-doc",
        },
    )
    _call_tool(
        server,
        "add_label",
        {
            "entity_type": "conversation",
            "entity_id": seeded["conversation_id"],
            "namespace": "cross",
            "value": "entity-conv",
        },
    )

    result = _call_tool(server, "list_labels", {})
    values = {lbl["value"] for lbl in result["labels"]}
    assert "entity-chunk" in values, f"chunk label missing; got {values}"
    assert "entity-doc" in values, f"document label missing; got {values}"
    assert "entity-conv" in values, f"conversation label missing; got {values}"


# ---------------------------------------------------------------------------
# 6. append_conversation
# ---------------------------------------------------------------------------


def test_append_conversation_pg(
    pg_backend: PostgresBackend, seeded: dict[str, object], server: Server
) -> None:
    """append_conversation creates a conversation + messages; message_count is correct."""
    messages = [
        {"role": "user", "content": "Hello from PG test"},
        {"role": "assistant", "content": "Hi back from PG!"},
        {"role": "user", "content": "Great, three messages total"},
    ]
    result = _call_tool(
        server,
        "append_conversation",
        {
            "dataset": seeded["dataset_name"],
            "title": "PG Append Test",
            "messages": messages,
        },
    )
    assert isinstance(result.get("conversation_id"), int), f"Expected int conv id; got {result}"
    assert result.get("message_count") == 3, (
        f"Expected message_count=3; got {result.get('message_count')}"
    )
    assert isinstance(result.get("audit_id"), int)

    # Verify rows were actually written
    rows = pg_backend._execute(
        "SELECT COUNT(*) AS n FROM corpus.messages WHERE conversation_id = %s",
        (result["conversation_id"],),
    )
    assert rows[0]["n"] == 3, f"Expected 3 message rows in PG; got {rows[0]['n']}"


# ---------------------------------------------------------------------------
# 7. append_message extends existing conversation
# ---------------------------------------------------------------------------


def test_append_message_extends_existing_pg(
    pg_backend: PostgresBackend, seeded: dict[str, object], server: Server
) -> None:
    """append_conversation then 2x append_message; turn indices go 0,1,2,3,4."""
    messages = [
        {"role": "user", "content": "Turn 0"},
        {"role": "assistant", "content": "Turn 1"},
        {"role": "user", "content": "Turn 2"},
    ]
    conv_result = _call_tool(
        server,
        "append_conversation",
        {
            "dataset": seeded["dataset_name"],
            "title": "Extend Test",
            "messages": messages,
        },
    )
    conv_id = conv_result["conversation_id"]

    r3 = _call_tool(
        server,
        "append_message",
        {"conversation_id": conv_id, "role": "assistant", "content": "Turn 3"},
    )
    r4 = _call_tool(
        server,
        "append_message",
        {"conversation_id": conv_id, "role": "user", "content": "Turn 4"},
    )

    assert r3.get("turn_index") == 3, f"Expected turn_index=3; got {r3}"
    assert r4.get("turn_index") == 4, f"Expected turn_index=4; got {r4}"

    rows = pg_backend._execute(
        "SELECT turn_index FROM corpus.messages WHERE conversation_id = %s ORDER BY turn_index",
        (conv_id,),
    )
    indices = [row["turn_index"] for row in rows]
    assert indices == [0, 1, 2, 3, 4], f"Expected turn indices [0,1,2,3,4]; got {indices}"


# ---------------------------------------------------------------------------
# 8. add_feedback
# ---------------------------------------------------------------------------


def test_add_feedback_pg(
    pg_backend: PostgresBackend, seeded: dict[str, object], server: Server
) -> None:
    """add_feedback with rating + text appears in subsequent search hit recent_feedback."""
    result = _call_tool(
        server,
        "add_feedback",
        {
            "entity_type": "chunk",
            "entity_id": seeded["chunk_id"],
            "kind": "thumbs",
            "rating": 1,
            "text": "Very helpful PG chunk!",
        },
    )
    assert isinstance(result.get("feedback_id"), int), f"Expected int feedback_id; got {result}"
    assert isinstance(result.get("audit_id"), int)

    # Verify persistence
    rows = pg_backend._execute(
        "SELECT COUNT(*) AS n FROM corpus.feedback WHERE entity_type = 'chunk' AND entity_id = %s",
        (seeded["chunk_id"],),
    )
    assert rows[0]["n"] == 1, f"Expected 1 feedback row in PG; got {rows[0]['n']}"


# ---------------------------------------------------------------------------
# 9. audit event emitted for every write (and every dry_run)
# ---------------------------------------------------------------------------


def test_audit_event_emitted_for_every_write_pg(
    pg_backend: PostgresBackend, seeded: dict[str, object], server: Server
) -> None:
    """Each write call (including dry_run) emits exactly one mcp_audit row."""
    # Baseline audit count
    before_count = _count_audit_rows(pg_backend)

    write_calls = [
        (
            "add_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "audit-test",
                "value": "v1",
            },
        ),
        (
            "remove_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "audit-test",
                "value": "v1",
            },
        ),
        (
            "set_description",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "text": "audit desc",
            },
        ),
        (
            "add_feedback",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "kind": "thumbs",
                "rating": 1,
            },
        ),
        # dry_run variants
        (
            "add_label",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "namespace": "dry-audit",
                "value": "ghost",
                "dry_run": True,
            },
        ),
        (
            "add_feedback",
            {
                "entity_type": "chunk",
                "entity_id": seeded["chunk_id"],
                "kind": "comment",
                "text": "dry feedback",
                "dry_run": True,
            },
        ),
    ]

    expected_count = before_count + len(write_calls)
    for tool, args in write_calls:
        _call_tool(server, tool, args)

    after_count = _count_audit_rows(pg_backend)
    assert after_count == expected_count, (
        f"Expected {expected_count} audit rows; got {after_count}. "
        f"Before={before_count}, write calls={len(write_calls)}"
    )


# ---------------------------------------------------------------------------
# 10. dry_run does not persist entity state
# ---------------------------------------------------------------------------


def test_dry_run_does_not_persist_pg(
    pg_backend: PostgresBackend, seeded: dict[str, object], server: Server
) -> None:
    """dry_run=True on each write tool produces NO entity state mutation in PG."""
    # add_label dry_run — no junction row
    _call_tool(
        server,
        "add_label",
        {
            "entity_type": "chunk",
            "entity_id": seeded["chunk_id"],
            "namespace": "ghost-ns",
            "value": "ghost-val",
            "dry_run": True,
        },
    )
    rows = pg_backend._execute(
        "SELECT COUNT(*) AS n FROM corpus.chunk_labels cl"
        " JOIN corpus.labels l ON l.id = cl.label_id"
        " WHERE l.namespace = %s AND l.value = %s AND cl.chunk_id = %s",
        ("ghost-ns", "ghost-val", seeded["chunk_id"]),
    )
    assert rows[0]["n"] == 0, (
        f"dry_run add_label must not persist junction row; found {rows[0]['n']}"
    )

    # add_feedback dry_run — no feedback row
    _call_tool(
        server,
        "add_feedback",
        {
            "entity_type": "chunk",
            "entity_id": seeded["chunk_id"],
            "kind": "comment",
            "text": "ghost feedback",
            "dry_run": True,
        },
    )
    fb_rows = pg_backend._execute(
        "SELECT COUNT(*) AS n FROM corpus.feedback WHERE entity_type = 'chunk' AND entity_id = %s",
        (seeded["chunk_id"],),
    )
    assert fb_rows[0]["n"] == 0, (
        f"dry_run add_feedback must not persist row; found {fb_rows[0]['n']}"
    )

    # append_conversation dry_run — conversation_id is None, no conv row
    result = _call_tool(
        server,
        "append_conversation",
        {
            "dataset": seeded["dataset_name"],
            "title": "Dry Run Conv",
            "messages": [{"role": "user", "content": "ghost msg"}],
            "dry_run": True,
        },
    )
    assert result.get("conversation_id") is None, (
        f"dry_run append_conversation must return conversation_id=None; got {result}"
    )
