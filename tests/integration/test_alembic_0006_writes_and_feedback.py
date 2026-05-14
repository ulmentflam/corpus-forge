"""F-01 RED — Schema test: alembic revision 0006_writes_and_feedback.

Flow
----
1.  Spin up a testcontainers Postgres instance (session-scoped postgres_container).
2.  Apply ``alembic.command.upgrade(config, "0006_writes_and_feedback")``.
3.  Assert the schema produced by revision 0006 matches the Phase F plan exactly:
    - ``description TEXT NULL`` added to corpus.documents, corpus.conversations,
      corpus.chunks.
    - ``corpus.mcp_audit`` table created with 11 columns and 2 indexes.
    - ``corpus.feedback`` table created with 11 columns and 2 indexes.

RED condition
-------------
At tester-commit time the ``0006_writes_and_feedback`` revision file does not yet
exist.  Every test in this file fails with:

    alembic.util.exc.CommandError: Can't locate revision identified by
    '0006_writes_and_feedback'

SQLite parity
-------------
SQLite is OUT OF SCOPE for this test module.  Column type info is not reliably
exposed via ``sqlite_master`` (everything is TEXT at the DDL level).  The F-01
coder will add a SQLite branch to the revision itself and the SQLite smoke tests
cover boot + connectivity, not per-column types.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path
from typing import Any

import psycopg
import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Availability guards
# ---------------------------------------------------------------------------

_TESTCONTAINERS_AVAILABLE = importlib.util.find_spec("testcontainers") is not None

_skip_no_tc = pytest.mark.skipif(
    not _TESTCONTAINERS_AVAILABLE,
    reason="testcontainers not installed — 0006 schema test skipped",
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


def _alembic_upgrade(dsn: str, target: str) -> None:
    """Run alembic.command.upgrade(config, target) against *dsn*.

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


def _reset_schema(dsn: str) -> None:
    """Drop and recreate the corpus schema + pgvector extension."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS corpus CASCADE")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE SCHEMA IF NOT EXISTS corpus")


def _column_info(
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


def _index_names(
    conn: psycopg.Connection,
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
# Tests
# ---------------------------------------------------------------------------


@_skip_no_tc
def test_description_columns_added(postgres_container: Any) -> None:  # type: ignore[return]
    """description TEXT NULL is added to documents, conversations, and chunks.

    Revision 0006_writes_and_feedback runs three ALTER TABLE … ADD COLUMN
    statements.  This test verifies each one landed with the correct type
    (text) and nullability (YES).

    RED: fails with CommandError because revision 0006 doesn't exist yet.
    """
    dsn = _dsn_from_container(postgres_container)
    _reset_schema(dsn)

    # Upgrade all the way through 0006 — this is the RED trip-wire.
    _alembic_upgrade(dsn, "0006_writes_and_feedback")

    tables_to_check = ["documents", "conversations", "chunks"]

    with psycopg.connect(dsn) as conn:
        for table in tables_to_check:
            cols = _column_info(conn, "corpus", table)

            assert "description" in cols, (
                f"corpus.{table}: 'description' column not found after 0006 upgrade. "
                f"Columns present: {sorted(cols)}"
            )

            col = cols["description"]
            assert col["data_type"] == "text", (
                f"corpus.{table}.description: expected data_type='text', got {col['data_type']!r}"
            )
            assert col["is_nullable"] == "YES", (
                f"corpus.{table}.description: expected is_nullable='YES' (nullable), "
                f"got {col['is_nullable']!r}"
            )


@_skip_no_tc
def test_mcp_audit_table_shape(postgres_container: Any) -> None:  # type: ignore[return]
    """corpus.mcp_audit exists with 11 columns matching the Phase F plan and 2 indexes.

    Expected columns (in plan order):
      id BIGSERIAL PRIMARY KEY
      ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
      host TEXT NOT NULL
      client TEXT (nullable)
      session_id TEXT (nullable)
      tool TEXT NOT NULL
      entity_type TEXT NOT NULL
      entity_id BIGINT NOT NULL
      before JSONB (nullable)
      after JSONB (nullable)
      dry_run BOOLEAN NOT NULL DEFAULT FALSE

    Expected indexes:
      mcp_audit_entity_idx  ON (entity_type, entity_id)
      mcp_audit_session_idx ON (session_id)

    RED: fails with CommandError because revision 0006 doesn't exist yet.
    """
    dsn = _dsn_from_container(postgres_container)
    _reset_schema(dsn)

    _alembic_upgrade(dsn, "0006_writes_and_feedback")

    with psycopg.connect(dsn) as conn:
        cols = _column_info(conn, "corpus", "mcp_audit")

        # ── Table must exist ──────────────────────────────────────────────────
        assert cols, (
            "corpus.mcp_audit table not found after 0006 upgrade "
            "(information_schema.columns returned no rows)"
        )

        # ── Expected column set ───────────────────────────────────────────────
        expected_columns = {
            "id",
            "ts",
            "host",
            "client",
            "session_id",
            "tool",
            "entity_type",
            "entity_id",
            "before",
            "after",
            "dry_run",
        }
        assert set(cols.keys()) == expected_columns, (
            f"corpus.mcp_audit columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(cols.keys())}"
        )

        # ── Per-column type + nullability ─────────────────────────────────────
        # id — bigint (BIGSERIAL resolves to bigint in information_schema), NOT NULL
        assert cols["id"]["data_type"] == "bigint", (
            f"mcp_audit.id: expected bigint, got {cols['id']['data_type']!r}"
        )
        assert cols["id"]["is_nullable"] == "NO", (
            f"mcp_audit.id: expected NOT NULL, got is_nullable={cols['id']['is_nullable']!r}"
        )

        # ts — timestamp with time zone, NOT NULL, DEFAULT now()
        assert cols["ts"]["data_type"] == "timestamp with time zone", (
            f"mcp_audit.ts: expected 'timestamp with time zone', got {cols['ts']['data_type']!r}"
        )
        assert cols["ts"]["is_nullable"] == "NO", (
            f"mcp_audit.ts: expected NOT NULL, got is_nullable={cols['ts']['is_nullable']!r}"
        )
        ts_default = (cols["ts"]["column_default"] or "").lower()
        assert "now" in ts_default, (
            f"mcp_audit.ts: expected DEFAULT containing 'now', got {cols['ts']['column_default']!r}"
        )

        # host — text, NOT NULL
        assert cols["host"]["data_type"] == "text", (
            f"mcp_audit.host: expected text, got {cols['host']['data_type']!r}"
        )
        assert cols["host"]["is_nullable"] == "NO", (
            f"mcp_audit.host: expected NOT NULL, got is_nullable={cols['host']['is_nullable']!r}"
        )

        # client — text, nullable
        assert cols["client"]["data_type"] == "text", (
            f"mcp_audit.client: expected text, got {cols['client']['data_type']!r}"
        )
        assert cols["client"]["is_nullable"] == "YES", (
            f"mcp_audit.client: expected nullable, "
            f"got is_nullable={cols['client']['is_nullable']!r}"
        )

        # session_id — text, nullable
        assert cols["session_id"]["data_type"] == "text", (
            f"mcp_audit.session_id: expected text, got {cols['session_id']['data_type']!r}"
        )
        assert cols["session_id"]["is_nullable"] == "YES", (
            f"mcp_audit.session_id: expected nullable, "
            f"got is_nullable={cols['session_id']['is_nullable']!r}"
        )

        # tool — text, NOT NULL
        assert cols["tool"]["data_type"] == "text", (
            f"mcp_audit.tool: expected text, got {cols['tool']['data_type']!r}"
        )
        assert cols["tool"]["is_nullable"] == "NO", (
            f"mcp_audit.tool: expected NOT NULL, got is_nullable={cols['tool']['is_nullable']!r}"
        )

        # entity_type — text, NOT NULL
        assert cols["entity_type"]["data_type"] == "text", (
            f"mcp_audit.entity_type: expected text, got {cols['entity_type']['data_type']!r}"
        )
        assert cols["entity_type"]["is_nullable"] == "NO", (
            f"mcp_audit.entity_type: expected NOT NULL, "
            f"got is_nullable={cols['entity_type']['is_nullable']!r}"
        )

        # entity_id — bigint, NOT NULL
        assert cols["entity_id"]["data_type"] == "bigint", (
            f"mcp_audit.entity_id: expected bigint, got {cols['entity_id']['data_type']!r}"
        )
        assert cols["entity_id"]["is_nullable"] == "NO", (
            f"mcp_audit.entity_id: expected NOT NULL, "
            f"got is_nullable={cols['entity_id']['is_nullable']!r}"
        )

        # before — jsonb, nullable
        assert cols["before"]["data_type"] == "jsonb", (
            f"mcp_audit.before: expected jsonb, got {cols['before']['data_type']!r}"
        )
        assert cols["before"]["is_nullable"] == "YES", (
            f"mcp_audit.before: expected nullable, "
            f"got is_nullable={cols['before']['is_nullable']!r}"
        )

        # after — jsonb, nullable
        assert cols["after"]["data_type"] == "jsonb", (
            f"mcp_audit.after: expected jsonb, got {cols['after']['data_type']!r}"
        )
        assert cols["after"]["is_nullable"] == "YES", (
            f"mcp_audit.after: expected nullable, got is_nullable={cols['after']['is_nullable']!r}"
        )

        # dry_run — boolean, NOT NULL, DEFAULT false
        assert cols["dry_run"]["data_type"] == "boolean", (
            f"mcp_audit.dry_run: expected boolean, got {cols['dry_run']['data_type']!r}"
        )
        assert cols["dry_run"]["is_nullable"] == "NO", (
            f"mcp_audit.dry_run: expected NOT NULL, "
            f"got is_nullable={cols['dry_run']['is_nullable']!r}"
        )
        dry_run_default = (cols["dry_run"]["column_default"] or "").lower()
        assert "false" in dry_run_default, (
            f"mcp_audit.dry_run: expected DEFAULT containing 'false', "
            f"got {cols['dry_run']['column_default']!r}"
        )

        # ── Indexes ───────────────────────────────────────────────────────────
        indexes = _index_names(conn, "corpus", "mcp_audit")

        assert "mcp_audit_entity_idx" in indexes, (
            f"mcp_audit_entity_idx not found in pg_indexes. Indexes present: {sorted(indexes)}"
        )
        entity_idx_def = indexes["mcp_audit_entity_idx"].lower()
        assert "entity_type" in entity_idx_def and "entity_id" in entity_idx_def, (
            f"mcp_audit_entity_idx does not reference (entity_type, entity_id). "
            f"indexdef: {indexes['mcp_audit_entity_idx']!r}"
        )

        assert "mcp_audit_session_idx" in indexes, (
            f"mcp_audit_session_idx not found in pg_indexes. Indexes present: {sorted(indexes)}"
        )
        session_idx_def = indexes["mcp_audit_session_idx"].lower()
        assert "session_id" in session_idx_def, (
            f"mcp_audit_session_idx does not reference session_id. "
            f"indexdef: {indexes['mcp_audit_session_idx']!r}"
        )


@_skip_no_tc
def test_feedback_table_shape(postgres_container: Any) -> None:  # type: ignore[return]
    """corpus.feedback exists with 11 columns matching the Phase F plan and 2 indexes.

    Expected columns (in plan order):
      id BIGSERIAL PRIMARY KEY
      ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
      host TEXT NOT NULL
      client TEXT (nullable)
      session_id TEXT (nullable)
      entity_type TEXT NOT NULL
      entity_id BIGINT NOT NULL
      kind TEXT NOT NULL
      rating INTEGER (nullable)
      text TEXT (nullable)
      metadata JSONB NOT NULL DEFAULT '{}'::jsonb

    Expected indexes:
      feedback_entity_idx  ON (entity_type, entity_id)
      feedback_session_idx ON (session_id)

    RED: fails with CommandError because revision 0006 doesn't exist yet.
    """
    dsn = _dsn_from_container(postgres_container)
    _reset_schema(dsn)

    _alembic_upgrade(dsn, "0006_writes_and_feedback")

    with psycopg.connect(dsn) as conn:
        cols = _column_info(conn, "corpus", "feedback")

        # ── Table must exist ──────────────────────────────────────────────────
        assert cols, (
            "corpus.feedback table not found after 0006 upgrade "
            "(information_schema.columns returned no rows)"
        )

        # ── Expected column set ───────────────────────────────────────────────
        expected_columns = {
            "id",
            "ts",
            "host",
            "client",
            "session_id",
            "entity_type",
            "entity_id",
            "kind",
            "rating",
            "text",
            "metadata",
        }
        assert set(cols.keys()) == expected_columns, (
            f"corpus.feedback columns mismatch.\n"
            f"  expected : {sorted(expected_columns)}\n"
            f"  actual   : {sorted(cols.keys())}"
        )

        # ── Per-column type + nullability ─────────────────────────────────────
        # id — bigint (BIGSERIAL), NOT NULL
        assert cols["id"]["data_type"] == "bigint", (
            f"feedback.id: expected bigint, got {cols['id']['data_type']!r}"
        )
        assert cols["id"]["is_nullable"] == "NO", (
            f"feedback.id: expected NOT NULL, got is_nullable={cols['id']['is_nullable']!r}"
        )

        # ts — timestamp with time zone, NOT NULL, DEFAULT now()
        assert cols["ts"]["data_type"] == "timestamp with time zone", (
            f"feedback.ts: expected 'timestamp with time zone', got {cols['ts']['data_type']!r}"
        )
        assert cols["ts"]["is_nullable"] == "NO", (
            f"feedback.ts: expected NOT NULL, got is_nullable={cols['ts']['is_nullable']!r}"
        )
        ts_default = (cols["ts"]["column_default"] or "").lower()
        assert "now" in ts_default, (
            f"feedback.ts: expected DEFAULT containing 'now', got {cols['ts']['column_default']!r}"
        )

        # host — text, NOT NULL
        assert cols["host"]["data_type"] == "text", (
            f"feedback.host: expected text, got {cols['host']['data_type']!r}"
        )
        assert cols["host"]["is_nullable"] == "NO", (
            f"feedback.host: expected NOT NULL, got is_nullable={cols['host']['is_nullable']!r}"
        )

        # client — text, nullable
        assert cols["client"]["data_type"] == "text", (
            f"feedback.client: expected text, got {cols['client']['data_type']!r}"
        )
        assert cols["client"]["is_nullable"] == "YES", (
            f"feedback.client: expected nullable, got is_nullable={cols['client']['is_nullable']!r}"
        )

        # session_id — text, nullable
        assert cols["session_id"]["data_type"] == "text", (
            f"feedback.session_id: expected text, got {cols['session_id']['data_type']!r}"
        )
        assert cols["session_id"]["is_nullable"] == "YES", (
            f"feedback.session_id: expected nullable, "
            f"got is_nullable={cols['session_id']['is_nullable']!r}"
        )

        # entity_type — text, NOT NULL
        assert cols["entity_type"]["data_type"] == "text", (
            f"feedback.entity_type: expected text, got {cols['entity_type']['data_type']!r}"
        )
        assert cols["entity_type"]["is_nullable"] == "NO", (
            f"feedback.entity_type: expected NOT NULL, "
            f"got is_nullable={cols['entity_type']['is_nullable']!r}"
        )

        # entity_id — bigint, NOT NULL
        assert cols["entity_id"]["data_type"] == "bigint", (
            f"feedback.entity_id: expected bigint, got {cols['entity_id']['data_type']!r}"
        )
        assert cols["entity_id"]["is_nullable"] == "NO", (
            f"feedback.entity_id: expected NOT NULL, "
            f"got is_nullable={cols['entity_id']['is_nullable']!r}"
        )

        # kind — text, NOT NULL
        assert cols["kind"]["data_type"] == "text", (
            f"feedback.kind: expected text, got {cols['kind']['data_type']!r}"
        )
        assert cols["kind"]["is_nullable"] == "NO", (
            f"feedback.kind: expected NOT NULL, got is_nullable={cols['kind']['is_nullable']!r}"
        )

        # rating — integer, nullable
        assert cols["rating"]["data_type"] == "integer", (
            f"feedback.rating: expected integer, got {cols['rating']['data_type']!r}"
        )
        assert cols["rating"]["is_nullable"] == "YES", (
            f"feedback.rating: expected nullable, got is_nullable={cols['rating']['is_nullable']!r}"
        )

        # text — text, nullable
        assert cols["text"]["data_type"] == "text", (
            f"feedback.text: expected text, got {cols['text']['data_type']!r}"
        )
        assert cols["text"]["is_nullable"] == "YES", (
            f"feedback.text: expected nullable, got is_nullable={cols['text']['is_nullable']!r}"
        )

        # metadata — jsonb, NOT NULL, DEFAULT '{}'::jsonb
        assert cols["metadata"]["data_type"] == "jsonb", (
            f"feedback.metadata: expected jsonb, got {cols['metadata']['data_type']!r}"
        )
        assert cols["metadata"]["is_nullable"] == "NO", (
            f"feedback.metadata: expected NOT NULL, "
            f"got is_nullable={cols['metadata']['is_nullable']!r}"
        )
        metadata_default = (cols["metadata"]["column_default"] or "").lower()
        # Postgres may normalize '{}'::jsonb to '{}'::jsonb or similar; just
        # check that the default contains '{}' and 'jsonb'.
        assert "{}" in metadata_default and "jsonb" in metadata_default, (
            f"feedback.metadata: expected DEFAULT containing '{{}}' and 'jsonb', "
            f"got {cols['metadata']['column_default']!r}"
        )

        # ── Indexes ───────────────────────────────────────────────────────────
        indexes = _index_names(conn, "corpus", "feedback")

        assert "feedback_entity_idx" in indexes, (
            f"feedback_entity_idx not found in pg_indexes. Indexes present: {sorted(indexes)}"
        )
        entity_idx_def = indexes["feedback_entity_idx"].lower()
        assert "entity_type" in entity_idx_def and "entity_id" in entity_idx_def, (
            f"feedback_entity_idx does not reference (entity_type, entity_id). "
            f"indexdef: {indexes['feedback_entity_idx']!r}"
        )

        assert "feedback_session_idx" in indexes, (
            f"feedback_session_idx not found in pg_indexes. Indexes present: {sorted(indexes)}"
        )
        session_idx_def = indexes["feedback_session_idx"].lower()
        assert "session_id" in session_idx_def, (
            f"feedback_session_idx does not reference session_id. "
            f"indexdef: {indexes['feedback_session_idx']!r}"
        )
