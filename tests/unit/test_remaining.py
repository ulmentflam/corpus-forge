"""Unit tests for embedders and remaining ingest functions."""

from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.chunkers.base import MarkdownChunker
from corpus_forge.embedders.openai import OpenAIEmbedder
from corpus_forge.embedders.sentence_transformers import SentenceTransformersEmbedder
from corpus_forge.ingest import (
    _process_conversation,
    _write_embeddings_for_chunks,
    ingest_once,
)
from corpus_forge.sources.base import RawConversation, RawMessage


class TestProcessConversationFallback:
    """Tests for _process_conversation fallback path."""

    def test_process_conversation_empty_messages(self):
        """Test processing a conversation with empty messages."""
        conv = RawConversation(
            source_uri="test://conv.jsonl",
            external_id="conv1",
            content_hash="abc123",
            title="Test",
            started_at=1000.0,
            ended_at=1001.0,
            messages=[
                RawMessage(
                    external_uuid="msg1",
                    parent_uuid=None,
                    role="user",
                    content="",
                    tool_calls=None,
                    tool_results=None,
                    ts=1000.0,
                    metadata={},
                ),
                RawMessage(
                    external_uuid="msg2",
                    parent_uuid="msg1",
                    role="assistant",
                    content="  ",
                    tool_calls=None,
                    tool_results=None,
                    ts=1001.0,
                    metadata={},
                ),
            ],
            metadata={},
            labels=[],
        )
        chunker = MarkdownChunker(max_chars=1500)
        result = _process_conversation(conv, chunker)
        # When chunker.chunk() receives a list, it processes it as a sequence
        assert len(result) == 2

    def test_process_conversation_mixed_empty(self):
        """Test processing a conversation with mixed empty/non-empty messages."""
        conv = RawConversation(
            source_uri="test://conv.jsonl",
            external_id="conv1",
            content_hash="abc123",
            title="Test",
            started_at=1000.0,
            ended_at=1002.0,
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
                    content="",
                    tool_calls=None,
                    tool_results=None,
                    ts=1001.0,
                    metadata={},
                ),
                RawMessage(
                    external_uuid="msg3",
                    parent_uuid="msg2",
                    role="user",
                    content="World",
                    tool_calls=None,
                    tool_results=None,
                    ts=1002.0,
                    metadata={},
                ),
            ],
            metadata={},
            labels=[],
        )
        chunker = MarkdownChunker(max_chars=1500)
        result = _process_conversation(conv, chunker)
        # When chunker.chunk() receives a list, it processes it as a sequence
        assert len(result) == 3


class TestWriteEmbeddings:
    """Tests for _write_embeddings_for_chunks function."""

    def test_write_embeddings_no_chunks(self):
        """Test when no chunks need embedding."""
        mock_backend = MagicMock()
        mock_backend.chunks_missing_embedding.return_value = []

        mock_embedder = MagicMock()
        mock_embedder.name = "test-embedder"

        with patch("corpus_forge.ingest.logger"):
            _write_embeddings_for_chunks(
                mock_backend,
                embedder_id=1,
                _chunk_ids=[],
                embedder=mock_embedder,
                _fallback_text=None,
            )

        mock_embedder.encode.assert_not_called()
        mock_backend.write_embeddings.assert_not_called()

    def test_write_embeddings_with_chunks(self):
        """Test writing embeddings for chunks that need them."""
        mock_backend = MagicMock()
        mock_backend.chunks_missing_embedding.return_value = [(1, "text1"), (2, "text2")]

        mock_embedder = MagicMock()
        mock_embedder.name = "test-embedder"
        mock_embedder.encode.return_value = [[0.1, 0.2], [0.3, 0.4]]

        with patch("corpus_forge.ingest.logger"):
            _write_embeddings_for_chunks(
                mock_backend,
                embedder_id=1,
                _chunk_ids=[1, 2],
                embedder=mock_embedder,
                _fallback_text=None,
            )

        mock_embedder.encode.assert_called_once()
        mock_backend.write_embeddings.assert_called_once()
        call_args = mock_backend.write_embeddings.call_args
        assert call_args[0][0] == 1
        assert len(call_args[0][1]) == 2


class TestIngestOnceErrorHandling:
    """Tests for ingest_once error handling."""

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


class TestEmbedderInit:
    """Tests for embedder initialization."""

    def test_sentence_transformers_embedder_init(self):
        """Test SentenceTransformersEmbedder initialization."""
        embedder = SentenceTransformersEmbedder(
            name="test-st",
            model_id="test-model",
            dimension=384,
        )
        assert embedder.name == "test-st"
        assert embedder.provider == "sentence_transformers"
        assert embedder.model_id == "test-model"
        assert embedder.dimension == 384
        assert embedder.normalized is True
        assert embedder.distance == "cosine"
        assert embedder.device == "auto"
        assert embedder.batch_size == 32

    def test_openai_embedder_init(self):
        """Test OpenAIEmbedder initialization."""
        embedder = OpenAIEmbedder(
            name="test-openai",
            model_id="text-embedding-3-small",
            dimension=1536,
        )
        assert embedder.name == "test-openai"
        assert embedder.provider == "openai"
        assert embedder.model_id == "text-embedding-3-small"
        assert embedder.dimension == 1536
        assert embedder.normalized is True
        assert embedder.distance == "cosine"
        assert embedder.api_key_env == "OPENAI_API_KEY"
        assert embedder.batch_size == 256

    def test_openai_embedder_custom_values(self):
        """Test OpenAIEmbedder with custom values."""
        embedder = OpenAIEmbedder(
            name="test-custom",
            model_id="text-embedding-3-large",
            dimension=3072,
            normalized=False,
            distance="l2",
            api_key_env="CUSTOM_API_KEY",
            batch_size=128,
        )
        assert embedder.normalized is False
        assert embedder.distance == "l2"
        assert embedder.api_key_env == "CUSTOM_API_KEY"
        assert embedder.batch_size == 128
