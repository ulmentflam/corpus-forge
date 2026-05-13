-- Phase R1 — SQLite FTS5: external-content virtual table mirroring the
-- chunks table, plus AFTER INSERT / DELETE / UPDATE triggers to keep it
-- in sync.  Tokenizer is "porter unicode61" so plural / singular forms
-- match each other (running ⇄ run) while still being Unicode-correct.
--
-- Re-applying this migration is a no-op: every CREATE has IF NOT EXISTS,
-- and the migration runner invokes backend.backfill_lexical_index() after
-- this file is applied to populate chunks_fts for any pre-existing rows
-- (the AFTER INSERT trigger handles new rows automatically).

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  text,
  content='chunks',
  content_rowid='id',
  tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
END;

CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
  INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', old.id, old.text);
  INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
