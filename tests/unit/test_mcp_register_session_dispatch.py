"""H-02 RED — MCP register_session dispatch unit tests.

Tests the ``register_session`` dispatch function that H-02 will add to
``corpus_forge/mcp/writes.py``.

Pinned dispatch signature (Coder must match exactly):

    register_session(
        backend, ctx,
        client: str,
        session_id: str,
        *,
        host: str | None = None,
    ) -> dict
    # {"feedback_session_id": int, "created": bool}

WRITE tool (gated by writes_enabled in server.py).

Each test uses a real ``SQLiteBackend(":memory:")`` (migrated, includes
0008_feedback_sessions) and a minimal ``_MCPContext`` dataclass.

Run command:
    .venv/bin/python -m pytest tests/unit/test_mcp_register_session_dispatch.py -v

pytestmark: pytest.mark.unit
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.mcp import writes

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _MCPContext — duck-type compatible with WriteContext
# ---------------------------------------------------------------------------


@dataclass
class _MCPContext:
    """Minimal context carrying MCP caller identity."""

    host: str
    client: str | None
    session_id: str | None


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> SQLiteBackend:
    """Fresh migrated in-memory SQLiteBackend for each test."""
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


@pytest.fixture
def ctx() -> _MCPContext:
    return _MCPContext(host="mcp-server", client="cursor-ext", session_id="sess-reg-01")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRegisterSessionDispatch:
    def test_creates_session_row_and_returns_dict(
        self, backend: SQLiteBackend, ctx: _MCPContext
    ) -> None:
        """register_session creates a feedback_sessions row.

        Returns {feedback_session_id: int, created: True} on first call.
        """
        result = writes.register_session(
            backend,
            ctx,
            client="cursor",
            session_id="s-001",
        )
        assert isinstance(result, dict)
        assert "feedback_session_id" in result
        assert isinstance(result["feedback_session_id"], int)
        assert result["feedback_session_id"] > 0
        assert result["created"] is True

        # Row must exist in the table
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM feedback_sessions WHERE id = ?",
                (result["feedback_session_id"],),
            ).fetchone()
        assert row is not None
        assert row["client"] == "cursor"
        assert row["session_id"] == "s-001"

    def test_duplicate_returns_existing_id_and_created_false(
        self, backend: SQLiteBackend, ctx: _MCPContext
    ) -> None:
        """Second call with same (client, session_id) returns same id + created=False."""
        r1 = writes.register_session(backend, ctx, client="cursor", session_id="s-dup")
        r2 = writes.register_session(backend, ctx, client="cursor", session_id="s-dup")

        assert r1["feedback_session_id"] == r2["feedback_session_id"]
        assert r2["created"] is False

        # Only one row in the table
        with backend._get_connection() as conn:
            count = conn.execute("SELECT COUNT(*) FROM feedback_sessions").fetchone()[0]
        assert count == 1

    def test_explicit_host_overrides_ctx_host(
        self, backend: SQLiteBackend, ctx: _MCPContext
    ) -> None:
        """When host kwarg is supplied, it is stored instead of ctx.host."""
        result = writes.register_session(
            backend,
            ctx,
            client="cursor",
            session_id="s-explicit-host",
            host="override-host",
        )
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT host FROM feedback_sessions WHERE id = ?",
                (result["feedback_session_id"],),
            ).fetchone()
        assert row["host"] == "override-host"
