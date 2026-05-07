"""Unit tests for embedder implementations."""

import pytest

from corpus_forge.embedders.openai import OPENAI_AVAILABLE, OpenAIEmbedder
from corpus_forge.embedders.sentence_transformers import (
    SENTENCE_TRANSFORMERS_AVAILABLE,
    SentenceTransformersEmbedder,
)


class TestSentenceTransformersEmbedder:
    """Tests for SentenceTransformersEmbedder class."""

    def test_init_defaults(self):
        """Test initialization with default values."""
        embedder = SentenceTransformersEmbedder(
            name="test-st",
            model_id="test-model",
            dimension=384,
        )
        assert embedder.name == "test-st"
        assert embedder.provider == "sentence_transformers"
        assert embedder.model_id == "test-model"
        assert embedder.dimension == 384  # noqa: PLR2004
        assert embedder.normalized is True
        assert embedder.distance == "cosine"
        assert embedder.device == "auto"
        assert embedder.batch_size == 32  # noqa: PLR2004

    def test_init_custom_values(self):
        """Test initialization with custom values."""
        embedder = SentenceTransformersEmbedder(
            name="test-custom",
            model_id="custom-model",
            dimension=768,
            normalized=False,
            distance="l2",
            device="cpu",
            batch_size=64,
        )
        assert embedder.normalized is False
        assert embedder.distance == "l2"
        assert embedder.device == "cpu"
        assert embedder.batch_size == 64  # noqa: PLR2004

    def test_model_not_available(self):
        """Test encode when sentence-transformers is not available."""
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            pytest.skip("sentence-transformers is available")
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="test-model",
            dimension=384,
        )
        with pytest.raises(ImportError, match="sentence-transformers package is required"):
            embedder.encode(["test"])

    def test_warmup_no_model(self):
        """Test warmup when model is not available."""
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            pytest.skip("sentence-transformers is available")
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="test-model",
            dimension=384,
        )
        # Should not raise
        embedder.warmup()

    def test_model_id_validation(self):
        """Test that model_id is stored correctly."""
        embedder = SentenceTransformersEmbedder(
            name="test",
            model_id="BAAI/bge-small-en-v1.5",
            dimension=384,
        )
        assert embedder.model_id == "BAAI/bge-small-en-v1.5"


class TestOpenAIEmbedder:
    """Tests for OpenAIEmbedder class."""

    def test_init_defaults(self):
        """Test initialization with default values."""
        embedder = OpenAIEmbedder(
            name="test-openai",
            model_id="text-embedding-3-small",
            dimension=1536,
        )
        assert embedder.name == "test-openai"
        assert embedder.provider == "openai"
        assert embedder.model_id == "text-embedding-3-small"
        assert embedder.dimension == 1536  # noqa: PLR2004
        assert embedder.normalized is True
        assert embedder.distance == "cosine"
        assert embedder.api_key_env == "OPENAI_API_KEY"
        assert embedder.batch_size == 256  # noqa: PLR2004

    def test_init_custom_values(self):
        """Test initialization with custom values."""
        embedder = OpenAIEmbedder(
            name="test-custom",
            model_id="text-embedding-3-large",
            dimension=3072,
            normalized=False,
            distance="ip",
            api_key_env="CUSTOM_API_KEY",
            batch_size=128,
        )
        assert embedder.normalized is False
        assert embedder.distance == "ip"
        assert embedder.api_key_env == "CUSTOM_API_KEY"
        assert embedder.batch_size == 128  # noqa: PLR2004

    def test_api_key_not_found(self):
        """Test that missing API key raises ValueError."""
        # Import here to avoid circular dependency with openai package
        import os  # noqa: PLC0415

        if "OPENAI_API_KEY" in os.environ:
            del os.environ["OPENAI_API_KEY"]
        if not OPENAI_AVAILABLE:
            pytest.skip("openai package not available")
        embedder = OpenAIEmbedder(
            name="test",
            model_id="text-embedding-3-small",
            dimension=1536,
        )
        with pytest.raises(ValueError, match="API key not found"):
            embedder._get_client()

    def test_warmup_noop_when_no_key(self):
        """Test warmup doesn't fail when API key is missing."""
        # Import here to avoid circular dependency with openai package
        import os  # noqa: PLC0415

        # Ensure no API key is set
        orig_key = os.environ.pop("OPENAI_API_KEY", None)
        try:
            if not OPENAI_AVAILABLE:
                pytest.skip("openai package not available")
            embedder = OpenAIEmbedder(
                name="test",
                model_id="text-embedding-3-small",
                dimension=1536,
            )
            # Should not raise even without API key
            embedder.warmup()
        finally:
            if orig_key is not None:
                os.environ["OPENAI_API_KEY"] = orig_key

    def test_init_normalized_false(self):
        """Test initialization with normalized=False."""
        embedder = OpenAIEmbedder(
            name="test",
            model_id="text-embedding-3-small",
            dimension=1536,
            normalized=False,
        )
        assert embedder.normalized is False

    def test_init_custom_distance(self):
        """Test initialization with custom distance metric."""
        embedder = OpenAIEmbedder(
            name="test",
            model_id="text-embedding-3-small",
            dimension=1536,
            distance="l2",
        )
        assert embedder.distance == "l2"
