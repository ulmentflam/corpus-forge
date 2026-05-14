"""Schema migration utility for corpus-forge."""

import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from alembic import command as alembic_command
from alembic.config import Config

import corpus_forge.alembic as _alembic_pkg

from ..backends.postgres import PostgresBackend

if TYPE_CHECKING:
    from ..backends.sqlite import SQLiteBackend

logger = logging.getLogger(__name__)


def _build_alembic_config(
    backend: "PostgresBackend | SQLiteBackend | None" = None,
    dialect: Literal["postgres", "sqlite"] = "postgres",
) -> Config:
    """Build a programmatic Alembic Config object.

    When *backend* is provided, derives the DB URL from backend attributes:
    - Postgres: backend.dsn  (postgresql[+driver]://…)
    - SQLite:   backend.path (file path, wrapped in sqlite:///…)

    When *backend* is None, the returned Config has no ``sqlalchemy.url``
    set (suitable for CLI meta-operations such as ``revision`` and
    ``history`` that read from alembic.ini / environment themselves, or
    where the caller will set the URL separately).
    """
    config = Config()
    config.set_main_option(
        "script_location",
        str(Path(_alembic_pkg.__file__).parent),
    )

    if backend is not None:
        if dialect == "sqlite":
            path_str = str(backend.path)  # type: ignore[union-attr]
            if path_str == ":memory:":
                # Named shared-cache URI — matches SQLiteBackend._get_connection() so
                # Alembic operates on the same in-memory database as the backend.
                # SQLAlchemy's pysqlite driver does not support uri=True; pass a
                # creator factory instead so sqlite3.connect() gets uri=True.
                mem_uri = f"file:corpus_forge_mem_{id(backend)}?mode=memory&cache=shared"
                config.attributes["creator"] = lambda: sqlite3.connect(mem_uri, uri=True)
                # Placeholder URL tells env.py the dialect; creator overrides the pool.
                config.set_main_option("sqlalchemy.url", "sqlite+pysqlite://")
            else:
                config.set_main_option("sqlalchemy.url", f"sqlite:///{path_str}")
        else:
            # Postgres: ensure SQLAlchemy can use the psycopg v3 driver.
            # postgresql:// → postgresql+psycopg://  (if not already prefixed)
            dsn: str = backend.dsn  # type: ignore[union-attr]
            sa_url = re.sub(r"^postgresql(s?)://", r"postgresql+psycopg\1://", dsn)
            config.set_main_option("sqlalchemy.url", sa_url)
            # Signal env.py to place alembic_version inside the corpus schema.
            config.attributes["version_table_schema"] = "corpus"

    return config


def _apply_alembic(
    backend: "PostgresBackend | SQLiteBackend",
    dialect: Literal["postgres", "sqlite"],
) -> None:
    """Run Alembic upgrade to head against *backend*.

    Builds a programmatic Alembic Config so no alembic.ini on disk is
    required at the call site.  Derives the DB URL from backend attributes:
    - Postgres: backend.dsn  (postgresql[+driver]://…)
    - SQLite:   backend.path (file path, wrapped in sqlite:///…)

    For in-memory SQLite backends (path=":memory:"), the backend's keeper
    connection is initialised before Alembic runs so that the shared-cache
    in-memory database persists across Alembic's own connection lifecycle.
    """
    if dialect == "sqlite" and str(getattr(backend, "path", "")) == ":memory:":
        # Force-open the keeper so the shared-cache URI exists before Alembic
        # tries to connect.  Without this, Alembic creates + destroys its own
        # connection to the URI, and the in-memory DB evaporates when no
        # connection holds it open.
        with backend._get_connection():  # type: ignore[union-attr]
            pass

    config = _build_alembic_config(backend, dialect)
    alembic_command.upgrade(config, "head")


def apply_migrations(
    backend: "PostgresBackend | SQLiteBackend",
    schema_dir: Path,
    dialect: Literal["postgres", "sqlite"] = "postgres",
) -> None:
    """Apply all pending migrations via Alembic upgrade-to-head.

    The *schema_dir* parameter is preserved for call-site back-compat but is
    ignored — Alembic revisions in corpus_forge/alembic/versions/ are the
    sole source of truth after D-10.

    The 'dialect' parameter controls whether Postgres-specific or
    SQLite-specific Alembic ops are executed (env.py branches on dialect name
    via the SQLAlchemy connection dialect).
    """
    logger.debug(
        "apply_migrations: schema_dir parameter ignored (Alembic-only path); schema_dir=%s",
        schema_dir,
    )
    _apply_alembic(backend, dialect)


def main() -> None:
    """Main entry point for migration command."""
    # In a real implementation, we'd get these from config
    # For now, we'll use defaults
    dsn = os.getenv("DATABASE_URL", "postgresql://memory@localhost/memory")
    schema = "corpus"

    backend = PostgresBackend(dsn=dsn, schema=schema)
    schema_dir = Path(__file__).parent

    apply_migrations(backend, schema_dir)
    logger.info("Migrations applied successfully!")


if __name__ == "__main__":
    main()
