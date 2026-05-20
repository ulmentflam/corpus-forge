"""Unit tests for ``corpus_forge.mcp.writes.record_demonstration``.

The full MCP-tool flow is exercised by
``tests/integration/test_mcp_record_demonstration.py`` end-to-end against a
real SQLite migration chain.  This file isolates the validation paths
(invalid source, dataset/dataset_id contract, find-by-name lookup) so they
contribute to unit-suite coverage too.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from corpus_forge.mcp.writes import record_demonstration


class _Ctx:
    host = "test-host"
    client = "claude_code"
    session_id = "test-session"


class _FakeBackend:
    """Minimal duck-typed backend for record_demonstration unit tests."""

    def __init__(self, *, dataset_id: int | None = 1, audit_returns: int = 99) -> None:
        self._dataset_id = dataset_id
        self._audit_returns = audit_returns
        self.audit_calls: list[tuple[Any, ...]] = []
        # In-memory SQLite for the underlying _record path.
        self._conn = sqlite3.connect(":memory:")
        self._conn.execute(
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
        self._conn.commit()

    def find_dataset_id_by_name(self, name: str) -> int | None:
        return self._dataset_id

    def _get_connection(self) -> Any:
        class _Cx:
            def __init__(self, conn: sqlite3.Connection) -> None:
                self._conn = conn

            def __enter__(self) -> sqlite3.Connection:
                return self._conn

            def __exit__(self, *_a: Any) -> None:
                return None

        return _Cx(self._conn)

    def audit_event(self, *args: Any) -> int:
        self.audit_calls.append(args)
        return self._audit_returns


@pytest.fixture
def backend() -> _FakeBackend:
    return _FakeBackend()


def test_record_demonstration_rejects_invalid_source(backend: _FakeBackend) -> None:
    with pytest.raises(ValueError, match="not a valid SDFTSource"):
        record_demonstration(
            backend,
            _Ctx(),
            query="q",
            student_messages=[],
            teacher_messages=[],
            target="t",
            source="__not_a_source__",
            dataset="demo",
        )


def test_record_demonstration_rejects_both_dataset_and_dataset_id(
    backend: _FakeBackend,
) -> None:
    with pytest.raises(ValueError, match="exactly one of dataset or dataset_id"):
        record_demonstration(
            backend,
            _Ctx(),
            query="q",
            student_messages=[],
            teacher_messages=[],
            target="t",
            source="cli_feedback",
            dataset="demo",
            dataset_id=99,
        )


def test_record_demonstration_rejects_neither_dataset_nor_dataset_id(
    backend: _FakeBackend,
) -> None:
    with pytest.raises(ValueError, match="exactly one of dataset or dataset_id"):
        record_demonstration(
            backend,
            _Ctx(),
            query="q",
            student_messages=[],
            teacher_messages=[],
            target="t",
            source="cli_feedback",
        )


def test_record_demonstration_unknown_dataset_name_raises() -> None:
    backend = _FakeBackend(dataset_id=None)  # find_dataset_id_by_name → None
    with pytest.raises(ValueError, match="dataset 'missing' not found"):
        record_demonstration(
            backend,
            _Ctx(),
            query="q",
            student_messages=[],
            teacher_messages=[],
            target="t",
            source="cli_feedback",
            dataset="missing",
        )


def test_record_demonstration_with_dataset_name_round_trips(
    backend: _FakeBackend,
) -> None:
    out = record_demonstration(
        backend,
        _Ctx(),
        query="q",
        student_messages=[{"role": "user", "content": "s"}],
        teacher_messages=[{"role": "assistant", "content": "t"}],
        target="the answer",
        source="cli_feedback",
        dataset="demo",
    )
    assert out["deduped"] is False
    assert isinstance(out["demonstration_id"], int)
    assert out["audit_id"] == 99
    # Audit was called with the right tool name + entity_type.
    assert backend.audit_calls
    args = backend.audit_calls[0]
    assert args[3] == "record_demonstration"  # tool name


def test_record_demonstration_with_dataset_id_skips_lookup(
    backend: _FakeBackend,
) -> None:
    out = record_demonstration(
        backend,
        _Ctx(),
        query="q",
        student_messages=[],
        teacher_messages=[],
        target="t",
        source="record_demonstration",
        dataset_id=7,
    )
    assert out["deduped"] is False
    # The audit row got the explicit dataset_id, not the resolved one.
    # audit_event positional args: (host, client, session_id, tool, entity_type,
    #   entity_id, args_json, metadata_dict, errored). Metadata is at index 7.
    args = backend.audit_calls[0]
    audit_metadata = next(a for a in args if isinstance(a, dict))
    assert audit_metadata["dataset_id"] == 7


def test_record_demonstration_returns_dedup_on_second_identical_call(
    backend: _FakeBackend,
) -> None:
    kwargs = {
        "query": "q",
        "student_messages": [],
        "teacher_messages": [],
        "target": "t",
        "source": "cli_feedback",
        "dataset": "demo",
    }
    first = record_demonstration(backend, _Ctx(), **kwargs)
    second = record_demonstration(backend, _Ctx(), **kwargs)
    assert first["deduped"] is False
    assert second["deduped"] is True
    assert first["demonstration_id"] == second["demonstration_id"]
