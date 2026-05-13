"""B-16 — Dual-backend parametrized integration tests.

Pins the shared ``StorageBackend`` contract by running a representative slice
of behaviour against both the Postgres backend and the SQLite backend via the
``storage_backend`` parametrize fixture (values: ``postgres``, ``sqlite``).

Execution expectations
----------------------
- With Docker up:   all tests run 2x (once per backend) -> all green.
- Without Docker:   ``[postgres]`` ids are SKIPPED; ``[sqlite]`` ids pass.

Deliberately excluded from ``pytestmark = pytest.mark.integration``
--------------------------------------------------------------------
Unlike the single-backend suites that need Postgres, this file must NOT carry
``pytestmark = pytest.mark.integration``.  The hook in ``conftest.py`` would
skip ALL items in the ``tests.integration`` namespace when Docker is absent,
defeating the "SQLite always available" requirement.  The Postgres skip is
handled inside the ``storage_backend`` fixture via an explicit ``pytest.skip``
call instead.

Do NOT add P1-30..P1-32 (sync E2E) tests here — those are Postgres-only by
product decision (B-14 validator enforces this at config-construction time).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from corpus_forge.backends.base import StorageBackend
from corpus_forge.chunkers.base import MarkdownChunker
from corpus_forge.embedders.base import BaseEmbedder
from corpus_forge.ingest import ingest_one
from corpus_forge.sources.base import RawDocument

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FAKE_EMBEDDER_DIM = 8
_FAKE_EMBEDDER_NAME = "dual-fake"

# Chunker parameters matching test_chunk_reuse_e2e.py
_MAX_CHARS = 400
_OVERLAP = 20

_MIN_INITIAL_CHUNKS = 10
_MIN_REUSED = 7
_MAX_NEW_ENCODES = 3

# ---------------------------------------------------------------------------
# FakeEmbedder (stable, deterministic — copied from test_chunk_reuse_e2e.py)
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    """Deterministic embedder: sha256(text) → unit-normalised float32 vector.

    Satisfies the ``Embedder`` protocol (``name``, ``provider``, ``model_id``,
    ``dimension``, ``normalized``, ``distance``, ``encode``, ``warmup``).
    Spy attributes ``call_count`` and ``call_args_list`` mirror the MagicMock
    API used in unit tests.
    """

    name: str = _FAKE_EMBEDDER_NAME
    provider: str = "fake"
    model_id: str = "fake-dual-v1"
    dimension: int = _FAKE_EMBEDDER_DIM
    normalized: bool = True
    distance: str = "cosine"

    def __init__(self) -> None:
        self.call_count: int = 0
        self.call_args_list: list[tuple[tuple, dict]] = []

    def _encode_one(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = np.frombuffer(digest[:_FAKE_EMBEDDER_DIM], dtype=np.uint8).astype(np.float32)
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
# Dataset helpers (backend-agnostic)
# ---------------------------------------------------------------------------

# _PARA: ~229-char body used to build multi-section docs that produce ≥10 chunks.
_PARA = (
    "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim "
    "veniam quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea "
    "commodo consequat. "
)


def _build_doc(n_sections: int) -> str:
    """Return a markdown string with *n_sections* distinct heading+paragraph pairs."""
    parts: list[str] = []
    for i in range(1, n_sections + 1):
        para = _PARA + f"[section-{i}]"
        parts.append(f"## Section {i}\n\n{para}")
    return "\n\n".join(parts)


def _sample_raw_doc(
    source_uri: str = "dual://test.md",
    content_hash: str = "abc123",
    text: str = "# Hello\n\nDual backend test content.",
    title: str = "Dual Test",
) -> RawDocument:
    return RawDocument(
        source_uri=source_uri,
        content_hash=content_hash,
        text=text,
        title=title,
        modified_at=1000.0,
        metadata={},
        labels=[],
    )


def _fake_embedder(name: str = _FAKE_EMBEDDER_NAME, dim: int = _FAKE_EMBEDDER_DIM) -> BaseEmbedder:
    return BaseEmbedder(
        name=name,
        provider="sentence_transformers",
        model_id="test/dual-model",
        dimension=dim,
        normalized=True,
        distance="cosine",
    )


def _insert_dataset(backend: StorageBackend, name: str, kind: str = "text") -> int:
    """Backend-agnostic dataset insert using the ``_execute`` helper present on both backends."""
    # Both PostgresBackend and SQLiteBackend expose _execute() with compatible signatures.
    # Postgres uses %s placeholders; SQLite uses ?.
    # We detect which dialect to use by checking the backend class name.
    backend_cls = type(backend).__name__
    if backend_cls == "PostgresBackend":
        rows = backend._execute(
            "INSERT INTO corpus.datasets (name, kind) VALUES (%s, %s) RETURNING id",
            (name, kind),
        )
    else:
        rows = backend._execute(
            "INSERT INTO datasets (name, kind) VALUES (?, ?) RETURNING id",
            (name, kind),
        )
    return rows[0]["id"]


# ---------------------------------------------------------------------------
# TestUpsertDocumentSmoke
# ---------------------------------------------------------------------------


class TestUpsertDocumentSmoke:
    """Smoke: upsert_document + register_embedder + write_embeddings end-to-end."""

    def test_upsert_document_returns_int(self, storage_backend: StorageBackend) -> None:
        """upsert_document must return a positive integer document id."""
        ds_id = _insert_dataset(storage_backend, "dual-upsert-int")
        doc = _sample_raw_doc()
        doc_id = storage_backend.upsert_document(
            ds_id, doc, [("Hello", "Dual backend test content.")]
        )
        assert isinstance(doc_id, int)
        assert doc_id > 0

    def test_upsert_document_unchanged_skips(self, storage_backend: StorageBackend) -> None:
        """Re-inserting with identical content_hash returns the same doc_id (short-circuit)."""
        ds_id = _insert_dataset(storage_backend, "dual-upsert-noop")
        doc = _sample_raw_doc()
        chunks = [("Hello", "Dual backend test content.")]
        id1 = storage_backend.upsert_document(ds_id, doc, chunks)
        id2 = storage_backend.upsert_document(ds_id, doc, chunks)
        assert id2 == id1

    def test_upsert_document_change_updates(self, storage_backend: StorageBackend) -> None:
        """Changing content_hash causes the document text to be updated."""
        ds_id = _insert_dataset(storage_backend, "dual-upsert-changed")
        doc1 = _sample_raw_doc(content_hash="hash-v1", title="Old", text="# Old\n\nOld content.")
        storage_backend.upsert_document(ds_id, doc1, [("Old", "Old content.")])

        doc2 = _sample_raw_doc(content_hash="hash-v2", title="New", text="# New\n\nNew content.")
        doc_id2 = storage_backend.upsert_document(ds_id, doc2, [("New", "New content.")])
        assert doc_id2 is not None and doc_id2 > 0

    def test_register_embedder_returns_int(self, storage_backend: StorageBackend) -> None:
        """register_embedder must return a positive integer id."""
        embedder = _fake_embedder(name="dual-reg-smoke")
        eid = storage_backend.register_embedder(embedder)
        assert isinstance(eid, int)
        assert eid > 0

    def test_register_embedder_idempotent(self, storage_backend: StorageBackend) -> None:
        """Registering the same embedder twice returns the same id."""
        e = _fake_embedder(name="dual-reg-idem")
        id1 = storage_backend.register_embedder(e)
        id2 = storage_backend.register_embedder(e)
        assert id1 == id2

    def test_write_embeddings_empty_noop(self, storage_backend: StorageBackend) -> None:
        """write_embeddings with an empty list must not raise."""
        e = _fake_embedder(name="dual-write-empty")
        eid = storage_backend.register_embedder(e)
        storage_backend.write_embeddings(eid, [])  # must not raise

    def test_chunks_missing_embedding_contract(self, storage_backend: StorageBackend) -> None:
        """chunks_missing_embedding excludes chunks that already have an embedding."""
        ds_id = _insert_dataset(storage_backend, "dual-missing-emb")
        e = _fake_embedder(name="dual-missing-e")
        eid = storage_backend.register_embedder(e)

        doc_a = _sample_raw_doc(
            source_uri="dual://a.md",
            content_hash="a1",
            text="# A\n\nA content.",
            title="A",
        )
        doc_b = _sample_raw_doc(
            source_uri="dual://b.md",
            content_hash="b1",
            text="# B\n\nB content.",
            title="B",
        )
        storage_backend.upsert_document(ds_id, doc_a, [("A", "A content.")])
        storage_backend.upsert_document(ds_id, doc_b, [("B", "B content.")])

        # Embed only doc_a's chunks
        all_missing = list(storage_backend.chunks_missing_embedding(eid))
        assert len(all_missing) >= 2, "Expected at least 2 unembedded chunks (one per doc)"

        # Embed the first chunk only
        first_chunk_id, _ = all_missing[0]
        vec = np.random.default_rng(42).random(_FAKE_EMBEDDER_DIM).astype(np.float32)
        storage_backend.write_embeddings(eid, [(first_chunk_id, vec)])

        remaining = list(storage_backend.chunks_missing_embedding(eid))
        remaining_ids = {cid for cid, _ in remaining}
        assert first_chunk_id not in remaining_ids, (
            "After write_embeddings, embedded chunk should not appear in missing list"
        )

    def test_end_to_end_ingest_via_ingest_one(
        self, storage_backend: StorageBackend, tmp_path: Path
    ) -> None:
        """ingest_one wires upsert_document + register_embedder + write_embeddings together."""
        ds_id = _insert_dataset(storage_backend, "dual-ingest-e2e")
        chunker = MarkdownChunker(max_chars=_MAX_CHARS, overlap=_OVERLAP)
        embedder = _FakeEmbedder()

        doc_path = tmp_path / "note.md"
        doc_text = _build_doc(3)
        doc_path.write_text(doc_text, encoding="utf-8")

        doc_hash = hashlib.sha256(doc_text.encode()).hexdigest()
        raw = RawDocument(
            source_uri="dual://ingest-e2e/note.md",
            content_hash=doc_hash,
            text=doc_text,
            title="Dual E2E",
            modified_at=0.0,
            metadata={},
            labels=[],
        )

        # ingest_one must not raise on either backend
        ingest_one(storage_backend, raw, chunker, [embedder], ds_id)

        # Embedder must have been called at least once (real encoding happened)
        assert embedder.call_count >= 1, (
            f"Expected at least one encode() call during ingest_one — got {embedder.call_count}"
        )

        # After ingest, chunks_missing_embedding must return 0 for this embedder
        eid = storage_backend.register_embedder(embedder)
        still_missing = list(storage_backend.chunks_missing_embedding(eid))
        assert len(still_missing) == 0, (
            f"Expected 0 chunks missing embedding after ingest_one, "
            f"got {len(still_missing)}: {still_missing[:3]}"
        )


# ---------------------------------------------------------------------------
# TestChunkReuseE2E (dual-backend analog of test_chunk_reuse_e2e.py)
# ---------------------------------------------------------------------------


class TestChunkReuseE2E:
    """Pins the chunk-embedding-reuse contract on both backends.

    Analog of ``tests/integration/test_chunk_reuse_e2e.py::TestChunkReuseE2E``
    but parametrized via ``storage_backend``.  The single-backend Postgres
    version in ``test_chunk_reuse_e2e.py`` is kept; this class pins the same
    contract on SQLite and verifies it holds on Postgres too.
    """

    def test_chunk_reuse_e2e(self, storage_backend: StorageBackend, tmp_path: Path) -> None:
        """Append one section to a 12-section doc: ≥7 embeddings reused, ≤3 new encodes."""
        embedder = _FakeEmbedder()
        chunker = MarkdownChunker(max_chars=_MAX_CHARS, overlap=_OVERLAP)
        ds_id = _insert_dataset(storage_backend, "dual-reuse-e2e")

        # ── First ingest ────────────────────────────────────────────────────
        doc_path = tmp_path / "note.md"
        doc_text = _build_doc(12)
        doc_path.write_text(doc_text, encoding="utf-8")
        source_uri = f"dual://reuse-e2e/{doc_path.name}"

        doc_hash_1 = hashlib.sha256(doc_text.encode()).hexdigest()
        raw1 = RawDocument(
            source_uri=source_uri,
            content_hash=doc_hash_1,
            text=doc_text,
            title="Dual Reuse E2E",
            modified_at=0.0,
            metadata={},
            labels=[],
        )
        ingest_one(storage_backend, raw1, chunker, [embedder], ds_id)
        first_pass_call_count = embedder.call_count
        first_pass_arg_count = sum(len(args[0]) for args, _ in embedder.call_args_list)
        assert first_pass_arg_count >= _MIN_INITIAL_CHUNKS, (
            f"First pass should have encoded >={_MIN_INITIAL_CHUNKS} texts, "
            f"got {first_pass_arg_count}"
        )

        eid = storage_backend.register_embedder(embedder)
        missing_after_first = list(storage_backend.chunks_missing_embedding(eid))
        assert len(missing_after_first) == 0, (
            f"Expected 0 missing after first ingest, got {len(missing_after_first)}"
        )

        # ── Append one tail section ─────────────────────────────────────────
        new_section = "\n\n## Section 99\n\n" + _PARA + "[section-99]"
        new_text = doc_text + new_section
        doc_path.write_text(new_text, encoding="utf-8")

        doc_hash_2 = hashlib.sha256(new_text.encode()).hexdigest()
        assert doc_hash_2 != doc_hash_1, "Content hash must change after append"
        raw2 = RawDocument(
            source_uri=source_uri,
            content_hash=doc_hash_2,
            text=new_text,
            title="Dual Reuse E2E",
            modified_at=1.0,
            metadata={},
            labels=[],
        )
        ingest_one(storage_backend, raw2, chunker, [embedder], ds_id)

        # ── Assert: encoder called ≤3 additional times ──────────────────────
        second_pass_args = embedder.call_args_list[first_pass_call_count:]
        texts_encoded_second = sum(len(args[0]) for args, _ in second_pass_args)
        assert texts_encoded_second <= _MAX_NEW_ENCODES, (
            f"Expected <={_MAX_NEW_ENCODES} new encodes on second pass, "
            f"got {texts_encoded_second}. "
            "Reuse path should have copied embeddings for unchanged chunks."
        )

        # ── Assert: 0 missing after second ingest ───────────────────────────
        missing_after_second = list(storage_backend.chunks_missing_embedding(eid))
        assert len(missing_after_second) == 0, (
            f"Expected 0 missing after second ingest, got {len(missing_after_second)}"
        )

    def test_reuse_skips_encode_for_identical_reingest(
        self, storage_backend: StorageBackend, tmp_path: Path
    ) -> None:
        """Re-ingesting the exact same document must not call encode() again."""
        embedder = _FakeEmbedder()
        chunker = MarkdownChunker(max_chars=_MAX_CHARS, overlap=_OVERLAP)
        ds_id = _insert_dataset(storage_backend, "dual-reuse-noop")

        doc_text = _build_doc(12)
        doc_hash = hashlib.sha256(doc_text.encode()).hexdigest()
        raw = RawDocument(
            source_uri="dual://reuse-noop/note.md",
            content_hash=doc_hash,
            text=doc_text,
            title="Dual Reuse Noop",
            modified_at=0.0,
            metadata={},
            labels=[],
        )

        ingest_one(storage_backend, raw, chunker, [embedder], ds_id)
        count_after_first = embedder.call_count

        # Second ingest of identical document — content_hash unchanged → should
        # short-circuit without calling encode() again.
        ingest_one(storage_backend, raw, chunker, [embedder], ds_id)
        count_after_second = embedder.call_count

        assert count_after_second == count_after_first, (
            "Re-ingesting an unchanged document must not call encode() again. "
            f"call_count before={count_after_first}, after={count_after_second}."
        )


# ---------------------------------------------------------------------------
# TestRevisions
# ---------------------------------------------------------------------------


class TestRevisions:
    """Pins the revision API contract on both backends.

    Tests: insert_revision returns monotonic numbers; latest_revision reads
    back the highest-numbered revision; pending_remote_revisions filters by
    host and by last_pulled_revision_id.
    """

    def _setup_doc(self, backend: StorageBackend, ds_name: str, source_uri: str) -> tuple[int, int]:
        """Return (dataset_id, document_id)."""
        ds_id = _insert_dataset(backend, ds_name)
        doc_id = backend.upsert_document(
            ds_id,
            _sample_raw_doc(source_uri=source_uri, content_hash="rev-setup"),
            [("R", "Revision content.")],
        )
        return ds_id, doc_id

    def test_insert_revision_returns_id_and_number(self, storage_backend: StorageBackend) -> None:
        """insert_revision must return a dict with 'id' and 'revision_number' keys."""
        _ds_id, doc_id = self._setup_doc(storage_backend, "dual-rev-insert", "dual://rev1.md")
        with storage_backend.lock_source("dual://rev1.md"):
            rev = storage_backend.insert_revision(
                document_id=doc_id,
                source_uri="dual://rev1.md",
                content_hash="rh1",
                text="first revision",
                parent_revision_id=None,
                author_host="host-a",
                is_tombstone=False,
            )
        assert "id" in rev
        assert "revision_number" in rev
        assert isinstance(rev["id"], int) and rev["id"] > 0
        assert rev["revision_number"] == 1

    def test_insert_revision_monotonic(self, storage_backend: StorageBackend) -> None:
        """Each successive insert_revision must produce a strictly larger revision_number."""
        _ds_id, doc_id = self._setup_doc(storage_backend, "dual-rev-mono", "dual://rev2.md")
        with storage_backend.lock_source("dual://rev2.md"):
            r1 = storage_backend.insert_revision(
                document_id=doc_id,
                source_uri="dual://rev2.md",
                content_hash="rm1",
                text="v1",
                parent_revision_id=None,
                author_host="host-a",
                is_tombstone=False,
            )
        with storage_backend.lock_source("dual://rev2.md"):
            r2 = storage_backend.insert_revision(
                document_id=doc_id,
                source_uri="dual://rev2.md",
                content_hash="rm2",
                text="v2",
                parent_revision_id=r1["id"],
                author_host="host-a",
                is_tombstone=False,
            )
        assert r2["revision_number"] == r1["revision_number"] + 1

    def test_latest_revision_reads_back_highest(self, storage_backend: StorageBackend) -> None:
        """latest_revision must return the revision with the highest revision_number."""
        _ds_id, doc_id = self._setup_doc(storage_backend, "dual-rev-latest", "dual://rev3.md")
        with storage_backend.lock_source("dual://rev3.md"):
            r1 = storage_backend.insert_revision(
                document_id=doc_id,
                source_uri="dual://rev3.md",
                content_hash="rl1",
                text="v1",
                parent_revision_id=None,
                author_host="host-a",
                is_tombstone=False,
            )
        with storage_backend.lock_source("dual://rev3.md"):
            r2 = storage_backend.insert_revision(
                document_id=doc_id,
                source_uri="dual://rev3.md",
                content_hash="rl2",
                text="v2",
                parent_revision_id=r1["id"],
                author_host="host-a",
                is_tombstone=False,
            )
        latest = storage_backend.latest_revision(doc_id)
        assert latest is not None
        assert latest["revision_number"] == r2["revision_number"]
        assert latest["id"] == r2["id"]

    def test_latest_revision_none_when_no_revisions(self, storage_backend: StorageBackend) -> None:
        """latest_revision must return None if no revisions have been inserted."""
        _ds_id, doc_id = self._setup_doc(storage_backend, "dual-rev-none", "dual://rev-none.md")
        result = storage_backend.latest_revision(doc_id)
        assert result is None

    def test_pending_remote_revisions_filters_self_host(
        self, storage_backend: StorageBackend
    ) -> None:
        """pending_remote_revisions must exclude revisions authored by self_host."""
        ds_id, doc_id = self._setup_doc(storage_backend, "dual-pend-self", "dual://pend-self.md")
        with storage_backend.lock_source("dual://pend-self.md"):
            storage_backend.insert_revision(
                document_id=doc_id,
                source_uri="dual://pend-self.md",
                content_hash="ps1",
                text="self authored",
                parent_revision_id=None,
                author_host="host-self",
                is_tombstone=False,
            )
        pending = storage_backend.pending_remote_revisions(
            dataset_id=ds_id,
            last_pulled_revision_id=None,
            self_host="host-self",
        )
        assert pending == [], (
            "pending_remote_revisions must return [] when all revisions are self-authored"
        )

    def test_pending_remote_revisions_returns_remote(self, storage_backend: StorageBackend) -> None:
        """pending_remote_revisions must include revisions from other hosts."""
        ds_id, doc_id = self._setup_doc(
            storage_backend, "dual-pend-remote", "dual://pend-remote.md"
        )
        with storage_backend.lock_source("dual://pend-remote.md"):
            rev = storage_backend.insert_revision(
                document_id=doc_id,
                source_uri="dual://pend-remote.md",
                content_hash="pr1",
                text="from remote",
                parent_revision_id=None,
                author_host="host-b",
                is_tombstone=False,
            )
        pending = storage_backend.pending_remote_revisions(
            dataset_id=ds_id,
            last_pulled_revision_id=None,
            self_host="host-a",
        )
        assert len(pending) == 1
        assert pending[0]["id"] == rev["id"]

    def test_pending_remote_revisions_respects_last_pulled(
        self, storage_backend: StorageBackend
    ) -> None:
        """pending_remote_revisions must only return revisions with id > last_pulled."""
        ds_id, doc_id = self._setup_doc(storage_backend, "dual-pend-ptr", "dual://pend-ptr.md")
        with storage_backend.lock_source("dual://pend-ptr.md"):
            r1 = storage_backend.insert_revision(
                document_id=doc_id,
                source_uri="dual://pend-ptr.md",
                content_hash="pp1",
                text="v1",
                parent_revision_id=None,
                author_host="host-b",
                is_tombstone=False,
            )
            r2 = storage_backend.insert_revision(
                document_id=doc_id,
                source_uri="dual://pend-ptr.md",
                content_hash="pp2",
                text="v2",
                parent_revision_id=r1["id"],
                author_host="host-b",
                is_tombstone=False,
            )

        # Only r2 should appear when we've already pulled r1
        pending = storage_backend.pending_remote_revisions(
            dataset_id=ds_id,
            last_pulled_revision_id=r1["id"],
            self_host="host-a",
        )
        assert len(pending) == 1
        assert pending[0]["id"] == r2["id"]


# ---------------------------------------------------------------------------
# TestRetrievalSurface (R1) — dense/lexical search, chunk lookup, dataset list,
# lexical backfill. Pins the StorageBackend retrieval contract on both backends.
# ---------------------------------------------------------------------------


def _norm_vec_from_seed(seed: int, dim: int = _FAKE_EMBEDDER_DIM) -> np.ndarray:
    """Deterministic unit-norm float32 vector for a given seed (cosine-friendly)."""
    rng = np.random.default_rng(seed)
    v = rng.random(dim).astype(np.float32)
    n = float(np.linalg.norm(v))
    return v / n


def _seed_doc_with_chunks(
    backend: StorageBackend,
    dataset_id: int,
    source_uri: str,
    title: str,
    chunk_texts: list[str],
    content_hash: str | None = None,
) -> int:
    """Upsert a document with the given chunk texts. Returns doc_id."""
    text = "\n\n".join(chunk_texts)
    raw = RawDocument(
        source_uri=source_uri,
        content_hash=content_hash or hashlib.sha256(text.encode()).hexdigest(),
        text=text,
        title=title,
        modified_at=0.0,
        metadata={},
        labels=[],
    )
    chunks = [(None, ct) for ct in chunk_texts]
    return backend.upsert_document(dataset_id, raw, chunks)


class TestSearchDense:
    """search_dense returns top-k Hits ordered by similarity (higher score = closer)."""

    def test_search_dense_returns_topk(self, storage_backend: StorageBackend) -> None:
        ds_id = _insert_dataset(storage_backend, "dual-sd-topk")
        embedder = _fake_embedder(name="dual-sd-topk-e", dim=_FAKE_EMBEDDER_DIM)
        eid = storage_backend.register_embedder(embedder)

        _seed_doc_with_chunks(
            storage_backend,
            ds_id,
            "dual://sd-topk.md",
            "SD Topk",
            ["alpha alpha alpha", "beta beta beta", "gamma gamma gamma"],
        )

        # Embed each chunk with a known vector keyed by chunk_id.
        missing = list(storage_backend.chunks_missing_embedding(eid))
        assert len(missing) == 3
        # Write three distinct vectors; chunk[0]'s vector is exactly the query
        target_chunk_id = missing[0][0]
        target_vec = _norm_vec_from_seed(1)
        pairs = [
            (missing[0][0], target_vec),
            (missing[1][0], _norm_vec_from_seed(2)),
            (missing[2][0], _norm_vec_from_seed(3)),
        ]
        storage_backend.write_embeddings(eid, pairs)

        hits = storage_backend.search_dense(eid, target_vec, k=3)
        assert isinstance(hits, list)
        assert len(hits) == 3
        # The top hit must be the chunk whose vector equals the query
        assert hits[0].chunk_id == target_chunk_id
        # All hits have source="dense"
        for h in hits:
            assert h.source == "dense", f"Expected source=dense, got {h.source!r}"
        # Scores are monotonically non-increasing (top first)
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True), f"Hits not ordered by score: {scores}"
        # The exact-match hit has the highest score
        assert hits[0].score >= hits[1].score

    def test_search_dense_filters_by_dataset(self, storage_backend: StorageBackend) -> None:
        ds_a = _insert_dataset(storage_backend, "dual-sd-a")
        ds_b = _insert_dataset(storage_backend, "dual-sd-b")
        embedder = _fake_embedder(name="dual-sd-filter-e", dim=_FAKE_EMBEDDER_DIM)
        eid = storage_backend.register_embedder(embedder)

        _seed_doc_with_chunks(
            storage_backend, ds_a, "dual://sd-a.md", "A", ["a-chunk-1", "a-chunk-2"]
        )
        _seed_doc_with_chunks(storage_backend, ds_b, "dual://sd-b.md", "B", ["b-chunk-1"])

        missing = list(storage_backend.chunks_missing_embedding(eid))
        vec = _norm_vec_from_seed(42)
        storage_backend.write_embeddings(eid, [(cid, vec) for cid, _ in missing])

        hits = storage_backend.search_dense(eid, vec, k=10, dataset_id=ds_a)
        chunk_dataset_ids = {h.dataset_id for h in hits}
        assert chunk_dataset_ids == {ds_a}, (
            f"Dataset filter failed: expected only dataset_id={ds_a}, got {chunk_dataset_ids}"
        )


class TestSearchLexical:
    """search_lexical returns FTS hits with source='lexical'."""

    def test_search_lexical_matches_phrase(self, storage_backend: StorageBackend) -> None:
        ds_id = _insert_dataset(storage_backend, "dual-sl-phrase")
        _seed_doc_with_chunks(
            storage_backend,
            ds_id,
            "dual://sl-phrase.md",
            "SL Phrase",
            [
                "the quick brown fox jumps over the lazy dog",
                "an alpaca grazes on the grass",
                "elephants never forget their friends",
            ],
        )

        hits = storage_backend.search_lexical("brown fox", k=10)
        assert isinstance(hits, list)
        assert len(hits) >= 1, "Expected at least one lexical hit for 'brown fox'"
        for h in hits:
            assert h.source == "lexical"
        # The top hit must be the quick-brown-fox chunk
        top = hits[0]
        assert "brown" in top.text.lower() and "fox" in top.text.lower()
        # Scores in [0, 1] for both dialects (sqlite uses 1/(1+bm25); postgres
        # uses ts_rank_cd which is already small-but-positive). Just assert non-neg.
        for h in hits:
            assert h.score >= 0.0

    def test_search_lexical_excludes_other_datasets(self, storage_backend: StorageBackend) -> None:
        ds_a = _insert_dataset(storage_backend, "dual-sl-a")
        ds_b = _insert_dataset(storage_backend, "dual-sl-b")
        _seed_doc_with_chunks(storage_backend, ds_a, "dual://sl-a.md", "A", ["alpha keyword alpha"])
        _seed_doc_with_chunks(storage_backend, ds_b, "dual://sl-b.md", "B", ["beta keyword beta"])

        hits_a = storage_backend.search_lexical("keyword", k=10, dataset_id=ds_a)
        assert len(hits_a) == 1
        assert hits_a[0].dataset_id == ds_a
        assert "alpha" in hits_a[0].text

        hits_b = storage_backend.search_lexical("keyword", k=10, dataset_id=ds_b)
        assert len(hits_b) == 1
        assert hits_b[0].dataset_id == ds_b

    def test_search_lexical_respects_k(self, storage_backend: StorageBackend) -> None:
        ds_id = _insert_dataset(storage_backend, "dual-sl-k")
        _seed_doc_with_chunks(
            storage_backend,
            ds_id,
            "dual://sl-k.md",
            "K",
            [f"keyword text number {i}" for i in range(5)],
        )
        hits = storage_backend.search_lexical("keyword", k=2)
        assert len(hits) <= 2, f"Expected <=2 hits with k=2, got {len(hits)}"


class TestGetChunk:
    def test_get_chunk_returns_joined_document_metadata(
        self, storage_backend: StorageBackend
    ) -> None:
        ds_id = _insert_dataset(storage_backend, "dual-gc-joined")
        _seed_doc_with_chunks(
            storage_backend,
            ds_id,
            "dual://gc.md",
            "GC Title",
            ["chunk text content"],
        )
        # Find the chunk id via search_lexical or list
        hits = storage_backend.search_lexical("chunk", k=5, dataset_id=ds_id)
        assert len(hits) >= 1
        cid = hits[0].chunk_id

        row = storage_backend.get_chunk(cid)
        assert row is not None
        assert row["id"] == cid
        assert "chunk" in row["text"]
        assert row["source_uri"] == "dual://gc.md"
        assert row["title"] == "GC Title"

    def test_get_chunk_returns_none_for_missing(self, storage_backend: StorageBackend) -> None:
        result = storage_backend.get_chunk(999_999_999)
        assert result is None


class TestListDatasets:
    def test_list_datasets_counts(self, storage_backend: StorageBackend) -> None:
        ds_a = _insert_dataset(storage_backend, "dual-ld-a")
        ds_b = _insert_dataset(storage_backend, "dual-ld-b")
        _seed_doc_with_chunks(storage_backend, ds_a, "dual://ld-a.md", "A", ["c1", "c2", "c3"])
        _seed_doc_with_chunks(storage_backend, ds_b, "dual://ld-b.md", "B", ["c1", "c2"])

        rows = storage_backend.list_datasets()
        names = {r["name"] for r in rows}
        assert {"dual-ld-a", "dual-ld-b"}.issubset(names)
        by_name = {r["name"]: r for r in rows}
        assert by_name["dual-ld-a"]["chunk_count"] == 3
        assert by_name["dual-ld-b"]["chunk_count"] == 2
        assert by_name["dual-ld-a"]["document_count"] == 1
        assert by_name["dual-ld-b"]["document_count"] == 1
        # kind echoed back
        assert by_name["dual-ld-a"]["kind"] == "text"

    def test_list_datasets_empty(self, storage_backend: StorageBackend) -> None:
        """Empty (or freshly migrated) db: no datasets — must not error."""
        rows = storage_backend.list_datasets()
        assert isinstance(rows, list)


class TestBackfillLexicalIndex:
    """backfill_lexical_index is idempotent.

    On SQLite: returns the rowcount of rows inserted into chunks_fts (≥ N on
    first call, 0 thereafter).
    On Postgres: returns 0 always (GENERATED column auto-populates).
    """

    def test_backfill_returns_int(self, storage_backend: StorageBackend) -> None:
        result = storage_backend.backfill_lexical_index()
        assert isinstance(result, int)
        assert result >= 0

    def test_backfill_idempotent(self, storage_backend: StorageBackend) -> None:
        ds_id = _insert_dataset(storage_backend, "dual-bf-idem")
        _seed_doc_with_chunks(
            storage_backend,
            ds_id,
            "dual://bf.md",
            "BF",
            ["alpha", "beta", "gamma"],
        )
        # First call: may or may not backfill (depends on whether triggers
        # already mirrored on insert). Postgres returns 0. SQLite returns 0
        # because the ai trigger already populated chunks_fts at INSERT time.
        first = storage_backend.backfill_lexical_index()
        second = storage_backend.backfill_lexical_index()
        # Second call must be 0 — backfill is "rows not already in chunks_fts"
        assert second == 0, (
            f"Second backfill call must be 0 (idempotent); first={first}, second={second}"
        )


# ---------------------------------------------------------------------------
# TestHybridSearch (R2) — HybridRetriever against real seeded corpus on both
# backends. Pins that the R2 retriever fuses dense + lexical hits, emits
# source="fused", and resolves dataset filters end-to-end.
# ---------------------------------------------------------------------------


def _norm_vec_from_text(text: str, dim: int = _FAKE_EMBEDDER_DIM) -> np.ndarray:
    """Deterministic unit-norm float32 vector keyed by the text content.

    Mirrors `_FakeEmbedder._encode_one` but is a free function so the
    HybridRetriever's `encode_query` path lands on the same vector that
    the seeded chunk's embedding occupies.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    vec = np.frombuffer(digest[:dim], dtype=np.uint8).astype(np.float32)
    vec = (vec + 1.0) / 256.0
    norm = np.linalg.norm(vec)
    return vec / norm


class TestHybridSearch:
    """Pin the R2 HybridRetriever contract on both backends."""

    def test_hybrid_search_returns_fused_hits(self, storage_backend: StorageBackend) -> None:
        """Hybrid search returns ranked fused Hits over a real seeded corpus.

        Seed three chunks; embed each with its own deterministic vector;
        construct a HybridRetriever whose embedder encodes the query to match
        the first chunk's vector; verify:
        - The returned list has length <= k.
        - All hits have source="fused".
        - The top-1 hit's text contains the query word.
        """
        from corpus_forge.retrieval import HybridRetriever
        from corpus_forge.retrieval.types import SearchOptions

        ds_id = _insert_dataset(storage_backend, "dual-hybrid-fused")
        embedder = _FakeEmbedder()
        eid = storage_backend.register_embedder(embedder)

        chunk_texts = [
            "the quick brown fox jumps over the lazy dog",
            "the alpaca grazes on the sweet grass",
            "elephants never forget their old friends",
        ]
        _seed_doc_with_chunks(
            storage_backend,
            ds_id,
            "dual://hybrid-fused.md",
            "HybridFused",
            chunk_texts,
        )

        # Embed each chunk with a vector keyed by its text.
        missing = list(storage_backend.chunks_missing_embedding(eid))
        assert len(missing) == 3
        # Order missing by chunk id so we can pin which chunk gets which vector.
        missing.sort(key=lambda t: t[0])
        chunk_ids = [cid for cid, _ in missing]
        chunk_vecs = [_norm_vec_from_text(t) for t in chunk_texts]
        storage_backend.write_embeddings(eid, list(zip(chunk_ids, chunk_vecs, strict=True)))

        # The HybridRetriever asks the embedder to encode the query.  Wire
        # the embedder so encode_query returns the vector of the first chunk
        # — and the lexical match also lands on it ("brown fox").
        target_vec = chunk_vecs[0]
        target_chunk_id = chunk_ids[0]

        class _QueryEmbedder(_FakeEmbedder):
            def encode_query(
                self,
                texts: Sequence[str],
                *,
                batch_size: int = 32,
            ) -> np.ndarray:
                # Always return target_vec replicated, regardless of the query
                # text — keeps the test agnostic to the model.
                return np.stack([target_vec for _ in texts])

        retriever = HybridRetriever(
            backend=storage_backend,
            embedder=_QueryEmbedder(),
            embedder_id=eid,
        )

        out = retriever.search("brown fox", SearchOptions(k=3, fusion="rrf"))
        assert isinstance(out, list)
        assert len(out) >= 1
        for h in out:
            assert h.source == "fused", f"Expected source=fused, got {h.source!r}"

        # The top-1 hit must be the "brown fox" chunk — it wins both lists.
        assert out[0].chunk_id == target_chunk_id

    def test_hybrid_search_alpha_fusion_blends_scores(
        self, storage_backend: StorageBackend
    ) -> None:
        """Alpha fusion respects the alpha knob: high alpha → dense winner."""
        from corpus_forge.retrieval import HybridRetriever
        from corpus_forge.retrieval.types import SearchOptions

        ds_id = _insert_dataset(storage_backend, "dual-hybrid-alpha")
        embedder = _FakeEmbedder()
        eid = storage_backend.register_embedder(embedder)

        chunk_texts = [
            "first chunk has unique keyword aardvark",
            "second chunk has unique keyword baobab",
            "third chunk has unique keyword cicada",
        ]
        _seed_doc_with_chunks(
            storage_backend,
            ds_id,
            "dual://hybrid-alpha.md",
            "HybridAlpha",
            chunk_texts,
        )

        missing = list(storage_backend.chunks_missing_embedding(eid))
        missing.sort(key=lambda t: t[0])
        chunk_ids = [cid for cid, _ in missing]
        chunk_vecs = [_norm_vec_from_text(t) for t in chunk_texts]
        storage_backend.write_embeddings(eid, list(zip(chunk_ids, chunk_vecs, strict=True)))

        # We want the dense vector to favour chunk 0 but the lexical query to
        # favour chunk 1 ("baobab").
        dense_target_vec = chunk_vecs[0]
        dense_target_id = chunk_ids[0]
        lexical_target_id = chunk_ids[1]

        class _DenseTargetEmbedder(_FakeEmbedder):
            def encode_query(
                self,
                texts: Sequence[str],
                *,
                batch_size: int = 32,
            ) -> np.ndarray:
                return np.stack([dense_target_vec for _ in texts])

        retriever = HybridRetriever(
            backend=storage_backend,
            embedder=_DenseTargetEmbedder(),
            embedder_id=eid,
        )

        # alpha=1.0 → dense only → chunk 0 wins
        out_dense = retriever.search(
            "baobab", SearchOptions(k=1, fusion="alpha", alpha=1.0)
        )
        assert len(out_dense) == 1
        assert out_dense[0].chunk_id == dense_target_id

        # alpha=0.0 → lexical only → "baobab" chunk (chunk 1) wins
        out_lex = retriever.search(
            "baobab", SearchOptions(k=1, fusion="alpha", alpha=0.0)
        )
        assert len(out_lex) == 1
        assert out_lex[0].chunk_id == lexical_target_id

    def test_hybrid_search_dataset_filter(self, storage_backend: StorageBackend) -> None:
        """HybridRetriever resolves dataset name → id and filters both lists."""
        from corpus_forge.retrieval import HybridRetriever
        from corpus_forge.retrieval.types import SearchOptions

        ds_a = _insert_dataset(storage_backend, "dual-hybrid-ds-a")
        ds_b = _insert_dataset(storage_backend, "dual-hybrid-ds-b")
        # Register the names via get_or_create_dataset semantics — the
        # backend's find_dataset_id_by_name uses datasets.name as the lookup
        # key, so the names we inserted above are addressable.
        # (No-op; just exercising the lookup.)

        embedder = _FakeEmbedder()
        eid = storage_backend.register_embedder(embedder)

        _seed_doc_with_chunks(
            storage_backend,
            ds_a,
            "dual://h-a.md",
            "A",
            ["alpha keyword alpha"],
        )
        _seed_doc_with_chunks(
            storage_backend,
            ds_b,
            "dual://h-b.md",
            "B",
            ["beta keyword beta"],
        )

        missing = list(storage_backend.chunks_missing_embedding(eid))
        vec = _norm_vec_from_seed(7)
        storage_backend.write_embeddings(eid, [(cid, vec) for cid, _ in missing])

        class _StaticEmbedder(_FakeEmbedder):
            def encode_query(
                self,
                texts: Sequence[str],
                *,
                batch_size: int = 32,
            ) -> np.ndarray:
                return np.stack([vec for _ in texts])

        retriever = HybridRetriever(
            backend=storage_backend,
            embedder=_StaticEmbedder(),
            embedder_id=eid,
        )

        out_a = retriever.search(
            "keyword", SearchOptions(k=5, dataset="dual-hybrid-ds-a")
        )
        out_b = retriever.search(
            "keyword", SearchOptions(k=5, dataset="dual-hybrid-ds-b")
        )

        for h in out_a:
            assert h.dataset_id == ds_a, (
                f"dataset filter leaked: expected {ds_a}, got {h.dataset_id}"
            )
        for h in out_b:
            assert h.dataset_id == ds_b, (
                f"dataset filter leaked: expected {ds_b}, got {h.dataset_id}"
            )
