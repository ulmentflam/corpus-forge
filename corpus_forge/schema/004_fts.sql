-- Phase R1 — Postgres FTS: add a STORED generated tsvector column on
-- corpus.chunks and a GIN index on it.  The GENERATED ALWAYS AS ... STORED
-- column auto-populates on ADD COLUMN for all existing rows, so no
-- explicit backfill is required on the Postgres side.

ALTER TABLE corpus.chunks ADD COLUMN IF NOT EXISTS text_tsv tsvector
  GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;

CREATE INDEX IF NOT EXISTS chunks_tsv_idx ON corpus.chunks USING GIN (text_tsv);
