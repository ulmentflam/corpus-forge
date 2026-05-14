"""core schema

Revision ID: 0001_core
Revises:
Create Date: 2026-05-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0001_core"
down_revision: str | None = None
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
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS corpus")
    op.execute("SET search_path = corpus, public")

    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.datasets (
  id          BIGSERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  kind        TEXT NOT NULL,
  description TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.sources (
  id           BIGSERIAL PRIMARY KEY,
  dataset_id   BIGINT NOT NULL REFERENCES corpus.datasets(id) ON DELETE CASCADE,
  plugin       TEXT NOT NULL,
  identity     TEXT NOT NULL,
  host         TEXT NOT NULL,
  config       JSONB NOT NULL DEFAULT '{}'::jsonb,
  last_seen_at TIMESTAMPTZ,
  UNIQUE (dataset_id, plugin, identity, host)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.documents (
  id           BIGSERIAL PRIMARY KEY,
  dataset_id   BIGINT NOT NULL REFERENCES corpus.datasets(id) ON DELETE CASCADE,
  source_uri   TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  title        TEXT,
  text         TEXT NOT NULL,
  modified_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  metadata     JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (dataset_id, source_uri)
)
""")

    op.execute("CREATE INDEX IF NOT EXISTS documents_hash_idx ON corpus.documents(content_hash)")

    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.conversations (
  id            BIGSERIAL PRIMARY KEY,
  dataset_id    BIGINT NOT NULL REFERENCES corpus.datasets(id) ON DELETE CASCADE,
  source_uri    TEXT NOT NULL,
  external_id   TEXT,
  title         TEXT,
  started_at    TIMESTAMPTZ,
  ended_at      TIMESTAMPTZ,
  message_count INT NOT NULL DEFAULT 0,
  content_hash  TEXT NOT NULL,
  metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (dataset_id, source_uri)
)
""")

    op.execute(
        "CREATE INDEX IF NOT EXISTS conversations_hash_idx ON corpus.conversations(content_hash)"
    )

    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.messages (
  id              BIGSERIAL PRIMARY KEY,
  conversation_id BIGINT NOT NULL REFERENCES corpus.conversations(id) ON DELETE CASCADE,
  external_uuid   TEXT,
  parent_uuid     TEXT,
  turn_index      INT NOT NULL,
  role            TEXT NOT NULL,
  content         TEXT NOT NULL,
  tool_calls      JSONB,
  tool_results    JSONB,
  ts              TIMESTAMPTZ,
  metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (conversation_id, turn_index)
)
""")

    op.execute("""
CREATE INDEX IF NOT EXISTS messages_external_idx
  ON corpus.messages(conversation_id, external_uuid)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.chunks (
  id              BIGSERIAL PRIMARY KEY,
  document_id     BIGINT REFERENCES corpus.documents(id)     ON DELETE CASCADE,
  conversation_id BIGINT REFERENCES corpus.conversations(id) ON DELETE CASCADE,
  message_id      BIGINT REFERENCES corpus.messages(id)      ON DELETE CASCADE,
  chunk_index     INT NOT NULL,
  text            TEXT NOT NULL,
  heading         TEXT,
  role            TEXT,
  token_count     INT,
  metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
  CHECK ( (document_id IS NOT NULL)::int + (conversation_id IS NOT NULL)::int = 1 ),
  UNIQUE (document_id, chunk_index),
  UNIQUE (conversation_id, message_id, chunk_index)
)
""")

    op.execute("CREATE INDEX IF NOT EXISTS chunks_doc_idx  ON corpus.chunks(document_id)")
    op.execute("CREATE INDEX IF NOT EXISTS chunks_conv_idx ON corpus.chunks(conversation_id)")

    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.embedders (
  id          BIGSERIAL PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  provider    TEXT NOT NULL,
  model_id    TEXT NOT NULL,
  dimension   INT NOT NULL,
  normalized  BOOLEAN NOT NULL DEFAULT TRUE,
  distance    TEXT NOT NULL DEFAULT 'cosine',
  active      BOOLEAN NOT NULL DEFAULT TRUE,
  table_name  TEXT NOT NULL UNIQUE,
  config      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.labels (
  id        BIGSERIAL PRIMARY KEY,
  namespace TEXT NOT NULL,
  value     TEXT NOT NULL,
  UNIQUE (namespace, value)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.chunk_labels (
  chunk_id   BIGINT NOT NULL REFERENCES corpus.chunks(id) ON DELETE CASCADE,
  label_id   BIGINT NOT NULL REFERENCES corpus.labels(id) ON DELETE CASCADE,
  confidence REAL,
  source     TEXT NOT NULL,
  PRIMARY KEY (chunk_id, label_id, source)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.document_labels (
  document_id BIGINT NOT NULL REFERENCES corpus.documents(id) ON DELETE CASCADE,
  label_id    BIGINT NOT NULL REFERENCES corpus.labels(id)    ON DELETE CASCADE,
  source      TEXT NOT NULL,
  PRIMARY KEY (document_id, label_id, source)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS corpus.conversation_labels (
  conversation_id BIGINT NOT NULL REFERENCES corpus.conversations(id) ON DELETE CASCADE,
  label_id        BIGINT NOT NULL REFERENCES corpus.labels(id)        ON DELETE CASCADE,
  source          TEXT NOT NULL,
  PRIMARY KEY (conversation_id, label_id, source)
)
""")


def _upgrade_sqlite() -> None:
    # No CREATE EXTENSION or CREATE SCHEMA — SQLite has neither.
    # Use unqualified table names (no corpus. prefix).
    # INTEGER PRIMARY KEY without AUTOINCREMENT to match legacy SQLiteBackend._execute
    # behavior (the _execute method strips AUTOINCREMENT, so the legacy schema
    # has no sqlite_sequence rows for these tables).

    op.execute("""
CREATE TABLE IF NOT EXISTS datasets (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  kind        TEXT NOT NULL,
  description TEXT,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS sources (
  id           INTEGER PRIMARY KEY,
  dataset_id   INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  plugin       TEXT NOT NULL,
  identity     TEXT NOT NULL,
  host         TEXT NOT NULL,
  config       TEXT NOT NULL DEFAULT '{}',
  last_seen_at TEXT,
  UNIQUE (dataset_id, plugin, identity, host)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS documents (
  id           INTEGER PRIMARY KEY,
  dataset_id   INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  source_uri   TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  title        TEXT,
  text         TEXT NOT NULL,
  modified_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  metadata     TEXT NOT NULL DEFAULT '{}',
  UNIQUE (dataset_id, source_uri)
)
""")

    op.execute("CREATE INDEX IF NOT EXISTS documents_hash_idx ON documents(content_hash)")

    op.execute("""
CREATE TABLE IF NOT EXISTS conversations (
  id            INTEGER PRIMARY KEY,
  dataset_id    INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  source_uri    TEXT NOT NULL,
  external_id   TEXT,
  title         TEXT,
  started_at    TEXT,
  ended_at      TEXT,
  message_count INT NOT NULL DEFAULT 0,
  content_hash  TEXT NOT NULL,
  metadata      TEXT NOT NULL DEFAULT '{}',
  UNIQUE (dataset_id, source_uri)
)
""")

    op.execute("CREATE INDEX IF NOT EXISTS conversations_hash_idx ON conversations(content_hash)")

    op.execute("""
CREATE TABLE IF NOT EXISTS messages (
  id              INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  external_uuid   TEXT,
  parent_uuid     TEXT,
  turn_index      INT NOT NULL,
  role            TEXT NOT NULL,
  content         TEXT NOT NULL,
  tool_calls      TEXT,
  tool_results    TEXT,
  ts              TEXT,
  metadata        TEXT NOT NULL DEFAULT '{}',
  UNIQUE (conversation_id, turn_index)
)
""")

    op.execute("""
CREATE INDEX IF NOT EXISTS messages_external_idx
  ON messages(conversation_id, external_uuid)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS chunks (
  id              INTEGER PRIMARY KEY,
  document_id     INTEGER REFERENCES documents(id)     ON DELETE CASCADE,
  conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
  message_id      INTEGER REFERENCES messages(id)      ON DELETE CASCADE,
  chunk_index     INT NOT NULL,
  text            TEXT NOT NULL,
  heading         TEXT,
  role            TEXT,
  token_count     INT,
  metadata        TEXT NOT NULL DEFAULT '{}',
  UNIQUE (document_id, chunk_index),
  UNIQUE (conversation_id, message_id, chunk_index)
)
""")

    op.execute("CREATE INDEX IF NOT EXISTS chunks_doc_idx  ON chunks(document_id)")
    op.execute("CREATE INDEX IF NOT EXISTS chunks_conv_idx ON chunks(conversation_id)")

    op.execute("""
CREATE TABLE IF NOT EXISTS embedders (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  provider    TEXT NOT NULL,
  model_id    TEXT NOT NULL,
  dimension   INT NOT NULL,
  normalized  INTEGER NOT NULL DEFAULT 1,
  distance    TEXT NOT NULL DEFAULT 'cosine',
  active      INTEGER NOT NULL DEFAULT 1,
  table_name  TEXT NOT NULL UNIQUE,
  config      TEXT NOT NULL DEFAULT '{}',
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS labels (
  id        INTEGER PRIMARY KEY,
  namespace TEXT NOT NULL,
  value     TEXT NOT NULL,
  UNIQUE (namespace, value)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS chunk_labels (
  chunk_id   INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  label_id   INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
  confidence REAL,
  source     TEXT NOT NULL,
  PRIMARY KEY (chunk_id, label_id, source)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS document_labels (
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  label_id    INTEGER NOT NULL REFERENCES labels(id)    ON DELETE CASCADE,
  source      TEXT NOT NULL,
  PRIMARY KEY (document_id, label_id, source)
)
""")

    op.execute("""
CREATE TABLE IF NOT EXISTS conversation_labels (
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  label_id        INTEGER NOT NULL REFERENCES labels(id)        ON DELETE CASCADE,
  source          TEXT NOT NULL,
  PRIMARY KEY (conversation_id, label_id, source)
)
""")
