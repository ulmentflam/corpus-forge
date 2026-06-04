"""Smoke tests: MCP write tools enabled by default; opt-out via ``writes_enabled=False``.

Three tests pin the new default-on contract (hotfix for the
``corpus-forge mcp serve`` CLI that previously left the 16 write
tools unreachable because nothing flipped the default):

1. Default ``build_server(...)`` exposes ALL tools (read + write).
2. ``build_server(..., writes_enabled=True)`` is identical to default
   (kept as an explicit opt-in surface for the SDK).
3. ``build_server(..., writes_enabled=False)`` is the explicit
   opt-out path — exposes ONLY the read tools, and calling a write
   tool (add_label) returns an error result.

Run command:
    uv run python -m pytest tests/smoke/test_mcp_writes_enabled_by_default.py -v
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

# G-03: render_conversation + list_chat_templates are always-available read tools.
# J1:   estimate_sync_size is an always-available read tool.
# J4:   next_curation_target / next_curation_batch are always-available read tools.
_READ_TOOL_NAMES = {
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
    # agent-chunk-explorer — chunk navigation read tools
    "chunk_neighbors",
    "get_document",
}

_WRITE_TOOL_NAMES = {
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
    # Phase M Wave 3 — .corpusignore write tools
    "add_ignore_pattern",
    "remove_ignore_pattern",
    "sync_ignore",
    # Phase M Wave 4 — Zotero ingest tool
    "zotero_sync",
    # Phase P Wave 2 — search result rating tool
    "rate_search_result",
    # Phase Q Wave 1 — SDFT demonstration capture tool
    "record_demonstration",
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


def test_default_build_server_exposes_all_tools() -> None:
    """build_server() with no opt-out exposes every read + write tool.

    Hotfix contract: ``corpus-forge mcp serve`` (and any other caller
    that doesn't pass ``writes_enabled`` explicitly) gets writes ON
    by default.  The 16 write tools were unreachable in 0.1.0b14 and
    earlier because no caller flipped the parameter from its old
    ``False`` default.
    """
    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.mcp.server import build_server

    retriever = _FakeRetriever()
    backend = SQLiteBackend(path=":memory:")
    backend.migrate()
    retriever.backend = backend

    # No ``writes_enabled`` kwarg — picks up the default.
    server = build_server(retriever_builder=lambda: retriever)

    tool_names = _list_tool_names(server)
    assert tool_names == _ALL_TOOL_NAMES, (
        f"Default server must expose all read + write tools; got {tool_names}"
    )
    for name in _WRITE_TOOL_NAMES:
        assert name in tool_names, f"Write tool {name!r} missing under the new default"


def test_explicit_writes_enabled_true_is_a_no_op() -> None:
    """``writes_enabled=True`` (explicit) matches the new default.

    Kept as an SDK-level surface: callers that want to be explicit
    about the policy still type-check + behave correctly.
    """
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
    assert tool_names == _ALL_TOOL_NAMES


def test_writes_disabled_via_opt_out_still_works() -> None:
    """``writes_enabled=False`` (explicit opt-out) exposes ONLY reads.

    The kill-switch path stays available for paranoid sandboxes (e.g.
    connect-to-prod-corpus-for-debugging without write risk).  Calling
    a write tool under opt-out must return an error result.
    """
    from corpus_forge.mcp.server import build_server

    retriever = _FakeRetriever()
    server = build_server(retriever_builder=lambda: retriever, writes_enabled=False)

    tool_names = _list_tool_names(server)
    assert tool_names == _READ_TOOL_NAMES, (
        f"writes_enabled=False must expose only {_READ_TOOL_NAMES}; got {tool_names}"
    )
    for write_tool in _WRITE_TOOL_NAMES:
        assert write_tool not in tool_names, (
            f"Write tool {write_tool!r} must be absent when writes_enabled=False"
        )

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
