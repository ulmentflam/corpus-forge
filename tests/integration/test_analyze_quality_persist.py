"""Phase O Wave 3 (O3-T2) — Integration tests for persist_quality_signals.

Tests that ``persist_quality_signals(conn, chunk_ids, scores, *, source)``
writes (or skips on conflict) rows into ``chunk_quality_signals`` and returns
the count of rows actually inserted.

Contract pinned here
--------------------
- Function signature::

      persist_quality_signals(
          conn,
          chunk_ids: list[int],
          scores: list[float],
          *,
          source: str = "heuristic_v1",
      ) -> int

- Signal name written to the table: ``"learned_quality"`` (fixed).
- Idempotency strategy: **WHERE NOT EXISTS** (a SELECT-before-INSERT guard) or
  equivalently INSERT ... ON CONFLICT DO NOTHING keyed on a unique index over
  ``(chunk_id, signal_name, source)``.  The implementation may choose either;
  the behaviour is: re-running with the same ``(chunk_id, signal_name, source)``
  triple does NOT double-insert, and the second call returns 0 for those rows.
  This file pins the observable outcome (no double rows) rather than the
  specific SQL pattern.
- Returns the count of rows inserted (not updated, not total in table).
- ``source`` defaults to ``"heuristic_v1"``.
- ``computed_at`` is populated by the server-side default.

RED condition
-------------
``corpus_forge.analyze.quality`` does not exist yet.  Every test fails with::

    ModuleNotFoundError: No module named 'corpus_forge.analyze.quality'

That is the expected RED state for O3-T2.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import pytest

if TYPE_CHECKING:
    import psycopg


class _CqsRow(TypedDict):
    """Row shape returned by ``_sqlite_cqs_rows`` / ``_pg_cqs_rows``."""

    id: int
    chunk_id: int
    signal_name: str
    signal_value: float
    source: str
    computed_at: object


pytestmark = [pytest.mark.integration]

# ---------------------------------------------------------------------------
# Module-level paths (mirrors test_analyze_dedup_persist.py)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_TARGET_REVISION = "0012_analyze_signals"

# ---------------------------------------------------------------------------
# Shared alembic / SQLite helpers
# ---------------------------------------------------------------------------


def _alembic_upgrade_sqlite(db_path: Path, target: str) -> None:
    """Run alembic upgrade against a SQLite db_path."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, target)


def _sqlite_row_count(conn: sqlite3.Connection, table: str, **where: object) -> int:
    """Row count from *table* filtered by kwargs."""
    clauses = " AND ".join(f"{col} = ?" for col in where)
    sql = f"SELECT COUNT(*) FROM {table}"
    if clauses:
        sql += f" WHERE {clauses}"
    return conn.execute(sql, tuple(where.values())).fetchone()[0]


def _sqlite_cqs_rows(conn: sqlite3.Connection) -> list[_CqsRow]:
    """Fetch all chunk_quality_signals rows as dicts."""
    rows = conn.execute(
        "SELECT id, chunk_id, signal_name, signal_value, source, computed_at "
        "FROM chunk_quality_signals ORDER BY id"
    ).fetchall()
    return [
        _CqsRow(
            id=int(r[0]),
            chunk_id=int(r[1]),
            signal_name=str(r[2]),
            signal_value=float(r[3]),
            source=str(r[4]),
            computed_at=r[5],
        )
        for r in rows
    ]


def _seed_chunks_sqlite(conn: sqlite3.Connection, chunk_ids: list[int]) -> None:
    """Insert minimal chunks rows for FK satisfaction (SQLite)."""
    conn.execute("PRAGMA foreign_keys = ON")
    for cid in chunk_ids:
        conn.execute(
            "INSERT OR IGNORE INTO chunks (id, chunk_index, text, content_hash, token_count) "
            "VALUES (?, 0, 'quality test text', ?, 20)",
            (cid, f"qhash_{cid}"),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Import helper
# ---------------------------------------------------------------------------


def _import_persist_quality_signals():
    """Import persist_quality_signals.

    Raises ModuleNotFoundError (the RED state) when quality.py is absent.
    """
    from corpus_forge.analyze.quality import persist_quality_signals

    return persist_quality_signals


# ---------------------------------------------------------------------------
# SQLite test class
# ---------------------------------------------------------------------------


class TestPersistQualitySignalsSQLite:
    """Functional tests for persist_quality_signals against a fresh SQLite DB.

    Each test provisions its own DB via tmp_path for full isolation.
    No Docker required.
    """

    def _fresh_db(self, tmp_path: Path, name: str) -> tuple[Path, sqlite3.Connection]:
        """Upgrade to 0012 and return an open sqlite3.Connection."""
        db_path = tmp_path / name
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        return db_path, conn

    # ── T1: empty inputs return 0 ────────────────────────────────────────

    def test_empty_inputs_return_zero(self, tmp_path: Path) -> None:
        """persist_quality_signals(conn, [], []) must return 0 and insert nothing."""
        persist = _import_persist_quality_signals()
        _, conn = self._fresh_db(tmp_path, "empty.db")

        result = persist(conn, [], [])
        assert result == 0, f"Expected 0 for empty inputs, got {result}"
        assert _sqlite_row_count(conn, "chunk_quality_signals") == 0

    # ── T2: single chunk inserts one row ─────────────────────────────────

    def test_single_chunk_inserts_one_row(self, tmp_path: Path) -> None:
        """A single (chunk_id, score) pair produces exactly one row."""
        persist = _import_persist_quality_signals()
        _, conn = self._fresh_db(tmp_path, "single.db")
        _seed_chunks_sqlite(conn, [1001])

        result = persist(conn, [1001], [0.75])
        assert result == 1, f"Expected 1 row inserted, got {result}"

        rows = _sqlite_cqs_rows(conn)
        assert len(rows) == 1
        assert rows[0]["chunk_id"] == 1001
        assert rows[0]["signal_name"] == "learned_quality"
        assert abs(rows[0]["signal_value"] - 0.75) < 1e-5
        assert rows[0]["source"] == "heuristic_v1"

    # ── T3: multiple chunks insert correct count ──────────────────────────

    def test_multiple_chunks_insert_correct_count(self, tmp_path: Path) -> None:
        """Five (chunk_id, score) pairs produce five rows."""
        persist = _import_persist_quality_signals()
        _, conn = self._fresh_db(tmp_path, "multi.db")
        chunk_ids = [2001, 2002, 2003, 2004, 2005]
        scores = [0.1, 0.3, 0.5, 0.7, 0.9]
        _seed_chunks_sqlite(conn, chunk_ids)

        result = persist(conn, chunk_ids, scores)
        assert result == 5, f"Expected 5 rows inserted, got {result}"

        rows = _sqlite_cqs_rows(conn)
        assert len(rows) == 5
        inserted_chunk_ids = {r["chunk_id"] for r in rows}
        assert inserted_chunk_ids == set(chunk_ids)

    # ── T4: signal_name is always "learned_quality" ───────────────────────

    def test_signal_name_is_learned_quality(self, tmp_path: Path) -> None:
        """Every row must have signal_name == 'learned_quality'."""
        persist = _import_persist_quality_signals()
        _, conn = self._fresh_db(tmp_path, "signal_name.db")
        _seed_chunks_sqlite(conn, [3001, 3002])

        persist(conn, [3001, 3002], [0.4, 0.6])
        rows = _sqlite_cqs_rows(conn)
        for row in rows:
            assert row["signal_name"] == "learned_quality", (
                f"Expected signal_name='learned_quality', got {row['signal_name']!r}"
            )

    # ── T5: idempotency — re-run with same triple does not double-insert ──

    def test_idempotent_rerun_does_not_double_insert(self, tmp_path: Path) -> None:
        """Second call with same (chunk_id, signal_name, source) must not add rows.

        The idempotency strategy (WHERE NOT EXISTS or ON CONFLICT DO NOTHING)
        means the second call returns 0 for already-existing rows, and the
        total row count stays the same.
        """
        persist = _import_persist_quality_signals()
        _, conn = self._fresh_db(tmp_path, "idempotent.db")
        _seed_chunks_sqlite(conn, [4001, 4002])

        first = persist(conn, [4001, 4002], [0.5, 0.6])
        assert first == 2, f"First call: expected 2 rows, got {first}"

        second = persist(conn, [4001, 4002], [0.5, 0.6])
        assert second == 0, (
            f"Second call with same inputs must return 0 (idempotent). "
            f"Got {second}, meaning rows were double-inserted."
        )

        total = _sqlite_row_count(conn, "chunk_quality_signals")
        assert total == 2, f"Row count after two calls must still be 2, got {total}"

    # ── T6: idempotency — row count stays stable on re-run ───────────────

    def test_idempotent_row_count_stable(self, tmp_path: Path) -> None:
        """After three calls with the same data, row count equals number of chunks."""
        persist = _import_persist_quality_signals()
        _, conn = self._fresh_db(tmp_path, "idempotent_count.db")
        _seed_chunks_sqlite(conn, [5001, 5002, 5003])

        chunk_ids = [5001, 5002, 5003]
        scores = [0.2, 0.4, 0.8]

        for _ in range(3):
            persist(conn, chunk_ids, scores)

        total = _sqlite_row_count(conn, "chunk_quality_signals")
        assert total == 3, f"After 3 calls with same data, row count must be 3, got {total}"

    # ── T7: source kwarg is written to every row ──────────────────────────

    def test_custom_source_is_stored(self, tmp_path: Path) -> None:
        """The ``source`` kwarg is stored on every inserted row."""
        persist = _import_persist_quality_signals()
        _, conn = self._fresh_db(tmp_path, "source_kwarg.db")
        _seed_chunks_sqlite(conn, [6001, 6002])

        persist(conn, [6001, 6002], [0.3, 0.7], source="model_v2")
        rows = _sqlite_cqs_rows(conn)
        for row in rows:
            assert row["source"] == "model_v2", f"Expected source='model_v2', got {row['source']!r}"

    # ── T8: default source is "heuristic_v1" ─────────────────────────────

    def test_default_source_is_heuristic_v1(self, tmp_path: Path) -> None:
        """Calling without a source kwarg stores 'heuristic_v1'."""
        persist = _import_persist_quality_signals()
        _, conn = self._fresh_db(tmp_path, "default_source.db")
        _seed_chunks_sqlite(conn, [7001])

        persist(conn, [7001], [0.55])
        rows = _sqlite_cqs_rows(conn)
        assert rows[0]["source"] == "heuristic_v1", (
            f"Default source must be 'heuristic_v1', got {rows[0]['source']!r}"
        )

    # ── T9: score round-trip within float32 tolerance ────────────────────

    def test_score_roundtrip_within_float32_tolerance(self, tmp_path: Path) -> None:
        """signal_value round-trips through REAL column within float32 tolerance."""
        persist = _import_persist_quality_signals()
        _, conn = self._fresh_db(tmp_path, "score_precision.db")
        _seed_chunks_sqlite(conn, [8001])

        original_score = 0.876543210987654
        persist(conn, [8001], [original_score])

        rows = _sqlite_cqs_rows(conn)
        assert len(rows) == 1
        stored = rows[0]["signal_value"]
        assert abs(stored - original_score) < 1e-5, (
            f"Score round-trip failed: stored {stored!r}, "
            f"original {original_score!r}, "
            f"delta {abs(stored - original_score)}"
        )

    # ── T10: computed_at is populated by server default ───────────────────

    def test_computed_at_is_populated(self, tmp_path: Path) -> None:
        """Every inserted row must have a non-NULL computed_at."""
        persist = _import_persist_quality_signals()
        _, conn = self._fresh_db(tmp_path, "computed_at.db")
        _seed_chunks_sqlite(conn, [9001])

        persist(conn, [9001], [0.42])
        rows = _sqlite_cqs_rows(conn)
        assert len(rows) == 1
        assert rows[0]["computed_at"] is not None, "computed_at must be populated by server default"
        assert isinstance(rows[0]["computed_at"], str)
        assert len(rows[0]["computed_at"]) > 0

    # ── T11: return value equals rows inserted ─────────────────────────────

    def test_return_value_equals_rows_inserted(self, tmp_path: Path) -> None:
        """Return value matches the actual number of rows in the table."""
        persist = _import_persist_quality_signals()
        _, conn = self._fresh_db(tmp_path, "return_value.db")
        _seed_chunks_sqlite(conn, [10001, 10002, 10003, 10004])

        returned = persist(conn, [10001, 10002, 10003, 10004], [0.1, 0.2, 0.3, 0.4])
        actual = _sqlite_row_count(conn, "chunk_quality_signals")
        assert returned == actual, f"Return value {returned} != actual DB row count {actual}"
        assert returned == 4

    # ── T12: partial idempotency — new chunk_ids are inserted, old are skipped ─

    def test_partial_idempotency_new_ids_inserted(self, tmp_path: Path) -> None:
        """On second call with a mix of old and new chunk_ids, only new ones count.

        First call: [11001, 11002] → 2 rows.
        Second call: [11001, 11003] → 1 new row (11001 is skipped, 11003 is new).
        Return value for second call: 1.
        Total rows: 3.
        """
        persist = _import_persist_quality_signals()
        _, conn = self._fresh_db(tmp_path, "partial_idempotency.db")
        _seed_chunks_sqlite(conn, [11001, 11002, 11003])

        first = persist(conn, [11001, 11002], [0.5, 0.6])
        assert first == 2

        second = persist(conn, [11001, 11003], [0.5, 0.7])
        assert second == 1, f"Second call with 1 new chunk_id must return 1, got {second}"

        total = _sqlite_row_count(conn, "chunk_quality_signals")
        assert total == 3, f"Total rows after partial insert must be 3, got {total}"


# ---------------------------------------------------------------------------
# Postgres test class (requires Docker)
# ---------------------------------------------------------------------------


def _sa_dsn(dsn: str) -> str:
    """Convert ``postgresql://`` → ``postgresql+psycopg://`` for Alembic."""
    return re.sub(r"^postgresql(s?)://", r"postgresql+psycopg\1://", dsn)


def _alembic_upgrade_pg(dsn: str, target: str) -> None:
    """Run alembic upgrade against Postgres."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )
    cfg.set_main_option("sqlalchemy.url", _sa_dsn(dsn))
    command.upgrade(cfg, target)


def _reset_pg_schema(dsn: str) -> None:
    """Drop and recreate the corpus schema + pgvector extension."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS corpus CASCADE")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE SCHEMA IF NOT EXISTS corpus")


def _pg_cqs_rows(conn: psycopg.Connection) -> list[_CqsRow]:
    """Fetch all chunk_quality_signals rows from Postgres."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, chunk_id, signal_name, signal_value, source, computed_at "
            "FROM corpus.chunk_quality_signals ORDER BY id"
        )
        rows = cur.fetchall()
    return [
        _CqsRow(
            id=int(r[0]),
            chunk_id=int(r[1]),
            signal_name=str(r[2]),
            signal_value=float(r[3]),
            source=str(r[4]),
            computed_at=r[5],
        )
        for r in rows
    ]


def _pg_seed_chunks(conn: psycopg.Connection, chunk_ids: list[int]) -> None:
    """Insert minimal corpus.chunks rows for FK satisfaction (Postgres).

    Follows the pattern established in test_analyze_dedup_persist.py's
    _pg_seed_chunks — seeds a dataset + document first because the
    chunks_check constraint requires document_id IS NOT NULL.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corpus.datasets (id, name, kind) "
            "VALUES (8801, 'quality_persist_fixture', 'text') ON CONFLICT (id) DO NOTHING"
        )
        cur.execute(
            "INSERT INTO corpus.documents "
            "(id, dataset_id, source_uri, content_hash, text) "
            "VALUES (8801, 8801, 'fixture://quality-persist', 'qp_doc_hash',"
            " 'quality persist fixture document') ON CONFLICT (id) DO NOTHING"
        )
        for cid in chunk_ids:
            cur.execute(
                "INSERT INTO corpus.chunks "
                "(id, document_id, chunk_index, text, content_hash, token_count) "
                "VALUES (%s, 8801, %s, 'quality test text', %s, 20) "
                "ON CONFLICT (id) DO NOTHING",
                (cid, cid, f"qhash_{cid}"),
            )
    conn.commit()


def _pg_row_count(conn: psycopg.Connection, table: str) -> int:
    """Return total row count for a table in corpus schema (Postgres)."""
    from psycopg import sql

    with conn.cursor() as cur:
        cur.execute(sql.SQL("SELECT COUNT(*) FROM corpus.{tbl}").format(tbl=sql.Identifier(table)))
        row = cur.fetchone()
        assert row is not None
        return int(row[0])


@pytest.mark.requires_docker
class TestPersistQualitySignalsPostgres:
    """Integration tests for persist_quality_signals against Postgres.

    Requires Docker + testcontainers. Uses the session-scoped ``pg_dsn``
    fixture from the root conftest. Each test resets and re-migrates for
    isolation (same pattern as TestPersistClustersPostgres).
    """

    def test_empty_inputs_return_zero(self, pg_dsn: str) -> None:
        """persist_quality_signals(conn, [], []) returns 0 (Postgres)."""
        import psycopg

        persist = _import_persist_quality_signals()
        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            result = persist(conn, [], [])
            assert result == 0
            assert _pg_row_count(conn, "chunk_quality_signals") == 0

    def test_single_chunk_inserts_one_row(self, pg_dsn: str) -> None:
        """Single chunk_id produces one row in chunk_quality_signals (Postgres)."""
        import psycopg

        persist = _import_persist_quality_signals()
        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            _pg_seed_chunks(conn, [1001])
            result = persist(conn, [1001], [0.75])
            assert result == 1

            rows = _pg_cqs_rows(conn)
            assert len(rows) == 1
            assert rows[0]["chunk_id"] == 1001
            assert rows[0]["signal_name"] == "learned_quality"
            assert abs(float(rows[0]["signal_value"]) - 0.75) < 1e-5
            assert rows[0]["source"] == "heuristic_v1"

    def test_idempotent_rerun_does_not_double_insert(self, pg_dsn: str) -> None:
        """Second call with same inputs returns 0 (Postgres ON CONFLICT DO NOTHING)."""
        import psycopg

        persist = _import_persist_quality_signals()
        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            _pg_seed_chunks(conn, [2001, 2002])
            first = persist(conn, [2001, 2002], [0.4, 0.6])
            assert first == 2

            second = persist(conn, [2001, 2002], [0.4, 0.6])
            assert second == 0, f"Second call must return 0 (idempotent). Got {second}."
            assert _pg_row_count(conn, "chunk_quality_signals") == 2

    def test_custom_source_stored(self, pg_dsn: str) -> None:
        """Custom source kwarg is stored on every row (Postgres)."""
        import psycopg

        persist = _import_persist_quality_signals()
        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            _pg_seed_chunks(conn, [3001])
            persist(conn, [3001], [0.88], source="trained_v1")

            rows = _pg_cqs_rows(conn)
            assert rows[0]["source"] == "trained_v1"

    def test_computed_at_is_populated(self, pg_dsn: str) -> None:
        """computed_at must be non-NULL for inserted rows (Postgres)."""
        import psycopg

        persist = _import_persist_quality_signals()
        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            _pg_seed_chunks(conn, [4001])
            persist(conn, [4001], [0.5])

            rows = _pg_cqs_rows(conn)
            assert rows[0]["computed_at"] is not None

    def test_score_roundtrip_within_tolerance(self, pg_dsn: str) -> None:
        """signal_value round-trips through Postgres REAL within float32 tolerance."""
        import psycopg

        persist = _import_persist_quality_signals()
        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        original_score = 0.823456789
        with psycopg.connect(pg_dsn) as conn:
            _pg_seed_chunks(conn, [5001])
            persist(conn, [5001], [original_score])

            rows = _pg_cqs_rows(conn)
            stored = float(rows[0]["signal_value"])
            # Postgres REAL is 4-byte float32; tolerance ~1e-5.
            assert abs(stored - original_score) < 1e-4, (
                f"Postgres REAL round-trip: stored {stored!r}, "
                f"original {original_score!r}, "
                f"delta {abs(stored - original_score)}"
            )
