"""views (Postgres-only)

Revision ID: 0003_views
Revises: 0002_chunk_content_hash
Create Date: 2026-05-13 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003_views"
down_revision: str | None = "0002_chunk_content_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        _upgrade_postgres()
    # SQLite: no-op. Views are Postgres-only; the legacy migrator has no
    # corresponding sqlite/002_views.sql, so SQLite parity at this head
    # is just "Alembic SQLite did nothing".


def downgrade() -> None:
    pass  # Phase D is forward-only


def _upgrade_postgres() -> None:
    op.execute("""
CREATE OR REPLACE VIEW corpus.corpus_text_export AS
SELECT
  ('chunk:' || c.id)::text                AS id,
  c.text                                  AS text,
  d.name                                  AS source,
  COALESCE(doc.title, conv.title)         AS title,
  c.heading,
  c.role,
  jsonb_build_object(
    'document_uri',     doc.source_uri,
    'conversation_uri', conv.source_uri,
    'chunk_index',      c.chunk_index,
    'token_count',      c.token_count,
    'metadata',         c.metadata
  )                                       AS metadata,
  ARRAY(SELECT l.namespace || ':' || l.value
          FROM corpus.chunk_labels cl
          JOIN corpus.labels l ON l.id = cl.label_id
         WHERE cl.chunk_id = c.id)        AS labels
FROM corpus.chunks c
LEFT JOIN corpus.documents     doc  ON doc.id  = c.document_id
LEFT JOIN corpus.conversations conv ON conv.id = c.conversation_id
JOIN corpus.datasets d
  ON d.id = COALESCE(doc.dataset_id, conv.dataset_id)
""")

    op.execute("""
CREATE OR REPLACE VIEW corpus.corpus_chat_export AS
SELECT
  ('conv:' || conv.id)::text AS id,
  d.name                     AS source,
  conv.title,
  (SELECT jsonb_agg(
           jsonb_build_object('role', m.role, 'content', m.content)
           ORDER BY m.turn_index)
      FROM corpus.messages m
     WHERE m.conversation_id = conv.id) AS messages,
  conv.metadata
FROM corpus.conversations conv
JOIN corpus.datasets d ON d.id = conv.dataset_id
""")
