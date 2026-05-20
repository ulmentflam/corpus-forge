"""Unit tests for ``corpus_forge.analyze.quality.persist_quality_signals``.

Covers the SQLite happy path, the validation guard, the rollback-on-error
path, and the Postgres-branch (with a duck-typed fake connection).  The
full migration-chain integration is exercised by
``tests/integration/test_analyze_quality_persist.py``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest

from corpus_forge.analyze.quality import persist_quality_signals


def _seed_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE chunk_quality_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id INTEGER NOT NULL,
            signal_name TEXT NOT NULL,
            signal_value REAL,
            source TEXT NOT NULL,
            computed_at TEXT NOT NULL DEFAULT (datetime('now'))
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
# SQLite branch
# ─────────────────────────────────────────────────────────────────────────────


def test_persist_empty_chunk_ids_returns_zero(conn: sqlite3.Connection) -> None:
    assert persist_quality_signals(conn, [], []) == 0


def test_persist_length_mismatch_raises(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="same length"):
        persist_quality_signals(conn, [1, 2], [0.5])


def test_persist_writes_rows(conn: sqlite3.Connection) -> None:
    n = persist_quality_signals(conn, [1, 2, 3], [0.1, 0.5, 0.9])
    assert n == 3
    rows = list(conn.execute("SELECT chunk_id, signal_value, source FROM chunk_quality_signals"))
    assert sorted(r[0] for r in rows) == [1, 2, 3]
    # Default source is "heuristic_v1".
    assert all(r[2] == "heuristic_v1" for r in rows)


def test_persist_idempotent_re_run_returns_zero(conn: sqlite3.Connection) -> None:
    persist_quality_signals(conn, [1, 2], [0.5, 0.7])
    n_second = persist_quality_signals(conn, [1, 2], [0.5, 0.7])
    assert n_second == 0
    count = conn.execute("SELECT COUNT(*) FROM chunk_quality_signals").fetchone()[0]
    assert count == 2


def test_persist_custom_source(conn: sqlite3.Connection) -> None:
    n = persist_quality_signals(conn, [1], [0.42], source="model_v2")
    assert n == 1
    row = conn.execute("SELECT source FROM chunk_quality_signals").fetchone()
    assert row[0] == "model_v2"


# ─────────────────────────────────────────────────────────────────────────────
# Postgres branch via fake conn
# ─────────────────────────────────────────────────────────────────────────────


class _FakeCursor:
    rowcount = 1

    def __init__(self, fail_at: int | None = None) -> None:
        self._fail_at = fail_at
        self._exec_count = 0
        self.executed: list[tuple[str, tuple]] = []

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, *_a: Any) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self._exec_count += 1
        if self._fail_at is not None and self._exec_count == self._fail_at:
            raise RuntimeError("simulated pg error")
        self.executed.append((sql, params))


class _FakePgConn:
    def __init__(self, fail_at: int | None = None) -> None:
        self._cur = _FakeCursor(fail_at=fail_at)
        self.commit_called = False
        self.rollback_called = False

    def cursor(self) -> _FakeCursor:
        return self._cur

    def commit(self) -> None:
        self.commit_called = True

    def rollback(self) -> None:
        self.rollback_called = True


def test_persist_postgres_branch_uses_pct_s() -> None:
    conn = _FakePgConn()
    n = persist_quality_signals(conn, [1, 2], [0.3, 0.7])
    assert n == 2
    assert conn.commit_called is True
    # Uses %s placeholders and corpus.chunk_quality_signals.
    sql, _params = conn._cur.executed[0]
    assert "corpus.chunk_quality_signals" in sql
    assert "%s" in sql


def test_persist_postgres_rollback_on_exception() -> None:
    conn = _FakePgConn(fail_at=2)
    with pytest.raises(RuntimeError, match="simulated pg error"):
        persist_quality_signals(conn, [1, 2, 3], [0.1, 0.2, 0.3])
    assert conn.rollback_called is True
    assert conn.commit_called is False


def test_persist_sqlite_rollback_on_exception(conn: sqlite3.Connection) -> None:
    """The SQLite branch's rollback path also fires under DB errors."""
    # Drop the table mid-flight to trigger an OperationalError on the next execute.
    conn.execute("DROP TABLE chunk_quality_signals")
    conn.commit()
    with pytest.raises(sqlite3.OperationalError):
        persist_quality_signals(conn, [1], [0.5])
