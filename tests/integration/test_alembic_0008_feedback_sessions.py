"""H-01 RED — Schema test: alembic revision 0008_feedback_sessions.

Flow
----
1.  Spin up a testcontainers Postgres instance (session-scoped postgres_container).
2.  Apply ``alembic.command.upgrade(config, "0008_feedback_sessions")``.
3.  Assert the schema produced by revision 0008 matches the Phase H plan exactly:
    - ``corpus.feedback_sessions`` table with 7 columns and UNIQUE(client, session_id).
    - ``corpus.feedback_events`` table with 7 columns and index on (feedback_session_id).

SQLite parity
-------------
``test_feedback_sessions_table_shape_sqlite`` and
``test_feedback_events_table_shape_sqlite`` run against an in-memory SQLite DB
via ``alembic.command.upgrade(config, "0008_feedback_sessions")``.

SQLite type conventions from prior phases apply:
  - BIGSERIAL → INTEGER PRIMARY KEY (no AUTOINCREMENT keyword)
  - TIMESTAMPTZ → TEXT (with DEFAULT (datetime('now')) for ts; no default for started_at)
  - No ``corpus.`` schema prefix
  - FKs as ordinary SQLite REFERENCES clauses

RED condition
-------------
At tester-commit time the ``0008_feedback_sessions`` revision file does not yet exist.
Every test in this file fails with:

    alembic.util.exc.CommandError: Can't locate revision identified by
    '0008_feedback_sessions'
"""

from __future__ import annotations

import importlib
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Availability guards
# ---------------------------------------------------------------------------

_TESTCONTAINERS_AVAILABLE = importlib.util.find_spec("testcontainers") is not None

_skip_no_tc = pytest.mark.skipif(
    not _TESTCONTAINERS_AVAILABLE,
    reason="testcontainers not installed — 0008 schema test skipped",
)

# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dsn_from_container(c: Any) -> str:
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
    conn: Any,
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
    conn: Any,
    schema_name: str,
    table_name: str,
) -> list[frozenset[str]]:
    """Return a list of frozensets, each representing the column set of one UNIQUE constraint."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tc.constraint_name, kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema    = kcu.table_schema
             AND tc.table_name      = kcu.table_name
            WHERE tc.constraint_type = 'UNIQUE'
              AND tc.table_schema    = %s
              AND tc.table_name      = %s
            ORDER BY tc.constraint_name, kcu.ordinal_position
            """,
            (schema_name, table_name),
        )
        rows = cur.fetchall()

    # Group by constraint_name
    groups: dict[str, set[str]] = {}
    for constraint_name, col_name in rows:
        groups.setdefault(constraint_name, set()).add(col_name)
    return [frozenset(cols) for cols in groups.values()]


def _index_names_pg(
    conn: Any,
    schema_name: str,
    table_name: str,
) -> dict[str, str]:
    """Return a dict of indexname → indexdef for the given table, from pg_indexes."""
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


# ---------------------------------------------------------------------------
# Tests — Postgres
# ---------------------------------------------------------------------------


@_skip_no_tc
def test_feedback_sessions_table_shape_pg(postgres_container: Any) -> None:  # type: ignore[return]
    """corpus.feedback_sessions exists with correct columns after 0008_feedback_sessions upgrade.

    Expected columns:
      id              BIGSERIAL PRIMARY KEY       → bigint, NOT NULL
      client          TEXT NOT NULL               → text, NOT NULL
      session_id      TEXT NOT NULL               → text, NOT NULL
      host            TEXT NOT NULL               → text, NOT NULL
      started_at      TIMESTAMPTZ NOT NULL        → timestamp with time zone, NOT NULL
      ended_at        TIMESTAMPTZ (nullable)      → timestamp with time zone, nullable
      conversation_id BIGINT (nullable, FK →      → bigint, nullable
                      corpus.conversations(id)
                      ON DELETE SET NULL)

    Plus UNIQUE(client, session_id).

    RED: fails with CommandError because revision 0008_feedback_sessions doesn't exist yet.
    """
    import psycopg

    dsn = _dsn_from_container(postgres_container)
    _reset_schema(dsn)

    # Upgrade all the way through 0008 — this is the RED trip-wire.
    _alembic_upgrade_pg(dsn, "0008_feedback_sessions")

    with psycopg.connect(dsn) as conn:
        cols = _column_info_pg(conn, "corpus", "feedback_sessions")

        # ── Table must exist ──────────────────────────────────────────────────
        assert cols, (
            "corpus.feedback_sessions table not found after 0008_feedback_sessions upgrade "
            "(information_schema.columns returned no rows)"
        )

        # ── Expected column set ───────────────────────────────────────────────
        expected_columns = {
            "id",
            "client",
            "session_id",
            "host",
            "started_at",
            "ended_at",
            "conversation_id",
        }
        assert set(cols.keys()) == expected_columns, (
            f"corpus.feedback_sessions columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(cols.keys())}"
        )

        # ── id — bigint (BIGSERIAL), NOT NULL ─────────────────────────────────
        assert cols["id"]["data_type"] == "bigint", (
            f"feedback_sessions.id: expected bigint, got {cols['id']['data_type']!r}"
        )
        assert cols["id"]["is_nullable"] == "NO", (
            f"feedback_sessions.id: expected NOT NULL, "
            f"got is_nullable={cols['id']['is_nullable']!r}"
        )

        # ── client — text, NOT NULL ───────────────────────────────────────────
        assert cols["client"]["data_type"] == "text", (
            f"feedback_sessions.client: expected text, got {cols['client']['data_type']!r}"
        )
        assert cols["client"]["is_nullable"] == "NO", (
            f"feedback_sessions.client: expected NOT NULL, "
            f"got is_nullable={cols['client']['is_nullable']!r}"
        )

        # ── session_id — text, NOT NULL ───────────────────────────────────────
        assert cols["session_id"]["data_type"] == "text", (
            f"feedback_sessions.session_id: expected text, got {cols['session_id']['data_type']!r}"
        )
        assert cols["session_id"]["is_nullable"] == "NO", (
            f"feedback_sessions.session_id: expected NOT NULL, "
            f"got is_nullable={cols['session_id']['is_nullable']!r}"
        )

        # ── host — text, NOT NULL ─────────────────────────────────────────────
        assert cols["host"]["data_type"] == "text", (
            f"feedback_sessions.host: expected text, got {cols['host']['data_type']!r}"
        )
        assert cols["host"]["is_nullable"] == "NO", (
            f"feedback_sessions.host: expected NOT NULL, "
            f"got is_nullable={cols['host']['is_nullable']!r}"
        )

        # ── started_at — timestamp with time zone, NOT NULL ───────────────────
        assert cols["started_at"]["data_type"] == "timestamp with time zone", (
            f"feedback_sessions.started_at: expected 'timestamp with time zone', "
            f"got {cols['started_at']['data_type']!r}"
        )
        assert cols["started_at"]["is_nullable"] == "NO", (
            f"feedback_sessions.started_at: expected NOT NULL, "
            f"got is_nullable={cols['started_at']['is_nullable']!r}"
        )

        # ── ended_at — timestamp with time zone, nullable ─────────────────────
        assert cols["ended_at"]["data_type"] == "timestamp with time zone", (
            f"feedback_sessions.ended_at: expected 'timestamp with time zone', "
            f"got {cols['ended_at']['data_type']!r}"
        )
        assert cols["ended_at"]["is_nullable"] == "YES", (
            f"feedback_sessions.ended_at: expected nullable, "
            f"got is_nullable={cols['ended_at']['is_nullable']!r}"
        )

        # ── conversation_id — bigint, nullable ────────────────────────────────
        assert cols["conversation_id"]["data_type"] == "bigint", (
            f"feedback_sessions.conversation_id: expected bigint, "
            f"got {cols['conversation_id']['data_type']!r}"
        )
        assert cols["conversation_id"]["is_nullable"] == "YES", (
            f"feedback_sessions.conversation_id: expected nullable, "
            f"got is_nullable={cols['conversation_id']['is_nullable']!r}"
        )

        # ── UNIQUE(client, session_id) ─────────────────────────────────────────
        unique_constraints = _unique_constraints_pg(conn, "corpus", "feedback_sessions")
        target_unique = frozenset({"client", "session_id"})
        assert target_unique in unique_constraints, (
            f"corpus.feedback_sessions: UNIQUE(client, session_id) constraint not found. "
            f"Unique constraint column-sets present: "
            f"{[sorted(c) for c in unique_constraints]}"
        )


@_skip_no_tc
def test_feedback_events_table_shape_pg(postgres_container: Any) -> None:  # type: ignore[return]
    """corpus.feedback_events exists with correct columns after 0008_feedback_sessions upgrade.

    Expected columns:
      id                   BIGSERIAL PRIMARY KEY       → bigint, NOT NULL
      feedback_session_id  BIGINT NOT NULL             → bigint, NOT NULL
                           (FK → corpus.feedback_sessions(id) ON DELETE CASCADE)
      audit_id             BIGINT (nullable)           → bigint, nullable
                           (FK → corpus.mcp_audit(id) ON DELETE CASCADE)
      feedback_id          BIGINT (nullable)           → bigint, nullable
                           (FK → corpus.feedback(id) ON DELETE CASCADE)
      entity_type          TEXT NOT NULL               → text, NOT NULL
      entity_id            BIGINT NOT NULL             → bigint, NOT NULL
      ts                   TIMESTAMPTZ NOT NULL        → timestamp with time zone, NOT NULL
                           DEFAULT NOW()

    Plus index on (feedback_session_id).

    RED: fails with CommandError because revision 0008_feedback_sessions doesn't exist yet.
    """
    import psycopg

    dsn = _dsn_from_container(postgres_container)
    _reset_schema(dsn)

    _alembic_upgrade_pg(dsn, "0008_feedback_sessions")

    with psycopg.connect(dsn) as conn:
        cols = _column_info_pg(conn, "corpus", "feedback_events")

        # ── Table must exist ──────────────────────────────────────────────────
        assert cols, (
            "corpus.feedback_events table not found after 0008_feedback_sessions upgrade "
            "(information_schema.columns returned no rows)"
        )

        # ── Expected column set ───────────────────────────────────────────────
        expected_columns = {
            "id",
            "feedback_session_id",
            "audit_id",
            "feedback_id",
            "entity_type",
            "entity_id",
            "ts",
        }
        assert set(cols.keys()) == expected_columns, (
            f"corpus.feedback_events columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(cols.keys())}"
        )

        # ── id — bigint (BIGSERIAL), NOT NULL ─────────────────────────────────
        assert cols["id"]["data_type"] == "bigint", (
            f"feedback_events.id: expected bigint, got {cols['id']['data_type']!r}"
        )
        assert cols["id"]["is_nullable"] == "NO", (
            f"feedback_events.id: expected NOT NULL, got is_nullable={cols['id']['is_nullable']!r}"
        )

        # ── feedback_session_id — bigint, NOT NULL ────────────────────────────
        assert cols["feedback_session_id"]["data_type"] == "bigint", (
            f"feedback_events.feedback_session_id: expected bigint, "
            f"got {cols['feedback_session_id']['data_type']!r}"
        )
        assert cols["feedback_session_id"]["is_nullable"] == "NO", (
            f"feedback_events.feedback_session_id: expected NOT NULL, "
            f"got is_nullable={cols['feedback_session_id']['is_nullable']!r}"
        )

        # ── audit_id — bigint, nullable ───────────────────────────────────────
        assert cols["audit_id"]["data_type"] == "bigint", (
            f"feedback_events.audit_id: expected bigint, got {cols['audit_id']['data_type']!r}"
        )
        assert cols["audit_id"]["is_nullable"] == "YES", (
            f"feedback_events.audit_id: expected nullable, "
            f"got is_nullable={cols['audit_id']['is_nullable']!r}"
        )

        # ── feedback_id — bigint, nullable ────────────────────────────────────
        assert cols["feedback_id"]["data_type"] == "bigint", (
            f"feedback_events.feedback_id: expected bigint, "
            f"got {cols['feedback_id']['data_type']!r}"
        )
        assert cols["feedback_id"]["is_nullable"] == "YES", (
            f"feedback_events.feedback_id: expected nullable, "
            f"got is_nullable={cols['feedback_id']['is_nullable']!r}"
        )

        # ── entity_type — text, NOT NULL ──────────────────────────────────────
        assert cols["entity_type"]["data_type"] == "text", (
            f"feedback_events.entity_type: expected text, got {cols['entity_type']['data_type']!r}"
        )
        assert cols["entity_type"]["is_nullable"] == "NO", (
            f"feedback_events.entity_type: expected NOT NULL, "
            f"got is_nullable={cols['entity_type']['is_nullable']!r}"
        )

        # ── entity_id — bigint, NOT NULL ──────────────────────────────────────
        assert cols["entity_id"]["data_type"] == "bigint", (
            f"feedback_events.entity_id: expected bigint, got {cols['entity_id']['data_type']!r}"
        )
        assert cols["entity_id"]["is_nullable"] == "NO", (
            f"feedback_events.entity_id: expected NOT NULL, "
            f"got is_nullable={cols['entity_id']['is_nullable']!r}"
        )

        # ── ts — timestamp with time zone, NOT NULL, DEFAULT NOW() ────────────
        assert cols["ts"]["data_type"] == "timestamp with time zone", (
            f"feedback_events.ts: expected 'timestamp with time zone', "
            f"got {cols['ts']['data_type']!r}"
        )
        assert cols["ts"]["is_nullable"] == "NO", (
            f"feedback_events.ts: expected NOT NULL, got is_nullable={cols['ts']['is_nullable']!r}"
        )
        ts_default = (cols["ts"]["column_default"] or "").lower()
        assert "now" in ts_default, (
            f"feedback_events.ts: expected DEFAULT containing 'now', "
            f"got {cols['ts']['column_default']!r}"
        )

        # ── Index on (feedback_session_id) ────────────────────────────────────
        indexes = _index_names_pg(conn, "corpus", "feedback_events")
        # Find an index whose definition references feedback_session_id
        session_id_indexed = any(
            "feedback_session_id" in idx_def.lower()
            for idx_def in indexes.values()
            if idx_def is not None
        )
        assert session_id_indexed, (
            f"corpus.feedback_events: no index found on feedback_session_id column. "
            f"Indexes present: {list(indexes.keys())}"
        )


# ---------------------------------------------------------------------------
# Tests — SQLite
# ---------------------------------------------------------------------------


def test_feedback_sessions_table_shape_sqlite(tmp_path: Path) -> None:
    """feedback_sessions table shape after 0008_feedback_sessions upgrade (SQLite).

    SQLite type conventions (established by prior phases):
      - BIGSERIAL → INTEGER PRIMARY KEY (no AUTOINCREMENT)
      - TIMESTAMPTZ NOT NULL → TEXT NOT NULL (no default for started_at)
      - TIMESTAMPTZ nullable → TEXT (nullable) for ended_at
      - No corpus. prefix
      - FKs as ordinary SQLite REFERENCES clauses
      - UNIQUE(client, session_id) enforced via sqlite_master index

    Expected columns:
      id              INTEGER PRIMARY KEY
      client          TEXT NOT NULL
      session_id      TEXT NOT NULL
      host            TEXT NOT NULL
      started_at      TEXT NOT NULL
      ended_at        TEXT (nullable)
      conversation_id INTEGER (nullable)

    RED: fails with CommandError because revision 0008_feedback_sessions doesn't exist yet.
    """
    db_path = tmp_path / "feedback_sessions_test.db"

    # Upgrade all the way through 0008 — this is the RED trip-wire.
    _alembic_upgrade_sqlite(db_path, "0008_feedback_sessions")

    conn = sqlite3.connect(str(db_path))
    try:
        # ── Table must exist ──────────────────────────────────────────────────
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='feedback_sessions'"
        ).fetchall()
        assert len(table_rows) == 1, (
            "feedback_sessions table not found in sqlite_master after 0008 upgrade"
        )

        # PRAGMA table_info returns: (cid, name, type, notnull, dflt_value, pk)
        pragma_rows = conn.execute("PRAGMA table_info(feedback_sessions)").fetchall()
        assert pragma_rows, (
            "PRAGMA table_info(feedback_sessions) returned no rows — table may be empty DDL"
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
            "client",
            "session_id",
            "host",
            "started_at",
            "ended_at",
            "conversation_id",
        }
        assert set(col_map.keys()) == expected_columns, (
            f"feedback_sessions columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(col_map.keys())}"
        )

        # ── id — INTEGER PRIMARY KEY ──────────────────────────────────────────
        assert col_map["id"]["pk"], (
            "feedback_sessions.id: expected PRIMARY KEY (pk=1 in PRAGMA table_info)"
        )
        assert "INTEGER" in col_map["id"]["type"], (
            f"feedback_sessions.id: expected INTEGER type, got {col_map['id']['type']!r}"
        )

        # ── client — TEXT, NOT NULL ───────────────────────────────────────────
        assert "TEXT" in col_map["client"]["type"], (
            f"feedback_sessions.client: expected TEXT type, got {col_map['client']['type']!r}"
        )
        assert col_map["client"]["notnull"], "feedback_sessions.client: expected NOT NULL"

        # ── session_id — TEXT, NOT NULL ───────────────────────────────────────
        assert "TEXT" in col_map["session_id"]["type"], (
            f"feedback_sessions.session_id: expected TEXT type, "
            f"got {col_map['session_id']['type']!r}"
        )
        assert col_map["session_id"]["notnull"], "feedback_sessions.session_id: expected NOT NULL"

        # ── host — TEXT, NOT NULL ─────────────────────────────────────────────
        assert "TEXT" in col_map["host"]["type"], (
            f"feedback_sessions.host: expected TEXT type, got {col_map['host']['type']!r}"
        )
        assert col_map["host"]["notnull"], "feedback_sessions.host: expected NOT NULL"

        # ── started_at — TEXT, NOT NULL (no default) ──────────────────────────
        assert "TEXT" in col_map["started_at"]["type"], (
            f"feedback_sessions.started_at: expected TEXT type (SQLite TIMESTAMPTZ convention), "
            f"got {col_map['started_at']['type']!r}"
        )
        assert col_map["started_at"]["notnull"], "feedback_sessions.started_at: expected NOT NULL"

        # ── ended_at — TEXT, nullable ─────────────────────────────────────────
        assert "TEXT" in col_map["ended_at"]["type"], (
            f"feedback_sessions.ended_at: expected TEXT type (SQLite TIMESTAMPTZ convention), "
            f"got {col_map['ended_at']['type']!r}"
        )
        assert not col_map["ended_at"]["notnull"], (
            "feedback_sessions.ended_at: expected nullable (notnull=0)"
        )

        # ── conversation_id — INTEGER, nullable ───────────────────────────────
        assert "INTEGER" in col_map["conversation_id"]["type"], (
            f"feedback_sessions.conversation_id: expected INTEGER type, "
            f"got {col_map['conversation_id']['type']!r}"
        )
        assert not col_map["conversation_id"]["notnull"], (
            "feedback_sessions.conversation_id: expected nullable (notnull=0)"
        )

        # ── UNIQUE(client, session_id) — verify via sqlite_master index ────────
        index_rows = conn.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type='index' AND tbl_name='feedback_sessions'"
        ).fetchall()

        unique_client_session = False

        # Check explicit indexes with UNIQUE keyword covering both columns
        for _idx_name, idx_sql in index_rows:
            if idx_sql is None:
                continue
            idx_sql_lower = idx_sql.lower()
            if (
                "unique" in idx_sql_lower
                and "client" in idx_sql_lower
                and "session_id" in idx_sql_lower
            ):
                unique_client_session = True
                break

        # Check autoindex entries (created for inline UNIQUE constraints)
        if not unique_client_session:
            for idx_name, idx_sql in index_rows:
                if idx_sql is None and "autoindex" in idx_name.lower():
                    info = conn.execute(f"PRAGMA index_info({idx_name})").fetchall()
                    col_names_in_idx = {row[2] for row in info}
                    if "client" in col_names_in_idx and "session_id" in col_names_in_idx:
                        unique_client_session = True
                        break

        assert unique_client_session, (
            f"feedback_sessions: no UNIQUE(client, session_id) constraint/index found. "
            f"Indexes present: {[row[0] for row in index_rows]}"
        )
    finally:
        conn.close()


def test_feedback_events_table_shape_sqlite(tmp_path: Path) -> None:
    """feedback_events table shape after 0008_feedback_sessions upgrade (SQLite).

    SQLite type conventions:
      - BIGSERIAL → INTEGER PRIMARY KEY (no AUTOINCREMENT)
      - TIMESTAMPTZ NOT NULL DEFAULT NOW() → TEXT NOT NULL DEFAULT (datetime('now'))
      - No corpus. prefix
      - FKs as ordinary SQLite REFERENCES clauses
      - Index on (feedback_session_id) via separate CREATE INDEX statement

    Expected columns:
      id                   INTEGER PRIMARY KEY
      feedback_session_id  INTEGER NOT NULL
      audit_id             INTEGER (nullable)
      feedback_id          INTEGER (nullable)
      entity_type          TEXT NOT NULL
      entity_id            INTEGER NOT NULL
      ts                   TEXT NOT NULL DEFAULT (datetime('now'))

    RED: fails with CommandError because revision 0008_feedback_sessions doesn't exist yet.
    """
    db_path = tmp_path / "feedback_events_test.db"

    # Upgrade all the way through 0008 — this is the RED trip-wire.
    _alembic_upgrade_sqlite(db_path, "0008_feedback_sessions")

    conn = sqlite3.connect(str(db_path))
    try:
        # ── Table must exist ──────────────────────────────────────────────────
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='feedback_events'"
        ).fetchall()
        assert len(table_rows) == 1, (
            "feedback_events table not found in sqlite_master after 0008_feedback_sessions upgrade"
        )

        # PRAGMA table_info returns: (cid, name, type, notnull, dflt_value, pk)
        pragma_rows = conn.execute("PRAGMA table_info(feedback_events)").fetchall()
        assert pragma_rows, (
            "PRAGMA table_info(feedback_events) returned no rows — table may be empty DDL"
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
            "feedback_session_id",
            "audit_id",
            "feedback_id",
            "entity_type",
            "entity_id",
            "ts",
        }
        assert set(col_map.keys()) == expected_columns, (
            f"feedback_events columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(col_map.keys())}"
        )

        # ── id — INTEGER PRIMARY KEY ──────────────────────────────────────────
        assert col_map["id"]["pk"], (
            "feedback_events.id: expected PRIMARY KEY (pk=1 in PRAGMA table_info)"
        )
        assert "INTEGER" in col_map["id"]["type"], (
            f"feedback_events.id: expected INTEGER type, got {col_map['id']['type']!r}"
        )

        # ── feedback_session_id — INTEGER, NOT NULL ───────────────────────────
        assert "INTEGER" in col_map["feedback_session_id"]["type"], (
            f"feedback_events.feedback_session_id: expected INTEGER type, "
            f"got {col_map['feedback_session_id']['type']!r}"
        )
        assert col_map["feedback_session_id"]["notnull"], (
            "feedback_events.feedback_session_id: expected NOT NULL"
        )

        # ── audit_id — INTEGER, nullable ──────────────────────────────────────
        assert "INTEGER" in col_map["audit_id"]["type"], (
            f"feedback_events.audit_id: expected INTEGER type, got {col_map['audit_id']['type']!r}"
        )
        assert not col_map["audit_id"]["notnull"], (
            "feedback_events.audit_id: expected nullable (notnull=0)"
        )

        # ── feedback_id — INTEGER, nullable ───────────────────────────────────
        assert "INTEGER" in col_map["feedback_id"]["type"], (
            f"feedback_events.feedback_id: expected INTEGER type, "
            f"got {col_map['feedback_id']['type']!r}"
        )
        assert not col_map["feedback_id"]["notnull"], (
            "feedback_events.feedback_id: expected nullable (notnull=0)"
        )

        # ── entity_type — TEXT, NOT NULL ──────────────────────────────────────
        assert "TEXT" in col_map["entity_type"]["type"], (
            f"feedback_events.entity_type: expected TEXT type, "
            f"got {col_map['entity_type']['type']!r}"
        )
        assert col_map["entity_type"]["notnull"], "feedback_events.entity_type: expected NOT NULL"

        # ── entity_id — INTEGER, NOT NULL ─────────────────────────────────────
        assert "INTEGER" in col_map["entity_id"]["type"], (
            f"feedback_events.entity_id: expected INTEGER type, "
            f"got {col_map['entity_id']['type']!r}"
        )
        assert col_map["entity_id"]["notnull"], "feedback_events.entity_id: expected NOT NULL"

        # ── ts — TEXT, NOT NULL, DEFAULT (datetime('now')) ────────────────────
        assert "TEXT" in col_map["ts"]["type"], (
            f"feedback_events.ts: expected TEXT type (SQLite TIMESTAMPTZ convention), "
            f"got {col_map['ts']['type']!r}"
        )
        assert col_map["ts"]["notnull"], "feedback_events.ts: expected NOT NULL"
        ts_default = (col_map["ts"]["dflt_value"] or "").lower()
        assert "datetime" in ts_default, (
            f"feedback_events.ts: expected DEFAULT containing 'datetime', "
            f"got {col_map['ts']['dflt_value']!r}"
        )
        assert "now" in ts_default, (
            f"feedback_events.ts: expected DEFAULT containing 'now', "
            f"got {col_map['ts']['dflt_value']!r}"
        )

        # ── Index on (feedback_session_id) ────────────────────────────────────
        index_rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='feedback_events'"
        ).fetchall()

        session_id_indexed = False
        for _idx_name, idx_sql in index_rows:
            if idx_sql is None:
                continue
            if "feedback_session_id" in idx_sql.lower():
                session_id_indexed = True
                break

        assert session_id_indexed, (
            f"feedback_events: no index found on feedback_session_id column. "
            f"Indexes present: {[row[0] for row in index_rows]}"
        )
    finally:
        conn.close()
