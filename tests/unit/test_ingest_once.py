"""Unit tests for ingest_once and ingest_one functions."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.ingest import ingest_once, ingest_one
from corpus_forge.sources.base import RawConversation, RawDocument, RawMessage


class TestIngestOne:
    """Tests for ingest_one function."""

    def test_ingest_one_document(self):
        """Test ingesting a RawDocument."""
        mock_backend = MagicMock()
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=None)
        mock_backend.lock_source.return_value = mock_lock
        mock_backend.get_hash.return_value = None
        mock_backend.register_embedder.return_value = 1
        mock_backend.chunks_missing_embedding.return_value = []

        doc = RawDocument(
            source_uri="test://doc.md",
            content_hash="new_hash",
            text="Test content",
            title="Test",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )

        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = []

        mock_embedder = MagicMock()
        mock_embedder.name = "test-embedder"
        mock_embedder.encode.return_value = [[0.1] * 384]

        with patch("corpus_forge.ingest.logger"):
            ingest_one(mock_backend, doc, mock_chunker, [mock_embedder], dataset_id=1)

        mock_backend.upsert_document.assert_called_once()

    def test_ingest_one_document_unchanged(self):
        """Test that unchanged content is skipped."""
        mock_backend = MagicMock()
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=None)
        mock_backend.lock_source.return_value = mock_lock
        mock_backend.get_hash.return_value = "same_hash"

        doc = RawDocument(
            source_uri="test://doc.md",
            content_hash="same_hash",
            text="Test content",
            title="Test",
            modified_at=1000.0,
            metadata={},
            labels=[],
        )

        mock_chunker = MagicMock()
        mock_embedder = MagicMock()

        with patch("corpus_forge.ingest.logger"):
            ingest_one(mock_backend, doc, mock_chunker, [mock_embedder], dataset_id=1)

        mock_backend.upsert_document.assert_not_called()

    def test_ingest_one_conversation(self):
        """Test ingesting a RawConversation."""
        mock_backend = MagicMock()
        mock_lock = MagicMock()
        mock_lock.__enter__ = MagicMock(return_value=None)
        mock_lock.__exit__ = MagicMock(return_value=None)
        mock_backend.lock_source.return_value = mock_lock
        mock_backend.get_hash.return_value = None
        mock_backend.register_embedder.return_value = 1

        conv = RawConversation(
            source_uri="test://conv.jsonl",
            external_id="conv1",
            content_hash="new_hash",
            title="Test",
            started_at=1000.0,
            ended_at=1001.0,
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
                )
            ],
            metadata={},
            labels=[],
        )

        mock_chunker = MagicMock()
        mock_chunker.chunk.return_value = []

        mock_embedder = MagicMock()
        mock_embedder.name = "test-embedder"

        with patch("corpus_forge.ingest.logger"):
            ingest_one(mock_backend, conv, mock_chunker, [mock_embedder], dataset_id=1)

        mock_backend.upsert_conversation.assert_called_once()


class TestIngestOnce:
    """Tests for ingest_once function."""

    def test_ingest_once_postgres_backend(self, temp_dir):
        """Test ingest_once with postgres backend."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "test-vault"
kind = "text"
  [[datasets.sources]]
  plugin         = "markdown_vault"
  vault_root     = "/tmp/test-vault"
  exclude_globs  = [".obsidian/**", ".trash/**", ".*"]
  chunker        = "markdown"
  chunker_config = { max_chars = 1500, overlap = 200 }
"""
        config_file = temp_dir / "corpus-forge.toml"
        config_file.write_text(config_content)

        with patch("corpus_forge.config.Config.load") as mock_load:
            mock_config = MagicMock()
            mock_config.backend.kind = "postgres"
            mock_config.backend.dsn = "postgresql://test@test/memory"
            mock_config.backend.schema = "corpus"
            mock_config.daemon.log_level = "INFO"

            # Create a mock dataset config
            mock_dataset = MagicMock()
            mock_dataset.name = "test-vault"
            mock_dataset.kind = "text"

            mock_source_config = MagicMock()
            mock_source_config.plugin = "markdown_vault"
            mock_source_config.vault_root = Path("/tmp/test-vault")
            mock_source_config.exclude_globs = [".obsidian/**", ".trash/**", ".*"]
            mock_source_config.chunker = "markdown"
            mock_source_config.chunker_config = {"max_chars": 1500, "overlap": 200}
            mock_dataset.sources = [mock_source_config]

            mock_config.datasets = [mock_dataset]
            mock_config.embedders = []
            mock_load.return_value = mock_config

            with patch("corpus_forge.backends.postgres.PostgresBackend") as mock_backend_cls:
                mock_backend = MagicMock()
                mock_backend.migrate.return_value = None
                mock_backend.register_embedder.return_value = 1
                mock_lock = MagicMock()
                mock_lock.__enter__ = MagicMock(return_value=None)
                mock_lock.__exit__ = MagicMock(return_value=None)
                mock_backend.lock_source.return_value = mock_lock
                mock_backend.get_hash.return_value = None
                mock_backend._execute.return_value = [{"id": 1}]
                mock_backend_cls.return_value = mock_backend

                # Should not raise
                ingest_once(mock_config)

    def test_ingest_once_unsupported_backend(self, temp_dir):
        """Test ingest_once with unsupported backend raises ValueError."""
        config_content = """
[backend]
kind = "redis"
dsn = "redis://localhost"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "test-vault"
kind = "text"
  [[datasets.sources]]
  plugin         = "markdown_vault"
  vault_root     = "/tmp/test-vault"
  chunker        = "markdown"
"""
        config_file = temp_dir / "corpus-forge.toml"
        config_file.write_text(config_content)

        with patch("corpus_forge.config.Config.load") as mock_load:
            mock_config = MagicMock()
            mock_config.backend.kind = "redis"
            mock_config.backend.dsn = "redis://localhost"
            mock_config.backend.schema = "corpus"
            mock_config.daemon.log_level = "INFO"
            mock_config.datasets = []
            mock_config.embedders = []
            mock_load.return_value = mock_config

            with pytest.raises(ValueError, match="Unsupported backend kind"):
                ingest_once(mock_config)
