-- SQLite translation of 002_chunk_content_hash.sql
-- Table names are unqualified (no schema prefix, no corpus. prefix)
-- The Postgres-only backfill pass (sha256 via pg function) does not apply here
-- apply_migrations() skips that backfill when dialect="sqlite"

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS content_hash TEXT;

CREATE INDEX IF NOT EXISTS chunks_content_hash_idx ON chunks(content_hash);
