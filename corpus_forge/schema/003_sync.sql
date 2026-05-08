-- Migration: add sync support — document_revisions table + source columns
-- Purpose: enable bidirectional sync with revision tracking, tombstone
--         propagation, and per-source pull progress.
-- Idempotent: IF NOT EXISTS guards on all DDL statements.

-- New table: document_revisions ------------------------------------------------
CREATE TABLE IF NOT EXISTS corpus.document_revisions (
  id                BIGSERIAL PRIMARY KEY,
  document_id       BIGINT NOT NULL REFERENCES corpus.documents(id) ON DELETE CASCADE,
  revision_number   BIGINT NOT NULL,
  parent_revision_id BIGINT REFERENCES corpus.document_revisions(id) ON DELETE SET NULL,
  content_hash      TEXT NOT NULL,
  text              TEXT NOT NULL DEFAULT '',
  author_host       TEXT NOT NULL,
  is_tombstone      BOOLEAN NOT NULL DEFAULT FALSE,
  metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (document_id, revision_number)
);

CREATE INDEX IF NOT EXISTS document_revisions_doc_idx
  ON corpus.document_revisions(document_id);

CREATE INDEX IF NOT EXISTS document_revisions_parent_idx
  ON corpus.document_revisions(parent_revision_id);

-- New column: documents.tombstoned_at -----------------------------------------
ALTER TABLE corpus.documents ADD COLUMN IF NOT EXISTS tombstoned_at TIMESTAMPTZ;

-- New columns: sources.last_pulled_revision_id, sources.sync_enabled ----------
ALTER TABLE corpus.sources ADD COLUMN IF NOT EXISTS last_pulled_revision_id BIGINT;

ALTER TABLE corpus.sources ADD COLUMN IF NOT EXISTS sync_enabled BOOLEAN DEFAULT FALSE;
