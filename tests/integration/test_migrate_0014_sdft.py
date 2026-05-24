"""Q1-T1 RED — Integration test for the Phase Q Wave 1 migration: 0014_sdft_demonstrations.

Asserts that after applying ``alembic upgrade 0014_sdft_demonstrations``:

- ``sdft_demonstrations`` table exists with the expected columns, types,
  nullability, server defaults, and indexes on ``(dataset_id, source)``
  and ``(trace_id)``.
- ``sdft_demonstrations.dataset_id`` is a FK to ``datasets(id)``
  ON DELETE CASCADE.
- ``sdft_demonstrations.content_hash`` has a UNIQUE constraint
  (idempotency invariant).
- ``sdft_demonstrations.student_messages`` and ``teacher_messages`` are
  JSONB on Postgres, TEXT on SQLite.
- The migration module chains correctly:
  ``revision == "0014_sdft_demonstrations"`` and
  ``down_revision == "0013_search_sessions"``.
- ``downgrade()`` is forward-only: its body is a single ``pass`` statement
  (project convention established by 0008, 0010, 0011, 0012, 0013).

RED condition
-------------
The migration file ``corpus_forge/alembic/versions/0014_sdft_demonstrations.py``
does not yet exist.  Every test in this file should fail with either:

  - ``alembic.util.exc.CommandError: Can't locate revision identified by
    '0014_sdft_demonstrations'``    (the alembic-upgrade tests), or
  - ``ModuleNotFoundError`` / ``ImportError`` on
    ``corpus_forge.alembic.versions.0014_sdft_demonstrations``
    (the revision-attribute tests and the downgrade-AST test).

Both are acceptable RED states.

Run command::

    uv run pytest tests/integration/test_migrate_0014_sdft.py \\
        -m 'not requires_docker' -x 2>&1 | tail -30
"""

from __future__ import annotations

import ast
import importlib
import json
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import pytest

if TYPE_CHECKING:
    import psycopg


class _SqliteColInfo(TypedDict):
    """PRAGMA table_info row, projected to the fields this module asserts on."""

    type: str
    notnull: bool
    dflt_value: str | None
    pk: bool


class _SqliteIndexRow(TypedDict):
    """sqlite_master index row (name + DDL, DDL is None for autoindexes)."""

    name: str
    sql: str | None


pytestmark = [pytest.mark.integration]

# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_MIGRATION_MODULE = "corpus_forge.alembic.versions.0014_sdft_demonstrations"
_MIGRATION_FILE = (
    _REPO_ROOT / "corpus_forge" / "alembic" / "versions" / "0014_sdft_demonstrations.py"
)
_TARGET_REVISION = "0014_sdft_demonstrations"

# ---------------------------------------------------------------------------
# Shared alembic / connection helpers (mirrors test_migrate_0013_search_sessions.py)
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


def _reset_pg_schema(dsn: str) -> None:
    """Drop and recreate the corpus schema + pgvector extension."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS corpus CASCADE")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE SCHEMA IF NOT EXISTS corpus")


# ---------------------------------------------------------------------------
# Postgres introspection helpers
# ---------------------------------------------------------------------------


def _pg_column_info(
    conn: psycopg.Connection,
    table_schema: str,
    table_name: str,
) -> dict[str, dict[str, str | None]]:
    """Return ``{column_name: {data_type, is_nullable, column_default}}``."""
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
    """Return ``{indexname: indexdef}`` from ``pg_indexes``."""
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
    """Return FK constraints for the given table."""
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


def _pg_unique_constraints(
    conn: psycopg.Connection,
    schema_name: str,
    table_name: str,
) -> list[list[str]]:
    """Return list of UNIQUE constraint column-name lists for the given table."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema    = kcu.table_schema
            WHERE tc.constraint_type = 'UNIQUE'
              AND tc.table_schema    = %s
              AND tc.table_name      = %s
            ORDER BY tc.constraint_name, kcu.ordinal_position
            """,
            (schema_name, table_name),
        )
        rows = cur.fetchall()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# SQLite introspection helpers
# ---------------------------------------------------------------------------


def _sqlite_col_map(conn: sqlite3.Connection, table: str) -> dict[str, _SqliteColInfo]:
    """Return PRAGMA table_info as ``{name: {type, notnull, dflt_value, pk}}``."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {
        row[1]: _SqliteColInfo(
            type=str(row[2]).upper(),
            notnull=bool(row[3]),
            dflt_value=row[4],
            pk=bool(row[5]),
        )
        for row in rows
    }


def _sqlite_indexes(conn: sqlite3.Connection, table: str) -> list[_SqliteIndexRow]:
    """Return sqlite_master index rows for *table*."""
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


def _sqlite_unique_indexes(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return column names covered by UNIQUE indexes on *table*."""
    index_rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql LIKE '%UNIQUE%'",
        (table,),
    ).fetchall()
    covered: list[str] = []
    for (idx_name,) in index_rows:
        info = conn.execute(f"PRAGMA index_info({idx_name})").fetchall()
        covered.extend(r[2] for r in info)
    return covered


# ---------------------------------------------------------------------------
# SQLite seed helpers
# ---------------------------------------------------------------------------


def _sqlite_seed_dataset(
    conn: sqlite3.Connection,
    dataset_id: int,
) -> None:
    """Insert a minimal datasets row for FK satisfaction in SQLite."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
        (dataset_id, f"sdft_fixture_{dataset_id}", "text"),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Postgres seed helper
# ---------------------------------------------------------------------------


def _pg_seed_dataset(conn: psycopg.Connection, dataset_id: int) -> None:
    """Insert a minimal corpus.datasets row for FK satisfaction (Postgres)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corpus.datasets (id, name, kind) "
            "VALUES (%s, %s, 'text') ON CONFLICT (id) DO NOTHING",
            (dataset_id, f"sdft_fixture_{dataset_id}"),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Cross-cutting tests (no DB required)
# ---------------------------------------------------------------------------


class TestMigrationModuleAttributes:
    """Assert the migration module's revision chain and forward-only downgrade.

    RED state: fails immediately with ModuleNotFoundError while the migration
    file does not exist.
    """

    def test_revision_value(self) -> None:
        """Migration module must declare ``revision = "0014_sdft_demonstrations"``."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.revision == "0014_sdft_demonstrations", (
            f"Expected revision='0014_sdft_demonstrations', got {mod.revision!r}"
        )

    def test_down_revision_value(self) -> None:
        """Migration module must declare ``down_revision = "0013_search_sessions"``."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.down_revision == "0013_search_sessions", (
            f"Expected down_revision='0013_search_sessions', got {mod.down_revision!r}. "
            "Accidental rebase drift will break the alembic chain in CI."
        )

    def test_downgrade_is_forward_only_pass(self) -> None:
        """``downgrade()`` body must be a single ``pass`` statement.

        Project convention (established by 0008, 0010, 0011, 0012, 0013):
        migrations that create new tables are forward-only.
        """
        assert _MIGRATION_FILE.exists(), (
            f"Migration file not found at {_MIGRATION_FILE}. Write it before this test can pass."
        )
        source = _MIGRATION_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source)

        downgrade_func: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
                downgrade_func = node
                break

        assert downgrade_func is not None, (
            "No ``downgrade()`` function found in 0014_sdft_demonstrations.py"
        )

        body = downgrade_func.body
        assert len(body) == 1 and isinstance(body[0], ast.Pass), (
            f"downgrade() body must be a single ``pass`` statement (forward-only convention). "
            f"Got {len(body)} statement(s): "
            f"{[ast.dump(s) for s in body]}"
        )


# ---------------------------------------------------------------------------
# SQLite tests (no Docker required)
# ---------------------------------------------------------------------------


class TestSQLiteSdftDemonstrations:
    """Schema assertions for the 0014_sdft_demonstrations migration against SQLite.

    Runs without Docker.  Uses ``tmp_path`` for per-test DB isolation.

    RED state: fails with alembic.util.exc.CommandError because
    ``0014_sdft_demonstrations`` revision does not yet exist.
    """

    # ── Table existence ───────────────────────────────────────────────────

    def test_sdft_demonstrations_table_exists(self, tmp_path: Path) -> None:
        """sdft_demonstrations table must exist after upgrade."""
        db_path = tmp_path / "sdft_exists.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            assert _sqlite_table_exists(conn, "sdft_demonstrations"), (
                "sdft_demonstrations table not found in sqlite_master after 0014 upgrade"
            )

    # ── Column set ───────────────────────────────────────────────────────

    def test_sdft_demonstrations_columns(self, tmp_path: Path) -> None:
        """sdft_demonstrations must have exactly the expected columns."""
        db_path = tmp_path / "sdft_cols.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "sdft_demonstrations")

        expected_columns = {
            "id",
            "dataset_id",
            "query",
            "student_messages",
            "teacher_messages",
            "target",
            "source",
            "trace_id",
            "content_hash",
            "created_at",
        }
        assert set(col_map.keys()) == expected_columns, (
            f"sdft_demonstrations columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(col_map.keys())}"
        )

    # ── PK ───────────────────────────────────────────────────────────────

    def test_sdft_demonstrations_id_pk(self, tmp_path: Path) -> None:
        """sdft_demonstrations.id must be an INTEGER PRIMARY KEY."""
        db_path = tmp_path / "sdft_id.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "sdft_demonstrations")

        assert col_map["id"]["pk"], "sdft_demonstrations.id: expected PRIMARY KEY"
        assert "INTEGER" in col_map["id"]["type"], (
            f"sdft_demonstrations.id: expected INTEGER type, got {col_map['id']['type']!r}"
        )

    # ── NOT NULL columns ──────────────────────────────────────────────────

    def test_sdft_demonstrations_dataset_id_not_null(self, tmp_path: Path) -> None:
        """sdft_demonstrations.dataset_id must be INTEGER NOT NULL."""
        db_path = tmp_path / "sdft_dsid.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "sdft_demonstrations")

        assert "INTEGER" in col_map["dataset_id"]["type"], (
            f"sdft_demonstrations.dataset_id: expected INTEGER, "
            f"got {col_map['dataset_id']['type']!r}"
        )
        assert col_map["dataset_id"]["notnull"], "sdft_demonstrations.dataset_id: expected NOT NULL"

    def test_sdft_demonstrations_query_not_null(self, tmp_path: Path) -> None:
        """sdft_demonstrations.query must be TEXT NOT NULL."""
        db_path = tmp_path / "sdft_query.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "sdft_demonstrations")

        assert "TEXT" in col_map["query"]["type"], (
            f"sdft_demonstrations.query: expected TEXT, got {col_map['query']['type']!r}"
        )
        assert col_map["query"]["notnull"], "sdft_demonstrations.query: expected NOT NULL"

    def test_sdft_demonstrations_student_messages_not_null(self, tmp_path: Path) -> None:
        """sdft_demonstrations.student_messages must be TEXT NOT NULL (JSON string in SQLite)."""
        db_path = tmp_path / "sdft_student.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "sdft_demonstrations")

        assert "TEXT" in col_map["student_messages"]["type"], (
            f"sdft_demonstrations.student_messages: expected TEXT, "
            f"got {col_map['student_messages']['type']!r}"
        )
        assert col_map["student_messages"]["notnull"], (
            "sdft_demonstrations.student_messages: expected NOT NULL"
        )

    def test_sdft_demonstrations_teacher_messages_not_null(self, tmp_path: Path) -> None:
        """sdft_demonstrations.teacher_messages must be TEXT NOT NULL (JSON string in SQLite)."""
        db_path = tmp_path / "sdft_teacher.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "sdft_demonstrations")

        assert "TEXT" in col_map["teacher_messages"]["type"], (
            f"sdft_demonstrations.teacher_messages: expected TEXT, "
            f"got {col_map['teacher_messages']['type']!r}"
        )
        assert col_map["teacher_messages"]["notnull"], (
            "sdft_demonstrations.teacher_messages: expected NOT NULL"
        )

    def test_sdft_demonstrations_target_not_null(self, tmp_path: Path) -> None:
        """sdft_demonstrations.target must be TEXT NOT NULL."""
        db_path = tmp_path / "sdft_target.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "sdft_demonstrations")

        assert "TEXT" in col_map["target"]["type"], (
            f"sdft_demonstrations.target: expected TEXT, got {col_map['target']['type']!r}"
        )
        assert col_map["target"]["notnull"], "sdft_demonstrations.target: expected NOT NULL"

    def test_sdft_demonstrations_source_not_null(self, tmp_path: Path) -> None:
        """sdft_demonstrations.source must be TEXT NOT NULL."""
        db_path = tmp_path / "sdft_source.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "sdft_demonstrations")

        assert "TEXT" in col_map["source"]["type"], (
            f"sdft_demonstrations.source: expected TEXT, got {col_map['source']['type']!r}"
        )
        assert col_map["source"]["notnull"], "sdft_demonstrations.source: expected NOT NULL"

    def test_sdft_demonstrations_content_hash_not_null(self, tmp_path: Path) -> None:
        """sdft_demonstrations.content_hash must be TEXT NOT NULL."""
        db_path = tmp_path / "sdft_chash.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "sdft_demonstrations")

        assert "TEXT" in col_map["content_hash"]["type"], (
            f"sdft_demonstrations.content_hash: expected TEXT, "
            f"got {col_map['content_hash']['type']!r}"
        )
        assert col_map["content_hash"]["notnull"], (
            "sdft_demonstrations.content_hash: expected NOT NULL"
        )

    # ── Nullable columns ──────────────────────────────────────────────────

    def test_sdft_demonstrations_trace_id_nullable(self, tmp_path: Path) -> None:
        """sdft_demonstrations.trace_id must be TEXT and nullable."""
        db_path = tmp_path / "sdft_trace.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "sdft_demonstrations")

        assert "TEXT" in col_map["trace_id"]["type"], (
            f"sdft_demonstrations.trace_id: expected TEXT, got {col_map['trace_id']['type']!r}"
        )
        assert not col_map["trace_id"]["notnull"], (
            "sdft_demonstrations.trace_id: expected NULL-able (optional tracing field)"
        )

    # ── created_at default ────────────────────────────────────────────────

    def test_sdft_demonstrations_created_at_not_null_with_default(self, tmp_path: Path) -> None:
        """sdft_demonstrations.created_at must be TEXT NOT NULL with a server-default timestamp."""
        db_path = tmp_path / "sdft_cat.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "sdft_demonstrations")

        assert col_map["created_at"]["notnull"], "sdft_demonstrations.created_at: expected NOT NULL"
        dflt = (col_map["created_at"]["dflt_value"] or "").lower()
        assert "current_timestamp" in dflt or ("datetime" in dflt and "now" in dflt), (
            f"sdft_demonstrations.created_at: expected a CURRENT_TIMESTAMP or datetime('now') "
            f"server default, got {col_map['created_at']['dflt_value']!r}"
        )

    # ── UNIQUE constraint on content_hash ────────────────────────────────

    def test_sdft_demonstrations_content_hash_unique(self, tmp_path: Path) -> None:
        """content_hash must have a UNIQUE constraint (idempotency invariant)."""
        db_path = tmp_path / "sdft_unique.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            _sqlite_seed_dataset(conn, dataset_id=9001)

            # Insert first row.
            conn.execute(
                "INSERT INTO sdft_demonstrations "
                "(dataset_id, query, student_messages, teacher_messages, "
                "target, source, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    9001,
                    "What is eigenvalue decomposition?",
                    json.dumps([{"role": "assistant", "content": "Some prior answer"}]),
                    json.dumps([{"role": "user", "content": "Better answer prompt"}]),
                    "The corrected target text.",
                    "curation_commit",
                    "sha256-abc123-unique-test",
                ),
            )
            conn.commit()

            # Inserting a duplicate content_hash must raise IntegrityError.
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO sdft_demonstrations "
                    "(dataset_id, query, student_messages, teacher_messages, "
                    "target, source, content_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        9001,
                        "Different query",
                        json.dumps([]),
                        json.dumps([]),
                        "Different target",
                        "record_demonstration",
                        "sha256-abc123-unique-test",  # same hash — must conflict
                    ),
                )

    # ── Indexes ───────────────────────────────────────────────────────────

    def test_sdft_demonstrations_dataset_id_source_index_exists(self, tmp_path: Path) -> None:
        """An index on (dataset_id, source) must exist."""
        db_path = tmp_path / "sdft_idx_ds.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            indexes = _sqlite_indexes(conn, "sdft_demonstrations")

        found = False
        for idx in indexes:
            sql = (idx["sql"] or "").lower()
            if "dataset_id" in sql and "source" in sql:
                found = True
                break
            if idx["sql"] is None:
                with sqlite3.connect(str(db_path)) as conn2:
                    info_rows = conn2.execute(f"PRAGMA index_info({idx['name']})").fetchall()
                    cols_in_idx = {r[2] for r in info_rows}
                if "dataset_id" in cols_in_idx and "source" in cols_in_idx:
                    found = True
                    break

        assert found, (
            "sdft_demonstrations: no index found covering (dataset_id, source). "
            f"Indexes present: {[i['name'] for i in indexes]}"
        )

    def test_sdft_demonstrations_trace_id_index_exists(self, tmp_path: Path) -> None:
        """An index on (trace_id) must exist."""
        db_path = tmp_path / "sdft_idx_trace.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            indexes = _sqlite_indexes(conn, "sdft_demonstrations")

        found = False
        for idx in indexes:
            sql = (idx["sql"] or "").lower()
            if "trace_id" in sql:
                found = True
                break
            if idx["sql"] is None:
                with sqlite3.connect(str(db_path)) as conn2:
                    info_rows = conn2.execute(f"PRAGMA index_info({idx['name']})").fetchall()
                    cols_in_idx = {r[2] for r in info_rows}
                if "trace_id" in cols_in_idx:
                    found = True
                    break

        assert found, (
            "sdft_demonstrations: no index found covering trace_id. "
            f"Indexes present: {[i['name'] for i in indexes]}"
        )

    # ── FK cascade on dataset delete ──────────────────────────────────────

    def test_sdft_demonstrations_fk_cascade_on_dataset_delete(self, tmp_path: Path) -> None:
        """Deleting a datasets row must cascade-delete its sdft_demonstrations rows."""
        db_path = tmp_path / "sdft_fk.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            _sqlite_seed_dataset(conn, dataset_id=9002)

            conn.execute(
                "INSERT INTO sdft_demonstrations "
                "(dataset_id, query, student_messages, teacher_messages, "
                "target, source, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    9002,
                    "FK cascade test query",
                    json.dumps([{"role": "assistant", "content": "prior"}]),
                    json.dumps([{"role": "user", "content": "prompt"}]),
                    "target text",
                    "curation_commit",
                    "sha256-fk-cascade-test",
                ),
            )
            conn.commit()

            count_before = conn.execute(
                "SELECT COUNT(*) FROM sdft_demonstrations WHERE dataset_id = 9002"
            ).fetchone()[0]
            assert count_before == 1

            conn.execute("DELETE FROM datasets WHERE id = 9002")
            conn.commit()

            count_after = conn.execute(
                "SELECT COUNT(*) FROM sdft_demonstrations WHERE dataset_id = 9002"
            ).fetchone()[0]
            assert count_after == 0, (
                "sdft_demonstrations: FK ON DELETE CASCADE did not remove demonstration row "
                "when parent dataset was deleted"
            )

    # ── Round-trip: JSON in student/teacher fields ────────────────────────

    def test_sdft_demonstrations_json_roundtrip(self, tmp_path: Path) -> None:
        """student_messages and teacher_messages survive a JSON round-trip via TEXT."""
        db_path = tmp_path / "sdft_json.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        student_payload = [
            {"role": "assistant", "content": "Original model output with incorrect claim."}
        ]
        teacher_payload = [
            {"role": "user", "content": "The correct claim is: eigenvalues must be real."},
            {"role": "system", "content": "You are a physics expert."},
        ]

        with sqlite3.connect(str(db_path)) as conn:
            _sqlite_seed_dataset(conn, dataset_id=9003)
            conn.execute(
                "INSERT INTO sdft_demonstrations "
                "(dataset_id, query, student_messages, teacher_messages, "
                "target, source, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    9003,
                    "JSON round-trip test query",
                    json.dumps(student_payload),
                    json.dumps(teacher_payload),
                    "Corrected description.",
                    "record_demonstration",
                    "sha256-json-roundtrip",
                ),
            )
            conn.commit()

            row = conn.execute(
                "SELECT student_messages, teacher_messages FROM sdft_demonstrations "
                "WHERE content_hash = 'sha256-json-roundtrip'"
            ).fetchone()

        assert row is not None, "Expected inserted row not found"
        assert json.loads(row[0]) == student_payload, (
            f"student_messages round-trip failed: {row[0]!r}"
        )
        assert json.loads(row[1]) == teacher_payload, (
            f"teacher_messages round-trip failed: {row[1]!r}"
        )

    # ── down_revision guard ───────────────────────────────────────────────

    def test_down_revision_chain_sqlite(self, tmp_path: Path) -> None:
        """Import the migration module and assert down_revision (SQLite path guard)."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.down_revision == "0013_search_sessions", (
            f"down_revision drift detected: got {mod.down_revision!r}"
        )

    # ── Insert with all fields populated ─────────────────────────────────

    def test_sdft_demonstrations_insert_all_fields(self, tmp_path: Path) -> None:
        """Full insert (all fields including trace_id) succeeds without error."""
        db_path = tmp_path / "sdft_full.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            _sqlite_seed_dataset(conn, dataset_id=9004)
            conn.execute(
                "INSERT INTO sdft_demonstrations "
                "(dataset_id, query, student_messages, teacher_messages, "
                "target, source, trace_id, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    9004,
                    "Full insert query",
                    json.dumps([{"role": "assistant", "content": "student answer"}]),
                    json.dumps([{"role": "user", "content": "teacher prompt"}]),
                    "Expected target",
                    "claude_code",
                    "trace-abc-001",
                    "sha256-full-insert-test",
                ),
            )
            conn.commit()

            row = conn.execute(
                "SELECT trace_id, source FROM sdft_demonstrations "
                "WHERE content_hash = 'sha256-full-insert-test'"
            ).fetchone()

        assert row is not None, "Inserted row not found"
        assert row[0] == "trace-abc-001", f"trace_id mismatch: {row[0]!r}"
        assert row[1] == "claude_code", f"source mismatch: {row[1]!r}"

    # ── Insert without trace_id (NULL) ────────────────────────────────────

    def test_sdft_demonstrations_insert_without_trace_id(self, tmp_path: Path) -> None:
        """Insert without trace_id leaves that column NULL."""
        db_path = tmp_path / "sdft_notrace.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            _sqlite_seed_dataset(conn, dataset_id=9005)
            conn.execute(
                "INSERT INTO sdft_demonstrations "
                "(dataset_id, query, student_messages, teacher_messages, "
                "target, source, content_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    9005,
                    "No trace_id query",
                    json.dumps([{"role": "assistant", "content": "x"}]),
                    json.dumps([{"role": "user", "content": "y"}]),
                    "target",
                    "gemini",
                    "sha256-no-trace",
                ),
            )
            conn.commit()

            row = conn.execute(
                "SELECT trace_id FROM sdft_demonstrations WHERE content_hash = 'sha256-no-trace'"
            ).fetchone()

        assert row is not None
        assert row[0] is None, f"Expected trace_id=NULL when omitted, got {row[0]!r}"

    # ── ON CONFLICT DO NOTHING idempotency (dedup pattern) ───────────────

    def test_sdft_demonstrations_on_conflict_do_nothing(self, tmp_path: Path) -> None:
        """INSERT OR IGNORE on duplicate content_hash leaves table unchanged."""
        db_path = tmp_path / "sdft_dedup.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            _sqlite_seed_dataset(conn, dataset_id=9006)

            def _insert(source: str, hash_val: str) -> None:
                conn.execute(
                    "INSERT OR IGNORE INTO sdft_demonstrations "
                    "(dataset_id, query, student_messages, teacher_messages, "
                    "target, source, content_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        9006,
                        "Dedup test query",
                        json.dumps([{"role": "assistant", "content": "prior"}]),
                        json.dumps([{"role": "user", "content": "prompt"}]),
                        "target",
                        source,
                        hash_val,
                    ),
                )
                conn.commit()

            _insert("curation_commit", "sha256-dedup-001")
            count_after_first = conn.execute("SELECT COUNT(*) FROM sdft_demonstrations").fetchone()[
                0
            ]

            # Identical hash — INSERT OR IGNORE must silently skip.
            _insert("rate_search_result", "sha256-dedup-001")
            count_after_second = conn.execute(
                "SELECT COUNT(*) FROM sdft_demonstrations"
            ).fetchone()[0]

        assert count_after_first == 1, "Expected 1 row after first insert"
        assert count_after_second == 1, (
            "Expected INSERT OR IGNORE to keep row count at 1 on duplicate content_hash"
        )


# ---------------------------------------------------------------------------
# Postgres tests (requires Docker)
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker
class TestPostgresSdftDemonstrations:
    """Schema assertions for the 0014_sdft_demonstrations migration against Postgres.

    Requires Docker + testcontainers (skipped automatically when unavailable).
    Uses the session-scoped ``pg_dsn`` fixture from the root conftest.

    RED state: fails with alembic.util.exc.CommandError because
    ``0014_sdft_demonstrations`` revision does not yet exist.
    """

    # ── Table existence ───────────────────────────────────────────────────

    def test_sdft_demonstrations_table_exists(self, pg_dsn: str) -> None:
        """sdft_demonstrations table must exist in corpus schema after upgrade."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "sdft_demonstrations")

        assert cols, (
            "corpus.sdft_demonstrations table not found after 0014_sdft_demonstrations upgrade "
            "(information_schema.columns returned no rows)"
        )

    # ── Column set ───────────────────────────────────────────────────────

    def test_sdft_demonstrations_columns(self, pg_dsn: str) -> None:
        """sdft_demonstrations must have exactly the expected column set."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "sdft_demonstrations")

        expected_columns = {
            "id",
            "dataset_id",
            "query",
            "student_messages",
            "teacher_messages",
            "target",
            "source",
            "trace_id",
            "content_hash",
            "created_at",
        }
        assert set(cols.keys()) == expected_columns, (
            f"corpus.sdft_demonstrations columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(cols.keys())}"
        )

    # ── Column types ──────────────────────────────────────────────────────

    def test_sdft_demonstrations_id_bigint_not_null(self, pg_dsn: str) -> None:
        """sdft_demonstrations.id must be bigint NOT NULL (BIGSERIAL PK)."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "sdft_demonstrations")

        assert cols["id"]["data_type"] == "bigint", (
            f"sdft_demonstrations.id: expected bigint, got {cols['id']['data_type']!r}"
        )
        assert cols["id"]["is_nullable"] == "NO", (
            f"sdft_demonstrations.id: expected NOT NULL, got {cols['id']['is_nullable']!r}"
        )

    def test_sdft_demonstrations_dataset_id_bigint_not_null(self, pg_dsn: str) -> None:
        """sdft_demonstrations.dataset_id must be bigint NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "sdft_demonstrations")

        assert cols["dataset_id"]["data_type"] == "bigint", (
            f"sdft_demonstrations.dataset_id: expected bigint, "
            f"got {cols['dataset_id']['data_type']!r}"
        )
        assert cols["dataset_id"]["is_nullable"] == "NO"

    def test_sdft_demonstrations_student_messages_jsonb_not_null(self, pg_dsn: str) -> None:
        """sdft_demonstrations.student_messages must be jsonb NOT NULL on Postgres."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "sdft_demonstrations")

        assert cols["student_messages"]["data_type"] == "jsonb", (
            f"sdft_demonstrations.student_messages: expected jsonb, "
            f"got {cols['student_messages']['data_type']!r}"
        )
        assert cols["student_messages"]["is_nullable"] == "NO"

    def test_sdft_demonstrations_teacher_messages_jsonb_not_null(self, pg_dsn: str) -> None:
        """sdft_demonstrations.teacher_messages must be jsonb NOT NULL on Postgres."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "sdft_demonstrations")

        assert cols["teacher_messages"]["data_type"] == "jsonb", (
            f"sdft_demonstrations.teacher_messages: expected jsonb, "
            f"got {cols['teacher_messages']['data_type']!r}"
        )
        assert cols["teacher_messages"]["is_nullable"] == "NO"

    def test_sdft_demonstrations_content_hash_text_not_null(self, pg_dsn: str) -> None:
        """sdft_demonstrations.content_hash must be text NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "sdft_demonstrations")

        assert cols["content_hash"]["data_type"] == "text", (
            f"sdft_demonstrations.content_hash: expected text, "
            f"got {cols['content_hash']['data_type']!r}"
        )
        assert cols["content_hash"]["is_nullable"] == "NO"

    def test_sdft_demonstrations_trace_id_text_nullable(self, pg_dsn: str) -> None:
        """sdft_demonstrations.trace_id must be text and nullable."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "sdft_demonstrations")

        assert cols["trace_id"]["data_type"] == "text", (
            f"sdft_demonstrations.trace_id: expected text, got {cols['trace_id']['data_type']!r}"
        )
        assert cols["trace_id"]["is_nullable"] == "YES", (
            "sdft_demonstrations.trace_id: expected NULL-able"
        )

    def test_sdft_demonstrations_created_at_timestamptz_not_null_default_now(
        self, pg_dsn: str
    ) -> None:
        """sdft_demonstrations.created_at must be TIMESTAMPTZ NOT NULL DEFAULT NOW()."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "sdft_demonstrations")

        assert cols["created_at"]["data_type"] == "timestamp with time zone", (
            f"sdft_demonstrations.created_at: expected 'timestamp with time zone', "
            f"got {cols['created_at']['data_type']!r}"
        )
        assert cols["created_at"]["is_nullable"] == "NO"
        default_val = (cols["created_at"]["column_default"] or "").lower()
        assert "now" in default_val, (
            f"sdft_demonstrations.created_at: expected DEFAULT NOW(), "
            f"got {cols['created_at']['column_default']!r}"
        )

    # ── FK cascade ────────────────────────────────────────────────────────

    def test_sdft_demonstrations_dataset_id_fk_cascade(self, pg_dsn: str) -> None:
        """sdft_demonstrations.dataset_id FK must be ON DELETE CASCADE."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            fks = _pg_fk_info(conn, "corpus", "sdft_demonstrations")

        ds_fks = [fk for fk in fks if fk["column_name"] == "dataset_id"]
        assert ds_fks, "corpus.sdft_demonstrations: no FK found on dataset_id column"
        fk = ds_fks[0]
        assert fk["foreign_table_name"] == "datasets", (
            f"sdft_demonstrations.dataset_id FK references {fk['foreign_table_name']!r}, "
            f"expected 'datasets'"
        )
        assert fk["delete_rule"].upper() == "CASCADE", (
            f"sdft_demonstrations.dataset_id FK delete_rule is {fk['delete_rule']!r}, "
            f"expected 'CASCADE'"
        )

    # ── Indexes ───────────────────────────────────────────────────────────

    def test_sdft_demonstrations_dataset_id_source_index(self, pg_dsn: str) -> None:
        """Index on (dataset_id, source) must exist."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            indexes = _pg_index_defs(conn, "corpus", "sdft_demonstrations")

        found = any(
            "dataset_id" in defn.lower() and "source" in defn.lower()
            for defn in indexes.values()
            if defn is not None
        )
        assert found, (
            "corpus.sdft_demonstrations: no index found on (dataset_id, source). "
            f"Indexes present: {list(indexes.keys())}"
        )

    def test_sdft_demonstrations_trace_id_index(self, pg_dsn: str) -> None:
        """Index on (trace_id) must exist."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            indexes = _pg_index_defs(conn, "corpus", "sdft_demonstrations")

        found = any("trace_id" in defn.lower() for defn in indexes.values() if defn is not None)
        assert found, (
            "corpus.sdft_demonstrations: no index found on trace_id. "
            f"Indexes present: {list(indexes.keys())}"
        )

    # ── UNIQUE constraint on content_hash ────────────────────────────────

    def test_sdft_demonstrations_content_hash_unique_constraint(self, pg_dsn: str) -> None:
        """content_hash must have a UNIQUE constraint on Postgres."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            unique_cols = _pg_unique_constraints(conn, "corpus", "sdft_demonstrations")

        assert "content_hash" in unique_cols, (
            f"corpus.sdft_demonstrations: expected UNIQUE constraint on content_hash; "
            f"found unique cols: {unique_cols}"
        )

    # ── down_revision guard ───────────────────────────────────────────────

    def test_down_revision_chain_postgres(self, pg_dsn: str) -> None:
        """Import the migration module and assert down_revision (Postgres path guard)."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.down_revision == "0013_search_sessions", (
            f"down_revision drift detected: got {mod.down_revision!r}"
        )
