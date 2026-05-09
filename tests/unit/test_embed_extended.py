"""Unit tests for embed module backfill logic — extended coverage."""

from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.config import Config
from corpus_forge.embed import backfill_embedder, main


class TestBackfillDatasetFiltering:
    """Test dataset filtering in backfill_embedder."""

    def test_backfill_with_dataset_filter(self, temp_dir):
        """Test backfill with dataset_name parameter."""
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

            with patch("corpus_forge.embed.registry.register", return_value=mock_embedder):
                with patch("corpus_forge.embed.PostgresBackend") as mock_backend_cls:
                    mock_backend = MagicMock()
                    mock_backend.register_embedder.return_value = 1
                    mock_backend._execute.return_value = [{"id": 42}]  # dataset found
                    mock_backend.chunks_missing_embedding.return_value = []
                    mock_backend_cls.return_value = mock_backend

                    backfill_embedder("test-embedder", dataset_name="my-dataset")

                    # Verify _execute was called with dataset query
                    dataset_queries = [
                        c for c in mock_backend._execute.call_args_list if "datasets" in str(c)
                    ]
                    assert len(dataset_queries) >= 1

    def test_backfill_dataset_filter_no_chunks_after_filter(self, temp_dir):
        """Test backfill where dataset filter removes all chunks."""
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

            with patch("corpus_forge.embed.registry.register", return_value=mock_embedder):
                with patch("corpus_forge.embed.PostgresBackend") as mock_backend_cls:
                    mock_backend = MagicMock()
                    mock_backend.register_embedder.return_value = 1
                    mock_backend._execute.return_value = [{"id": 42}]  # dataset found
                    # Return empty list — no chunks need embedding
                    mock_backend.chunks_missing_embedding.return_value = []
                    mock_backend_cls.return_value = mock_backend

                    # Should not raise, just break early
                    backfill_embedder("test-embedder", dataset_name="my-dataset")

    def test_backfill_limit_hits_mid_batch(self, temp_dir):
        """Test backfill where limit is hit during a batch."""
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

            with patch("corpus_forge.embed.registry.register", return_value=mock_embedder):
                with patch("corpus_forge.embed.PostgresBackend") as mock_backend_cls:
                    mock_backend = MagicMock()
                    mock_backend.register_embedder.return_value = 1
                    # First call returns 5 chunks, second returns 5 more
                    mock_backend.chunks_missing_embedding.side_effect = [
                        [(i, f"text{i}") for i in range(1, 6)],
                        [(i, f"text{i}") for i in range(6, 11)],
                        [],
                    ]

                    def mock_encode(texts):
                        return [[0.1] * 384 for _ in texts]

                    mock_embedder.encode.side_effect = mock_encode
                    mock_backend_cls.return_value = mock_backend

                    backfill_embedder("test-embedder", limit=7)
                    # Should process first batch of 5, then second batch of 2 (limit=7)
                    assert mock_embedder.encode.call_count == 2


class TestMainFunction:
    """Tests for embed.py main function."""

    def test_main_calls_backfill(self, temp_dir):
        """Test that main calls backfill_embedder."""
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

            with patch("corpus_forge.embed.registry.register", return_value=mock_embedder):
                with patch("corpus_forge.embed.PostgresBackend") as mock_backend_cls:
                    mock_backend = MagicMock()
                    mock_backend.register_embedder.return_value = 1
                    mock_backend.chunks_missing_embedding.return_value = []
                    mock_backend_cls.return_value = mock_backend

                    # Should not raise
                    main("test-embedder")

    def test_main_catches_and_re_raises_exception(self, temp_dir):
        """Test that main logs error and re-raises."""
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

            # Should raise ValueError for missing embedder
            with pytest.raises(ValueError, match="not found in config"):
                main("nonexistent-embedder")

    def test_main_with_dataset_and_limit(self, temp_dir):
        """Test main with dataset and limit parameters."""
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

            with patch("corpus_forge.embed.registry.register", return_value=mock_embedder):
                with patch("corpus_forge.embed.PostgresBackend") as mock_backend_cls:
                    mock_backend = MagicMock()
                    mock_backend.register_embedder.return_value = 1
                    mock_backend._execute.return_value = [{"id": 42}]
                    mock_backend.chunks_missing_embedding.return_value = []
                    mock_backend_cls.return_value = mock_backend

                    main("test-embedder", dataset="my-dataset", limit=10)
