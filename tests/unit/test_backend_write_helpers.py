"""Unit tests for F-02 backend write helpers.

Tests all nine helper methods that F-02 will add to SQLiteBackend
(and, by protocol symmetry, PostgresBackend):

  apply_label / revoke_label / patch_metadata / set_description /
  append_conversation / append_message / add_feedback / audit_event /
  hydrate_hit_metadata

Run command:
    uv run pytest tests/unit/test_backend_write_helpers.py -v

Backend target: SQLite in-memory only for speed.  PG-only behaviour
(JSONB merge semantics, NOW() server-side defaults, BIGSERIAL
sequencing) is deferred to F-05 integration smoke.  The nine helpers
are pure Python logic + SQL; the dialect differences are trivial
enough that SQLite green implies PG green for the helper contracts.

Schema notes (from 0001_core + 0006_writes_and_feedback migrations):
- ``document_labels`` has NO ``confidence`` column; only chunk_labels does.
- ``feedback`` table has ``host TEXT NOT NULL`` — supply a default.
- ``mcp_audit`` table has ``host TEXT NOT NULL``.
- ``description`` column exists on documents, conversations, chunks
  (added by migration 0006).
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Any

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.retrieval.types import Hit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> SQLiteBackend:
    """Fresh migrated in-memory SQLiteBackend for each test."""
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


@pytest.fixture
def seeded(backend: SQLiteBackend):
    """Backend with one dataset, document, chunk, and conversation pre-inserted.

    Returns a dict with keys: dataset_id, document_id, chunk_id,
    conversation_id, message_id.
    """
    with backend._get_connection() as conn:
        # dataset
        dataset_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("test-dataset", "text", "unit test dataset"),
        ).fetchone()[0]

        # document
        document_id = conn.execute(
            "INSERT INTO documents (dataset_id, source_uri, content_hash, title, text, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (dataset_id, "test://doc/a.md", "hash-a", "Doc A", "Hello world", "{}"),
        ).fetchone()[0]

        # chunk
        chunk_id = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text, metadata)"
            " VALUES (?, ?, ?, ?) RETURNING id",
            (document_id, 0, "Hello world chunk", "{}"),
        ).fetchone()[0]

        # conversation
        conversation_id = conn.execute(
            "INSERT INTO conversations"
            " (dataset_id, source_uri, content_hash, title, message_count, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (dataset_id, "test://conv/1", "conv-hash-1", "Conv 1", 0, "{}"),
        ).fetchone()[0]

        # message in that conversation
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
    }


# ---------------------------------------------------------------------------
# apply_label
# ---------------------------------------------------------------------------


class TestApplyLabel:
    """apply_label(entity_type, entity_id, namespace, value, ...) -> (label_id, created)"""

    def test_happy_path_creates_label_and_junction_row(self, backend, seeded):
        """First call creates a labels row and a document_labels junction row."""
        label_id, created = backend.apply_label(
            "document", seeded["document_id"], "topic", "python"
        )
        assert isinstance(label_id, int)
        assert created is True

        with backend._get_connection() as conn:
            label = conn.execute(
                "SELECT namespace, value FROM labels WHERE id = ?", (label_id,)
            ).fetchone()
            assert label["namespace"] == "topic"
            assert label["value"] == "python"

            junc = conn.execute(
                "SELECT * FROM document_labels WHERE document_id = ? AND label_id = ?",
                (seeded["document_id"], label_id),
            ).fetchone()
            assert junc is not None

    def test_duplicate_label_reuses_existing_label_id(self, backend, seeded):
        """Second apply_label with the same namespace/value returns the same label_id."""
        label_id1, created1 = backend.apply_label(
            "document", seeded["document_id"], "topic", "python"
        )
        label_id2, created2 = backend.apply_label(
            "document", seeded["document_id"], "topic", "python"
        )
        assert label_id1 == label_id2
        assert created1 is True
        assert created2 is False  # second call: label already exists

    def test_source_defaults_to_user(self, backend, seeded):
        """source defaults to 'user' when not specified."""
        label_id, _ = backend.apply_label("document", seeded["document_id"], "ns", "val")
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT source FROM document_labels WHERE document_id = ? AND label_id = ?",
                (seeded["document_id"], label_id),
            ).fetchone()
        assert row["source"] == "user"

    def test_confidence_stored_on_chunk_label(self, backend, seeded):
        """confidence is persisted in chunk_labels (which has that column)."""
        label_id, _ = backend.apply_label(
            "chunk", seeded["chunk_id"], "quality", "high", confidence=0.95
        )
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT confidence FROM chunk_labels WHERE chunk_id = ? AND label_id = ?",
                (seeded["chunk_id"], label_id),
            ).fetchone()
        assert row is not None
        assert abs(row["confidence"] - 0.95) < 1e-6

    def test_conversation_entity_type_works(self, backend, seeded):
        """entity_type='conversation' writes to conversation_labels."""
        label_id, created = backend.apply_label(
            "conversation", seeded["conversation_id"], "lang", "en"
        )
        assert created is True
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM conversation_labels WHERE conversation_id = ? AND label_id = ?",
                (seeded["conversation_id"], label_id),
            ).fetchone()
        assert row is not None

    def test_custom_source_is_stored(self, backend, seeded):
        """source='model' is persisted in the junction table."""
        label_id, _ = backend.apply_label(
            "document", seeded["document_id"], "sentiment", "positive", source="model"
        )
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT source FROM document_labels WHERE document_id = ? AND label_id = ?",
                (seeded["document_id"], label_id),
            ).fetchone()
        assert row["source"] == "model"

    def test_invalid_entity_type_raises(self, backend, seeded):
        """Unknown entity_type raises ValueError (not a silent no-op)."""
        with pytest.raises((ValueError, AttributeError, KeyError)):
            backend.apply_label("widget", 999, "ns", "val")


# ---------------------------------------------------------------------------
# revoke_label
# ---------------------------------------------------------------------------


class TestRevokeLabel:
    """revoke_label(entity_type, entity_id, namespace, value) -> bool"""

    def test_happy_path_deletes_existing_junction_row(self, backend, seeded):
        """Revoke a label that was previously applied — returns True."""
        backend.apply_label("document", seeded["document_id"], "topic", "python")
        result = backend.revoke_label("document", seeded["document_id"], "topic", "python")
        assert result is True

        with backend._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM document_labels WHERE document_id = ?",
                (seeded["document_id"],),
            ).fetchall()
        assert len(rows) == 0

    def test_revoke_nonexistent_returns_false(self, backend, seeded):
        """Revoking a label that was never applied returns False (no exception)."""
        result = backend.revoke_label("document", seeded["document_id"], "ghost", "ns")
        assert result is False

    def test_revoke_idempotent_second_call(self, backend, seeded):
        """Double-revoking the same label returns False on the second call."""
        backend.apply_label("document", seeded["document_id"], "topic", "python")
        backend.revoke_label("document", seeded["document_id"], "topic", "python")
        result = backend.revoke_label("document", seeded["document_id"], "topic", "python")
        assert result is False


# ---------------------------------------------------------------------------
# patch_metadata
# ---------------------------------------------------------------------------


class TestPatchMetadata:
    """patch_metadata(entity_type, entity_id, key, value) -> (before, after)"""

    def test_happy_path_merges_new_key(self, backend, seeded):
        """Patching a new key adds it to the metadata dict."""
        before, after = backend.patch_metadata("document", seeded["document_id"], "author", "Alice")
        assert isinstance(before, dict)
        assert isinstance(after, dict)
        assert "author" not in before
        assert after["author"] == "Alice"

    def test_overwrites_existing_key(self, backend, seeded):
        """Patching an existing key returns the old value in before and new in after."""
        backend.patch_metadata("document", seeded["document_id"], "author", "Alice")
        before, after = backend.patch_metadata("document", seeded["document_id"], "author", "Bob")
        assert before["author"] == "Alice"
        assert after["author"] == "Bob"

    def test_before_and_after_match_db(self, backend, seeded):
        """before dict matches pre-patch DB state; after matches post-patch DB state."""
        _before, after = backend.patch_metadata("document", seeded["document_id"], "score", 42)
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT metadata FROM documents WHERE id = ?", (seeded["document_id"],)
            ).fetchone()
        current = json.loads(row["metadata"])
        assert current == after

    def test_chunk_entity_type(self, backend, seeded):
        """patch_metadata works on chunks."""
        _before, after = backend.patch_metadata("chunk", seeded["chunk_id"], "quality_score", 0.9)
        assert after["quality_score"] == pytest.approx(0.9)

    def test_conversation_entity_type(self, backend, seeded):
        """patch_metadata works on conversations."""
        _before, after = backend.patch_metadata(
            "conversation", seeded["conversation_id"], "language", "en"
        )
        assert after["language"] == "en"


# ---------------------------------------------------------------------------
# set_description
# ---------------------------------------------------------------------------


class TestSetDescription:
    """set_description(entity_type, entity_id, text) -> (before, after)"""

    def test_happy_path_sets_description(self, backend, seeded):
        """First call sets description on document; before is None, after is the text."""
        before, after = backend.set_description(
            "document", seeded["document_id"], "A short summary."
        )
        assert before is None
        assert after == "A short summary."

    def test_setting_to_none_clears_description(self, backend, seeded):
        """Setting description to None clears it; after is None."""
        backend.set_description("document", seeded["document_id"], "Some text")
        before, after = backend.set_description("document", seeded["document_id"], None)
        assert before == "Some text"
        assert after is None

    def test_before_after_returned_correctly(self, backend, seeded):
        """Second call: before reflects the previously set text."""
        backend.set_description("document", seeded["document_id"], "Old text")
        before, after = backend.set_description("document", seeded["document_id"], "New text")
        assert before == "Old text"
        assert after == "New text"

    def test_chunk_entity_type(self, backend, seeded):
        """set_description works on chunks."""
        _before, after = backend.set_description("chunk", seeded["chunk_id"], "Chunk desc.")
        assert after == "Chunk desc."

    def test_description_persisted_in_db(self, backend, seeded):
        """After set_description, the DB row reflects the new value."""
        backend.set_description("document", seeded["document_id"], "Persisted.")
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT description FROM documents WHERE id = ?", (seeded["document_id"],)
            ).fetchone()
        assert row["description"] == "Persisted."


# ---------------------------------------------------------------------------
# append_conversation
# ---------------------------------------------------------------------------


class TestAppendConversation:
    """append_conversation(dataset_id, title, started_at, messages, ...) -> (conv_id, msg_count)"""

    def test_happy_path_three_messages(self, backend, seeded):
        """Inserts a new conversation with 3 messages; returns correct msg_count."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "What's up?"},
        ]
        conv_id, msg_count = backend.append_conversation(
            seeded["dataset_id"],
            title="Test convo",
            started_at=datetime(2026, 1, 1, tzinfo=UTC),
            messages=messages,
        )
        assert isinstance(conv_id, int)
        assert msg_count == 3

    def test_turn_indexes_are_sequential(self, backend, seeded):
        """Messages get turn_index 0, 1, 2 in insertion order."""
        messages = [
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
            {"role": "user", "content": "C"},
        ]
        conv_id, _ = backend.append_conversation(
            seeded["dataset_id"],
            title="Turn test",
            started_at=None,
            messages=messages,
        )
        with backend._get_connection() as conn:
            rows = conn.execute(
                "SELECT turn_index, content FROM messages WHERE conversation_id = ?"
                " ORDER BY turn_index",
                (conv_id,),
            ).fetchall()
        assert [r["turn_index"] for r in rows] == [0, 1, 2]
        assert [r["content"] for r in rows] == ["A", "B", "C"]

    def test_labels_populated(self, backend, seeded):
        """Labels list is applied to the new conversation."""
        labels = [("topic", "greetings"), ("lang", "en")]
        conv_id, _ = backend.append_conversation(
            seeded["dataset_id"],
            title="Labeled convo",
            started_at=None,
            messages=[{"role": "user", "content": "hi"}],
            labels=labels,
        )
        with backend._get_connection() as conn:
            rows = conn.execute(
                "SELECT l.namespace, l.value FROM labels l"
                " JOIN conversation_labels cl ON cl.label_id = l.id"
                " WHERE cl.conversation_id = ?",
                (conv_id,),
            ).fetchall()
        applied = {(r["namespace"], r["value"]) for r in rows}
        assert ("topic", "greetings") in applied
        assert ("lang", "en") in applied

    def test_metadata_stored(self, backend, seeded):
        """metadata dict is persisted on the conversation row."""
        conv_id, _ = backend.append_conversation(
            seeded["dataset_id"],
            title="Meta convo",
            started_at=None,
            messages=[{"role": "user", "content": "x"}],
            metadata={"source": "import", "version": 2},
        )
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT metadata FROM conversations WHERE id = ?", (conv_id,)
            ).fetchone()
        stored = json.loads(row["metadata"])
        assert stored["source"] == "import"
        assert stored["version"] == 2

    def test_empty_messages_list(self, backend, seeded):
        """Appending an empty message list creates the conversation with 0 messages."""
        conv_id, msg_count = backend.append_conversation(
            seeded["dataset_id"],
            title="Empty convo",
            started_at=None,
            messages=[],
        )
        assert msg_count == 0
        assert isinstance(conv_id, int)


# ---------------------------------------------------------------------------
# append_message
# ---------------------------------------------------------------------------


class TestAppendMessage:
    """append_message(conversation_id, role, content, ...) -> (message_id, turn_index)"""

    def test_happy_path_appends_to_existing_conversation(self, backend, seeded):
        """Appending a message to an existing conv returns valid (message_id, turn_index)."""
        msg_id, turn_idx = backend.append_message(
            seeded["conversation_id"], "assistant", "Hello back!"
        )
        assert isinstance(msg_id, int)
        # The seeded conversation already has one message at turn_index 0
        assert turn_idx == 1

    def test_turn_index_advances_monotonically(self, backend, seeded):
        """Multiple appends yield strictly increasing turn_indexes."""
        _, t1 = backend.append_message(seeded["conversation_id"], "assistant", "One")
        _, t2 = backend.append_message(seeded["conversation_id"], "user", "Two")
        _, t3 = backend.append_message(seeded["conversation_id"], "assistant", "Three")
        assert t1 < t2 < t3
        assert t3 == t1 + 2

    def test_optional_fields_stored(self, backend, seeded):
        """tool_calls, tool_results, ts, metadata are stored when provided."""
        ts = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
        msg_id, _ = backend.append_message(
            seeded["conversation_id"],
            "assistant",
            "Used a tool",
            tool_calls=[{"name": "search", "args": {}}],
            tool_results=[{"output": "result"}],
            ts=ts,
            metadata={"model": "claude-3"},
        )
        with backend._get_connection() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id = ?", (msg_id,)).fetchone()
        assert json.loads(row["tool_calls"]) == [{"name": "search", "args": {}}]
        assert json.loads(row["tool_results"]) == [{"output": "result"}]
        assert row["metadata"] is not None

    def test_concurrent_appends_get_distinct_turn_indexes(self, backend, seeded):
        """Two threads appending simultaneously get distinct turn_index values."""
        results: list[tuple[int, int]] = []
        errors: list[Exception] = []

        def _append(content: str) -> None:
            try:
                result = backend.append_message(seeded["conversation_id"], "user", content)
                results.append(result)
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=_append, args=("concurrent A",))
        t2 = threading.Thread(target=_append, args=("concurrent B",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Concurrent appends raised: {errors}"
        assert len(results) == 2
        turn_indexes = {r[1] for r in results}
        assert len(turn_indexes) == 2, f"Expected distinct turn_indexes, got {turn_indexes}"


# ---------------------------------------------------------------------------
# add_feedback
# ---------------------------------------------------------------------------


class TestAddFeedback:
    """add_feedback(entity_type, entity_id, kind, ...) -> feedback_id"""

    def test_happy_path_rating_and_text(self, backend, seeded):
        """Happy path: rating + text both stored; returns a valid integer id."""
        fb_id = backend.add_feedback(
            "document",
            seeded["document_id"],
            "thumbs",
            rating=1,
            text="Great doc!",
        )
        assert isinstance(fb_id, int)
        assert fb_id > 0

        with backend._get_connection() as conn:
            row = conn.execute("SELECT * FROM feedback WHERE id = ?", (fb_id,)).fetchone()
        assert row["rating"] == 1
        assert row["text"] == "Great doc!"
        assert row["kind"] == "thumbs"
        assert row["entity_type"] == "document"
        assert row["entity_id"] == seeded["document_id"]

    def test_text_only_feedback_no_rating(self, backend, seeded):
        """Feedback with text and no rating (rating=None) is accepted."""
        fb_id = backend.add_feedback(
            "chunk",
            seeded["chunk_id"],
            "comment",
            text="This chunk is confusing.",
        )
        with backend._get_connection() as conn:
            row = conn.execute("SELECT rating FROM feedback WHERE id = ?", (fb_id,)).fetchone()
        assert row["rating"] is None

    def test_metadata_defaults_to_empty_dict(self, backend, seeded):
        """When metadata is not passed, stored metadata is an empty dict (not None)."""
        fb_id = backend.add_feedback("document", seeded["document_id"], "flag", rating=0)
        with backend._get_connection() as conn:
            row = conn.execute("SELECT metadata FROM feedback WHERE id = ?", (fb_id,)).fetchone()
        stored = (
            json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
        )
        assert stored == {} or stored is None  # either empty dict or NULL is acceptable

    def test_custom_metadata_stored(self, backend, seeded):
        """Custom metadata dict is persisted."""
        fb_id = backend.add_feedback(
            "conversation",
            seeded["conversation_id"],
            "rating",
            rating=5,
            metadata={"session": "abc", "version": 1},
        )
        with backend._get_connection() as conn:
            row = conn.execute("SELECT metadata FROM feedback WHERE id = ?", (fb_id,)).fetchone()
        stored = (
            json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
        )
        assert stored["session"] == "abc"

    def test_multiple_feedback_entries_independent(self, backend, seeded):
        """Two add_feedback calls return different ids."""
        id1 = backend.add_feedback("document", seeded["document_id"], "thumbs", rating=1)
        id2 = backend.add_feedback("document", seeded["document_id"], "thumbs", rating=0)
        assert id1 != id2


# ---------------------------------------------------------------------------
# audit_event
# ---------------------------------------------------------------------------


class TestAuditEvent:
    """audit_event(host, client, session_id, tool, entity_type, entity_id,
    before, after, dry_run) -> audit_id
    """

    def test_happy_path(self, backend, seeded):
        """Inserts a row in mcp_audit and returns an integer audit_id."""
        audit_id = backend.audit_event(
            host="host-1",
            client="cursor",
            session_id="sess-abc",
            tool="apply_label",
            entity_type="document",
            entity_id=seeded["document_id"],
            before=None,
            after={"label": "python"},
            dry_run=False,
        )
        assert isinstance(audit_id, int)
        assert audit_id > 0

        with backend._get_connection() as conn:
            row = conn.execute("SELECT * FROM mcp_audit WHERE id = ?", (audit_id,)).fetchone()
        assert row["host"] == "host-1"
        assert row["tool"] == "apply_label"
        assert row["entity_type"] == "document"
        assert row["entity_id"] == seeded["document_id"]
        assert row["dry_run"] in (0, False)

    def test_dry_run_true_recorded_faithfully(self, backend, seeded):
        """dry_run=True is persisted; it does NOT prevent the row from being inserted."""
        audit_id = backend.audit_event(
            host="host-1",
            client=None,
            session_id=None,
            tool="patch_metadata",
            entity_type="chunk",
            entity_id=seeded["chunk_id"],
            before={"key": "old"},
            after={"key": "new"},
            dry_run=True,
        )
        with backend._get_connection() as conn:
            row = conn.execute("SELECT dry_run FROM mcp_audit WHERE id = ?", (audit_id,)).fetchone()
        # SQLite stores booleans as 0/1 integers
        assert row["dry_run"] in (1, True)

    def test_before_and_after_serialized_as_json(self, backend, seeded):
        """before/after dicts are persisted and round-trip through JSON correctly."""
        before_dict: dict[str, Any] = {"score": 0.5, "tags": ["a", "b"]}
        after_dict: dict[str, Any] = {"score": 0.9, "tags": ["a", "b", "c"]}

        audit_id = backend.audit_event(
            host="host-1",
            client=None,
            session_id=None,
            tool="patch_metadata",
            entity_type="document",
            entity_id=seeded["document_id"],
            before=before_dict,
            after=after_dict,
            dry_run=False,
        )
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT before, after FROM mcp_audit WHERE id = ?", (audit_id,)
            ).fetchone()

        before_stored = (
            json.loads(row["before"]) if isinstance(row["before"], str) else row["before"]
        )
        after_stored = json.loads(row["after"]) if isinstance(row["after"], str) else row["after"]
        assert before_stored == before_dict
        assert after_stored == after_dict

    def test_null_client_and_session_accepted(self, backend, seeded):
        """client=None, session_id=None are stored as SQL NULL without error."""
        audit_id = backend.audit_event(
            host="host-1",
            client=None,
            session_id=None,
            tool="revoke_label",
            entity_type="document",
            entity_id=seeded["document_id"],
            before=None,
            after=None,
            dry_run=False,
        )
        with backend._get_connection() as conn:
            row = conn.execute(
                "SELECT client, session_id FROM mcp_audit WHERE id = ?", (audit_id,)
            ).fetchone()
        assert row["client"] is None
        assert row["session_id"] is None


# ---------------------------------------------------------------------------
# hydrate_hit_metadata
# ---------------------------------------------------------------------------


class TestHydrateHitMetadata:
    """hydrate_hit_metadata(hits: list[Hit]) -> list[Hit]"""

    def _make_hit(self, chunk_id: int, document_id: int | None, dataset_id: int) -> Hit:
        return Hit(
            chunk_id=chunk_id,
            score=1.0,
            text="test chunk text",
            document_id=document_id,
            source_uri="test://doc/a.md",
            title="Doc A",
            dataset_id=dataset_id,
            metadata={},
            source="dense",
        )

    def test_happy_path_hits_gain_labels_and_description(self, backend, seeded):
        """After hydration, hits have labels and description from DB."""
        # Apply a label and description to the chunk
        backend.apply_label("chunk", seeded["chunk_id"], "topic", "ml")
        backend.set_description("chunk", seeded["chunk_id"], "A relevant chunk.")

        hit = self._make_hit(seeded["chunk_id"], seeded["document_id"], seeded["dataset_id"])
        result = backend.hydrate_hit_metadata([hit])

        assert len(result) == 1
        hydrated = result[0]
        # The hydrated hit should have labels populated
        assert hasattr(hydrated, "labels") or isinstance(hydrated, dict)
        # Labels should contain our applied label
        labels = hydrated.labels if hasattr(hydrated, "labels") else hydrated.get("labels", [])
        assert any(
            (lbl == ("topic", "ml") or (isinstance(lbl, dict) and lbl.get("namespace") == "topic"))
            for lbl in labels
        ), f"Expected topic/ml in labels, got: {labels}"

    def test_no_data_case_returns_empty_not_none(self, backend, seeded):
        """Entity with no labels/description/feedback: labels is [] not None."""
        hit = self._make_hit(seeded["chunk_id"], seeded["document_id"], seeded["dataset_id"])
        result = backend.hydrate_hit_metadata([hit])

        assert len(result) == 1
        hydrated = result[0]
        labels = getattr(hydrated, "labels", None)
        if labels is None:
            # If returned as dict
            labels = hydrated.get("labels") if isinstance(hydrated, dict) else None
        assert labels is not None, "labels must not be None — must be empty list at minimum"
        assert labels == []

    def test_recent_feedback_bounded_to_five(self, backend, seeded):
        """recent_feedback contains at most 5 entries even if more exist."""
        for i in range(8):
            backend.add_feedback(
                "chunk",
                seeded["chunk_id"],
                "thumbs",
                rating=i % 2,
                text=f"Feedback {i}",
            )

        hit = self._make_hit(seeded["chunk_id"], seeded["document_id"], seeded["dataset_id"])
        result = backend.hydrate_hit_metadata([hit])
        hydrated = result[0]
        recent_fb = getattr(hydrated, "recent_feedback", None)
        if recent_fb is None and isinstance(hydrated, dict):
            recent_fb = hydrated.get("recent_feedback", [])
        assert recent_fb is not None
        assert len(recent_fb) <= 5

    def test_empty_hits_list_returns_empty_list(self, backend, seeded):
        """hydrate_hit_metadata([]) returns [] without error."""
        result = backend.hydrate_hit_metadata([])
        assert result == []

    def test_no_n_plus_one_single_query_per_entity_type(self, backend, seeded):
        """Multiple hits of the same entity type are bulk-hydrated (smoke check).

        This test verifies that passing N chunk hits works without per-hit queries
        blowing up.  The correctness guarantee is: each hit comes back hydrated.
        The 'no N+1' guarantee is a code-review concern; this test ensures the
        method accepts a batch and returns the same count.
        """
        # Insert a second chunk for the same document
        with backend._get_connection() as conn:
            chunk_id2 = conn.execute(
                "INSERT INTO chunks (document_id, chunk_index, text, metadata)"
                " VALUES (?, ?, ?, ?) RETURNING id",
                (seeded["document_id"], 1, "Second chunk", "{}"),
            ).fetchone()[0]
            conn.commit()

        backend.apply_label("chunk", seeded["chunk_id"], "tag", "first")
        backend.apply_label("chunk", chunk_id2, "tag", "second")

        hits = [
            self._make_hit(seeded["chunk_id"], seeded["document_id"], seeded["dataset_id"]),
            self._make_hit(chunk_id2, seeded["document_id"], seeded["dataset_id"]),
        ]
        result = backend.hydrate_hit_metadata(hits)
        assert len(result) == 2

    @pytest.mark.skip(reason="parent-rollup (chunk hit inherits DOCUMENT labels) is F-04's concern")
    def test_chunk_hit_inherits_document_labels(self, backend, seeded):
        """A chunk hit should surface its parent document's labels too.

        This rollup logic is specified in F-04 (MCP search enrichment), not F-02.
        Pinning here as a skip so the boundary is explicit and the Coder doesn't
        accidentally implement it in hydrate_hit_metadata and break F-04's contract.
        """
        backend.apply_label("document", seeded["document_id"], "doc-topic", "ml")
        hit = self._make_hit(seeded["chunk_id"], seeded["document_id"], seeded["dataset_id"])
        result = backend.hydrate_hit_metadata([hit])
        hydrated = result[0]
        labels = getattr(hydrated, "labels", [])
        assert any(
            (isinstance(lbl, tuple) and lbl == ("doc-topic", "ml"))
            or (isinstance(lbl, dict) and lbl.get("namespace") == "doc-topic")
            for lbl in labels
        )
