"""Alembic migration environment.

Dialect-aware configuration:
- SQLite  → render_as_batch=True (no DDL transactional ALTER support)
- Postgres → version_table_schema="corpus"

All operator-facing messages go through the ``alembic.runtime.migration``
logger (stderr).  No print() calls.
"""

from __future__ import annotations

import logging
import os

from alembic import context
from sqlalchemy import engine_from_config, pool

log = logging.getLogger("alembic.runtime.migration")


def _get_url() -> str:
    """Resolve the database URL from env var or alembic.ini config.

    Priority:
    1. ``DATABASE_URL`` environment variable (honoured by the legacy migrator)
    2. ``CORPUS_FORGE_DATABASE_URL`` environment variable
    3. ``sqlalchemy.url`` from the Alembic Config object
    """
    url = os.environ.get("DATABASE_URL") or os.environ.get("CORPUS_FORGE_DATABASE_URL")
    if url:
        return url
    cfg_url: str = context.config.get_main_option("sqlalchemy.url", default="")
    return cfg_url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine; calls to
    context.execute() emit the given string to the script output.
    """
    url = _get_url()

    # Determine whether this looks like a Postgres URL to set schema.
    is_postgres = url.startswith(("postgresql", "postgres"))

    configure_kwargs: dict = {
        "url": url,
        "target_metadata": None,
        "literal_binds": True,
        "dialect_opts": {"paramstyle": "named"},
        "version_table": "alembic_version",
    }
    if is_postgres:
        # Honor whatever schema migrate.py set in
        # ``config.attributes["version_table_schema"]`` (which mirrors
        # ``backend.schema``). Fall back to the canonical "corpus"
        # default for direct ``alembic upgrade`` invocations that don't
        # go through ``_build_alembic_config``.
        configure_kwargs["version_table_schema"] = context.config.attributes.get(
            "version_table_schema", "corpus"
        )

    with context.begin_transaction():
        context.configure(**configure_kwargs)
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (with an Engine/Connection)."""
    creator = context.config.attributes.get("creator")
    if creator is not None:
        # In-memory SQLite: use the backend's shared-cache factory so Alembic
        # operates on the same in-memory database as the SQLiteBackend instance.
        connectable = engine_from_config(
            {},
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
            creator=creator,
            # SQLite dialect is inferred from the creator connection.
            url="sqlite+pysqlite://",
        )
    else:
        connectable = engine_from_config(
            context.config.get_section(context.config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
            url=_get_url() or None,
        )

    with connectable.connect() as connection:
        dialect_name: str = connection.dialect.name

        if dialect_name == "sqlite":
            context.configure(
                connection=connection,
                render_as_batch=True,
                version_table="alembic_version",
                target_metadata=None,
            )
        else:
            # Postgres (and any other dialect) — use the schema
            # ``_build_alembic_config`` recorded (mirrors
            # ``backend.schema``). Fall back to "corpus" for direct
            # alembic invocations.
            context.configure(
                connection=connection,
                version_table="alembic_version",
                version_table_schema=context.config.attributes.get(
                    "version_table_schema", "corpus"
                ),
                target_metadata=None,
            )

        with context.begin_transaction():
            context.run_migrations()


# Module body: only execute when invoked via Alembic CLI / programmatic runner.
# Guarded so that a plain ``import corpus_forge.alembic.env`` (e.g. in tests)
# does not attempt to run migrations without a configured Alembic context.
try:
    _offline = context.is_offline_mode()
except Exception:
    # Not running under Alembic's migration framework — plain import, skip.
    pass
else:
    if _offline:
        run_migrations_offline()
    else:
        run_migrations_online()
