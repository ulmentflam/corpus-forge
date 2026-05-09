"""Unit tests for embedder registry and base classes."""

import pytest

from corpus_forge.embedders.base import BaseEmbedder
from corpus_forge.embedders.registry import EmbedderRegistry


class TestEmbedderRegistry:
    """Tests for EmbedderRegistry class."""

    def test_register_sentence_transformers(self):
        """Test registering a sentence transformers embedder."""
        registry = EmbedderRegistry()
        embedder = registry.register(
            name="test-st",
            provider="sentence_transformers",
            model_id="test-model",
            dimension=384,
        )
        assert embedder.name == "test-st"
        assert embedder.provider == "sentence_transformers"
        assert embedder.model_id == "test-model"
        assert embedder.dimension == 384

    def test_register_openai(self):
        """Test registering an OpenAI embedder."""
        registry = EmbedderRegistry()
        embedder = registry.register(
            name="test-openai",
            provider="openai",
            model_id="text-embedding-3-small",
            dimension=1536,
        )
        assert embedder.name == "test-openai"
        assert embedder.provider == "openai"
        assert embedder.model_id == "text-embedding-3-small"
        assert embedder.dimension == 1536

    def test_register_invalid_provider(self):
        """Test that invalid provider raises ValueError."""
        registry = EmbedderRegistry()
        with pytest.raises(ValueError, match="Unknown embedder provider"):
            registry.register(
                name="test-invalid",
                provider="invalid_provider",
                model_id="test-model",
                dimension=384,
            )

    def test_get_registered_embedder(self):
        """Test getting a registered embedder by name."""
        registry = EmbedderRegistry()
        registry.register(
            name="test-embedder",
            provider="sentence_transformers",
            model_id="test-model",
            dimension=384,
        )
        embedder = registry.get("test-embedder")
        assert embedder is not None
        assert embedder.name == "test-embedder"

    def test_get_unregistered_embedder(self):
        """Test getting an unregistered embedder returns None."""
        registry = EmbedderRegistry()
        embedder = registry.get("nonexistent")
        assert embedder is None

    def test_list_names(self):
        """Test listing all registered embedder names."""
        registry = EmbedderRegistry()
        registry.register(
            name="embedder-1",
            provider="sentence_transformers",
            model_id="model-1",
            dimension=384,
        )
        registry.register(
            name="embedder-2",
            provider="openai",
            model_id="model-2",
            dimension=1536,
        )
        names = registry.list_names()
        assert "embedder-1" in names
        assert "embedder-2" in names
        assert len(names) == 2

    def test_clear(self):
        """Test clearing all registered embedders."""
        registry = EmbedderRegistry()
        registry.register(
            name="embedder-1",
            provider="sentence_transformers",
            model_id="model-1",
            dimension=384,
        )
        registry.clear()
        assert registry.list_names() == []
        assert registry.get("embedder-1") is None

    def test_default_values(self):
        """Test default values for embedder configuration."""
        registry = EmbedderRegistry()
        embedder = registry.register(
            name="test-defaults",
            provider="sentence_transformers",
            model_id="test-model",
            dimension=384,
        )
        # Check that default values are set correctly
        assert embedder.normalized is True
        assert embedder.distance == "cosine"


class TestBaseEmbedder:
    """Tests for BaseEmbedder class."""

    def test_base_embedder_init(self):
        """Test BaseEmbedder initialization."""
        embedder = BaseEmbedder(
            name="test",
            provider="sentence_transformers",
            model_id="test-model",
            dimension=384,
        )
        assert embedder.name == "test"
        assert embedder.provider == "sentence_transformers"
        assert embedder.model_id == "test-model"
        assert embedder.dimension == 384
        assert embedder.normalized is True
        assert embedder.distance == "cosine"

    def test_base_embedder_custom_values(self):
        """Test BaseEmbedder with custom values."""
        embedder = BaseEmbedder(
            name="test-custom",
            provider="openai",
            model_id="text-embedding-3-large",
            dimension=3072,
            normalized=False,
            distance="l2",
        )
        assert embedder.normalized is False
        assert embedder.distance == "l2"

    def test_base_embedder_warmup_noop(self):
        """Test that BaseEmbedder.warmup is a no-op."""
        embedder = BaseEmbedder(
            name="test",
            provider="sentence_transformers",
            model_id="test-model",
            dimension=384,
        )
        # Should not raise
        embedder.warmup()
