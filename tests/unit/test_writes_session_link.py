"""H-02 RED — writes.py session-link hook + backend helpers.

Tests the session-linking behaviour that H-02 will add:

1. Backend helpers on ``SQLiteBackend``:
   - ``upsert_feedback_session(client, session_id, host, started_at) -> int``
   - ``append_feedback_event(feedback_session_id, *, audit_id, feedback_id,
                             entity_type, entity_id) -> int``
   - ``end_feedback_session(client, session_id) -> bool``

2. writes.py hook: after each write's ``audit_event`` emission, if
   ``ctx.session_id`` is set, upsert ``feedback_sessions`` and append
   ``feedback_events``.  If ``ctx.session_id is None``, skip both tables.

All tests use an in-memory ``SQLiteBackend`` (migrated, includes the
0008_feedback_sessions revision).

Run command:
    .venv/bin/python -m pytest tests/unit/test_writes_session_link.py -v

pytestmark: pytest.mark.unit
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

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
def ctx_with_session() -> _MCPContext:
    return _MCPContext(host="test-host", client="test-client", session_id="sess-h02")


@pytest.fixture
def ctx_no_session() -> _MCPContext:
    return _MCPContext(host="test-host", client="test-client", session_id=None)


@pytest.fixture
def seeded(backend: SQLiteBackend) -> dict[str, int]:
    """Insert one dataset, document, chunk, conversation, message."""
    with backend._get_connection() as conn:
        dataset_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("ds-h02", "text", "h02 unit test dataset"),
        ).fetchone()[0]

        document_id = conn.execute(
            "INSERT INTO documents (dataset_id, source_uri, content_hash, title, text, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (dataset_id, "test://doc/h02.md", "hash-h02", "Doc H02", "Hello H02 world", "{}"),
        ).fetchone()[0]

        chunk_id = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, metadata)"
            " VALUES (?, ?, ?, ?) RETURNING id",
            (document_id, 0, "Hello H02 chunk", "{}"),
        ).fetchone()[0]

        conversation_id = conn.execute(
            "INSERT INTO conversations"
            " (dataset_id, source_uri, content_hash, title, message_count, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (dataset_id, "test://conv/h02", "conv-hash-h02", "Conv H02", 0, "{}"),
        ).fetchone()[0]

        conn.commit()

    return {
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "conversation_id": conversation_id,
        "dataset_name": "ds-h02",
    }


def _count_table(backend: SQLiteBackend, table: str) -> int:
    with backend._get_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _get_feedback_sessions(backend: SQLiteBackend) -> list[Any]:
    with backend._get_connection() as conn:
        return conn.execute("SELECT * FROM feedback_sessions ORDER BY id").fetchall()


def _get_feedback_events(backend: SQLiteBackend) -> list[Any]:
    with backend._get_connection() as conn:
        return conn.execute("SELECT * FROM feedback_events ORDER BY id").fetchall()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ===========================================================================
# Backend helper tests
# ===========================================================================


class TestUpsertFeedbackSession:
    def test_creates_row_on_first_call(self, backend: SQLiteBackend) -> None:
        """upsert_feedback_session creates a row and returns a positive int id."""
        fs_id = backend.upsert_feedback_session(
            client="cursor",
            session_id="sess-001",
            host="mac-01",
            started_at=_now_iso(),
        )
        assert isinstance(fs_id, int)
        assert fs_id > 0
        rows = _get_feedback_sessions(backend)
        assert len(rows) == 1
        row = rows[0]
        assert row["client"] == "cursor"
        assert row["session_id"] == "sess-001"
        assert row["host"] == "mac-01"

    def test_duplicate_key_returns_existing_id(self, backend: SQLiteBackend) -> None:
        """Second call with same (client, session_id) returns the same id without error."""
        started = _now_iso()
        id1 = backend.upsert_feedback_session(
            client="cursor", session_id="sess-dup", host="mac-01", started_at=started
        )
        id2 = backend.upsert_feedback_session(
            client="cursor", session_id="sess-dup", host="mac-01", started_at=started
        )
        assert id1 == id2
        # Only one row should exist
        assert _count_table(backend, "feedback_sessions") == 1

    def test_different_session_ids_are_distinct_rows(self, backend: SQLiteBackend) -> None:
        """Two different session_ids produce two distinct rows."""
        id1 = backend.upsert_feedback_session(
            client="cursor", session_id="s-A", host="host", started_at=_now_iso()
        )
        id2 = backend.upsert_feedback_session(
            client="cursor", session_id="s-B", host="host", started_at=_now_iso()
        )
        assert id1 != id2
        assert _count_table(backend, "feedback_sessions") == 2


class TestAppendFeedbackEvent:
    def _make_session(self, backend: SQLiteBackend) -> int:
        return backend.upsert_feedback_session(
            client="c", session_id="s", host="h", started_at=_now_iso()
        )

    def _make_audit(self, backend: SQLiteBackend) -> int:
        return backend.audit_event(
            "host", "client", "sess", "add_label", "document", 1, {}, {}, False
        )

    def test_append_with_audit_id(self, backend: SQLiteBackend) -> None:
        """append_feedback_event with audit_id set returns a positive event id."""
        fs_id = self._make_session(backend)
        audit_id = self._make_audit(backend)
        ev_id = backend.append_feedback_event(
            fs_id,
            audit_id=audit_id,
            feedback_id=None,
            entity_type="document",
            entity_id=1,
        )
        assert isinstance(ev_id, int)
        assert ev_id > 0
        events = _get_feedback_events(backend)
        assert len(events) == 1
        assert events[0]["audit_id"] == audit_id
        assert events[0]["feedback_id"] is None

    def test_append_with_feedback_id(self, backend: SQLiteBackend) -> None:
        """append_feedback_event with feedback_id set (audit_id=None) works."""
        fs_id = self._make_session(backend)
        # Insert a real feedback row first
        with backend._get_connection() as conn:
            fb_id = conn.execute(
                "INSERT INTO feedback (entity_type, entity_id, kind) VALUES (?, ?, ?) RETURNING id",
                ("document", 1, "thumbs"),
            ).fetchone()[0]
            conn.commit()
        ev_id = backend.append_feedback_event(
            fs_id,
            audit_id=None,
            feedback_id=fb_id,
            entity_type="document",
            entity_id=1,
        )
        assert isinstance(ev_id, int)
        events = _get_feedback_events(backend)
        assert events[0]["feedback_id"] == fb_id
        assert events[0]["audit_id"] is None

    def test_append_with_both_audit_and_feedback(self, backend: SQLiteBackend) -> None:
        """Both audit_id and feedback_id can be set simultaneously."""
        fs_id = self._make_session(backend)
        audit_id = self._make_audit(backend)
        with backend._get_connection() as conn:
            fb_id = conn.execute(
                "INSERT INTO feedback (entity_type, entity_id, kind) VALUES (?, ?, ?) RETURNING id",
                ("document", 1, "rating"),
            ).fetchone()[0]
            conn.commit()
        ev_id = backend.append_feedback_event(
            fs_id,
            audit_id=audit_id,
            feedback_id=fb_id,
            entity_type="document",
            entity_id=1,
        )
        assert isinstance(ev_id, int)
        events = _get_feedback_events(backend)
        assert events[0]["audit_id"] == audit_id
        assert events[0]["feedback_id"] == fb_id

    def test_requires_audit_or_feedback_not_both_none(self, backend: SQLiteBackend) -> None:
        """Passing audit_id=None and feedback_id=None raises ValueError."""
        fs_id = self._make_session(backend)
        with pytest.raises(ValueError, match=r"audit_id.*feedback_id"):
            backend.append_feedback_event(
                fs_id,
                audit_id=None,
                feedback_id=None,
                entity_type="document",
                entity_id=1,
            )


class TestEndFeedbackSession:
    def test_sets_ended_at_on_existing_row(self, backend: SQLiteBackend) -> None:
        """end_feedback_session sets ended_at and returns True."""
        backend.upsert_feedback_session(
            client="cursor", session_id="sess-end", host="mac", started_at=_now_iso()
        )
        result = backend.end_feedback_session(client="cursor", session_id="sess-end")
        assert result is True
        rows = _get_feedback_sessions(backend)
        assert rows[0]["ended_at"] is not None

    def test_returns_false_for_unknown_session(self, backend: SQLiteBackend) -> None:
        """end_feedback_session for a non-existent session returns False without raising."""
        result = backend.end_feedback_session(client="nobody", session_id="no-such-session")
        assert result is False


# ===========================================================================
# writes.py hook tests
# ===========================================================================


class TestAddLabelWithSession:
    def test_creates_feedback_session_and_event(
        self, backend: SQLiteBackend, ctx_with_session: _MCPContext, seeded: dict
    ) -> None:
        """add_label with session_id set upserts feedback_sessions + appends feedback_events."""
        result = writes.add_label(
            backend,
            ctx_with_session,
            "document",
            seeded["document_id"],
            "topic",
            "python",
        )
        assert isinstance(result["audit_id"], int)

        sessions = _get_feedback_sessions(backend)
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == ctx_with_session.session_id
        assert sessions[0]["client"] == ctx_with_session.client

        events = _get_feedback_events(backend)
        assert len(events) == 1
        assert events[0]["audit_id"] == result["audit_id"]
        assert events[0]["feedback_session_id"] == sessions[0]["id"]

    def test_skips_feedback_tables_when_session_id_is_none(
        self, backend: SQLiteBackend, ctx_no_session: _MCPContext, seeded: dict
    ) -> None:
        """add_label without session_id must NOT write to feedback_sessions or feedback_events."""
        writes.add_label(
            backend,
            ctx_no_session,
            "document",
            seeded["document_id"],
            "topic",
            "rust",
        )
        # Audit row still exists
        with backend._get_connection() as conn:
            audit_count = conn.execute("SELECT COUNT(*) FROM mcp_audit").fetchone()[0]
        assert audit_count == 1

        # Feedback tables remain empty
        assert _count_table(backend, "feedback_sessions") == 0
        assert _count_table(backend, "feedback_events") == 0


class TestAddFeedbackWithSession:
    def test_feedback_event_has_both_audit_id_and_feedback_id(
        self, backend: SQLiteBackend, ctx_with_session: _MCPContext, seeded: dict
    ) -> None:
        """add_feedback with session_id links both audit_id and feedback_id in the event row."""
        result = writes.add_feedback(
            backend,
            ctx_with_session,
            "document",
            seeded["document_id"],
            "thumbs",
            rating=1,
        )
        feedback_id = result["feedback_id"]
        audit_id = result["audit_id"]

        events = _get_feedback_events(backend)
        assert len(events) == 1
        ev = events[0]
        assert ev["audit_id"] == audit_id
        assert ev["feedback_id"] == feedback_id


class TestAppendConversationWithSession:
    def test_creates_feedback_event_for_append_conversation(
        self, backend: SQLiteBackend, ctx_with_session: _MCPContext, seeded: dict
    ) -> None:
        """append_conversation with session_id creates a feedback_events row."""
        result = writes.append_conversation(
            backend,
            ctx_with_session,
            dataset=seeded["dataset_name"],
            title="Session conv H02",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert isinstance(result["audit_id"], int)
        events = _get_feedback_events(backend)
        assert len(events) == 1
        assert events[0]["audit_id"] == result["audit_id"]


class TestDryRunWithSession:
    def test_dry_run_with_session_still_creates_feedback_event(
        self, backend: SQLiteBackend, ctx_with_session: _MCPContext, seeded: dict
    ) -> None:
        """dry_run=True still emits an audit row; feedback_event should follow the audit row.

        The plan states 'audit is always emitted; feedback_event follows audit.'
        We assert: feedback_events has exactly 1 row pointing at the dry-run audit.
        """
        result = writes.add_label(
            backend,
            ctx_with_session,
            "document",
            seeded["document_id"],
            "ghost",
            "dry-val",
            dry_run=True,
        )
        # Audit row emitted with dry_run=True
        with backend._get_connection() as conn:
            audit_rows = conn.execute("SELECT * FROM mcp_audit").fetchall()
        assert len(audit_rows) == 1
        assert audit_rows[0]["dry_run"] in (1, True)

        # feedback_event follows
        events = _get_feedback_events(backend)
        assert len(events) == 1
        assert events[0]["audit_id"] == result["audit_id"]


class TestMultipleWritesSameSession:
    def test_reuses_session_row_across_multiple_writes(
        self, backend: SQLiteBackend, ctx_with_session: _MCPContext, seeded: dict
    ) -> None:
        """Two add_label calls with same session_id yield 1 session row + 2 event rows."""
        writes.add_label(
            backend,
            ctx_with_session,
            "document",
            seeded["document_id"],
            "topic",
            "alpha",
        )
        writes.add_label(
            backend,
            ctx_with_session,
            "document",
            seeded["document_id"],
            "topic",
            "beta",
        )
        assert _count_table(backend, "feedback_sessions") == 1
        assert _count_table(backend, "feedback_events") == 2
