-- Migration: add content_hash column to chunks table
-- Purpose: enable deduplication and embedding reuse on chunk content.
-- Idempotent: IF NOT EXISTS guards on both ALTER and CREATE.

ALTER TABLE corpus.chunks ADD COLUMN IF NOT EXISTS content_hash TEXT;

CREATE INDEX IF NOT EXISTS chunks_content_hash_idx ON corpus.chunks(content_hash);
