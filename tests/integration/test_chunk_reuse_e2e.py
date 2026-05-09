"""E2E integration test: chunk-embedding reuse after a small append.

P0-08 — Wave 13b.

Production code for P0-03..P0-07 is already shipped; this is a
characterisation/pin test that verifies the reuse pipeline works end-to-end
against a real (testcontainers) Postgres database.

Test contract (from tasks.md S P0-08 acceptance):
- Ingest a markdown doc that chunks to >=10 chunks.
- Capture all (content_hash, embedding bytes) pairs after the first ingest.
- Append a single paragraph (<=1 new tail chunk).  Re-ingest.
- Assert: >=7 of the original chunks' embeddings survived verbatim.
- Encoder-spy: the number of texts the embedder is asked to encode on the
  second pass is <=3.
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
# Constants (extracted to avoid PLR2004 magic-value warnings)
# ---------------------------------------------------------------------------

FAKE_EMBEDDER_NAME = "fake_embedder"
FAKE_DIMENSION = 8

# Chunker parameters: max_chars wide enough that each ~260-char section
# is treated as one unit; overlap kept small so boundary shift on append
# affects only 1-2 tail chunks.
_MAX_CHARS = 400
_OVERLAP = 20

# Reuse-threshold constants (contractual; from tasks.md acceptance)
_MIN_INITIAL_CHUNKS = 10  # the test doc must produce at least this many
_MIN_REUSED = 7  # >=70% reuse on a small append
_MAX_NEW_ENCODES = 3  # second pass encodes at most 3 new texts

# ---------------------------------------------------------------------------
# Fake embedder
# ---------------------------------------------------------------------------


class FakeEmbedder:
    """Deterministic embedder that records every encode() call.

    - ``encode`` is a real callable that logs invocations via ``call_args_list``
      and ``call_count``, matching the MagicMock spy API used in unit tests.
    - Embeddings are stable: sha256(text)[:dim] mapped to float32 via
      per-byte normalisation.  Non-zero guaranteed for any non-empty input.
    """

    name: str = FAKE_EMBEDDER_NAME
    provider: str = "fake"
    model_id: str = "fake-v1"
    dimension: int = FAKE_DIMENSION
    normalized: bool = True
    distance: str = "cosine"

    def __init__(self) -> None:
        self.call_count: int = 0
        self.call_args_list: list[tuple[tuple, dict]] = []

    def _encode_one(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # Map each byte to a float in (0, 1], guaranteed non-zero.
        vec = np.frombuffer(digest[:FAKE_DIMENSION], dtype=np.uint8).astype(np.float32)
        vec = (vec + 1.0) / 256.0  # range (1/256, 1]; never zero
        norm = np.linalg.norm(vec)
        return vec / norm  # unit-normalised

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        """Record the call and return stable deterministic embeddings."""
        args: tuple = (list(texts),)
        kwargs: dict = {"batch_size": batch_size}
        self.call_args_list.append((args, kwargs))
        self.call_count += 1
        return np.stack([self._encode_one(t) for t in texts])

    def warmup(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ~229 chars body; append `[section-N]` suffix = ~240 total per paragraph.
# With max_chars=400, each section (heading ~16 chars + "\n\n" + ~240 chars
# paragraph = ~258 chars) fits comfortably in one chunk boundary pass.
_PARA = (
    "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim "
    "veniam quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea "
    "commodo consequat. "
)


def _build_doc(n_sections: int) -> str:
    """Return a markdown string with *n_sections* distinct headings+paragraphs."""
    parts: list[str] = []
    for i in range(1, n_sections + 1):
        # Suffixed with section number so every section has a distinct hash.
        para = _PARA + f"[section-{i}]"
        parts.append(f"## Section {i}\n\n{para}")
    return "\n\n".join(parts)


def _make_backend(pg_dsn: str) -> PostgresBackend:
    b = PostgresBackend(dsn=pg_dsn, schema="corpus")
    b.migrate()
    return b


def _make_raw_doc(path: Path, source_uri: str) -> RawDocument:
    text = path.read_text(encoding="utf-8")
    doc_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return RawDocument(
        source_uri=source_uri,
        content_hash=doc_hash,
        text=text,
        title="E2E Reuse Test",
        modified_at=0.0,
        metadata={},
        labels=[],
    )


def _read_embedding_snapshot(pg_dsn: str, embedder_name: str) -> list[dict]:
    """Return all (chunk_id, content_hash, embedding text repr) rows."""
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


def _count_chunks(text: str, max_chars: int, overlap: int) -> int:
    chunker = MarkdownChunker(max_chars=max_chars, overlap=overlap)
    return len(chunker.chunk(text))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestChunkReuseE2E:
    """End-to-end chunk-embedding reuse against a testcontainers Postgres."""

    def test_chunk_count_prediction(self) -> None:
        """Sanity check: the document we build actually produces >=10 chunks."""
        doc_text = _build_doc(12)
        count = _count_chunks(doc_text, _MAX_CHARS, _OVERLAP)
        assert count >= _MIN_INITIAL_CHUNKS, (
            f"Expected >={_MIN_INITIAL_CHUNKS} chunks from test doc, got {count}. "
            "Adjust _build_doc() or _MAX_CHARS."
        )

    def test_chunk_reuse_e2e(self, pg_dsn: str, tmp_path: Path) -> None:
        """Append one paragraph to a 12-section doc: >=7 embeddings reused, <=3 new encodes."""
        backend = _make_backend(pg_dsn)
        embedder = FakeEmbedder()
        chunker = MarkdownChunker(max_chars=_MAX_CHARS, overlap=_OVERLAP)

        # ── Create dataset ────────────────────────────────────────────────────
        dataset_rows = backend._execute(
            "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
            ("e2e-reuse-dataset", "text"),
        )
        dataset_id: int = dataset_rows[0]["id"]

        # ── Write initial document ───────────────────────────────────────────
        doc_path = tmp_path / "note.md"
        doc_text = _build_doc(12)
        doc_path.write_text(doc_text, encoding="utf-8")

        source_uri = f"vault://e2e-reuse-test/{doc_path.name}"
        raw = _make_raw_doc(doc_path, source_uri)

        # ── First ingest ──────────────────────────────────────────────────────
        ingest_one(backend, raw, chunker, [embedder], dataset_id)

        initial_rows = _read_embedding_snapshot(pg_dsn, embedder.name)
        assert len(initial_rows) >= _MIN_INITIAL_CHUNKS, (
            f"Expected >={_MIN_INITIAL_CHUNKS} embedded chunks after first ingest, "
            f"got {len(initial_rows)}. "
            "Check _build_doc() and MarkdownChunker parameters."
        )

        first_pass_call_count = embedder.call_count
        first_pass_arg_count = sum(len(args[0]) for args, _ in embedder.call_args_list)
        assert first_pass_arg_count >= _MIN_INITIAL_CHUNKS, (
            f"First pass should have encoded >={_MIN_INITIAL_CHUNKS} texts, "
            f"got {first_pass_arg_count}."
        )

        # ── Append a single new tail section ─────────────────────────────────
        new_section = "\n\n## Section 99\n\n" + _PARA + "[section-99]"
        doc_path.write_text(doc_text + new_section, encoding="utf-8")

        raw2 = _make_raw_doc(doc_path, source_uri)
        # Sanity: content_hash changed (so ingest_one won't short-circuit).
        assert raw2.content_hash != raw.content_hash

        # ── Second ingest ─────────────────────────────────────────────────────
        ingest_one(backend, raw2, chunker, [embedder], dataset_id)

        second_rows = _read_embedding_snapshot(pg_dsn, embedder.name)

        # ── Assert reuse threshold ───────────────────────────────────────────
        # Map content_hash → embedding_text for first-pass snapshot.
        initial_by_hash: dict[str, str] = {
            r["content_hash"]: r["embedding_text"]
            for r in initial_rows
            if r["content_hash"] is not None
        }

        reused = sum(
            1
            for r in second_rows
            if r["content_hash"] is not None
            and r["content_hash"] in initial_by_hash
            and r["embedding_text"] == initial_by_hash[r["content_hash"]]
        )

        assert reused >= _MIN_REUSED, (
            f"Expected >={_MIN_REUSED} reused embeddings, got {reused}. "
            f"Initial snapshot: {len(initial_rows)} rows. "
            f"Second snapshot: {len(second_rows)} rows."
        )

        # ── Assert encoder spy ────────────────────────────────────────────────
        second_pass_args = embedder.call_args_list[first_pass_call_count:]
        texts_encoded_on_second_pass = sum(len(args[0]) for args, _ in second_pass_args)

        assert texts_encoded_on_second_pass <= _MAX_NEW_ENCODES, (
            f"Expected <={_MAX_NEW_ENCODES} texts encoded on second pass, "
            f"got {texts_encoded_on_second_pass}. "
            "The reuse path should have copied embeddings for unchanged chunks."
        )

    def test_reuse_skips_encode_for_identical_reingest(self, pg_dsn: str, tmp_path: Path) -> None:
        """Re-ingesting the exact same document encodes 0 new texts (content_hash short-circuit)."""
        backend = _make_backend(pg_dsn)
        embedder = FakeEmbedder()
        chunker = MarkdownChunker(max_chars=_MAX_CHARS, overlap=_OVERLAP)

        dataset_rows = backend._execute(
            "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
            ("e2e-reuse-noop-dataset", "text"),
        )
        dataset_id: int = dataset_rows[0]["id"]

        doc_path = tmp_path / "noop.md"
        doc_path.write_text(_build_doc(12), encoding="utf-8")
        source_uri = f"vault://e2e-reuse-noop/{doc_path.name}"

        raw = _make_raw_doc(doc_path, source_uri)
        ingest_one(backend, raw, chunker, [embedder], dataset_id)
        count_after_first = embedder.call_count

        # Re-ingest exact same doc — content_hash unchanged → ingest_one
        # short-circuits before encoding anything new.
        ingest_one(backend, raw, chunker, [embedder], dataset_id)
        count_after_second = embedder.call_count

        assert count_after_second == count_after_first, (
            "Re-ingesting an unchanged document should not call encode() again. "
            f"encode call count before: {count_after_first}, after: {count_after_second}."
        )

    # ── FakeEmbedder self-tests (no Docker needed) ────────────────────────────

    def test_fake_embedder_is_deterministic(self) -> None:
        """FakeEmbedder returns identical vectors for the same text."""
        embedder = FakeEmbedder()
        text = "hello world"
        v1 = embedder.encode([text])
        v2 = embedder.encode([text])
        np.testing.assert_array_equal(v1, v2)

    def test_fake_embedder_vectors_are_nonzero(self) -> None:
        """FakeEmbedder never returns a zero vector (production may filter zeros)."""
        embedder = FakeEmbedder()
        texts = [f"chunk-{i}" for i in range(5)]
        vecs = embedder.encode(texts)
        for i, vec in enumerate(vecs):
            assert np.any(vec != 0.0), f"Vector for texts[{i}] is all-zero"

    def test_fake_embedder_distinct_texts_distinct_vectors(self) -> None:
        """Different texts produce different vectors."""
        embedder = FakeEmbedder()
        texts = [f"unique-text-{i}" for i in range(_MIN_INITIAL_CHUNKS)]
        vecs = embedder.encode(texts)
        seen: set[bytes] = set()
        for vec in vecs:
            key = vec.tobytes()
            assert key not in seen, "Two different texts produced identical vectors"
            seen.add(key)

    def test_fake_embedder_records_call_args(self) -> None:
        """call_args_list and call_count spy attributes work correctly."""
        embedder = FakeEmbedder()
        assert embedder.call_count == 0

        embedder.encode(["a", "b"])
        call_after_first = embedder.call_count
        assert call_after_first == 1
        assert len(embedder.call_args_list) == 1
        args, _kwargs = embedder.call_args_list[0]
        assert args[0] == ["a", "b"]

        embedder.encode(["c"])
        call_after_second = embedder.call_count
        assert call_after_second == call_after_first + 1
        assert len(embedder.call_args_list) == call_after_second
