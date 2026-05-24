"""Integration test for the Phase O Wave 1 migration: 0012_analyze_signals.

Asserts that after applying ``alembic upgrade 0012_analyze_signals``:

- ``chunk_quality_signals`` table exists with the expected columns, types,
  nullability, and a composite index on ``(chunk_id, signal_name)``.
- ``near_duplicate_clusters`` table exists with the expected columns, types,
  nullability, and an index on ``(cluster_id)``.
- Both tables define ``chunk_id`` as a FK to ``chunks(id)`` with ON DELETE CASCADE.
- The migration module itself chains correctly:
  ``revision == "0012_analyze_signals"`` and
  ``down_revision == "0011_image_embeddings"``.
- ``downgrade()`` is forward-only: its body is a single ``pass`` statement
  (project convention established by 0008, 0010, and 0011).

RED condition
-------------
The migration file ``corpus_forge/alembic/versions/0012_analyze_signals.py``
does not yet exist.  Every test in this file should fail with either:

  - ``alembic.util.exc.CommandError: Can't locate revision identified by
    '0012_analyze_signals'``         (the alembic-upgrade tests), or
  - ``ModuleNotFoundError`` / ``ImportError`` on
    ``corpus_forge.alembic.versions.0012_analyze_signals``
    (the revision-attribute tests and the downgrade-AST test).

Both are acceptable RED states.
"""

from __future__ import annotations

import ast
import importlib
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import pytest

if TYPE_CHECKING:
    import psycopg


class _SqliteColInfo(TypedDict):
    type: str
    notnull: bool
    dflt_value: str | None
    pk: bool


class _SqliteIndexRow(TypedDict):
    name: str
    sql: str | None


pytestmark = [pytest.mark.integration]

# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_MIGRATION_MODULE = "corpus_forge.alembic.versions.0012_analyze_signals"
_MIGRATION_FILE = _REPO_ROOT / "corpus_forge" / "alembic" / "versions" / "0012_analyze_signals.py"
_TARGET_REVISION = "0012_analyze_signals"

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _sa_dsn(dsn: str) -> str:
    """Convert ``postgresql://`` → ``postgresql+psycopg://`` for SQLAlchemy/Alembic."""
    return re.sub(r"^postgresql(s?)://", r"postgresql+psycopg\1://", dsn)


def _alembic_upgrade_pg(dsn: str, target: str) -> None:
    """Run ``alembic.command.upgrade(config, target)`` against a Postgres DSN.

    Raises ``alembic.util.exc.CommandError`` when *target* is unknown (RED state).
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )
    cfg.set_main_option("sqlalchemy.url", _sa_dsn(dsn))
    command.upgrade(cfg, target)


def _alembic_upgrade_sqlite(db_path: Path, target: str) -> None:
    """Run ``alembic.command.upgrade(config, target)`` against a SQLite *db_path*.

    Raises ``alembic.util.exc.CommandError`` when *target* is unknown (RED state).
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, target)


def _reset_pg_schema(dsn: str) -> None:
    """Drop and recreate the corpus schema + pgvector extension."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS corpus CASCADE")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE SCHEMA IF NOT EXISTS corpus")


def _pg_column_info(
    conn: psycopg.Connection,
    table_schema: str,
    table_name: str,
) -> dict[str, dict[str, str | None]]:
    """Return ``{column_name: {data_type, is_nullable, column_default}}`` from
    ``information_schema.columns`` for the given table.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                column_name,
                data_type,
                is_nullable,
                column_default
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name   = %s
            ORDER BY ordinal_position
            """,
            (table_schema, table_name),
        )
        rows = cur.fetchall()
    return {
        row[0]: {
            "data_type": row[1],
            "is_nullable": row[2],
            "column_default": row[3],
        }
        for row in rows
    }


def _pg_index_defs(
    conn: psycopg.Connection,
    schema_name: str,
    table_name: str,
) -> dict[str, str]:
    """Return ``{indexname: indexdef}`` from ``pg_indexes`` for the given table."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = %s
              AND tablename  = %s
            """,
            (schema_name, table_name),
        )
        rows = cur.fetchall()
    return {row[0]: row[1] for row in rows}


def _pg_fk_info(
    conn: psycopg.Connection,
    schema_name: str,
    table_name: str,
) -> list[dict[str, str]]:
    """Return FK constraints for the given table.

    Each entry is a dict with keys: ``column_name``, ``foreign_table_name``,
    ``foreign_column_name``, ``delete_rule``.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                kcu.column_name,
                ccu.table_name  AS foreign_table_name,
                ccu.column_name AS foreign_column_name,
                rc.delete_rule
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema    = kcu.table_schema
            JOIN information_schema.referential_constraints rc
              ON tc.constraint_name = rc.constraint_name
             AND tc.table_schema    = rc.constraint_schema
            JOIN information_schema.constraint_column_usage ccu
              ON rc.unique_constraint_name   = ccu.constraint_name
             AND rc.unique_constraint_schema = ccu.constraint_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema    = %s
              AND tc.table_name      = %s
            ORDER BY kcu.column_name
            """,
            (schema_name, table_name),
        )
        rows = cur.fetchall()
    return [
        {
            "column_name": row[0],
            "foreign_table_name": row[1],
            "foreign_column_name": row[2],
            "delete_rule": row[3],
        }
        for row in rows
    ]


def _sqlite_col_map(conn: sqlite3.Connection, table: str) -> dict[str, _SqliteColInfo]:
    """Return PRAGMA table_info as ``{name: {type, notnull, dflt_value, pk}}``."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {
        row[1]: _SqliteColInfo(
            type=row[2].upper(),
            notnull=bool(row[3]),
            dflt_value=row[4],
            pk=bool(row[5]),
        )
        for row in rows
    }


def _sqlite_indexes(conn: sqlite3.Connection, table: str) -> list[_SqliteIndexRow]:
    """Return sqlite_master index rows for *table* as a list of dicts."""
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?",
        (table,),
    ).fetchall()
    return [_SqliteIndexRow(name=row[0], sql=row[1]) for row in rows]


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchall()
    return len(rows) == 1


# ---------------------------------------------------------------------------
# Cross-cutting tests (no DB required)
# ---------------------------------------------------------------------------


class TestMigrationModuleAttributes:
    """Assert the migration module's revision chain and forward-only downgrade.

    These tests import the migration Python module directly and inspect its
    attributes + AST.  They fail immediately with ModuleNotFoundError while
    the migration file does not exist — that is the expected RED state.
    """

    def test_revision_value(self) -> None:
        """Migration module must declare ``revision = "0012_analyze_signals"``."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.revision == "0012_analyze_signals", (
            f"Expected revision='0012_analyze_signals', got {mod.revision!r}"
        )

    def test_down_revision_value(self) -> None:
        """Migration module must declare ``down_revision = "0011_image_embeddings"``."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.down_revision == "0011_image_embeddings", (
            f"Expected down_revision='0011_image_embeddings', "
            f"got {mod.down_revision!r}.  "
            "Accidental rebase drift will break the alembic chain in CI."
        )

    def test_downgrade_is_forward_only_pass(self) -> None:
        """``downgrade()`` body must be a single ``pass`` statement.

        Project convention (established by 0008, 0010, 0011): migrations that
        create new tables are forward-only.  The plan's wording about a 'clean
        downgrade' was overridden — this assertion enforces the real contract.
        """
        assert _MIGRATION_FILE.exists(), (
            f"Migration file not found at {_MIGRATION_FILE}. Write it before this test can proceed."
        )
        source = _MIGRATION_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source)

        # Find the downgrade() function definition.
        downgrade_func: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
                downgrade_func = node
                break

        assert downgrade_func is not None, (
            "No ``downgrade()`` function found in 0012_analyze_signals.py"
        )

        # The body must consist of exactly one statement, and it must be ``pass``.
        body = downgrade_func.body
        assert len(body) == 1 and isinstance(body[0], ast.Pass), (
            f"downgrade() body must be a single ``pass`` statement (forward-only convention). "
            f"Got {len(body)} statement(s): "
            f"{[ast.dump(s) for s in body]}"
        )


# ---------------------------------------------------------------------------
# SQLite tests (no Docker required)
# ---------------------------------------------------------------------------


class TestSQLiteAnalyzeSignals:
    """Schema assertions for the 0012_analyze_signals migration against SQLite.

    Runs without Docker.  Uses ``tmp_path`` for per-test DB isolation.

    RED state: fails with alembic.util.exc.CommandError because
    ``0012_analyze_signals`` revision does not yet exist.
    """

    # ── chunk_quality_signals ────────────────────────────────────────────

    def test_chunk_quality_signals_table_exists(self, tmp_path: Path) -> None:
        """chunk_quality_signals table must exist after upgrade."""
        db_path = tmp_path / "analyze_signals.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            assert _sqlite_table_exists(conn, "chunk_quality_signals"), (
                "chunk_quality_signals table not found in sqlite_master after 0012 upgrade"
            )

    def test_chunk_quality_signals_columns(self, tmp_path: Path) -> None:
        """chunk_quality_signals must have the exact expected columns."""
        db_path = tmp_path / "cqs_cols.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "chunk_quality_signals")

        expected_columns = {
            "id",
            "chunk_id",
            "signal_name",
            "signal_value",
            "source",
            "computed_at",
        }
        assert set(col_map.keys()) == expected_columns, (
            f"chunk_quality_signals columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(col_map.keys())}"
        )

    def test_chunk_quality_signals_id_pk(self, tmp_path: Path) -> None:
        """chunk_quality_signals.id must be an INTEGER PRIMARY KEY."""
        db_path = tmp_path / "cqs_id.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "chunk_quality_signals")

        assert col_map["id"]["pk"], "chunk_quality_signals.id: expected PRIMARY KEY"
        assert "INTEGER" in col_map["id"]["type"], (
            f"chunk_quality_signals.id: expected INTEGER type, got {col_map['id']['type']!r}"
        )

    def test_chunk_quality_signals_chunk_id_not_null(self, tmp_path: Path) -> None:
        """chunk_quality_signals.chunk_id must be INTEGER NOT NULL."""
        db_path = tmp_path / "cqs_chunk_id.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "chunk_quality_signals")

        assert "INTEGER" in col_map["chunk_id"]["type"], (
            f"chunk_quality_signals.chunk_id: expected INTEGER type, "
            f"got {col_map['chunk_id']['type']!r}"
        )
        assert col_map["chunk_id"]["notnull"], "chunk_quality_signals.chunk_id: expected NOT NULL"

    def test_chunk_quality_signals_signal_name_not_null(self, tmp_path: Path) -> None:
        """chunk_quality_signals.signal_name must be TEXT NOT NULL."""
        db_path = tmp_path / "cqs_sname.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "chunk_quality_signals")

        assert "TEXT" in col_map["signal_name"]["type"], (
            f"chunk_quality_signals.signal_name: expected TEXT, "
            f"got {col_map['signal_name']['type']!r}"
        )
        assert col_map["signal_name"]["notnull"], (
            "chunk_quality_signals.signal_name: expected NOT NULL"
        )

    def test_chunk_quality_signals_signal_value_real(self, tmp_path: Path) -> None:
        """chunk_quality_signals.signal_value must be REAL."""
        db_path = tmp_path / "cqs_svalue.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "chunk_quality_signals")

        assert "REAL" in col_map["signal_value"]["type"], (
            f"chunk_quality_signals.signal_value: expected REAL type, "
            f"got {col_map['signal_value']['type']!r}"
        )

    def test_chunk_quality_signals_source_not_null(self, tmp_path: Path) -> None:
        """chunk_quality_signals.source must be TEXT NOT NULL."""
        db_path = tmp_path / "cqs_source.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "chunk_quality_signals")

        assert "TEXT" in col_map["source"]["type"], (
            f"chunk_quality_signals.source: expected TEXT, got {col_map['source']['type']!r}"
        )
        assert col_map["source"]["notnull"], "chunk_quality_signals.source: expected NOT NULL"

    def test_chunk_quality_signals_computed_at_not_null_with_default(self, tmp_path: Path) -> None:
        """chunk_quality_signals.computed_at must be TEXT NOT NULL DEFAULT (datetime('now'))."""
        db_path = tmp_path / "cqs_cat.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "chunk_quality_signals")

        assert "TEXT" in col_map["computed_at"]["type"], (
            "chunk_quality_signals.computed_at: expected TEXT type "
            f"(SQLite TIMESTAMPTZ convention), got {col_map['computed_at']['type']!r}"
        )
        assert col_map["computed_at"]["notnull"], (
            "chunk_quality_signals.computed_at: expected NOT NULL"
        )
        default_val = (col_map["computed_at"]["dflt_value"] or "").lower()
        assert "datetime" in default_val and "now" in default_val, (
            f"chunk_quality_signals.computed_at: expected DEFAULT (datetime('now')), "
            f"got {col_map['computed_at']['dflt_value']!r}"
        )

    def test_chunk_quality_signals_chunk_signal_index_exists(self, tmp_path: Path) -> None:
        """An index on (chunk_id, signal_name) must exist for chunk_quality_signals."""
        db_path = tmp_path / "cqs_idx.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            indexes = _sqlite_indexes(conn, "chunk_quality_signals")

        # Find an index that covers both chunk_id and signal_name.
        found = False
        for idx in indexes:
            sql = (idx["sql"] or "").lower()
            if "chunk_id" in sql and "signal_name" in sql:
                found = True
                break
            # For indexes without SQL (auto-created), inspect index_info.
            if idx["sql"] is None:
                with sqlite3.connect(str(db_path)) as conn2:
                    info_rows = conn2.execute(f"PRAGMA index_info({idx['name']})").fetchall()
                cols_in_idx = {r[2] for r in info_rows}
                if "chunk_id" in cols_in_idx and "signal_name" in cols_in_idx:
                    found = True
                    break

        assert found, (
            "chunk_quality_signals: no index found covering (chunk_id, signal_name). "
            f"Indexes present: {[i['name'] for i in indexes]}"
        )

    def test_chunk_quality_signals_fk_cascade_on_delete(self, tmp_path: Path) -> None:
        """Deleting a chunks row must cascade-delete its chunk_quality_signals rows."""
        db_path = tmp_path / "cqs_fk.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")

            # Insert a minimal chunks row.  The chunks table was created by an
            # earlier migration (0001 or similar); we insert the minimum required fields.
            # We rely on any NOT NULL constraints already being met by the existing schema.
            # If this INSERT fails, it means the chunks table schema differs — the test
            # will surface that as a clear error.
            conn.execute(
                "INSERT INTO chunks (id, chunk_index, text, content_hash, token_count) "
                "VALUES (9991, 0, 'dummy', 'hash_fk_test', 10)"
            )
            conn.execute(
                "INSERT INTO chunk_quality_signals "
                "(chunk_id, signal_name, signal_value, source, computed_at) "
                "VALUES (9991, 'test_signal', 0.5, 'unit_test', datetime('now'))"
            )
            conn.commit()

            # Verify the signal row exists.
            rows_before = conn.execute(
                "SELECT COUNT(*) FROM chunk_quality_signals WHERE chunk_id = 9991"
            ).fetchone()[0]
            assert rows_before == 1, (
                "chunk_quality_signals: expected 1 row before cascade delete test"
            )

            # Delete the chunk — should cascade.
            conn.execute("DELETE FROM chunks WHERE id = 9991")
            conn.commit()

            rows_after = conn.execute(
                "SELECT COUNT(*) FROM chunk_quality_signals WHERE chunk_id = 9991"
            ).fetchone()[0]
            assert rows_after == 0, (
                "chunk_quality_signals: FK ON DELETE CASCADE did not remove signal row "
                "when parent chunk was deleted"
            )

    # ── near_duplicate_clusters ──────────────────────────────────────────

    def test_near_duplicate_clusters_table_exists(self, tmp_path: Path) -> None:
        """near_duplicate_clusters table must exist after upgrade."""
        db_path = tmp_path / "ndc_exists.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            assert _sqlite_table_exists(conn, "near_duplicate_clusters"), (
                "near_duplicate_clusters table not found in sqlite_master after 0012 upgrade"
            )

    def test_near_duplicate_clusters_columns(self, tmp_path: Path) -> None:
        """near_duplicate_clusters must have the exact expected columns."""
        db_path = tmp_path / "ndc_cols.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "near_duplicate_clusters")

        expected_columns = {"id", "cluster_id", "chunk_id", "similarity", "method", "computed_at"}
        assert set(col_map.keys()) == expected_columns, (
            f"near_duplicate_clusters columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(col_map.keys())}"
        )

    def test_near_duplicate_clusters_id_pk(self, tmp_path: Path) -> None:
        """near_duplicate_clusters.id must be an INTEGER PRIMARY KEY."""
        db_path = tmp_path / "ndc_id.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "near_duplicate_clusters")

        assert col_map["id"]["pk"], "near_duplicate_clusters.id: expected PRIMARY KEY"
        assert "INTEGER" in col_map["id"]["type"], (
            f"near_duplicate_clusters.id: expected INTEGER type, got {col_map['id']['type']!r}"
        )

    def test_near_duplicate_clusters_cluster_id_not_null(self, tmp_path: Path) -> None:
        """near_duplicate_clusters.cluster_id must be TEXT NOT NULL."""
        db_path = tmp_path / "ndc_cid.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "near_duplicate_clusters")

        assert "TEXT" in col_map["cluster_id"]["type"], (
            f"near_duplicate_clusters.cluster_id: expected TEXT, "
            f"got {col_map['cluster_id']['type']!r}"
        )
        assert col_map["cluster_id"]["notnull"], (
            "near_duplicate_clusters.cluster_id: expected NOT NULL"
        )

    def test_near_duplicate_clusters_chunk_id_not_null(self, tmp_path: Path) -> None:
        """near_duplicate_clusters.chunk_id must be INTEGER NOT NULL."""
        db_path = tmp_path / "ndc_chunkid.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "near_duplicate_clusters")

        assert "INTEGER" in col_map["chunk_id"]["type"], (
            f"near_duplicate_clusters.chunk_id: expected INTEGER type, "
            f"got {col_map['chunk_id']['type']!r}"
        )
        assert col_map["chunk_id"]["notnull"], "near_duplicate_clusters.chunk_id: expected NOT NULL"

    def test_near_duplicate_clusters_similarity_real(self, tmp_path: Path) -> None:
        """near_duplicate_clusters.similarity must be REAL."""
        db_path = tmp_path / "ndc_sim.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "near_duplicate_clusters")

        assert "REAL" in col_map["similarity"]["type"], (
            f"near_duplicate_clusters.similarity: expected REAL type, "
            f"got {col_map['similarity']['type']!r}"
        )

    def test_near_duplicate_clusters_method_not_null(self, tmp_path: Path) -> None:
        """near_duplicate_clusters.method must be TEXT NOT NULL."""
        db_path = tmp_path / "ndc_method.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "near_duplicate_clusters")

        assert "TEXT" in col_map["method"]["type"], (
            f"near_duplicate_clusters.method: expected TEXT, got {col_map['method']['type']!r}"
        )
        assert col_map["method"]["notnull"], "near_duplicate_clusters.method: expected NOT NULL"

    def test_near_duplicate_clusters_computed_at_not_null_with_default(
        self, tmp_path: Path
    ) -> None:
        """near_duplicate_clusters.computed_at must be TEXT NOT NULL DEFAULT (datetime('now'))."""
        db_path = tmp_path / "ndc_cat.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "near_duplicate_clusters")

        assert "TEXT" in col_map["computed_at"]["type"], (
            f"near_duplicate_clusters.computed_at: expected TEXT type, "
            f"got {col_map['computed_at']['type']!r}"
        )
        assert col_map["computed_at"]["notnull"], (
            "near_duplicate_clusters.computed_at: expected NOT NULL"
        )
        default_val = (col_map["computed_at"]["dflt_value"] or "").lower()
        assert "datetime" in default_val and "now" in default_val, (
            f"near_duplicate_clusters.computed_at: expected DEFAULT (datetime('now')), "
            f"got {col_map['computed_at']['dflt_value']!r}"
        )

    def test_near_duplicate_clusters_cluster_id_index_exists(self, tmp_path: Path) -> None:
        """An index on cluster_id must exist for near_duplicate_clusters."""
        db_path = tmp_path / "ndc_idx.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            indexes = _sqlite_indexes(conn, "near_duplicate_clusters")

        found = False
        for idx in indexes:
            sql = (idx["sql"] or "").lower()
            if "cluster_id" in sql:
                found = True
                break
            if idx["sql"] is None:
                with sqlite3.connect(str(db_path)) as conn2:
                    info_rows = conn2.execute(f"PRAGMA index_info({idx['name']})").fetchall()
                cols_in_idx = {r[2] for r in info_rows}
                if "cluster_id" in cols_in_idx:
                    found = True
                    break

        assert found, (
            "near_duplicate_clusters: no index found on cluster_id column. "
            f"Indexes present: {[i['name'] for i in indexes]}"
        )

    def test_near_duplicate_clusters_fk_cascade_on_delete(self, tmp_path: Path) -> None:
        """Deleting a chunks row must cascade-delete its near_duplicate_clusters rows."""
        db_path = tmp_path / "ndc_fk.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")

            conn.execute(
                "INSERT INTO chunks (id, chunk_index, text, content_hash, token_count) "
                "VALUES (9992, 0, 'dummy2', 'hash_fk_test2', 5)"
            )
            conn.execute(
                "INSERT INTO near_duplicate_clusters "
                "(cluster_id, chunk_id, similarity, method, computed_at) "
                "VALUES ('cluster_abc', 9992, 0.9, 'minhash_lsh', datetime('now'))"
            )
            conn.commit()

            rows_before = conn.execute(
                "SELECT COUNT(*) FROM near_duplicate_clusters WHERE chunk_id = 9992"
            ).fetchone()[0]
            assert rows_before == 1

            conn.execute("DELETE FROM chunks WHERE id = 9992")
            conn.commit()

            rows_after = conn.execute(
                "SELECT COUNT(*) FROM near_duplicate_clusters WHERE chunk_id = 9992"
            ).fetchone()[0]
            assert rows_after == 0, (
                "near_duplicate_clusters: FK ON DELETE CASCADE did not remove cluster row "
                "when parent chunk was deleted"
            )

    def test_down_revision_chain_sqlite(self, tmp_path: Path) -> None:
        """Import the migration module and assert down_revision is correct (SQLite path guard)."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.down_revision == "0011_image_embeddings", (
            f"down_revision drift detected: got {mod.down_revision!r}"
        )


# ---------------------------------------------------------------------------
# Postgres tests (requires Docker)
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker
class TestPostgresAnalyzeSignals:
    """Schema assertions for the 0012_analyze_signals migration against Postgres.

    Requires Docker + testcontainers (skipped automatically when unavailable).
    Uses the session-scoped ``pg_dsn`` fixture from the root conftest.

    RED state: fails with alembic.util.exc.CommandError because
    ``0012_analyze_signals`` revision does not yet exist.
    """

    # ── chunk_quality_signals ────────────────────────────────────────────

    def test_chunk_quality_signals_table_exists(self, pg_dsn: str) -> None:
        """chunk_quality_signals table must exist in corpus schema after upgrade."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "chunk_quality_signals")

        assert cols, (
            "corpus.chunk_quality_signals table not found after 0012_analyze_signals upgrade "
            "(information_schema.columns returned no rows)"
        )

    def test_chunk_quality_signals_columns(self, pg_dsn: str) -> None:
        """chunk_quality_signals must have the exact expected column set."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "chunk_quality_signals")

        expected_columns = {
            "id",
            "chunk_id",
            "signal_name",
            "signal_value",
            "source",
            "computed_at",
        }
        assert set(cols.keys()) == expected_columns, (
            f"corpus.chunk_quality_signals columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(cols.keys())}"
        )

    def test_chunk_quality_signals_id_bigint_not_null(self, pg_dsn: str) -> None:
        """chunk_quality_signals.id must be bigint NOT NULL (BIGSERIAL PK)."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "chunk_quality_signals")

        assert cols["id"]["data_type"] == "bigint", (
            f"chunk_quality_signals.id: expected bigint, got {cols['id']['data_type']!r}"
        )
        assert cols["id"]["is_nullable"] == "NO", (
            f"chunk_quality_signals.id: expected NOT NULL, "
            f"got is_nullable={cols['id']['is_nullable']!r}"
        )

    def test_chunk_quality_signals_chunk_id_bigint_not_null(self, pg_dsn: str) -> None:
        """chunk_quality_signals.chunk_id must be bigint NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "chunk_quality_signals")

        assert cols["chunk_id"]["data_type"] == "bigint", (
            f"chunk_quality_signals.chunk_id: expected bigint, "
            f"got {cols['chunk_id']['data_type']!r}"
        )
        assert cols["chunk_id"]["is_nullable"] == "NO", (
            "chunk_quality_signals.chunk_id: expected NOT NULL"
        )

    def test_chunk_quality_signals_signal_name_text_not_null(self, pg_dsn: str) -> None:
        """chunk_quality_signals.signal_name must be text NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "chunk_quality_signals")

        assert cols["signal_name"]["data_type"] == "text", (
            f"chunk_quality_signals.signal_name: expected text, "
            f"got {cols['signal_name']['data_type']!r}"
        )
        assert cols["signal_name"]["is_nullable"] == "NO"

    def test_chunk_quality_signals_signal_value_real(self, pg_dsn: str) -> None:
        """chunk_quality_signals.signal_value must be a real/float type."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "chunk_quality_signals")

        # Postgres maps REAL → 'real'; double precision → 'double precision'
        assert cols["signal_value"]["data_type"] in ("real", "double precision"), (
            f"chunk_quality_signals.signal_value: expected real, "
            f"got {cols['signal_value']['data_type']!r}"
        )

    def test_chunk_quality_signals_source_text_not_null(self, pg_dsn: str) -> None:
        """chunk_quality_signals.source must be text NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "chunk_quality_signals")

        assert cols["source"]["data_type"] == "text", (
            f"chunk_quality_signals.source: expected text, got {cols['source']['data_type']!r}"
        )
        assert cols["source"]["is_nullable"] == "NO"

    def test_chunk_quality_signals_computed_at_timestamptz_not_null_default_now(
        self, pg_dsn: str
    ) -> None:
        """chunk_quality_signals.computed_at must be TIMESTAMPTZ NOT NULL DEFAULT NOW()."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "chunk_quality_signals")

        assert cols["computed_at"]["data_type"] == "timestamp with time zone", (
            f"chunk_quality_signals.computed_at: expected 'timestamp with time zone', "
            f"got {cols['computed_at']['data_type']!r}"
        )
        assert cols["computed_at"]["is_nullable"] == "NO"
        default_val = (cols["computed_at"]["column_default"] or "").lower()
        assert "now" in default_val, (
            f"chunk_quality_signals.computed_at: expected DEFAULT NOW(), "
            f"got {cols['computed_at']['column_default']!r}"
        )

    def test_chunk_quality_signals_chunk_signal_index(self, pg_dsn: str) -> None:
        """Index chunk_quality_signals_chunk_signal_idx on (chunk_id, signal_name) must exist."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            indexes = _pg_index_defs(conn, "corpus", "chunk_quality_signals")

        # Find an index referencing both chunk_id and signal_name.
        found = any(
            "chunk_id" in defn.lower() and "signal_name" in defn.lower()
            for defn in indexes.values()
            if defn is not None
        )
        assert found, (
            "corpus.chunk_quality_signals: no index found on (chunk_id, signal_name). "
            f"Indexes present: {list(indexes.keys())}"
        )

    def test_chunk_quality_signals_fk_cascade(self, pg_dsn: str) -> None:
        """chunk_quality_signals.chunk_id FK must be ON DELETE CASCADE."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            fks = _pg_fk_info(conn, "corpus", "chunk_quality_signals")

        chunk_id_fks = [fk for fk in fks if fk["column_name"] == "chunk_id"]
        assert chunk_id_fks, "corpus.chunk_quality_signals: no FK found on chunk_id column"
        fk = chunk_id_fks[0]
        assert fk["foreign_table_name"] == "chunks", (
            f"chunk_quality_signals.chunk_id FK references {fk['foreign_table_name']!r}, "
            f"expected 'chunks'"
        )
        assert fk["delete_rule"].upper() == "CASCADE", (
            f"chunk_quality_signals.chunk_id FK delete_rule is {fk['delete_rule']!r}, "
            f"expected 'CASCADE'"
        )

    # ── near_duplicate_clusters ──────────────────────────────────────────

    def test_near_duplicate_clusters_table_exists(self, pg_dsn: str) -> None:
        """near_duplicate_clusters table must exist in corpus schema after upgrade."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "near_duplicate_clusters")

        assert cols, (
            "corpus.near_duplicate_clusters table not found after 0012_analyze_signals upgrade"
        )

    def test_near_duplicate_clusters_columns(self, pg_dsn: str) -> None:
        """near_duplicate_clusters must have the exact expected column set."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "near_duplicate_clusters")

        expected_columns = {"id", "cluster_id", "chunk_id", "similarity", "method", "computed_at"}
        assert set(cols.keys()) == expected_columns, (
            f"corpus.near_duplicate_clusters columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(cols.keys())}"
        )

    def test_near_duplicate_clusters_id_bigint_not_null(self, pg_dsn: str) -> None:
        """near_duplicate_clusters.id must be bigint NOT NULL (BIGSERIAL PK)."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "near_duplicate_clusters")

        assert cols["id"]["data_type"] == "bigint", (
            f"near_duplicate_clusters.id: expected bigint, got {cols['id']['data_type']!r}"
        )
        assert cols["id"]["is_nullable"] == "NO"

    def test_near_duplicate_clusters_cluster_id_text_not_null(self, pg_dsn: str) -> None:
        """near_duplicate_clusters.cluster_id must be text NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "near_duplicate_clusters")

        assert cols["cluster_id"]["data_type"] == "text", (
            f"near_duplicate_clusters.cluster_id: expected text, "
            f"got {cols['cluster_id']['data_type']!r}"
        )
        assert cols["cluster_id"]["is_nullable"] == "NO"

    def test_near_duplicate_clusters_chunk_id_bigint_not_null(self, pg_dsn: str) -> None:
        """near_duplicate_clusters.chunk_id must be bigint NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "near_duplicate_clusters")

        assert cols["chunk_id"]["data_type"] == "bigint", (
            f"near_duplicate_clusters.chunk_id: expected bigint, "
            f"got {cols['chunk_id']['data_type']!r}"
        )
        assert cols["chunk_id"]["is_nullable"] == "NO"

    def test_near_duplicate_clusters_similarity_real(self, pg_dsn: str) -> None:
        """near_duplicate_clusters.similarity must be a real/float type."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "near_duplicate_clusters")

        assert cols["similarity"]["data_type"] in ("real", "double precision"), (
            f"near_duplicate_clusters.similarity: expected real, "
            f"got {cols['similarity']['data_type']!r}"
        )

    def test_near_duplicate_clusters_method_text_not_null(self, pg_dsn: str) -> None:
        """near_duplicate_clusters.method must be text NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "near_duplicate_clusters")

        assert cols["method"]["data_type"] == "text", (
            f"near_duplicate_clusters.method: expected text, got {cols['method']['data_type']!r}"
        )
        assert cols["method"]["is_nullable"] == "NO"

    def test_near_duplicate_clusters_computed_at_timestamptz_not_null_default_now(
        self, pg_dsn: str
    ) -> None:
        """near_duplicate_clusters.computed_at must be TIMESTAMPTZ NOT NULL DEFAULT NOW()."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "near_duplicate_clusters")

        assert cols["computed_at"]["data_type"] == "timestamp with time zone", (
            f"near_duplicate_clusters.computed_at: expected 'timestamp with time zone', "
            f"got {cols['computed_at']['data_type']!r}"
        )
        assert cols["computed_at"]["is_nullable"] == "NO"
        default_val = (cols["computed_at"]["column_default"] or "").lower()
        assert "now" in default_val, (
            f"near_duplicate_clusters.computed_at: expected DEFAULT NOW(), "
            f"got {cols['computed_at']['column_default']!r}"
        )

    def test_near_duplicate_clusters_cluster_id_index(self, pg_dsn: str) -> None:
        """Index near_duplicate_clusters_cluster_idx on (cluster_id) must exist."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            indexes = _pg_index_defs(conn, "corpus", "near_duplicate_clusters")

        found = any("cluster_id" in defn.lower() for defn in indexes.values() if defn is not None)
        assert found, (
            "corpus.near_duplicate_clusters: no index found on cluster_id column. "
            f"Indexes present: {list(indexes.keys())}"
        )

    def test_near_duplicate_clusters_fk_cascade(self, pg_dsn: str) -> None:
        """near_duplicate_clusters.chunk_id FK must be ON DELETE CASCADE."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            fks = _pg_fk_info(conn, "corpus", "near_duplicate_clusters")

        chunk_id_fks = [fk for fk in fks if fk["column_name"] == "chunk_id"]
        assert chunk_id_fks, "corpus.near_duplicate_clusters: no FK found on chunk_id column"
        fk = chunk_id_fks[0]
        assert fk["foreign_table_name"] == "chunks", (
            f"near_duplicate_clusters.chunk_id FK references {fk['foreign_table_name']!r}, "
            f"expected 'chunks'"
        )
        assert fk["delete_rule"].upper() == "CASCADE", (
            f"near_duplicate_clusters.chunk_id FK delete_rule is {fk['delete_rule']!r}, "
            f"expected 'CASCADE'"
        )

    def test_down_revision_chain_postgres(self, pg_dsn: str) -> None:
        """Import the migration module and assert down_revision (Postgres path guard)."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.down_revision == "0011_image_embeddings", (
            f"down_revision drift detected: got {mod.down_revision!r}"
        )
