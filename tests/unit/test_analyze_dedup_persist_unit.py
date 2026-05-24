"""Unit tests for ``corpus_forge.analyze.dedup.persist_clusters``.

The integration suite (``tests/integration/test_analyze_dedup_persist.py``)
exercises this against a fully-migrated SQLite + Postgres backend, but
those tests don't count toward the unit-coverage gate.  This file uses a
minimal in-memory SQLite schema so the persist path is covered without
spinning up alembic or Docker.
"""

from __future__ import annotations

import sqlite3

import pytest

from corpus_forge.analyze.dedup import persist_clusters


def _make_schema(conn: sqlite3.Connection) -> None:
    """Create the minimal near_duplicate_clusters shape."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS near_duplicate_clusters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cluster_id TEXT NOT NULL,
            chunk_id INTEGER NOT NULL,
            similarity REAL,
            method TEXT NOT NULL,
            computed_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def _count(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM near_duplicate_clusters")
    return int(cur.fetchone()[0])


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    _make_schema(c)
    return c


def test_persist_empty_clusters_returns_zero(conn: sqlite3.Connection) -> None:
    n = persist_clusters(conn, [])
    assert n == 0
    assert _count(conn) == 0


def test_persist_single_cluster_writes_member_count(conn: sqlite3.Connection) -> None:
    clusters = [
        {"cluster_id": "ndc_abc", "chunk_ids": [1, 2, 3], "similarity": 0.92},
    ]
    n = persist_clusters(conn, clusters)
    assert n == 3
    assert _count(conn) == 3


def test_persist_default_method_is_minhash_lsh(conn: sqlite3.Connection) -> None:
    persist_clusters(conn, [{"cluster_id": "g", "chunk_ids": [10]}])
    rows = list(conn.execute("SELECT method FROM near_duplicate_clusters"))
    assert rows[0][0] == "minhash_lsh"


def test_persist_method_override(conn: sqlite3.Connection) -> None:
    persist_clusters(
        conn,
        [{"cluster_id": "g", "chunk_ids": [10]}],
        method="custom_simhash",
    )
    rows = list(conn.execute("SELECT method FROM near_duplicate_clusters"))
    assert rows[0][0] == "custom_simhash"


def test_persist_is_idempotent_on_second_run(conn: sqlite3.Connection) -> None:
    clusters = [{"cluster_id": "g1", "chunk_ids": [1, 2], "similarity": 0.8}]
    persist_clusters(conn, clusters)
    n_second = persist_clusters(conn, clusters)
    assert n_second == 0  # WHERE NOT EXISTS guard
    assert _count(conn) == 2


def test_persist_multiple_clusters(conn: sqlite3.Connection) -> None:
    clusters = [
        {"cluster_id": "g1", "chunk_ids": [1, 2], "similarity": 0.9},
        {"cluster_id": "g2", "chunk_ids": [3, 4, 5], "similarity": 0.85},
    ]
    n = persist_clusters(conn, clusters)
    assert n == 5
    assert _count(conn) == 5


def test_persist_missing_cluster_id_raises_keyerror(conn: sqlite3.Connection) -> None:
    with pytest.raises(KeyError):
        persist_clusters(conn, [{"chunk_ids": [1, 2]}])  # type: ignore[arg-type]


def test_persist_missing_chunk_ids_raises_keyerror(conn: sqlite3.Connection) -> None:
    with pytest.raises(KeyError):
        persist_clusters(conn, [{"cluster_id": "g"}])  # type: ignore[arg-type]


def test_persist_non_list_chunk_ids_raises_valueerror(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="must be a list"):
        persist_clusters(conn, [{"cluster_id": "g", "chunk_ids": "not-a-list"}])  # type: ignore[arg-type]


def test_persist_default_similarity_is_zero_when_missing(conn: sqlite3.Connection) -> None:
    persist_clusters(conn, [{"cluster_id": "g", "chunk_ids": [1]}])
    rows = list(conn.execute("SELECT similarity FROM near_duplicate_clusters"))
    assert rows[0][0] == 0.0


def test_persist_none_similarity_normalized_to_zero(conn: sqlite3.Connection) -> None:
    persist_clusters(
        conn,
        [{"cluster_id": "g", "chunk_ids": [1], "similarity": None}],
    )
    rows = list(conn.execute("SELECT similarity FROM near_duplicate_clusters"))
    assert rows[0][0] == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Postgres branch — exercised against a duck-typed fake connection so we
# don't need testcontainers for unit coverage. Real Postgres behavior is
# pinned by tests/integration/test_analyze_dedup_persist.py.
# ─────────────────────────────────────────────────────────────────────────────


class _FakeCursor:
    """A psycopg-shaped cursor: supports context-manager + .execute + .rowcount."""

    def __init__(self) -> None:
        self.rowcount = 1
        self.executed: list[tuple[str, tuple]] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_a: object) -> None:
        return None

    def execute(self, sql: str, params: tuple) -> None:
        self.executed.append((sql, params))


class _FakePgConn:
    """Non-sqlite3 connection so persist_clusters routes through the PG branch."""

    def __init__(self, fail_at: int | None = None) -> None:
        self._cur = _FakeCursor()
        self.commit_called = False
        self.rollback_called = False
        self._fail_at = fail_at  # raise after Nth execute (None = never)
        self._exec_count = 0

    def cursor(self) -> _FakeCursor:
        # Wrap execute to optionally fail.
        outer = self

        class _Cur(_FakeCursor):
            def execute(self, sql: str, params: tuple) -> None:
                outer._exec_count += 1
                if outer._fail_at is not None and outer._exec_count == outer._fail_at:
                    raise RuntimeError("simulated db error")
                outer._cur.executed.append((sql, params))

        c = _Cur()
        # Bridge state so the test can inspect.
        self._cur_proxy = c
        return c

    def commit(self) -> None:
        self.commit_called = True

    def rollback(self) -> None:
        self.rollback_called = True


def test_persist_postgres_branch_uses_pct_s_placeholders() -> None:
    conn = _FakePgConn()
    n = persist_clusters(
        conn,
        [{"cluster_id": "pg-1", "chunk_ids": [1, 2], "similarity": 0.7}],
    )
    assert n == 2
    assert conn.commit_called is True
    # SQL uses corpus. schema-prefix + %s placeholders.
    first_sql, _params = conn._cur.executed[0]
    assert "corpus.near_duplicate_clusters" in first_sql
    assert "%s" in first_sql


def test_persist_postgres_rollback_on_exception() -> None:
    # Fail on the 2nd execute → triggers rollback + re-raise.
    conn = _FakePgConn(fail_at=2)
    with pytest.raises(RuntimeError, match="simulated db error"):
        persist_clusters(
            conn,
            [{"cluster_id": "pg-rb", "chunk_ids": [1, 2, 3], "similarity": 0.6}],
        )
    assert conn.rollback_called is True
    assert conn.commit_called is False


def test_persist_postgres_validates_chunk_ids_list_type() -> None:
    conn = _FakePgConn()
    with pytest.raises(ValueError, match="must be a list"):
        persist_clusters(
            conn,
            [{"cluster_id": "g", "chunk_ids": "not-a-list"}],  # type: ignore[arg-type]
        )
    # Validation failure also rolls back (the Postgres branch wraps it
    # in the same try/except, so rollback fires even on the type check).
    assert conn.rollback_called is True
