"""Unit tests for embed module backfill logic."""

from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.config import Config
from corpus_forge.embed import backfill_embedder


class TestBackfillEmbedder:
    """Tests for backfill_embedder function."""

    def test_backfill_embedder_not_found(self, temp_dir):
        """Test that missing embedder raises ValueError."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[[embedders]]
name = "other-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "corpus-forge.toml"
        config_file.write_text(config_content)

        with patch.object(Config, "load") as mock_load:
            mock_config = MagicMock()
            mock_config.backend.kind = "postgres"
            mock_config.backend.dsn = "postgresql://test@test/memory"
            mock_config.backend.schema = "corpus"
            mock_config.embedders = []
            mock_load.return_value = mock_config

            with pytest.raises(ValueError, match="not found in config"):
                backfill_embedder("nonexistent-embedder")

    def test_backfill_embedder_unsupported_backend(self, temp_dir):
        """Test that an unsupported backend kind raises ValueError."""
        config_content = """
[backend]
kind = "duckdb"
dsn = "duckdb://memory"
schema = "corpus"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "corpus-forge.toml"
        config_file.write_text(config_content)

        with patch.object(Config, "load") as mock_load:
            mock_config = MagicMock()
            mock_config.backend.kind = "duckdb"
            mock_config.backend.dsn = "duckdb://memory"
            mock_config.backend.schema = "corpus"
            mock_config.embedders = []
            mock_load.return_value = mock_config

            with pytest.raises(ValueError, match="Unsupported backend kind"):
                backfill_embedder("test-embedder")

    def test_backfill_embedder_no_chunks_needed(self, temp_dir):
        """Test backfill when no chunks need embedding."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "corpus-forge.toml"
        config_file.write_text(config_content)

        with patch.object(Config, "load") as mock_load:
            mock_config = MagicMock()
            mock_config.backend.kind = "postgres"
            mock_config.backend.dsn = "postgresql://test@test/memory"
            mock_config.backend.schema = "corpus"
            mock_embedder_config = MagicMock()
            mock_embedder_config.name = "test-embedder"
            mock_embedder_config.provider = "sentence_transformers"
            mock_embedder_config.model_id = "test-model"
            mock_embedder_config.dimension = 384
            mock_embedder_config.normalize = True
            mock_embedder_config.distance = "cosine"
            mock_embedder_config.active = True
            mock_embedder_config.batch_size = 32
            mock_embedder_config.device = "auto"
            mock_embedder_config.api_key_env = "OPENAI_API_KEY"
            mock_config.embedders = [mock_embedder_config]
            mock_load.return_value = mock_config

            mock_embedder = MagicMock()
            mock_embedder.name = "test-embedder"

            with patch("corpus_forge.embed.registry.register", return_value=mock_embedder):  # noqa: SIM117
                with patch("corpus_forge.embed.PostgresBackend") as mock_backend_cls:
                    mock_backend = MagicMock()
                    mock_backend.register_embedder.return_value = 1
                    mock_backend.chunks_missing_embedding.return_value = []
                    mock_backend_cls.return_value = mock_backend

                    # Should not raise
                    backfill_embedder("test-embedder")
                    mock_embedder.warmup.assert_called_once()

    def test_backfill_embedder_with_limit(self, temp_dir):
        """Test backfill with limit parameter."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "corpus-forge.toml"
        config_file.write_text(config_content)

        with patch.object(Config, "load") as mock_load:
            mock_config = MagicMock()
            mock_config.backend.kind = "postgres"
            mock_config.backend.dsn = "postgresql://test@test/memory"
            mock_config.backend.schema = "corpus"
            mock_embedder_config = MagicMock()
            mock_embedder_config.name = "test-embedder"
            mock_embedder_config.provider = "sentence_transformers"
            mock_embedder_config.model_id = "test-model"
            mock_embedder_config.dimension = 384
            mock_embedder_config.normalize = True
            mock_embedder_config.distance = "cosine"
            mock_embedder_config.active = True
            mock_embedder_config.batch_size = 32
            mock_embedder_config.device = "auto"
            mock_embedder_config.api_key_env = "OPENAI_API_KEY"
            mock_config.embedders = [mock_embedder_config]
            mock_load.return_value = mock_config

            mock_embedder = MagicMock()
            mock_embedder.name = "test-embedder"

            with patch("corpus_forge.embed.registry.register", return_value=mock_embedder):  # noqa: SIM117
                with patch("corpus_forge.embed.PostgresBackend") as mock_backend_cls:
                    mock_backend = MagicMock()
                    mock_backend.register_embedder.return_value = 1
                    mock_backend.chunks_missing_embedding.return_value = [
                        (1, "text1"),
                        (2, "text2"),
                        (3, "text3"),
                    ]

                    # Make encode return a list of embeddings matching input length
                    def mock_encode(texts):
                        return [[0.1] * 384 for _ in texts]

                    mock_embedder.encode.side_effect = mock_encode
                    mock_backend_cls.return_value = mock_backend

                    # Should process only 2 chunks due to limit
                    backfill_embedder("test-embedder", limit=2)
                    # encode should be called with limited texts (2 items)
                    mock_embedder.encode.assert_called_once()
                    # Verify it was called with exactly 2 texts
                    call_args = mock_embedder.encode.call_args[0][0]
                    assert len(call_args) == 2

    def test_backfill_embedder_dataset_not_found(self, temp_dir):
        """Test that missing dataset raises ValueError."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "corpus-forge.toml"
        config_file.write_text(config_content)

        with patch.object(Config, "load") as mock_load:
            mock_config = MagicMock()
            mock_config.backend.kind = "postgres"
            mock_config.backend.dsn = "postgresql://test@test/memory"
            mock_config.backend.schema = "corpus"
            mock_config.embedders = []
            mock_load.return_value = mock_config

            mock_embedder = MagicMock()
            mock_embedder.name = "test-embedder"

            with patch("corpus_forge.embed.registry.register", return_value=mock_embedder):  # noqa: SIM117
                with patch("corpus_forge.embed.PostgresBackend") as mock_backend_cls:
                    mock_backend = MagicMock()
                    mock_backend.register_embedder.return_value = 1
                    mock_backend._execute.return_value = []  # Dataset not found
                    mock_backend.chunks_missing_embedding.return_value = []
                    mock_backend_cls.return_value = mock_backend

                    with pytest.raises(ValueError, match="not found"):
                        backfill_embedder("test-embedder", dataset_name="nonexistent")
