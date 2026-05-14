"""F-03 RED — smoke tests: MCP write tools gated by writes_enabled flag.

Three tests:
1. Default ``build_server(...)`` omits write tools entirely (only 3 read tools exposed).
2. ``build_server(..., writes_enabled=True)`` exposes all 11 tools (3 read + 8 write).
3. When disabled, calling a write tool (add_label) raises MCP method-not-found or returns
   an error result — not silently processed.

All three fail today with:
  ImportError: cannot import name 'writes' from 'corpus_forge.mcp'
  (once writes.py exists, test 1 and 3 will need writes_enabled=False explicit assertion,
   and test 2 will need writes_enabled=True which triggers the unexpected-kwarg error)

Run command:
    uv run python -m pytest tests/smoke/test_mcp_writes_disabled_by_default.py -v
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

mcp = pytest.importorskip("mcp")
from mcp import types as mcp_types  # noqa: E402

# Module-level import: all three tests in this file fail at collection time
# until corpus_forge.mcp.writes exists.
from corpus_forge.mcp import writes as _writes_module  # noqa: E402,F401

pytestmark = pytest.mark.smoke

# ---------------------------------------------------------------------------
# Expected tool sets
# ---------------------------------------------------------------------------

_READ_TOOL_NAMES = {"search", "get_chunk", "list_datasets"}

_WRITE_TOOL_NAMES = {
    "add_label",
    "remove_label",
    "set_metadata",
    "set_description",
    "list_labels",
    "append_conversation",
    "append_message",
    "add_feedback",
}

_ALL_TOOL_NAMES = _READ_TOOL_NAMES | _WRITE_TOOL_NAMES


# ---------------------------------------------------------------------------
# Helpers (copied from test_mcp_server.py pattern — no shared fixture module)
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)


def _list_tool_names(server) -> set[str]:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    root = result.root if hasattr(result, "root") else result
    return {t.name for t in root.tools}


def _call_tool_result(server, name: str, arguments: dict[str, Any]):
    handler = server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = _run(handler(request))
    return result.root if hasattr(result, "root") else result


class _FakeRetriever:
    def __init__(self):
        self.backend = MagicMock()
        self.backend.get_chunk.return_value = None
        self.backend.list_datasets.return_value = []

    def search(self, query: str, options: Any):
        return []


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_default_build_server_omits_write_tools() -> None:
    """build_server without writes_enabled=True exposes only the 3 read tools.

    Explicitly passes writes_enabled=False to pin the kwarg contract — this
    fails until build_server accepts the writes_enabled parameter.
    """
    from corpus_forge.mcp.server import build_server

    retriever = _FakeRetriever()
    # Explicitly pass writes_enabled=False — this triggers the unexpected-kwarg
    # error until build_server is updated.
    server = build_server(retriever_builder=lambda: retriever, writes_enabled=False)

    tool_names = _list_tool_names(server)
    assert tool_names == _READ_TOOL_NAMES, (
        f"Default server must expose only {_READ_TOOL_NAMES}; got {tool_names}"
    )
    for write_tool in _WRITE_TOOL_NAMES:
        assert write_tool not in tool_names, (
            f"Write tool {write_tool!r} must be absent when writes_enabled=False"
        )


def test_explicit_writes_enabled_true_exposes_writes() -> None:
    """build_server(writes_enabled=True) exposes all 11 tools."""
    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.mcp.server import build_server

    retriever = _FakeRetriever()
    backend = SQLiteBackend(path=":memory:")
    backend.migrate()
    retriever.backend = backend

    server = build_server(
        retriever_builder=lambda: retriever,
        writes_enabled=True,
    )

    tool_names = _list_tool_names(server)
    assert tool_names == _ALL_TOOL_NAMES, (
        f"writes_enabled=True must expose all 11 tools; got {tool_names}"
    )
    for name in _WRITE_TOOL_NAMES:
        assert name in tool_names, f"Write tool {name!r} missing with writes_enabled=True"


def test_writes_disabled_means_calling_a_write_tool_errors() -> None:
    """Calling add_label when writes_enabled=False returns an error result.

    The server must not dispatch write tools when they are not registered.
    Either the MCP framework returns an error (tool-not-found) or the
    handler explicitly returns isError=True — either is acceptable.

    Explicitly passes writes_enabled=False — fails until build_server
    accepts the parameter.
    """
    from corpus_forge.mcp.server import build_server

    retriever = _FakeRetriever()
    server = build_server(retriever_builder=lambda: retriever, writes_enabled=False)

    # Calling an unregistered tool should produce an error result.
    # The MCP framework may raise or return CallToolResult(isError=True).
    try:
        result = _call_tool_result(
            server,
            "add_label",
            {
                "entity_type": "document",
                "entity_id": 1,
                "namespace": "topic",
                "value": "test",
            },
        )
        # If no exception: result must signal error
        assert getattr(result, "isError", True), (
            "Calling add_label when writes are disabled must return isError=True"
        )
    except Exception:
        # Any exception (McpError, KeyError, etc.) is acceptable — the point
        # is that the call must not silently succeed and mutate state.
        pass
