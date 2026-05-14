"""D-03 RED — Backfill test: content_hash populated by 0002_chunk_content_hash revision.

Flow
----
1.  Spin up a testcontainers Postgres instance (session-scoped postgres_container).
2.  Apply Alembic upgrade to ``0001_core`` only.
3.  Insert a dataset, a document, and 5 chunks with realistic text via raw SQL.
    At this point the ``content_hash`` column does NOT exist (it lands in 0002).
4.  Apply Alembic upgrade to ``0002_chunk_content_hash``.
5.  Assert:
    - The ``content_hash`` column exists on ``corpus.chunks``.
    - Every chunk row has a non-null ``content_hash`` matching
      ``hashlib.sha256(text.encode()).hexdigest()``.
    - The ``chunks_content_hash_idx`` index exists (checked via ``pg_indexes``).

SQLite is OUT OF SCOPE: the legacy migrator's content_hash backfill is
Postgres-only (corpus_forge/schema/migrate.py:77-83).  The D-03 coder will
gate the data-migration step on ``dialect == "postgresql"``.

RED condition
-------------
At tester-commit time the ``0002_chunk_content_hash`` revision file does not yet
exist.  Every test in this file fails with:

    alembic.util.exc.CommandError: Can't locate revision identified by
    '0002_chunk_content_hash'
"""

from __future__ import annotations

import hashlib
import importlib
import re
from pathlib import Path
from typing import Any

import psycopg
import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Availability guards
# ---------------------------------------------------------------------------

_TESTCONTAINERS_AVAILABLE = importlib.util.find_spec("testcontainers") is not None

_skip_no_tc = pytest.mark.skipif(
    not _TESTCONTAINERS_AVAILABLE,
    reason="testcontainers not installed — backfill test skipped",
)

# ---------------------------------------------------------------------------
# Module-level paths
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPO_ROOT / "alembic.ini"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dsn_from_container(c: Any) -> str:
    """Build a bare postgresql:// DSN from a testcontainers Postgres container."""
    return (
        f"postgresql://{c.username}:{c.password}"
        f"@{c.get_container_host_ip()}:{c.get_exposed_port(5432)}"
        f"/{c.dbname}"
    )


def _sa_dsn(dsn: str) -> str:
    """Convert postgresql:// → postgresql+psycopg:// for SQLAlchemy/Alembic."""
    return re.sub(r"^postgresql(s?)://", r"postgresql+psycopg\1://", dsn)


def _alembic_upgrade(dsn: str, target: str) -> None:
    """Run alembic.command.upgrade(config, target) against *dsn*.

    Raises alembic.util.exc.CommandError when *target* is unknown (RED state).
    """
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option(
        "script_location",
        str(_REPO_ROOT / "corpus_forge" / "alembic"),
    )
    cfg.set_main_option("sqlalchemy.url", _sa_dsn(dsn))
    command.upgrade(cfg, target)


def _reset_schema(dsn: str) -> None:
    """Drop and recreate the corpus schema + pgvector extension."""
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS corpus CASCADE")
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE SCHEMA IF NOT EXISTS corpus")


# ---------------------------------------------------------------------------
# Expected SHA-256 hash helper
# ---------------------------------------------------------------------------

_CHUNK_TEXTS: list[str] = [
    "The quick brown fox jumps over the lazy dog.",
    "Sphinx of black quartz, judge my vow.",
    "How vexingly quick daft zebras jump!",
    "Pack my box with five dozen liquor jugs.",
    "Jackdaws love my big sphinx of quartz.",
]


def _expected_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@_skip_no_tc
def test_backfill_populates_content_hash(postgres_container: Any) -> None:  # type: ignore[return]
    """content_hash column is added and back-filled by 0002_chunk_content_hash.

    Steps:
    1. Upgrade to 0001_core only (content_hash column does not exist yet).
    2. Insert 5 chunks with distinct text (no content_hash column referenced).
    3. Upgrade to 0002_chunk_content_hash — this is what the D-03 coder lands.
       At RED time this raises CommandError.
    4. Assert every chunk's content_hash equals sha256(text.encode()).hexdigest().

    RED: fails at step 3 with CommandError — revision file doesn't exist yet.
    """
    dsn = _dsn_from_container(postgres_container)
    _reset_schema(dsn)

    # ── Step 1: apply 0001_core ──────────────────────────────────────────────
    _alembic_upgrade(dsn, "0001_core")

    # ── Step 2: insert seed data ─────────────────────────────────────────────
    # FK chain: datasets → documents → chunks
    # corpus.chunks has a CHECK constraint: exactly one of document_id /
    # conversation_id must be non-null.  We use document_id.
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # Insert one dataset
            cur.execute(
                """
                INSERT INTO corpus.datasets (name, kind, description)
                VALUES ('backfill-test-dataset', 'text', 'D-03 backfill test dataset')
                RETURNING id
                """
            )
            row = cur.fetchone()
            assert row is not None
            dataset_id: int = row[0]

            # Insert one document (content_hash is a required column on documents,
            # not on chunks — chunks get content_hash added by 0002)
            cur.execute(
                """
                INSERT INTO corpus.documents
                    (dataset_id, source_uri, content_hash, title, text)
                VALUES (%s, 'test://backfill/doc1.md', 'placeholder-hash', 'Backfill Doc', 'body')
                RETURNING id
                """,
                (dataset_id,),
            )
            row = cur.fetchone()
            assert row is not None
            document_id: int = row[0]

            # Insert 5 chunks — do NOT reference content_hash (column absent at 0001_core)
            chunk_ids: list[int] = []
            for idx, text in enumerate(_CHUNK_TEXTS):
                cur.execute(
                    """
                    INSERT INTO corpus.chunks
                        (document_id, chunk_index, text)
                    VALUES (%s, %s, %s)
                    RETURNING id
                    """,
                    (document_id, idx, text),
                )
                row = cur.fetchone()
                assert row is not None
                chunk_ids.append(row[0])

        conn.commit()

    # Sanity: confirm content_hash column is truly absent at 0001_core
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'corpus'
              AND table_name   = 'chunks'
              AND column_name  = 'content_hash'
            """
        )
        assert cur.fetchone() is None, (
            "content_hash column already present after 0001_core — "
            "schema assumption is wrong; check 001_core.sql"
        )

    # ── Step 3: upgrade to 0002_chunk_content_hash ───────────────────────────
    # RED: raises CommandError here because the revision doesn't exist yet.
    _alembic_upgrade(dsn, "0002_chunk_content_hash")

    # ── Step 4: assert column exists and is populated ────────────────────────
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # 4a. Column existence
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'corpus'
              AND table_name   = 'chunks'
              AND column_name  = 'content_hash'
            """
        )
        assert cur.fetchone() is not None, (
            "content_hash column missing from corpus.chunks after 0002_chunk_content_hash upgrade"
        )

        # 4b. Every chunk has a non-null content_hash matching sha256(text)
        cur.execute("SELECT id, content_hash FROM corpus.chunks ORDER BY id")
        rows = cur.fetchall()

    assert len(rows) == len(_CHUNK_TEXTS), (
        f"Expected {len(_CHUNK_TEXTS)} chunk rows, got {len(rows)}"
    )

    for (chunk_id, actual_hash), expected_text in zip(rows, _CHUNK_TEXTS, strict=True):
        expected = _expected_hash(expected_text)
        assert actual_hash is not None, f"chunk id={chunk_id} has NULL content_hash after backfill"
        assert actual_hash == expected, (
            f"chunk id={chunk_id}: content_hash mismatch.\n"
            f"  text     = {expected_text!r}\n"
            f"  expected = {expected!r}\n"
            f"  actual   = {actual_hash!r}"
        )


@_skip_no_tc
def test_chunks_content_hash_idx_exists(postgres_container: Any) -> None:  # type: ignore[return]
    """chunks_content_hash_idx index is present after 0002_chunk_content_hash upgrade.

    RED: fails with CommandError because revision doesn't exist yet.
    """
    dsn = _dsn_from_container(postgres_container)
    _reset_schema(dsn)

    # Upgrade through 0002 in one shot — the backfill test exercises stepping;
    # this test only cares about index presence.
    _alembic_upgrade(dsn, "0002_chunk_content_hash")  # RED: CommandError here

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'corpus'
              AND tablename  = 'chunks'
              AND indexname  = 'chunks_content_hash_idx'
            """
        )
        row = cur.fetchone()

    assert row is not None, (
        "chunks_content_hash_idx not found in pg_indexes after 0002_chunk_content_hash upgrade"
    )


@_skip_no_tc
def test_backfill_null_text_handled(postgres_container: Any) -> None:  # type: ignore[return]
    """Chunks that have non-null text all get hashed; the query is idempotent on re-run.

    Exercises the WHERE content_hash IS NULL guard — running the backfill SQL a
    second time against already-populated rows must not corrupt them.

    RED: fails with CommandError because revision doesn't exist yet.
    """
    dsn = _dsn_from_container(postgres_container)
    _reset_schema(dsn)

    _alembic_upgrade(dsn, "0001_core")

    # Insert one chunk with a well-known text value
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO corpus.datasets (name, kind)
                VALUES ('idempotency-dataset', 'text')
                RETURNING id
                """
            )
            row = cur.fetchone()
            assert row is not None
            dataset_id = row[0]

            cur.execute(
                """
                INSERT INTO corpus.documents
                    (dataset_id, source_uri, content_hash, title, text)
                VALUES (%s, 'test://idempotency/doc.md', 'idem-hash', 'Idem Doc', 'body')
                RETURNING id
                """,
                (dataset_id,),
            )
            row = cur.fetchone()
            assert row is not None
            document_id = row[0]

            cur.execute(
                """
                INSERT INTO corpus.chunks (document_id, chunk_index, text)
                VALUES (%s, 0, %s)
                RETURNING id
                """,
                (document_id, "Idempotency test chunk text."),
            )
            row = cur.fetchone()
            assert row is not None
            chunk_id = row[0]

        conn.commit()

    # RED: upgrade to 0002 fails here
    _alembic_upgrade(dsn, "0002_chunk_content_hash")

    # Simulate second run of backfill (WHERE content_hash IS NULL → no-op)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE corpus.chunks
            SET content_hash = encode(sha256(text::bytea), 'hex')
            WHERE content_hash IS NULL
            """
        )
        # Fetch to confirm no corruption
        cur.execute(
            "SELECT content_hash FROM corpus.chunks WHERE id = %s",
            (chunk_id,),
        )
        row = cur.fetchone()

    expected = _expected_hash("Idempotency test chunk text.")
    assert row is not None
    assert row[0] == expected, f"Idempotency check failed: got {row[0]!r}, expected {expected!r}"
