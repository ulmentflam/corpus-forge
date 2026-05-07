"""Integration tests for the ingestion pipeline (source → chunker → backend)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from testcontainers.postgres import PostgresContainer

from corpus_forge.backends.postgres import PostgresBackend
from corpus_forge.chunkers.markdown import MarkdownChunker
from corpus_forge.embedders.base import BaseEmbedder
from corpus_forge.ingest import ingest_once, ingest_one
from corpus_forge.sources.markdown_vault import MarkdownVaultSource
from corpus_forge.sources.base import RawDocument

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def pg():
    with PostgresContainer("pgvector/pgvector:pg17", port=5432) as container:
        yield container


@pytest.fixture
def backend(pg):
    dsn = pg.get_connection_url()
    b = PostgresBackend(dsn=dsn, schema="corpus")
    b.migrate()
    return b


@pytest.fixture
def vault_dir(temp_dir):
    """Create a test vault with markdown files."""
    vault = temp_dir / "test-vault"
    vault.mkdir()

    (vault / "note1.md").write_text("# Note 1\n\nFirst note content here.\n\n## Subsection\n\nMore detail.")
    (vault / "note2.md").write_text("# Note 2\n\nSecond note.\n\n## Section A\n\nContent A.\n\n## Section B\n\nContent B.")
    (vault / "empty.md").write_text("")
    (vault / "dotfile.md").write_text("# Should be ignored")
    (vault / ".trash").mkdir()
    (vault / ".trash" / "old.md").write_text("# Trash")

    return vault


# ── Source scan ──────────────────────────────────────────────────────────────


class TestMarkdownVaultSource:
    def test_discovers_markdown_files(self, vault_dir):
        source = MarkdownVaultSource(vault_root=vault_dir)
        paths = list(source.discover())
        names = {p.name for p in paths}
        assert "note1.md" in names
        assert "note2.md" in names
        assert "empty.md" in names
        assert "dotfile.md" in names

    def test_excludes_trash_and_hidden(self, vault_dir):
        source = MarkdownVaultSource(vault_root=vault_dir, exclude_globs=[".trash/**", ".*"])
        paths = list(source.discover())
        names = {p.name for p in paths}
        assert ".trash" not in names
        assert "dotfile.md" not in names

    def test_scan_yields_raw_documents(self, vault_dir):
        source = MarkdownVaultSource(vault_root=vault_dir)
        docs = list(source.scan())
        assert len(docs) >= 2
        for doc in docs:
            assert isinstance(doc, RawDocument)
            assert doc.content_hash  # non-empty hash

    def test_empty_file_yields_empty_doc(self, vault_dir):
        source = MarkdownVaultSource(vault_root=vault_dir)
        docs = {d.source_uri: d for d in source.scan()}
        empty_doc = [d for d in docs.values() if d.source_uri.endswith("empty.md")]
        assert len(empty_doc) == 1
        assert empty_doc[0].text == ""


# ── Chunking integration ─────────────────────────────────────────────────────


class TestMarkdownChunking:
    def test_chunks_long_text(self):
        chunker = MarkdownChunker(max_chars=100, overlap=20)
        long_text = "# Header\n\nPara one.\n\nPara two that is longer.\n\nPara three even longer content here."
        chunks = chunker.chunk(long_text)
        assert len(chunks) >= 1
        for chunk in chunks:
            assert chunk.text  # non-empty

    def test_single_chunk_for_short_text(self):
        chunker = MarkdownChunker(max_chars=1000, overlap=100)
        short_text = "# Header\n\nShort content."
        chunks = chunker.chunk(short_text)
        assert len(chunks) == 1

    def test_chunk_preserves_heading(self):
        chunker = MarkdownChunker(max_chars=50, overlap=10)
        text = "# Main\n\nPara one.\n\n## Sub\n\nPara two."
        chunks = chunker.chunk(text)
        headings = [c.heading for c in chunks]
        assert "Main" in headings or "Sub" in headings


# ── ingest_one (backend + source + chunker) ─────────────────────────────────


class TestIngestOne:
    def test_ingest_document(self, backend, temp_dir):
        doc = RawDocument(
            source_uri="vault://test.md",
            content_hash="abc123",
            text="# Test\n\nIngested content.",
            title="Test",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunker = MarkdownChunker(max_chars=1500, overlap=200)
        embedders = [
            BaseEmbedder(
                name="mock-embed",
                provider="sentence_transformers",
                model_id="mock/model",
                dimension=384,
            )
        ]

        with pg.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus.datasets (name, kind) VALUES ('test', 'text') RETURNING id"
            )
            dataset_id = cur.fetchone()[0]

        # Mock the embedder encode to avoid loading a real model
        mock_embedder = MagicMock()
        mock_embedder.name = "mock-embed"
        mock_embedder.dimension = 384
        mock_embedder.encode = MagicMock(return_value=np.random.randn(1, 384).astype(np.float32))

        ingest_one(backend, doc, chunker, [mock_embedder], dataset_id)

        with pg.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM corpus.documents WHERE source_uri = %s;", ("vault://test.md",))
            assert cur.fetchone()[0] == 1

            cur.execute("SELECT COUNT(*) FROM corpus.chunks WHERE document_id IN (SELECT id FROM corpus.documents WHERE source_uri = %s);", ("vault://test.md",))
            assert cur.fetchone()[0] >= 1

    def test_ingest_unchanged_skips(self, backend, temp_dir):
        doc = RawDocument(
            source_uri="vault://skip.md",
            content_hash="unchanged",
            text="# Skip\n\nUnchanged.",
            title="Skip",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )
        chunker = MarkdownChunker()
        mock_embedder = MagicMock()
        mock_embedder.name = "mock"
        mock_embedder.dimension = 384
        mock_embedder.encode = MagicMock(return_value=np.random.randn(1, 384).astype(np.float32))

        with pg.get_connection() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO corpus.datasets (name, kind) VALUES ('test', 'text') RETURNING id"
            )
            dataset_id = cur.fetchone()[0]

        ingest_one(backend, doc, chunker, [mock_embedder], dataset_id)
        ingest_one(backend, doc, chunker, [mock_embedder], dataset_id)

        with pg.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM corpus.documents WHERE source_uri = %s;", ("vault://skip.md",))
            assert cur.fetchone()[0] == 1


# ── ingest_once (full pipeline) ─────────────────────────────────────────────


class TestIngestOnce:
    def test_full_ingestion_pass(self, backend, vault_dir, temp_dir):
        """End-to-end: config → backend → source scan → chunk → store."""
        from corpus_forge.config import Config, BackendConfig, DaemonConfig, DatasetConfig, EmbedderConfig, SourceConfig

        config = Config(
            backend=BackendConfig(kind="postgres", dsn=pg.get_connection_url(), schema="corpus"),
            daemon=DaemonConfig(debounce_seconds=1.0, log_level="INFO", log_format="text"),
            datasets=[
                DatasetConfig(
                    name="integration-vault",
                    kind="text",
                    description="Integration test vault",
                    sources=[
                        DatasetSourceConfig(
                            plugin="markdown_vault",
                            vault_root=str(vault_dir),
                            exclude_globs=[".trash/**"],
                            chunker="markdown",
                            chunker_config={"max_chars": 1500, "overlap": 200},
                        )
                    ],
                )
            ],
            embedders=[
                EmbedderConfig(
                    name="integration-embed",
                    provider="sentence_transformers",
                    model_id="test/model",
                    dimension=384,
                    normalize=True,
                    distance="cosine",
                    active=True,
                    batch_size=32,
                    device="cpu",
                )
            ],
        )

        # Patch the embedder registry to avoid loading a real model
        with patch("corpus_forge.ingest.registry") as mock_registry:
            mock_embedder = MagicMock()
            mock_embedder.name = "integration-embed"
            mock_embedder.provider = "sentence_transformers"
            mock_embedder.model_id = "test/model"
            mock_embedder.dimension = 384
            mock_embedder.normalized = True
            mock_embedder.distance = "cosine"
            mock_embedder.encode = MagicMock(return_value=np.random.randn(1, 384).astype(np.float32))
            mock_registry.register = MagicMock(return_value=mock_embedder)

            ingest_once(config)

        # Verify documents were ingested
        with pg.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM corpus.datasets WHERE name = 'integration-vault';")
            assert cur.fetchone()[0] == 1

            cur.execute("SELECT COUNT(*) FROM corpus.documents WHERE dataset_id = 1;")
            count = cur.fetchone()[0]
            assert count >= 2  # note1.md and note2.md at minimum

    def test_ingest_creates_dataset_if_missing(self, backend, vault_dir, temp_dir):
        from corpus_forge.config import Config, BackendConfig, DaemonConfig, DatasetConfig, EmbedderConfig, SourceConfig

        config = Config(
            backend=BackendConfig(kind="postgres", dsn=pg.get_connection_url(), schema="corpus"),
            daemon=DaemonConfig(debounce_seconds=1.0, log_level="INFO", log_format="text"),
            datasets=[
                DatasetConfig(
                    name="new-dataset",
                    kind="text",
                    sources=[
                        DatasetSourceConfig(
                            plugin="markdown_vault",
                            vault_root=str(vault_dir),
                            chunker="markdown",
                            chunker_config={"max_chars": 1500, "overlap": 200},
                        )
                    ],
                )
            ],
            embedders=[],
        )

        with patch("corpus_forge.ingest.registry") as mock_registry:
            mock_registry.register = MagicMock(return_value=MagicMock())
            mock_registry.list_names = MagicMock(return_value=[])
            ingest_once(config)

        with pg.get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM corpus.datasets WHERE name = 'new-dataset';")
            assert cur.fetchone()[0] == 1
