"""chat_templates table

Revision ID: 0007_chat_templates
Revises: 0006_writes_and_feedback
Create Date: 2026-05-14 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_chat_templates"
down_revision: str | None = "0006_writes_and_feedback"
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
    pass


def _upgrade_postgres() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.chat_templates (
  id           BIGSERIAL PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  source       TEXT NOT NULL,
  jinja        TEXT,
  model_id     TEXT,
  description  TEXT,
  host         TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")


def _upgrade_sqlite() -> None:
    op.execute("""
CREATE TABLE IF NOT EXISTS chat_templates (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL UNIQUE,
  source       TEXT NOT NULL,
  jinja        TEXT,
  model_id     TEXT,
  description  TEXT,
  host         TEXT NOT NULL,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
)
""")
