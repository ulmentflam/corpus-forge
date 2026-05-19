"""Phase M Wave 4 — ``zotero_sync`` MCP tool registration + dispatch.

Mirrors the Wave-3 ignore-tool tests:

- ``writes_enabled=False`` → tool absent from ``list_tools()``.
- ``writes_enabled=True`` → tool present.
- ``dry_run=true`` returns counts without invoking ``ingest_once``.
- ``dry_run=false`` reaches the ingest path and returns
  ``{ingested, skipped, by_mode, audit_id}``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest

mcp = pytest.importorskip("mcp")
from mcp import types as mcp_types  # noqa: E402

from corpus_forge.mcp.server import build_server  # noqa: E402


def _stub_retriever() -> object:
    """Placeholder retriever — zotero_sync doesn't touch the retriever path."""
    return object()


def _run(coro):
    return asyncio.run(coro)


def _list_tools(server) -> list[mcp_types.Tool]:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    root = result.root if hasattr(result, "root") else result
    return list(root.tools)


def _call_tool(server, name: str, arguments: dict[str, Any]):
    handler = server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = _run(handler(request))
    return result.root if hasattr(result, "root") else result


def _structured_or_text(call_result) -> dict[str, Any]:
    """Pull structured content (or JSON text fallback) from a CallToolResult."""
    if call_result.structuredContent:
        return dict(call_result.structuredContent)
    for block in call_result.content or []:
        if isinstance(block, mcp_types.TextContent):
            try:
                return json.loads(block.text)
            except (ValueError, TypeError):
                return {"text": block.text}
    return {}


class TestRegistration:
    def test_tool_absent_when_writes_disabled(self) -> None:
        server = build_server(retriever_builder=_stub_retriever, writes_enabled=False)
        names = {t.name for t in _list_tools(server)}
        assert "zotero_sync" not in names

    def test_tool_present_when_writes_enabled(self) -> None:
        server = build_server(retriever_builder=_stub_retriever, writes_enabled=True)
        tools = {t.name: t for t in _list_tools(server)}
        assert "zotero_sync" in tools
        schema = tools["zotero_sync"].inputSchema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "dataset" in props
        assert "dry_run" in props


class TestDryRun:
    def test_dry_run_short_circuits_without_ingest(self) -> None:
        server = build_server(retriever_builder=_stub_retriever, writes_enabled=True)

        async def _no_ingest(*args, **kwargs):
            raise AssertionError("ingest_once must not run in dry_run mode")

        with (
            patch("corpus_forge.ingest.ingest_once", new=_no_ingest),
            patch(
                "corpus_forge.mcp.server._zotero_dry_run_count",
                return_value={"would_ingest": 3, "by_mode": {"local": 3, "web": 0}},
                create=True,
            ),
        ):
            result = _call_tool(
                server,
                "zotero_sync",
                {"dataset": "anything", "dry_run": True},
            )
        body = _structured_or_text(result)
        assert body.get("would_ingest") == 3
        assert body.get("by_mode") == {"local": 3, "web": 0}


class TestRealSync:
    def test_real_sync_returns_started_plus_audit_id(self) -> None:
        """`zotero_sync` (dry_run=False) returns ``{started, audit_id}``.

        Per-document ingest counts are intentionally NOT surfaced: the
        underlying `ingest_once` doesn't plumb them back, and returning
        fabricated zeros for `ingested`/`skipped`/`by_mode` would mislead
        callers. The audit id is the correlation handle into the log.
        """
        server = build_server(retriever_builder=_stub_retriever, writes_enabled=True)
        fake = {"started": True, "audit_id": "zsync-1"}
        with patch(
            "corpus_forge.mcp.server._zotero_real_sync",
            return_value=fake,
            create=True,
        ):
            result = _call_tool(
                server,
                "zotero_sync",
                {"dataset": "anything", "dry_run": False},
            )
        body = _structured_or_text(result)
        assert body.get("started") is True
        assert body.get("audit_id") == "zsync-1"
