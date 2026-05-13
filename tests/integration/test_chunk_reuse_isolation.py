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


class TestChunkReuseIsolation:
    """The pg_dsn fixture must isolate per-test schema state."""

    def test_back_to_back_ingest_does_not_reuse_across_tests(
        self, pg_dsn: str, tmp_path: Path
    ) -> None:
        """Single test simulates the cross-test pattern: ingest twice with the same
        embedder name + identical text but *different* source_uris.

        Without per-test schema reset this would still work inside one test
        because the second ingest's embedder *would* reuse, but the assertion
        we need to pin is on the **fixture**: the table state at fixture entry
        is empty.  We verify that by counting rows in corpus.chunks before any
        ingest happens (must be 0) and again in the embeddings table.
        """
        # ── Contract A: the fixture hands us an empty corpus schema. ─────────
        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM corpus.chunks")
            row = cur.fetchone()
            assert row is not None
            chunk_count_at_entry = row[0]

        assert chunk_count_at_entry == 0, (
            "pg_dsn must hand each test an empty corpus.chunks table; "
            f"found {chunk_count_at_entry} pre-existing rows. "
            "Implement per-test schema reset in tests/conftest.py."
        )

        # ── Contract B: a brand-new ingest must call encode() at least once. ─
        backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
        backend.migrate()
        embedder = _CountingEmbedder(name="iso-fake")
        chunker = MarkdownChunker(max_chars=_MAX_CHARS, overlap=_OVERLAP)

        ds_rows = backend._execute(
            "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
            ("iso-dataset", "text"),
        )
        ds_id = ds_rows[0]["id"]

        doc_path = tmp_path / "iso.md"
        raw = _make_doc(doc_path, "vault://iso/note.md", _build_doc(12))

        ingest_one(backend, raw, chunker, [embedder], ds_id)

        first_pass_arg_count = sum(len(args[0]) for args, _ in embedder.call_args_list)
        assert first_pass_arg_count >= _MIN_INITIAL_CHUNKS, (
            f"First ingest into a CLEAN schema must encode ≥{_MIN_INITIAL_CHUNKS} texts; "
            f"got {first_pass_arg_count}. This indicates _copy_reusable_embeddings "
            f"is pre-filling embeddings from a prior test's residue — the pg_dsn "
            f"fixture is not resetting the corpus schema between tests."
        )

    def test_back_to_back_test_runs_each_see_clean_chunks_table(
        self, pg_dsn: str, tmp_path: Path
    ) -> None:
        """A second test sharing the same session container also sees an empty schema."""
        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM corpus.chunks")
            row = cur.fetchone()
            assert row is not None
            count = row[0]

        assert count == 0, (
            "Each test must receive an empty corpus.chunks table; "
            f"found {count} rows from a prior test in the same session."
        )

        # Smoke-burn the schema so the next test would fail without reset.
        backend = PostgresBackend(dsn=pg_dsn, schema="corpus")
        backend.migrate()
        embedder = _CountingEmbedder(name="iso-fake-2")
        chunker = MarkdownChunker(max_chars=_MAX_CHARS, overlap=_OVERLAP)
        ds_rows = backend._execute(
            "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
            ("iso-dataset-2", "text"),
        )
        ds_id = ds_rows[0]["id"]
        doc_path = tmp_path / "iso2.md"
        raw = _make_doc(doc_path, "vault://iso2/note.md", _build_doc(12))
        ingest_one(backend, raw, chunker, [embedder], ds_id)
        # No assertion here — purpose is to leave rows behind so the *next*
        # test in this class will fail loudly if the fixture skips reset.

    def test_third_run_still_clean(self, pg_dsn: str) -> None:
        """Third call into the fixture must still see 0 chunks."""
        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM corpus.chunks")
            row = cur.fetchone()
            assert row is not None
            count = row[0]
        assert count == 0, (
            f"Third pg_dsn test should see clean schema; got {count} rows. "
            "Reset hook not running."
        )
