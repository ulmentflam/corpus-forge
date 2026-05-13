"""Schema migration utility for corpus-forge."""

import os
from pathlib import Path
from typing import Literal

from ..backends.postgres import PostgresBackend


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


def apply_migrations(
    backend: PostgresBackend,
    schema_dir: Path,
    dialect: Literal["postgres", "sqlite"] = "postgres",
) -> None:
    """Apply all pending migrations.

    The 'dialect' parameter controls which SQL files are read and whether
    Postgres-specific backfill passes are executed:
    - dialect='postgres' (default): reads top-level schema files and runs backfill.
    - dialect='sqlite': reads schema/sqlite/ files; skips the Postgres-only backfill.
    """
    migration_files = get_migration_files(schema_dir, dialect=dialect)
    applied: set[str] = set()

    for migration_file in migration_files:
        print(f"Applying migration: {migration_file.name}")
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
        print("Running 002 backfill: content_hash for NULL rows")
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
            print(f"Running 004 backfill: mirrored {backfilled} chunks into chunks_fts")


def main() -> None:
    """Main entry point for migration command."""
    # In a real implementation, we'd get these from config
    # For now, we'll use defaults
    dsn = os.getenv("DATABASE_URL", "postgresql://memory@localhost/memory")
    schema = "corpus"

    backend = PostgresBackend(dsn=dsn, schema=schema)
    schema_dir = Path(__file__).parent

    apply_migrations(backend, schema_dir)
    print("Migrations applied successfully!")


if __name__ == "__main__":
    main()
