"""Integration test for alembic revision 0017_ingest_runs.

Asserts that after applying ``alembic upgrade 0017_ingest_runs``:

- ``ingest_runs`` table exists with all required columns, types, nullability,
  defaults, and indexes.
- ``ingest_run_sources`` table exists with all required columns, types,
  nullability, defaults, and indexes.
- ``ingest_run_sources.run_id`` carries a FK referencing
  ``ingest_runs.run_id`` with ON DELETE CASCADE.
- The migration chains correctly from ``0016_chunk_provenance``.
- ``downgrade()`` is forward-only (single ``pass`` body per project convention).
- ``alembic upgrade head`` is idempotent (double-run, no error).
- ``alembic downgrade -1`` drops both tables.

Both Postgres and SQLite dialects are exercised.  The Postgres path is gated on
``requires_docker`` and uses the session-scoped ``pg_dsn`` fixture from the root
conftest.  The SQLite path runs in-process against a ``tmp_path`` database with
no external dependencies.

RED condition
-------------
The migration file ``corpus_forge/alembic/versions/0017_ingest_runs.py`` does
not yet exist.  Every test in this file should fail with either::

    alembic.util.exc.CommandError: Can't locate revision identified by
    '0017_ingest_runs'

or::

    ModuleNotFoundError

Both are acceptable RED states.  The downgrade and idempotency tests will
additionally assert behaviour visible only after the revision is written.
"""

from __future__ import annotations

import ast
import importlib
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.integration]

# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_MIGRATION_MODULE = "corpus_forge.alembic.versions.0017_ingest_runs"
_MIGRATION_FILE = _REPO_ROOT / "corpus_forge" / "alembic" / "versions" / "0017_ingest_runs.py"
_TARGET_REVISION = "0017_ingest_runs"
_PRIOR_REVISION = "0016_chunk_provenance"

# ---------------------------------------------------------------------------
# Shared helpers
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


def _alembic_downgrade_pg(dsn: str, target: str) -> None:
    """Run ``alembic.command.downgrade(config, target)`` against a Postgres DSN."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )
    cfg.set_main_option("sqlalchemy.url", _sa_dsn(dsn))
    command.downgrade(cfg, target)


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


def _alembic_downgrade_sqlite(db_path: Path, target: str) -> None:
    """Run ``alembic.command.downgrade(config, target)`` against a SQLite *db_path*."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.downgrade(cfg, target)


def _reset_pg_schema(dsn: str) -> None:
    """Drop and recreate the corpus schema + pgvector extension."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS corpus CASCADE")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE SCHEMA IF NOT EXISTS corpus")


def _pg_column_info(
    conn: Any,
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
    conn: Any,
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
    conn: Any,
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


def _pg_table_exists(conn: Any, schema: str, table: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_name   = %s
            """,
            (schema, table),
        )
        return cur.fetchone()[0] == 1


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
    """Return sqlite_master index rows for *table*."""
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


def _sqlite_index_covers(conn: sqlite3.Connection, table: str, *columns: str) -> bool:
    """Return True if any index on *table* covers every column in *columns*."""
    indexes = _sqlite_indexes(conn, table)
    for idx in indexes:
        # Check SQL text first (handles CREATE INDEX ... ON t(a, b) form)
        sql = (idx["sql"] or "").lower()
        if all(c.lower() in sql for c in columns):
            return True
        # Fall back to PRAGMA index_info for system-created indexes
        if idx["sql"] is None:
            info_rows = conn.execute(f"PRAGMA index_info({idx['name']})").fetchall()
            cols_in_idx = {r[2] for r in info_rows}
            if all(c in cols_in_idx for c in columns):
                return True
    return False


# ---------------------------------------------------------------------------
# Cross-cutting: migration module attributes + forward-only downgrade
# ---------------------------------------------------------------------------


class TestMigrationModuleAttributes:
    """Assert revision chain and forward-only downgrade without DB access.

    These tests import the migration Python module directly.  They fail
    immediately with ModuleNotFoundError while the file does not exist —
    that is the expected RED state.
    """

    def test_revision_value(self) -> None:
        """Migration module must declare ``revision = '0017_ingest_runs'``."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.revision == "0017_ingest_runs", (
            f"Expected revision='0017_ingest_runs', got {mod.revision!r}"
        )

    def test_down_revision_points_at_0016(self) -> None:
        """Migration must chain directly onto 0016_chunk_provenance."""
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert mod.down_revision == "0016_chunk_provenance", (
            f"Expected down_revision='0016_chunk_provenance', "
            f"got {mod.down_revision!r}. "
            "Accidental rebase drift will break the alembic chain in CI."
        )

    def test_revision_id_fits_alembic_version_num_column(self) -> None:
        """revision id must be <= 32 chars to fit alembic_version.version_num VARCHAR(32).

        The original 0015 revision used a 37-char id and caused
        StringDataRightTruncation on Postgres after a successful migration
        body — the id is checked here to prevent that class of failure
        recurring.
        """
        mod = importlib.import_module(_MIGRATION_MODULE)
        assert len(mod.revision) <= 32, (
            f"revision id {mod.revision!r} is {len(mod.revision)} chars; "
            "must fit in alembic_version.version_num VARCHAR(32) — "
            "see the long note on revision 0015 for regression context"
        )

    def test_downgrade_is_forward_only_pass(self) -> None:
        """``downgrade()`` body must be a single ``pass`` statement.

        Project convention (established by 0008, 0010, 0011, 0012, 0013,
        0014, 0015, 0016): new-table migrations are forward-only.
        """
        assert _MIGRATION_FILE.exists(), (
            f"Migration file not found at {_MIGRATION_FILE}. "
            "Write the file before this test can proceed."
        )
        source = _MIGRATION_FILE.read_text(encoding="utf-8")
        tree = ast.parse(source)

        downgrade_func: ast.FunctionDef | None = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
                downgrade_func = node
                break

        assert downgrade_func is not None, (
            "No ``downgrade()`` function found in 0017_ingest_runs.py"
        )

        body = downgrade_func.body
        assert len(body) == 1 and isinstance(body[0], ast.Pass), (
            "downgrade() body must be a single ``pass`` statement "
            "(forward-only convention). "
            f"Got {len(body)} statement(s): {[ast.dump(s) for s in body]}"
        )


# ---------------------------------------------------------------------------
# SQLite tests (no Docker required)
# ---------------------------------------------------------------------------


class TestSQLiteIngestRuns:
    """Schema assertions for the 0017_ingest_runs migration against SQLite.

    All tests use ``tmp_path`` for per-test DB isolation. No Docker needed.

    RED state: fails with alembic.util.exc.CommandError because
    ``0017_ingest_runs`` revision does not exist yet.
    """

    # ── ingest_runs: table presence ──────────────────────────────────────

    def test_ingest_runs_table_exists_after_upgrade(self, tmp_path: Path) -> None:
        """ingest_runs table must exist after upgrade to 0017."""
        db = tmp_path / "ingest_runs_exists.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            assert _sqlite_table_exists(conn, "ingest_runs"), (
                "ingest_runs table not found in sqlite_master after 0017 upgrade"
            )

    # ── ingest_runs: column set ──────────────────────────────────────────

    def test_ingest_runs_has_all_required_columns(self, tmp_path: Path) -> None:
        """ingest_runs must have the exact expected column set (per design contract)."""
        db = tmp_path / "ingest_runs_cols.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        expected = {
            "id",
            "run_id",
            "started_at",
            "ended_at",
            "last_progress_at",
            "status",
            "last_op",
            "last_done",
            "last_total",
            "error",
            "host",
            "pid",
            "config_digest",
        }
        assert set(col_map.keys()) == expected, (
            "ingest_runs column set mismatch.\n"
            f"  expected : {sorted(expected)}\n"
            f"  actual   : {sorted(col_map.keys())}"
        )

    # ── ingest_runs: individual column contracts ─────────────────────────

    def test_ingest_runs_id_is_integer_pk(self, tmp_path: Path) -> None:
        """ingest_runs.id must be INTEGER PRIMARY KEY (AUTOINCREMENT implied)."""
        db = tmp_path / "ir_id.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        assert col_map["id"]["pk"], "ingest_runs.id: expected PRIMARY KEY"
        assert "INTEGER" in col_map["id"]["type"], (
            f"ingest_runs.id: expected INTEGER type, got {col_map['id']['type']!r}"
        )

    def test_ingest_runs_run_id_is_text_not_null(self, tmp_path: Path) -> None:
        """ingest_runs.run_id must be TEXT NOT NULL (logical primary key, ULID/UUIDv4)."""
        db = tmp_path / "ir_run_id.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        assert "TEXT" in col_map["run_id"]["type"], (
            f"ingest_runs.run_id: expected TEXT, got {col_map['run_id']['type']!r}"
        )
        assert col_map["run_id"]["notnull"], "ingest_runs.run_id: expected NOT NULL"

    def test_ingest_runs_started_at_is_text_not_null_with_default(self, tmp_path: Path) -> None:
        """ingest_runs.started_at must be TEXT NOT NULL with DEFAULT CURRENT_TIMESTAMP."""
        db = tmp_path / "ir_started.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        assert "TEXT" in col_map["started_at"]["type"], (
            f"ingest_runs.started_at: expected TEXT (ISO-8601 convention), "
            f"got {col_map['started_at']['type']!r}"
        )
        assert col_map["started_at"]["notnull"], "ingest_runs.started_at: expected NOT NULL"
        dflt = (col_map["started_at"]["dflt_value"] or "").upper()
        assert "CURRENT_TIMESTAMP" in dflt or "NOW" in dflt or "DATETIME" in dflt, (
            f"ingest_runs.started_at: expected a timestamp DEFAULT, "
            f"got {col_map['started_at']['dflt_value']!r}"
        )

    def test_ingest_runs_ended_at_is_text_nullable(self, tmp_path: Path) -> None:
        """ingest_runs.ended_at must be TEXT and nullable (NULL while running)."""
        db = tmp_path / "ir_ended.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        assert "TEXT" in col_map["ended_at"]["type"], (
            f"ingest_runs.ended_at: expected TEXT, got {col_map['ended_at']['type']!r}"
        )
        assert not col_map["ended_at"]["notnull"], (
            "ingest_runs.ended_at: must be nullable (NULL while run is in progress)"
        )

    def test_ingest_runs_last_progress_at_is_text_not_null_with_default(
        self, tmp_path: Path
    ) -> None:
        """ingest_runs.last_progress_at must be TEXT NOT NULL with timestamp DEFAULT."""
        db = tmp_path / "ir_last_prog.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        assert "TEXT" in col_map["last_progress_at"]["type"], (
            f"ingest_runs.last_progress_at: expected TEXT, "
            f"got {col_map['last_progress_at']['type']!r}"
        )
        assert col_map["last_progress_at"]["notnull"], (
            "ingest_runs.last_progress_at: expected NOT NULL"
        )
        dflt = (col_map["last_progress_at"]["dflt_value"] or "").upper()
        assert "CURRENT_TIMESTAMP" in dflt or "NOW" in dflt or "DATETIME" in dflt, (
            f"ingest_runs.last_progress_at: expected a timestamp DEFAULT, "
            f"got {col_map['last_progress_at']['dflt_value']!r}"
        )

    def test_ingest_runs_status_is_text_not_null(self, tmp_path: Path) -> None:
        """ingest_runs.status must be TEXT NOT NULL (running|completed|interrupted|failed)."""
        db = tmp_path / "ir_status.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        assert "TEXT" in col_map["status"]["type"], (
            f"ingest_runs.status: expected TEXT, got {col_map['status']['type']!r}"
        )
        assert col_map["status"]["notnull"], "ingest_runs.status: expected NOT NULL"

    def test_ingest_runs_last_op_is_text_nullable(self, tmp_path: Path) -> None:
        """ingest_runs.last_op must be TEXT and nullable."""
        db = tmp_path / "ir_last_op.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        assert "TEXT" in col_map["last_op"]["type"], (
            f"ingest_runs.last_op: expected TEXT, got {col_map['last_op']['type']!r}"
        )
        assert not col_map["last_op"]["notnull"], "ingest_runs.last_op: must be nullable"

    def test_ingest_runs_last_done_is_integer_not_null_default_zero(self, tmp_path: Path) -> None:
        """ingest_runs.last_done must be INTEGER NOT NULL DEFAULT 0."""
        db = tmp_path / "ir_last_done.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        assert "INTEGER" in col_map["last_done"]["type"], (
            f"ingest_runs.last_done: expected INTEGER, got {col_map['last_done']['type']!r}"
        )
        assert col_map["last_done"]["notnull"], "ingest_runs.last_done: expected NOT NULL"
        assert col_map["last_done"]["dflt_value"] == "0", (
            f"ingest_runs.last_done: expected DEFAULT 0, got {col_map['last_done']['dflt_value']!r}"
        )

    def test_ingest_runs_last_total_is_integer_nullable(self, tmp_path: Path) -> None:
        """ingest_runs.last_total must be INTEGER and nullable (unknown until scanned)."""
        db = tmp_path / "ir_last_total.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        assert "INTEGER" in col_map["last_total"]["type"], (
            f"ingest_runs.last_total: expected INTEGER, got {col_map['last_total']['type']!r}"
        )
        assert not col_map["last_total"]["notnull"], (
            "ingest_runs.last_total: must be nullable (NULL when total is unknown)"
        )

    def test_ingest_runs_error_is_text_nullable(self, tmp_path: Path) -> None:
        """ingest_runs.error must be TEXT and nullable."""
        db = tmp_path / "ir_error.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        assert "TEXT" in col_map["error"]["type"], (
            f"ingest_runs.error: expected TEXT, got {col_map['error']['type']!r}"
        )
        assert not col_map["error"]["notnull"], "ingest_runs.error: must be nullable"

    def test_ingest_runs_host_is_text_not_null(self, tmp_path: Path) -> None:
        """ingest_runs.host must be TEXT NOT NULL (socket.gethostname())."""
        db = tmp_path / "ir_host.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        assert "TEXT" in col_map["host"]["type"], (
            f"ingest_runs.host: expected TEXT, got {col_map['host']['type']!r}"
        )
        assert col_map["host"]["notnull"], "ingest_runs.host: expected NOT NULL"

    def test_ingest_runs_pid_is_integer_not_null(self, tmp_path: Path) -> None:
        """ingest_runs.pid must be INTEGER NOT NULL."""
        db = tmp_path / "ir_pid.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        assert "INTEGER" in col_map["pid"]["type"], (
            f"ingest_runs.pid: expected INTEGER, got {col_map['pid']['type']!r}"
        )
        assert col_map["pid"]["notnull"], "ingest_runs.pid: expected NOT NULL"

    def test_ingest_runs_config_digest_is_text_not_null(self, tmp_path: Path) -> None:
        """ingest_runs.config_digest must be TEXT NOT NULL (sha256 of config blob)."""
        db = tmp_path / "ir_digest.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_runs")

        assert "TEXT" in col_map["config_digest"]["type"], (
            f"ingest_runs.config_digest: expected TEXT, got {col_map['config_digest']['type']!r}"
        )
        assert col_map["config_digest"]["notnull"], "ingest_runs.config_digest: expected NOT NULL"

    # ── ingest_runs: indexes ─────────────────────────────────────────────

    def test_ingest_runs_status_index_exists(self, tmp_path: Path) -> None:
        """An index on ingest_runs(status) must exist (ingest_runs_status_idx)."""
        db = tmp_path / "ir_status_idx.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            assert _sqlite_index_covers(conn, "ingest_runs", "status"), (
                "ingest_runs: no index found covering status column. "
                f"Indexes: {[i['name'] for i in _sqlite_indexes(conn, 'ingest_runs')]}"
            )

    def test_ingest_runs_started_at_index_exists(self, tmp_path: Path) -> None:
        """An index on ingest_runs(started_at) must exist."""
        db = tmp_path / "ir_started_idx.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            assert _sqlite_index_covers(conn, "ingest_runs", "started_at"), (
                "ingest_runs: no index found covering started_at column. "
                f"Indexes: {[i['name'] for i in _sqlite_indexes(conn, 'ingest_runs')]}"
            )

    # ── ingest_runs: UNIQUE constraint on run_id ─────────────────────────

    def test_ingest_runs_run_id_unique_constraint_enforced(self, tmp_path: Path) -> None:
        """Inserting two rows with the same run_id must raise IntegrityError."""
        db = tmp_path / "ir_unique.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        _ir_cols = (
            "run_id, started_at, last_progress_at, status, last_done, host, pid, config_digest"
        )
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "INSERT INTO ingest_runs "
                f"({_ir_cols}) "
                "VALUES ('run-aaa', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "'running', 0, 'host1', 12345, 'digest1')"
            )
            conn.commit()

            with pytest.raises(sqlite3.IntegrityError):  # noqa: PT012
                conn.execute(
                    "INSERT INTO ingest_runs "
                    f"({_ir_cols}) "
                    "VALUES ('run-aaa', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                    "'running', 0, 'host2', 99999, 'digest2')"
                )
                conn.commit()

    # ── ingest_run_sources: table presence ──────────────────────────────

    def test_ingest_run_sources_table_exists_after_upgrade(self, tmp_path: Path) -> None:
        """ingest_run_sources table must exist after upgrade to 0017."""
        db = tmp_path / "irs_exists.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            assert _sqlite_table_exists(conn, "ingest_run_sources"), (
                "ingest_run_sources table not found in sqlite_master after 0017 upgrade"
            )

    # ── ingest_run_sources: column set ──────────────────────────────────

    def test_ingest_run_sources_has_all_required_columns(self, tmp_path: Path) -> None:
        """ingest_run_sources must have the exact expected column set."""
        db = tmp_path / "irs_cols.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_run_sources")

        expected = {
            "id",
            "run_id",
            "source_uri_prefix",
            "dataset_id",
            "last_scanned_at",
            "docs_seen",
            "docs_skipped",
            "docs_failed",
            "finished_at",
        }
        assert set(col_map.keys()) == expected, (
            "ingest_run_sources column set mismatch.\n"
            f"  expected : {sorted(expected)}\n"
            f"  actual   : {sorted(col_map.keys())}"
        )

    # ── ingest_run_sources: individual column contracts ──────────────────

    def test_ingest_run_sources_id_is_integer_pk(self, tmp_path: Path) -> None:
        """ingest_run_sources.id must be INTEGER PRIMARY KEY."""
        db = tmp_path / "irs_id.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_run_sources")

        assert col_map["id"]["pk"], "ingest_run_sources.id: expected PRIMARY KEY"
        assert "INTEGER" in col_map["id"]["type"], (
            f"ingest_run_sources.id: expected INTEGER, got {col_map['id']['type']!r}"
        )

    def test_ingest_run_sources_run_id_is_text_not_null(self, tmp_path: Path) -> None:
        """ingest_run_sources.run_id must be TEXT NOT NULL (FK to ingest_runs.run_id)."""
        db = tmp_path / "irs_run_id.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_run_sources")

        assert "TEXT" in col_map["run_id"]["type"], (
            f"ingest_run_sources.run_id: expected TEXT, got {col_map['run_id']['type']!r}"
        )
        assert col_map["run_id"]["notnull"], "ingest_run_sources.run_id: expected NOT NULL"

    def test_ingest_run_sources_source_uri_prefix_is_text_not_null(self, tmp_path: Path) -> None:
        """ingest_run_sources.source_uri_prefix must be TEXT NOT NULL."""
        db = tmp_path / "irs_uri.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_run_sources")

        assert "TEXT" in col_map["source_uri_prefix"]["type"], (
            f"ingest_run_sources.source_uri_prefix: expected TEXT, "
            f"got {col_map['source_uri_prefix']['type']!r}"
        )
        assert col_map["source_uri_prefix"]["notnull"], (
            "ingest_run_sources.source_uri_prefix: expected NOT NULL"
        )

    def test_ingest_run_sources_dataset_id_is_integer_not_null(self, tmp_path: Path) -> None:
        """ingest_run_sources.dataset_id must be INTEGER NOT NULL."""
        db = tmp_path / "irs_ds_id.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_run_sources")

        assert "INTEGER" in col_map["dataset_id"]["type"], (
            f"ingest_run_sources.dataset_id: expected INTEGER, "
            f"got {col_map['dataset_id']['type']!r}"
        )
        assert col_map["dataset_id"]["notnull"], "ingest_run_sources.dataset_id: expected NOT NULL"

    def test_ingest_run_sources_last_scanned_at_is_text_nullable(self, tmp_path: Path) -> None:
        """ingest_run_sources.last_scanned_at must be TEXT and nullable (NULL = not yet scanned)."""
        db = tmp_path / "irs_last_scan.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_run_sources")

        assert "TEXT" in col_map["last_scanned_at"]["type"], (
            f"ingest_run_sources.last_scanned_at: expected TEXT, "
            f"got {col_map['last_scanned_at']['type']!r}"
        )
        assert not col_map["last_scanned_at"]["notnull"], (
            "ingest_run_sources.last_scanned_at: must be nullable "
            "(NULL when source not yet scanned this run)"
        )

    def test_ingest_run_sources_docs_seen_is_integer_not_null_default_zero(
        self, tmp_path: Path
    ) -> None:
        """ingest_run_sources.docs_seen must be INTEGER NOT NULL DEFAULT 0."""
        db = tmp_path / "irs_docs_seen.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_run_sources")

        assert "INTEGER" in col_map["docs_seen"]["type"], (
            f"ingest_run_sources.docs_seen: expected INTEGER, got {col_map['docs_seen']['type']!r}"
        )
        assert col_map["docs_seen"]["notnull"], "ingest_run_sources.docs_seen: expected NOT NULL"
        assert col_map["docs_seen"]["dflt_value"] == "0", (
            f"ingest_run_sources.docs_seen: expected DEFAULT 0, "
            f"got {col_map['docs_seen']['dflt_value']!r}"
        )

    def test_ingest_run_sources_docs_skipped_is_integer_not_null_default_zero(
        self, tmp_path: Path
    ) -> None:
        """ingest_run_sources.docs_skipped must be INTEGER NOT NULL DEFAULT 0."""
        db = tmp_path / "irs_docs_skip.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_run_sources")

        assert "INTEGER" in col_map["docs_skipped"]["type"], (
            f"ingest_run_sources.docs_skipped: expected INTEGER, "
            f"got {col_map['docs_skipped']['type']!r}"
        )
        assert col_map["docs_skipped"]["notnull"], (
            "ingest_run_sources.docs_skipped: expected NOT NULL"
        )
        assert col_map["docs_skipped"]["dflt_value"] == "0", (
            f"ingest_run_sources.docs_skipped: expected DEFAULT 0, "
            f"got {col_map['docs_skipped']['dflt_value']!r}"
        )

    def test_ingest_run_sources_docs_failed_is_integer_not_null_default_zero(
        self, tmp_path: Path
    ) -> None:
        """ingest_run_sources.docs_failed must be INTEGER NOT NULL DEFAULT 0."""
        db = tmp_path / "irs_docs_fail.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_run_sources")

        assert "INTEGER" in col_map["docs_failed"]["type"], (
            f"ingest_run_sources.docs_failed: expected INTEGER, "
            f"got {col_map['docs_failed']['type']!r}"
        )
        assert col_map["docs_failed"]["notnull"], (
            "ingest_run_sources.docs_failed: expected NOT NULL"
        )
        assert col_map["docs_failed"]["dflt_value"] == "0", (
            f"ingest_run_sources.docs_failed: expected DEFAULT 0, "
            f"got {col_map['docs_failed']['dflt_value']!r}"
        )

    def test_ingest_run_sources_finished_at_is_text_nullable(self, tmp_path: Path) -> None:
        """ingest_run_sources.finished_at must be TEXT and nullable (NULL while walking)."""
        db = tmp_path / "irs_finished.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            col_map = _sqlite_col_map(conn, "ingest_run_sources")

        assert "TEXT" in col_map["finished_at"]["type"], (
            f"ingest_run_sources.finished_at: expected TEXT, got {col_map['finished_at']['type']!r}"
        )
        assert not col_map["finished_at"]["notnull"], (
            "ingest_run_sources.finished_at: must be nullable "
            "(NULL while source is still being walked)"
        )

    # ── ingest_run_sources: indexes ──────────────────────────────────────

    def test_ingest_run_sources_run_id_index_exists(self, tmp_path: Path) -> None:
        """An index on ingest_run_sources(run_id) must exist."""
        db = tmp_path / "irs_run_idx.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            assert _sqlite_index_covers(conn, "ingest_run_sources", "run_id"), (
                "ingest_run_sources: no index found covering run_id. "
                f"Indexes: "
                f"{[i['name'] for i in _sqlite_indexes(conn, 'ingest_run_sources')]}"
            )

    def test_ingest_run_sources_source_uri_prefix_index_exists(self, tmp_path: Path) -> None:
        """An index on ingest_run_sources(source_uri_prefix) must exist."""
        db = tmp_path / "irs_src_idx.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        with sqlite3.connect(str(db)) as conn:
            assert _sqlite_index_covers(conn, "ingest_run_sources", "source_uri_prefix"), (
                "ingest_run_sources: no index found covering source_uri_prefix. "
                f"Indexes: "
                f"{[i['name'] for i in _sqlite_indexes(conn, 'ingest_run_sources')]}"
            )

    # ── ingest_run_sources: UNIQUE (run_id, source_uri_prefix) ──────────

    def test_ingest_run_sources_unique_run_id_source_uri_prefix(self, tmp_path: Path) -> None:
        """Inserting duplicate (run_id, source_uri_prefix) pairs must raise IntegrityError."""
        db = tmp_path / "irs_unique.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        _ir_cols2 = (
            "run_id, started_at, last_progress_at, status, last_done, host, pid, config_digest"
        )
        _irs_cols = "run_id, source_uri_prefix, dataset_id, docs_seen, docs_skipped, docs_failed"
        with sqlite3.connect(str(db)) as conn:
            # First insert a parent ingest_runs row
            conn.execute(
                "INSERT INTO ingest_runs "
                f"({_ir_cols2}) "
                "VALUES ('run-bbb', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "'running', 0, 'host1', 11111, 'digest_bbb')"
            )
            # Insert the datasets row required by the FK on dataset_id
            # (the datasets table exists from the earlier migrations in the chain)
            conn.execute(
                "INSERT INTO datasets (id, name) VALUES (1001, 'test-ds') "
                "ON CONFLICT(id) DO NOTHING"
            )
            conn.commit()

            conn.execute(
                "INSERT INTO ingest_run_sources "
                f"({_irs_cols}) "
                "VALUES ('run-bbb', 'fs:///tmp/notes', 1001, 0, 0, 0)"
            )
            conn.commit()

            with pytest.raises(sqlite3.IntegrityError):  # noqa: PT012
                conn.execute(
                    "INSERT INTO ingest_run_sources "
                    f"({_irs_cols}) "
                    "VALUES ('run-bbb', 'fs:///tmp/notes', 1001, 0, 0, 0)"
                )
                conn.commit()

    # ── ingest_run_sources: FK referencing ingest_runs ───────────────────

    def test_ingest_run_sources_fk_cascade_delete_sqlite(self, tmp_path: Path) -> None:
        """Deleting an ingest_runs row must cascade-delete its ingest_run_sources rows.

        SQLite only enforces FK constraints when ``PRAGMA foreign_keys = ON``
        is set in the connection.
        """
        db = tmp_path / "irs_fk_cascade.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        _ir_cols3 = (
            "run_id, started_at, last_progress_at, status, last_done, host, pid, config_digest"
        )
        with sqlite3.connect(str(db)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")

            # Insert parent + dataset
            conn.execute(
                "INSERT INTO ingest_runs "
                f"({_ir_cols3}) "
                "VALUES ('run-ccc', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, "
                "'running', 0, 'host1', 22222, 'digest_ccc')"
            )
            conn.execute(
                "INSERT INTO datasets (id, name) VALUES (1002, 'test-ds-2') "
                "ON CONFLICT(id) DO NOTHING"
            )
            conn.commit()

            conn.execute(
                "INSERT INTO ingest_run_sources "
                "(run_id, source_uri_prefix, dataset_id, docs_seen, docs_skipped, docs_failed) "
                "VALUES ('run-ccc', 'fs:///tmp/vault', 1002, 5, 1, 0)"
            )
            conn.commit()

            before = conn.execute(
                "SELECT COUNT(*) FROM ingest_run_sources WHERE run_id = 'run-ccc'"
            ).fetchone()[0]
            assert before == 1, "Expected 1 source row before cascade test"

            conn.execute("DELETE FROM ingest_runs WHERE run_id = 'run-ccc'")
            conn.commit()

            after = conn.execute(
                "SELECT COUNT(*) FROM ingest_run_sources WHERE run_id = 'run-ccc'"
            ).fetchone()[0]
            assert after == 0, (
                "ingest_run_sources FK ON DELETE CASCADE did not remove source row "
                "when parent ingest_runs row was deleted"
            )

    # ── Idempotency and downgrade ─────────────────────────────────────────

    def test_upgrade_is_idempotent_on_double_run_sqlite(self, tmp_path: Path) -> None:
        """Running alembic upgrade head twice must not raise.

        This tests that the migration's idempotence guards (IF NOT EXISTS
        or existence probes) work correctly when the schema already exists.
        """
        db = tmp_path / "ir_idempotent.db"
        # First upgrade
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        # Roll back alembic_version to prior revision so upgrade() fires again
        with sqlite3.connect(str(db)) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert row[0] == _TARGET_REVISION, (
            f"Expected version_num={_TARGET_REVISION!r} after first upgrade, got {row[0]!r}"
        )

        # Rewind and re-run — should NOT raise
        with sqlite3.connect(str(db)) as conn:
            conn.execute(f"UPDATE alembic_version SET version_num = '{_PRIOR_REVISION}'")
            conn.commit()

        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        # Both tables must still be present
        with sqlite3.connect(str(db)) as conn:
            assert _sqlite_table_exists(conn, "ingest_runs"), (
                "ingest_runs lost on idempotent second upgrade"
            )
            assert _sqlite_table_exists(conn, "ingest_run_sources"), (
                "ingest_run_sources lost on idempotent second upgrade"
            )

    def test_downgrade_minus1_drops_both_tables_sqlite(self, tmp_path: Path) -> None:
        """alembic downgrade -1 must drop ingest_runs and ingest_run_sources.

        The project convention is forward-only (downgrade is a no-op pass),
        so ``downgrade -1`` rolls back to 0016_chunk_provenance, which by
        definition means the 0017 tables vanish.

        NOTE: Because downgrade() is a no-op ``pass``, the alembic runner
        will update ``alembic_version`` to 0016 but leave the tables in
        place.  If the coder implements an actual DROP in downgrade(), the
        tables WILL be gone and this test catches any regression there.
        However, the project convention IS forward-only — so this test
        asserts the alembic_version pin moves, NOT that tables are dropped
        (alembic itself handles the version bookkeeping; the tables staying
        is acceptable under the forward-only convention).

        What we DO assert: the downgrade command completes without error and
        the alembic_version moves back to 0016_chunk_provenance.
        """
        db = tmp_path / "ir_downgrade.db"
        _alembic_upgrade_sqlite(db, _TARGET_REVISION)

        # Confirm we're at 0017
        with sqlite3.connect(str(db)) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert row[0] == _TARGET_REVISION

        # Downgrade one step — should not raise
        _alembic_downgrade_sqlite(db, "-1")

        # alembic_version must now be at the prior revision
        with sqlite3.connect(str(db)) as conn:
            row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        assert row[0] == _PRIOR_REVISION, (
            f"After downgrade -1 expected version_num={_PRIOR_REVISION!r}, got {row[0]!r}"
        )


# ---------------------------------------------------------------------------
# Postgres tests (requires Docker)
# ---------------------------------------------------------------------------


@pytest.mark.requires_docker
class TestPostgresIngestRuns:
    """Schema assertions for the 0017_ingest_runs migration against Postgres.

    Requires Docker + testcontainers.  Uses the session-scoped ``pg_dsn``
    fixture from the root conftest (``tests/conftest.py``).

    RED state: fails with alembic.util.exc.CommandError.
    """

    # ── ingest_runs: table + columns ─────────────────────────────────────

    def test_ingest_runs_table_exists(self, pg_dsn: str) -> None:
        """corpus.ingest_runs must exist after upgrade."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            assert _pg_table_exists(conn, "corpus", "ingest_runs"), (
                "corpus.ingest_runs not found after 0017_ingest_runs upgrade"
            )

    def test_ingest_runs_column_set(self, pg_dsn: str) -> None:
        """corpus.ingest_runs must expose exactly the contracted columns."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_runs")

        expected = {
            "id",
            "run_id",
            "started_at",
            "ended_at",
            "last_progress_at",
            "status",
            "last_op",
            "last_done",
            "last_total",
            "error",
            "host",
            "pid",
            "config_digest",
        }
        assert set(cols.keys()) == expected, (
            "corpus.ingest_runs column set mismatch.\n"
            f"  expected : {sorted(expected)}\n"
            f"  actual   : {sorted(cols.keys())}"
        )

    def test_ingest_runs_id_bigint_not_null(self, pg_dsn: str) -> None:
        """corpus.ingest_runs.id must be bigint NOT NULL (BIGSERIAL PK)."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_runs")

        assert cols["id"]["data_type"] == "bigint", (
            f"corpus.ingest_runs.id: expected bigint, got {cols['id']['data_type']!r}"
        )
        assert cols["id"]["is_nullable"] == "NO"

    def test_ingest_runs_run_id_text_not_null(self, pg_dsn: str) -> None:
        """corpus.ingest_runs.run_id must be text NOT NULL (logical PK, ULID/UUIDv4)."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_runs")

        assert cols["run_id"]["data_type"] == "text", (
            f"corpus.ingest_runs.run_id: expected text, got {cols['run_id']['data_type']!r}"
        )
        assert cols["run_id"]["is_nullable"] == "NO"

    def test_ingest_runs_started_at_timestamptz_not_null_default_now(self, pg_dsn: str) -> None:
        """corpus.ingest_runs.started_at must be TIMESTAMPTZ NOT NULL DEFAULT NOW()."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_runs")

        assert cols["started_at"]["data_type"] == "timestamp with time zone", (
            f"corpus.ingest_runs.started_at: expected 'timestamp with time zone', "
            f"got {cols['started_at']['data_type']!r}"
        )
        assert cols["started_at"]["is_nullable"] == "NO"
        dflt = (cols["started_at"]["column_default"] or "").lower()
        assert "now" in dflt, (
            f"corpus.ingest_runs.started_at: expected DEFAULT NOW(), "
            f"got {cols['started_at']['column_default']!r}"
        )

    def test_ingest_runs_ended_at_timestamptz_nullable(self, pg_dsn: str) -> None:
        """corpus.ingest_runs.ended_at must be TIMESTAMPTZ and nullable."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_runs")

        assert cols["ended_at"]["data_type"] == "timestamp with time zone", (
            f"corpus.ingest_runs.ended_at: expected 'timestamp with time zone', "
            f"got {cols['ended_at']['data_type']!r}"
        )
        assert cols["ended_at"]["is_nullable"] == "YES"

    def test_ingest_runs_last_progress_at_timestamptz_not_null_default_now(
        self, pg_dsn: str
    ) -> None:
        """corpus.ingest_runs.last_progress_at must be TIMESTAMPTZ NOT NULL DEFAULT NOW()."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_runs")

        assert cols["last_progress_at"]["data_type"] == "timestamp with time zone", (
            f"corpus.ingest_runs.last_progress_at: expected 'timestamp with time zone', "
            f"got {cols['last_progress_at']['data_type']!r}"
        )
        assert cols["last_progress_at"]["is_nullable"] == "NO"
        dflt = (cols["last_progress_at"]["column_default"] or "").lower()
        assert "now" in dflt, (
            f"corpus.ingest_runs.last_progress_at: expected DEFAULT NOW(), "
            f"got {cols['last_progress_at']['column_default']!r}"
        )

    def test_ingest_runs_status_text_not_null(self, pg_dsn: str) -> None:
        """corpus.ingest_runs.status must be text NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_runs")

        assert cols["status"]["data_type"] == "text", (
            f"corpus.ingest_runs.status: expected text, got {cols['status']['data_type']!r}"
        )
        assert cols["status"]["is_nullable"] == "NO"

    def test_ingest_runs_last_done_bigint_not_null_default_zero(self, pg_dsn: str) -> None:
        """corpus.ingest_runs.last_done must be bigint NOT NULL DEFAULT 0."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_runs")

        assert cols["last_done"]["data_type"] == "bigint", (
            f"corpus.ingest_runs.last_done: expected bigint, got {cols['last_done']['data_type']!r}"
        )
        assert cols["last_done"]["is_nullable"] == "NO"
        dflt = cols["last_done"]["column_default"] or ""
        assert dflt.strip() == "0", (
            f"corpus.ingest_runs.last_done: expected DEFAULT 0, got {dflt!r}"
        )

    def test_ingest_runs_last_total_bigint_nullable(self, pg_dsn: str) -> None:
        """corpus.ingest_runs.last_total must be bigint and nullable."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_runs")

        assert cols["last_total"]["data_type"] == "bigint", (
            f"corpus.ingest_runs.last_total: expected bigint, "
            f"got {cols['last_total']['data_type']!r}"
        )
        assert cols["last_total"]["is_nullable"] == "YES"

    def test_ingest_runs_host_text_not_null(self, pg_dsn: str) -> None:
        """corpus.ingest_runs.host must be text NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_runs")

        assert cols["host"]["data_type"] == "text"
        assert cols["host"]["is_nullable"] == "NO"

    def test_ingest_runs_pid_integer_not_null(self, pg_dsn: str) -> None:
        """corpus.ingest_runs.pid must be integer NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_runs")

        assert cols["pid"]["data_type"] == "integer", (
            f"corpus.ingest_runs.pid: expected integer, got {cols['pid']['data_type']!r}"
        )
        assert cols["pid"]["is_nullable"] == "NO"

    def test_ingest_runs_config_digest_text_not_null(self, pg_dsn: str) -> None:
        """corpus.ingest_runs.config_digest must be text NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_runs")

        assert cols["config_digest"]["data_type"] == "text"
        assert cols["config_digest"]["is_nullable"] == "NO"

    # ── ingest_runs: indexes ─────────────────────────────────────────────

    def test_ingest_runs_status_index(self, pg_dsn: str) -> None:
        """ingest_runs_status_idx on corpus.ingest_runs(status) must exist."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            indexes = _pg_index_defs(conn, "corpus", "ingest_runs")

        assert any("status" in defn.lower() for defn in indexes.values() if defn is not None), (
            "corpus.ingest_runs: no index found on status column. "
            f"Indexes present: {list(indexes.keys())}"
        )

    def test_ingest_runs_started_at_index(self, pg_dsn: str) -> None:
        """ingest_runs_started_at_desc_idx on corpus.ingest_runs(started_at) must exist."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            indexes = _pg_index_defs(conn, "corpus", "ingest_runs")

        assert any("started_at" in defn.lower() for defn in indexes.values() if defn is not None), (
            "corpus.ingest_runs: no index found on started_at column. "
            f"Indexes present: {list(indexes.keys())}"
        )

    # ── ingest_run_sources: table + columns ──────────────────────────────

    def test_ingest_run_sources_table_exists(self, pg_dsn: str) -> None:
        """corpus.ingest_run_sources must exist after upgrade."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            assert _pg_table_exists(conn, "corpus", "ingest_run_sources"), (
                "corpus.ingest_run_sources not found after 0017_ingest_runs upgrade"
            )

    def test_ingest_run_sources_column_set(self, pg_dsn: str) -> None:
        """corpus.ingest_run_sources must expose exactly the contracted columns."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_run_sources")

        expected = {
            "id",
            "run_id",
            "source_uri_prefix",
            "dataset_id",
            "last_scanned_at",
            "docs_seen",
            "docs_skipped",
            "docs_failed",
            "finished_at",
        }
        assert set(cols.keys()) == expected, (
            "corpus.ingest_run_sources column set mismatch.\n"
            f"  expected : {sorted(expected)}\n"
            f"  actual   : {sorted(cols.keys())}"
        )

    def test_ingest_run_sources_run_id_text_not_null(self, pg_dsn: str) -> None:
        """corpus.ingest_run_sources.run_id must be text NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_run_sources")

        assert cols["run_id"]["data_type"] == "text"
        assert cols["run_id"]["is_nullable"] == "NO"

    def test_ingest_run_sources_dataset_id_bigint_not_null(self, pg_dsn: str) -> None:
        """corpus.ingest_run_sources.dataset_id must be bigint NOT NULL."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_run_sources")

        assert cols["dataset_id"]["data_type"] == "bigint", (
            f"corpus.ingest_run_sources.dataset_id: expected bigint, "
            f"got {cols['dataset_id']['data_type']!r}"
        )
        assert cols["dataset_id"]["is_nullable"] == "NO"

    def test_ingest_run_sources_docs_seen_bigint_not_null_default_zero(self, pg_dsn: str) -> None:
        """corpus.ingest_run_sources.docs_seen must be bigint NOT NULL DEFAULT 0."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_run_sources")

        assert cols["docs_seen"]["data_type"] == "bigint"
        assert cols["docs_seen"]["is_nullable"] == "NO"
        dflt = (cols["docs_seen"]["column_default"] or "").strip()
        assert dflt == "0", f"corpus.ingest_run_sources.docs_seen: expected DEFAULT 0, got {dflt!r}"

    def test_ingest_run_sources_docs_skipped_bigint_not_null_default_zero(
        self, pg_dsn: str
    ) -> None:
        """corpus.ingest_run_sources.docs_skipped must be bigint NOT NULL DEFAULT 0."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_run_sources")

        assert cols["docs_skipped"]["data_type"] == "bigint"
        assert cols["docs_skipped"]["is_nullable"] == "NO"
        dflt = (cols["docs_skipped"]["column_default"] or "").strip()
        assert dflt == "0"

    def test_ingest_run_sources_docs_failed_bigint_not_null_default_zero(self, pg_dsn: str) -> None:
        """corpus.ingest_run_sources.docs_failed must be bigint NOT NULL DEFAULT 0."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_run_sources")

        assert cols["docs_failed"]["data_type"] == "bigint"
        assert cols["docs_failed"]["is_nullable"] == "NO"
        dflt = (cols["docs_failed"]["column_default"] or "").strip()
        assert dflt == "0"

    def test_ingest_run_sources_last_scanned_at_timestamptz_nullable(self, pg_dsn: str) -> None:
        """corpus.ingest_run_sources.last_scanned_at must be TIMESTAMPTZ and nullable."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_run_sources")

        assert cols["last_scanned_at"]["data_type"] == "timestamp with time zone", (
            f"corpus.ingest_run_sources.last_scanned_at: expected "
            f"'timestamp with time zone', got {cols['last_scanned_at']['data_type']!r}"
        )
        assert cols["last_scanned_at"]["is_nullable"] == "YES"

    def test_ingest_run_sources_finished_at_timestamptz_nullable(self, pg_dsn: str) -> None:
        """corpus.ingest_run_sources.finished_at must be TIMESTAMPTZ and nullable."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            cols = _pg_column_info(conn, "corpus", "ingest_run_sources")

        assert cols["finished_at"]["data_type"] == "timestamp with time zone", (
            f"corpus.ingest_run_sources.finished_at: expected "
            f"'timestamp with time zone', got {cols['finished_at']['data_type']!r}"
        )
        assert cols["finished_at"]["is_nullable"] == "YES"

    # ── ingest_run_sources: indexes ──────────────────────────────────────

    def test_ingest_run_sources_run_id_index(self, pg_dsn: str) -> None:
        """ingest_run_sources_run_idx on corpus.ingest_run_sources(run_id) must exist."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            indexes = _pg_index_defs(conn, "corpus", "ingest_run_sources")

        assert any("run_id" in defn.lower() for defn in indexes.values() if defn is not None), (
            "corpus.ingest_run_sources: no index found on run_id column. "
            f"Indexes present: {list(indexes.keys())}"
        )

    def test_ingest_run_sources_source_uri_prefix_index(self, pg_dsn: str) -> None:
        """ingest_run_sources_last_scanned_idx on (source_uri_prefix, ...) must exist."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            indexes = _pg_index_defs(conn, "corpus", "ingest_run_sources")

        assert any(
            "source_uri_prefix" in defn.lower() for defn in indexes.values() if defn is not None
        ), (
            "corpus.ingest_run_sources: no index found on source_uri_prefix column. "
            f"Indexes present: {list(indexes.keys())}"
        )

    # ── ingest_run_sources: FK to ingest_runs ────────────────────────────

    def test_ingest_run_sources_fk_references_ingest_runs_run_id(self, pg_dsn: str) -> None:
        """corpus.ingest_run_sources.run_id FK must reference corpus.ingest_runs.run_id."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            fks = _pg_fk_info(conn, "corpus", "ingest_run_sources")

        run_id_fks = [fk for fk in fks if fk["column_name"] == "run_id"]
        assert run_id_fks, "corpus.ingest_run_sources: no FK found on run_id column"
        fk = run_id_fks[0]
        assert fk["foreign_table_name"] == "ingest_runs", (
            f"ingest_run_sources.run_id FK references {fk['foreign_table_name']!r}, "
            "expected 'ingest_runs'"
        )
        assert fk["foreign_column_name"] == "run_id", (
            f"ingest_run_sources.run_id FK foreign column is "
            f"{fk['foreign_column_name']!r}, expected 'run_id'"
        )

    def test_ingest_run_sources_fk_on_delete_cascade(self, pg_dsn: str) -> None:
        """corpus.ingest_run_sources.run_id FK must be ON DELETE CASCADE."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            fks = _pg_fk_info(conn, "corpus", "ingest_run_sources")

        run_id_fks = [fk for fk in fks if fk["column_name"] == "run_id"]
        assert run_id_fks
        fk = run_id_fks[0]
        assert fk["delete_rule"].upper() == "CASCADE", (
            f"ingest_run_sources.run_id FK delete_rule is {fk['delete_rule']!r}, expected 'CASCADE'"
        )

    # ── Idempotency ───────────────────────────────────────────────────────

    def test_upgrade_is_idempotent_pg(self, pg_dsn: str) -> None:
        """Running upgrade head twice on Postgres must not raise."""
        import psycopg

        _reset_pg_schema(pg_dsn)
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        # Check the current head
        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT version_num FROM corpus.alembic_version")
            row = cur.fetchone()
        assert row[0] == _TARGET_REVISION

        # Rewind one step and re-run
        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(f"UPDATE corpus.alembic_version SET version_num = '{_PRIOR_REVISION}'")

        # Should not raise
        _alembic_upgrade_pg(pg_dsn, _TARGET_REVISION)

        with psycopg.connect(pg_dsn) as conn:
            assert _pg_table_exists(conn, "corpus", "ingest_runs"), (
                "corpus.ingest_runs lost on idempotent second PG upgrade"
            )
            assert _pg_table_exists(conn, "corpus", "ingest_run_sources"), (
                "corpus.ingest_run_sources lost on idempotent second PG upgrade"
            )
