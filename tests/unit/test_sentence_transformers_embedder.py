"""Unit tests for SentenceTransformersEmbedder with mocked model."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from corpus_forge.embedders.sentence_transformers import (
    SENTENCE_TRANSFORMERS_AVAILABLE,
    SentenceTransformersEmbedder,
)

pytestmark = pytest.mark.integration


class TestSentenceTransformersInit:
    def test_init_default_device(self):
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        assert embedder.device == "auto"

    def test_init_custom_device(self):
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
            device="cpu",
        )
        assert embedder.device == "cpu"

    def test_init_default_batch_size(self):
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        assert embedder.batch_size == 32

    def test_init_custom_batch_size(self):
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
            batch_size=64,
        )
        assert embedder.batch_size == 64


class TestSentenceTransformersModel:
    def test_load_model_lazy(self):
        """Test that model is not loaded until needed."""
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        assert embedder._model is None

        with patch("corpus_forge.embedders.sentence_transformers.SentenceTransformer") as MockST:
            mock_model = MagicMock()
            mock_model.get_sentence_embedding_dimension.return_value = 384
            MockST.return_value = mock_model
            embedder._load_model()
            assert embedder._model is mock_model
            MockST.assert_called_once_with("BAAI/bge-small-en-v1.5", device="auto")

    def test_load_model_uses_custom_device(self):
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
            device="cuda",
        )
        with patch("corpus_forge.embedders.sentence_transformers.SentenceTransformer") as MockST:
            mock_model = MagicMock()
            mock_model.get_sentence_embedding_dimension.return_value = 384
            MockST.return_value = mock_model
            embedder._load_model()
            MockST.assert_called_once_with("BAAI/bge-small-en-v1.5", device="cuda")

    def test_load_model_noop_when_package_missing(self):
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        with patch("corpus_forge.embedders.sentence_transformers.SENTENCE_TRANSFORMERS_AVAILABLE", False):
            embedder._load_model()
            assert embedder._model is None


class TestSentenceTransformersEncode:
    @pytest.fixture
    def embedder_with_mocked_model(self):
        """Create an embedder with a mocked SentenceTransformer model."""
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            pytest.skip("sentence-transformers not installed")
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        mock_model.encode.return_value = np.zeros((2, 384), dtype=np.float32)
        embedder._model = mock_model
        return embedder

    def test_encode_returns_numpy_array(self, embedder_with_mocked_model):
        result = embedder_with_mocked_model.encode(["hello", "world"])
        assert isinstance(result, np.ndarray)
        assert result.shape == (2, 384)

    def test_encode_uses_instance_batch_size(self, embedder_with_mocked_model):
        embedder_with_mocked_model.batch_size = 64
        embedder_with_mocked_model.encode(["a", "b"])
        call_kwargs = embedder_with_mocked_model._model.encode.call_args
        assert call_kwargs[1]["batch_size"] == 64

    def test_encode_uses_provided_batch_size(self, embedder_with_mocked_model):
        embedder_with_mocked_model.batch_size = 64
        embedder_with_mocked_model.encode(["a", "b"], batch_size=128)
        call_kwargs = embedder_with_mocked_model._model.encode.call_args
        assert call_kwargs[1]["batch_size"] == 128

    def test_encode_normalizes_when_enabled(self, embedder_with_mocked_model):
        embedder_with_mocked_model.normalized = True
        mock_model = embedder_with_mocked_model._model
        mock_model.encode.return_value = np.ones((1, 384), dtype=np.float32)
        embedder_with_mocked_model.encode(["hello"])
        call_kwargs = mock_model.encode.call_args
        assert call_kwargs[1]["normalize_embeddings"] is True

    def test_encode_does_not_normalize_when_disabled(self, embedder_with_mocked_model):
        embedder_with_mocked_model.normalized = False
        mock_model = embedder_with_mocked_model._model
        mock_model.encode.return_value = np.ones((1, 384), dtype=np.float32)
        embedder_with_mocked_model.encode(["hello"])
        call_kwargs = mock_model.encode.call_args
        assert call_kwargs[1]["normalize_embeddings"] is False

    def test_encode_dimension_mismatch_raises(self):
        """Test that wrong dimension raises ValueError."""
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            pytest.skip("sentence-transformers not installed")
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=768,  # wrong dimension
        )
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        mock_model.encode.return_value = np.zeros((1, 384), dtype=np.float32)
        embedder._model = mock_model

        with pytest.raises(ValueError, match="produced embeddings of dimension"):
            embedder.encode(["hello"])

    def test_encode_raises_when_model_none(self):
        """Test encode raises RuntimeError when model failed to load."""
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        embedder._model = None
        with pytest.raises(RuntimeError, match="Failed to load SentenceTransformer model"):
            embedder.encode(["hello"])

    def test_encode_raises_when_package_missing(self):
        """Test encode raises ImportError when sentence-transformers missing."""
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        with patch("corpus_forge.embedders.sentence_transformers.SENTENCE_TRANSFORMERS_AVAILABLE", False):
            with pytest.raises(ImportError, match="sentence-transformers package is required"):
                embedder.encode(["hello"])


class TestSentenceTransformersWarmup:
    def test_warmup_loads_model(self):
        """Test that warmup loads the model."""
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            pytest.skip("sentence-transformers not installed")
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        with patch("corpus_forge.embedders.sentence_transformers.SentenceTransformer", return_value=mock_model):
            embedder.warmup()
            assert embedder._model is mock_model

    def test_warmup_runs_dummy_inference(self):
        """Test that warmup runs a dummy encode."""
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            pytest.skip("sentence-transformers not installed")
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        mock_model = MagicMock()
        mock_model.get_sentence_embedding_dimension.return_value = 384
        with patch("corpus_forge.embedders.sentence_transformers.SentenceTransformer", return_value=mock_model):
            embedder.warmup()
            mock_model.encode.assert_called_once()
            call_args = mock_model.encode.call_args
            assert call_args[0][0] == ["warmup"]

    def test_warmup_noop_when_package_missing(self):
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        with patch("corpus_forge.embedders.sentence_transformers.SENTENCE_TRANSFORMERS_AVAILABLE", False):
            embedder.warmup()
            assert embedder._model is None
