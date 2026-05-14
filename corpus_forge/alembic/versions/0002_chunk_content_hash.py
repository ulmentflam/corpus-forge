"""chunk_content_hash

Revision ID: 0002_chunk_content_hash
Revises: 0001_core
Create Date: 2026-05-13 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_chunk_content_hash"
down_revision: str | None = "0001_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    pass  # Phase D is forward-only


def _upgrade_postgres() -> None:
    # DDL — match schema/002_chunk_content_hash.sql exactly.
    op.execute("ALTER TABLE corpus.chunks ADD COLUMN IF NOT EXISTS content_hash TEXT")
    op.execute("CREATE INDEX IF NOT EXISTS chunks_content_hash_idx ON corpus.chunks(content_hash)")
    # Data migration — same backfill statement the legacy migrator runs.
    op.execute(
        "UPDATE corpus.chunks "
        "SET content_hash = encode(sha256(text::bytea), 'hex') "
        "WHERE content_hash IS NULL"
    )


def _upgrade_sqlite() -> None:
    # DDL — match schema/sqlite/002_chunk_content_hash.sql.
    # SQLAlchemy's SQLite dialect does not support IF NOT EXISTS on ALTER TABLE
    # ADD COLUMN (even though SQLite >= 3.37 does). Alembic tracks the revision
    # so this runs exactly once — idempotency guard is not needed here.
    op.execute("ALTER TABLE chunks ADD COLUMN content_hash TEXT")
    op.execute("CREATE INDEX IF NOT EXISTS chunks_content_hash_idx ON chunks(content_hash)")
    # NO data-migration backfill on SQLite — the legacy migrator skips it
    # for sqlite dialect (see corpus_forge/schema/migrate.py:77 gate).
