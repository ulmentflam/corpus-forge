"""Integration tests for SQLiteBackend — B-15.

Mirrors ``tests/integration/test_backend.py`` (PostgresBackend suite) using
SQLite via ``tmp_path / "corpus.db"``.

Key differences from the Postgres suite:
- No testcontainers / Docker required.  sqlite3 is in Python stdlib.
- Direct DB assertions use ``sqlite3.connect(str(db_path))`` (not psycopg).
- Schema introspection uses ``sqlite_master`` / ``PRAGMA`` instead of
  ``information_schema``.
- JSON columns stored as TEXT — read back with ``json.loads()``.
- Timestamps stored as ISO-8601 TEXT.
- No ``pytestmark = pytest.mark.integration`` (these run unconditionally).
- vec0 (sqlite-vec) tests are gated on ``SQLITE_VEC_AVAILABLE``.
"""

import json
import sqlite3
import threading
from pathlib import Path

import numpy as np
import pytest

from corpus_forge.backends.sqlite import SQLiteBackend
from corpus_forge.backends.sqlite_vec_loader import SQLITE_VEC_AVAILABLE
from corpus_forge.embedders.base import BaseEmbedder
from corpus_forge.sources.base import RawConversation, RawDocument, RawMessage

# ---------------------------------------------------------------------------
# No pytestmark — runs without Docker.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(db_path: Path) -> SQLiteBackend:
    """Create a migrated SQLiteBackend."""
    backend = SQLiteBackend(path=str(db_path))
    backend.migrate()
    return backend


def _db(db_path: Path) -> sqlite3.Connection:
    """Open a raw sqlite3 connection for assertion-side reads."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _count(db_path: Path, table: str, where: str = "", params: tuple = ()) -> int:
    sql = f"SELECT COUNT(*) FROM {table}"
    if where:
        sql += f" WHERE {where}"
    conn = _db(db_path)
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0]
    finally:
        conn.close()


def _fake_embedder(name: str = "test-embed", dim: int = 8) -> BaseEmbedder:
    return BaseEmbedder(
        name=name,
        provider="sentence_transformers",
        model_id="test/model",
        dimension=dim,
        normalized=True,
        distance="cosine",
    )


def _sample_doc(
    source_uri: str = "vault://test.md",
    content_hash: str = "abc123",
    title: str = "Hello",
    text: str = "# Hello\n\nWorld content.",
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


def _sample_conv(
    source_uri: str = "claude-code://proj/session1",
    content_hash: str = "conv123",
    messages: list[RawMessage] | None = None,
) -> RawConversation:
    if messages is None:
        messages = [
            RawMessage(
                external_uuid="msg1",
                parent_uuid=None,
                role="user",
                content="Hello",
                tool_calls=None,
                tool_results=None,
                ts=1000.0,
                metadata={},
            ),
            RawMessage(
                external_uuid="msg2",
                parent_uuid="msg1",
                role="assistant",
                content="Hi!",
                tool_calls=None,
                tool_results=None,
                ts=1001.0,
                metadata={},
            ),
        ]
    return RawConversation(
        source_uri=source_uri,
        external_id=source_uri,
        content_hash=content_hash,
        title="Test Chat",
        started_at=1000.0,
        ended_at=1005.0,
        messages=messages,
        metadata={},
        labels=[],
    )


def _insert_dataset(backend: SQLiteBackend, name: str, kind: str = "text") -> int:
    rows = backend._execute(
        "INSERT INTO datasets (name, kind) VALUES (?, ?) RETURNING id",
        (name, kind),
    )
    return rows[0]["id"]


# ---------------------------------------------------------------------------
# TestMigrate
# ---------------------------------------------------------------------------


class TestMigrate:
    """mirror of TestMigrate in test_backend.py"""

    def test_creates_all_tables(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)

        conn = _db(db_path)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            tables = {r[0] for r in rows}
        finally:
            conn.close()

        expected = {
            "datasets",
            "sources",
            "documents",
            "conversations",
            "messages",
            "chunks",
            "embedders",
            "labels",
            "chunk_labels",
            "document_labels",
            "conversation_labels",
            "document_revisions",
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    def test_idempotent_migrate(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = SQLiteBackend(path=str(db_path))
        backend.migrate()
        backend.migrate()  # must not raise

    def test_chunks_doc_idx_created(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        _make_backend(db_path)
        conn = _db(db_path)
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name = 'chunks_doc_idx'"
            ).fetchall()
            assert rows, "chunks_doc_idx index not found"
        finally:
            conn.close()

    def test_unique_constraint_on_documents(self, tmp_path: Path) -> None:
        """documents(dataset_id, source_uri) UNIQUE prevents duplicates."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_migrate_uniq")

        backend._execute(
            "INSERT INTO documents (dataset_id, source_uri, content_hash, text)"
            " VALUES (?, 'test://dup', 'h1', 'content')",
            (ds_id,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            backend._execute(
                "INSERT INTO documents (dataset_id, source_uri, content_hash, text)"
                " VALUES (?, 'test://dup', 'h2', 'content2')",
                (ds_id,),
            )

    def test_foreign_keys_on_per_connection(self, tmp_path: Path) -> None:
        """backend connections have PRAGMA foreign_keys = ON."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        with backend._get_connection() as conn:
            fk = conn.execute("PRAGMA foreign_keys").fetchone()
            assert fk[0] == 1


# ---------------------------------------------------------------------------
# TestRegisterEmbedder
# ---------------------------------------------------------------------------


class TestRegisterEmbedder:
    """mirror of TestEmbedderOps.test_register_embedder* in test_backend.py"""

    def test_register_embedder_returns_int(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        embedder_id = backend.register_embedder(_fake_embedder())
        assert isinstance(embedder_id, int)
        assert embedder_id > 0

    def test_register_embedder_inserts_row(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        eid = backend.register_embedder(_fake_embedder(name="test-embed-row"))

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT name, provider, dimension FROM embedders WHERE id = ?",
                (eid,),
            ).fetchone()
            assert row is not None
            assert row["name"] == "test-embed-row"
            assert row["provider"] == "sentence_transformers"
            assert row["dimension"] == 8
        finally:
            conn.close()

    def test_register_embedder_creates_embedding_table(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        backend.register_embedder(_fake_embedder(name="test-embed-tbl"))

        conn = _db(db_path)
        try:
            # Table name is embeddings_<sanitized name>
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow') "
                "AND name = 'embeddings_test_embed_tbl'"
            ).fetchone()
            assert row is not None, "embeddings_test_embed_tbl not found in sqlite_master"
        finally:
            conn.close()

    def test_register_embedder_idempotent(self, tmp_path: Path) -> None:
        """Re-registering same embedder returns same id."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        e = _fake_embedder(name="idem-embed")
        id1 = backend.register_embedder(e)
        id2 = backend.register_embedder(e)
        assert id1 == id2

    def test_register_embedder_update_on_collision(self, tmp_path: Path) -> None:
        """Re-registering with different dimension updates the row in-place."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)

        e1 = BaseEmbedder(
            name="update-embed",
            provider="sentence_transformers",
            model_id="model-v1",
            dimension=8,
            normalized=True,
            distance="cosine",
        )
        eid = backend.register_embedder(e1)

        e2 = BaseEmbedder(
            name="update-embed",
            provider="sentence_transformers",
            model_id="model-v2",
            dimension=16,
            normalized=True,
            distance="cosine",
        )
        eid2 = backend.register_embedder(e2)

        assert eid == eid2  # same row

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT model_id, dimension FROM embedders WHERE id = ?",
                (eid,),
            ).fetchone()
            assert row["model_id"] == "model-v2"
            assert row["dimension"] == 16
        finally:
            conn.close()

    def test_hyphen_in_name_sanitized(self, tmp_path: Path) -> None:
        """Embedder name with hyphens is sanitized for table name."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        e = _fake_embedder(name="my-test-embed")
        backend.register_embedder(e)

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'shadow') "
                "AND name = 'embeddings_my_test_embed'"
            ).fetchone()
            assert row is not None, "embeddings_my_test_embed not found"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# TestUpsertDocument
# ---------------------------------------------------------------------------


class TestUpsertDocument:
    """mirror of TestDatasetOps upsert_document tests in test_backend.py"""

    def test_upsert_document_returns_int(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_upsert_int")

        doc_id = backend.upsert_document(ds_id, _sample_doc(), [("Hello", "World content.")])
        assert isinstance(doc_id, int)
        assert doc_id > 0

    def test_upsert_document_creates_chunks(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_upsert_chunks")

        doc_id = backend.upsert_document(ds_id, _sample_doc(), [("Hello", "World content.")])

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM chunks WHERE document_id = ?",
                (doc_id,),
            ).fetchone()
            assert row["cnt"] == 1
        finally:
            conn.close()

    def test_upsert_document_unchanged_skips_chunks(self, tmp_path: Path) -> None:
        """Re-inserting with same content_hash returns same doc_id (no-op path)."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_upsert_noop")

        doc = _sample_doc()
        chunks = [("Hello", "World content.")]
        id1 = backend.upsert_document(ds_id, doc, chunks)
        id2 = backend.upsert_document(ds_id, doc, chunks)
        assert id2 == id1

    def test_upsert_document_changed_updates_text(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_upsert_changed")

        doc1 = _sample_doc(content_hash="hash1", title="Old", text="# Old\n\nOld content.")
        backend.upsert_document(ds_id, doc1, [("Old", "Old content.")])

        doc2 = _sample_doc(content_hash="hash2", title="New", text="# New\n\nNew content.")
        backend.upsert_document(ds_id, doc2, [("New", "New content.")])

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT text, title FROM documents WHERE source_uri = ?",
                (doc2.source_uri,),
            ).fetchone()
            assert row["text"] == "# New\n\nNew content."
            assert row["title"] == "New"
        finally:
            conn.close()

    def test_upsert_document_multiple_chunks(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_multi_chunk")

        doc = _sample_doc(
            source_uri="vault://multi.md",
            content_hash="multi_hash",
            text="# H1\n\nPara 1.\n\n# H2\n\nPara 2.",
        )
        chunks = [("H1", "Para 1."), ("H2", "Para 2.")]
        doc_id = backend.upsert_document(ds_id, doc, chunks)

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM chunks WHERE document_id = ?",
                (doc_id,),
            ).fetchone()
            assert row["cnt"] == 2
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# TestChunkReuse
# ---------------------------------------------------------------------------


class TestChunkReuse:
    """Chunk-hash-based embedding reuse on re-ingest."""

    def test_chunk_reuse_copies_embedding(self, tmp_path: Path) -> None:
        """When a chunk's content_hash matches a prior chunk, embeddings are copied."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        embedder = _fake_embedder(name="reuse-embed", dim=8)
        eid = backend.register_embedder(embedder)
        ds_id = _insert_dataset(backend, "ds_chunk_reuse")

        doc1 = _sample_doc(
            source_uri="vault://reuse.md",
            content_hash="hash_v1",
            text="# Stable\n\nStable content.",
        )
        chunks1 = [("Stable", "Stable content.")]
        doc_id = backend.upsert_document(ds_id, doc1, chunks1)

        # Write embedding for the original chunk
        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT id FROM chunks WHERE document_id = ? LIMIT 1",
                (doc_id,),
            ).fetchone()
            original_chunk_id = row["id"]
        finally:
            conn.close()

        embedding = np.ones(8, dtype=np.float32)
        backend.write_embeddings(eid, [(original_chunk_id, embedding)])

        # Re-ingest with same content + one extra new chunk
        doc2 = _sample_doc(
            source_uri="vault://reuse.md",
            content_hash="hash_v2",
            text="# Stable\n\nStable content.\n\n# New\n\nNew content.",
        )
        chunks2 = [("Stable", "Stable content."), ("New", "New content.")]
        backend.upsert_document(ds_id, doc2, chunks2, embedder_ids=[eid])

        # The stable chunk's embedding should now exist on the new chunk id too
        missing = list(backend.chunks_missing_embedding(eid))
        missing_ids = {cid for cid, _ in missing}

        # After re-ingest, stable content chunk should have an embedding
        # (either the original id was preserved, or the new one has a copied embedding).
        # At most 1 chunk (the new one) should be missing.
        assert len(missing_ids) <= 1, (
            f"Expected at most 1 chunk missing embedding, got {len(missing_ids)}: {missing_ids}"
        )


# ---------------------------------------------------------------------------
# TestUpsertConversation
# ---------------------------------------------------------------------------


class TestUpsertConversation:
    """mirror of TestDatasetOps.test_upsert_conversation in test_backend.py"""

    def test_upsert_conversation_returns_int(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_conv_int", kind="chat")

        conv = _sample_conv()
        chunked = [[("Hello", "Hello")], [("Hi!", "Hi!")]]
        conv_id = backend.upsert_conversation(ds_id, conv, chunked)
        assert isinstance(conv_id, int)
        assert conv_id > 0

    def test_upsert_conversation_creates_messages(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_conv_msgs", kind="chat")

        conv = _sample_conv()
        chunked = [[("Hello", "Hello")], [("Hi!", "Hi!")]]
        conv_id = backend.upsert_conversation(ds_id, conv, chunked)

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM messages WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()
            assert row["cnt"] == 2
        finally:
            conn.close()

    def test_upsert_conversation_creates_chunks(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_conv_chunks", kind="chat")

        conv = _sample_conv()
        chunked = [[("Hello", "Hello")], [("Hi!", "Hi!")]]
        conv_id = backend.upsert_conversation(ds_id, conv, chunked)

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM chunks WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()
            assert row["cnt"] == 2
        finally:
            conn.close()

    def test_upsert_conversation_unchanged_noop(self, tmp_path: Path) -> None:
        """Same content_hash → returns same conv_id without touching messages."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_conv_noop", kind="chat")

        conv = _sample_conv()
        chunked = [[("Hello", "Hello")], [("Hi!", "Hi!")]]
        id1 = backend.upsert_conversation(ds_id, conv, chunked)
        id2 = backend.upsert_conversation(ds_id, conv, chunked)
        assert id2 == id1

    def test_upsert_conversation_changed_replaces_messages(self, tmp_path: Path) -> None:
        """Changed content_hash → old messages deleted, new ones inserted."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_conv_change", kind="chat")

        conv1 = _sample_conv(content_hash="hash_a")
        chunked1 = [[("Hello", "Hello")], [("Hi!", "Hi!")]]
        conv_id = backend.upsert_conversation(ds_id, conv1, chunked1)

        conv2 = _sample_conv(
            content_hash="hash_b",
            messages=[
                RawMessage(
                    external_uuid="m1",
                    parent_uuid=None,
                    role="user",
                    content="New msg",
                    tool_calls=None,
                    tool_results=None,
                    ts=2000.0,
                    metadata={},
                ),
            ],
        )
        chunked2 = [[("New", "New msg")]]
        id2 = backend.upsert_conversation(ds_id, conv2, chunked2)
        assert id2 == conv_id

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM messages WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()
            assert row["cnt"] == 1, "Old messages should have been replaced"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# TestWriteEmbeddings
# ---------------------------------------------------------------------------


class TestWriteEmbeddings:
    """mirror of TestEmbedderOps.test_write_embeddings* in test_backend.py"""

    def test_write_embeddings_stores_vector(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        embedder = _fake_embedder(name="write-embed", dim=8)
        eid = backend.register_embedder(embedder)
        ds_id = _insert_dataset(backend, "ds_write_embed")

        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://embed.md", content_hash="e1"),
            [("Test", "Content for embedding.")],
        )

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT id FROM chunks WHERE document_id = ? LIMIT 1",
                (doc_id,),
            ).fetchone()
            chunk_id = row["id"]
        finally:
            conn.close()

        vec = np.random.randn(8).astype(np.float32)
        backend.write_embeddings(eid, [(chunk_id, vec)])

        # Verify row is present in the embedding table.
        # Use the backend's own connection so that sqlite-vec is loaded (vec0 tables
        # are invisible to a raw sqlite3.connect() that lacks the extension).
        safe_name = embedder.name.replace("-", "_")
        table = f"embeddings_{safe_name}"
        with backend._get_connection() as bconn:
            row = bconn.execute(
                f"SELECT chunk_id FROM {table} WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
        assert row is not None
        assert row["chunk_id"] == chunk_id

    def test_write_embeddings_empty_does_not_raise(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        embedder = _fake_embedder(name="empty-embed")
        eid = backend.register_embedder(embedder)
        backend.write_embeddings(eid, [])  # empty — must not raise

    def test_write_embeddings_unknown_embedder_raises(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        vec = np.ones(8, dtype=np.float32)
        with pytest.raises(ValueError, match="not found"):
            backend.write_embeddings(99999, [(1, vec)])

    def test_write_embeddings_idempotent(self, tmp_path: Path) -> None:
        """Writing the same embedding twice does not raise."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        embedder = _fake_embedder(name="idem-embed-w", dim=8)
        eid = backend.register_embedder(embedder)
        ds_id = _insert_dataset(backend, "ds_idem_embed")

        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://idem.md", content_hash="idem1"),
            [("Idem", "Idempotent content.")],
        )
        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT id FROM chunks WHERE document_id = ? LIMIT 1",
                (doc_id,),
            ).fetchone()
            chunk_id = row["id"]
        finally:
            conn.close()

        vec = np.ones(8, dtype=np.float32)
        backend.write_embeddings(eid, [(chunk_id, vec)])
        backend.write_embeddings(eid, [(chunk_id, vec)])  # second write must not raise

    @pytest.mark.skipif(not SQLITE_VEC_AVAILABLE, reason="sqlite-vec not installed")
    def test_write_embeddings_vec0_stores_vector(self, tmp_path: Path) -> None:
        """When sqlite-vec is available, embedding is stored in vec0 virtual table."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        embedder = _fake_embedder(name="vec0-embed", dim=8)
        eid = backend.register_embedder(embedder)
        ds_id = _insert_dataset(backend, "ds_vec0")

        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://vec0.md", content_hash="v0h"),
            [("Vec", "Vector content.")],
        )
        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT id FROM chunks WHERE document_id = ? LIMIT 1",
                (doc_id,),
            ).fetchone()
            chunk_id = row["id"]
        finally:
            conn.close()

        vec = np.random.randn(8).astype(np.float32)
        backend.write_embeddings(eid, [(chunk_id, vec)])

        safe_name = embedder.name.replace("-", "_")
        table = f"embeddings_{safe_name}"
        # Use backend connection so vec0 extension is loaded.
        with backend._get_connection() as bconn:
            row = bconn.execute(
                f"SELECT chunk_id FROM {table} WHERE chunk_id = ?",
                (chunk_id,),
            ).fetchone()
        assert row is not None


# ---------------------------------------------------------------------------
# TestChunksMissingEmbedding
# ---------------------------------------------------------------------------


class TestChunksMissingEmbedding:
    """mirror of TestEmbedderOps.test_chunks_missing_embedding in test_backend.py"""

    def test_chunks_missing_embedding_returns_unembedded(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        embedder = _fake_embedder(name="miss-embed", dim=8)
        eid = backend.register_embedder(embedder)
        ds_id = _insert_dataset(backend, "ds_missing")

        doc1 = _sample_doc(source_uri="vault://a.md", content_hash="a1")
        doc2 = _sample_doc(
            source_uri="vault://b.md",
            content_hash="b1",
            text="# B\n\nB content.",
            title="B",
        )
        doc_id1 = backend.upsert_document(ds_id, doc1, [("A", "A content.")])
        doc_id2 = backend.upsert_document(ds_id, doc2, [("B", "B content.")])

        conn = _db(db_path)
        try:
            chunk_id1 = conn.execute(
                "SELECT id FROM chunks WHERE document_id = ? LIMIT 1",
                (doc_id1,),
            ).fetchone()["id"]
            chunk_id2 = conn.execute(
                "SELECT id FROM chunks WHERE document_id = ? LIMIT 1",
                (doc_id2,),
            ).fetchone()["id"]
        finally:
            conn.close()

        # Embed doc1's chunk only
        backend.write_embeddings(eid, [(chunk_id1, np.ones(8, dtype=np.float32))])

        missing = list(backend.chunks_missing_embedding(eid))
        missing_ids = {cid for cid, _ in missing}

        assert chunk_id1 not in missing_ids, "doc1's chunk was embedded; should NOT be missing"
        assert chunk_id2 in missing_ids, "doc2's chunk was NOT embedded; should be missing"

    def test_chunks_missing_embedding_unknown_embedder_returns_empty(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        missing = list(backend.chunks_missing_embedding(99999))
        assert missing == []

    def test_chunks_missing_embedding_all_embedded(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        embedder = _fake_embedder(name="all-embed", dim=8)
        eid = backend.register_embedder(embedder)
        ds_id = _insert_dataset(backend, "ds_all_embed")

        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://all.md", content_hash="all1"),
            [("A", "All content.")],
        )
        conn = _db(db_path)
        try:
            chunk_id = conn.execute(
                "SELECT id FROM chunks WHERE document_id = ? LIMIT 1",
                (doc_id,),
            ).fetchone()["id"]
        finally:
            conn.close()

        backend.write_embeddings(eid, [(chunk_id, np.ones(8, dtype=np.float32))])
        missing = list(backend.chunks_missing_embedding(eid))
        assert missing == []


# ---------------------------------------------------------------------------
# TestLockSource
# ---------------------------------------------------------------------------


class TestLockSource:
    """mirror of TestEmbedderOps.test_advisory_lock* in test_backend.py"""

    def test_lock_source_context_manager_does_not_raise(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        with backend.lock_source("test-key"):
            pass

    def test_lock_source_executes_body(self, tmp_path: Path) -> None:
        """Work inside the lock body executes normally."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        executed = []
        with backend.lock_source("key"):
            executed.append(1)
        assert executed == [1]

    def test_lock_source_serializes_threads(self, tmp_path: Path) -> None:
        """Two threads both succeed when lock is used serially (not nested)."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        results: list[int] = []

        def worker(n: int) -> None:
            with backend.lock_source("shared-key"):
                results.append(n)

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert sorted(results) == [1, 2], "Both threads should have run"

    def test_lock_source_reraises_exception(self, tmp_path: Path) -> None:
        """Exceptions from the lock body are re-raised."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        with pytest.raises(ValueError, match="test error"), backend.lock_source("err-key"):
            raise ValueError("test error")


# ---------------------------------------------------------------------------
# TestDeleteDocument
# ---------------------------------------------------------------------------


class TestDeleteDocument:
    """mirror of TestDatasetOps.test_delete_document"""

    def test_delete_document_removes_row(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_del_doc")

        doc = _sample_doc(source_uri="vault://del.md", content_hash="del1")
        backend.upsert_document(ds_id, doc, [("Del", "Delete me.")])

        backend.delete_document(ds_id, "vault://del.md")

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM documents WHERE source_uri = ?",
                ("vault://del.md",),
            ).fetchone()
            assert row["cnt"] == 0
        finally:
            conn.close()

    def test_delete_document_cascades_chunks(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_del_cascade")

        doc = _sample_doc(source_uri="vault://cascade.md", content_hash="cas1")
        doc_id = backend.upsert_document(ds_id, doc, [("A", "A."), ("B", "B.")])

        backend.delete_document(ds_id, "vault://cascade.md")

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM chunks WHERE document_id = ?",
                (doc_id,),
            ).fetchone()
            assert row["cnt"] == 0, "FK cascade should have deleted chunks"
        finally:
            conn.close()

    def test_delete_document_idempotent(self, tmp_path: Path) -> None:
        """Deleting a non-existent document is a no-op."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_del_noop")
        backend.delete_document(ds_id, "vault://nonexistent.md")  # must not raise

    def test_delete_document_wrong_dataset_no_effect(self, tmp_path: Path) -> None:
        """delete_document with wrong dataset_id does not delete the document."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id1 = _insert_dataset(backend, "ds_del_right")
        ds_id2 = _insert_dataset(backend, "ds_del_wrong")

        doc = _sample_doc(source_uri="vault://shared.md", content_hash="s1")
        backend.upsert_document(ds_id1, doc, [("S", "Shared.")])

        backend.delete_document(ds_id2, "vault://shared.md")  # wrong dataset

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM documents WHERE source_uri = ? AND dataset_id = ?",
                ("vault://shared.md", ds_id1),
            ).fetchone()
            assert row["cnt"] == 1, "Document in correct dataset should still exist"
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# TestDeleteConversation
# ---------------------------------------------------------------------------


class TestDeleteConversation:
    """mirror of TestDatasetOps.test_delete_conversation"""

    def test_delete_conversation_removes_row(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_del_conv", kind="chat")

        conv = _sample_conv(source_uri="claude-code://proj/del-sess")
        backend.upsert_conversation(ds_id, conv, [[("Hi", "Hi")]])

        backend.delete_conversation(ds_id, "claude-code://proj/del-sess")

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM conversations WHERE source_uri = ?",
                ("claude-code://proj/del-sess",),
            ).fetchone()
            assert row["cnt"] == 0
        finally:
            conn.close()

    def test_delete_conversation_cascades_messages(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_del_conv_msgs", kind="chat")

        conv = _sample_conv(source_uri="claude-code://proj/casc-sess")
        conv_id = backend.upsert_conversation(ds_id, conv, [[("Hi", "Hi")], [("Ok", "Ok")]])

        backend.delete_conversation(ds_id, "claude-code://proj/casc-sess")

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM messages WHERE conversation_id = ?",
                (conv_id,),
            ).fetchone()
            assert row["cnt"] == 0, "FK cascade should have deleted messages"
        finally:
            conn.close()

    def test_delete_conversation_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_del_conv_noop", kind="chat")
        backend.delete_conversation(ds_id, "claude-code://nonexistent")  # no-op


# ---------------------------------------------------------------------------
# TestFindDocument
# ---------------------------------------------------------------------------


class TestFindDocument:
    """mirror of B-09: find_document"""

    def test_find_document_returns_none_when_missing(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_find_none")
        result = backend.find_document(ds_id, "vault://notexist.md")
        assert result is None

    def test_find_document_returns_row_when_present(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_find_present")

        doc = _sample_doc(source_uri="vault://found.md", content_hash="found1")
        doc_id = backend.upsert_document(ds_id, doc, [("F", "Found.")])

        result = backend.find_document(ds_id, "vault://found.md")
        assert result is not None
        assert result["id"] == doc_id
        assert result["content_hash"] == "found1"

    def test_find_document_does_not_create_row(self, tmp_path: Path) -> None:
        """find_document is read-only — never inserts."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_find_readonly")

        backend.find_document(ds_id, "vault://no-create.md")

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM documents WHERE source_uri = ?",
                ("vault://no-create.md",),
            ).fetchone()
            assert row["cnt"] == 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# TestResolveDocument
# ---------------------------------------------------------------------------


class TestResolveDocument:
    """mirror of B-09: resolve_document"""

    def test_resolve_document_returns_none_for_empty_uri(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_resolve_empty")
        result = backend.resolve_document(ds_id, "")
        assert result is None

    def test_resolve_document_creates_stub_when_missing(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_resolve_create")

        result = backend.resolve_document(ds_id, "vault://stub.md")
        assert result is not None
        assert "id" in result

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM documents WHERE source_uri = ? AND dataset_id = ?",
                ("vault://stub.md", ds_id),
            ).fetchone()
            assert row["cnt"] == 1
        finally:
            conn.close()

    def test_resolve_document_returns_existing_row(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_resolve_existing")

        doc = _sample_doc(source_uri="vault://exist.md", content_hash="ex1")
        doc_id = backend.upsert_document(ds_id, doc, [("E", "Existing.")])

        result = backend.resolve_document(ds_id, "vault://exist.md")
        assert result is not None
        assert result["id"] == doc_id
        assert result["content_hash"] == "ex1"

    def test_resolve_document_idempotent(self, tmp_path: Path) -> None:
        """Calling resolve_document twice returns the same id."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_resolve_idem")

        r1 = backend.resolve_document(ds_id, "vault://idem.md")
        r2 = backend.resolve_document(ds_id, "vault://idem.md")
        assert r1 is not None and r2 is not None
        assert r1["id"] == r2["id"]


# ---------------------------------------------------------------------------
# TestResolveSelfSource
# ---------------------------------------------------------------------------


class TestResolveSelfSource:
    """mirror of B-09: resolve_self_source"""

    def test_resolve_self_source_returns_int(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_self_int")
        source_id = backend.resolve_self_source(ds_id, "host-a")
        assert isinstance(source_id, int)
        assert source_id > 0

    def test_resolve_self_source_idempotent(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_self_idem")
        id1 = backend.resolve_self_source(ds_id, "host-a")
        id2 = backend.resolve_self_source(ds_id, "host-a")
        assert id1 == id2

    def test_resolve_self_source_different_hosts_different_ids(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_self_hosts")
        id_a = backend.resolve_self_source(ds_id, "host-a")
        id_b = backend.resolve_self_source(ds_id, "host-b")
        assert id_a != id_b


# ---------------------------------------------------------------------------
# TestInsertRevision
# ---------------------------------------------------------------------------


class TestInsertRevision:
    """mirror of B-10: insert_revision with monotonic revision_number"""

    def _setup(self, tmp_path: Path) -> tuple[SQLiteBackend, int, int]:
        """Return (backend, dataset_id, document_id)."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_rev")
        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://rev.md", content_hash="r0"),
            [("R", "Revision content.")],
        )
        return backend, ds_id, doc_id

    def test_insert_revision_returns_dict_with_id(self, tmp_path: Path) -> None:
        backend, _ds_id, doc_id = self._setup(tmp_path)
        with backend.lock_source("vault://rev.md"):
            rev = backend.insert_revision(
                document_id=doc_id,
                source_uri="vault://rev.md",
                content_hash="r1",
                text="revision text",
                parent_revision_id=None,
                author_host="host1",
                is_tombstone=False,
            )
        assert "id" in rev
        assert "revision_number" in rev
        assert rev["revision_number"] == 1

    def test_insert_revision_monotonic(self, tmp_path: Path) -> None:
        backend, _ds_id, doc_id = self._setup(tmp_path)
        with backend.lock_source("vault://rev.md"):
            r1 = backend.insert_revision(
                document_id=doc_id,
                source_uri="vault://rev.md",
                content_hash="r1",
                text="v1",
                parent_revision_id=None,
                author_host="host1",
                is_tombstone=False,
            )
        with backend.lock_source("vault://rev.md"):
            r2 = backend.insert_revision(
                document_id=doc_id,
                source_uri="vault://rev.md",
                content_hash="r2",
                text="v2",
                parent_revision_id=r1["id"],
                author_host="host1",
                is_tombstone=False,
            )
        assert r2["revision_number"] == r1["revision_number"] + 1

    def test_insert_revision_tombstone_flag(self, tmp_path: Path) -> None:
        backend, _ds_id, doc_id = self._setup(tmp_path)
        db_path = tmp_path / "corpus.db"
        with backend.lock_source("vault://rev.md"):
            rev = backend.insert_revision(
                document_id=doc_id,
                source_uri="vault://rev.md",
                content_hash="deadbeef",
                text="",
                parent_revision_id=None,
                author_host="host1",
                is_tombstone=True,
            )

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT is_tombstone FROM document_revisions WHERE id = ?",
                (rev["id"],),
            ).fetchone()
            assert row["is_tombstone"] in (1, True)
        finally:
            conn.close()

    def test_insert_revision_metadata_stored_as_json(self, tmp_path: Path) -> None:
        backend, _ds_id, doc_id = self._setup(tmp_path)
        db_path = tmp_path / "corpus.db"
        meta = {"key": "value", "num": 42}
        with backend.lock_source("vault://rev.md"):
            rev = backend.insert_revision(
                document_id=doc_id,
                source_uri="vault://rev.md",
                content_hash="rm1",
                text="with meta",
                parent_revision_id=None,
                author_host="host1",
                is_tombstone=False,
                metadata=meta,
            )

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT metadata FROM document_revisions WHERE id = ?",
                (rev["id"],),
            ).fetchone()
            stored_meta = json.loads(row["metadata"])
            assert stored_meta == meta
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# TestLatestRevision
# ---------------------------------------------------------------------------


class TestLatestRevision:
    """mirror of B-11: latest_revision"""

    def test_latest_revision_none_when_no_revisions(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_latest_none")
        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://latest.md", content_hash="lnone"),
            [("L", "Latest.")],
        )
        result = backend.latest_revision(doc_id)
        assert result is None

    def test_latest_revision_returns_highest_number(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_latest_num")
        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://latest2.md", content_hash="l2"),
            [("L", "Latest.")],
        )

        with backend.lock_source("vault://latest2.md"):
            r1 = backend.insert_revision(
                document_id=doc_id,
                source_uri="vault://latest2.md",
                content_hash="lh1",
                text="v1",
                parent_revision_id=None,
                author_host="host1",
                is_tombstone=False,
            )
            r2 = backend.insert_revision(
                document_id=doc_id,
                source_uri="vault://latest2.md",
                content_hash="lh2",
                text="v2",
                parent_revision_id=r1["id"],
                author_host="host1",
                is_tombstone=False,
            )

        latest = backend.latest_revision(doc_id)
        assert latest is not None
        assert latest["revision_number"] == r2["revision_number"]


# ---------------------------------------------------------------------------
# TestPendingRemoteRevisions
# ---------------------------------------------------------------------------


class TestPendingRemoteRevisions:
    """mirror of B-11: pending_remote_revisions"""

    def test_pending_remote_revisions_empty_initially(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_pending_empty")
        pending = backend.pending_remote_revisions(
            dataset_id=ds_id,
            last_pulled_revision_id=None,
            self_host="host-a",
        )
        assert pending == []

    def test_pending_remote_revisions_filters_self_host(self, tmp_path: Path) -> None:
        """Revisions authored by self_host are NOT included."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_pending_self")
        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://p.md", content_hash="ph"),
            [("P", "Pending.")],
        )

        with backend.lock_source("vault://p.md"):
            backend.insert_revision(
                document_id=doc_id,
                source_uri="vault://p.md",
                content_hash="ph1",
                text="v1",
                parent_revision_id=None,
                author_host="host-a",  # self-authored
                is_tombstone=False,
            )

        pending = backend.pending_remote_revisions(
            dataset_id=ds_id,
            last_pulled_revision_id=None,
            self_host="host-a",
        )
        assert pending == []

    def test_pending_remote_revisions_returns_remote_revisions(self, tmp_path: Path) -> None:
        """Revisions from other hosts with id > last_pulled are returned."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_pending_remote")
        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://rem.md", content_hash="remh"),
            [("R", "Remote.")],
        )

        with backend.lock_source("vault://rem.md"):
            rev = backend.insert_revision(
                document_id=doc_id,
                source_uri="vault://rem.md",
                content_hash="rh1",
                text="from remote",
                parent_revision_id=None,
                author_host="host-b",  # remote author
                is_tombstone=False,
            )

        pending = backend.pending_remote_revisions(
            dataset_id=ds_id,
            last_pulled_revision_id=None,
            self_host="host-a",
        )
        assert len(pending) == 1
        assert pending[0]["id"] == rev["id"]
        assert pending[0]["source_uri"] == "vault://rem.md"

    def test_pending_remote_revisions_respects_last_pulled(self, tmp_path: Path) -> None:
        """Only revisions with id > last_pulled_revision_id are returned."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_pending_ptr")
        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://ptr.md", content_hash="ptrh"),
            [("P", "Ptr.")],
        )

        with backend.lock_source("vault://ptr.md"):
            r1 = backend.insert_revision(
                document_id=doc_id,
                source_uri="vault://ptr.md",
                content_hash="ph1",
                text="v1",
                parent_revision_id=None,
                author_host="host-b",
                is_tombstone=False,
            )
            r2 = backend.insert_revision(
                document_id=doc_id,
                source_uri="vault://ptr.md",
                content_hash="ph2",
                text="v2",
                parent_revision_id=r1["id"],
                author_host="host-b",
                is_tombstone=False,
            )

        # Only r2 should appear if last_pulled = r1["id"]
        pending = backend.pending_remote_revisions(
            dataset_id=ds_id,
            last_pulled_revision_id=r1["id"],
            self_host="host-a",
        )
        assert len(pending) == 1
        assert pending[0]["id"] == r2["id"]


# ---------------------------------------------------------------------------
# TestMarkRevisionPulled
# ---------------------------------------------------------------------------


class TestMarkRevisionPulled:
    """mirror of B-11: mark_revision_pulled"""

    def test_mark_revision_pulled_advances_pointer(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_mrp")
        source_id = backend.resolve_self_source(ds_id, "host-a")

        backend.mark_revision_pulled(source_id, 42)

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT last_pulled_revision_id FROM sources WHERE id = ?",
                (source_id,),
            ).fetchone()
            assert row["last_pulled_revision_id"] == 42
        finally:
            conn.close()

    def test_mark_revision_pulled_monotonic(self, tmp_path: Path) -> None:
        """Calling with a smaller id does not regress the pointer."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_mrp_mono")
        source_id = backend.resolve_self_source(ds_id, "host-a")

        backend.mark_revision_pulled(source_id, 100)
        backend.mark_revision_pulled(source_id, 50)  # smaller — should not regress

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT last_pulled_revision_id FROM sources WHERE id = ?",
                (source_id,),
            ).fetchone()
            assert row["last_pulled_revision_id"] == 100
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# TestSetTombstone / TestClearTombstone
# ---------------------------------------------------------------------------


class TestSetTombstone:
    """mirror of B-12: set_tombstone"""

    def test_set_tombstone_marks_document(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_tomb")
        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://tomb.md", content_hash="t1"),
            [("T", "Tombstone.")],
        )

        backend.set_tombstone(doc_id)

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT tombstoned_at FROM documents WHERE id = ?",
                (doc_id,),
            ).fetchone()
            assert row["tombstoned_at"] is not None
        finally:
            conn.close()

    def test_set_tombstone_timestamp_is_iso8601(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_tomb_ts")
        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://tomb_ts.md", content_hash="tts1"),
            [("T", "Ts.")],
        )

        backend.set_tombstone(doc_id)

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT tombstoned_at FROM documents WHERE id = ?",
                (doc_id,),
            ).fetchone()
            ts = row["tombstoned_at"]
        finally:
            conn.close()

        # Must start with a 4-digit year
        assert ts is not None
        assert ts[:4].isdigit(), f"Unexpected timestamp format: {ts!r}"
        assert "T" in ts, f"Timestamp missing 'T' separator: {ts!r}"
        assert ts.endswith("Z"), f"Timestamp not UTC: {ts!r}"

    def test_set_tombstone_idempotent(self, tmp_path: Path) -> None:
        """Calling set_tombstone twice is fine (updates the timestamp)."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_tomb_idem")
        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://tomb_idem.md", content_hash="ti1"),
            [("T", "Idem.")],
        )
        backend.set_tombstone(doc_id)
        backend.set_tombstone(doc_id)  # must not raise

    def test_set_tombstone_unknown_id_noop(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        backend.set_tombstone(99999)  # unknown id — must not raise


class TestClearTombstone:
    """mirror of B-12: clear_tombstone"""

    def test_clear_tombstone_removes_timestamp(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_clear_tomb")
        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://clear_tomb.md", content_hash="ct1"),
            [("C", "Clear.")],
        )

        backend.set_tombstone(doc_id)
        backend.clear_tombstone(doc_id)

        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT tombstoned_at FROM documents WHERE id = ?",
                (doc_id,),
            ).fetchone()
            assert row["tombstoned_at"] is None
        finally:
            conn.close()

    def test_clear_tombstone_idempotent(self, tmp_path: Path) -> None:
        """Clearing an already-NULL tombstone is a no-op."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_clear_idem")
        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://clear_idem.md", content_hash="ci1"),
            [("C", "Ci.")],
        )
        backend.clear_tombstone(doc_id)  # already NULL — no-op

    def test_clear_tombstone_unknown_id_noop(self, tmp_path: Path) -> None:
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        backend.clear_tombstone(99999)  # must not raise

    def test_tombstone_round_trip(self, tmp_path: Path) -> None:
        """set_tombstone then clear_tombstone restores NULL state."""
        db_path = tmp_path / "corpus.db"
        backend = _make_backend(db_path)
        ds_id = _insert_dataset(backend, "ds_tomb_round")
        doc_id = backend.upsert_document(
            ds_id,
            _sample_doc(source_uri="vault://round.md", content_hash="rnd1"),
            [("R", "Round.")],
        )

        assert backend.find_document(ds_id, "vault://round.md") is not None

        backend.set_tombstone(doc_id)
        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT tombstoned_at FROM documents WHERE id = ?",
                (doc_id,),
            ).fetchone()
            assert row["tombstoned_at"] is not None
        finally:
            conn.close()

        backend.clear_tombstone(doc_id)
        conn = _db(db_path)
        try:
            row = conn.execute(
                "SELECT tombstoned_at FROM documents WHERE id = ?",
                (doc_id,),
            ).fetchone()
            assert row["tombstoned_at"] is None
        finally:
            conn.close()
