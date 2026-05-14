"""D-02 — Alembic vs legacy-migrator schema parity: SQLite dialect.

Uses two temp .db files (pytest tmp_path fixture) so no Docker is needed.
Despite being in tests/integration/, the conftest hook skips the whole
integration namespace when Docker is absent — but SQLite itself has no Docker
dependency.  The pytestmark is kept to stay consistent with the namespace
convention; tests that need only sqlite3 (stdlib) would be fully runnable
without Docker if conftest is relaxed in future.

Parameterized on `head` so the test pin GROWS monotonically:
  - D-02 lands: head="0001_core"
  - D-03 lands: add  "0002_chunk_content_hash"
  - ... and so on.

Adding a new revision to the test is a one-line change:
  @pytest.mark.parametrize("head", ["0001_core", "0002_chunk_content_hash", ...], ...)

RED condition: alembic.util.exc.CommandError: Can't locate revision identified by '0001_core'
(no revision file exists yet at D-02 tester time).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_SCHEMA_DIR = _REPO_ROOT / "corpus_forge" / "schema"

# ---------------------------------------------------------------------------
# Alembic config builder
# ---------------------------------------------------------------------------


def _build_alembic_config(db_path: Path):
    """Return an Alembic Config pointing at *db_path* as a SQLite DB."""
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


# ---------------------------------------------------------------------------
# Schema dump helpers (sqlite_master)
# ---------------------------------------------------------------------------

# Tables that should be excluded from the Alembic side comparison.
_ALEMBIC_STRIP = frozenset({"alembic_version"})
# Tables that sqlite3 creates internally and should never appear in diffs.
_SQLITE_INTERNAL_STRIP = frozenset({"sqlite_sequence", "sqlite_stat1"})


def _normalize_sql(sql: str) -> str:
    """Normalize a CREATE statement for stable comparison.

    Rules applied:
    - Lowercase keywords (SQLite preserves original case).
    - Collapse multi-space runs to a single space.
    - Strip trailing whitespace on each line.
    - Trim leading/trailing whitespace from the whole string.
    """
    # Lowercase the whole statement — SQLite stores types/keywords verbatim.
    normalized = sql.lower()
    # Collapse multi-space runs (formatting noise).
    normalized = re.sub(r"[ \t]+", " ", normalized)
    # Strip trailing whitespace on each line.
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines())
    # Trim overall.
    return normalized.strip()


def _dump_sqlite_schema(
    db_path: Path,
    *,
    strip_tables: frozenset[str] = frozenset(),
) -> dict[str, list[str]]:
    """Return a normalized schema dump from *db_path*.

    Returns a dict:
      {
        "tables":  sorted list of normalized CREATE TABLE statements,
        "indexes": sorted list of normalized CREATE INDEX statements,
        "triggers": sorted list of normalized CREATE TRIGGER statements,
        "views":   sorted list of normalized CREATE VIEW statements,
      }

    Normalization:
    - Strip alembic_version and sqlite internal tables.
    - Lowercase keywords.
    - Collapse multi-space runs to one space.
    - Sort each list for stable ordering.
    """
    all_strip = strip_tables | _SQLITE_INTERNAL_STRIP

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            """
            SELECT type, name, sql
            FROM sqlite_master
            WHERE sql IS NOT NULL
            ORDER BY type, name
            """
        ).fetchall()
    finally:
        conn.close()

    tables: list[str] = []
    indexes: list[str] = []
    triggers: list[str] = []
    views: list[str] = []

    for obj_type, obj_name, sql in rows:
        # Determine associated table name for filtering.
        # For tables: obj_name is the table name.
        # For indexes/triggers on stripped tables: skip them.
        if obj_type == "table":
            if obj_name in all_strip:
                continue
            tables.append(_normalize_sql(sql))
        elif obj_type == "index":
            # sqlite_master stores index-owning table in tbl_name; we rely on
            # the SQL text containing the table name.  Skip alembic artifacts.
            # A simple heuristic: skip if the index name contains a stripped table name.
            if any(t in obj_name for t in all_strip):
                continue
            indexes.append(_normalize_sql(sql))
        elif obj_type == "trigger":
            if any(t in obj_name for t in all_strip):
                continue
            triggers.append(_normalize_sql(sql))
        elif obj_type == "view":
            if obj_name in all_strip:
                continue
            views.append(_normalize_sql(sql))

    return {
        "tables": sorted(tables),
        "indexes": sorted(indexes),
        "triggers": sorted(triggers),
        "views": sorted(views),
    }


# ---------------------------------------------------------------------------
# Legacy migrator helper
# ---------------------------------------------------------------------------


def _apply_legacy_sqlite(db_path: Path) -> None:
    """Apply the legacy migrate pipeline to *db_path*."""
    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.schema.migrate import apply_migrations

    backend = SQLiteBackend(path=str(db_path))
    # backend.migrate() calls apply_migrations internally.
    backend.migrate()
    # Also call apply_migrations explicitly to ensure all SQL files are run.
    apply_migrations(backend, _SCHEMA_DIR, dialect="sqlite")


# ---------------------------------------------------------------------------
# Alembic migrator helper
# ---------------------------------------------------------------------------


def _apply_alembic_sqlite(db_path: Path, head: str) -> None:
    """Apply Alembic up to *head* on the SQLite DB at *db_path*.

    Raises alembic.util.exc.CommandError if *head* is not found (RED state).
    """
    from alembic import command

    cfg = _build_alembic_config(db_path)
    command.upgrade(cfg, head)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("head", ["0001_core"], ids=["head=0001_core"])
def test_parity_sqlite(head: str, tmp_path: Path) -> None:
    """Legacy apply_migrations and Alembic upgrade(head) produce identical SQLite schemas.

    legacy_db  ← legacy apply_migrations (SQLiteBackend.migrate + apply_migrations)
    alembic_db ← alembic command.upgrade(config, head)

    Strip alembic_version (and sqlite_sequence) from the Alembic side before
    comparing.  Normalize CREATE statements to eliminate formatting noise.

    RED until D-02 coder lands 0001_core.py:
      CommandError: Can't locate revision identified by '0001_core'
    """
    legacy_db = tmp_path / "legacy.db"
    alembic_db = tmp_path / "alembic.db"

    # Apply legacy migrator.
    _apply_legacy_sqlite(legacy_db)

    # Apply Alembic — raises CommandError at RED time (no revision file yet).
    _apply_alembic_sqlite(alembic_db, head)

    # Dump + normalize both schemas.
    legacy_schema = _dump_sqlite_schema(legacy_db)
    alembic_schema = _dump_sqlite_schema(
        alembic_db,
        strip_tables=_ALEMBIC_STRIP,
    )

    assert legacy_schema == alembic_schema, (
        f"SQLite schema parity mismatch for head={head!r}.\n"
        f"Legacy tables:  {legacy_schema['tables']}\n"
        f"Alembic tables: {alembic_schema['tables']}\n"
        "Full diff: compare legacy_schema vs alembic_schema dicts."
    )
