"""Schema migration utility for corpus-forge."""

import logging
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from alembic import command as alembic_command
from alembic.config import Config

import corpus_forge.alembic as _alembic_pkg

from ..backends.postgres import PostgresBackend

if TYPE_CHECKING:
    from ..backends.sqlite import SQLiteBackend

logger = logging.getLogger(__name__)


def get_migration_files(
    schema_dir: Path, dialect: Literal["postgres", "sqlite"] = "postgres"
) -> list[Path]:
    """Get numbered SQL migration files in order.

    For dialect='postgres' (default): reads from schema_dir (top-level Postgres files).
    For dialect='sqlite': reads from schema_dir/sqlite/ subdirectory.
    """
    if dialect == "postgres":
        search_dir = schema_dir
    elif dialect == "sqlite":
        search_dir = schema_dir / "sqlite"
    else:
        # Unknown dialect: return empty list (do not silently serve Postgres files).
        return []

    sql_files = list(search_dir.glob("[0-9]*.sql"))
    return sorted(sql_files, key=lambda p: int(p.stem.split("_")[0]))


def _apply_alembic(
    backend: "PostgresBackend | SQLiteBackend",
    dialect: Literal["postgres", "sqlite"],
) -> None:
    """Run Alembic upgrade to head against *backend*.

    Builds a programmatic Alembic Config so no alembic.ini on disk is
    required at the call site.  Derives the DB URL from backend attributes:
    - Postgres: backend.dsn  (postgresql[+driver]://…)
    - SQLite:   backend.path (file path, wrapped in sqlite:///…)
    """
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(_alembic_pkg.__file__).parent),
    )

    if dialect == "sqlite":
        config.set_main_option("sqlalchemy.url", f"sqlite:///{backend.path}")  # type: ignore[union-attr]
    else:
        # Postgres: ensure SQLAlchemy can use the psycopg v3 driver.
        # postgresql:// → postgresql+psycopg://  (if not already prefixed)
        dsn: str = backend.dsn  # type: ignore[union-attr]
        sa_url = re.sub(r"^postgresql(s?)://", r"postgresql+psycopg\1://", dsn)
        config.set_main_option("sqlalchemy.url", sa_url)
        # Signal env.py to place alembic_version inside the corpus schema.
        config.attributes["version_table_schema"] = "corpus"

    alembic_command.upgrade(config, "head")


def apply_migrations(
    backend: "PostgresBackend | SQLiteBackend",
    schema_dir: Path,
    dialect: Literal["postgres", "sqlite"] = "postgres",
) -> None:
    """Apply all pending migrations.

    Post-D07 routing logic:
    - If *schema_dir* contains numbered SQL files for the given dialect, the
      legacy file-based migrator runs (backward-compat for parity tests and
      any call-site that still passes a real schema directory with SQL files).
    - If no SQL files are found (empty directory, bogus path, or D-10 has
      deleted them), Alembic's upgrade-to-head path runs instead.

    The 'dialect' parameter controls which SQL files are read and whether
    Postgres-specific backfill passes are executed:
    - dialect='postgres' (default): reads top-level schema files and runs backfill.
    - dialect='sqlite': reads schema/sqlite/ files; skips the Postgres-only backfill.
    """
    migration_files = get_migration_files(schema_dir, dialect=dialect)

    if not migration_files:
        # No SQL files found — delegate to Alembic.
        logger.debug(
            "schema_dir parameter ignored after Alembic rewire (%s); "
            "no SQL files found, delegating to Alembic",
            schema_dir,
        )
        _apply_alembic(backend, dialect)
        return

    # --- legacy file-based migrator (kept for backward compat; deleted in D-10) ---
    applied: set[str] = set()

    for migration_file in migration_files:
        # stderr, not stdout: this function runs inside `corpus-forge mcp serve`
        # where stdout is the JSON-RPC channel and a stray "Applying migration:"
        # line breaks the framing for strict MCP clients (Claude Code).
        print(f"Applying migration: {migration_file.name}", file=sys.stderr)
        sql_content = migration_file.read_text()

        # SQLite + CREATE TRIGGER:  trigger bodies contain "BEGIN ... END;"
        # blocks; naive split-by-";" mangles them.  Use the backend's
        # executescript helper, which streams the full script through
        # sqlite3.Connection.executescript().  Postgres handles trigger
        # bodies natively via psycopg, so this is purely a SQLite quirk.
        if dialect == "sqlite" and "CREATE TRIGGER" in sql_content.upper():
            backend._executescript(sql_content)  # type: ignore[attr-defined]
            applied.add(migration_file.stem)
            continue

        # Split by semicolon and execute each statement.
        # Strip leading comment lines before checking for real SQL content, because a
        # statement that starts with "-- comment" may still contain actual SQL below.
        statements = [stmt.strip() for stmt in sql_content.split(";") if stmt.strip()]
        for statement in statements:
            non_comment_lines = [
                line for line in statement.splitlines() if not line.lstrip().startswith("--")
            ]
            sql_body = "\n".join(non_comment_lines).strip()
            if sql_body:
                backend._execute(statement)
        applied.add(migration_file.stem)

    # --- backfill passes (Postgres-only) ---
    if dialect == "postgres" and "002_chunk_content_hash" in applied:
        print("Running 002 backfill: content_hash for NULL rows", file=sys.stderr)
        backend._execute("""
            UPDATE corpus.chunks
            SET content_hash = encode(sha256(text::bytea), 'hex')
            WHERE content_hash IS NULL
        """)

    # --- 004_fts: SQLite-side backfill (Postgres GENERATED column auto-populates) ---
    # The chunks_fts virtual table is empty right after CREATE VIRTUAL TABLE,
    # so any rows that pre-date the 004 migration must be mirrored once.
    # The AFTER INSERT trigger handles new rows from this point onward.
    if dialect == "sqlite" and "004_fts" in applied:
        backfilled = backend.backfill_lexical_index()  # type: ignore[attr-defined]
        if backfilled:
            print(
                f"Running 004 backfill: mirrored {backfilled} chunks into chunks_fts",
                file=sys.stderr,
            )


def main() -> None:
    """Main entry point for migration command."""
    # In a real implementation, we'd get these from config
    # For now, we'll use defaults
    dsn = os.getenv("DATABASE_URL", "postgresql://memory@localhost/memory")
    schema = "corpus"

    backend = PostgresBackend(dsn=dsn, schema=schema)
    schema_dir = Path(__file__).parent

    apply_migrations(backend, schema_dir)
    print("Migrations applied successfully!", file=sys.stderr)


if __name__ == "__main__":
    main()
