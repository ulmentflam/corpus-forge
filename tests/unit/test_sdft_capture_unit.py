"""Unit tests for ``corpus_forge.sdft.capture`` helpers.

Integration tests already exercise the full MCP write tool path via the
real migration chain; these unit tests pin the pure helpers + the SQLite
dialect branch of ``record_demonstration`` against a minimal in-memory
schema so coverage of ``corpus_forge/sdft/capture.py`` is high.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from corpus_forge.sdft.capture import (
    _canonical_json,
    _content_hash,
    _should_capture_curation,
    record_demonstration,
)


def _seed_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE sdft_demonstrations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            student_messages TEXT NOT NULL,
            teacher_messages TEXT NOT NULL,
            target TEXT NOT NULL,
            source TEXT NOT NULL,
            trace_id TEXT,
            content_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    _seed_schema(c)
    return c


# ─────────────────────────────────────────────────────────────────────────────
# _canonical_json
# ─────────────────────────────────────────────────────────────────────────────


def test_canonical_json_is_deterministic_across_key_order() -> None:
    a = _canonical_json({"b": 1, "a": 2})
    b = _canonical_json({"a": 2, "b": 1})
    assert a == b


def test_canonical_json_no_whitespace() -> None:
    out = _canonical_json({"x": 1, "y": [1, 2]})
    assert " " not in out


def test_canonical_json_preserves_unicode() -> None:
    out = _canonical_json({"label": "漢字"})
    assert "漢字" in out


# ─────────────────────────────────────────────────────────────────────────────
# _content_hash
# ─────────────────────────────────────────────────────────────────────────────


def test_content_hash_is_deterministic() -> None:
    args = (
        "what is X?",
        [{"role": "user", "content": "q"}],
        [{"role": "assistant", "content": "a"}],
        "X is a thing",
    )
    assert _content_hash(*args) == _content_hash(*args)


def test_content_hash_differs_on_query_change() -> None:
    base_args = ("q", [], [], "t")
    other_args = ("q2", [], [], "t")
    assert _content_hash(*base_args) != _content_hash(*other_args)


def test_content_hash_hex_length() -> None:
    out = _content_hash("q", [], [], "t")
    assert len(out) == 64  # sha256 hex = 64 chars
    assert all(c in "0123456789abcdef" for c in out)


# ─────────────────────────────────────────────────────────────────────────────
# _should_capture_curation
# ─────────────────────────────────────────────────────────────────────────────


def test_should_capture_curation_false_when_either_none() -> None:
    assert _should_capture_curation(None, "x") is False
    assert _should_capture_curation("x", None) is False
    assert _should_capture_curation(None, None) is False


def test_should_capture_curation_false_when_equal() -> None:
    assert _should_capture_curation("same text", "same text") is False


def test_should_capture_curation_false_when_only_whitespace_differs() -> None:
    assert _should_capture_curation("  text  ", "text") is False


def test_should_capture_curation_true_on_meaningful_change() -> None:
    assert _should_capture_curation("old text", "brand new text") is True


# ─────────────────────────────────────────────────────────────────────────────
# record_demonstration — SQLite path (insert + dedup)
# ─────────────────────────────────────────────────────────────────────────────


def test_record_demonstration_inserts_row(conn: sqlite3.Connection) -> None:
    out = record_demonstration(
        conn,
        query="what is X?",
        student_messages=[{"role": "user", "content": "q"}],
        teacher_messages=[{"role": "assistant", "content": "a"}],
        target="X is a thing",
        source="cli_feedback",
        dataset_id=1,
    )
    assert out["deduped"] is False
    assert isinstance(out["demonstration_id"], int)
    # Row exists.
    row = conn.execute(
        "SELECT query, target, source FROM sdft_demonstrations WHERE id = ?",
        (out["demonstration_id"],),
    ).fetchone()
    assert row == ("what is X?", "X is a thing", "cli_feedback")


def test_record_demonstration_dedupes_identical_payload(conn: sqlite3.Connection) -> None:
    args = {
        "query": "q",
        "student_messages": [{"role": "user", "content": "s"}],
        "teacher_messages": [{"role": "assistant", "content": "t"}],
        "target": "target",
        "source": "cli_feedback",
        "dataset_id": 1,
    }
    first = record_demonstration(conn, **args)
    second = record_demonstration(conn, **args)

    assert first["deduped"] is False
    assert second["deduped"] is True
    assert first["demonstration_id"] == second["demonstration_id"]
    # Only one row exists.
    count = conn.execute("SELECT COUNT(*) FROM sdft_demonstrations").fetchone()[0]
    assert count == 1


def test_record_demonstration_persists_trace_id(conn: sqlite3.Connection) -> None:
    out = record_demonstration(
        conn,
        query="q",
        student_messages=[],
        teacher_messages=[],
        target="t",
        source="cli_feedback",
        dataset_id=1,
        trace_id="trace-abc-001",
    )
    row = conn.execute(
        "SELECT trace_id FROM sdft_demonstrations WHERE id = ?",
        (out["demonstration_id"],),
    ).fetchone()
    assert row[0] == "trace-abc-001"


def test_record_demonstration_messages_round_trip_as_json(conn: sqlite3.Connection) -> None:
    student = [{"role": "user", "content": "ask"}]
    teacher = [{"role": "assistant", "content": "answer"}]
    out = record_demonstration(
        conn,
        query="q",
        student_messages=student,
        teacher_messages=teacher,
        target="t",
        source="cli_feedback",
        dataset_id=1,
    )
    row = conn.execute(
        "SELECT student_messages, teacher_messages FROM sdft_demonstrations WHERE id = ?",
        (out["demonstration_id"],),
    ).fetchone()
    assert json.loads(row[0]) == student
    assert json.loads(row[1]) == teacher
