"""Integration tests for corpus_forge.analyze.dedup.persist_clusters.

Tests that ``persist_clusters(conn, clusters, *, method="minhash_lsh")`` writes
one row per ``(cluster_id, chunk_id)`` pair into the ``near_duplicate_clusters``
table (created by migration 0012_analyze_signals) and returns the count of rows
inserted.

Contract pinned here
--------------------
- ``clusters`` items shape: ``{"cluster_id": str, "chunk_ids": list[int],
  "similarity": float, "method": str}`` — the same shape produced by
  ``near_duplicates()`` from O2-T1.
- Idempotency strategy: **INSERT OR IGNORE** (SQLite) / ``ON CONFLICT DO
  NOTHING`` (Postgres) keyed on a unique index over ``(cluster_id, chunk_id)``.
  Calling twice with the same clusters does NOT double-insert.
- ``computed_at`` is populated by the server-side default (``datetime('now')``
  for SQLite; ``NOW()`` for Postgres) — the caller never supplies it.
- ``method`` parameter on the function call overrides the ``"method"`` field
  inside each cluster dict when the caller wants to tag rows with a different
  method label.

RED condition
-------------
``corpus_forge.analyze.dedup`` does not exist yet.  Every test in this module
fails with::

    ModuleNotFoundError: No module named 'corpus_forge.analyze.dedup'

That is the expected RED state for O2-T4.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.integration]

# ---------------------------------------------------------------------------
# Module-level paths (mirrors test_migrate_0012_analyze.py)
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_TARGET_REVISION = "0012_analyze_signals"

# ---------------------------------------------------------------------------
# Shared alembic/SQLite helpers (same pattern as test_migrate_0012_analyze.py)
# ---------------------------------------------------------------------------


def _alembic_upgrade_sqlite(db_path: Path, target: str) -> None:
    """Run ``alembic.command.upgrade(config, target)`` against a SQLite *db_path*."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, target)


def _sqlite_row_count(conn: sqlite3.Connection, table: str, **where: Any) -> int:
    """Return the row count from *table* filtered by the keyword args."""
    clauses = " AND ".join(f"{col} = ?" for col in where)
    sql = f"SELECT COUNT(*) FROM {table}"
    if clauses:
        sql += f" WHERE {clauses}"
    return conn.execute(sql, tuple(where.values())).fetchone()[0]


def _sqlite_ndc_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Fetch all near_duplicate_clusters rows as a list of dicts."""
    rows = conn.execute(
        "SELECT id, cluster_id, chunk_id, similarity, method, computed_at "
        "FROM near_duplicate_clusters ORDER BY id"
    ).fetchall()
    return [
        {
            "id": r[0],
            "cluster_id": r[1],
            "chunk_id": r[2],
            "similarity": r[3],
            "method": r[4],
            "computed_at": r[5],
        }
        for r in rows
    ]


def _seed_chunks(conn: sqlite3.Connection, chunk_ids: list[int]) -> None:
    """Insert minimal chunks rows into the chunks table for FK satisfaction."""
    conn.execute("PRAGMA foreign_keys = ON")
    for cid in chunk_ids:
        conn.execute(
            "INSERT OR IGNORE INTO chunks (id, chunk_index, text, content_hash, token_count) "
            "VALUES (?, 0, 'dummy text', ?, 10)",
            (cid, f"hash_{cid}"),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# import-under-test helper
# ---------------------------------------------------------------------------


def _import_persist_clusters():
    """Import persist_clusters from corpus_forge.analyze.dedup.

    Raises ModuleNotFoundError (the RED state) when the module does not exist.
    """
    from corpus_forge.analyze.dedup import persist_clusters

    return persist_clusters


# ---------------------------------------------------------------------------
# SQLite test class
# ---------------------------------------------------------------------------


class TestPersistClustersSQLite:
    """Functional tests for persist_clusters against a fresh SQLite database.

    Each test provisions its own database via tmp_path for full isolation.
    No Docker required.
    """

    # ── helper to get a ready DB + raw sqlite3 connection ───────────────

    def _fresh_db(self, tmp_path: Path, name: str) -> tuple[Path, sqlite3.Connection]:
        """Upgrade to 0012 and return an open sqlite3.Connection."""
        db_path = tmp_path / name
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys = ON")
        return db_path, conn

    # ── T1: empty cluster list returns 0 ────────────────────────────────

    def test_empty_cluster_list_returns_zero(self, tmp_path: Path) -> None:
        """persist_clusters([]) must return 0 and insert nothing."""
        persist_clusters = _import_persist_clusters()
        _, conn = self._fresh_db(tmp_path, "empty.db")

        result = persist_clusters(conn, [], method="minhash_lsh")

        assert result == 0, f"Expected 0 rows inserted for empty cluster list, got {result}"
        assert _sqlite_row_count(conn, "near_duplicate_clusters") == 0

    # ── T2: single cluster with two members inserts 2 rows ──────────────

    def test_single_cluster_two_members_inserts_two_rows(self, tmp_path: Path) -> None:
        """A single cluster with 2 chunk_ids must produce 2 rows in the table."""
        persist_clusters = _import_persist_clusters()
        _, conn = self._fresh_db(tmp_path, "single_two.db")
        _seed_chunks(conn, [101, 102])

        clusters = [
            {
                "cluster_id": "clust-aaa",
                "chunk_ids": [101, 102],
                "similarity": 0.92,
                "method": "minhash_lsh",
            },
        ]
        result = persist_clusters(conn, clusters, method="minhash_lsh")

        assert result == 2, f"Expected 2 rows inserted, got {result}"
        rows = _sqlite_ndc_rows(conn)
        assert len(rows) == 2
        chunk_ids_inserted = {r["chunk_id"] for r in rows}
        assert chunk_ids_inserted == {101, 102}
        for row in rows:
            assert row["cluster_id"] == "clust-aaa"
            assert row["method"] == "minhash_lsh"

    # ── T3: three clusters with mixed sizes inserts correct total ────────

    def test_three_clusters_mixed_sizes_correct_total(self, tmp_path: Path) -> None:
        """Three clusters of sizes 2, 3, 4 must insert 9 rows total."""
        persist_clusters = _import_persist_clusters()
        _, conn = self._fresh_db(tmp_path, "mixed_sizes.db")
        all_ids = [201, 202, 203, 204, 205, 206, 207, 208, 209]
        _seed_chunks(conn, all_ids)

        clusters = [
            {
                "cluster_id": "c-aa",
                "chunk_ids": [201, 202],
                "similarity": 0.88,
                "method": "minhash_lsh",
            },
            {
                "cluster_id": "c-bb",
                "chunk_ids": [203, 204, 205],
                "similarity": 0.91,
                "method": "minhash_lsh",
            },
            {
                "cluster_id": "c-cc",
                "chunk_ids": [206, 207, 208, 209],
                "similarity": 0.95,
                "method": "minhash_lsh",
            },
        ]
        result = persist_clusters(conn, clusters, method="minhash_lsh")

        assert result == 9, f"Expected 9 rows (2+3+4), got {result}"
        rows = _sqlite_ndc_rows(conn)
        assert len(rows) == 9

        cluster_ids_in_rows = {r["cluster_id"] for r in rows}
        assert cluster_ids_in_rows == {"c-aa", "c-bb", "c-cc"}

    # ── T4: idempotent re-run does not double-insert ─────────────────────

    def test_idempotent_rerun_does_not_double_insert(self, tmp_path: Path) -> None:
        """Calling persist_clusters twice with the same clusters must not double rows.

        The idempotency strategy is INSERT OR IGNORE keyed on (cluster_id, chunk_id).
        The second call must return 0 rows inserted (all conflict / ignored).
        """
        persist_clusters = _import_persist_clusters()
        _, conn = self._fresh_db(tmp_path, "idempotent.db")
        _seed_chunks(conn, [301, 302, 303])

        clusters = [
            {
                "cluster_id": "idem-x",
                "chunk_ids": [301, 302],
                "similarity": 0.87,
                "method": "minhash_lsh",
            },
            {
                "cluster_id": "idem-y",
                "chunk_ids": [303],
                "similarity": 0.86,
                "method": "minhash_lsh",
            },
        ]

        first_result = persist_clusters(conn, clusters, method="minhash_lsh")
        assert first_result == 3, f"First call: expected 3 rows, got {first_result}"

        second_result = persist_clusters(conn, clusters, method="minhash_lsh")
        assert second_result == 0, (
            f"Second call must return 0 (idempotent INSERT OR IGNORE). "
            f"Got {second_result}, meaning rows were double-inserted."
        )

        total = _sqlite_row_count(conn, "near_duplicate_clusters")
        assert total == 3, f"Total row count after two calls must still be 3, got {total}"

    # ── T5: FK cascade — deleting chunk removes cluster rows ─────────────

    def test_fk_cascade_on_chunk_delete_removes_cluster_rows(self, tmp_path: Path) -> None:
        """Deleting a chunk via FK cascade must also delete its near_dup_cluster rows."""
        persist_clusters = _import_persist_clusters()
        _, conn = self._fresh_db(tmp_path, "fk_cascade.db")
        _seed_chunks(conn, [401, 402, 403])

        clusters = [
            {
                "cluster_id": "fk-clust",
                "chunk_ids": [401, 402, 403],
                "similarity": 0.9,
                "method": "minhash_lsh",
            },
        ]
        inserted = persist_clusters(conn, clusters, method="minhash_lsh")
        assert inserted == 3

        rows_before = _sqlite_row_count(conn, "near_duplicate_clusters")
        assert rows_before == 3

        # Delete one chunk — cascade must remove its near_dup row.
        conn.execute("DELETE FROM chunks WHERE id = 401")
        conn.commit()

        rows_after = _sqlite_row_count(conn, "near_duplicate_clusters")
        assert rows_after == 2, (
            f"After deleting chunk 401, expected 2 near_dup rows, got {rows_after}"
        )
        # The deleted chunk must not appear in the remaining rows.
        remaining = _sqlite_ndc_rows(conn)
        remaining_chunk_ids = {r["chunk_id"] for r in remaining}
        assert 401 not in remaining_chunk_ids

    # ── T6: method parameter override tags rows with correct method label ─

    def test_method_parameter_overrides_cluster_method_field(self, tmp_path: Path) -> None:
        """The ``method`` keyword arg must be stored in every inserted row.

        The cluster dict has its own ``"method"`` field; the *function-level*
        ``method`` parameter is the authoritative tag written to the DB column.
        If both differ, the function-level parameter wins.
        """
        persist_clusters = _import_persist_clusters()
        _, conn = self._fresh_db(tmp_path, "method_override.db")
        _seed_chunks(conn, [501, 502])

        clusters = [
            {
                "cluster_id": "override-clust",
                "chunk_ids": [501, 502],
                "similarity": 0.85,
                "method": "some_other_method",  # this field is NOT the DB method
            },
        ]
        # Call with a different method kwarg.
        result = persist_clusters(conn, clusters, method="minhash_lsh_v2")
        assert result == 2

        rows = _sqlite_ndc_rows(conn)
        for row in rows:
            assert row["method"] == "minhash_lsh_v2", (
                f"Expected method='minhash_lsh_v2' (from kwarg), got {row['method']!r}"
            )

    # ── T7: similarity float precision round-trip ─────────────────────────

    def test_similarity_roundtrip_preserves_float_within_tolerance(self, tmp_path: Path) -> None:
        """Similarity written to REAL column must round-trip within float32 tolerance.

        SQLite REAL is 64-bit IEEE 754 (double). The column is declared REAL
        which maps to SQLite's 8-byte REAL affinity — so precision should be
        preserved to at least float32 tolerance (1e-6).
        """
        persist_clusters = _import_persist_clusters()
        _, conn = self._fresh_db(tmp_path, "similarity_precision.db")
        _seed_chunks(conn, [601, 602])

        original_similarity = 0.876543210987654  # high-precision float
        clusters = [
            {
                "cluster_id": "prec-clust",
                "chunk_ids": [601, 602],
                "similarity": original_similarity,
                "method": "minhash_lsh",
            },
        ]
        persist_clusters(conn, clusters, method="minhash_lsh")

        rows = _sqlite_ndc_rows(conn)
        assert len(rows) == 2
        for row in rows:
            stored = row["similarity"]
            assert abs(stored - original_similarity) < 1e-6, (
                f"Similarity round-trip failed: stored {stored!r}, "
                f"original {original_similarity!r}, delta {abs(stored - original_similarity)}"
            )

    # ── T8: computed_at is populated (not NULL) ───────────────────────────

    def test_computed_at_is_populated_by_server_default(self, tmp_path: Path) -> None:
        """Every inserted row must have a non-NULL computed_at from the server default."""
        persist_clusters = _import_persist_clusters()
        _, conn = self._fresh_db(tmp_path, "computed_at.db")
        _seed_chunks(conn, [701, 702])

        clusters = [
            {
                "cluster_id": "ts-clust",
                "chunk_ids": [701, 702],
                "similarity": 0.88,
                "method": "minhash_lsh",
            },
        ]
        persist_clusters(conn, clusters, method="minhash_lsh")

        rows = _sqlite_ndc_rows(conn)
        assert len(rows) == 2
        for row in rows:
            assert row["computed_at"] is not None, (
                "computed_at must be populated by server default, "
                f"got None for chunk_id={row['chunk_id']}"
            )
            # Must be a non-empty string (SQLite stores as TEXT).
            assert isinstance(row["computed_at"], str) and len(row["computed_at"]) > 0, (
                f"computed_at must be a non-empty string, got {row['computed_at']!r}"
            )

    # ── T9: invalid cluster shape raises clear error ──────────────────────

    def test_invalid_cluster_missing_cluster_id_raises(self, tmp_path: Path) -> None:
        """A cluster dict missing 'cluster_id' must raise a clear error (KeyError or ValueError)."""
        persist_clusters = _import_persist_clusters()
        _, conn = self._fresh_db(tmp_path, "invalid_shape.db")
        _seed_chunks(conn, [801])

        bad_clusters = [
            # Missing 'cluster_id' key entirely.
            {"chunk_ids": [801], "similarity": 0.9, "method": "minhash_lsh"},
        ]
        with pytest.raises((KeyError, ValueError, TypeError)):
            persist_clusters(conn, bad_clusters, method="minhash_lsh")

    def test_invalid_cluster_missing_chunk_ids_raises(self, tmp_path: Path) -> None:
        """A cluster dict missing 'chunk_ids' must raise a clear error."""
        persist_clusters = _import_persist_clusters()
        _, conn = self._fresh_db(tmp_path, "invalid_no_chunk_ids.db")

        bad_clusters = [
            {"cluster_id": "bad-clust", "similarity": 0.9, "method": "minhash_lsh"},
        ]
        with pytest.raises((KeyError, ValueError, TypeError)):
            persist_clusters(conn, bad_clusters, method="minhash_lsh")

    # ── T10: return value is exactly the number of rows inserted ─────────

    def test_return_value_equals_rows_inserted(self, tmp_path: Path) -> None:
        """Return value must equal the number of (cluster_id, chunk_id) pairs written.

        This is a cross-check: persist_clusters return value vs. actual DB count.
        """
        persist_clusters = _import_persist_clusters()
        _, conn = self._fresh_db(tmp_path, "return_value.db")
        _seed_chunks(conn, [901, 902, 903, 904, 905])

        clusters = [
            {
                "cluster_id": "rv-a",
                "chunk_ids": [901, 902, 903],
                "similarity": 0.89,
                "method": "minhash_lsh",
            },
            {
                "cluster_id": "rv-b",
                "chunk_ids": [904, 905],
                "similarity": 0.91,
                "method": "minhash_lsh",
            },
        ]
        returned = persist_clusters(conn, clusters, method="minhash_lsh")
        actual_in_db = _sqlite_row_count(conn, "near_duplicate_clusters")

        assert returned == actual_in_db, f"Return value {returned} != actual DB rows {actual_in_db}"
        assert returned == 5

    # ── T11: similarity stored per cluster (different clusters, different sims) ──

    def test_each_cluster_member_stores_its_own_similarity(self, tmp_path: Path) -> None:
        """Rows from different clusters must carry their respective similarity values."""
        persist_clusters = _import_persist_clusters()
        _, conn = self._fresh_db(tmp_path, "per_cluster_sim.db")
        _seed_chunks(conn, [1001, 1002, 1003, 1004])

        clusters = [
            {
                "cluster_id": "sim-high",
                "chunk_ids": [1001, 1002],
                "similarity": 0.98,
                "method": "minhash_lsh",
            },
            {
                "cluster_id": "sim-low",
                "chunk_ids": [1003, 1004],
                "similarity": 0.72,
                "method": "minhash_lsh",
            },
        ]
        persist_clusters(conn, clusters, method="minhash_lsh")

        rows = _sqlite_ndc_rows(conn)
        high_rows = [r for r in rows if r["cluster_id"] == "sim-high"]
        low_rows = [r for r in rows if r["cluster_id"] == "sim-low"]

        assert len(high_rows) == 2
        assert len(low_rows) == 2
        for r in high_rows:
            assert abs(r["similarity"] - 0.98) < 1e-5
        for r in low_rows:
            assert abs(r["similarity"] - 0.72) < 1e-5

    # ── T12: default method kwarg is "minhash_lsh" ───────────────────────

    def test_default_method_kwarg_is_minhash_lsh(self, tmp_path: Path) -> None:
        """Calling persist_clusters without a method kwarg uses 'minhash_lsh' as default."""
        persist_clusters = _import_persist_clusters()
        _, conn = self._fresh_db(tmp_path, "default_method.db")
        _seed_chunks(conn, [1101, 1102])

        clusters = [
            {
                "cluster_id": "def-meth",
                "chunk_ids": [1101, 1102],
                "similarity": 0.9,
                "method": "minhash_lsh",
            },
        ]
        # Call WITHOUT the method kwarg — rely on the default.
        persist_clusters(conn, clusters)

        rows = _sqlite_ndc_rows(conn)
        for row in rows:
            assert row["method"] == "minhash_lsh", (
                f"Expected default method='minhash_lsh', got {row['method']!r}"
            )


# ---------------------------------------------------------------------------
# Postgres test class (requires Docker)
# ---------------------------------------------------------------------------


def _sa_dsn(dsn: str) -> str:
    """Convert ``postgresql://`` → ``postgresql+psycopg://`` for SQLAlchemy/Alembic."""
    return re.sub(r"^postgresql(s?)://", r"postgresql+psycopg\1://", dsn)


def _alembic_upgrade_pg(dsn: str, target: str) -> None:
    """Run ``alembic.command.upgrade(config, target)`` against a Postgres DSN."""
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


def _pg_ndc_rows(conn: Any) -> list[dict[str, Any]]:
    """Fetch all near_duplicate_clusters rows from Postgres."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, cluster_id, chunk_id, similarity, method, computed_at "
            "FROM corpus.near_duplicate_clusters ORDER BY id"
        )
        rows = cur.fetchall()
    return [
        {
            "id": r[0],
            "cluster_id": r[1],
            "chunk_id": r[2],
            "similarity": r[3],
            "method": r[4],
            "computed_at": r[5],
        }
        for r in rows
    ]


def _pg_seed_chunks(conn: Any, chunk_ids: list[int]) -> None:
    """Insert minimal chunks rows into corpus.chunks for FK satisfaction (Postgres).

    chunks.chunks_check enforces exactly one of (document_id, conversation_id)
    is NOT NULL, so we seed a parent document and a dataset first.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corpus.datasets (id, name, kind) "
            "VALUES (9001, 'dedup_persist_fixture', 'text') ON CONFLICT (id) DO NOTHING"
        )
        cur.execute(
            "INSERT INTO corpus.documents "
            "(id, dataset_id, source_uri, content_hash, text) "
            "VALUES (9001, 9001, 'fixture://dedup-persist', 'doc_fixture_hash',"
            " 'fixture document body') ON CONFLICT (id) DO NOTHING"
        )
        for cid in chunk_ids:
            cur.execute(
                "INSERT INTO corpus.chunks "
                "(id, document_id, chunk_index, text, content_hash, token_count) "
                "VALUES (%s, 9001, %s, 'dummy text', %s, 10) "
                "ON CONFLICT (id) DO NOTHING",
                (cid, cid, f"hash_{cid}"),
            )
    conn.commit()


def _pg_row_count(conn: Any, table: str) -> int:
    """Return the total row count for a table in the corpus schema (Postgres)."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM corpus.{table}")
        return cur.fetchone()[0]


@pytest.mark.requires_docker
class TestPersistClustersPostgres:
    """Functional tests for persist_clusters against a fresh Postgres database.

    Requires Docker + testcontainers. Uses the session-scoped ``pg_dsn``
    fixture from the root conftest. Each test resets and re-migrates the
    schema for isolation (same pattern as TestPostgresAnalyzeSignals).
    """

    def test_empty_cluster_list_returns_zero(self, pg_dsn: str) -> None:
        """persist_clusters([]) must return 0 and insert nothing (Postgres)."""
        import psycopg

        persist_clusters = _import_persist_clusters()
        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            result = persist_clusters(conn, [], method="minhash_lsh")
            assert result == 0
            assert _pg_row_count(conn, "near_duplicate_clusters") == 0

    def test_single_cluster_two_members_inserts_two_rows(self, pg_dsn: str) -> None:
        """A single 2-member cluster must produce 2 rows (Postgres)."""
        import psycopg

        persist_clusters = _import_persist_clusters()
        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            _pg_seed_chunks(conn, [101, 102])

            clusters = [
                {
                    "cluster_id": "pg-clust-aaa",
                    "chunk_ids": [101, 102],
                    "similarity": 0.92,
                    "method": "minhash_lsh",
                },
            ]
            result = persist_clusters(conn, clusters, method="minhash_lsh")
            assert result == 2

            rows = _pg_ndc_rows(conn)
            assert len(rows) == 2
            chunk_ids_inserted = {r["chunk_id"] for r in rows}
            assert chunk_ids_inserted == {101, 102}

    def test_idempotent_rerun_does_not_double_insert(self, pg_dsn: str) -> None:
        """Second call with same clusters must return 0 (ON CONFLICT DO NOTHING — Postgres)."""
        import psycopg

        persist_clusters = _import_persist_clusters()
        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            _pg_seed_chunks(conn, [301, 302])

            clusters = [
                {
                    "cluster_id": "pg-idem",
                    "chunk_ids": [301, 302],
                    "similarity": 0.87,
                    "method": "minhash_lsh",
                },
            ]
            first = persist_clusters(conn, clusters, method="minhash_lsh")
            assert first == 2

            second = persist_clusters(conn, clusters, method="minhash_lsh")
            assert second == 0, f"Second call must return 0 (ON CONFLICT DO NOTHING). Got {second}."
            assert _pg_row_count(conn, "near_duplicate_clusters") == 2

    def test_similarity_roundtrip_within_float32_tolerance(self, pg_dsn: str) -> None:
        """Similarity round-trips through Postgres REAL within float32 tolerance (~1e-6)."""
        import psycopg

        persist_clusters = _import_persist_clusters()
        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        original_sim = 0.876543210987654
        with psycopg.connect(pg_dsn) as conn:
            _pg_seed_chunks(conn, [601, 602])

            clusters = [
                {
                    "cluster_id": "pg-prec",
                    "chunk_ids": [601, 602],
                    "similarity": original_sim,
                    "method": "minhash_lsh",
                },
            ]
            persist_clusters(conn, clusters, method="minhash_lsh")

            rows = _pg_ndc_rows(conn)
            for row in rows:
                stored = float(row["similarity"])
                assert abs(stored - original_sim) < 1e-5, (
                    f"Postgres similarity round-trip delta "
                    f"{abs(stored - original_sim)} > 1e-5. "
                    "Note: Postgres REAL is 4-byte float32; "
                    "delta may be up to ~5e-5 for this value."
                )

    def test_computed_at_is_populated(self, pg_dsn: str) -> None:
        """computed_at must be non-NULL for every inserted row (Postgres)."""
        import psycopg

        persist_clusters = _import_persist_clusters()
        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            _pg_seed_chunks(conn, [701, 702])

            clusters = [
                {
                    "cluster_id": "pg-ts",
                    "chunk_ids": [701, 702],
                    "similarity": 0.88,
                    "method": "minhash_lsh",
                },
            ]
            persist_clusters(conn, clusters, method="minhash_lsh")

            rows = _pg_ndc_rows(conn)
            assert len(rows) == 2
            for row in rows:
                assert row["computed_at"] is not None, (
                    "computed_at must be populated by NOW() default, "
                    f"got None for chunk_id={row['chunk_id']}"
                )

    def test_fk_cascade_on_chunk_delete(self, pg_dsn: str) -> None:
        """Deleting a chunk must cascade-delete its near_dup_cluster rows (Postgres)."""
        import psycopg

        persist_clusters = _import_persist_clusters()
        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            _pg_seed_chunks(conn, [401, 402, 403])

            clusters = [
                {
                    "cluster_id": "pg-fk",
                    "chunk_ids": [401, 402, 403],
                    "similarity": 0.9,
                    "method": "minhash_lsh",
                },
            ]
            inserted = persist_clusters(conn, clusters, method="minhash_lsh")
            assert inserted == 3

            with conn.cursor() as cur:
                cur.execute("DELETE FROM corpus.chunks WHERE id = 401")
            conn.commit()

            count_after = _pg_row_count(conn, "near_duplicate_clusters")
            assert count_after == 2, (
                f"After cascade delete of chunk 401, expected 2 near_dup rows, got {count_after}"
            )
