-- SQLite translation of 001_core.sql
-- Type mappings applied:
--   PK auto-increment: INTEGER PRIMARY KEY AUTOINCREMENT
--   JSON columns: TEXT (plain string; use json() to validate on insert)
--   Timestamp columns: TEXT (ISO-8601 UTC strings)
--   Default empty JSON object: '{}'
--   pgvector extension omitted: sqlite-vec is loaded at runtime, not via DDL
--   Schema prefix omitted: SQLite has no namespacing (no corpus. prefix on tables)

-- Datasets / sources --------------------------------------------------------
CREATE TABLE IF NOT EXISTS datasets (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL UNIQUE,
  kind        TEXT NOT NULL,                 -- 'text' | 'chat'
  description TEXT,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS sources (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  dataset_id   INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  plugin       TEXT NOT NULL,                -- 'markdown_vault' | 'claude_code' | 'opencode'
  identity     TEXT NOT NULL,                -- canonical id, e.g. vault root path
  host         TEXT NOT NULL,                -- writer hostname (multi-Mac coord)
  config       TEXT NOT NULL DEFAULT '{}',
  last_seen_at TEXT,
  UNIQUE (dataset_id, plugin, identity, host)
);

-- Documents -----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  dataset_id   INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  source_uri   TEXT NOT NULL,                -- e.g. 'vault://Claude Wiki/foo.md'
  content_hash TEXT NOT NULL,                -- sha256 of raw bytes (idempotency key)
  title        TEXT,
  text         TEXT NOT NULL,                -- HF 'text' column
  modified_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  metadata     TEXT NOT NULL DEFAULT '{}',
  UNIQUE (dataset_id, source_uri)
);
CREATE INDEX IF NOT EXISTS documents_hash_idx ON documents(content_hash);

-- Conversations -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  dataset_id    INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  source_uri    TEXT NOT NULL,               -- e.g. 'claude-code://<projectId>/<sessionId>'
  external_id   TEXT,
  title         TEXT,
  started_at    TEXT,
  ended_at      TEXT,
  message_count INT NOT NULL DEFAULT 0,
  content_hash  TEXT NOT NULL,
  metadata      TEXT NOT NULL DEFAULT '{}',
  UNIQUE (dataset_id, source_uri)
);
CREATE INDEX IF NOT EXISTS conversations_hash_idx ON conversations(content_hash);

CREATE TABLE IF NOT EXISTS messages (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  external_uuid   TEXT,
  parent_uuid     TEXT,
  turn_index      INT NOT NULL,
  role            TEXT NOT NULL,             -- 'user'|'assistant'|'system'|'tool'
  content         TEXT NOT NULL,             -- flattened text (tool calls rendered)
  tool_calls      TEXT,
  tool_results    TEXT,
  ts              TEXT,
  metadata        TEXT NOT NULL DEFAULT '{}',
  UNIQUE (conversation_id, turn_index)
);
CREATE INDEX IF NOT EXISTS messages_external_idx
  ON messages(conversation_id, external_uuid);

-- Chunks (the embedded unit - XOR doc/conv) ---------------------------------
CREATE TABLE IF NOT EXISTS chunks (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id     INTEGER REFERENCES documents(id)     ON DELETE CASCADE,
  conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
  message_id      INTEGER REFERENCES messages(id)      ON DELETE CASCADE,
  chunk_index     INT NOT NULL,
  text            TEXT NOT NULL,             -- HF 'text' column
  heading         TEXT,
  role            TEXT,                      -- echoed for chat chunks
  token_count     INT,
  metadata        TEXT NOT NULL DEFAULT '{}',
  UNIQUE (document_id, chunk_index),
  UNIQUE (conversation_id, message_id, chunk_index)
);
CREATE INDEX IF NOT EXISTS chunks_doc_idx  ON chunks(document_id);
CREATE INDEX IF NOT EXISTS chunks_conv_idx ON chunks(conversation_id);

-- Embedders registry --------------------------------------------------------
CREATE TABLE IF NOT EXISTS embedders (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  name        TEXT NOT NULL UNIQUE,          -- 'qwen3_8b' | 'openai_3l'
  provider    TEXT NOT NULL,                 -- 'sentence_transformers' | 'openai'
  model_id    TEXT NOT NULL,                 -- 'Qwen/Qwen3-Embedding-8B'
  dimension   INT NOT NULL,
  normalized  INTEGER NOT NULL DEFAULT 1,    -- SQLite booleans as INTEGER 0/1
  distance    TEXT NOT NULL DEFAULT 'cosine',-- 'cosine'|'l2'|'ip'
  active      INTEGER NOT NULL DEFAULT 1,    -- SQLite booleans as INTEGER 0/1
  table_name  TEXT NOT NULL UNIQUE,          -- e.g. 'embeddings_qwen3_8b'
  config      TEXT NOT NULL DEFAULT '{}',
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Labels (first-class — survives HF export) ---------------------------------
CREATE TABLE IF NOT EXISTS labels (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  namespace TEXT NOT NULL,                   -- 'tag'|'topic'|'language'|'classifier:foo'
  value     TEXT NOT NULL,
  UNIQUE (namespace, value)
);
CREATE TABLE IF NOT EXISTS chunk_labels (
  chunk_id   INTEGER NOT NULL REFERENCES chunks(id) ON DELETE CASCADE,
  label_id   INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
  confidence REAL,
  source     TEXT NOT NULL,                  -- 'frontmatter'|'auto:embedder'|'user'
  PRIMARY KEY (chunk_id, label_id, source)
);
CREATE TABLE IF NOT EXISTS document_labels (
  document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  label_id    INTEGER NOT NULL REFERENCES labels(id)    ON DELETE CASCADE,
  source      TEXT NOT NULL,
  PRIMARY KEY (document_id, label_id, source)
);
CREATE TABLE IF NOT EXISTS conversation_labels (
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  label_id        INTEGER NOT NULL REFERENCES labels(id)        ON DELETE CASCADE,
  source          TEXT NOT NULL,
  PRIMARY KEY (conversation_id, label_id, source)
);
