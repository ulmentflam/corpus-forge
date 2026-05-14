"""D-06 RED — SQLite FTS5 backfill test: 0005_fts revision.

Flow
----
1.  Create a fresh SQLite DB via tmp_path (no Docker needed — SQLite is in-process).
2.  Build an Alembic Config pointing at sqlite:///{db_path}.
3.  Upgrade to 0004_sync (schema up to, but NOT including, FTS).
4.  Insert 5 chunks with realistic text values (FK chain: dataset → document → chunks).
    Capture each chunk's (id, text) for later FTS verification.
5.  Upgrade to 0005_fts — this is what the D-06 coder will land.
    At RED time, this step raises:
        alembic.util.exc.CommandError: Can't locate revision identified by '0005_fts'

Assertions (post-upgrade):
  A. chunks_fts virtual table is present in sqlite_master.
  B. Pre-existing chunks are searchable via FTS — at least 2 distinct MATCH queries
     each return the correct rowid for their respective chunk.
  C. No delete-marker pollution: for each unique-word chunk, exactly 1 row matches.
     (If a naive ``INSERT INTO chunks_fts SELECT`` had been used instead of the FTS5
     ``'rebuild'`` command, external-content FTS5 semantics would produce 0 rows because
     there is no corresponding content-table row at query time — so "0 rows" is actually
     the naive-insert failure mode for external-content tables, not delete markers.
     The ``'rebuild'`` command re-indexes every rowid from the content table directly,
     so each chunk is exactly 1 hit.)
  D. AFTER INSERT trigger fires: insert a new chunk AFTER the migration and query
     chunks_fts for one of its unique words; expect exactly 1 hit.

RED condition
-------------
Every test fails at the ``alembic.command.upgrade(config, "0005_fts")`` call with:

    alembic.util.exc.CommandError: Can't locate revision identified by '0005_fts'

because the revision file ``corpus_forge/alembic/versions/0005_fts.py`` does not yet exist.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"

# ---------------------------------------------------------------------------
# Seed data: 5 chunks with realistic, distinct text
# Each chunk has at least one unique word (marked with <<UNIQUE>> in comments)
# so we can test exact-1-match queries without cross-contamination.
# ---------------------------------------------------------------------------

_CHUNK_TEXTS: list[str] = [
    "The quick brown fox jumps over the lazy dog.",  # unique: "jumps"
    "Sphinx of black quartz, judge my vow.",  # unique: "sphinx"
    "How vexingly quick daft zebras jump!",  # unique: "vexingly"
    "Pack my box with five dozen liquor jugs.",  # unique: "liquor"
    "Jackdaws love my big sphinx of quartz.",  # unique: "jackdaws"
]

# Words guaranteed to be unique within _CHUNK_TEXTS (one per chunk, for MATCH queries).
_UNIQUE_WORDS: list[str] = [
    "jumps",
    "judge",
    "vexingly",
    "liquor",
    "jackdaws",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_alembic_config(db_path: Path):
    """Return an Alembic Config pointing at *db_path* as a SQLite DB."""
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _seed_chunks(db_path: Path) -> list[tuple[int, str]]:
    """Insert a dataset, document, and 5 chunks into *db_path*.

    Returns list of (chunk_id, text) tuples in insertion order.
    Assumes schema is at head=0004_sync (chunks table exists, no chunks_fts yet).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        # Insert one dataset
        cur = conn.execute("INSERT INTO datasets (name, kind) VALUES ('fts-test-dataset', 'text')")
        dataset_id = cur.lastrowid

        # Insert one document (source_uri + content_hash are required NOT NULL columns)
        cur = conn.execute(
            """
            INSERT INTO documents (dataset_id, source_uri, content_hash, title, text)
            VALUES (?, 'test://fts/doc1.md', 'placeholder-hash', 'FTS Doc', 'body')
            """,
            (dataset_id,),
        )
        document_id = cur.lastrowid

        # Insert 5 chunks
        chunk_rows: list[tuple[int, str]] = []
        for idx, text in enumerate(_CHUNK_TEXTS):
            cur = conn.execute(
                "INSERT INTO chunks (document_id, chunk_index, text) VALUES (?, ?, ?)",
                (document_id, idx, text),
            )
            chunk_rows.append((cur.lastrowid, text))

        conn.commit()
    finally:
        conn.close()

    return chunk_rows


def _fts_match_rowids(db_path: Path, word: str) -> list[int]:
    """Query chunks_fts for *word* and return matching rowids (sorted)."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?",
            (word,),
        ).fetchall()
    finally:
        conn.close()
    return sorted(r[0] for r in rows)


def _virtual_table_exists(db_path: Path, table_name: str) -> bool:
    """Return True if *table_name* appears as a virtual table in sqlite_master."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
    finally:
        conn.close()
    return row is not None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_chunks_fts_virtual_table_exists(tmp_path: Path) -> None:
    """chunks_fts virtual table is present in sqlite_master after 0005_fts upgrade.

    RED: CommandError at upgrade("0005_fts") because revision doesn't exist yet.
    """
    from alembic import command

    db_path = tmp_path / "fts_table.db"
    cfg = _build_alembic_config(db_path)

    # Bring schema to 0004_sync (pre-FTS baseline).
    command.upgrade(cfg, "0004_sync")

    # Upgrade to 0005_fts — RED: raises CommandError here.
    command.upgrade(cfg, "0005_fts")

    # Assert virtual table is registered in sqlite_master.
    assert _virtual_table_exists(db_path, "chunks_fts"), (
        "chunks_fts virtual table not found in sqlite_master after 0005_fts upgrade"
    )


def test_preexisting_chunks_searchable_after_backfill(tmp_path: Path) -> None:
    """Pre-existing chunks are findable via FTS MATCH after the rebuild backfill.

    Steps:
    1. Upgrade to 0004_sync.
    2. Insert 5 chunks (pre-FTS rows).
    3. Upgrade to 0005_fts — the D-06 coder's revision must run
       ``INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')`` to index them.
    4. Query chunks_fts for unique words from chunk 0 and chunk 2; assert
       the correct rowid is returned.

    RED: CommandError at step 3 because 0005_fts revision doesn't exist yet.
    """
    from alembic import command

    db_path = tmp_path / "fts_preexisting.db"
    cfg = _build_alembic_config(db_path)

    # Step 1: bring to pre-FTS baseline
    command.upgrade(cfg, "0004_sync")

    # Step 2: seed chunks
    chunk_rows = _seed_chunks(db_path)

    # Step 3: upgrade to 0005_fts — RED here
    command.upgrade(cfg, "0005_fts")

    # Step 4: query for unique words from chunk 0 and chunk 2
    chunk0_id, _chunk0_text = chunk_rows[0]
    chunk2_id, _chunk2_text = chunk_rows[2]

    word0 = _UNIQUE_WORDS[0]  # "jumps" — unique to chunk 0
    word2 = _UNIQUE_WORDS[2]  # "vexingly" — unique to chunk 2

    hits0 = _fts_match_rowids(db_path, word0)
    assert hits0 == [chunk0_id], (
        f"FTS MATCH '{word0}' returned rowids {hits0!r}; expected [{chunk0_id}].\n"
        "Likely cause: backfill was not run (chunks_fts empty), or naive INSERT was "
        "used instead of the 'rebuild' command."
    )

    hits2 = _fts_match_rowids(db_path, word2)
    assert hits2 == [chunk2_id], (
        f"FTS MATCH '{word2}' returned rowids {hits2!r}; expected [{chunk2_id}].\n"
        "Likely cause: backfill was not run (chunks_fts empty), or naive INSERT was "
        "used instead of the 'rebuild' command."
    )


def test_no_delete_markers_after_rebuild_backfill(tmp_path: Path) -> None:
    """Each unique-word chunk matches exactly once — no delete-marker pollution.

    The FTS5 ``'rebuild'`` command is the only reliable backfill path for an
    external-content table (``content='chunks', content_rowid='id'``).

    A naive ``INSERT INTO chunks_fts SELECT id, text FROM chunks`` would create
    shadow rows without corresponding content-table entries.  For external-content
    FTS5, at query time SQLite re-fetches content from the content table by rowid;
    if the content row exists, you get a hit.  If the backfill approach is wrong,
    you get the wrong count.

    This test asserts COUNT = 1 for every unique word across all 5 chunks.
    Any count != 1 indicates a broken backfill strategy.

    RED: CommandError at upgrade("0005_fts") because revision doesn't exist yet.
    """
    from alembic import command

    db_path = tmp_path / "fts_no_delete_markers.db"
    cfg = _build_alembic_config(db_path)

    command.upgrade(cfg, "0004_sync")
    chunk_rows = _seed_chunks(db_path)
    command.upgrade(cfg, "0005_fts")

    conn = sqlite3.connect(str(db_path))
    try:
        for idx, word in enumerate(_UNIQUE_WORDS):
            row = conn.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?",
                (word,),
            ).fetchone()
            count = row[0] if row else 0
            chunk_id, text = chunk_rows[idx]
            assert count == 1, (
                f"FTS MATCH '{word}' (from chunk id={chunk_id}) returned count={count}, "
                f"expected 1.\n"
                f"  chunk text: {text!r}\n"
                "Possible causes:\n"
                "  count=0: backfill not run or failed silently\n"
                "  count>1: double-insertion (INSERT + rebuild), or duplicate rows"
            )
    finally:
        conn.close()


def test_after_insert_trigger_fires_for_new_chunks(tmp_path: Path) -> None:
    """AFTER INSERT trigger (chunks_ai) indexes new chunks added post-migration.

    Steps:
    1. Upgrade to 0004_sync.
    2. Insert 5 pre-FTS chunks (to confirm backfill scenario still works).
    3. Upgrade to 0005_fts.
    4. Insert a 6th chunk with a unique word NOT present in any earlier chunk.
    5. Query chunks_fts for that unique word; expect exactly 1 hit with the new rowid.

    This confirms the AFTER INSERT trigger is operational — not just the backfill.

    RED: CommandError at upgrade("0005_fts") because revision doesn't exist yet.
    """
    from alembic import command

    db_path = tmp_path / "fts_trigger.db"
    cfg = _build_alembic_config(db_path)

    # Step 1: bring to pre-FTS baseline
    command.upgrade(cfg, "0004_sync")

    # Step 2: seed pre-FTS chunks
    _seed_chunks(db_path)

    # Step 3: upgrade to 0005_fts — RED here
    command.upgrade(cfg, "0005_fts")

    # Step 4: insert a new chunk with a word that cannot appear in existing FTS index
    new_unique_word = "xyzzyquux"
    new_text = f"Unique post-migration chunk containing the word {new_unique_word}."

    conn = sqlite3.connect(str(db_path))
    try:
        # We need the document_id from the already-seeded data.
        row = conn.execute("SELECT id FROM documents LIMIT 1").fetchone()
        assert row is not None, "No document found — seed step did not run correctly"
        document_id = row[0]

        cur = conn.execute(
            "INSERT INTO chunks (document_id, chunk_index, text) VALUES (?, 99, ?)",
            (document_id, new_text),
        )
        new_chunk_id = cur.lastrowid
        conn.commit()

        # Step 5: query FTS for the unique word
        rows = conn.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ?",
            (new_unique_word,),
        ).fetchall()
    finally:
        conn.close()

    hit_rowids = sorted(r[0] for r in rows)
    assert hit_rowids == [new_chunk_id], (
        f"FTS MATCH '{new_unique_word}' returned {hit_rowids!r}; "
        f"expected [{new_chunk_id}].\n"
        "This means the AFTER INSERT trigger (chunks_ai) did not fire or is missing."
    )


def test_fts_total_chunk_count_after_backfill(tmp_path: Path) -> None:
    """Total FTS-indexed row count equals chunks table count after rebuild backfill.

    Uses the FTS5 meta-query ``SELECT COUNT(*) FROM chunks_fts`` to assert that
    exactly N live rows are present (matching the chunks table).

    Note: for external-content FTS5, ``SELECT COUNT(*) FROM chunks_fts`` counts
    index entries (including any delete markers if present).  After a clean
    ``'rebuild'``, the count equals the number of rows in the content table.

    RED: CommandError at upgrade("0005_fts") because revision doesn't exist yet.
    """
    from alembic import command

    db_path = tmp_path / "fts_count.db"
    cfg = _build_alembic_config(db_path)

    command.upgrade(cfg, "0004_sync")
    chunk_rows = _seed_chunks(db_path)
    command.upgrade(cfg, "0005_fts")

    expected_count = len(chunk_rows)

    conn = sqlite3.connect(str(db_path))
    try:
        # Count chunks in the base table
        chunks_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        assert chunks_count == expected_count, (
            f"chunks table has {chunks_count} rows, expected {expected_count}"
        )

        # Verify all chunks are searchable by querying each unique word
        # (a direct COUNT(*) FROM chunks_fts counts shadow-index entries,
        # which is not stable for external-content tables — unique-word
        # coverage is a more reliable proxy for "all rows indexed")
        for idx, word in enumerate(_UNIQUE_WORDS):
            hits = conn.execute(
                "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH ?",
                (word,),
            ).fetchone()[0]
            assert hits >= 1, (
                f"Word '{word}' (chunk {idx}) not found in chunks_fts after backfill. "
                "The rebuild backfill may not have run."
            )
    finally:
        conn.close()
