"""F-03 RED — MCP write dispatch unit tests.

Tests the ``corpus_forge.mcp.writes`` module (does not exist yet).

Each dispatch function is called in-process with:
  - a real ``SQLiteBackend(":memory:")`` (migrated),
  - a small ``_MCPContext`` dataclass carrying ``host``, ``client``,
    ``session_id``.

Pinned dispatch signatures (Coder must match exactly):

    add_label(
        backend, ctx,
        entity_type: str, entity_id: int,
        namespace: str, value: str,
        *, confidence: float | None = None, dry_run: bool = False
    ) -> dict   # {"label_id": int, "created": bool, "audit_id": int}

    remove_label(
        backend, ctx,
        entity_type: str, entity_id: int,
        namespace: str, value: str,
        *, dry_run: bool = False
    ) -> dict   # {"removed": bool, "audit_id": int}

    set_metadata(
        backend, ctx,
        entity_type: str, entity_id: int,
        key: str, value: Any,
        *, dry_run: bool = False
    ) -> dict   # {"before": dict, "after": dict, "audit_id": int}

    set_description(
        backend, ctx,
        entity_type: str, entity_id: int,
        text: str | None,
        *, dry_run: bool = False
    ) -> dict   # {"before": str | None, "after": str | None, "audit_id": int}

    list_labels(
        backend, ctx,
        entity_type: str | None = None,
        namespace: str | None = None
    ) -> dict   # {"labels": list[dict]}  — NO audit row

    append_conversation(
        backend, ctx,
        dataset: str, title: str,
        messages: list[dict],
        *, started_at: str | None = None,
        metadata: dict | None = None,
        labels: list[tuple[str, str]] | None = None,
        dry_run: bool = False
    ) -> dict   # {"conversation_id": int | None, "message_count": int, "audit_id": int}
                # dry_run → conversation_id = None, message_count = len(messages)

    append_message(
        backend, ctx,
        conversation_id: int, role: str, content: str,
        *, tool_calls: list | None = None,
        tool_results: list | None = None,
        ts: str | None = None,
        metadata: dict | None = None,
        dry_run: bool = False
    ) -> dict   # {"message_id": int | None, "turn_index": int, "audit_id": int}

    add_feedback(
        backend, ctx,
        entity_type: str, entity_id: int, kind: str,
        *, rating: int | None = None,
        text: str | None = None,
        metadata: dict | None = None,
        dry_run: bool = False
    ) -> dict   # {"feedback_id": int | None, "audit_id": int}

``_MCPContext`` carries:
    host: str
    client: str | None
    session_id: str | None

Run command:
    uv run pytest tests/unit/test_mcp_writes_dispatch.py -v
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend

# ---------------------------------------------------------------------------
# Import target — all tests fail here until writes.py exists
# ---------------------------------------------------------------------------
from corpus_forge.mcp import writes

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@dataclass
class _MCPContext:
    """Minimal context object carrying MCP caller identity."""

    host: str
    client: str | None
    session_id: str | None


@pytest.fixture
def backend() -> SQLiteBackend:
    """Fresh migrated in-memory SQLiteBackend for each test."""
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


@pytest.fixture
def ctx() -> _MCPContext:
    return _MCPContext(host="test-host", client="test-client", session_id="sess-001")


@pytest.fixture
def seeded(backend: SQLiteBackend) -> dict[str, int]:
    """Insert one dataset, document, chunk, conversation, message."""
    with backend._get_connection() as conn:
        dataset_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("ds-alpha", "text", "unit test dataset"),
        ).fetchone()[0]

        document_id = conn.execute(
            "INSERT INTO documents (dataset_id, source_uri, content_hash, title, text, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (dataset_id, "test://doc/a.md", "hash-a", "Doc A", "Hello world", "{}"),
        ).fetchone()[0]

        chunk_id = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, metadata)"
            " VALUES (?, ?, ?, ?) RETURNING id",
            (document_id, 0, "Hello world chunk", "{}"),
        ).fetchone()[0]

        conversation_id = conn.execute(
            "INSERT INTO conversations"
            " (dataset_id, source_uri, content_hash, title, message_count, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (dataset_id, "test://conv/1", "conv-hash-1", "Conv 1", 0, "{}"),
        ).fetchone()[0]

        message_id = conn.execute(
            "INSERT INTO messages"
            " (conversation_id, turn_index, role, content, metadata)"
            " VALUES (?, ?, ?, ?, ?) RETURNING id",
            (conversation_id, 0, "user", "Hi!", "{}"),
        ).fetchone()[0]

        conn.commit()

    return {
        "dataset_id": dataset_id,
        "document_id": document_id,
        "chunk_id": chunk_id,
        "conversation_id": conversation_id,
        "message_id": message_id,
        "dataset_name": "ds-alpha",
    }


def _get_audit_rows(backend: SQLiteBackend) -> list[Any]:
    with backend._get_connection() as conn:
        return conn.execute("SELECT * FROM mcp_audit ORDER BY id").fetchall()


# ---------------------------------------------------------------------------
# add_label
# ---------------------------------------------------------------------------


class TestAddLabel:
    def test_happy_path_returns_label_id_created_audit_id(self, backend, ctx, seeded):
        """add_label returns {label_id, created, audit_id} on first apply."""
        result = writes.add_label(
            backend,
            ctx,
            "document",
            seeded["document_id"],
            "topic",
            "python",
        )
        assert isinstance(result["label_id"], int)
        assert result["created"] is True
        assert isinstance(result["audit_id"], int)

    def test_duplicate_returns_created_false(self, backend, ctx, seeded):
        """Second call with same args returns created=False."""
        writes.add_label(backend, ctx, "document", seeded["document_id"], "topic", "python")
        result = writes.add_label(
            backend, ctx, "document", seeded["document_id"], "topic", "python"
        )
        assert result["created"] is False

    def test_persists_to_db_when_not_dry_run(self, backend, ctx, seeded):
        """Label junction row exists in DB after add_label(dry_run=False)."""
        res = writes.add_label(
            backend, ctx, "document", seeded["document_id"], "ns", "val", dry_run=False
        )
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM document_labels WHERE document_id = ? AND label_id = ?",
                (seeded["document_id"], res["label_id"]),
            ).fetchone()
        assert row is not None

    def test_dry_run_does_not_persist(self, backend, ctx, seeded):
        """dry_run=True does NOT write a junction row."""
        writes.add_label(
            backend, ctx, "document", seeded["document_id"], "ghost", "val", dry_run=True
        )
        with backend._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM document_labels WHERE document_id = ?",
                (seeded["document_id"],),
            ).fetchall()
        assert len(rows) == 0, "dry_run must not persist the label junction row"

    def test_dry_run_still_emits_audit(self, backend, ctx, seeded):
        """dry_run=True still records an audit_event row."""
        writes.add_label(
            backend, ctx, "document", seeded["document_id"], "ghost", "val", dry_run=True
        )
        audit_rows = _get_audit_rows(backend)
        assert len(audit_rows) == 1
        assert audit_rows[0]["dry_run"] in (1, True)

    def test_invalid_entity_type_raises(self, backend, ctx, seeded):
        """Unknown entity_type raises ValueError or similar."""
        with pytest.raises((ValueError, AttributeError, KeyError, TypeError)):
            writes.add_label(backend, ctx, "widget", 99, "ns", "val")

    def test_audit_row_records_tool_name(self, backend, ctx, seeded):
        """Audit row has tool='add_label'."""
        writes.add_label(backend, ctx, "document", seeded["document_id"], "ns", "v")
        rows = _get_audit_rows(backend)
        assert rows[0]["tool"] == "add_label"


# ---------------------------------------------------------------------------
# remove_label
# ---------------------------------------------------------------------------


class TestRemoveLabel:
    def test_happy_path_returns_removed_true(self, backend, ctx, seeded):
        """remove_label after add_label returns removed=True."""
        writes.add_label(backend, ctx, "document", seeded["document_id"], "topic", "py")
        result = writes.remove_label(backend, ctx, "document", seeded["document_id"], "topic", "py")
        assert result["removed"] is True
        assert isinstance(result["audit_id"], int)

    def test_nonexistent_returns_removed_false(self, backend, ctx, seeded):
        """Removing a label that was never applied returns removed=False."""
        result = writes.remove_label(backend, ctx, "document", seeded["document_id"], "ghost", "ns")
        assert result["removed"] is False

    def test_dry_run_does_not_remove(self, backend, ctx, seeded):
        """dry_run=True does not actually delete the junction row."""
        writes.add_label(backend, ctx, "document", seeded["document_id"], "topic", "py")
        writes.remove_label(
            backend, ctx, "document", seeded["document_id"], "topic", "py", dry_run=True
        )
        with backend._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM document_labels WHERE document_id = ?",
                (seeded["document_id"],),
            ).fetchall()
        assert len(rows) == 1, "dry_run must not delete the junction row"

    def test_idempotent_second_remove(self, backend, ctx, seeded):
        """Double-remove returns removed=False on the second call."""
        writes.add_label(backend, ctx, "document", seeded["document_id"], "topic", "py")
        writes.remove_label(backend, ctx, "document", seeded["document_id"], "topic", "py")
        result = writes.remove_label(backend, ctx, "document", seeded["document_id"], "topic", "py")
        assert result["removed"] is False


# ---------------------------------------------------------------------------
# set_metadata
# ---------------------------------------------------------------------------


class TestSetMetadata:
    def test_happy_path_returns_before_after_audit_id(self, backend, ctx, seeded):
        """set_metadata returns {before, after, audit_id}."""
        result = writes.set_metadata(
            backend, ctx, "document", seeded["document_id"], "author", "Alice"
        )
        assert isinstance(result["before"], dict)
        assert isinstance(result["after"], dict)
        assert result["after"]["author"] == "Alice"
        assert isinstance(result["audit_id"], int)

    def test_before_reflects_pre_patch_state(self, backend, ctx, seeded):
        """before dict matches state before the patch."""
        writes.set_metadata(backend, ctx, "document", seeded["document_id"], "k", "old")
        result = writes.set_metadata(backend, ctx, "document", seeded["document_id"], "k", "new")
        assert result["before"]["k"] == "old"
        assert result["after"]["k"] == "new"

    def test_dry_run_does_not_persist(self, backend, ctx, seeded):
        """dry_run=True does NOT update the DB metadata column."""
        writes.set_metadata(
            backend, ctx, "document", seeded["document_id"], "key", "val", dry_run=True
        )
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT metadata FROM documents WHERE id = ?", (seeded["document_id"],)
            ).fetchone()
        meta = json.loads(row["metadata"])
        assert "key" not in meta, "dry_run must not persist the metadata change"

    def test_dry_run_still_emits_audit(self, backend, ctx, seeded):
        """dry_run=True still records an audit_event row."""
        writes.set_metadata(
            backend, ctx, "document", seeded["document_id"], "key", "val", dry_run=True
        )
        rows = _get_audit_rows(backend)
        assert len(rows) == 1
        assert rows[0]["dry_run"] in (1, True)


# ---------------------------------------------------------------------------
# set_description
# ---------------------------------------------------------------------------


class TestSetDescription:
    def test_happy_path_sets_description(self, backend, ctx, seeded):
        """set_description returns {before, after, audit_id}; before is None initially."""
        result = writes.set_description(
            backend, ctx, "document", seeded["document_id"], "A short summary."
        )
        assert result["before"] is None
        assert result["after"] == "A short summary."
        assert isinstance(result["audit_id"], int)

    def test_before_after_correct_on_update(self, backend, ctx, seeded):
        """Second call: before reflects previously set text."""
        writes.set_description(backend, ctx, "document", seeded["document_id"], "Old")
        result = writes.set_description(backend, ctx, "document", seeded["document_id"], "New")
        assert result["before"] == "Old"
        assert result["after"] == "New"

    def test_dry_run_does_not_persist(self, backend, ctx, seeded):
        """dry_run=True does NOT update the description column."""
        writes.set_description(
            backend, ctx, "document", seeded["document_id"], "Not saved", dry_run=True
        )
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT description FROM documents WHERE id = ?", (seeded["document_id"],)
            ).fetchone()
        assert row["description"] is None, "dry_run must not persist description"

    def test_setting_to_none_clears(self, backend, ctx, seeded):
        """set_description with text=None clears the description; after is None."""
        writes.set_description(backend, ctx, "document", seeded["document_id"], "Some text")
        result = writes.set_description(backend, ctx, "document", seeded["document_id"], None)
        assert result["before"] == "Some text"
        assert result["after"] is None


# ---------------------------------------------------------------------------
# list_labels
# ---------------------------------------------------------------------------


class TestListLabels:
    def test_returns_labels_key(self, backend, ctx, seeded):
        """list_labels returns a dict with a 'labels' key (list)."""
        result = writes.list_labels(backend, ctx)
        assert "labels" in result
        assert isinstance(result["labels"], list)

    def test_returns_applied_labels(self, backend, ctx, seeded):
        """Labels applied via the backend appear in list_labels output."""
        backend.apply_label("document", seeded["document_id"], "topic", "ml")
        result = writes.list_labels(backend, ctx)
        namespaces = [item["namespace"] for item in result["labels"]]
        assert "topic" in namespaces

    def test_filter_by_entity_type(self, backend, ctx, seeded):
        """entity_type filter narrows results."""
        backend.apply_label("document", seeded["document_id"], "topic", "ml")
        backend.apply_label("chunk", seeded["chunk_id"], "quality", "high")
        result = writes.list_labels(backend, ctx, entity_type="document")
        # All returned items must be from document entities
        for item in result["labels"]:
            assert item.get("entity_type", "document") == "document"

    def test_filter_by_namespace(self, backend, ctx, seeded):
        """namespace filter narrows results to that namespace only."""
        backend.apply_label("document", seeded["document_id"], "topic", "ml")
        backend.apply_label("document", seeded["document_id"], "lang", "en")
        result = writes.list_labels(backend, ctx, namespace="topic")
        for item in result["labels"]:
            assert item["namespace"] == "topic"

    def test_no_audit_row_emitted(self, backend, ctx, seeded):
        """list_labels is a read tool — it must NOT emit an audit_event row."""
        writes.list_labels(backend, ctx)
        rows = _get_audit_rows(backend)
        assert len(rows) == 0, "list_labels must not produce audit rows"


# ---------------------------------------------------------------------------
# append_conversation
# ---------------------------------------------------------------------------


class TestAppendConversation:
    def test_happy_path_returns_conversation_id_message_count_audit_id(self, backend, ctx, seeded):
        """Returns {conversation_id, message_count, audit_id} on success."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        result = writes.append_conversation(
            backend,
            ctx,
            dataset=seeded["dataset_name"],
            title="Test convo",
            messages=messages,
        )
        assert isinstance(result["conversation_id"], int)
        assert result["message_count"] == 2
        assert isinstance(result["audit_id"], int)

    def test_messages_turn_indexes_sequential(self, backend, ctx, seeded):
        """Messages in the new conversation have turn_index 0..N-1."""
        messages = [
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
            {"role": "user", "content": "C"},
        ]
        result = writes.append_conversation(
            backend,
            ctx,
            dataset=seeded["dataset_name"],
            title="Turn test",
            messages=messages,
        )
        conv_id = result["conversation_id"]
        with backend._get_connection() as conn:
            rows = conn.execute(
                "SELECT turn_index FROM messages WHERE conversation_id = ? ORDER BY turn_index",
                (conv_id,),
            ).fetchall()
        assert [r["turn_index"] for r in rows] == [0, 1, 2]

    def test_dry_run_returns_none_conversation_id(self, backend, ctx, seeded):
        """dry_run=True returns conversation_id=None (no allocation)."""
        messages = [{"role": "user", "content": "dry"}]
        result = writes.append_conversation(
            backend,
            ctx,
            dataset=seeded["dataset_name"],
            title="Dry run convo",
            messages=messages,
            dry_run=True,
        )
        assert result["conversation_id"] is None
        assert result["message_count"] == 1  # predicted from len(messages)

    def test_dry_run_does_not_persist_conversation(self, backend, ctx, seeded):
        """dry_run=True does NOT insert a conversation row."""
        with backend._get_connection() as conn:
            count_before = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]

        writes.append_conversation(
            backend,
            ctx,
            dataset=seeded["dataset_name"],
            title="Ephemeral",
            messages=[{"role": "user", "content": "x"}],
            dry_run=True,
        )

        with backend._get_connection() as conn:
            count_after = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]

        assert count_after == count_before, "dry_run must not persist the conversation"

    def test_audit_row_references_conversation_id(self, backend, ctx, seeded):
        """Audit row's entity_id matches the returned conversation_id."""
        messages = [{"role": "user", "content": "hi"}]
        result = writes.append_conversation(
            backend,
            ctx,
            dataset=seeded["dataset_name"],
            title="Audited",
            messages=messages,
        )
        rows = _get_audit_rows(backend)
        assert len(rows) >= 1
        # The audit row entity_id should match the new conversation id
        assert any(r["entity_id"] == result["conversation_id"] for r in rows)

    def test_dry_run_still_emits_audit(self, backend, ctx, seeded):
        """dry_run=True still records audit_event with dry_run=True."""
        writes.append_conversation(
            backend,
            ctx,
            dataset=seeded["dataset_name"],
            title="Dry",
            messages=[{"role": "user", "content": "q"}],
            dry_run=True,
        )
        rows = _get_audit_rows(backend)
        assert len(rows) >= 1
        assert any(r["dry_run"] in (1, True) for r in rows)


# ---------------------------------------------------------------------------
# append_message
# ---------------------------------------------------------------------------


class TestAppendMessage:
    def test_happy_path_returns_message_id_turn_index_audit_id(self, backend, ctx, seeded):
        """append_message returns {message_id, turn_index, audit_id}."""
        result = writes.append_message(
            backend,
            ctx,
            seeded["conversation_id"],
            "assistant",
            "Hello back!",
        )
        assert isinstance(result["message_id"], int)
        # seeded conv has one message at turn_index 0
        assert result["turn_index"] == 1
        assert isinstance(result["audit_id"], int)

    def test_turn_index_advances_monotonically(self, backend, ctx, seeded):
        """Successive appends produce increasing turn_index values."""
        r1 = writes.append_message(backend, ctx, seeded["conversation_id"], "assistant", "One")
        r2 = writes.append_message(backend, ctx, seeded["conversation_id"], "user", "Two")
        r3 = writes.append_message(backend, ctx, seeded["conversation_id"], "assistant", "Three")
        assert r1["turn_index"] < r2["turn_index"] < r3["turn_index"]

    def test_dry_run_returns_none_message_id(self, backend, ctx, seeded):
        """dry_run=True returns message_id=None."""
        result = writes.append_message(
            backend,
            ctx,
            seeded["conversation_id"],
            "user",
            "dry content",
            dry_run=True,
        )
        assert result["message_id"] is None
        # turn_index should be predicted (next turn after existing messages)
        assert isinstance(result["turn_index"], int)
        assert result["turn_index"] >= 1

    def test_dry_run_does_not_persist_message(self, backend, ctx, seeded):
        """dry_run=True does NOT insert a message row."""
        with backend._get_connection() as conn:
            count_before = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                (seeded["conversation_id"],),
            ).fetchone()[0]

        writes.append_message(
            backend,
            ctx,
            seeded["conversation_id"],
            "user",
            "ephemeral",
            dry_run=True,
        )

        with backend._get_connection() as conn:
            count_after = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                (seeded["conversation_id"],),
            ).fetchone()[0]

        assert count_after == count_before, "dry_run must not persist the message"

    def test_audit_row_emitted(self, backend, ctx, seeded):
        """append_message emits an audit_event row."""
        writes.append_message(backend, ctx, seeded["conversation_id"], "user", "msg")
        rows = _get_audit_rows(backend)
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# add_feedback
# ---------------------------------------------------------------------------


class TestAddFeedback:
    def test_happy_path_returns_feedback_id_audit_id(self, backend, ctx, seeded):
        """add_feedback returns {feedback_id, audit_id}."""
        result = writes.add_feedback(
            backend,
            ctx,
            "document",
            seeded["document_id"],
            "thumbs",
            rating=1,
            text="Great!",
        )
        assert isinstance(result["feedback_id"], int)
        assert result["feedback_id"] > 0
        assert isinstance(result["audit_id"], int)

    def test_rating_optional(self, backend, ctx, seeded):
        """add_feedback with no rating (text-only) is accepted."""
        result = writes.add_feedback(
            backend,
            ctx,
            "chunk",
            seeded["chunk_id"],
            "comment",
            text="Confusing.",
        )
        assert isinstance(result["feedback_id"], int)

    def test_dry_run_returns_none_feedback_id(self, backend, ctx, seeded):
        """dry_run=True returns feedback_id=None."""
        result = writes.add_feedback(
            backend,
            ctx,
            "document",
            seeded["document_id"],
            "flag",
            rating=0,
            dry_run=True,
        )
        assert result["feedback_id"] is None

    def test_dry_run_does_not_persist_feedback(self, backend, ctx, seeded):
        """dry_run=True does NOT insert a feedback row."""
        writes.add_feedback(
            backend,
            ctx,
            "document",
            seeded["document_id"],
            "flag",
            rating=0,
            dry_run=True,
        )
        with backend._get_connection() as conn:
            rows = conn.execute("SELECT * FROM feedback").fetchall()
        assert len(rows) == 0, "dry_run must not persist feedback"

    def test_audit_row_emitted(self, backend, ctx, seeded):
        """add_feedback always emits an audit_event row."""
        writes.add_feedback(
            backend,
            ctx,
            "document",
            seeded["document_id"],
            "rating",
            rating=5,
        )
        rows = _get_audit_rows(backend)
        assert len(rows) >= 1


# ---------------------------------------------------------------------------
# Cross-cutting: audit identity + dry_run
# ---------------------------------------------------------------------------


class TestAuditIdentityFlow:
    def test_host_client_session_id_flow_through_audit(self, backend, seeded):
        """host/client/session_id from MCPContext flow into the audit row."""
        ctx = _MCPContext(host="prod-host", client="cursor-ext", session_id="sess-xyz")
        writes.add_label(
            backend,
            ctx,
            "document",
            seeded["document_id"],
            "flow-test",
            "value",
        )
        rows = _get_audit_rows(backend)
        assert len(rows) == 1
        row = rows[0]
        assert row["host"] == "prod-host"
        assert row["client"] == "cursor-ext"
        assert row["session_id"] == "sess-xyz"

    def test_null_client_and_session_accepted(self, backend, seeded):
        """client=None, session_id=None flow through as SQL NULL without error."""
        ctx = _MCPContext(host="host-anon", client=None, session_id=None)
        writes.add_feedback(
            backend,
            ctx,
            "document",
            seeded["document_id"],
            "thumbs",
            rating=1,
        )
        rows = _get_audit_rows(backend)
        assert len(rows) == 1
        assert rows[0]["client"] is None
        assert rows[0]["session_id"] is None

    def test_dry_run_across_multiple_tools_does_not_persist(self, backend, ctx, seeded):
        """dry_run=True on multiple tools leaves all data tables empty."""
        writes.add_label(backend, ctx, "document", seeded["document_id"], "ns", "v", dry_run=True)
        writes.set_metadata(backend, ctx, "document", seeded["document_id"], "k", "x", dry_run=True)
        writes.add_feedback(
            backend,
            ctx,
            "document",
            seeded["document_id"],
            "flag",
            rating=0,
            dry_run=True,
        )

        with backend._get_connection() as conn:
            label_junctions = conn.execute(
                "SELECT COUNT(*) FROM document_labels WHERE document_id = ?",
                (seeded["document_id"],),
            ).fetchone()[0]
        # metadata: original was "{}" — should still be "{}" not changed
        with backend._get_connection() as conn:
            meta = conn.execute(
                "SELECT metadata FROM documents WHERE id = ?", (seeded["document_id"],)
            ).fetchone()["metadata"]
        with backend._get_connection() as conn:
            feedback_count = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]

        assert label_junctions == 0, "dry_run add_label must not persist"
        assert json.loads(meta) == {}, "dry_run set_metadata must not persist"
        assert feedback_count == 0, "dry_run add_feedback must not persist"

    def test_dry_run_audit_rows_all_flagged(self, backend, ctx, seeded):
        """All audit rows from dry_run calls have dry_run=True."""
        writes.add_label(backend, ctx, "document", seeded["document_id"], "ns", "v", dry_run=True)
        writes.remove_label(
            backend, ctx, "document", seeded["document_id"], "ns", "v", dry_run=True
        )
        rows = _get_audit_rows(backend)
        assert all(r["dry_run"] in (1, True) for r in rows), (
            "All dry_run calls must produce audit rows flagged dry_run=True"
        )
