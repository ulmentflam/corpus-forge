"""Add nullable ``model_benchmarks.cold_start_s`` (model load + warmup seconds).

Revision ID: 0021_benchmark_cold_start
Revises: 0020_shared_config
Create Date: 2026-06-10 15:10:00.000000

Stretch task of ``rfc-bench-embed-progress``.  ``bench embed`` already
times the model load + warmup as ``cold_start_s`` (PR #121) and surfaces
it in the Rich table and the ``--json`` payload, but the value was never
persisted — so ``models list`` could not compare cold-start cost across
lanes the way it compares ``chunks_per_s``.  This revision adds the
column so the telemetry round-trips:

* ``model_benchmarks.cold_start_s`` — ``NUMERIC`` (Postgres) / ``REAL``
  (SQLite), **nullable**.  Old rows (and the ``embed-run`` passive path,
  which never measures a discrete cold start) keep ``NULL``; ``bench``
  writes the measured float.

The column is purely additive — no existing column changes, no index
touched — so the migration is a single ``ADD COLUMN``.  Both dialect
paths are idempotent (re-runnable on every upgrade, like the rest of the
0015+ chain): Postgres uses ``ADD COLUMN IF NOT EXISTS``; SQLite (which
has no ``IF NOT EXISTS`` for ``ADD COLUMN``) introspects
``PRAGMA table_info`` first and skips the add when the column is already
present.

Like 0019, this revision carries a real ``downgrade()`` that drops the
column (telemetry-only data — losing a cold-start sample on rollback
loses nothing durable).  ``DROP COLUMN`` is guarded the same way:
``IF EXISTS`` on Postgres, ``PRAGMA``-checked on SQLite (≥ 3.35).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from alembic import op

revision: str = "0021_benchmark_cold_start"
down_revision: str | None = "0020_shared_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute(
            "ALTER TABLE corpus.model_benchmarks ADD COLUMN IF NOT EXISTS cold_start_s NUMERIC"
        )
        logger.info("0021_benchmark_cold_start: added cold_start_s (postgres)")
    elif dialect == "sqlite":
        if not _sqlite_has_cold_start(bind):
            op.execute("ALTER TABLE model_benchmarks ADD COLUMN cold_start_s REAL")
            logger.info("0021_benchmark_cold_start: added cold_start_s (sqlite)")
        else:
            logger.info("0021_benchmark_cold_start: cold_start_s already present (sqlite)")
    else:
        raise NotImplementedError(f"unsupported dialect: {dialect}")


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TABLE corpus.model_benchmarks DROP COLUMN IF EXISTS cold_start_s")
    elif dialect == "sqlite":
        if _sqlite_has_cold_start(bind):
            op.execute("ALTER TABLE model_benchmarks DROP COLUMN cold_start_s")
    else:
        raise NotImplementedError(f"unsupported dialect: {dialect}")
    logger.info("0021_benchmark_cold_start: dropped cold_start_s (%s)", dialect)


def _sqlite_has_cold_start(bind) -> bool:
    """Whether ``model_benchmarks`` already has the ``cold_start_s`` column.

    SQLite's ``ALTER TABLE ADD COLUMN`` has no ``IF NOT EXISTS`` clause, so
    the only portable idempotency guard is to introspect ``PRAGMA
    table_info`` and compare column names.
    """
    rows = bind.exec_driver_sql("PRAGMA table_info(model_benchmarks)").fetchall()
    return any(row[1] == "cold_start_s" for row in rows)
