"""D-07 RED — apply_migrations dispatches to Alembic.

These three tests assert that after the D-07 rewire:
- apply_migrations builds an Alembic Config from the backend's DSN (ignoring
  the schema_dir parameter entirely) and calls command.upgrade("head").
- The corpus.alembic_version table (PG) or alembic_version table (SQLite) is
  populated with version_num == "0007_chat_templates" (the current head revision).
- All five revisions ran: corpus.documents, corpus.chunks, corpus.document_revisions
  are present in the resulting schema.

At RED time all three tests FAIL with AssertionError because the legacy
apply_migrations reads schema/*.sql files directly and never touches
alembic_version.

Phase H / H-01 bump: version_num assertions updated 0007_chat_templates →
0010_document_label_confidence to reflect the new alembic head.
"""

from __future__ import annotations

import importlib
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _expected_head_revision() -> str:
    """Resolve the current Alembic head revision from the script directory.

    Hardcoding the head in test assertions (e.g. ``"0014_sdft_demonstrations"``)
    means every new revision breaks these tests. Reading the head off the
    same ScriptDirectory ``apply_migrations`` uses is the canonical
    answer — they'll always agree, by construction.
    """
    from alembic.script import ScriptDirectory

    from corpus_forge.schema.migrate import _build_alembic_config

    return ScriptDirectory.from_config(_build_alembic_config()).get_current_head() or ""


# ---------------------------------------------------------------------------
# Availability guards
# ---------------------------------------------------------------------------

_TESTCONTAINERS_AVAILABLE = importlib.util.find_spec("testcontainers") is not None

_skip_no_tc = pytest.mark.skipif(
    not _TESTCONTAINERS_AVAILABLE,
    reason="testcontainers not installed — Postgres apply_migrations test skipped",
)

# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_SCHEMA_DIR = _REPO_ROOT / "corpus_forge" / "schema"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pg_dsn(postgres_container) -> str:  # type: ignore[return]
    """Build a bare postgresql:// DSN from the shared container fixture."""
    c = postgres_container
    return (
        f"postgresql://{c.username}:{c.password}"
        f"@{c.get_container_host_ip()}:{c.get_exposed_port(5432)}"
        f"/{c.dbname}"
    )


def _reset_corpus_schema(dsn: str) -> None:
    """Drop and recreate the corpus schema so tests start from a clean slate."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS corpus CASCADE")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE SCHEMA IF NOT EXISTS corpus")


# ---------------------------------------------------------------------------
# Test 1 — Postgres: alembic_version populated with head=0007_chat_templates
# ---------------------------------------------------------------------------


@_skip_no_tc
def test_apply_migrations_creates_alembic_version_table_pg(
    postgres_container,
) -> None:
    """apply_migrations(backend, schema_dir=bogus_path, dialect='postgres') must:
    - Ignore schema_dir entirely (bogus path proves it).
    - Write corpus.alembic_version with version_num == '0010_document_label_confidence'.

    FAILS at RED: legacy apply_migrations reads schema_dir/*.sql and never
    touches alembic_version at all.
    """
    import psycopg

    from corpus_forge.backends.postgres import PostgresBackend
    from corpus_forge.schema.migrate import apply_migrations

    dsn = _pg_dsn(postgres_container)
    _reset_corpus_schema(dsn)

    backend = PostgresBackend(dsn=dsn, schema="corpus")

    # Pass a path that definitely has no .sql files — proves schema_dir is ignored.
    bogus_path = Path("/tmp/empty_d07_test_does_not_exist")

    apply_migrations(backend, schema_dir=bogus_path, dialect="postgres")

    # Assert alembic_version table exists in corpus schema and has head row.
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # First check that the table itself exists (clean AssertionError if not).
        cur.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'corpus'
              AND table_name = 'alembic_version'
            """
        )
        table_count = cur.fetchone()[0]
        assert table_count == 1, (
            "corpus.alembic_version table does not exist after apply_migrations. "
            "Legacy apply_migrations reads schema_dir/*.sql and never writes "
            "alembic_version — this is the RED state."
        )

        cur.execute("SELECT version_num FROM corpus.alembic_version")
        rows = cur.fetchall()

    assert len(rows) == 1, f"Expected exactly 1 row in corpus.alembic_version, got {len(rows)}. "
    version_num = rows[0][0]
    expected_head = _expected_head_revision()
    assert version_num == expected_head, (
        f"Expected version_num={expected_head!r} (current alembic head), "
        f"got {version_num!r}. The rewired apply_migrations must call "
        "command.upgrade('head')."
    )


# ---------------------------------------------------------------------------
# Test 2 — SQLite: alembic_version populated with head=0010_document_label_confidence
# ---------------------------------------------------------------------------


def test_apply_migrations_creates_alembic_version_table_sqlite(
    tmp_path: Path,
) -> None:
    """apply_migrations(backend, schema_dir=bogus_path, dialect='sqlite') must:
    - Ignore schema_dir entirely.
    - Write the alembic_version table with version_num == '0010_document_label_confidence'.

    FAILS at RED: head is now '0010_document_label_confidence' but revision file does not
    exist yet — CommandError from alembic.
    """
    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.schema.migrate import apply_migrations

    db_path = tmp_path / "test.db"
    backend = SQLiteBackend(path=str(db_path))

    # Pass a path that definitely has no sqlite/ subdir — proves schema_dir is ignored.
    bogus_path = Path("/tmp/empty_d07_sqlite_test_does_not_exist")

    apply_migrations(backend, schema_dir=bogus_path, dialect="sqlite")

    # Assert alembic_version table exists and has head row.
    conn = sqlite3.connect(str(db_path))
    try:
        # Check table exists
        table_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchall()
        assert len(table_rows) == 1, (
            "alembic_version table does not exist in the SQLite database. "
            "Legacy apply_migrations reads schema_dir/sqlite/*.sql — this is the RED state."
        )

        rows = conn.execute("SELECT version_num FROM alembic_version").fetchall()
    finally:
        conn.close()

    assert len(rows) == 1, f"Expected exactly 1 row in alembic_version, got {len(rows)}."
    version_num = rows[0][0]
    expected_head = _expected_head_revision()
    assert version_num == expected_head, (
        f"Expected version_num={expected_head!r} (current alembic head), "
        f"got {version_num!r}. The rewired apply_migrations must call "
        "command.upgrade('head')."
    )


# ---------------------------------------------------------------------------
# Test 3 — Postgres: full 5-revision schema present after apply_migrations
# ---------------------------------------------------------------------------


@_skip_no_tc
def test_apply_migrations_produces_full_schema_pg(
    postgres_container,
) -> None:
    """apply_migrations on a fresh PG container produces the full 5-revision schema.

    Asserts that corpus.documents, corpus.chunks, and corpus.document_revisions
    all exist — proving Alembic ran through all 5 revisions, not just the first.

    FAILS at RED: bogus schema_dir means no SQL files are loaded, so no tables
    are created (legacy migrator returns after zero iterations).
    """
    import psycopg

    from corpus_forge.backends.postgres import PostgresBackend
    from corpus_forge.schema.migrate import apply_migrations

    dsn = _pg_dsn(postgres_container)
    _reset_corpus_schema(dsn)

    backend = PostgresBackend(dsn=dsn, schema="corpus")

    # Bogus path — schema_dir must be irrelevant after D-07 rewire.
    bogus_path = Path("/tmp/empty_d07_full_schema_test_does_not_exist")

    apply_migrations(backend, schema_dir=bogus_path, dialect="postgres")

    required_tables = {"documents", "chunks", "document_revisions"}

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'corpus'
              AND table_type = 'BASE TABLE'
              AND table_name != 'alembic_version'
            """
        )
        present_tables = {row[0] for row in cur.fetchall()}

    missing = required_tables - present_tables
    assert not missing, (
        f"Tables missing from corpus schema after apply_migrations: {missing}. "
        f"Tables present: {sorted(present_tables)}. "
        "Legacy apply_migrations with a bogus schema_dir finds no SQL files — "
        "this is the RED state."
    )
