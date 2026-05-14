"""G-04 RED — Integration tests for export_chat JSONL writer.

End-to-end: seed a SQLite-in-memory corpus with real conversations, call
``corpus_forge.export.export_chat(..., format='jsonl')``, and assert the
output file's shape and content.

All tests FAIL RED because ``corpus_forge.export.export_chat`` doesn't exist.

Run command:
    .venv/bin/python -m pytest tests/integration/test_export_chat_jsonl.py -v

pytestmark: pytest.mark.integration
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Import the target function (will raise ImportError / AttributeError → RED)
# ---------------------------------------------------------------------------

from corpus_forge.export import export_chat  # noqa: E402  (must be after pytestmark)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _seed_dataset(backend: SQLiteBackend, name: str) -> int:
    """Insert a dataset row and return its id."""
    with backend._get_connection() as conn:
        ds_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            (name, "chat", "G-04 integration test dataset"),
        ).fetchone()[0]
        conn.commit()
    return ds_id


def _seed_conversations(
    backend: SQLiteBackend,
    dataset_id: int,
    count: int = 2,
    messages_per_conv: int = 3,
) -> list[int]:
    """Insert ``count`` conversations each with ``messages_per_conv`` messages.

    Returns list of conversation IDs.
    """
    roles = ["user", "assistant", "user", "assistant"]
    conv_ids: list[int] = []
    for i in range(count):
        messages = [
            {
                "role": roles[j % len(roles)],
                "content": f"G-04 seed conv={i} msg={j}",
            }
            for j in range(messages_per_conv)
        ]
        conv_id, _ = backend.append_conversation(
            dataset_id=dataset_id,
            title=f"G-04 conversation {i}",
            started_at=None,
            messages=messages,
        )
        conv_ids.append(conv_id)
    return conv_ids


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file; each line must be valid JSON."""
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


# ---------------------------------------------------------------------------
# Required row schema keys
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = {
    "conversation_id",
    "title",
    "source_uri",
    "description",
    "template",
    "model_id",
    "text",
    "message_count",
    "messages",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestExportChatJsonlRoundTrip:
    def test_export_chat_jsonl_round_trip(self, tmp_path: Path) -> None:
        """Seed 2 conversations x 3 messages each; export to JSONL; assert schema."""
        backend = _make_backend()
        ds_id = _seed_dataset(backend, "test-export-jsonl")
        _seed_conversations(backend, ds_id, count=2, messages_per_conv=3)

        out_path = tmp_path / "out.jsonl"
        export_chat(
            dataset="test-export-jsonl",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        assert out_path.exists(), "JSONL output file must exist after export_chat"

        rows = _read_jsonl(out_path)
        assert len(rows) == 2, f"Expected 2 rows (one per conversation); got {len(rows)}"

        for row in rows:
            missing = _REQUIRED_KEYS - set(row.keys())
            assert not missing, f"Row missing required keys: {missing}. Row keys: {set(row.keys())}"

        # ChatML marker must appear in rendered text
        for row in rows:
            assert "<|im_start|>" in row["text"], (
                f"Expected ChatML <|im_start|> in row text for chatml template; "
                f"got: {row['text']!r}"
            )

    def test_export_chat_jsonl_message_count_is_correct(self, tmp_path: Path) -> None:
        """Each row's message_count matches the seeded message count."""
        backend = _make_backend()
        ds_id = _seed_dataset(backend, "test-export-msgcount")
        _seed_conversations(backend, ds_id, count=1, messages_per_conv=4)

        out_path = tmp_path / "out.jsonl"
        export_chat(
            dataset="test-export-msgcount",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        rows = _read_jsonl(out_path)
        assert len(rows) == 1
        assert rows[0]["message_count"] == 4, (
            f"Expected message_count=4; got {rows[0]['message_count']}"
        )

    def test_export_chat_jsonl_messages_field_has_role_and_content(self, tmp_path: Path) -> None:
        """The 'messages' list in each row contains dicts with 'role' and 'content'."""
        backend = _make_backend()
        ds_id = _seed_dataset(backend, "test-export-msgfield")
        _seed_conversations(backend, ds_id, count=1, messages_per_conv=2)

        out_path = tmp_path / "out.jsonl"
        export_chat(
            dataset="test-export-msgfield",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        rows = _read_jsonl(out_path)
        assert len(rows) == 1
        messages = rows[0]["messages"]
        assert isinstance(messages, list), f"'messages' must be a list; got {type(messages)}"
        assert len(messages) == 2
        for msg in messages:
            assert "role" in msg, f"Message dict missing 'role': {msg}"
            assert "content" in msg, f"Message dict missing 'content': {msg}"

    def test_export_chat_jsonl_template_field_matches_input(self, tmp_path: Path) -> None:
        """The 'template' field in each output row matches the template argument."""
        backend = _make_backend()
        ds_id = _seed_dataset(backend, "test-export-tmplfield")
        _seed_conversations(backend, ds_id, count=1, messages_per_conv=2)

        out_path = tmp_path / "out.jsonl"
        export_chat(
            dataset="test-export-tmplfield",
            template="llama3",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        rows = _read_jsonl(out_path)
        assert rows[0]["template"] == "llama3", (
            f"Expected template='llama3' in row; got {rows[0]['template']!r}"
        )


class TestExportChatJsonlEdgeCases:
    def test_export_chat_jsonl_skips_conversations_with_zero_messages(self, tmp_path: Path) -> None:
        """Conversations with zero messages are excluded from the export."""
        backend = _make_backend()
        ds_id = _seed_dataset(backend, "test-export-empty-conv")

        # Seed one real conversation (2 messages)
        _seed_conversations(backend, ds_id, count=1, messages_per_conv=2)

        # Insert a conversation row with zero messages directly (no append_conversation call)
        with backend._get_connection() as conn:
            import hashlib
            import uuid

            src = f"append://{uuid.uuid4()}"
            chash = hashlib.sha256(src.encode()).hexdigest()
            conn.execute(
                """
                INSERT INTO conversations
                  (dataset_id, source_uri, content_hash, title, started_at, message_count, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ds_id, src, chash, "Empty conversation", None, 0, "{}"),
            )
            conn.commit()

        out_path = tmp_path / "out.jsonl"
        export_chat(
            dataset="test-export-empty-conv",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        rows = _read_jsonl(out_path)
        # Only the conversation with messages should appear
        assert all(r["message_count"] > 0 for r in rows), (
            "Expected zero-message conversations to be excluded; "
            f"got rows: {[(r.get('title'), r.get('message_count')) for r in rows]}"
        )

    def test_export_chat_jsonl_with_custom_template_via_jinja(self, tmp_path: Path) -> None:
        """export_chat with custom_jinja renders each row's text as the jinja output.

        The template ``{{ messages | length }}`` should produce the message count
        as a string for each row.
        """
        backend = _make_backend()
        ds_id = _seed_dataset(backend, "test-export-custom-jinja")
        _seed_conversations(backend, ds_id, count=2, messages_per_conv=3)

        out_path = tmp_path / "out.jsonl"
        export_chat(
            dataset="test-export-custom-jinja",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
            custom_jinja="{{ messages | length }}",
        )

        rows = _read_jsonl(out_path)
        assert len(rows) == 2, f"Expected 2 rows; got {len(rows)}"
        for row in rows:
            # text should be the stringified message count
            assert row["text"] == "3", (
                f"Expected custom_jinja to render message count '3'; got {row['text']!r}"
            )

    def test_export_chat_jsonl_with_hf_model_id(self, tmp_path: Path) -> None:
        """export_chat with model_id uses hf_template; rows contain the HF-stub marker."""
        backend = _make_backend()
        ds_id = _seed_dataset(backend, "test-export-hf-model")
        _seed_conversations(backend, ds_id, count=1, messages_per_conv=2)

        stub_jinja = "<HF>{{ messages[0]['content'] }}</HF>"

        with patch("corpus_forge.templates.hf.hf_template", return_value=stub_jinja):
            out_path = tmp_path / "out.jsonl"
            export_chat(
                dataset="test-export-hf-model",
                template="chatml",
                out_path=out_path,
                format="jsonl",
                backend=backend,
                model_id="stub-model-id",
            )

        rows = _read_jsonl(out_path)
        assert len(rows) == 1
        assert "<HF>" in rows[0]["text"], (
            f"Expected HF stub marker <HF> in row text; got: {rows[0]['text']!r}"
        )


class TestExportChatJsonlBoundaries:
    def test_export_chat_jsonl_empty_dataset_produces_empty_file(self, tmp_path: Path) -> None:
        """A dataset with no conversations produces a valid (empty) output file."""
        backend = _make_backend()
        _seed_dataset(backend, "test-export-no-convs")

        out_path = tmp_path / "out.jsonl"
        export_chat(
            dataset="test-export-no-convs",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        # File should exist; may be empty or have zero non-blank lines
        assert out_path.exists(), "Output file must be created even for an empty dataset"
        rows = _read_jsonl(out_path)
        assert rows == [], f"Expected 0 rows for empty dataset; got {rows}"

    def test_export_chat_jsonl_conversation_id_is_int(self, tmp_path: Path) -> None:
        """conversation_id field in each row is an integer."""
        backend = _make_backend()
        ds_id = _seed_dataset(backend, "test-export-conv-id-type")
        _seed_conversations(backend, ds_id, count=1, messages_per_conv=2)

        out_path = tmp_path / "out.jsonl"
        export_chat(
            dataset="test-export-conv-id-type",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        rows = _read_jsonl(out_path)
        assert len(rows) == 1
        assert isinstance(rows[0]["conversation_id"], int), (
            f"Expected conversation_id to be int; got {type(rows[0]['conversation_id'])}"
        )

    def test_export_chat_jsonl_unknown_dataset_raises(self, tmp_path: Path) -> None:
        """Exporting from a non-existent dataset name raises a descriptive error."""
        backend = _make_backend()
        out_path = tmp_path / "out.jsonl"

        with pytest.raises((KeyError, ValueError, LookupError, RuntimeError)):
            export_chat(
                dataset="no-such-dataset",
                template="chatml",
                out_path=out_path,
                format="jsonl",
                backend=backend,
            )
