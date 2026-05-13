"""R5-03 — corpus-forge MCP server: tool registration + JSON schemas.

Surface under test
------------------

- ``corpus_forge.mcp.server.build_server(...)`` returns a fully-configured
  ``mcp.server.Server`` instance with three tools registered:
  ``search``, ``get_chunk``, ``list_datasets``.
- Each tool advertises a ``Tool`` definition (name + description +
  ``inputSchema`` JSON Schema) discoverable via the server's
  ``request_handlers[ListToolsRequest]`` handler.
- The ``search`` tool dispatches through a retriever returned by the
  caller-supplied builder; the reranker is constructed LAZILY only when
  ``rerank=True`` flows in the call arguments (default-off discipline,
  carried over from R4).
- ``call_tool`` returns a list of ``TextContent`` blocks on success and
  an error CallToolResult on invalid input (jsonschema validation).
- Importing ``corpus_forge.mcp.server`` is side-effect-free apart from
  the third-party ``mcp`` import; no retriever / no reranker is
  constructed at import time.

The tests exercise the request handlers in-process by calling them
directly (no stdio subprocess) — the smoke test in Wave 4 covers the
subprocess transport path.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

mcp = pytest.importorskip("mcp")
from mcp import types as mcp_types  # noqa: E402,I001


# ── Helpers ──────────────────────────────────────────────────────────────


def _run(coro):
    """Synchronously run a coroutine inside a fresh event loop."""
    return asyncio.run(coro)


def _list_tools_via_handler(server) -> list[mcp_types.Tool]:
    """Invoke the registered ListToolsRequest handler and pull out the tools."""
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    # ServerResult wraps a ListToolsResult; unwrap.
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


class _FakeHit:
    """Minimal stand-in for corpus_forge.retrieval.types.Hit."""

    def __init__(self, chunk_id: int, score: float, text: str, dataset_id: int = 1):
        self.chunk_id = chunk_id
        self.score = score
        self.text = text
        self.dataset_id = dataset_id
        self.document_id: int | None = None
        self.conversation_id: int | None = None
        self.message_id: int | None = None
        self.source_uri: str | None = f"fake://chunk-{chunk_id}.md"
        self.title: str | None = f"Fake chunk {chunk_id}"
        self.metadata: dict = {}
        self.source: str = "fused"


class _FakeRetriever:
    """Stand-in that records the SearchOptions it was called with."""

    def __init__(self, hits: list[_FakeHit]):
        self.hits = hits
        self.calls: list[tuple[str, Any]] = []
        self.backend = MagicMock()
        self.backend.get_chunk.side_effect = lambda cid: {
            "id": cid,
            "text": f"chunk text {cid}",
            "content_hash": f"hash-{cid}",
            "dataset_id": 1,
            "source_uri": f"fake://chunk-{cid}.md",
            "title": f"Fake chunk {cid}",
            "metadata": {},
        }
        self.backend.list_datasets.return_value = [
            {
                "name": "ds-a",
                "kind": "text",
                "description": "",
                "document_count": 3,
                "chunk_count": 9,
            },
            {
                "name": "ds-b",
                "kind": "chat",
                "description": "",
                "document_count": 1,
                "chunk_count": 2,
            },
        ]

    def search(self, query: str, options):
        self.calls.append((query, options))
        return list(self.hits)


# ── Tests ────────────────────────────────────────────────────────────────


class TestBuildServer:
    def test_build_server_returns_mcp_server(self) -> None:
        from corpus_forge.mcp.server import build_server

        retriever = _FakeRetriever([_FakeHit(1, 0.9, "alpha")])
        server = build_server(retriever_builder=lambda: retriever)
        assert isinstance(server, mcp.server.Server)
        assert server.name == "corpus-forge"

    def test_build_server_does_not_construct_retriever_eagerly(self) -> None:
        """The retriever builder MUST NOT be invoked at build_server time —
        only on tool dispatch."""
        from corpus_forge.mcp.server import build_server

        builder_called = {"count": 0}

        def builder():
            builder_called["count"] += 1
            return _FakeRetriever([_FakeHit(1, 0.9, "alpha")])

        build_server(retriever_builder=builder)
        assert builder_called["count"] == 0, "build_server must not construct the retriever eagerly"


class TestRegisteredTools:
    def _build(self):
        from corpus_forge.mcp.server import build_server

        retriever = _FakeRetriever([_FakeHit(1, 0.9, "alpha")])
        server = build_server(retriever_builder=lambda: retriever)
        return server, retriever

    def test_three_tools_registered(self) -> None:
        server, _ = self._build()
        tools = _list_tools_via_handler(server)
        names = {t.name for t in tools}
        assert names == {"search", "get_chunk", "list_datasets"}, (
            f"Expected three tools (search/get_chunk/list_datasets); got {names}"
        )

    def test_search_input_schema_has_query_required(self) -> None:
        server, _ = self._build()
        tools = {t.name: t for t in _list_tools_via_handler(server)}
        schema = tools["search"].inputSchema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "query" in props
        assert props["query"]["type"] == "string"
        assert "query" in schema.get("required", []), "search.query must be required"

    def test_search_input_schema_advertises_optional_fields(self) -> None:
        server, _ = self._build()
        tools = {t.name: t for t in _list_tools_via_handler(server)}
        schema = tools["search"].inputSchema
        props = schema["properties"]
        # k / dataset / rerank are advertised as optional knobs.
        for field in ("k", "dataset", "rerank"):
            assert field in props, f"search.inputSchema must advertise {field!r}"
        assert props["k"]["type"] == "integer"
        assert props["dataset"]["type"] == "string"
        assert props["rerank"]["type"] == "boolean"

    def test_get_chunk_input_schema_has_chunk_id_required(self) -> None:
        server, _ = self._build()
        tools = {t.name: t for t in _list_tools_via_handler(server)}
        schema = tools["get_chunk"].inputSchema
        props = schema["properties"]
        assert "chunk_id" in props
        assert props["chunk_id"]["type"] == "integer"
        assert "chunk_id" in schema.get("required", []), "get_chunk.chunk_id must be required"

    def test_list_datasets_input_schema_no_required(self) -> None:
        """list_datasets has no required args; an empty object is valid."""
        server, _ = self._build()
        tools = {t.name: t for t in _list_tools_via_handler(server)}
        schema = tools["list_datasets"].inputSchema
        assert schema["type"] == "object"
        assert not schema.get("required", []), "list_datasets must not have required fields"


class TestSearchDispatch:
    def _build(self):
        from corpus_forge.mcp.server import build_server

        retriever = _FakeRetriever(
            [
                _FakeHit(11, 0.91, "alpha"),
                _FakeHit(22, 0.84, "bravo"),
            ]
        )
        server = build_server(retriever_builder=lambda: retriever)
        return server, retriever

    def test_search_returns_hits(self) -> None:
        server, _retriever = self._build()
        result = _call_tool_via_handler(server, "search", {"query": "alpha"})
        # call_tool returns CallToolResult-like with .content (list[TextContent]).
        assert not result.isError, f"search should not error; got {result}"
        # At least one content block.
        assert result.content, "search must return at least one content block"
        # Structured content shape OR JSON-in-text:
        payload = self._extract_payload(result)
        assert isinstance(payload, dict)
        assert "hits" in payload, f"search payload must include 'hits'; got keys={list(payload)}"
        hits = payload["hits"]
        assert len(hits) == 2
        # Each hit carries the load-bearing fields.
        for h in hits:
            assert "chunk_id" in h
            assert "score" in h
            assert "text" in h

    def test_search_forwards_query_to_retriever(self) -> None:
        server, retriever = self._build()
        _call_tool_via_handler(server, "search", {"query": "tell me about lock_source"})
        assert len(retriever.calls) == 1
        query, opts = retriever.calls[0]
        assert query == "tell me about lock_source"
        # Default k is something sensible (e.g. 10); rerank is OFF by default.
        assert opts.k >= 1
        assert getattr(opts, "rerank", False) is False, (
            "rerank must default to False (R4 default-off discipline)"
        )

    def test_search_forwards_k_override(self) -> None:
        server, retriever = self._build()
        _call_tool_via_handler(server, "search", {"query": "q", "k": 25})
        _, opts = retriever.calls[0]
        assert opts.k == 25

    def test_search_rerank_true_flows_into_options(self) -> None:
        server, retriever = self._build()
        _call_tool_via_handler(server, "search", {"query": "q", "rerank": True})
        _, opts = retriever.calls[0]
        assert getattr(opts, "rerank", False) is True

    def _extract_payload(self, result) -> dict:
        # Prefer structuredContent when present
        if getattr(result, "structuredContent", None):
            return result.structuredContent
        # Else parse the JSON from the first TextContent block
        block = result.content[0]
        return json.loads(block.text)


class TestRerankLaziness:
    """Reranker must only be constructed when rerank=True flows through."""

    def test_rerank_false_does_not_construct_reranker(self) -> None:
        from corpus_forge.mcp.server import build_server

        reranker_calls = {"count": 0}

        def reranker_builder():
            reranker_calls["count"] += 1
            return MagicMock()

        retriever = _FakeRetriever([_FakeHit(1, 0.9, "alpha")])
        server = build_server(
            retriever_builder=lambda: retriever,
            reranker_builder=reranker_builder,
        )
        _call_tool_via_handler(server, "search", {"query": "q", "rerank": False})
        assert reranker_calls["count"] == 0, "Reranker must not be constructed when rerank=False"

    def test_rerank_true_constructs_reranker_once(self) -> None:
        from corpus_forge.mcp.server import build_server

        reranker_calls = {"count": 0}

        def reranker_builder():
            reranker_calls["count"] += 1
            return MagicMock()

        retriever = _FakeRetriever([_FakeHit(1, 0.9, "alpha")])
        server = build_server(
            retriever_builder=lambda: retriever,
            reranker_builder=reranker_builder,
        )
        _call_tool_via_handler(server, "search", {"query": "q", "rerank": True})
        assert reranker_calls["count"] == 1, (
            "Reranker must be constructed exactly once on first rerank=True call"
        )


class TestGetChunkDispatch:
    def _build(self):
        from corpus_forge.mcp.server import build_server

        retriever = _FakeRetriever([])
        server = build_server(retriever_builder=lambda: retriever)
        return server, retriever

    def test_get_chunk_returns_chunk_payload(self) -> None:
        server, retriever = self._build()
        result = _call_tool_via_handler(server, "get_chunk", {"chunk_id": 42})
        assert not result.isError
        payload = (
            result.structuredContent
            if getattr(result, "structuredContent", None)
            else json.loads(result.content[0].text)
        )
        assert payload["id"] == 42
        assert payload["text"] == "chunk text 42"
        # Backend was consulted with the right id.
        retriever.backend.get_chunk.assert_called_once_with(42)

    def test_get_chunk_missing_returns_error(self) -> None:
        """When the chunk_id is unknown, return an error CallToolResult."""
        from corpus_forge.mcp.server import build_server

        retriever = _FakeRetriever([])
        retriever.backend.get_chunk.side_effect = lambda cid: None
        server = build_server(retriever_builder=lambda: retriever)
        result = _call_tool_via_handler(server, "get_chunk", {"chunk_id": 9999})
        assert result.isError, "Unknown chunk_id must surface as CallToolResult.isError=True"


class TestListDatasetsDispatch:
    def _build(self):
        from corpus_forge.mcp.server import build_server

        retriever = _FakeRetriever([])
        server = build_server(retriever_builder=lambda: retriever)
        return server, retriever

    def test_list_datasets_returns_catalogue(self) -> None:
        server, retriever = self._build()
        result = _call_tool_via_handler(server, "list_datasets", {})
        assert not result.isError
        payload = (
            result.structuredContent
            if getattr(result, "structuredContent", None)
            else json.loads(result.content[0].text)
        )
        # Either {"datasets": [...]} or a bare list — accept either shape but
        # require the names to be present.
        datasets = payload["datasets"] if isinstance(payload, dict) else payload
        names = {d["name"] for d in datasets}
        assert names == {"ds-a", "ds-b"}
        retriever.backend.list_datasets.assert_called_once()


class TestInvalidInputHandling:
    def test_missing_required_query_raises_validation_error(self) -> None:
        from corpus_forge.mcp.server import build_server

        retriever = _FakeRetriever([])
        server = build_server(retriever_builder=lambda: retriever)
        result = _call_tool_via_handler(server, "search", {})  # missing 'query'
        assert result.isError, "Missing required 'query' must surface as isError=True"

    def test_wrong_type_on_chunk_id_raises_validation_error(self) -> None:
        from corpus_forge.mcp.server import build_server

        retriever = _FakeRetriever([])
        server = build_server(retriever_builder=lambda: retriever)
        result = _call_tool_via_handler(server, "get_chunk", {"chunk_id": "not-an-int"})
        assert result.isError


class TestImportSurface:
    def test_server_module_import_does_not_construct_retriever(self) -> None:
        """Importing the server module must not construct a Retriever."""
        for k in [k for k in list(sys.modules) if k.startswith("corpus_forge.mcp")]:
            sys.modules.pop(k, None)
        import corpus_forge.mcp.server  # noqa: F401

        # Direct introspection: no global retriever instance exists.
        mod = sys.modules["corpus_forge.mcp.server"]
        assert not hasattr(mod, "_global_retriever"), (
            "Server module must not stash a module-level retriever"
        )
