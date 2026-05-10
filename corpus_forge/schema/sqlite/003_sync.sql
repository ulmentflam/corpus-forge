-- SQLite translation of 003_sync.sql
-- Non-PK integer columns (revision_number, parent_revision_id) use INTEGER.
-- SQLite maps all integer-affinity types to INTEGER internally, so INTEGER is
-- used directly here for explicitness and dialect consistency.
-- All table references are unqualified (no schema prefix).
-- Timestamps stored as TEXT (ISO-8601 UTC); booleans stored as INTEGER (0/1).

-- New table: document_revisions ------------------------------------------------
CREATE TABLE IF NOT EXISTS document_revisions (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id        INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  revision_number    INTEGER NOT NULL,
  parent_revision_id INTEGER REFERENCES document_revisions(id) ON DELETE SET NULL,
  content_hash       TEXT NOT NULL,
  text               TEXT NOT NULL DEFAULT '',
  author_host        TEXT NOT NULL,
  is_tombstone       INTEGER NOT NULL DEFAULT 0,
  metadata           TEXT NOT NULL DEFAULT '{}',
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
  UNIQUE (document_id, revision_number)
);

CREATE INDEX IF NOT EXISTS document_revisions_doc_idx
  ON document_revisions(document_id);

CREATE INDEX IF NOT EXISTS document_revisions_parent_idx
  ON document_revisions(parent_revision_id);

-- Add soft-delete timestamp to documents table --------------------------------
ALTER TABLE documents ADD COLUMN IF NOT EXISTS tombstoned_at TEXT;

-- Add sync tracking columns to sources table ----------------------------------
ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_pulled_revision_id INTEGER;

ALTER TABLE sources ADD COLUMN IF NOT EXISTS sync_enabled INTEGER DEFAULT 0;
