"""Add five nullable provenance columns to corpus.chunks.

Revision ID: 0016_chunk_provenance
Revises: 0015_halfvec_hnsw_index
Create Date: 2026-05-22 12:35:00.000000

First task of RFC ``rfc-source-provenance-git-and-lines`` (P0). The
RFC turns chunks into self-locating units: every chunk says where it
came from on disk (``file_path`` + ``line_start``/``line_end``) and
at which commit (``git_commit`` + ``git_branch``). That unblocks
self-distillation feedback (attach signal to file-at-commit, not the
chunk excerpt) and live source navigation
(``MCP get_source_file_context`` reads these columns).

All five columns are nullable. Existing rows survive untouched —
their provenance fields stay NULL until the source/chunker
re-emits them on the next ingest pass. Subsequent RFC tasks
(``FilesystemSource`` wiring via the new ``git_context()`` helper
from PR #34, chunker line-number capture, backend write paths,
MCP tool) all read or write through these columns, but none of
them is a hard upgrade gate for this migration — running the
migration on a deployment that hasn't yet shipped the
write-path changes is a no-op enrichment.

Both Postgres and SQLite paths add the columns. Postgres uses
``ADD COLUMN IF NOT EXISTS`` so the migration is fully
idempotent. SQLite has no idempotent ``ADD COLUMN``, so the
SQLite path uses a ``PRAGMA table_info`` probe and skips the
column if already present — same end state, same forward-only
contract.

Forward-only per project convention.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from alembic import op

revision: str = "0016_chunk_provenance"
down_revision: str | None = "0015_halfvec_hnsw_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# (column_name, postgres_type, sqlite_type) — sqlite has no TEXT vs
# VARCHAR distinction worth making, but we keep the tuple shape
# explicit so adding a sixth column is mechanical.
_PROVENANCE_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("file_path", "TEXT", "TEXT"),
    ("line_start", "INTEGER", "INTEGER"),
    ("line_end", "INTEGER", "INTEGER"),
    ("git_commit", "TEXT", "TEXT"),
    ("git_branch", "TEXT", "TEXT"),
)


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
    # Forward-only — matches the rest of the chain (0008+).
    pass


def _upgrade_postgres() -> None:
    """Postgres path — ``ADD COLUMN IF NOT EXISTS`` is natively idempotent."""
    for col, pg_type, _sqlite_type in _PROVENANCE_COLUMNS:
        op.execute(f"ALTER TABLE corpus.chunks ADD COLUMN IF NOT EXISTS {col} {pg_type} NULL")
    logger.info(
        "0016_chunk_provenance: added %d provenance columns to corpus.chunks",
        len(_PROVENANCE_COLUMNS),
    )


def _upgrade_sqlite() -> None:
    """SQLite path — manual existence probe via ``PRAGMA table_info``.

    SQLite has no ``ADD COLUMN IF NOT EXISTS`` and ``CREATE TABLE
    IF NOT EXISTS`` only helps for whole tables. Re-running an
    ``ALTER TABLE chunks ADD COLUMN`` for an already-present column
    raises ``duplicate column name``. Probe first, add only if
    missing — same idempotence contract as the Postgres path.
    """
    bind = op.get_bind()
    existing_columns = {
        row[1]  # PRAGMA table_info: (cid, name, type, notnull, dflt_value, pk)
        for row in bind.exec_driver_sql("PRAGMA table_info(chunks)").fetchall()
    }
    added = 0
    for col, _pg_type, sqlite_type in _PROVENANCE_COLUMNS:
        if col in existing_columns:
            continue
        op.execute(f"ALTER TABLE chunks ADD COLUMN {col} {sqlite_type}")
        added += 1
    logger.info(
        "0016_chunk_provenance: added %d/%d provenance columns to chunks",
        added,
        len(_PROVENANCE_COLUMNS),
    )
