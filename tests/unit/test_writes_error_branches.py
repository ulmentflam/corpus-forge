"""Unit tests for mcp.writes error branches and internal helper paths.

Targets the specific uncovered lines:
  - Line 99: _read_metadata chunk path when get_chunk returns None → {}
  - Line 102: _read_metadata chunk path when metadata is None → {}
  - Line 104: _read_metadata chunk path when metadata is a dict → return directly
  - Line 124: _read_description chunk path when get_chunk returns None → None
  - Line 228: remove_label invalid entity_type raises ValueError
  - Line 290: set_metadata invalid entity_type raises ValueError
  - Line 351: set_description invalid entity_type raises ValueError
  - Lines 439, 463-465: append_conversation dataset not found raises ValueError
  - Lines 550-552: append_message ts string parsing
  - Line 604: add_feedback invalid entity_type raises ValueError

All tests use MagicMock backends — no real DB required.
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from corpus_forge.mcp import writes
from corpus_forge.mcp.writes import (
    _read_description,
    _read_metadata,
)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


@dataclass
class _Ctx:
    host: str = "test-host"
    client: str | None = "test-client"
    session_id: str | None = None


def _backend(**overrides) -> MagicMock:
    """Return a mock backend with sensible defaults for all methods used."""
    b = MagicMock()
    b.audit_event.return_value = 1
    b.apply_label.return_value = (42, True)
    b.revoke_label.return_value = True
    b.patch_metadata.return_value = ({}, {"k": "v"})
    b.set_description.return_value = (None, "text")
    b.find_dataset_id_by_name.return_value = 1
    b.append_conversation.return_value = (10, 2)
    b.count_messages.return_value = 0
    b.append_message.return_value = (100, 1)
    b.add_feedback.return_value = 77
    b.get_entity_metadata.return_value = {}
    b.get_entity_description.return_value = None
    b.upsert_feedback_session.return_value = 1
    b.append_feedback_event.return_value = 1
    for k, v in overrides.items():
        setattr(b, k, v)
    return b


# ---------------------------------------------------------------------------
# _read_metadata internal helper — chunk paths
# ---------------------------------------------------------------------------


class TestReadMetadataHelper:
    def test_chunk_entity_get_chunk_returns_none_yields_empty(self):
        """Line 99: get_chunk returns None → _read_metadata returns {}."""
        b = _backend()
        b.get_chunk.return_value = None
        result = _read_metadata(b, "chunk", 999)
        assert result == {}

    def test_chunk_entity_metadata_is_none_yields_empty(self):
        """Line 102: row exists but metadata is None → returns {}."""
        b = _backend()
        b.get_chunk.return_value = {"id": 1, "metadata": None}
        result = _read_metadata(b, "chunk", 1)
        assert result == {}

    def test_chunk_entity_metadata_is_dict_returns_directly(self):
        """Line 104: metadata already a dict → returned as-is (no json.loads)."""
        b = _backend()
        meta = {"key": "value", "num": 42}
        b.get_chunk.return_value = {"id": 1, "metadata": meta}
        result = _read_metadata(b, "chunk", 1)
        assert result == meta
        assert result is meta  # same object, not a copy

    def test_chunk_entity_metadata_is_json_string_parses(self):
        """Line 105-107: metadata is a JSON string → parsed and returned."""
        b = _backend()
        b.get_chunk.return_value = {"id": 1, "metadata": '{"foo": "bar"}'}
        result = _read_metadata(b, "chunk", 1)
        assert result == {"foo": "bar"}

    def test_non_chunk_entity_delegates_to_get_entity_metadata(self):
        """Document/conversation path delegates to backend.get_entity_metadata."""
        b = _backend()
        b.get_entity_metadata.return_value = {"author": "Alice"}
        result = _read_metadata(b, "document", 1)
        assert result == {"author": "Alice"}
        b.get_entity_metadata.assert_called_once_with("document", 1)


# ---------------------------------------------------------------------------
# _read_description internal helper — chunk paths
# ---------------------------------------------------------------------------


class TestReadDescriptionHelper:
    def test_chunk_entity_get_chunk_returns_none_yields_none(self):
        """Line 124: get_chunk returns None → returns None."""
        b = _backend()
        b.get_chunk.return_value = None
        result = _read_description(b, "chunk", 999)
        assert result is None

    def test_chunk_entity_returns_description_field(self):
        b = _backend()
        b.get_chunk.return_value = {"id": 1, "description": "Some text"}
        result = _read_description(b, "chunk", 1)
        assert result == "Some text"

    def test_non_chunk_entity_delegates_to_get_entity_description(self):
        b = _backend()
        b.get_entity_description.return_value = "Doc description"
        result = _read_description(b, "document", 5)
        assert result == "Doc description"


# ---------------------------------------------------------------------------
# remove_label — invalid entity_type (line 228)
# ---------------------------------------------------------------------------


class TestRemoveLabelErrorBranches:
    def test_invalid_entity_type_raises_value_error(self):
        """Line 228: entity_type not in _LABEL_ENTITY_TYPES → ValueError."""
        b = _backend()
        with pytest.raises(ValueError, match="entity_type"):
            writes.remove_label(b, _Ctx(), "widget", 1, "ns", "val")

    def test_valid_entity_types_accepted(self):
        """All three valid entity types work without raising."""
        for et in ("chunk", "document", "conversation"):
            b = _backend()
            b.revoke_label.return_value = False
            result = writes.remove_label(b, _Ctx(), et, 1, "ns", "val")
            assert "removed" in result


# ---------------------------------------------------------------------------
# set_metadata — invalid entity_type (line 290)
# ---------------------------------------------------------------------------


class TestSetMetadataErrorBranches:
    def test_invalid_entity_type_raises_value_error(self):
        """Line 290: entity_type not in _LABEL_ENTITY_TYPES → ValueError."""
        b = _backend()
        with pytest.raises(ValueError, match="entity_type"):
            writes.set_metadata(b, _Ctx(), "message", 1, "k", "v")

    def test_dry_run_skips_backend_patch(self):
        """dry_run=True: audit is emitted but patch_metadata is NOT called."""
        b = _backend()
        b.get_chunk.return_value = {"id": 1, "metadata": {}}
        # chunk entity uses get_chunk for read
        writes.set_metadata(b, _Ctx(), "chunk", 1, "k", "v", dry_run=True)
        b.patch_metadata.assert_not_called()
        b.audit_event.assert_called_once()


# ---------------------------------------------------------------------------
# set_description — invalid entity_type (line 351)
# ---------------------------------------------------------------------------


class TestSetDescriptionErrorBranches:
    def test_invalid_entity_type_raises_value_error(self):
        """Line 351: entity_type not in _LABEL_ENTITY_TYPES → ValueError."""
        b = _backend()
        with pytest.raises(ValueError, match="entity_type"):
            writes.set_description(b, _Ctx(), "message", 1, "text")

    def test_dry_run_skips_backend_set_description(self):
        """dry_run=True: audit emitted but backend.set_description NOT called."""
        b = _backend()
        b.get_entity_description.return_value = None
        writes.set_description(b, _Ctx(), "document", 1, "New", dry_run=True)
        b.set_description.assert_not_called()
        b.audit_event.assert_called_once()


# ---------------------------------------------------------------------------
# append_conversation — dataset not found (lines 439, 463-465)
# ---------------------------------------------------------------------------


class TestAppendConversationErrorBranches:
    def test_raises_when_dataset_not_found(self):
        """Lines 438-439: find_dataset_id_by_name returns None → ValueError."""
        b = _backend()
        b.find_dataset_id_by_name.return_value = None
        with pytest.raises(ValueError, match="not found"):
            writes.append_conversation(b, _Ctx(), "ghost-dataset", "T", [])

    def test_started_at_string_is_parsed_to_datetime(self):
        """Lines 463-465: started_at ISO string is converted to datetime."""
        b = _backend()
        b.find_dataset_id_by_name.return_value = 1
        b.append_conversation.return_value = (5, 1)
        writes.append_conversation(
            b,
            _Ctx(),
            "ds",
            "T",
            [{"role": "user", "content": "hi"}],
            started_at="2024-06-15T12:00:00",
        )
        b.append_conversation.assert_called_once()
        # Verify the first positional arg (dataset_id=1), second (title="T"),
        # and third arg is the parsed datetime (not the raw string)
        from datetime import datetime

        call_args = b.append_conversation.call_args[0]
        assert isinstance(call_args[2], datetime)

    def test_started_at_z_suffix_stripped(self):
        """Lines 463-465: trailing 'Z' in started_at is stripped before parsing."""
        b = _backend()
        b.find_dataset_id_by_name.return_value = 1
        b.append_conversation.return_value = (5, 1)
        # Should not raise — the Z is stripped
        writes.append_conversation(b, _Ctx(), "ds", "T", [], started_at="2024-06-15T12:00:00Z")

    def test_dry_run_does_not_call_backend_append_conversation(self):
        """dry_run=True skips backend.append_conversation."""
        b = _backend()
        b.find_dataset_id_by_name.return_value = 1
        writes.append_conversation(
            b, _Ctx(), "ds", "T", [{"role": "user", "content": "q"}], dry_run=True
        )
        b.append_conversation.assert_not_called()


# ---------------------------------------------------------------------------
# append_message — ts string parsing (lines 550-552)
# ---------------------------------------------------------------------------


class TestAppendMessageErrorBranches:
    def test_ts_string_is_parsed_to_datetime(self):
        """Lines 550-552: ts ISO string is converted to datetime before passing to backend."""
        b = _backend()
        b.count_messages.return_value = 0
        b.append_message.return_value = (1, 0)
        writes.append_message(b, _Ctx(), 1, "user", "hello", ts="2024-06-15T12:00:00")
        call_kwargs = b.append_message.call_args.kwargs
        from datetime import datetime

        assert isinstance(call_kwargs.get("ts"), datetime)

    def test_ts_z_suffix_stripped(self):
        """Trailing Z in ts is stripped before fromisoformat."""
        b = _backend()
        b.count_messages.return_value = 0
        b.append_message.return_value = (1, 0)
        # Should not raise
        writes.append_message(b, _Ctx(), 1, "user", "hello", ts="2024-06-15T12:00:00Z")

    def test_ts_none_passes_none_to_backend(self):
        """ts=None passes ts=None to backend.append_message."""
        b = _backend()
        b.count_messages.return_value = 0
        b.append_message.return_value = (1, 0)
        writes.append_message(b, _Ctx(), 1, "user", "hello", ts=None)
        call_kwargs = b.append_message.call_args.kwargs
        assert call_kwargs.get("ts") is None

    def test_dry_run_skips_backend_append_message(self):
        """dry_run=True: backend.append_message is NOT called."""
        b = _backend()
        b.count_messages.return_value = 1
        writes.append_message(b, _Ctx(), 1, "user", "dry", dry_run=True)
        b.append_message.assert_not_called()


# ---------------------------------------------------------------------------
# add_feedback — invalid entity_type (line 604)
# ---------------------------------------------------------------------------


class TestAddFeedbackErrorBranches:
    def test_invalid_entity_type_raises_value_error(self):
        """Line 604: entity_type not in _FEEDBACK_ENTITY_TYPES → ValueError."""
        b = _backend()
        with pytest.raises(ValueError, match="entity_type"):
            writes.add_feedback(b, _Ctx(), "widget", 1, "thumbs")

    def test_all_valid_feedback_entity_types_accepted(self):
        """chunk, document, conversation, message are all valid for add_feedback."""
        for et in ("chunk", "document", "conversation", "message"):
            b = _backend()
            result = writes.add_feedback(b, _Ctx(), et, 1, "thumbs", rating=1)
            assert "feedback_id" in result

    def test_dry_run_skips_backend_add_feedback(self):
        """dry_run=True: backend.add_feedback is NOT called."""
        b = _backend()
        writes.add_feedback(b, _Ctx(), "document", 1, "thumbs", dry_run=True)
        b.add_feedback.assert_not_called()
        b.audit_event.assert_called_once()


# ---------------------------------------------------------------------------
# _link_to_session — session_id=None short-circuit
# ---------------------------------------------------------------------------


class TestLinkToSession:
    def test_link_to_session_skips_when_session_id_none(self):
        """_link_to_session does nothing when ctx.session_id is None."""
        b = _backend()
        ctx = _Ctx(session_id=None)
        # add_label with no session_id: upsert_feedback_session should NOT be called
        b.apply_label.return_value = (1, True)
        writes.add_label(b, ctx, "document", 1, "ns", "val")
        b.upsert_feedback_session.assert_not_called()

    def test_link_to_session_calls_upsert_when_session_id_set(self):
        """_link_to_session calls upsert_feedback_session when ctx.session_id is set."""
        b = _backend()
        ctx = _Ctx(session_id="sess-abc")
        b.apply_label.return_value = (1, True)
        writes.add_label(b, ctx, "document", 1, "ns", "val")
        b.upsert_feedback_session.assert_called_once()
