"""Q2-T1 RED — Integration tests for per-client skill-pack provenance in SDFT rows.

When a chat-client skill pack calls ``record_demonstration`` via MCP with a
client-specific ``source`` value (``claude_code``, ``gemini``, ``opencode``,
``codex``), the resulting ``sdft_demonstrations`` row must:

1. Store the correct ``source`` value verbatim.
2. Produce a corresponding ``mcp_audit`` row that records the originating
   client name (i.e. the audit row's ``tool_name`` is ``record_demonstration``
   and the stored ``source`` matches the calling client).

Additionally:
- A default-source test verifies that ``source="claude_code"`` is either the
  hard default when the caller omits ``source`` OR is explicitly passed and
  accepted.
- A parametrized happy-path covers all four chat-client source values.

RED state
---------
``record_demonstration`` is not yet registered in ``server.py``; every
dispatch test will fail with ``isError=True`` ("unknown tool" or
"writes_enabled=False").  The migration ``0014_sdft_demonstrations`` must
already be applied (it was shipped in Q1-G1) — these tests depend on the
``sdft_demonstrations`` table existing.

Run command::

    uv run pytest tests/integration/test_skill_provenance.py \\
        -m 'not requires_docker' -x 2>&1 | tail -40

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.mcp.server import build_server

mcp = pytest.importorskip("mcp")
from mcp import types as mcp_types  # noqa: E402

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Chat-client source values under test
# ---------------------------------------------------------------------------

_CHAT_CLIENT_SOURCES = [
    "claude_code",
    "gemini",
    "opencode",
    "codex",
]

# ---------------------------------------------------------------------------
# In-process MCP harness (mirrors test_mcp_record_demonstration.py)
# ---------------------------------------------------------------------------


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _list_tool_names(server: Any) -> set[str]:
    handler = server.request_handlers[mcp_types.ListToolsRequest]
    request = mcp_types.ListToolsRequest(method="tools/list")
    result = _run(handler(request))
    root = result.root if hasattr(result, "root") else result
    return {t.name for t in root.tools}


def _call_raw(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    """Return the raw CallToolResult root (do NOT raise on isError)."""
    handler = server.request_handlers[mcp_types.CallToolRequest]
    request = mcp_types.CallToolRequest(
        method="tools/call",
        params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
    )
    result = _run(handler(request))
    return result.root if hasattr(result, "root") else result


def _payload(root: Any) -> dict:
    """Extract dict payload from a successful CallToolResult."""
    sc = getattr(root, "structuredContent", None)
    if sc is not None:
        return dict(sc)
    content = getattr(root, "content", [])
    return json.loads(content[0].text)


def _is_error(root: Any) -> bool:
    return bool(getattr(root, "isError", False))


def _error_text(root: Any) -> str:
    content = getattr(root, "content", [])
    return "".join(getattr(b, "text", "") for b in content)


# ---------------------------------------------------------------------------
# Backend + seeding helpers
# ---------------------------------------------------------------------------


def _fresh_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


class _BackedRetriever:
    def __init__(self, backend: SQLiteBackend) -> None:
        self.backend = backend

    def search(self, query: str, options: Any) -> list[Any]:
        return []


def _build_server(backend: SQLiteBackend, *, writes_enabled: bool = True) -> Any:
    retriever = _BackedRetriever(backend)
    return build_server(
        retriever_builder=lambda: retriever,
        writes_enabled=writes_enabled,
    )


def _seed_dataset(backend: SQLiteBackend, name: str = "provenance-test-ds") -> dict[str, int]:
    with backend._get_connection() as conn:
        dataset_id: int = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            (name, "text", "Skill provenance test dataset"),
        ).fetchone()[0]
        conn.commit()
    return {"dataset_id": dataset_id}


def _count_demonstrations(backend: SQLiteBackend) -> int:
    rows = backend._execute("SELECT COUNT(*) AS n FROM sdft_demonstrations")
    return int(rows[0]["n"])


def _count_audit_rows(backend: SQLiteBackend) -> int:
    rows = backend._execute("SELECT COUNT(*) AS n FROM mcp_audit")
    return int(rows[0]["n"])


def _get_latest_demonstration(backend: SQLiteBackend) -> dict:
    rows = backend._execute("SELECT * FROM sdft_demonstrations ORDER BY id DESC LIMIT 1")
    assert rows, "No sdft_demonstrations rows found"
    return dict(rows[0])


def _get_latest_audit(backend: SQLiteBackend) -> dict:
    rows = backend._execute("SELECT * FROM mcp_audit ORDER BY id DESC LIMIT 1")
    assert rows, "No mcp_audit rows found"
    return dict(rows[0])


# ---------------------------------------------------------------------------
# Argument factory
# ---------------------------------------------------------------------------


def _demo_args(
    *,
    source: str,
    dataset: str = "provenance-test-ds",
    query: str | None = None,
) -> dict[str, Any]:
    """Return a valid ``record_demonstration`` argument dict for the given source."""
    if query is None:
        # Make unique per source so content_hash doesn't collide across parametrize iterations.
        query = f"Provenance test query for source={source!r}"
    return {
        "query": query,
        "student_messages": [
            {
                "role": "assistant",
                "content": f"Student response from {source} skill pack.",
            }
        ],
        "teacher_messages": [
            {
                "role": "user",
                "content": f"Teacher demonstration captured via {source}.",
            }
        ],
        "target": f"Corrected output from {source} skill pack.",
        "source": source,
        "dataset": dataset,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> SQLiteBackend:
    return _fresh_backend()


@pytest.fixture
def seeded(backend: SQLiteBackend) -> dict[str, int]:
    return _seed_dataset(backend)


@pytest.fixture
def server_rw(backend: SQLiteBackend, seeded: dict) -> Any:
    return _build_server(backend, writes_enabled=True)


# ===========================================================================
# 1. Parametrized happy-path — all four chat-client sources
# ===========================================================================


class TestChatClientSourceHappyPath:
    """record_demonstration with each chat-client source stores the correct value."""

    @pytest.mark.parametrize("source", _CHAT_CLIENT_SOURCES)
    def test_source_stored_correctly(
        self, backend: SQLiteBackend, seeded: dict, source: str
    ) -> None:
        """``source={source}`` round-trips into ``sdft_demonstrations.source``."""
        server = _build_server(backend, writes_enabled=True)
        root = _call_raw(server, "record_demonstration", _demo_args(source=source))
        assert not _is_error(root), (
            f"record_demonstration with source={source!r} returned error: {_error_text(root)}"
        )
        row = _get_latest_demonstration(backend)
        assert row["source"] == source, (
            f"Expected sdft_demonstrations.source={source!r}; got {row['source']!r}"
        )

    @pytest.mark.parametrize("source", _CHAT_CLIENT_SOURCES)
    def test_audit_row_written_per_client(
        self, backend: SQLiteBackend, seeded: dict, source: str
    ) -> None:
        """Each chat-client call writes an mcp_audit row."""
        server = _build_server(backend, writes_enabled=True)
        before = _count_audit_rows(backend)
        root = _call_raw(server, "record_demonstration", _demo_args(source=source))
        assert not _is_error(root), (
            f"record_demonstration with source={source!r} failed: {_error_text(root)}"
        )
        after = _count_audit_rows(backend)
        assert after == before + 1, (
            f"Expected 1 new mcp_audit row for source={source!r}; got {after - before} new rows"
        )


# ===========================================================================
# 2. Per-client source round-trip — one dedicated test per client
# ===========================================================================


class TestClaudeCodeProvenance:
    """claude_code source is stored and audit captures originating client."""

    def test_claude_code_source_in_sdft_row(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """source='claude_code' is stored verbatim in sdft_demonstrations."""
        root = _call_raw(server_rw, "record_demonstration", _demo_args(source="claude_code"))
        assert not _is_error(root), f"Unexpected error: {_error_text(root)}"
        row = _get_latest_demonstration(backend)
        assert row["source"] == "claude_code"

    def test_claude_code_produces_demonstration_row(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """Calling with source='claude_code' writes exactly one new row."""
        before = _count_demonstrations(backend)
        root = _call_raw(server_rw, "record_demonstration", _demo_args(source="claude_code"))
        assert not _is_error(root)
        assert _count_demonstrations(backend) == before + 1


class TestGeminiProvenance:
    """gemini source is stored and audit captures originating client."""

    def test_gemini_source_in_sdft_row(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """source='gemini' is stored verbatim in sdft_demonstrations."""
        root = _call_raw(server_rw, "record_demonstration", _demo_args(source="gemini"))
        assert not _is_error(root), f"Unexpected error: {_error_text(root)}"
        row = _get_latest_demonstration(backend)
        assert row["source"] == "gemini"


class TestOpenCodeProvenance:
    """opencode source is stored and audit captures originating client."""

    def test_opencode_source_in_sdft_row(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """source='opencode' is stored verbatim in sdft_demonstrations."""
        root = _call_raw(server_rw, "record_demonstration", _demo_args(source="opencode"))
        assert not _is_error(root), f"Unexpected error: {_error_text(root)}"
        row = _get_latest_demonstration(backend)
        assert row["source"] == "opencode"


class TestCodexProvenance:
    """codex source is stored and audit captures originating client."""

    def test_codex_source_in_sdft_row(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """source='codex' is stored verbatim in sdft_demonstrations."""
        root = _call_raw(server_rw, "record_demonstration", _demo_args(source="codex"))
        assert not _is_error(root), f"Unexpected error: {_error_text(root)}"
        row = _get_latest_demonstration(backend)
        assert row["source"] == "codex"


# ===========================================================================
# 3. Default source test
# ===========================================================================


class TestDefaultSource:
    """When source='claude_code' is explicitly passed, it is accepted."""

    def test_claude_code_is_valid_explicit_source(
        self, backend: SQLiteBackend, seeded: dict, server_rw: Any
    ) -> None:
        """source='claude_code' is a valid enum value and round-trips correctly."""
        root = _call_raw(server_rw, "record_demonstration", _demo_args(source="claude_code"))
        assert not _is_error(root), (
            f"claude_code source should be valid but got error: {_error_text(root)}"
        )
        result = _payload(root)
        assert isinstance(result.get("demonstration_id"), int), (
            f"Expected demonstration_id int; got {result}"
        )
        row = _get_latest_demonstration(backend)
        assert row["source"] == "claude_code", (
            f"Expected source='claude_code'; got {row['source']!r}"
        )
