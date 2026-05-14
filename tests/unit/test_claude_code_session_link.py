"""H-03 RED — shared helper unit tests for _session_link + backend helper.

Tests the session-linking behaviour that H-03 will add:

1. ``corpus_forge.sources._session_link.link_session_to_conversation`` —
   the shared helper that calls the backend to perform the UPDATE.

2. ``SQLiteBackend.link_feedback_session_to_conversation`` —
   UPDATE feedback_sessions SET conversation_id=? WHERE client=? AND
   session_id=? AND conversation_id IS NULL; returns True if rowcount>0.

All tests use an in-memory ``SQLiteBackend`` (migrated, includes the
0008_feedback_sessions revision).  PG backend tested in integration file.

Run command:
    .venv/bin/python -m pytest tests/unit/test_claude_code_session_link.py -v

pytestmark: pytest.mark.unit
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

# This module does not exist yet — import will fail at collection time (RED).
from corpus_forge.sources._session_link import link_session_to_conversation

from corpus_forge.backends.sqlite import SQLiteBackend

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _make_backend() -> SQLiteBackend:
    """Fresh migrated in-memory SQLiteBackend."""
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _seed_conversation(backend: SQLiteBackend, dataset_id: int, source_uri: str) -> int:
    """Insert a minimal conversations row and return its id."""
    with backend._get_connection() as conn:
        row = conn.execute(
            """
            INSERT INTO conversations
              (dataset_id, source_uri, content_hash, title, message_count, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (dataset_id, source_uri, "hash-h03", "H-03 conv", 0, "{}"),
        ).fetchone()
        conn.commit()
    return int(row["id"])


def _seed_dataset(backend: SQLiteBackend) -> int:
    return backend.get_or_create_dataset("ds-h03", "chat", "h03 unit")


def _seed_feedback_session(
    backend: SQLiteBackend,
    *,
    client: str = "claude-code",
    session_id: str = "deadbeef-1234",
    host: str = "test-host",
) -> int:
    """Insert a feedback_sessions row with conversation_id=NULL and return its id."""
    return backend.upsert_feedback_session(
        client=client,
        session_id=session_id,
        host=host,
        started_at=_now_iso(),
    )


def _get_feedback_session(backend: SQLiteBackend, client: str, session_id: str) -> dict | None:
    return backend.get_feedback_session_by_key(client, session_id)


# ===========================================================================
# 1. Backend helper: link_feedback_session_to_conversation
# ===========================================================================


class TestBackendLinkFeedbackSessionToConversation:
    """Tests for SQLiteBackend.link_feedback_session_to_conversation."""

    def test_sets_conversation_id_and_returns_true(self) -> None:
        """Happy path: matching unlinked row → conversation_id is set, returns True."""
        backend = _make_backend()
        dataset_id = _seed_dataset(backend)
        conv_id = _seed_conversation(backend, dataset_id, "claude-code://proj/deadbeef-1234")
        _seed_feedback_session(backend, client="claude-code", session_id="deadbeef-1234")

        result = backend.link_feedback_session_to_conversation(
            "claude-code", "deadbeef-1234", conv_id
        )

        assert result is True
        row = _get_feedback_session(backend, "claude-code", "deadbeef-1234")
        assert row is not None
        assert row["conversation_id"] == conv_id

    def test_no_matching_row_returns_false(self) -> None:
        """No feedback_sessions row for (client, session_id) → returns False without raising."""
        backend = _make_backend()
        dataset_id = _seed_dataset(backend)
        conv_id = _seed_conversation(backend, dataset_id, "claude-code://proj/ghost")

        result = backend.link_feedback_session_to_conversation("claude-code", "ghost-id", conv_id)

        assert result is False

    def test_already_linked_returns_false(self) -> None:
        """conversation_id already set → returns False (idempotent, does not overwrite)."""
        backend = _make_backend()
        dataset_id = _seed_dataset(backend)
        conv_id_first = _seed_conversation(backend, dataset_id, "claude-code://proj/s-already")
        conv_id_second = _seed_conversation(backend, dataset_id, "claude-code://proj/s-new")
        fs_id = _seed_feedback_session(backend, client="claude-code", session_id="s-already-linked")

        # Link once (should succeed)
        linked_first = backend.link_feedback_session_to_conversation(
            "claude-code", "s-already-linked", conv_id_first
        )
        assert linked_first is True

        # Attempt to overwrite — WHERE conversation_id IS NULL no longer matches.
        linked_second = backend.link_feedback_session_to_conversation(
            "claude-code", "s-already-linked", conv_id_second
        )
        assert linked_second is False

        # First link still in place.
        row = _get_feedback_session(backend, "claude-code", "s-already-linked")
        assert row is not None
        assert row["conversation_id"] == conv_id_first
        _ = fs_id  # silence unused-variable warning

    def test_different_client_no_match(self) -> None:
        """feedback_sessions row with client='other'; called with 'claude-code' → False."""
        backend = _make_backend()
        dataset_id = _seed_dataset(backend)
        conv_id = _seed_conversation(backend, dataset_id, "claude-code://proj/shared-session")
        _seed_feedback_session(backend, client="opencode", session_id="shared-session-id")

        result = backend.link_feedback_session_to_conversation(
            "claude-code", "shared-session-id", conv_id
        )

        assert result is False
        # The opencode row should remain untouched.
        row = _get_feedback_session(backend, "opencode", "shared-session-id")
        assert row is not None
        assert row["conversation_id"] is None


# ===========================================================================
# 2. Shared helper: link_session_to_conversation
# ===========================================================================


class TestLinkSessionToConversation:
    """Tests for corpus_forge.sources._session_link.link_session_to_conversation."""

    def test_happy_path_links_and_returns_true(self) -> None:
        """Pre-populated feedback_sessions row; helper sets conversation_id and returns True."""
        backend = _make_backend()
        dataset_id = _seed_dataset(backend)
        conv_id = _seed_conversation(backend, dataset_id, "claude-code://proj/happy")
        _seed_feedback_session(backend, client="claude-code", session_id="happy-session")

        result = link_session_to_conversation(
            backend,
            client="claude-code",
            session_id="happy-session",
            conversation_id=conv_id,
        )

        assert result is True
        row = _get_feedback_session(backend, "claude-code", "happy-session")
        assert row is not None
        assert row["conversation_id"] == conv_id

    def test_no_matching_row_returns_false(self) -> None:
        """No feedback_sessions row → helper returns False, doesn't raise."""
        backend = _make_backend()
        dataset_id = _seed_dataset(backend)
        conv_id = _seed_conversation(backend, dataset_id, "claude-code://proj/nomatch")

        result = link_session_to_conversation(
            backend,
            client="claude-code",
            session_id="no-such-session",
            conversation_id=conv_id,
        )

        assert result is False

    def test_already_linked_returns_false(self) -> None:
        """conversation_id already set → helper returns False (idempotent)."""
        backend = _make_backend()
        dataset_id = _seed_dataset(backend)
        conv_id_a = _seed_conversation(backend, dataset_id, "claude-code://proj/sl-a")
        conv_id_b = _seed_conversation(backend, dataset_id, "claude-code://proj/sl-b")
        _seed_feedback_session(backend, client="claude-code", session_id="already-linked")

        # First call succeeds.
        first = link_session_to_conversation(
            backend,
            client="claude-code",
            session_id="already-linked",
            conversation_id=conv_id_a,
        )
        assert first is True

        # Second call (different conversation_id) should not overwrite.
        second = link_session_to_conversation(
            backend,
            client="claude-code",
            session_id="already-linked",
            conversation_id=conv_id_b,
        )
        assert second is False

        row = _get_feedback_session(backend, "claude-code", "already-linked")
        assert row is not None
        assert row["conversation_id"] == conv_id_a

    def test_different_client_returns_false(self) -> None:
        """feedback_sessions row has client='other'; helper called with 'claude-code' → False."""
        backend = _make_backend()
        dataset_id = _seed_dataset(backend)
        conv_id = _seed_conversation(backend, dataset_id, "claude-code://proj/other-client")
        _seed_feedback_session(backend, client="gemini-cli", session_id="gemini-session")

        result = link_session_to_conversation(
            backend,
            client="claude-code",
            session_id="gemini-session",
            conversation_id=conv_id,
        )

        assert result is False
        # Gemini row untouched.
        row = _get_feedback_session(backend, "gemini-cli", "gemini-session")
        assert row is not None
        assert row["conversation_id"] is None
