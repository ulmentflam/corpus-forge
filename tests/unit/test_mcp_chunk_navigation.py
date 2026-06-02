"""T5 — MCP chunk-navigation tools: ``chunk_neighbors``, ``get_document``
plus ``get_chunk`` enrichment (additive: ``prev_chunk_id``,
``next_chunk_id``, ``abs_path``).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

mcp = pytest.importorskip("mcp")
from mcp import types as mcp_types  # noqa: E402,I001


# ── Async runner + handler shims (mirror tests/unit/test_mcp_server.py) ─


def _run(coro):
    return asyncio.run(coro)


def _list_tools_via_handler(server) -> list[mcp_types.Tool]:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    root = result.root if hasattr(result, "root") else result
    return list(root.tools)


def _call_tool_via_handler(server, name: str, arguments: dict[str, Any]):
    handler = server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = _run(handler(request))
    return result.root if hasattr(result, "root") else result


def _parse_text_content(call_result) -> dict | list:
    """Extract the JSON payload from a CallToolResult's first text block."""
    contents = call_result.content
    assert contents, "expected at least one content block"
    first = contents[0]
    text = getattr(first, "text", None) or ""
    return json.loads(text)


# ── Fake retriever + backend ────────────────────────────────────────────


class _FakeBackend:
    def __init__(self) -> None:
        self.chunks: dict[int, dict] = {}
        self.docs: dict[int, list[dict]] = {}

    def get_chunk(self, chunk_id: int) -> dict | None:
        return self.chunks.get(chunk_id)

    def get_chunk_neighbors(self, chunk_id: int, *, before: int = 1, after: int = 1) -> list[dict]:
        c = self.chunks.get(chunk_id)
        if c is None:
            return []
        doc_id = c.get("document_id")
        if doc_id is None:
            return []
        siblings = sorted(self.docs.get(doc_id, []), key=lambda r: r["chunk_index"])
        anchor_idx = c["chunk_index"]
        out: list[dict] = []
        for r in siblings:
            if r["chunk_index"] == anchor_idx:
                continue
            d = r["chunk_index"] - anchor_idx
            if (d < 0 and -d <= before) or (d > 0 and d <= after):
                out.append(r)
        out.sort(key=lambda r: r["chunk_index"])
        return out

    def get_document_chunks(self, document_id: int) -> list[dict]:
        return sorted(self.docs.get(document_id, []), key=lambda r: r["chunk_index"])

    def list_datasets(self) -> list[dict]:
        return []

    def hydrate_hit_metadata(self, hits):  # pragma: no cover — exercised via get_chunk
        return [{"labels": [], "description": None, "recent_feedback": []} for _ in hits]

    def hydrate_document_metadata(self, doc_ids):  # pragma: no cover — exercised via get_chunk
        return [{"labels": [], "description": None, "recent_feedback": []} for _ in doc_ids]


class _FakeRetriever:
    def __init__(self, backend: _FakeBackend) -> None:
        self.backend = backend

    def search(self, *_a, **_kw):
        return []


def _seed_doc(backend: _FakeBackend, doc_id: int, *, source_uri: str, texts: list[str]) -> None:
    rows: list[dict] = []
    base = doc_id * 100
    for i, t in enumerate(texts):
        rows.append(
            {
                "id": base + i,
                "document_id": doc_id,
                "conversation_id": None,
                "message_id": None,
                "chunk_index": i,
                "text": t,
                "heading": None,
                "role": None,
                "token_count": len(t.split()),
                "metadata": {},
                "content_hash": f"h{base + i}",
                "dataset_id": 1,
                "source_uri": source_uri,
                "title": f"doc-{doc_id}",
            }
        )
    for i, r in enumerate(rows):
        r["prev_chunk_id"] = rows[i - 1]["id"] if i > 0 else None
        r["next_chunk_id"] = rows[i + 1]["id"] if i + 1 < len(rows) else None
        backend.chunks[r["id"]] = r
    backend.docs[doc_id] = rows


# ── Tests ──────────────────────────────────────────────────────────────


class TestNewToolsRegistered:
    def test_chunk_neighbors_and_get_document_tools_listed(self) -> None:
        from corpus_forge.mcp.server import build_server

        backend = _FakeBackend()
        server = build_server(retriever_builder=lambda: _FakeRetriever(backend))
        names = {t.name for t in _list_tools_via_handler(server)}
        assert "chunk_neighbors" in names
        assert "get_document" in names


class TestChunkNeighborsDispatch:
    def test_returns_envelope_with_before_and_after(self) -> None:
        from corpus_forge.mcp.server import build_server

        backend = _FakeBackend()
        _seed_doc(
            backend, 1, source_uri="filesystem://x/a.md", texts=["c0", "c1", "c2", "c3", "c4"]
        )
        server = build_server(retriever_builder=lambda: _FakeRetriever(backend))
        result = _call_tool_via_handler(
            server, "chunk_neighbors", {"chunk_id": 102, "before": 1, "after": 2}
        )
        payload = _parse_text_content(result)
        assert payload["anchor_chunk_id"] == 102
        assert [c["chunk_index"] for c in payload["before"]] == [1]
        assert [c["chunk_index"] for c in payload["after"]] == [3, 4]
        for c in payload["before"] + payload["after"]:
            assert "chunk_id" in c
            assert "text" in c
            assert "source_uri" in c
            # abs_path is None when no Config is loaded; key must be present.
            assert "abs_path" in c

    def test_missing_anchor_returns_empty_envelope(self) -> None:
        from corpus_forge.mcp.server import build_server

        backend = _FakeBackend()
        server = build_server(retriever_builder=lambda: _FakeRetriever(backend))
        result = _call_tool_via_handler(
            server, "chunk_neighbors", {"chunk_id": 99999, "before": 2, "after": 2}
        )
        payload = _parse_text_content(result)
        assert payload["before"] == []
        assert payload["after"] == []


class TestGetDocumentDispatch:
    def test_returns_document_and_chunks(self) -> None:
        from corpus_forge.mcp.server import build_server

        backend = _FakeBackend()
        _seed_doc(backend, 7, source_uri="filesystem://x/y.md", texts=["a", "b", "c"])
        server = build_server(retriever_builder=lambda: _FakeRetriever(backend))
        result = _call_tool_via_handler(server, "get_document", {"document_id": 7})
        payload = _parse_text_content(result)
        assert payload["document"]["id"] == 7
        assert payload["document"]["source_uri"] == "filesystem://x/y.md"
        assert [c["chunk_index"] for c in payload["chunks"]] == [0, 1, 2]

    def test_reassemble_concats_text(self) -> None:
        from corpus_forge.mcp.server import build_server

        backend = _FakeBackend()
        _seed_doc(backend, 8, source_uri="filesystem://x/r.md", texts=["alpha", "bravo", "charlie"])
        server = build_server(retriever_builder=lambda: _FakeRetriever(backend))
        result = _call_tool_via_handler(
            server, "get_document", {"document_id": 8, "reassemble": True}
        )
        payload = _parse_text_content(result)
        assert payload["text"] == "alphabravocharlie"
        assert "chunks" not in payload

    def test_empty_doc_returns_empty_chunks(self) -> None:
        from corpus_forge.mcp.server import build_server

        backend = _FakeBackend()
        server = build_server(retriever_builder=lambda: _FakeRetriever(backend))
        result = _call_tool_via_handler(server, "get_document", {"document_id": 999})
        payload = _parse_text_content(result)
        assert payload["chunks"] == []


class TestGetChunkEnrichment:
    """Existing ``get_chunk`` tool now also includes prev/next/abs_path."""

    def test_get_chunk_includes_prev_next_and_abs_path(self) -> None:
        from corpus_forge.mcp.server import build_server

        backend = _FakeBackend()
        _seed_doc(backend, 1, source_uri="filesystem://x/a.md", texts=["alpha", "bravo", "charlie"])
        server = build_server(retriever_builder=lambda: _FakeRetriever(backend))
        result = _call_tool_via_handler(server, "get_chunk", {"chunk_id": 101})
        payload = _parse_text_content(result)
        # Existing keys preserved.
        assert payload["id"] == 101
        assert payload["text"] == "bravo"
        # New keys.
        assert payload["prev_chunk_id"] == 100
        assert payload["next_chunk_id"] == 102
        assert "abs_path" in payload  # may be None when no Config; key must exist
