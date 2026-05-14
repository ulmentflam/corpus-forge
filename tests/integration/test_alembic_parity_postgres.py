"""D-02 — Alembic vs legacy-migrator schema parity: Postgres dialect.

Uses one testcontainers Postgres container (from the session-scoped
``postgres_container`` fixture in conftest.py).  Both migrators target the
hardcoded ``corpus`` schema — the legacy SQL files and the Alembic revision
both create/use ``corpus.*`` objects.  We apply each migrator sequentially
(drop schema → apply → dump → drop schema → apply other → dump) then compare
the normalized dumps for structural equality.

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
    reason="testcontainers not installed — Postgres parity test skipped",
)

# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"
_SCHEMA_DIR = _REPO_ROOT / "corpus_forge" / "schema"

# ---------------------------------------------------------------------------
# Schema dump helpers (information_schema + pg_* catalogue queries)
# ---------------------------------------------------------------------------


def _normalize_pg_schema(
    dsn: str,
    schema_name: str,
    *,
    strip_tables: frozenset[str] = frozenset({"alembic_version"}),
) -> dict[str, Any]:
    """Return a normalized representation of a Postgres schema.

    Queries:
    - information_schema.tables   → table list
    - information_schema.columns  → column definitions (type, nullability, default)
    - information_schema.table_constraints + key_column_usage → PK/FK/UNIQUE
    - pg_indexes                  → index definitions

    Normalization rules:
    - Sort everything by table_name, column_name, etc. for stable comparison.
    - Strip any table in strip_tables (e.g. alembic_version).
    - Remove the schema name from qualified identifiers.
    - Normalize nextval() defaults (sequence names may differ).
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # --- Tables ---
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """,
            (schema_name,),
        )
        tables = [r[0] for r in cur.fetchall() if r[0] not in strip_tables]

        # --- Columns ---
        cur.execute(
            """
            SELECT table_name,
                   column_name,
                   ordinal_position,
                   column_default,
                   is_nullable,
                   data_type,
                   character_maximum_length,
                   udt_name
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position
            """,
            (schema_name,),
        )
        raw_cols = cur.fetchall()

        # --- Constraints ---
        cur.execute(
            """
            SELECT tc.table_name,
                   tc.constraint_name,
                   tc.constraint_type,
                   kcu.column_name,
                   ccu.table_schema AS ref_schema,
                   ccu.table_name   AS ref_table,
                   ccu.column_name  AS ref_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema    = kcu.table_schema
            LEFT JOIN information_schema.constraint_column_usage ccu
              ON tc.constraint_name = ccu.constraint_name
             AND tc.table_schema    = ccu.table_schema
            WHERE tc.table_schema = %s
              AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')
            ORDER BY tc.table_name, tc.constraint_type, kcu.column_name
            """,
            (schema_name,),
        )
        raw_constraints = cur.fetchall()

        # --- Indexes ---
        cur.execute(
            """
            SELECT tablename, indexname, indexdef
            FROM pg_indexes
            WHERE schemaname = %s
            ORDER BY tablename, indexname
            """,
            (schema_name,),
        )
        raw_indexes = cur.fetchall()

    # ----- Normalize columns -----
    columns: dict[str, list[dict]] = {}
    for table_name, col_name, pos, default, nullable, dtype, max_len, udt in raw_cols:
        if table_name in strip_tables:
            continue
        # Strip sequence-based default values (nextval()) to avoid
        # sequence name drift between runs.
        norm_default: str | None
        if default is not None and default.startswith("nextval("):
            norm_default = "nextval(<sequence>)"
        else:
            norm_default = default
        columns.setdefault(table_name, []).append(
            {
                "col": col_name,
                "pos": pos,
                "default": norm_default,
                "nullable": nullable,
                "dtype": dtype,
                "max_len": max_len,
                "udt": udt,
            }
        )

    # ----- Normalize constraints -----
    constraints: dict[str, list[dict]] = {}
    for (
        tbl,
        cname,
        ctype,
        col,
        ref_schema,
        ref_table,
        ref_col,
    ) in raw_constraints:
        if tbl in strip_tables:
            continue
        # Strip schema name from constraint names to normalize across runs.
        norm_cname = re.sub(r"\b" + re.escape(schema_name) + r"\b", "<schema>", cname)
        norm_ref_schema = "<schema>" if ref_schema == schema_name else ref_schema
        constraints.setdefault(tbl, []).append(
            {
                "cname": norm_cname,
                "ctype": ctype,
                "col": col,
                "ref_schema": norm_ref_schema,
                "ref_table": ref_table,
                "ref_col": ref_col,
            }
        )
    for lst in constraints.values():
        lst.sort(key=lambda d: (d["ctype"], d["col"], d.get("ref_col") or ""))

    # ----- Normalize indexes -----
    indexes: dict[str, list[str]] = {}
    for tbl, _iname, idef in raw_indexes:
        if tbl in strip_tables:
            continue
        # Strip schema name from the index definition string.
        norm_idef = idef.replace(f"{schema_name}.", "<schema>.")
        indexes.setdefault(tbl, []).append(norm_idef)
    for lst in indexes.values():
        lst.sort()

    return {
        "tables": sorted(tables),
        "columns": dict(sorted(columns.items())),
        "constraints": dict(sorted(constraints.items())),
        "indexes": dict(sorted(indexes.items())),
    }


# ---------------------------------------------------------------------------
# Migrator helpers
# ---------------------------------------------------------------------------


def _reset_corpus_schema(dsn: str) -> None:
    """Drop and let each migrator recreate the corpus schema."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS corpus CASCADE")


def _apply_legacy(dsn: str) -> None:
    """Apply the legacy migrator into the 'corpus' schema.

    backend.migrate() calls apply_migrations internally.  We call
    apply_migrations a second time to ensure idempotency is exercised
    (also matching how existing integration tests use it).
    """
    from corpus_forge.backends.postgres import PostgresBackend
    from corpus_forge.schema.migrate import apply_migrations

    backend = PostgresBackend(dsn=dsn, schema="corpus")
    backend.migrate()
    apply_migrations(backend, _SCHEMA_DIR, dialect="postgres")


def _apply_alembic(dsn: str, head: str) -> None:
    """Apply Alembic up to *head* against the 'corpus' schema.

    Converts the psycopg DSN to a SQLAlchemy-style URL.
    Pre-creates the 'corpus' schema so env.py can place alembic_version there
    (the D-02 revision itself will also emit CREATE SCHEMA IF NOT EXISTS corpus).
    Raises alembic.util.exc.CommandError when *head* is not found (RED state).
    """
    from alembic import command
    from alembic.config import Config

    # Pre-create corpus schema so alembic_version can be placed there.
    # The D-02 revision will also do CREATE SCHEMA IF NOT EXISTS corpus; this
    # pre-creation is a no-op once the revision exists and runs first.
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE SCHEMA IF NOT EXISTS corpus")

    # Convert postgresql:// → postgresql+psycopg:// (psycopg v3) for SQLAlchemy.
    sa_dsn = re.sub(r"^postgresql(s?)://", r"postgresql+psycopg\1://", dsn)

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )
    cfg.set_main_option("sqlalchemy.url", sa_dsn)
    command.upgrade(cfg, head)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@_skip_no_tc
@pytest.mark.parametrize("head", ["0001_core"], ids=["head=0001_core"])
def test_parity_postgres(head: str, postgres_container) -> None:  # type: ignore[return]
    """Legacy apply_migrations and Alembic upgrade(head) produce byte-equal schemas.

    Both migrators target the hardcoded 'corpus' schema.  We apply each one
    sequentially — drop corpus → apply legacy → dump → drop corpus → apply
    alembic → dump — then compare normalized dumps.

    RED until D-02 coder lands 0001_core.py:
      alembic.util.exc.CommandError: Can't locate revision identified by '0001_core'
    """
    c = postgres_container
    dsn = (
        f"postgresql://{c.username}:{c.password}"
        f"@{c.get_container_host_ip()}:{c.get_exposed_port(5432)}"
        f"/{c.dbname}"
    )

    # ── Legacy pass ─────────────────────────────────────────────────────────
    _reset_corpus_schema(dsn)
    _apply_legacy(dsn)
    legacy_schema = _normalize_pg_schema(dsn, "corpus", strip_tables=frozenset())

    # ── Alembic pass (raises CommandError at RED time) ───────────────────────
    _reset_corpus_schema(dsn)
    _apply_alembic(dsn, head)  # <-- RED: No revision '0001_core' exists yet
    alembic_schema = _normalize_pg_schema(
        dsn,
        "corpus",
        strip_tables=frozenset({"alembic_version"}),
    )

    assert legacy_schema == alembic_schema, (
        f"Postgres schema parity mismatch for head={head!r}.\n"
        f"Tables only in legacy:  {set(legacy_schema['tables']) - set(alembic_schema['tables'])}\n"
        f"Tables only in alembic: {set(alembic_schema['tables']) - set(legacy_schema['tables'])}\n"
        "Run with -s to see full diff."
    )
