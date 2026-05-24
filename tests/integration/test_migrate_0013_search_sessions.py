"""Integration test for the Phase P Wave 1 migration: 0013_search_sessions.

Asserts that after applying ``alembic upgrade 0013_search_sessions``:

- ``search_sessions`` table exists with the expected columns, types,
  nullability, server defaults, and a composite index on
  ``(dataset_id, started_at)``.
- ``search_result_events`` table exists with the expected columns, types,
  nullability, server defaults, and a composite index on
  ``(session_id, chunk_id)``.
- ``search_sessions.dataset_id`` is a FK to ``datasets(id)`` ON DELETE CASCADE.
- ``search_result_events.session_id`` is a FK to ``search_sessions(id)``
  ON DELETE CASCADE.
- ``search_result_events.chunk_id`` is a FK to ``chunks(id)``
  ON DELETE CASCADE.
- ``search_result_events.replacement_chunk_id`` is a nullable FK to
  ``chunks(id)`` (no ON DELETE CASCADE required — it is a weak reference).
- The migration module chains correctly:
  ``revision == "0013_search_sessions"`` and
  ``down_revision == "0012_analyze_signals"``.
- ``downgrade()`` is forward-only: its body is a single ``pass`` statement
  (project convention established by 0008, 0010, 0011, 0012).

RED condition
-------------
The migration file ``corpus_forge/alembic/versions/0013_search_sessions.py``
does not yet exist.  Every test in this file should fail with either:

  - ``alembic.util.exc.CommandError: Can't locate revision identified by
    '0013_search_sessions'``    (the alembic-upgrade tests), or
  - ``ModuleNotFoundError`` / ``ImportError`` on
    ``corpus_forge.alembic.versions.0013_search_sessions``
    (the revision-attribute tests and the downgrade-AST test).

Both are acceptable RED states.
"""

from __future__ import annotations

import ast
import importlib
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import psycopg

pytestmark = [pytest.mark.integration]

# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_MIGRATION_MODULE = "corpus_forge.alembic.versions.0013_search_sessions"
_MIGRATION_FILE = _REPO_ROOT / "corpus_forge" / "alembic" / "versions" / "0013_search_sessions.py"
_TARGET_REVISION = "0013_search_sessions"

# ---------------------------------------------------------------------------
# Shared alembic / connection helpers
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


# ---------------------------------------------------------------------------
# Postgres introspection helpers (mirrors test_migrate_0012_analyze.py)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# SQLite introspection helpers
# ---------------------------------------------------------------------------


def _sqlite_col_map(conn: sqlite3.Connection, table: str) -> dict[str, dict]:
    """Return PRAGMA table_info as ``{name: {type, notnull, dflt_value, pk}}``."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {
        row[1]: {
            "type": row[2].upper(),
            "notnull": bool(row[3]),
            "dflt_value": row[4],
            "pk": bool(row[5]),
        }
        for row in rows
    }


def _sqlite_indexes(conn: sqlite3.Connection, table: str) -> list[dict]:
    """Return sqlite_master index rows for *table* as a list of dicts."""
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?",
        (table,),
    ).fetchall()
    return [{"name": row[0], "sql": row[1]} for row in rows]


def _sqlite_table_exists(conn: sqlite3.Connection, table: str) -> bool:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchall()
    return len(rows) == 1


# ---------------------------------------------------------------------------
# SQLite seed helpers
# ---------------------------------------------------------------------------


def _sqlite_seed_dataset_and_chunks(
    conn: sqlite3.Connection,
    dataset_id: int,
    chunk_ids: list[int],
) -> None:
    """Insert minimal datasets + chunks rows for FK satisfaction in SQLite.

    SQLite's chunks table has no CHECK constraint on document_id / conversation_id
    (that is a Postgres-only constraint in this schema), so we can insert
    chunks directly without a parent document.
    """
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "INSERT OR IGNORE INTO datasets (id, name, kind) VALUES (?, ?, ?)",
        (dataset_id, f"ds_fixture_{dataset_id}", "text"),
    )
    for cid in chunk_ids:
        conn.execute(
            "INSERT OR IGNORE INTO chunks "
            "(id, chunk_index, text, content_hash, token_count) "
            "VALUES (?, 0, 'dummy', ?, 10)",
            (cid, f"hash_{cid}"),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Postgres seed helper
# ---------------------------------------------------------------------------


def _pg_seed_dataset_and_chunks(
    conn: psycopg.Connection,
    dataset_id: int,
    chunk_ids: list[int],
) -> None:
    """Insert minimal corpus.datasets + corpus.chunks rows for FK satisfaction (Postgres).

    corpus.chunks.chunks_check enforces that exactly one of (document_id,
    conversation_id) is NOT NULL; we therefore seed a parent document first.
    Mirrors the pattern from test_analyze_dedup_persist.py::_pg_seed_chunks.
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO corpus.datasets (id, name, kind) "
            "VALUES (%s, %s, 'text') ON CONFLICT (id) DO NOTHING",
            (dataset_id, f"ss_fixture_{dataset_id}"),
        )
        cur.execute(
            "INSERT INTO corpus.documents "
            "(id, dataset_id, source_uri, content_hash, text) "
            "VALUES (%s, %s, %s, %s, 'fixture body') ON CONFLICT (id) DO NOTHING",
            (dataset_id, dataset_id, f"fixture://ss/{dataset_id}", f"doc_hash_{dataset_id}"),
        )
        for cid in chunk_ids:
            cur.execute(
                "INSERT INTO corpus.chunks "
                "(id, document_id, chunk_index, text, content_hash, token_count) "
                "VALUES (%s, %s, %s, 'dummy', %s, 10) "
                "ON CONFLICT (id) DO NOTHING",
                (cid, dataset_id, cid, f"chk_hash_{cid}"),
            )
    conn.commit()


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
        """Migration module must declare ``revision = "0013_search_sessions"``."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.revision == "0013_search_sessions", (
            f"Expected revision='0013_search_sessions', got {mod.revision!r}"
        )

    def test_down_revision_value(self) -> None:
        """Migration module must declare ``down_revision = "0012_analyze_signals"``."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.down_revision == "0012_analyze_signals", (
            f"Expected down_revision='0012_analyze_signals', got {mod.down_revision!r}.  "
            "Accidental rebase drift will break the alembic chain in CI."
        )

    def test_downgrade_is_forward_only_pass(self) -> None:
        """``downgrade()`` body must be a single ``pass`` statement.

        Project convention (established by 0008, 0010, 0011, 0012): migrations
        that create new tables are forward-only.
        """
        assert _MIGRATION_FILE.exists(), (
            f"Migration file not found at {_MIGRATION_FILE}. Write it before this test can proceed."
        )
        source = _MIGRATION_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source)

        downgrade_func: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
                downgrade_func = node
                break

        assert downgrade_func is not None, (
            "No ``downgrade()`` function found in 0013_search_sessions.py"
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


class TestSQLiteSearchSessions:
    """Schema assertions for the 0013_search_sessions migration against SQLite.

    Runs without Docker.  Uses ``tmp_path`` for per-test DB isolation.

    RED state: fails with alembic.util.exc.CommandError because
    ``0013_search_sessions`` revision does not yet exist.
    """

    # ── search_sessions ──────────────────────────────────────────────────

    def test_search_sessions_table_exists(self, tmp_path: Path) -> None:
        """search_sessions table must exist after upgrade."""
        db_path = tmp_path / "ss_exists.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            assert _sqlite_table_exists(conn, "search_sessions"), (
                "search_sessions table not found in sqlite_master after 0013 upgrade"
            )

    def test_search_sessions_columns(self, tmp_path: Path) -> None:
        """search_sessions must have exactly the expected columns."""
        db_path = tmp_path / "ss_cols.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_sessions")

        expected_columns = {"id", "query", "dataset_id", "started_at", "client", "host"}
        assert set(col_map.keys()) == expected_columns, (
            f"search_sessions columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(col_map.keys())}"
        )

    def test_search_sessions_id_pk(self, tmp_path: Path) -> None:
        """search_sessions.id must be an INTEGER PRIMARY KEY."""
        db_path = tmp_path / "ss_id.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_sessions")

        assert col_map["id"]["pk"], "search_sessions.id: expected PRIMARY KEY"
        assert "INTEGER" in col_map["id"]["type"], (
            f"search_sessions.id: expected INTEGER type, got {col_map['id']['type']!r}"
        )

    def test_search_sessions_query_not_null(self, tmp_path: Path) -> None:
        """search_sessions.query must be TEXT NOT NULL."""
        db_path = tmp_path / "ss_query.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_sessions")

        assert "TEXT" in col_map["query"]["type"], (
            f"search_sessions.query: expected TEXT, got {col_map['query']['type']!r}"
        )
        assert col_map["query"]["notnull"], "search_sessions.query: expected NOT NULL"

    def test_search_sessions_dataset_id_not_null(self, tmp_path: Path) -> None:
        """search_sessions.dataset_id must be INTEGER NOT NULL."""
        db_path = tmp_path / "ss_dsid.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_sessions")

        assert "INTEGER" in col_map["dataset_id"]["type"], (
            f"search_sessions.dataset_id: expected INTEGER, got {col_map['dataset_id']['type']!r}"
        )
        assert col_map["dataset_id"]["notnull"], "search_sessions.dataset_id: expected NOT NULL"

    def test_search_sessions_started_at_not_null_with_default(self, tmp_path: Path) -> None:
        """search_sessions.started_at must be TEXT NOT NULL with a server-default timestamp."""
        db_path = tmp_path / "ss_sat.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_sessions")

        assert col_map["started_at"]["notnull"], "search_sessions.started_at: expected NOT NULL"
        dflt = (col_map["started_at"]["dflt_value"] or "").lower()
        # Accept either (CURRENT_TIMESTAMP) or (datetime('now')) per project convention.
        assert "current_timestamp" in dflt or ("datetime" in dflt and "now" in dflt), (
            f"search_sessions.started_at: expected a CURRENT_TIMESTAMP or datetime('now') "
            f"server default, got {col_map['started_at']['dflt_value']!r}"
        )

    def test_search_sessions_client_nullable(self, tmp_path: Path) -> None:
        """search_sessions.client must be TEXT and nullable (optional client identifier)."""
        db_path = tmp_path / "ss_client.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_sessions")

        assert "TEXT" in col_map["client"]["type"], (
            f"search_sessions.client: expected TEXT, got {col_map['client']['type']!r}"
        )
        assert not col_map["client"]["notnull"], (
            "search_sessions.client: expected NULL-able (optional field)"
        )

    def test_search_sessions_host_nullable(self, tmp_path: Path) -> None:
        """search_sessions.host must be TEXT and nullable."""
        db_path = tmp_path / "ss_host.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_sessions")

        assert "TEXT" in col_map["host"]["type"], (
            f"search_sessions.host: expected TEXT, got {col_map['host']['type']!r}"
        )
        assert not col_map["host"]["notnull"], (
            "search_sessions.host: expected NULL-able (optional field)"
        )

    def test_search_sessions_dataset_id_started_at_index_exists(self, tmp_path: Path) -> None:
        """An index on (dataset_id, started_at) must exist for search_sessions."""
        db_path = tmp_path / "ss_idx.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            indexes = _sqlite_indexes(conn, "search_sessions")

        found = False
        for idx in indexes:
            sql = (idx["sql"] or "").lower()
            if "dataset_id" in sql and "started_at" in sql:
                found = True
                break
            if idx["sql"] is None:
                with sqlite3.connect(str(db_path)) as conn2:
                    info_rows = conn2.execute(f"PRAGMA index_info({idx['name']})").fetchall()
                cols_in_idx = {r[2] for r in info_rows}
                if "dataset_id" in cols_in_idx and "started_at" in cols_in_idx:
                    found = True
                    break

        assert found, (
            "search_sessions: no index found covering (dataset_id, started_at). "
            f"Indexes present: {[i['name'] for i in indexes]}"
        )

    def test_search_sessions_fk_cascade_on_dataset_delete(self, tmp_path: Path) -> None:
        """Deleting a datasets row must cascade-delete its search_sessions rows."""
        db_path = tmp_path / "ss_fk.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            _sqlite_seed_dataset_and_chunks(conn, dataset_id=8001, chunk_ids=[])

            conn.execute(
                "INSERT INTO search_sessions (dataset_id, query, started_at) "
                "VALUES (8001, 'what is corpus-forge?', CURRENT_TIMESTAMP)"
            )
            conn.commit()

            count_before = conn.execute(
                "SELECT COUNT(*) FROM search_sessions WHERE dataset_id = 8001"
            ).fetchone()[0]
            assert count_before == 1

            conn.execute("DELETE FROM datasets WHERE id = 8001")
            conn.commit()

            count_after = conn.execute(
                "SELECT COUNT(*) FROM search_sessions WHERE dataset_id = 8001"
            ).fetchone()[0]
            assert count_after == 0, (
                "search_sessions: FK ON DELETE CASCADE did not remove session row "
                "when parent dataset was deleted"
            )

    # ── search_result_events ─────────────────────────────────────────────

    def test_search_result_events_table_exists(self, tmp_path: Path) -> None:
        """search_result_events table must exist after upgrade."""
        db_path = tmp_path / "sre_exists.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            assert _sqlite_table_exists(conn, "search_result_events"), (
                "search_result_events table not found in sqlite_master after 0013 upgrade"
            )

    def test_search_result_events_columns(self, tmp_path: Path) -> None:
        """search_result_events must have exactly the expected columns."""
        db_path = tmp_path / "sre_cols.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_result_events")

        expected_columns = {
            "id",
            "session_id",
            "chunk_id",
            "signal",
            "value",
            "source",
            "created_at",
            "replacement_chunk_id",
        }
        assert set(col_map.keys()) == expected_columns, (
            f"search_result_events columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(col_map.keys())}"
        )

    def test_search_result_events_id_pk(self, tmp_path: Path) -> None:
        """search_result_events.id must be an INTEGER PRIMARY KEY."""
        db_path = tmp_path / "sre_id.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_result_events")

        assert col_map["id"]["pk"], "search_result_events.id: expected PRIMARY KEY"
        assert "INTEGER" in col_map["id"]["type"], (
            f"search_result_events.id: expected INTEGER type, got {col_map['id']['type']!r}"
        )

    def test_search_result_events_session_id_not_null(self, tmp_path: Path) -> None:
        """search_result_events.session_id must be INTEGER NOT NULL."""
        db_path = tmp_path / "sre_sid.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_result_events")

        assert "INTEGER" in col_map["session_id"]["type"], (
            f"search_result_events.session_id: expected INTEGER, "
            f"got {col_map['session_id']['type']!r}"
        )
        assert col_map["session_id"]["notnull"], (
            "search_result_events.session_id: expected NOT NULL"
        )

    def test_search_result_events_chunk_id_not_null(self, tmp_path: Path) -> None:
        """search_result_events.chunk_id must be INTEGER NOT NULL."""
        db_path = tmp_path / "sre_cid.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_result_events")

        assert "INTEGER" in col_map["chunk_id"]["type"], (
            f"search_result_events.chunk_id: expected INTEGER, got {col_map['chunk_id']['type']!r}"
        )
        assert col_map["chunk_id"]["notnull"], "search_result_events.chunk_id: expected NOT NULL"

    def test_search_result_events_signal_not_null(self, tmp_path: Path) -> None:
        """search_result_events.signal must be TEXT NOT NULL."""
        db_path = tmp_path / "sre_signal.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_result_events")

        assert "TEXT" in col_map["signal"]["type"], (
            f"search_result_events.signal: expected TEXT, got {col_map['signal']['type']!r}"
        )
        assert col_map["signal"]["notnull"], "search_result_events.signal: expected NOT NULL"

    def test_search_result_events_value_real_nullable(self, tmp_path: Path) -> None:
        """search_result_events.value must be REAL and nullable."""
        db_path = tmp_path / "sre_value.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_result_events")

        assert "REAL" in col_map["value"]["type"], (
            f"search_result_events.value: expected REAL, got {col_map['value']['type']!r}"
        )
        assert not col_map["value"]["notnull"], (
            "search_result_events.value: expected NULL-able (numeric rating is optional)"
        )

    def test_search_result_events_source_not_null(self, tmp_path: Path) -> None:
        """search_result_events.source must be TEXT NOT NULL."""
        db_path = tmp_path / "sre_source.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_result_events")

        assert "TEXT" in col_map["source"]["type"], (
            f"search_result_events.source: expected TEXT, got {col_map['source']['type']!r}"
        )
        assert col_map["source"]["notnull"], "search_result_events.source: expected NOT NULL"

    def test_search_result_events_created_at_not_null_with_default(self, tmp_path: Path) -> None:
        """search_result_events.created_at must be TEXT NOT NULL with a server default."""
        db_path = tmp_path / "sre_cat.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_result_events")

        assert col_map["created_at"]["notnull"], (
            "search_result_events.created_at: expected NOT NULL"
        )
        dflt = (col_map["created_at"]["dflt_value"] or "").lower()
        assert "current_timestamp" in dflt or ("datetime" in dflt and "now" in dflt), (
            f"search_result_events.created_at: expected a server default "
            f"(CURRENT_TIMESTAMP or datetime('now')), got {col_map['created_at']['dflt_value']!r}"
        )

    def test_search_result_events_replacement_chunk_id_nullable(self, tmp_path: Path) -> None:
        """search_result_events.replacement_chunk_id must be INTEGER and nullable."""
        db_path = tmp_path / "sre_rcid.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            col_map = _sqlite_col_map(conn, "search_result_events")

        assert "INTEGER" in col_map["replacement_chunk_id"]["type"], (
            f"search_result_events.replacement_chunk_id: expected INTEGER, "
            f"got {col_map['replacement_chunk_id']['type']!r}"
        )
        assert not col_map["replacement_chunk_id"]["notnull"], (
            "search_result_events.replacement_chunk_id: expected NULL-able "
            "(only set when signal records a replacement suggestion)"
        )

    def test_search_result_events_session_chunk_index_exists(self, tmp_path: Path) -> None:
        """An index on (session_id, chunk_id) must exist for search_result_events."""
        db_path = tmp_path / "sre_idx.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            indexes = _sqlite_indexes(conn, "search_result_events")

        found = False
        for idx in indexes:
            sql = (idx["sql"] or "").lower()
            if "session_id" in sql and "chunk_id" in sql:
                found = True
                break
            if idx["sql"] is None:
                with sqlite3.connect(str(db_path)) as conn2:
                    info_rows = conn2.execute(f"PRAGMA index_info({idx['name']})").fetchall()
                cols_in_idx = {r[2] for r in info_rows}
                if "session_id" in cols_in_idx and "chunk_id" in cols_in_idx:
                    found = True
                    break

        assert found, (
            "search_result_events: no index found covering (session_id, chunk_id). "
            f"Indexes present: {[i['name'] for i in indexes]}"
        )

    def test_search_result_events_fk_cascade_on_session_delete(self, tmp_path: Path) -> None:
        """Deleting a search_sessions row must cascade-delete its search_result_events rows."""
        db_path = tmp_path / "sre_fk_sess.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            _sqlite_seed_dataset_and_chunks(conn, dataset_id=8002, chunk_ids=[8101, 8102])

            conn.execute(
                "INSERT INTO search_sessions (id, dataset_id, query, started_at) "
                "VALUES (5001, 8002, 'test query', CURRENT_TIMESTAMP)"
            )
            conn.commit()

            conn.execute(
                "INSERT INTO search_result_events "
                "(session_id, chunk_id, signal, value, source, created_at) "
                "VALUES (5001, 8101, 'relevance', 0.9, 'human', CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "INSERT INTO search_result_events "
                "(session_id, chunk_id, signal, value, source, created_at) "
                "VALUES (5001, 8102, 'click', NULL, 'cli_feedback', CURRENT_TIMESTAMP)"
            )
            conn.commit()

            count_before = conn.execute(
                "SELECT COUNT(*) FROM search_result_events WHERE session_id = 5001"
            ).fetchone()[0]
            assert count_before == 2

            conn.execute("DELETE FROM search_sessions WHERE id = 5001")
            conn.commit()

            count_after = conn.execute(
                "SELECT COUNT(*) FROM search_result_events WHERE session_id = 5001"
            ).fetchone()[0]
            assert count_after == 0, (
                "search_result_events: FK ON DELETE CASCADE did not remove event rows "
                "when parent session was deleted"
            )

    def test_search_result_events_fk_cascade_on_chunk_delete(self, tmp_path: Path) -> None:
        """Deleting a chunks row must cascade-delete its search_result_events rows."""
        db_path = tmp_path / "sre_fk_chunk.db"
        _alembic_upgrade_sqlite(db_path, _TARGET_REVISION)

        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            _sqlite_seed_dataset_and_chunks(conn, dataset_id=8003, chunk_ids=[8201, 8202])

            conn.execute(
                "INSERT INTO search_sessions (id, dataset_id, query, started_at) "
                "VALUES (5002, 8003, 'cascade chunk test', CURRENT_TIMESTAMP)"
            )
            conn.commit()

            conn.execute(
                "INSERT INTO search_result_events "
                "(session_id, chunk_id, signal, value, source, created_at) "
                "VALUES (5002, 8201, 'thumbs_up', 1.0, 'human', CURRENT_TIMESTAMP)"
            )
            conn.execute(
                "INSERT INTO search_result_events "
                "(session_id, chunk_id, signal, value, source, created_at) "
                "VALUES (5002, 8202, 'thumbs_up', 1.0, 'human', CURRENT_TIMESTAMP)"
            )
            conn.commit()

            count_before = conn.execute(
                "SELECT COUNT(*) FROM search_result_events WHERE session_id = 5002"
            ).fetchone()[0]
            assert count_before == 2

            conn.execute("DELETE FROM chunks WHERE id = 8201")
            conn.commit()

            count_after = conn.execute(
                "SELECT COUNT(*) FROM search_result_events WHERE session_id = 5002"
            ).fetchone()[0]
            assert count_after == 1, (
                "search_result_events: FK ON DELETE CASCADE on chunk_id did not remove "
                "event row when parent chunk was deleted"
            )

    def test_down_revision_chain_sqlite(self, tmp_path: Path) -> None:
        """Import the migration module and assert down_revision (SQLite path guard)."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.down_revision == "0012_analyze_signals", (
            f"down_revision drift detected: got {mod.down_revision!r}"
        )


# ---------------------------------------------------------------------------
# Postgres tests (requires Docker)
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker
class TestPostgresSearchSessions:
    """Schema assertions for the 0013_search_sessions migration against Postgres.

    Requires Docker + testcontainers (skipped automatically when unavailable).
    Uses the session-scoped ``pg_dsn`` fixture from the root conftest.

    RED state: fails with alembic.util.exc.CommandError because
    ``0013_search_sessions`` revision does not yet exist.
    """

    # ── search_sessions ──────────────────────────────────────────────────

    def test_search_sessions_table_exists(self, pg_dsn: str) -> None:
        """search_sessions table must exist in corpus schema after upgrade."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_sessions")

        assert cols, (
            "corpus.search_sessions table not found after 0013_search_sessions upgrade "
            "(information_schema.columns returned no rows)"
        )

    def test_search_sessions_columns(self, pg_dsn: str) -> None:
        """search_sessions must have exactly the expected column set."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_sessions")

        expected_columns = {"id", "query", "dataset_id", "started_at", "client", "host"}
        assert set(cols.keys()) == expected_columns, (
            f"corpus.search_sessions columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(cols.keys())}"
        )

    def test_search_sessions_id_bigint_not_null(self, pg_dsn: str) -> None:
        """search_sessions.id must be bigint NOT NULL (BIGSERIAL PK)."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_sessions")

        assert cols["id"]["data_type"] == "bigint", (
            f"search_sessions.id: expected bigint, got {cols['id']['data_type']!r}"
        )
        assert cols["id"]["is_nullable"] == "NO", (
            f"search_sessions.id: expected NOT NULL, got {cols['id']['is_nullable']!r}"
        )

    def test_search_sessions_query_text_not_null(self, pg_dsn: str) -> None:
        """search_sessions.query must be text NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_sessions")

        assert cols["query"]["data_type"] == "text", (
            f"search_sessions.query: expected text, got {cols['query']['data_type']!r}"
        )
        assert cols["query"]["is_nullable"] == "NO"

    def test_search_sessions_dataset_id_bigint_not_null(self, pg_dsn: str) -> None:
        """search_sessions.dataset_id must be bigint NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_sessions")

        assert cols["dataset_id"]["data_type"] == "bigint", (
            f"search_sessions.dataset_id: expected bigint, got {cols['dataset_id']['data_type']!r}"
        )
        assert cols["dataset_id"]["is_nullable"] == "NO"

    def test_search_sessions_started_at_timestamptz_not_null_default_now(self, pg_dsn: str) -> None:
        """search_sessions.started_at must be TIMESTAMPTZ NOT NULL DEFAULT NOW()."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_sessions")

        assert cols["started_at"]["data_type"] == "timestamp with time zone", (
            f"search_sessions.started_at: expected 'timestamp with time zone', "
            f"got {cols['started_at']['data_type']!r}"
        )
        assert cols["started_at"]["is_nullable"] == "NO"
        default_val = (cols["started_at"]["column_default"] or "").lower()
        assert "now" in default_val, (
            f"search_sessions.started_at: expected DEFAULT NOW(), "
            f"got {cols['started_at']['column_default']!r}"
        )

    def test_search_sessions_client_text_nullable(self, pg_dsn: str) -> None:
        """search_sessions.client must be text and nullable."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_sessions")

        assert cols["client"]["data_type"] == "text", (
            f"search_sessions.client: expected text, got {cols['client']['data_type']!r}"
        )
        assert cols["client"]["is_nullable"] == "YES", (
            "search_sessions.client: expected NULL-able (optional client identifier)"
        )

    def test_search_sessions_host_text_nullable(self, pg_dsn: str) -> None:
        """search_sessions.host must be text and nullable."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_sessions")

        assert cols["host"]["data_type"] == "text", (
            f"search_sessions.host: expected text, got {cols['host']['data_type']!r}"
        )
        assert cols["host"]["is_nullable"] == "YES", "search_sessions.host: expected NULL-able"

    def test_search_sessions_dataset_id_started_at_index(self, pg_dsn: str) -> None:
        """Index on (dataset_id, started_at) must exist for search_sessions."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            indexes = _pg_index_defs(conn, "corpus", "search_sessions")

        found = any(
            "dataset_id" in defn.lower() and "started_at" in defn.lower()
            for defn in indexes.values()
            if defn is not None
        )
        assert found, (
            "corpus.search_sessions: no index found on (dataset_id, started_at). "
            f"Indexes present: {list(indexes.keys())}"
        )

    def test_search_sessions_dataset_id_fk_cascade(self, pg_dsn: str) -> None:
        """search_sessions.dataset_id FK must be ON DELETE CASCADE."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            fks = _pg_fk_info(conn, "corpus", "search_sessions")

        ds_fks = [fk for fk in fks if fk["column_name"] == "dataset_id"]
        assert ds_fks, "corpus.search_sessions: no FK found on dataset_id column"
        fk = ds_fks[0]
        assert fk["foreign_table_name"] == "datasets", (
            f"search_sessions.dataset_id FK references {fk['foreign_table_name']!r}, "
            f"expected 'datasets'"
        )
        assert fk["delete_rule"].upper() == "CASCADE", (
            f"search_sessions.dataset_id FK delete_rule is {fk['delete_rule']!r}, "
            f"expected 'CASCADE'"
        )

    # ── search_result_events ─────────────────────────────────────────────

    def test_search_result_events_table_exists(self, pg_dsn: str) -> None:
        """search_result_events table must exist in corpus schema after upgrade."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_result_events")

        assert cols, (
            "corpus.search_result_events table not found after 0013_search_sessions upgrade"
        )

    def test_search_result_events_columns(self, pg_dsn: str) -> None:
        """search_result_events must have exactly the expected column set."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_result_events")

        expected_columns = {
            "id",
            "session_id",
            "chunk_id",
            "signal",
            "value",
            "source",
            "created_at",
            "replacement_chunk_id",
        }
        assert set(cols.keys()) == expected_columns, (
            f"corpus.search_result_events columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(cols.keys())}"
        )

    def test_search_result_events_id_bigint_not_null(self, pg_dsn: str) -> None:
        """search_result_events.id must be bigint NOT NULL (BIGSERIAL PK)."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_result_events")

        assert cols["id"]["data_type"] == "bigint", (
            f"search_result_events.id: expected bigint, got {cols['id']['data_type']!r}"
        )
        assert cols["id"]["is_nullable"] == "NO"

    def test_search_result_events_session_id_bigint_not_null(self, pg_dsn: str) -> None:
        """search_result_events.session_id must be bigint NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_result_events")

        assert cols["session_id"]["data_type"] == "bigint", (
            f"search_result_events.session_id: expected bigint, "
            f"got {cols['session_id']['data_type']!r}"
        )
        assert cols["session_id"]["is_nullable"] == "NO"

    def test_search_result_events_chunk_id_bigint_not_null(self, pg_dsn: str) -> None:
        """search_result_events.chunk_id must be bigint NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_result_events")

        assert cols["chunk_id"]["data_type"] == "bigint", (
            f"search_result_events.chunk_id: expected bigint, got {cols['chunk_id']['data_type']!r}"
        )
        assert cols["chunk_id"]["is_nullable"] == "NO"

    def test_search_result_events_signal_text_not_null(self, pg_dsn: str) -> None:
        """search_result_events.signal must be text NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_result_events")

        assert cols["signal"]["data_type"] == "text", (
            f"search_result_events.signal: expected text, got {cols['signal']['data_type']!r}"
        )
        assert cols["signal"]["is_nullable"] == "NO"

    def test_search_result_events_value_real_nullable(self, pg_dsn: str) -> None:
        """search_result_events.value must be a real/float type and nullable."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_result_events")

        assert cols["value"]["data_type"] in ("real", "double precision"), (
            f"search_result_events.value: expected real, got {cols['value']['data_type']!r}"
        )
        assert cols["value"]["is_nullable"] == "YES", (
            "search_result_events.value: expected NULL-able (numeric rating is optional)"
        )

    def test_search_result_events_source_text_not_null(self, pg_dsn: str) -> None:
        """search_result_events.source must be text NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_result_events")

        assert cols["source"]["data_type"] == "text", (
            f"search_result_events.source: expected text, got {cols['source']['data_type']!r}"
        )
        assert cols["source"]["is_nullable"] == "NO"

    def test_search_result_events_created_at_timestamptz_not_null_default_now(
        self, pg_dsn: str
    ) -> None:
        """search_result_events.created_at must be TIMESTAMPTZ NOT NULL DEFAULT NOW()."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_result_events")

        assert cols["created_at"]["data_type"] == "timestamp with time zone", (
            f"search_result_events.created_at: expected 'timestamp with time zone', "
            f"got {cols['created_at']['data_type']!r}"
        )
        assert cols["created_at"]["is_nullable"] == "NO"
        default_val = (cols["created_at"]["column_default"] or "").lower()
        assert "now" in default_val, (
            f"search_result_events.created_at: expected DEFAULT NOW(), "
            f"got {cols['created_at']['column_default']!r}"
        )

    def test_search_result_events_replacement_chunk_id_bigint_nullable(self, pg_dsn: str) -> None:
        """search_result_events.replacement_chunk_id must be bigint and nullable."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "search_result_events")

        assert cols["replacement_chunk_id"]["data_type"] == "bigint", (
            f"search_result_events.replacement_chunk_id: expected bigint, "
            f"got {cols['replacement_chunk_id']['data_type']!r}"
        )
        assert cols["replacement_chunk_id"]["is_nullable"] == "YES", (
            "search_result_events.replacement_chunk_id: expected NULL-able "
            "(only set for replacement signals)"
        )

    def test_search_result_events_session_chunk_index(self, pg_dsn: str) -> None:
        """Index on (session_id, chunk_id) must exist for search_result_events."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            indexes = _pg_index_defs(conn, "corpus", "search_result_events")

        found = any(
            "session_id" in defn.lower() and "chunk_id" in defn.lower()
            for defn in indexes.values()
            if defn is not None
        )
        assert found, (
            "corpus.search_result_events: no index found on (session_id, chunk_id). "
            f"Indexes present: {list(indexes.keys())}"
        )

    def test_search_result_events_session_id_fk_cascade(self, pg_dsn: str) -> None:
        """search_result_events.session_id FK must be ON DELETE CASCADE."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            fks = _pg_fk_info(conn, "corpus", "search_result_events")

        sess_fks = [fk for fk in fks if fk["column_name"] == "session_id"]
        assert sess_fks, "corpus.search_result_events: no FK found on session_id column"
        fk = sess_fks[0]
        assert fk["foreign_table_name"] == "search_sessions", (
            f"search_result_events.session_id FK references {fk['foreign_table_name']!r}, "
            f"expected 'search_sessions'"
        )
        assert fk["delete_rule"].upper() == "CASCADE", (
            f"search_result_events.session_id FK delete_rule is {fk['delete_rule']!r}, "
            f"expected 'CASCADE'"
        )

    def test_search_result_events_chunk_id_fk_cascade(self, pg_dsn: str) -> None:
        """search_result_events.chunk_id FK must be ON DELETE CASCADE."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            fks = _pg_fk_info(conn, "corpus", "search_result_events")

        chunk_fks = [fk for fk in fks if fk["column_name"] == "chunk_id"]
        assert chunk_fks, "corpus.search_result_events: no FK found on chunk_id column"
        fk = chunk_fks[0]
        assert fk["foreign_table_name"] == "chunks", (
            f"search_result_events.chunk_id FK references {fk['foreign_table_name']!r}, "
            f"expected 'chunks'"
        )
        assert fk["delete_rule"].upper() == "CASCADE", (
            f"search_result_events.chunk_id FK delete_rule is {fk['delete_rule']!r}, "
            f"expected 'CASCADE'"
        )

    def test_down_revision_chain_postgres(self, pg_dsn: str) -> None:
        """Import the migration module and assert down_revision (Postgres path guard)."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.down_revision == "0012_analyze_signals", (
            f"down_revision drift detected: got {mod.down_revision!r}"
        )
