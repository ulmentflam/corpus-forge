"""Phase F follow-up — fixture-corpus chunk-reuse landing test.

The wave's E2E test (``test_chunk_reuse_e2e``) verifies the rechunk
plumbing + idempotency on a *single* document. This test exercises the
*aggregate* `% chunk-reuse` metric across a multi-document fixture
corpus: ingest several markdown files, append a small section to
exactly one of them, re-ingest the whole corpus, and assert that at
least 70 % of the total chunks across the corpus were reused via the
``content_hash`` short-circuit.

References:
- ``.planning/tdd/phase_f_cdc_chunking.md`` — the unchecked acceptance
  item this test discharges.
- ``tests/integration/test_chunk_reuse_e2e.py`` — single-doc sibling
  whose helpers (``_build_doc``, ``FakeEmbedder``, ``_make_backend``)
  this test mirrors structurally.
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

# ---------------------------------------------------------------------------
# Constants — matched to test_chunk_reuse_e2e for boundary-shift stability
# ---------------------------------------------------------------------------

FAKE_EMBEDDER_NAME = "fake_embedder_corpus"  # distinct table per test
FAKE_DIMENSION = 8

_MAX_CHARS = 400
_OVERLAP = 20

# Fixture corpus: 4 docs x 5 sections each = 20 chunks initially.
_N_DOCS = 4
_SECTIONS_PER_DOC = 5

# Reuse threshold: >= 70 % of the post-edit total must come from the
# pre-edit snapshot. With 4x5=20 chunks and one ~1-chunk append we
# expect roughly 20/21 ~= 95 % reuse — well above the contractual 70 %.
_MIN_REUSE_PERCENT = 70


# ---------------------------------------------------------------------------
# Fake embedder — copy of the single-doc sibling, distinct name only
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """Deterministic embedder identical to the single-doc sibling.

    Uses a distinct ``name`` so the embeddings table doesn't collide
    with ``test_chunk_reuse_e2e``'s when both tests share a database.
    """

    name: str = FAKE_EMBEDDER_NAME
    provider: str = "fake"
    model_id: str = "fake-corpus-v1"
    dimension: int = FAKE_DIMENSION
    normalized: bool = True
    distance: str = "cosine"

    def __init__(self) -> None:
        self.call_count: int = 0
        self.call_args_list: list[tuple[tuple, dict]] = []

    def _encode_one(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = np.frombuffer(digest[:FAKE_DIMENSION], dtype=np.uint8).astype(np.float32)
        vec = (vec + 1.0) / 256.0
        norm = np.linalg.norm(vec)
        return vec / norm

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        args: tuple = (list(texts),)
        kwargs: dict = {"batch_size": batch_size}
        self.call_args_list.append((args, kwargs))
        self.call_count += 1
        return np.stack([self._encode_one(t) for t in texts])

    def warmup(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Doc + corpus helpers
# ---------------------------------------------------------------------------

_PARA = (
    "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim "
    "veniam quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea "
    "commodo consequat. "
)


def _build_doc(doc_id: int, n_sections: int) -> str:
    """Return a markdown string with content unique per ``doc_id``.

    Each section has a `[doc-X-section-Y]` suffix so cross-doc hash
    collisions are impossible.
    """
    parts: list[str] = []
    for i in range(1, n_sections + 1):
        para = _PARA + f"[doc-{doc_id}-section-{i}]"
        parts.append(f"## Doc {doc_id} — Section {i}\n\n{para}")
    return "\n\n".join(parts)


def _make_backend(pg_dsn: str) -> PostgresBackend:
    b = PostgresBackend(dsn=pg_dsn, schema="corpus")
    b.migrate()
    return b


def _make_raw_doc(path: Path, source_uri: str, *, title: str) -> RawDocument:
    text = path.read_text(encoding="utf-8")
    doc_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return RawDocument(
        source_uri=source_uri,
        content_hash=doc_hash,
        text=text,
        title=title,
        modified_at=0.0,
        metadata={},
        labels=[],
    )


def _read_embedding_snapshot(pg_dsn: str, embedder_name: str) -> list[dict]:
    safe_name = embedder_name.replace("-", "_")
    table = f"corpus.embeddings_{safe_name}"
    query = f"""
        SELECT c.id AS chunk_id,
               c.content_hash,
               e.embedding::text AS embedding_text
        FROM corpus.chunks c
        JOIN {table} e ON e.chunk_id = c.id
        ORDER BY c.id
    """
    with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(query)
        rows = cur.fetchall()
    return [{"chunk_id": r[0], "content_hash": r[1], "embedding_text": r[2]} for r in rows]


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


class TestChunkReuseFixtureCorpus:
    """>= 70 % chunk reuse across a multi-doc corpus after a small edit."""

    def test_corpus_reuse_threshold(self, pg_dsn: str, tmp_path: Path) -> None:
        """Edit one doc in a 4-doc corpus; assert >= 70 % of chunks survive.

        Sets up a deterministic fixture corpus of 4 markdown docs (5
        sections each), ingests them, appends a single ~1-chunk section
        to doc 2 only, re-ingests the whole corpus, and computes the
        reuse ratio as
        ``|second_hashes ∩ first_hashes| / |second_hashes|``.
        """
        backend = _make_backend(pg_dsn)
        embedder = FakeEmbedder()
        chunker = MarkdownChunker(max_chars=_MAX_CHARS, overlap=_OVERLAP)

        dataset_rows = backend._execute(
            "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
            ("fixture-corpus-reuse-dataset", "text"),
        )
        dataset_id: int = dataset_rows[0]["id"]

        # ── Build + ingest the initial corpus ─────────────────────────
        doc_paths: list[Path] = []
        original_texts: list[str] = []
        for d in range(1, _N_DOCS + 1):
            doc_path = tmp_path / f"note-{d}.md"
            text = _build_doc(d, _SECTIONS_PER_DOC)
            doc_path.write_text(text, encoding="utf-8")
            doc_paths.append(doc_path)
            original_texts.append(text)

        for d, doc_path in enumerate(doc_paths, start=1):
            source_uri = f"vault://fixture-corpus-test/doc-{d}/{doc_path.name}"
            raw = _make_raw_doc(doc_path, source_uri, title=f"Doc {d}")
            ingest_one(backend, raw, chunker, [embedder], dataset_id)

        first_snapshot = _read_embedding_snapshot(pg_dsn, embedder.name)
        # Sanity: corpus is non-trivial.
        assert len(first_snapshot) >= _N_DOCS * 3, (
            f"Initial corpus snapshot too small: {len(first_snapshot)} chunks. "
            f"Check _build_doc() and MarkdownChunker parameters."
        )

        first_hashes: set[str] = {
            r["content_hash"] for r in first_snapshot if r["content_hash"] is not None
        }

        # ── Edit exactly one doc (doc 2) with a small append ──────────
        edit_index = 1  # 0-based → doc 2
        new_section = "\n\n## Doc 2 — Section 99\n\n" + _PARA + "[doc-2-section-99]"
        edited_path = doc_paths[edit_index]
        edited_path.write_text(original_texts[edit_index] + new_section, encoding="utf-8")

        # ── Re-ingest the whole corpus ────────────────────────────────
        for d, doc_path in enumerate(doc_paths, start=1):
            source_uri = f"vault://fixture-corpus-test/doc-{d}/{doc_path.name}"
            raw = _make_raw_doc(doc_path, source_uri, title=f"Doc {d}")
            ingest_one(backend, raw, chunker, [embedder], dataset_id)

        second_snapshot = _read_embedding_snapshot(pg_dsn, embedder.name)
        second_hashes_present: list[str] = [
            r["content_hash"] for r in second_snapshot if r["content_hash"] is not None
        ]

        # ── Compute reuse ratio ──────────────────────────────────────
        # A chunk is "reused" if its post-edit content_hash already
        # existed in the pre-edit snapshot.
        reused = sum(1 for h in second_hashes_present if h in first_hashes)
        total = len(second_hashes_present)

        assert total > 0, "Second-pass snapshot is empty — corpus ingest broke"
        reuse_percent = (reused / total) * 100

        assert reuse_percent >= _MIN_REUSE_PERCENT, (
            f"Expected >= {_MIN_REUSE_PERCENT}% chunk reuse across the corpus, "
            f"got {reuse_percent:.1f}% ({reused}/{total}). "
            f"First snapshot: {len(first_snapshot)} chunks; "
            f"second snapshot: {len(second_snapshot)} chunks."
        )
