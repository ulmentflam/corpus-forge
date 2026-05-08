"""Schema migration utility for corpus-forge."""

import os
from pathlib import Path

from ..backends.postgres import PostgresBackend


def get_migration_files(schema_dir: Path) -> list[Path]:
    """Get numbered SQL migration files in order."""
    sql_files = list(schema_dir.glob("[0-9]*.sql"))
    return sorted(sql_files, key=lambda p: int(p.stem.split("_")[0]))


def apply_migrations(backend: PostgresBackend, schema_dir: Path) -> None:
    """Apply all pending migrations."""
    migration_files = get_migration_files(schema_dir)
    applied: set[str] = set()

    for migration_file in migration_files:
        print(f"Applying migration: {migration_file.name}")
        sql_content = migration_file.read_text()

        # Split by semicolon and execute each statement
        statements = [stmt.strip() for stmt in sql_content.split(";") if stmt.strip()]
        for statement in statements:
            if statement and not statement.startswith("--"):
                backend._execute(statement)
        applied.add(migration_file.stem)

    # --- backfill passes ---
    if "002_chunk_content_hash" in applied:
        print("Running 002 backfill: content_hash for NULL rows")
        backend._execute("""
            UPDATE corpus.chunks
            SET content_hash = encode(sha256(text::bytea), 'hex')
            WHERE content_hash IS NULL
        """)


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
