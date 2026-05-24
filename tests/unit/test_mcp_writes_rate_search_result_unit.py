"""Unit tests for ``corpus_forge.mcp.writes.rate_search_result``.

Covers the validation guards + the session auto-create branch that aren't
hit by the existing integration suite when a query_id matches an existing
session row.
"""

from __future__ import annotations

import pytest

from corpus_forge.mcp.writes import _q, rate_search_result


class _Ctx:
    host = "test-host"
    client = "claude_code"
    session_id = "test-session"


class _FakeBackend:
    """Stub backend with scripted ``_execute`` responses + ``audit_event`` stub."""

    def __init__(
        self,
        *,
        responses: list[list[dict] | None] | None = None,
        audit_returns: int = 42,
    ) -> None:
        # responses is consumed in order: chunk_rows, session_rows,
        # (doc_rows, new_session_rows, event_rows) depending on which branch
        # the function takes.
        self._responses: list[list[dict] | None] = list(responses or [])
        self._audit_returns = audit_returns
        self.executes: list[tuple[str, tuple]] = []
        self.audit_calls: list[tuple[object, ...]] = []
        # `_q` uses type(backend).__module__ to decide placeholder style;
        # we live in this test module, so placeholders stay as `?`.

    def _execute(self, sql: str, params: tuple) -> list[dict]:
        self.executes.append((sql, params))
        if not self._responses:
            return []
        return self._responses.pop(0) or []

    def audit_event(self, *args: object) -> int:
        self.audit_calls.append(args)
        return self._audit_returns


def test_q_replaces_question_marks_for_postgres_module() -> None:
    """_q translates ? → %s when backend's module name contains 'postgres'."""

    class _PgBackend:
        pass

    _PgBackend.__module__ = "corpus_forge.backends.postgres"
    assert _q(_PgBackend(), "SELECT ? FROM x WHERE y = ?") == "SELECT %s FROM x WHERE y = %s"


def test_q_keeps_question_marks_for_sqlite_module() -> None:
    """_q is a no-op for non-postgres backends."""

    class _SqliteBackend:
        pass

    _SqliteBackend.__module__ = "corpus_forge.backends.sqlite"
    assert _q(_SqliteBackend(), "SELECT ? FROM x") == "SELECT ? FROM x"


def test_rate_search_result_missing_chunk_raises() -> None:
    """Empty chunk_rows from the existence check raises ValueError."""
    backend = _FakeBackend(responses=[[]])  # First call → no chunk row.
    with pytest.raises(ValueError, match="does not exist"):
        rate_search_result(
            backend,
            _Ctx(),
            query_id="q1",
            chunk_id=999,
            signal="relevance",
            value=1.0,
            source="human",
        )


def test_rate_search_result_existing_session_reused() -> None:
    """When the query_id matches an existing session row, that id is reused."""
    backend = _FakeBackend(
        responses=[
            [{"id": 5}],  # chunk_rows
            [{"id": 77}],  # existing session_rows
            [{"id": 1001}],  # event_rows insert
        ]
    )
    out = rate_search_result(
        backend,
        _Ctx(),
        query_id="reuse-me",
        chunk_id=5,
        signal="thumbs_up",
        value=None,
        source="human",
    )
    assert out == {"event_id": 1001, "session_id": 77}


def test_rate_search_result_auto_creates_session_for_unknown_query_id() -> None:
    """No existing session → resolve dataset via chunk → document and INSERT."""
    backend = _FakeBackend(
        responses=[
            [{"id": 5}],  # chunk_rows
            [],  # session_rows empty → auto-create branch
            [{"dataset_id": 99}],  # doc_rows
            [{"id": 200}],  # new_session_rows
            [{"id": 1234}],  # event_rows
        ]
    )
    out = rate_search_result(
        backend,
        _Ctx(),
        query_id="new-q",
        chunk_id=5,
        signal="thumbs_down",
        value=0.0,
        source="cli_feedback",
        replacement_chunk_id=7,
    )
    assert out == {"event_id": 1234, "session_id": 200}
    # The INSERT used query_id (not "(retroactive)"). Inspect the executed SQLs.
    new_session_sql = backend.executes[3][0]
    new_session_params = backend.executes[3][1]
    assert "INSERT INTO search_sessions" in new_session_sql
    assert new_session_params[0] == "new-q"
    assert new_session_params[1] == 99


def test_rate_search_result_auto_create_when_chunk_doc_missing() -> None:
    """If the chunk → document join returns no rows, dataset_id is None."""
    backend = _FakeBackend(
        responses=[
            [{"id": 5}],  # chunk_rows
            [],  # session_rows empty
            [],  # doc_rows empty → dataset_id = None
            [{"id": 300}],  # new_session_rows
            [{"id": 4242}],  # event_rows
        ]
    )
    out = rate_search_result(
        backend,
        _Ctx(),
        query_id="orphan-q",
        chunk_id=5,
        signal="relevance",
        value=0.5,
        source="human",
    )
    assert out == {"event_id": 4242, "session_id": 300}
    # The INSERT INTO search_sessions used None for dataset_id.
    new_session_params = backend.executes[3][1]
    assert new_session_params == ("orphan-q", None)
