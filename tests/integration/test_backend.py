"""Integration tests for PostgresBackend with pgvector container."""

import numpy as np
import psycopg
import pytest

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.embedders.base import BaseEmbedder
from corpus_forge.sources.base import RawConversation, RawDocument, RawMessage

pytestmark = pytest.mark.integration


def _make_backend(pg_dsn: str) -> PostgresBackend:
    """Create a PostgresBackend pointing at the test container."""
    return PostgresBackend(dsn=pg_dsn, schema="corpus")


# ── Schema migration ─────────────────────────────────────────────────────────


class TestMigrate:
    def test_creates_all_tables(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'corpus'
                ORDER BY table_name
                """
            )
            tables = {row[0] for row in cur.fetchall()}

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
        }
        assert expected.issubset(tables), f"Missing tables: {expected - tables}"

    def test_creates_vector_extension(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector';")
            assert cur.fetchone() is not None

    def test_idempotent_migrate(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()
        backend.migrate()  # Should not raise

    def test_hnsw_index_created(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT indexname
                FROM pg_indexes
                WHERE schemaname = 'corpus'
                  AND indexname = 'chunks_doc_idx'
                """
            )
            assert cur.fetchone() is not None

    def test_unique_constraints_exist(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT conname
                FROM pg_constraint
                WHERE contype = 'u'
                  AND conrelid = 'corpus.documents'::regclass
                """
            )
            constraints = {row[0] for row in cur.fetchall()}
            assert "documents_dataset_id_source_uri_key" in constraints


# ── Dataset CRUD ─────────────────────────────────────────────────────────────


class TestDatasetOps:
    def test_upsert_document(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus.datasets (name, kind) VALUES ('test_upsert_doc', 'text') RETURNING id"
            )
            dataset_id = cur.fetchone()[0]
            conn.commit()

        doc = RawDocument(
            source_uri="vault://test.md",
            content_hash="abc123",
            text="# Hello\n\nWorld content.",
            title="Hello",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunks = [("Hello", "World content.")]
        doc_id = backend.upsert_document(dataset_id, doc, chunks)
        assert doc_id is not None

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM corpus.chunks WHERE document_id = %s;", (doc_id,))
            assert cur.fetchone()[0] == 1

    def test_upsert_document_unchanged_skips_chunks(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus.datasets (name, kind) VALUES ('test_upsert_unchanged', 'text') RETURNING id"
            )
            dataset_id = cur.fetchone()[0]
            conn.commit()

        doc = RawDocument(
            source_uri="vault://test.md",
            content_hash="abc123",
            text="# Hello\n\nWorld content.",
            title="Hello",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunks = [("Hello", "World content.")]
        doc_id = backend.upsert_document(dataset_id, doc, chunks)

        # Upsert again with same hash — should return same doc_id without error
        doc_id2 = backend.upsert_document(dataset_id, doc, chunks)
        assert doc_id2 == doc_id

    def test_upsert_document_changed_updates(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus.datasets (name, kind) VALUES ('test_upsert_changed', 'text') RETURNING id"
            )
            dataset_id = cur.fetchone()[0]
            conn.commit()

        doc1 = RawDocument(
            source_uri="vault://test.md",
            content_hash="hash1",
            text="# Old\n\nOld content.",
            title="Old",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunks1 = [("Old", "Old content.")]
        doc_id = backend.upsert_document(dataset_id, doc1, chunks1)

        doc2 = RawDocument(
            source_uri="vault://test.md",
            content_hash="hash2",
            text="# New\n\nNew content.",
            title="New",
            modified_at=2000.0,
            metadata={},
            labels=[],
        )
        chunks2 = [("New", "New content.")]
        backend.upsert_document(dataset_id, doc2, chunks2)

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT text, title FROM corpus.documents WHERE id = %s;", (doc_id,))
            row = cur.fetchone()
            assert row[0] == "# New\n\nNew content."
            assert row[1] == "New"

    def test_upsert_conversation(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus.datasets (name, kind) VALUES ('test_upsert_conv', 'chat') RETURNING id"
            )
            dataset_id = cur.fetchone()[0]
            conn.commit()

        conv = RawConversation(
            source_uri="claude-code://proj/session1",
            external_id="session1",
            content_hash="conv123",
            title="Test Chat",
            started_at=1000.0,
            ended_at=1005.0,
            messages=[
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
            ],
            metadata={},
            labels=[],
        )
        chunked = [[("Hello", "Hello")], [("Hi!", "Hi!")]]
        conv_id = backend.upsert_conversation(dataset_id, conv, chunked)
        assert conv_id is not None

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM corpus.messages WHERE conversation_id = %s;", (conv_id,)
            )
            assert cur.fetchone()[0] == 2

    def test_delete_document(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus.datasets (name, kind) VALUES ('test_delete_doc', 'text') RETURNING id"
            )
            dataset_id = cur.fetchone()[0]
            conn.commit()

        doc = RawDocument(
            source_uri="vault://delete-me.md",
            content_hash="del123",
            text="# Delete me",
            title="Delete me",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        backend.upsert_document(dataset_id, doc, [("Delete me", "# Delete me")])

        backend.delete_document(dataset_id, "vault://delete-me.md")

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM corpus.documents WHERE source_uri = %s;",
                ("vault://delete-me.md",),
            )
            assert cur.fetchone()[0] == 0

    def test_delete_conversation(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus.datasets (name, kind) VALUES ('test_delete_conv', 'chat') RETURNING id"
            )
            dataset_id = cur.fetchone()[0]
            conn.commit()

        conv = RawConversation(
            source_uri="claude-code://proj/del-session",
            external_id="del-session",
            content_hash="del456",
            title="Delete Me",
            started_at=1000.0,
            ended_at=1005.0,
            messages=[
                RawMessage(
                    external_uuid="m1",
                    parent_uuid=None,
                    role="user",
                    content="Hi",
                    tool_calls=None,
                    tool_results=None,
                    ts=1000.0,
                    metadata={},
                ),
            ],
            metadata={},
            labels=[],
        )
        backend.upsert_conversation(dataset_id, conv, [[("Hi", "Hi")]])

        backend.delete_conversation(dataset_id, "claude-code://proj/del-session")

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM corpus.conversations WHERE source_uri = %s;",
                ("claude-code://proj/del-session",),
            )
            assert cur.fetchone()[0] == 0


# ── Embedder registration & embedding writes ─────────────────────────────────


class TestEmbedderOps:
    def test_register_embedder(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        embedder = BaseEmbedder(
            name="test-embed",
            provider="sentence_transformers",
            model_id="test/model",
            dimension=384,
            normalized=True,
            distance="cosine",
        )
        embedder_id = backend.register_embedder(embedder)
        assert embedder_id is not None

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT name, provider, dimension FROM corpus.embedders WHERE id = %s;",
                (embedder_id,),
            )
            row = cur.fetchone()
            assert row[0] == "test-embed"
            assert row[1] == "sentence_transformers"
            assert row[2] == 384

    def test_register_embedder_creates_table(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        embedder = BaseEmbedder(
            name="test-embed",
            provider="sentence_transformers",
            model_id="test/model",
            dimension=384,
        )
        backend.register_embedder(embedder)

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'corpus' AND table_name = 'embeddings_test_embed';"
            )
            assert cur.fetchone() is not None

    def test_write_embeddings(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        embedder = BaseEmbedder(
            name="test-embed",
            provider="sentence_transformers",
            model_id="test/model",
            dimension=384,
        )
        embedder_id = backend.register_embedder(embedder)

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus.datasets (name, kind) VALUES ('test_write_embed', 'text') RETURNING id"
            )
            dataset_id = cur.fetchone()[0]
            conn.commit()

        doc = RawDocument(
            source_uri="vault://embed.md",
            content_hash="emb123",
            text="# Test\n\nContent for embedding.",
            title="Test",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        doc_id = backend.upsert_document(dataset_id, doc, [("Test", "Content for embedding.")])

        # Fetch actual chunk ID (document ID != chunk ID with separate sequences)
        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM corpus.chunks WHERE document_id = %s ORDER BY id LIMIT 1;",
                (doc_id,),
            )
            chunk_id = cur.fetchone()[0]

        embedding = np.random.randn(384).astype(np.float32)
        backend.write_embeddings(embedder_id, [(chunk_id, embedding)])

        from pgvector.psycopg import register_vector

        with psycopg.connect(pg_dsn) as conn:
            register_vector(conn)
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT chunk_id, embedding FROM corpus.embeddings_test_embed WHERE chunk_id = %s;",
                    (chunk_id,),
                )
                row = cur.fetchone()
                assert row[0] == chunk_id
                assert len(row[1]) == 384

    def test_write_embeddings_empty_does_not_raise(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        embedder = BaseEmbedder(
            name="test-embed",
            provider="sentence_transformers",
            model_id="test/model",
            dimension=384,
        )
        backend.register_embedder(embedder)
        backend.write_embeddings(1, [])  # empty — should not raise

    def test_chunks_missing_embedding(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        backend.migrate()

        embedder = BaseEmbedder(
            name="test-embed",
            provider="sentence_transformers",
            model_id="test/model",
            dimension=384,
        )
        embedder_id = backend.register_embedder(embedder)

        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus.datasets (name, kind) VALUES ('test_missing_embed', 'text') RETURNING id"
            )
            dataset_id = cur.fetchone()[0]
            conn.commit()

        doc1 = RawDocument(
            source_uri="vault://a.md",
            content_hash="a1",
            text="# A\n\nA content.",
            title="A",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        doc2 = RawDocument(
            source_uri="vault://b.md",
            content_hash="b1",
            text="# B\n\nB content.",
            title="B",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        doc_id1 = backend.upsert_document(dataset_id, doc1, [("A", "A content.")])
        doc_id2 = backend.upsert_document(dataset_id, doc2, [("B", "B content.")])

        # Fetch actual chunk IDs for each document
        with psycopg.connect(pg_dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM corpus.chunks WHERE document_id = %s ORDER BY id", (doc_id1,)
            )
            chunk_ids1 = [r[0] for r in cur.fetchall()]
            cur.execute(
                "SELECT id FROM corpus.chunks WHERE document_id = %s ORDER BY id", (doc_id2,)
            )
            chunk_ids2 = [r[0] for r in cur.fetchall()]

        assert chunk_ids1, "doc1 should have at least one chunk"
        assert chunk_ids2, "doc2 should have at least one chunk"

        # Embed doc1's chunk
        embedding = np.random.randn(384).astype(np.float32)
        backend.write_embeddings(embedder_id, [(chunk_ids1[0], embedding)])

        missing = list(backend.chunks_missing_embedding(embedder_id))
        missing_ids = {cid for cid, _ in missing}
        assert chunk_ids1[0] not in missing_ids  # doc1's chunk was embedded
        assert chunk_ids2[0] in missing_ids  # doc2's chunk is still missing

    def test_advisory_lock_context(self, pg_dsn):
        backend = _make_backend(pg_dsn)
        # lock_source should not raise
        with backend.lock_source("test-key"):
            pass

    def test_advisory_lock_conflict(self, pg_dsn):
        backend = _make_backend(pg_dsn)

        with backend.lock_source("test-key"):
            with pytest.raises(RuntimeError, match="Could not acquire lock"):
                with backend.lock_source("test-key"):
                    pass
