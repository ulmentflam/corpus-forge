"""Add nullable model_benchmarks.cold_start_s (bench cold-start persistence).

Revision ID: 0021_benchmark_cold_start
Revises: 0020_shared_config
Create Date: 2026-06-13 04:30:00.000000

Stretch task of ``rfc-bench-embed-progress``.  ``bench embed`` already
computes a ``cold_start_s`` (model load + warmup seconds) and surfaces it
in the ``--json`` payload, but never persisted it.  This revision adds a
nullable ``cold_start_s`` column to ``model_benchmarks`` so the figure is
durable, queryable, and surfaced by ``models list`` alongside
``chunks_per_s`` — letting an operator see the fixed spin-up cost a warm
run avoids.

Additive + nullable, so old rows simply read ``NULL``.  The upgrade is
idempotent on both dialects: Postgres uses ``ADD COLUMN IF NOT EXISTS``;
SQLite (no such clause) probes ``PRAGMA table_info`` before the
``ALTER TABLE``.  The downgrade drops the column where supported
(``DROP COLUMN`` landed in SQLite 3.35); a too-old SQLite degrades to a
logged no-op rather than failing the rollback.
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
        _upgrade_postgres()
    elif dialect == "sqlite":
        _upgrade_sqlite()
    else:
        raise NotImplementedError(f"unsupported dialect: {dialect}")


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("ALTER TABLE corpus.model_benchmarks DROP COLUMN IF EXISTS cold_start_s")
        logger.info("0021_benchmark_cold_start: dropped cold_start_s (postgres)")
    elif dialect == "sqlite":
        _downgrade_sqlite()
    else:
        raise NotImplementedError(f"unsupported dialect: {dialect}")


def _upgrade_postgres() -> None:
    """Postgres path — ADD COLUMN IF NOT EXISTS is fully idempotent."""
    op.execute("ALTER TABLE corpus.model_benchmarks ADD COLUMN IF NOT EXISTS cold_start_s NUMERIC")
    logger.info("0021_benchmark_cold_start: added cold_start_s (postgres)")


def _upgrade_sqlite() -> None:
    """SQLite path — PRAGMA-probe then ADD COLUMN (idempotent re-run safe)."""
    if not _sqlite_has_column("model_benchmarks", "cold_start_s"):
        op.execute("ALTER TABLE model_benchmarks ADD COLUMN cold_start_s REAL")
    logger.info("0021_benchmark_cold_start: added cold_start_s (sqlite)")


def _downgrade_sqlite() -> None:
    """SQLite path — DROP COLUMN where supported (>= 3.35), else no-op."""
    if not _sqlite_has_column("model_benchmarks", "cold_start_s"):
        return
    try:
        op.execute("ALTER TABLE model_benchmarks DROP COLUMN cold_start_s")
        logger.info("0021_benchmark_cold_start: dropped cold_start_s (sqlite)")
    except Exception as exc:  # SQLite < 3.35 has no DROP COLUMN
        logger.warning(
            "0021_benchmark_cold_start: SQLite DROP COLUMN unsupported (%r); "
            "leaving cold_start_s in place",
            exc,
        )


def _sqlite_has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    rows = bind.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)
