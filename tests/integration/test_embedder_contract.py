"""Contract tests for embedder implementations against the Embedder Protocol."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from corpus_forge.embedders.base import BaseEmbedder
from corpus_forge.embedders.registry import EmbedderRegistry

pytestmark = pytest.mark.integration


# ── Base embedder ────────────────────────────────────────────────────────────


class TestBaseEmbedder:
    def test_default_attributes(self):
        e = BaseEmbedder(
            name="test",
            provider="sentence_transformers",
            model_id="test/model",
            dimension=384,
        )
        assert e.name == "test"
        assert e.provider == "sentence_transformers"
        assert e.model_id == "test/model"
        assert e.dimension == 384
        assert e.normalized is True
        assert e.distance == "cosine"

    def test_custom_attributes(self):
        e = BaseEmbedder(
            name="test",
            provider="openai",
            model_id="text-embedding-3-small",
            dimension=1536,
            normalized=False,
            distance="l2",
        )
        assert e.normalized is False
        assert e.distance == "l2"

    def test_warmup_noop(self):
        e = BaseEmbedder(name="test", provider="test", model_id="t/m", dimension=384)
        e.warmup()  # Should not raise


# ── Registry ─────────────────────────────────────────────────────────────────


class TestEmbedderRegistry:
    def test_register_sentence_transformers(self):
        registry = EmbedderRegistry()
        embedder = registry.register(
            name="st-1",
            provider="sentence_transformers",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        assert embedder is not None
        assert embedder.name == "st-1"
        assert len(registry.list_names()) == 1

    def test_register_openai(self):
        registry = EmbedderRegistry()
        embedder = registry.register(
            name="oa-1",
            provider="openai",
            model_id="text-embedding-3-large",
            dimension=3072,
        )
        assert embedder is not None
        assert embedder.name == "oa-1"

    def test_register_unknown_provider_raises(self):
        registry = EmbedderRegistry()
        with pytest.raises(ValueError, match="Unknown embedder provider"):
            registry.register(
                name="bad",
                provider="unknown",
                model_id="x/y",
                dimension=128,
            )

    def test_get_returns_registered(self):
        registry = EmbedderRegistry()
        registry.register(
            name="get-test",
            provider="sentence_transformers",
            model_id="t/m",
            dimension=384,
        )
        result = registry.get("get-test")
        assert result is not None
        assert result.name == "get-test"

    def test_get_returns_none_for_missing(self):
        registry = EmbedderRegistry()
        assert registry.get("nonexistent") is None

    def test_clear_removes_all(self):
        registry = EmbedderRegistry()
        registry.register(
            name="c1",
            provider="sentence_transformers",
            model_id="t/m",
            dimension=384,
        )
        registry.register(
            name="c2",
            provider="openai",
            model_id="t/m",
            dimension=384,
        )
        registry.clear()
        assert len(registry.list_names()) == 0

    def test_duplicate_register_overwrites(self):
        registry = EmbedderRegistry()
        e1 = registry.register(
            name="dup",
            provider="sentence_transformers",
            model_id="v1",
            dimension=384,
        )
        e2 = registry.register(
            name="dup",
            provider="openai",
            model_id="v2",
            dimension=1536,
        )
        assert e1 is e2  # Same instance overwritten
        assert e1.provider == "openai"
        assert e1.model_id == "v2"


# ── SentenceTransformersEmbedder contract ────────────────────────────────────


class TestSentenceTransformersEmbedderContract:
    """Verify the SentenceTransformersEmbedder conforms to the Embedder Protocol."""

    @pytest.fixture
    def embedder(self):
        """Return a SentenceTransformersEmbedder with mocked model loading."""
        with patch("corpus_forge.embedders.sentence_transformers.SentenceTransformer") as MockST:
            mock_model = MagicMock()
            mock_model.get_sentence_embedding_dimension.return_value = 384
            mock_model.encode.return_value = np.random.randn(2, 384).astype(np.float32)
            MockST.return_value = mock_model

            from corpus_forge.embedders.sentence_transformers import SentenceTransformersEmbedder

            embedder = SentenceTransformersEmbedder(
                name="st-contract",
                model_id="BAAI/bge-small-en-v1.5",
                dimension=384,
                device="cpu",
            )
            return embedder

    def test_encode_returns_numpy_array(self, embedder):
        result = embedder.encode(["hello", "world"])
        assert isinstance(result, np.ndarray)

    def test_encode_output_shape(self, embedder):
        result = embedder.encode(["hello", "world"])
        assert result.shape[0] == 2
        assert result.shape[1] == 384

    def test_encode_single_text(self, embedder):
        result = embedder.encode(["single"])
        assert result.shape[0] == 1
        assert result.shape[1] == 384

    def test_encode_empty_list(self, embedder):
        result = embedder.encode([])
        assert result.shape[0] == 0
        assert result.shape[1] == 384

    def test_warmup_loads_model(self, embedder):
        with patch("corpus_forge.embedders.sentence_transformers.SentenceTransformer") as MockST:
            mock_model = MagicMock()
            mock_model.get_sentence_embedding_dimension.return_value = 384
            MockST.return_value = mock_model

            embedder.warmup()
            MockST.assert_called_once()

    def test_attributes_set_correctly(self, embedder):
        assert embedder.name == "st-contract"
        assert embedder.provider == "sentence_transformers"
        assert embedder.dimension == 384
        assert embedder.normalized is True
        assert embedder.distance == "cosine"
