"""Phase G (G-14) — SQLite backend image-embedding helpers.

Postgres unit-level tests live alongside the existing
``test_postgres_backend_helpers.py`` integration suite; this file
exercises the SQLite path which can run without Docker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.sources.base import RawDocument


@pytest.fixture
def backend(tmp_path: Path) -> SQLiteBackend:
    db_path = tmp_path / "test.db"
    b = SQLiteBackend(path=str(db_path))
    b.migrate()
    return b


def _seed_image_document(backend: SQLiteBackend) -> int:
    """Insert a document with a chunk labelled ``format=image``.

    Returns the chunk_id.
    """
    dataset_id = backend.get_or_create_dataset("d", "text", "")
    doc = RawDocument(
        source_uri="filesystem://root/image.png",
        content_hash="hash1",
        text="alt text",
        title="img",
        modified_at=0.0,
        metadata={"image_path": "/nonexistent/image.png", "byte_count": 4},
        labels=[("format", "image")],
    )
    doc_id = backend.upsert_document(dataset_id, doc, chunks=[("h", "image markdown text")])
    chunk_rows = backend._execute("SELECT id FROM chunks WHERE document_id = ?", (doc_id,))
    return chunk_rows[0]["id"]


def _seed_text_document(backend: SQLiteBackend) -> int:
    dataset_id = backend.get_or_create_dataset("d", "text", "")
    doc = RawDocument(
        source_uri="filesystem://root/note.md",
        content_hash="hash2",
        text="some text",
        title="note",
        modified_at=0.0,
        metadata={},
        labels=[("format", "markdown")],
    )
    doc_id = backend.upsert_document(dataset_id, doc, chunks=[("h", "some text body")])
    chunk_rows = backend._execute("SELECT id FROM chunks WHERE document_id = ?", (doc_id,))
    return chunk_rows[0]["id"]


# ── register_multimodal_embedder ───────────────────────────────────────


def test_register_multimodal_embedder_creates_row_and_table(backend: SQLiteBackend) -> None:
    emb_id = backend.register_multimodal_embedder(
        name="clip_local", model_id="clip-ViT-B-32", dimension=512
    )
    assert emb_id > 0
    rows = backend._execute("SELECT name, image, table_name FROM embedders WHERE id = ?", (emb_id,))
    assert rows[0]["name"] == "clip_local"
    assert rows[0]["image"] == 1
    assert rows[0]["table_name"] == "image_embeddings_clip_local"
    # Table exists.
    table_rows = backend._execute(
        "SELECT name FROM sqlite_master WHERE name = 'image_embeddings_clip_local'"
    )
    assert len(table_rows) == 1


def test_register_multimodal_idempotent(backend: SQLiteBackend) -> None:
    first = backend.register_multimodal_embedder(name="x", model_id="m1", dimension=8)
    second = backend.register_multimodal_embedder(name="x", model_id="m2", dimension=8)
    assert first == second
    rows = backend._execute("SELECT model_id FROM embedders WHERE id = ?", (first,))
    assert rows[0]["model_id"] == "m2"


def test_register_sanitises_hyphenated_name(backend: SQLiteBackend) -> None:
    emb_id = backend.register_multimodal_embedder(name="my-cool-model", model_id="m", dimension=4)
    rows = backend._execute("SELECT table_name FROM embedders WHERE id = ?", (emb_id,))
    assert rows[0]["table_name"] == "image_embeddings_my_cool_model"


# ── write_image_embeddings ─────────────────────────────────────────────


def test_write_image_embeddings_persists(backend: SQLiteBackend) -> None:
    emb_id = backend.register_multimodal_embedder(name="clip_local", model_id="m", dimension=4)
    chunk_id = _seed_image_document(backend)
    backend.write_image_embeddings(emb_id, [(chunk_id, [0.1, 0.2, 0.3, 0.4])])
    rows = backend._execute(
        "SELECT chunk_id FROM image_embeddings_clip_local WHERE chunk_id = ?", (chunk_id,)
    )
    assert len(rows) == 1


def test_write_image_embeddings_empty_no_op(backend: SQLiteBackend) -> None:
    emb_id = backend.register_multimodal_embedder(name="clip_local", model_id="m", dimension=4)
    backend.write_image_embeddings(emb_id, [])
    rows = backend._execute("SELECT chunk_id FROM image_embeddings_clip_local")
    assert rows == []


def test_write_image_embeddings_unknown_embedder_raises(backend: SQLiteBackend) -> None:
    with pytest.raises(ValueError, match=r"(?i)not found"):
        backend.write_image_embeddings(9999, [(1, [0.0] * 4)])


def test_write_image_embeddings_replace_semantics(backend: SQLiteBackend) -> None:
    """Second write to the same chunk_id overwrites the first."""
    emb_id = backend.register_multimodal_embedder(name="clip_local", model_id="m", dimension=4)
    chunk_id = _seed_image_document(backend)
    backend.write_image_embeddings(emb_id, [(chunk_id, [0.1, 0.2, 0.3, 0.4])])
    backend.write_image_embeddings(emb_id, [(chunk_id, [0.5, 0.6, 0.7, 0.8])])
    rows = backend._execute(
        "SELECT COUNT(*) AS n FROM image_embeddings_clip_local WHERE chunk_id = ?", (chunk_id,)
    )
    assert rows[0]["n"] == 1


# ── image_chunks_missing_embedding ─────────────────────────────────────


def test_missing_yields_image_labeled_chunks_only(backend: SQLiteBackend) -> None:
    emb_id = backend.register_multimodal_embedder(name="clip_local", model_id="m", dimension=4)
    image_chunk = _seed_image_document(backend)
    _seed_text_document(backend)

    out = list(backend.image_chunks_missing_embedding(emb_id))
    chunk_ids = [cid for cid, _ in out]
    assert image_chunk in chunk_ids
    # Text chunk must not appear (no format=image label).
    assert len(out) == 1


def test_missing_skips_already_embedded(backend: SQLiteBackend) -> None:
    emb_id = backend.register_multimodal_embedder(name="clip_local", model_id="m", dimension=4)
    chunk_id = _seed_image_document(backend)
    backend.write_image_embeddings(emb_id, [(chunk_id, [0.1, 0.2, 0.3, 0.4])])
    out = list(backend.image_chunks_missing_embedding(emb_id))
    assert out == []


def test_missing_returns_metadata_dict(backend: SQLiteBackend) -> None:
    emb_id = backend.register_multimodal_embedder(name="clip_local", model_id="m", dimension=4)
    _seed_image_document(backend)
    out = list(backend.image_chunks_missing_embedding(emb_id))
    assert len(out) == 1
    _cid, meta = out[0]
    # Metadata flows through from upsert_document's chunk metadata; per
    # current SQLiteBackend code the chunks.metadata is per-chunk
    # (defaults to {}), so the dict at least has a text fallback.
    assert isinstance(meta, dict)
    assert "text" in meta


def test_missing_unknown_embedder_returns_empty(backend: SQLiteBackend) -> None:
    out = list(backend.image_chunks_missing_embedding(9999))
    assert out == []
