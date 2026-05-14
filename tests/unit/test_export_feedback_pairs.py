"""H-04 RED — Unit tests for export_feedback_pairs.

Tests against an in-memory SQLiteBackend (migrated, includes revision
0008_feedback_sessions).  Seed: a feedback_session linked to a conversation
with 4 messages; 2 feedback_events (one with audit_id, one with feedback_id).

All tests FAIL RED because ``corpus_forge.export.export_feedback_pairs``
does not exist yet, and the backend helpers
``list_feedback_events_for_dataset``, ``get_audit_event``, ``get_feedback``,
``get_conversation_messages_up_to_ts`` are not implemented.

Run command:
    .venv/bin/python -m pytest tests/unit/test_export_feedback_pairs.py -v

pytestmark: pytest.mark.unit
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend

# ---------------------------------------------------------------------------
# Import the target function — will raise AttributeError → RED
# ---------------------------------------------------------------------------
from corpus_forge.export import export_feedback_pairs

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Required row schema keys
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = {
    "feedback_event_id",
    "feedback_session_id",
    "client",
    "session_id",
    "host",
    "prompt",
    "response",
    "after",
    "kind",
    "ts",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend() -> SQLiteBackend:
    b = SQLiteBackend(path=":memory:")
    b.migrate()
    return b


def _now_iso(offset_seconds: int = 0) -> str:
    return (datetime.now(UTC) + timedelta(seconds=offset_seconds)).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def _seed_base(backend: SQLiteBackend) -> dict[str, Any]:
    """Insert dataset + conversation + 4 messages.  Returns id mapping."""
    with backend._get_connection() as conn:
        ds_id = conn.execute(
            "INSERT INTO datasets (name, kind, description) VALUES (?, ?, ?) RETURNING id",
            ("h04-ds", "chat", "H-04 unit test dataset"),
        ).fetchone()[0]

        conv_id = conn.execute(
            "INSERT INTO conversations"
            " (dataset_id, source_uri, content_hash, title, message_count, metadata)"
            " VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (ds_id, "test://conv/h04", "hash-h04", "H-04 Conv", 0, "{}"),
        ).fetchone()[0]

        # 4 messages with distinct timestamps
        msg_ids = []
        for i in range(4):
            ts = _now_iso(-100 + i * 10)
            msg_id = conn.execute(
                "INSERT INTO messages"
                " (conversation_id, turn_index, role, content, ts)"
                " VALUES (?, ?, ?, ?, ?) RETURNING id",
                (conv_id, i, "user" if i % 2 == 0 else "assistant", f"msg content {i}", ts),
            ).fetchone()[0]
            msg_ids.append(msg_id)

        conn.commit()

    return {
        "dataset_id": ds_id,
        "dataset_name": "h04-ds",
        "conv_id": conv_id,
        "msg_ids": msg_ids,
    }


def _seed_feedback_session(backend: SQLiteBackend, dataset_name: str, conv_id: int | None) -> int:
    """Insert a feedback_session row; return its id."""
    fs_id = backend.upsert_feedback_session(
        client="claude-code",
        session_id="test-session-h04",
        host="test-host",
        started_at=_now_iso(-200),
    )
    if conv_id is not None:
        backend.link_feedback_session_to_conversation("claude-code", "test-session-h04", conv_id)
    return fs_id


def _seed_audit_event(backend: SQLiteBackend) -> int:
    return backend.audit_event(
        "test-host",
        "claude-code",
        "test-session-h04",
        "add_label",
        "conversation",
        1,
        {"before": "val"},
        {"after": "val"},
        False,
    )


def _seed_feedback_row(backend: SQLiteBackend, conv_id: int) -> int:
    return backend.add_feedback(
        "conversation",
        conv_id,
        kind="thumbs",
        rating=1,
        text="good",
    )


def _seed_full(backend: SQLiteBackend) -> dict[str, Any]:
    """Seed the full test scenario: dataset, conversation, session, 2 events."""
    ids = _seed_base(backend)
    fs_id = _seed_feedback_session(backend, ids["dataset_name"], ids["conv_id"])

    audit_id = _seed_audit_event(backend)
    feedback_id = _seed_feedback_row(backend, ids["conv_id"])

    ev1_id = backend.append_feedback_event(
        fs_id,
        audit_id=audit_id,
        feedback_id=None,
        entity_type="conversation",
        entity_id=ids["conv_id"],
    )
    ev2_id = backend.append_feedback_event(
        fs_id,
        audit_id=None,
        feedback_id=feedback_id,
        entity_type="conversation",
        entity_id=ids["conv_id"],
    )

    return {
        **ids,
        "fs_id": fs_id,
        "audit_id": audit_id,
        "feedback_id": feedback_id,
        "ev1_id": ev1_id,
        "ev2_id": ev2_id,
    }


# ===========================================================================
# Tests
# ===========================================================================


class TestExportFeedbackPairsJsonlEmitsRows:
    def test_export_feedback_pairs_jsonl_emits_one_row_per_event(self, tmp_path: Path) -> None:
        """With 2 feedback_events in the dataset, the JSONL output has exactly 2 lines."""
        backend = _make_backend()
        _seed_full(backend)
        out_path = tmp_path / "pairs.jsonl"

        export_feedback_pairs(
            dataset="h04-ds",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        assert out_path.exists(), "output file must be created"
        rows = _read_jsonl(out_path)
        assert len(rows) == 2, f"expected 2 rows (one per feedback_event); got {len(rows)}"

    def test_export_feedback_pairs_row_shape(self, tmp_path: Path) -> None:
        """Each output row contains all required keys."""
        backend = _make_backend()
        _seed_full(backend)
        out_path = tmp_path / "pairs.jsonl"

        export_feedback_pairs(
            dataset="h04-ds",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        rows = _read_jsonl(out_path)
        for row in rows:
            missing = _REQUIRED_KEYS - set(row.keys())
            assert not missing, f"Row missing required keys: {missing}. Row keys: {set(row.keys())}"

    def test_export_feedback_pairs_prompt_is_templated_conversation(self, tmp_path: Path) -> None:
        """The 'prompt' field contains the ChatML marker '<|im_start|>' for chatml template."""
        backend = _make_backend()
        _seed_full(backend)
        out_path = tmp_path / "pairs.jsonl"

        export_feedback_pairs(
            dataset="h04-ds",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        rows = _read_jsonl(out_path)
        assert rows, "expected at least one output row"
        for row in rows:
            assert "<|im_start|>" in row["prompt"], (
                f"Expected ChatML <|im_start|> in prompt for chatml template; "
                f"got: {row['prompt']!r}"
            )


class TestExportFeedbackPairsSkipUnlinked:
    def test_export_feedback_pairs_skips_unlinked_sessions(self, tmp_path: Path) -> None:
        """Events under a session with conversation_id IS NULL do NOT appear in output."""
        backend = _make_backend()
        # Seed the normal (linked) session with 2 events
        _seed_full(backend)

        # Seed a second session without a linked conversation
        fs_id2 = backend.upsert_feedback_session(
            client="claude-code",
            session_id="unlinked-session",
            host="test-host",
            started_at=_now_iso(-50),
        )
        # conversation_id stays NULL (no link_feedback_session_to_conversation call)
        audit_id2 = backend.audit_event(
            "test-host",
            "claude-code",
            "unlinked-session",
            "add_label",
            "conversation",
            1,
            {},
            {},
            False,
        )
        backend.append_feedback_event(
            fs_id2,
            audit_id=audit_id2,
            feedback_id=None,
            entity_type="conversation",
            entity_id=1,
        )

        out_path = tmp_path / "pairs.jsonl"
        export_feedback_pairs(
            dataset="h04-ds",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        rows = _read_jsonl(out_path)
        # Only the 2 linked events should appear
        assert len(rows) == 2, f"expected 2 rows (unlinked session events skipped); got {len(rows)}"
        # No row should reference the unlinked session
        for row in rows:
            assert row["session_id"] != "unlinked-session", (
                "unlinked session event must not appear in output"
            )


class TestExportFeedbackPairsResponseShape:
    def test_export_feedback_pairs_audit_event_response_shape(self, tmp_path: Path) -> None:
        """For a row with kind='audit', response has keys {tool, args}."""
        backend = _make_backend()
        _seed_full(backend)
        out_path = tmp_path / "pairs.jsonl"

        export_feedback_pairs(
            dataset="h04-ds",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        rows = _read_jsonl(out_path)
        audit_rows = [r for r in rows if r["kind"] == "audit"]
        assert audit_rows, "expected at least one audit-kind row"
        for row in audit_rows:
            resp = row["response"]
            assert "tool" in resp, f"audit response missing 'tool': {resp}"
            assert "args" in resp, f"audit response missing 'args': {resp}"

    def test_export_feedback_pairs_feedback_event_response_shape(self, tmp_path: Path) -> None:
        """For a row with kind='feedback', response has keys {kind, rating, text}."""
        backend = _make_backend()
        _seed_full(backend)
        out_path = tmp_path / "pairs.jsonl"

        export_feedback_pairs(
            dataset="h04-ds",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        rows = _read_jsonl(out_path)
        feedback_rows = [r for r in rows if r["kind"] == "feedback"]
        assert feedback_rows, "expected at least one feedback-kind row"
        for row in feedback_rows:
            resp = row["response"]
            assert "kind" in resp, f"feedback response missing 'kind': {resp}"
            assert "rating" in resp, f"feedback response missing 'rating': {resp}"
            assert "text" in resp, f"feedback response missing 'text': {resp}"


class TestExportFeedbackPairsCustomJinja:
    def test_export_feedback_pairs_with_custom_jinja(self, tmp_path: Path) -> None:
        """custom_jinja overrides the built-in template; prompt is rendered through it."""
        backend = _make_backend()
        _seed_full(backend)
        out_path = tmp_path / "pairs.jsonl"

        custom = "CUSTOM:{{ messages | length }}"
        export_feedback_pairs(
            dataset="h04-ds",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
            custom_jinja=custom,
        )

        rows = _read_jsonl(out_path)
        assert rows, "expected at least one row"
        for row in rows:
            assert row["prompt"].startswith("CUSTOM:"), (
                f"expected prompt to start with 'CUSTOM:' (custom_jinja applied); "
                f"got: {row['prompt']!r}"
            )


class TestExportFeedbackPairsEdgeCases:
    def test_export_feedback_pairs_no_events_writes_empty_file(self, tmp_path: Path) -> None:
        """With no feedback_events, output file exists but is empty (zero rows)."""
        backend = _make_backend()
        ids = _seed_base(backend)
        # Seed the session + link but add NO feedback_events
        _seed_feedback_session(backend, ids["dataset_name"], ids["conv_id"])

        out_path = tmp_path / "pairs.jsonl"
        export_feedback_pairs(
            dataset="h04-ds",
            template="chatml",
            out_path=out_path,
            format="jsonl",
            backend=backend,
        )

        assert out_path.exists(), "output file must exist even with zero events"
        rows = _read_jsonl(out_path)
        assert rows == [], f"expected 0 rows for empty event set; got {rows}"
