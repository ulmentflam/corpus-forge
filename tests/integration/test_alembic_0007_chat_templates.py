"""G-01 RED — Schema test: alembic revision 0007_chat_templates.

Flow
----
1.  Spin up a testcontainers Postgres instance (session-scoped postgres_container).
2.  Apply ``alembic.command.upgrade(config, "0007_chat_templates")``.
3.  Assert the schema produced by revision 0007 matches the Phase G plan exactly:
    - ``corpus.chat_templates`` table with 8 columns and 1 UNIQUE constraint on name.

SQLite parity
-------------
``test_chat_templates_table_shape_sqlite`` runs against an in-memory SQLite DB via
``alembic.command.upgrade(config, "0007_chat_templates")``.  SQLite type conventions
from prior phases apply:
  - BIGSERIAL → INTEGER PRIMARY KEY (no AUTOINCREMENT keyword)
  - TIMESTAMPTZ → TEXT with DEFAULT (datetime('now'))
  - No ``corpus.`` schema prefix

RED condition
-------------
At tester-commit time the ``0007_chat_templates`` revision file does not yet exist.
Every test in this file fails with:

    alembic.util.exc.CommandError: Can't locate revision identified by
    '0007_chat_templates'
"""

from __future__ import annotations

import importlib
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import psycopg
    from testcontainers.postgres import PostgresContainer

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Availability guards
# ---------------------------------------------------------------------------

_TESTCONTAINERS_AVAILABLE = importlib.util.find_spec("testcontainers") is not None

_skip_no_tc = pytest.mark.skipif(
    not _TESTCONTAINERS_AVAILABLE,
    reason="testcontainers not installed — 0007 schema test skipped",
)

# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dsn_from_container(c: PostgresContainer) -> str:
    """Build a bare postgresql:// DSN from a testcontainers Postgres container."""
    return (
        f"postgresql://{c.username}:{c.password}"
        f"@{c.get_container_host_ip()}:{c.get_exposed_port(5432)}"
        f"/{c.dbname}"
    )


def _sa_dsn(dsn: str) -> str:
    """Convert postgresql:// → postgresql+psycopg:// for SQLAlchemy/Alembic."""
    return re.sub(r"^postgresql(s?)://", r"postgresql+psycopg\1://", dsn)


def _alembic_upgrade_pg(dsn: str, target: str) -> None:
    """Run alembic.command.upgrade(config, target) against a Postgres *dsn*.

    Raises alembic.util.exc.CommandError when *target* is unknown (RED state).
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
    """Run alembic.command.upgrade(config, target) against a SQLite *db_path*.

    Raises alembic.util.exc.CommandError when *target* is unknown (RED state).
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


def _reset_schema(dsn: str) -> None:
    """Drop and recreate the corpus schema + pgvector extension."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS corpus CASCADE")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE SCHEMA IF NOT EXISTS corpus")


def _column_info_pg(
    conn: psycopg.Connection,
    table_schema: str,
    table_name: str,
) -> dict[str, dict[str, str | None]]:
    """Return a dict of column_name → {data_type, is_nullable, column_default}
    for all columns in the given table, queried from information_schema.columns.
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


def _unique_constraints_pg(
    conn: psycopg.Connection,
    schema_name: str,
    table_name: str,
) -> set[str]:
    """Return the set of column names covered by UNIQUE constraints on the table."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema    = kcu.table_schema
             AND tc.table_name      = kcu.table_name
            WHERE tc.constraint_type = 'UNIQUE'
              AND tc.table_schema    = %s
              AND tc.table_name      = %s
            """,
            (schema_name, table_name),
        )
        rows = cur.fetchall()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_skip_no_tc
def test_chat_templates_table_shape_pg(postgres_container: PostgresContainer) -> None:  # type: ignore[return]
    """corpus.chat_templates exists with correct columns after 0007_chat_templates upgrade.

    Expected columns:
      id          BIGSERIAL PRIMARY KEY  → bigint, NOT NULL
      name        TEXT NOT NULL UNIQUE   → text, NOT NULL; UNIQUE constraint present
      source      TEXT NOT NULL          → text, NOT NULL  ('builtin'|'huggingface'|'custom')
      jinja       TEXT (nullable)        → text, nullable
      model_id    TEXT (nullable)        → text, nullable
      description TEXT (nullable)        → text, nullable
      host        TEXT NOT NULL          → text, NOT NULL
      created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                                         → timestamp with time zone, NOT NULL, default now()

    RED: fails with CommandError because revision 0007_chat_templates doesn't exist yet.
    """
    import psycopg

    dsn = _dsn_from_container(postgres_container)
    _reset_schema(dsn)

    # Upgrade all the way through 0007 — this is the RED trip-wire.
    _alembic_upgrade_pg(dsn, "0007_chat_templates")

    with psycopg.connect(dsn) as conn:
        cols = _column_info_pg(conn, "corpus", "chat_templates")

        # ── Table must exist ──────────────────────────────────────────────────
        assert cols, (
            "corpus.chat_templates table not found after 0007_chat_templates upgrade "
            "(information_schema.columns returned no rows)"
        )

        # ── Expected column set ───────────────────────────────────────────────
        expected_columns = {
            "id",
            "name",
            "source",
            "jinja",
            "model_id",
            "description",
            "host",
            "created_at",
        }
        assert set(cols.keys()) == expected_columns, (
            f"corpus.chat_templates columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(cols.keys())}"
        )

        # ── id — bigint (BIGSERIAL), NOT NULL ─────────────────────────────────
        assert cols["id"]["data_type"] == "bigint", (
            f"chat_templates.id: expected bigint, got {cols['id']['data_type']!r}"
        )
        assert cols["id"]["is_nullable"] == "NO", (
            f"chat_templates.id: expected NOT NULL, got is_nullable={cols['id']['is_nullable']!r}"
        )

        # ── name — text, NOT NULL ─────────────────────────────────────────────
        assert cols["name"]["data_type"] == "text", (
            f"chat_templates.name: expected text, got {cols['name']['data_type']!r}"
        )
        assert cols["name"]["is_nullable"] == "NO", (
            f"chat_templates.name: expected NOT NULL, "
            f"got is_nullable={cols['name']['is_nullable']!r}"
        )

        # ── source — text, NOT NULL ───────────────────────────────────────────
        assert cols["source"]["data_type"] == "text", (
            f"chat_templates.source: expected text, got {cols['source']['data_type']!r}"
        )
        assert cols["source"]["is_nullable"] == "NO", (
            f"chat_templates.source: expected NOT NULL, "
            f"got is_nullable={cols['source']['is_nullable']!r}"
        )

        # ── jinja — text, nullable ────────────────────────────────────────────
        assert cols["jinja"]["data_type"] == "text", (
            f"chat_templates.jinja: expected text, got {cols['jinja']['data_type']!r}"
        )
        assert cols["jinja"]["is_nullable"] == "YES", (
            f"chat_templates.jinja: expected nullable, "
            f"got is_nullable={cols['jinja']['is_nullable']!r}"
        )

        # ── model_id — text, nullable ─────────────────────────────────────────
        assert cols["model_id"]["data_type"] == "text", (
            f"chat_templates.model_id: expected text, got {cols['model_id']['data_type']!r}"
        )
        assert cols["model_id"]["is_nullable"] == "YES", (
            f"chat_templates.model_id: expected nullable, "
            f"got is_nullable={cols['model_id']['is_nullable']!r}"
        )

        # ── description — text, nullable ──────────────────────────────────────
        assert cols["description"]["data_type"] == "text", (
            f"chat_templates.description: expected text, got {cols['description']['data_type']!r}"
        )
        assert cols["description"]["is_nullable"] == "YES", (
            f"chat_templates.description: expected nullable, "
            f"got is_nullable={cols['description']['is_nullable']!r}"
        )

        # ── host — text, NOT NULL ─────────────────────────────────────────────
        assert cols["host"]["data_type"] == "text", (
            f"chat_templates.host: expected text, got {cols['host']['data_type']!r}"
        )
        assert cols["host"]["is_nullable"] == "NO", (
            f"chat_templates.host: expected NOT NULL, "
            f"got is_nullable={cols['host']['is_nullable']!r}"
        )

        # ── created_at — timestamp with time zone, NOT NULL, DEFAULT now() ────
        assert cols["created_at"]["data_type"] == "timestamp with time zone", (
            f"chat_templates.created_at: expected 'timestamp with time zone', "
            f"got {cols['created_at']['data_type']!r}"
        )
        assert cols["created_at"]["is_nullable"] == "NO", (
            f"chat_templates.created_at: expected NOT NULL, "
            f"got is_nullable={cols['created_at']['is_nullable']!r}"
        )
        created_at_default = (cols["created_at"]["column_default"] or "").lower()
        assert "now" in created_at_default, (
            f"chat_templates.created_at: expected DEFAULT containing 'now', "
            f"got {cols['created_at']['column_default']!r}"
        )

        # ── UNIQUE constraint on name ─────────────────────────────────────────
        unique_cols = _unique_constraints_pg(conn, "corpus", "chat_templates")
        assert "name" in unique_cols, (
            f"corpus.chat_templates: UNIQUE constraint on 'name' not found. "
            f"Columns with UNIQUE constraints: {sorted(unique_cols)}"
        )


def test_chat_templates_table_shape_sqlite(tmp_path: Path) -> None:
    """chat_templates table exists with correct shape after 0007_chat_templates upgrade (SQLite).

    SQLite type conventions (established by prior phases):
      - BIGSERIAL → INTEGER PRIMARY KEY (no AUTOINCREMENT)
      - TIMESTAMPTZ → TEXT with DEFAULT (datetime('now'))
      - No corpus. prefix
      - Column type info via PRAGMA table_info

    Expected columns (SQLite pragmatics):
      id          INTEGER PRIMARY KEY
      name        TEXT NOT NULL UNIQUE
      source      TEXT NOT NULL
      jinja       TEXT (nullable)
      model_id    TEXT (nullable)
      description TEXT (nullable)
      host        TEXT NOT NULL
      created_at  TEXT NOT NULL DEFAULT (datetime('now'))

    RED: fails with CommandError because revision 0007_chat_templates doesn't exist yet.
    """
    db_path = tmp_path / "chat_templates_test.db"

    # Upgrade all the way through 0007 — this is the RED trip-wire.
    _alembic_upgrade_sqlite(db_path, "0007_chat_templates")

    conn = sqlite3.connect(str(db_path))
    try:
        # ── Table must exist ──────────────────────────────────────────────────
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chat_templates'"
        ).fetchall()
        assert len(table_rows) == 1, (
            "chat_templates table not found in sqlite_master after 0007_chat_templates upgrade"
        )

        # PRAGMA table_info returns: (cid, name, type, notnull, dflt_value, pk)
        pragma_rows = conn.execute("PRAGMA table_info(chat_templates)").fetchall()
        assert pragma_rows, (
            "PRAGMA table_info(chat_templates) returned no rows — table may be empty DDL"
        )

        col_map = {
            row[1]: {
                "type": row[2].upper(),
                "notnull": bool(row[3]),
                "dflt_value": row[4],
                "pk": bool(row[5]),
            }
            for row in pragma_rows
        }

        # ── Expected column names ─────────────────────────────────────────────
        expected_columns = {
            "id",
            "name",
            "source",
            "jinja",
            "model_id",
            "description",
            "host",
            "created_at",
        }
        assert set(col_map.keys()) == expected_columns, (
            f"chat_templates columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(col_map.keys())}"
        )

        # ── id — INTEGER PRIMARY KEY ──────────────────────────────────────────
        assert col_map["id"]["pk"], (
            "chat_templates.id: expected PRIMARY KEY (pk=1 in PRAGMA table_info)"
        )
        assert "INTEGER" in col_map["id"]["type"], (
            f"chat_templates.id: expected INTEGER type, got {col_map['id']['type']!r}"
        )

        # ── name — TEXT, NOT NULL ─────────────────────────────────────────────
        assert "TEXT" in col_map["name"]["type"], (
            f"chat_templates.name: expected TEXT type, got {col_map['name']['type']!r}"
        )
        assert col_map["name"]["notnull"], "chat_templates.name: expected NOT NULL"

        # ── source — TEXT, NOT NULL ───────────────────────────────────────────
        assert "TEXT" in col_map["source"]["type"], (
            f"chat_templates.source: expected TEXT type, got {col_map['source']['type']!r}"
        )
        assert col_map["source"]["notnull"], "chat_templates.source: expected NOT NULL"

        # ── jinja — TEXT, nullable ────────────────────────────────────────────
        assert "TEXT" in col_map["jinja"]["type"], (
            f"chat_templates.jinja: expected TEXT type, got {col_map['jinja']['type']!r}"
        )
        assert not col_map["jinja"]["notnull"], (
            "chat_templates.jinja: expected nullable (notnull=0)"
        )

        # ── model_id — TEXT, nullable ─────────────────────────────────────────
        assert "TEXT" in col_map["model_id"]["type"], (
            f"chat_templates.model_id: expected TEXT type, got {col_map['model_id']['type']!r}"
        )
        assert not col_map["model_id"]["notnull"], (
            "chat_templates.model_id: expected nullable (notnull=0)"
        )

        # ── description — TEXT, nullable ──────────────────────────────────────
        assert "TEXT" in col_map["description"]["type"], (
            f"chat_templates.description: expected TEXT type, "
            f"got {col_map['description']['type']!r}"
        )
        assert not col_map["description"]["notnull"], (
            "chat_templates.description: expected nullable (notnull=0)"
        )

        # ── host — TEXT, NOT NULL ─────────────────────────────────────────────
        assert "TEXT" in col_map["host"]["type"], (
            f"chat_templates.host: expected TEXT type, got {col_map['host']['type']!r}"
        )
        assert col_map["host"]["notnull"], "chat_templates.host: expected NOT NULL"

        # ── created_at — TEXT, NOT NULL, DEFAULT (datetime('now')) ────────────
        assert "TEXT" in col_map["created_at"]["type"], (
            f"chat_templates.created_at: expected TEXT type (SQLite TIMESTAMPTZ convention), "
            f"got {col_map['created_at']['type']!r}"
        )
        assert col_map["created_at"]["notnull"], "chat_templates.created_at: expected NOT NULL"
        created_at_default = (col_map["created_at"]["dflt_value"] or "").lower()
        assert "datetime" in created_at_default, (
            f"chat_templates.created_at: expected DEFAULT containing 'datetime', "
            f"got {col_map['created_at']['dflt_value']!r}"
        )
        assert "now" in created_at_default, (
            f"chat_templates.created_at: expected DEFAULT containing 'now', "
            f"got {col_map['created_at']['dflt_value']!r}"
        )

        # ── UNIQUE constraint on name — verify via sqlite_master index ─────────
        # SQLite enforces UNIQUE inline constraints as implicit indexes named
        # sqlite_autoindex_<table>_<n> or explicit named indexes.
        # Check for any unique index on 'name' column.
        index_rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='chat_templates'"
        ).fetchall()
        unique_on_name = False
        for _idx_name, idx_sql in index_rows:
            if idx_sql is None:
                # sqlite_autoindex entries have NULL sql — they're always unique
                # We need to check their columns via PRAGMA index_info
                continue
            idx_sql_lower = idx_sql.lower()
            if "unique" in idx_sql_lower and "name" in idx_sql_lower:
                unique_on_name = True
                break

        # Also check autoindex entries (created for UNIQUE inline constraints)
        for idx_name, idx_sql in index_rows:
            if idx_sql is None and "autoindex" in idx_name.lower():
                # Inspect which columns this autoindex covers
                info = conn.execute(f"PRAGMA index_info({idx_name})").fetchall()
                col_names_in_idx = {row[2] for row in info}
                if "name" in col_names_in_idx:
                    unique_on_name = True
                    break

        assert unique_on_name, (
            f"chat_templates: no UNIQUE constraint/index found on 'name' column. "
            f"Indexes present: {[row[0] for row in index_rows]}"
        )
    finally:
        conn.close()
