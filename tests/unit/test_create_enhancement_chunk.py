"""RFC 001 Phase 1 — enhancement-chunk creation.

Covers three layers:

  1. Backend: ``SQLiteBackend.append_enhancement_chunk`` lazily creates the
     per-dataset host document, reuses it, and increments ``chunk_index``.
  2. MCP dispatch: ``writes.create_enhancement_chunk`` stamps lineage
     metadata, honors ``dry_run``, emits an audit row, and raises on an
     unknown dataset.
  3. Server: the ``create_enhancement_chunk`` tool is gated by
     ``writes_enabled``.

Run command:
    uv run pytest tests/unit/test_create_enhancement_chunk.py -v
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest
from mcp import types as mcp_types

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.mcp import writes

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class _MCPContext:
    host: str
    client: str | None
    session_id: str | None


@pytest.fixture
def backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


@pytest.fixture
def ctx() -> _MCPContext:
    return _MCPContext(host="test-host", client="test-client", session_id="sess-001")


@pytest.fixture
def seeded(backend: SQLiteBackend) -> dict[str, Any]:
    """One dataset + one ordinary document/chunk to derive enhancements from."""
    with backend._get_connection() as conn:
        dataset_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("ds-alpha", "text", "unit test dataset"),
        ).fetchone()[0]
        document_id = conn.execute(
            "INSERT INTO documents (dataset_id, source_uri, content_hash, title, text, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (dataset_id, "test://doc/a.md", "hash-a", "Doc A", "Hello world", "{}"),
        ).fetchone()[0]
        chunk_id = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, metadata)"
            " VALUES (?, ?, ?, ?) RETURNING id",
            (document_id, 0, "Hello world chunk", "{}"),
        ).fetchone()[0]
        conn.commit()
    return {
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "dataset_name": "ds-alpha",
    }


def _audit_rows(backend: SQLiteBackend) -> list[Any]:
    with backend._get_connection() as conn:
        return conn.execute("SELECT * FROM mcp_audit ORDER BY id").fetchall()


# ---------------------------------------------------------------------------
# Backend: append_enhancement_chunk
# ---------------------------------------------------------------------------


class TestAppendEnhancementChunk:
    def test_lazily_creates_host_document_and_returns_ids(self, backend, seeded):
        doc_id, chunk_id = backend.append_enhancement_chunk(
            seeded["dataset_id"],
            "corpus-forge://curation/ds-alpha",
            "An improved description of the thing.",
            title="Curation enhancements",
        )
        assert isinstance(doc_id, int)
        assert isinstance(chunk_id, int)

        with backend._get_connection() as conn:
            doc = conn.execute(
                "SELECT source_uri, title FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()
            assert doc["source_uri"] == "corpus-forge://curation/ds-alpha"
            assert doc["title"] == "Curation enhancements"
            ch = conn.execute(
                "SELECT document_id, chunk_index, text FROM chunks WHERE id = ?", (chunk_id,)
            ).fetchone()
            assert ch["document_id"] == doc_id
            assert ch["chunk_index"] == 0
            assert ch["text"] == "An improved description of the thing."

    def test_reuses_host_document_and_increments_chunk_index(self, backend, seeded):
        uri = "corpus-forge://curation/ds-alpha"
        doc_a, chunk_a = backend.append_enhancement_chunk(seeded["dataset_id"], uri, "first")
        doc_b, chunk_b = backend.append_enhancement_chunk(seeded["dataset_id"], uri, "second")

        assert doc_a == doc_b, "second enhancement must reuse the same host document"
        assert chunk_a != chunk_b
        with backend._get_connection() as conn:
            rows = conn.execute(
                "SELECT chunk_index FROM chunks WHERE document_id = ? ORDER BY chunk_index",
                (doc_a,),
            ).fetchall()
        assert [r["chunk_index"] for r in rows] == [0, 1]

    def test_persists_metadata_json(self, backend, seeded):
        _doc_id, chunk_id = backend.append_enhancement_chunk(
            seeded["dataset_id"],
            "corpus-forge://curation/ds-alpha",
            "body",
            metadata={"kind": "curation_enhancement", "derived_from_chunk_id": 7},
        )
        with backend._get_connection() as conn:
            raw = conn.execute("SELECT metadata FROM chunks WHERE id = ?", (chunk_id,)).fetchone()[
                "metadata"
            ]
        meta = json.loads(raw)
        assert meta["kind"] == "curation_enhancement"
        assert meta["derived_from_chunk_id"] == 7


# ---------------------------------------------------------------------------
# Dispatch: writes.create_enhancement_chunk
# ---------------------------------------------------------------------------


class TestCreateEnhancementChunkDispatch:
    def test_real_write_returns_ids_and_stamps_lineage(self, backend, ctx, seeded):
        result = writes.create_enhancement_chunk(
            backend,
            ctx,
            dataset="ds-alpha",
            text="The fix: pass dry_run=true first.",
            derived_from_chunk_id=seeded["chunk_id"],
        )
        assert isinstance(result["chunk_id"], int)
        assert isinstance(result["document_id"], int)
        assert isinstance(result["audit_id"], int)

        chunk = backend.get_chunk(result["chunk_id"])
        meta = chunk["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta["kind"] == "curation_enhancement"
        assert meta["derived_from_chunk_id"] == seeded["chunk_id"]
        assert meta["curation_session_id"] == "sess-001"

    def test_dry_run_writes_nothing_but_audits(self, backend, ctx, seeded):
        result = writes.create_enhancement_chunk(
            backend,
            ctx,
            dataset="ds-alpha",
            text="preview only",
            dry_run=True,
        )
        assert result["chunk_id"] is None
        assert result["document_id"] is None
        assert isinstance(result["audit_id"], int)

        # No host document and no new chunk were created.
        with backend._get_connection() as conn:
            doc = conn.execute(
                "SELECT id FROM documents WHERE source_uri = ?",
                ("corpus-forge://curation/ds-alpha",),
            ).fetchone()
            assert doc is None
        # An audit row marked dry_run was still emitted.
        rows = _audit_rows(backend)
        assert len(rows) == 1

    def test_caller_metadata_merges_over_lineage(self, backend, ctx, seeded):
        result = writes.create_enhancement_chunk(
            backend,
            ctx,
            dataset="ds-alpha",
            text="body",
            metadata={"language": "en"},
        )
        chunk = backend.get_chunk(result["chunk_id"])
        meta = chunk["metadata"]
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta["language"] == "en"
        assert meta["kind"] == "curation_enhancement"

    def test_unknown_dataset_raises(self, backend, ctx):
        with pytest.raises(ValueError, match="not found"):
            writes.create_enhancement_chunk(backend, ctx, dataset="does-not-exist", text="x")


# ---------------------------------------------------------------------------
# Server: writes_enabled gating
# ---------------------------------------------------------------------------


def _list_tool_names(server) -> set[str]:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = asyncio.run(handler(request))
    root = result.root if hasattr(result, "root") else result
    return {t.name for t in root.tools}


class TestServerGating:
    def test_tool_present_when_writes_enabled(self):
        from corpus_forge.mcp.server import build_server

        server = build_server(retriever_builder=object, writes_enabled=True)
        assert "create_enhancement_chunk" in _list_tool_names(server)

    def test_tool_absent_when_writes_disabled(self):
        from corpus_forge.mcp.server import build_server

        server = build_server(retriever_builder=object, writes_enabled=False)
        assert "create_enhancement_chunk" not in _list_tool_names(server)
