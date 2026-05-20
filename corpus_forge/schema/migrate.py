"""Schema migration utility for corpus-forge."""

import logging
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
            # Signal env.py to place alembic_version in the SAME schema
            # the user configured (default "corpus"). Reading from
            # ``backend.schema`` keeps this in lockstep with the
            # pre-create step in ``_apply_alembic`` — otherwise a
            # non-default schema would silently land the version table
            # elsewhere.
            config.attributes["version_table_schema"] = backend.schema  # type: ignore[union-attr]

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

    if dialect == "postgres":
        # Pre-create the target schema BEFORE Alembic runs.
        #
        # ``_build_alembic_config`` tells the env to put ``alembic_version``
        # in ``backend.schema`` (default ``"corpus"``). On first-run,
        # Alembic creates that version table the moment it connects —
        # BEFORE any migration script runs. Migration 0001 itself does
        # ``CREATE SCHEMA IF NOT EXISTS corpus``, but that's too late
        # (Alembic already errored out with
        # ``psycopg.errors.InvalidSchemaName: schema "corpus" does not
        # exist`` while creating its version table).
        #
        # Idempotent: ``CREATE SCHEMA IF NOT EXISTS`` is a no-op when
        # the schema already exists.
        #
        # Identifier composition: use ``psycopg.sql.Identifier`` so a
        # schema name with special characters (or hypothetical SQL
        # injection from a malicious config) is properly quoted —
        # never interpolate identifiers via f-string.
        import psycopg  # noqa: PLC0415 — avoid load-time cost when sqlite-only
        from psycopg import sql as psycopg_sql  # noqa: PLC0415

        target_schema: str = backend.schema  # type: ignore[union-attr]
        dsn: str = backend.dsn  # type: ignore[union-attr]
        # Strip the SQLAlchemy driver prefix if present — psycopg.connect
        # takes a raw libpq DSN.
        libpq_dsn = re.sub(r"^postgresql\+psycopg(s?)://", r"postgresql\1://", dsn)
        create_schema = psycopg_sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
            psycopg_sql.Identifier(target_schema)
        )
        with psycopg.connect(libpq_dsn, autocommit=True) as _conn:
            _conn.execute(create_schema)
        logger.debug("pre-created Postgres schema %r before Alembic upgrade", target_schema)

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
    """Main entry point for ``corpus-forge migrate``.

    Loads the user's ``Config`` and dispatches to the appropriate
    backend (Postgres or SQLite) based on ``config.backend.kind``. The
    Alembic env reads its DB URL out of the ``Config`` we hand it
    through ``_build_alembic_config``; we do NOT consult
    ``DATABASE_URL`` env var here (it's still honoured at the Alembic
    layer in ``alembic/env.py:_get_url`` as a CI override).

    Earlier revisions defaulted to ``postgresql://memory@localhost/memory``
    when ``DATABASE_URL`` was unset — a dev-machine leftover that silently
    redirected ``corpus-forge migrate`` away from the user's configured
    backend. Fixed in PR "install + migrate first-run UX".
    """
    from corpus_forge.config import Config  # noqa: PLC0415 — avoid import cycle at module load

    config = Config.load()
    schema_dir = Path(__file__).parent

    # NB: ``backend`` is intentionally un-annotated below because
    # ``SQLiteBackend`` is only imported in ``TYPE_CHECKING`` (lazy to
    # avoid pulling sqlite-vec into the postgres-only fast path). A
    # quoted annotation here would trip UP037; an unquoted one would
    # NameError on the sqlite branch.
    if config.backend.kind == "postgres":
        backend = PostgresBackend(dsn=config.backend.dsn, schema=config.backend.schema)
        dialect: Literal["postgres", "sqlite"] = "postgres"
    elif config.backend.kind == "sqlite":
        from ..backends.sqlite import SQLiteBackend as _SQLiteBackend  # noqa: PLC0415

        backend = _SQLiteBackend(path=config.backend.dsn, schema=config.backend.schema)
        dialect = "sqlite"
    else:  # pragma: no cover — Pydantic Literal narrows this away
        raise ValueError(f"unsupported backend kind: {config.backend.kind!r}")

    apply_migrations(backend, schema_dir, dialect=dialect)
    logger.info("Migrations applied successfully against backend.kind=%s", config.backend.kind)


if __name__ == "__main__":
    main()
