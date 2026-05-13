"""CI-2 — Pin the per-test isolation that fixes the chunk-reuse flake.

Carry-over from CI-1: ``test_chunk_reuse_e2e[postgres]`` order-couples with
``test_backend_dual.py::TestChunkReuseE2E`` under ``pytest-randomly`` because
both suites:

1.  Reuse the same session-scoped Postgres container via the ``pg_dsn`` fixture.
2.  Build the *same* ``_build_doc(12)`` markdown text → identical chunk
    ``content_hash`` per chunk.
3.  Register an embedder under a deterministic name (``fake_embedder`` or
    ``dual-fake``).  Inside ``upsert_document``, ``_copy_reusable_embeddings``
    finds prior chunks (same content_hash, same embedder table) and
    *bulk-copies* embeddings forward, bypassing ``embedder.encode()``.
4.  The test asserts ``encoder.call_count >= 10`` on the first ingest →
    when reuse fires, it sees 0 and fails.

The fix lives in ``tests/conftest.py``: the ``pg_dsn`` fixture must reset
``corpus.*`` between tests (TRUNCATE … RESTART IDENTITY CASCADE on every
table in the ``corpus`` schema) so each test starts with a clean slate.

This test pins that contract: after running a "warm up" ingest that would
otherwise contaminate the shared schema, a brand-new ingest in the same
session must still call ``encode()`` on every chunk.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import psycopg
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.chunkers.base import MarkdownChunker
from corpus_forge.ingest import ingest_one
from corpus_forge.sources.base import RawDocument

pytestmark = pytest.mark.integration

_DIM = 8
_MAX_CHARS = 400
_OVERLAP = 20
_MIN_INITIAL_CHUNKS = 10

_PARA = (
    "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim "
    "veniam quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea "
    "commodo consequat. "
)


def _build_doc(n_sections: int) -> str:
    parts: list[str] = []
    for i in range(1, n_sections + 1):
        para = _PARA + f"[section-{i}]"
        parts.append(f"## Section {i}\n\n{para}")
    return "\n\n".join(parts)


class _CountingEmbedder:
    """Same shape as the dual-fake / fake_embedder used by the suspect tests."""

    provider: str = "fake"
    model_id: str = "fake-isolation-v1"
    dimension: int = _DIM
    normalized: bool = True
    distance: str = "cosine"

    def __init__(self, name: str) -> None:
        self.name = name
        self.call_count = 0
        self.call_args_list: list[tuple[tuple, dict]] = []

    def _encode_one(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = np.frombuffer(digest[:_DIM], dtype=np.uint8).astype(np.float32)
        vec = (vec + 1.0) / 256.0
        return vec / np.linalg.norm(vec)

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        self.call_args_list.append(((list(texts),), {"batch_size": batch_size}))
        self.call_count += 1
        return np.stack([self._encode_one(t) for t in texts])

    def warmup(self) -> None:
        pass


def _make_doc(path: Path, source_uri: str, text: str) -> RawDocument:
    path.write_text(text, encoding="utf-8")
    return RawDocument(
        source_uri=source_uri,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        text=text,
        title="Isolation Pin",
        modified_at=0.0,
        metadata={},
        labels=[],
    )


def _chunks_count(pg_dsn: str) -> int:
    """Return COUNT(*) on corpus.chunks; returns -1 if the schema doesn't exist
    (i.e. the fixture dropped it as expected)."""
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_schema='corpus' AND table_name='chunks'"
            ")"
        )
        exists_row = cur.fetchone()
        if not (exists_row and exists_row[0]):
            return -1
        cur.execute("SELECT COUNT(*) FROM corpus.chunks")
        row = cur.fetchone()
        return row[0] if row else 0


class TestChunkReuseIsolation:
    """The pg_dsn fixture must isolate per-test schema state.

    The cross-test flake mechanism (CI-1 carry-over):

    1. Test A ingests _build_doc(12) under embedder name X, registering
       embeddings_X table rows for every chunk.
    2. Test B (using the same session-scoped container, no schema reset)
       ingests the *same* _build_doc(12) text under the *same* embedder name
       X. ingest_one → upsert_document → _copy_reusable_embeddings finds
       embeddings_X rows whose content_hash matches the new chunks and
       bulk-copies them. ``chunks_missing_embedding`` then returns 0.
    3. Test B's spy assertion ``encoder.call_count >= 10`` fails: the
       encoder was never called because reuse covered everything.

    The fix lives in tests/conftest.py: the ``pg_dsn`` fixture drops the
    ``corpus`` schema (CASCADE) before yielding so each test starts with no
    chunks, no embeddings, no datasets — and ``_copy_reusable_embeddings``
    has nothing to find.

    These three tests run in order (no:randomly via the class) so the second
    and third tests would FAIL if the reset hook didn't run.
    """

    def test_first_run_starts_clean(self, pg_dsn: str, tmp_path: Path) -> None:
        """Contract A: fixture entry hands us a wiped schema."""
        count = _chunks_count(pg_dsn)
        assert count in (-1, 0), (
            f"pg_dsn must reset the schema between tests; "
            f"found {count} pre-existing rows in corpus.chunks. "
            "See tests/conftest.py::pg_dsn for the per-test DROP SCHEMA hook."
        )

        # Contract B: an ingest into the clean schema actually invokes encode().
        backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
        backend.migrate()
        embedder = _CountingEmbedder(name="iso-fake")
        chunker = MarkdownChunker(max_chars=_MAX_CHARS, overlap=_OVERLAP)

        ds_rows = backend._execute(
            "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
            ("iso-dataset-1", "text"),
        )
        ds_id = ds_rows[0]["id"]

        doc_path = tmp_path / "iso.md"
        raw = _make_doc(doc_path, "vault://iso/note.md", _build_doc(12))
        ingest_one(backend, raw, chunker, [embedder], ds_id)

        first_pass_arg_count = sum(len(args[0]) for args, _ in embedder.call_args_list)
        assert first_pass_arg_count >= _MIN_INITIAL_CHUNKS, (
            f"First ingest into a CLEAN schema must encode ≥{_MIN_INITIAL_CHUNKS} texts; "
            f"got {first_pass_arg_count}. _copy_reusable_embeddings is pulling from "
            "a prior test's residue — the pg_dsn fixture isn't dropping corpus."
        )

    def test_second_run_does_not_inherit_first_runs_embeddings(
        self, pg_dsn: str, tmp_path: Path
    ) -> None:
        """Re-run the same pattern under the same embedder name — same assertion.

        Without the reset, ``test_first_run_starts_clean`` would have left
        ``embeddings_iso_fake`` rows for the 12 chunks of _build_doc(12). If
        the fixture doesn't drop the schema, this test would observe 0
        encoder calls and fail.
        """
        count = _chunks_count(pg_dsn)
        assert count in (-1, 0), (
            f"Second test got {count} pre-existing chunks — fixture reset is not firing."
        )

        backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
        backend.migrate()
        embedder = _CountingEmbedder(name="iso-fake")  # SAME NAME on purpose
        chunker = MarkdownChunker(max_chars=_MAX_CHARS, overlap=_OVERLAP)

        ds_rows = backend._execute(
            "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
            ("iso-dataset-2", "text"),
        )
        ds_id = ds_rows[0]["id"]

        doc_path = tmp_path / "iso.md"  # different tmp_path between tests anyway
        raw = _make_doc(doc_path, "vault://iso/note.md", _build_doc(12))
        ingest_one(backend, raw, chunker, [embedder], ds_id)

        encoded = sum(len(args[0]) for args, _ in embedder.call_args_list)
        assert encoded >= _MIN_INITIAL_CHUNKS, (
            f"Second ingest must also encode ≥{_MIN_INITIAL_CHUNKS} texts; "
            f"got {encoded}. Cross-test residue is leaking through fixture reset."
        )

    def test_third_run_still_clean(self, pg_dsn: str) -> None:
        """A read-only third test confirms the reset is stable across runs."""
        count = _chunks_count(pg_dsn)
        assert count in (-1, 0), (
            f"Third pg_dsn invocation should see clean schema; got {count} rows. "
            "Reset hook regressed."
        )
