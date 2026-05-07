"""Unit tests for OpenAI embedder with mocked client."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from corpus_forge.embedders.openai import OPENAI_AVAILABLE, OpenAIEmbedder


class TestOpenAIEmbedderInit:
    def test_init_default_batch_size(self):
        embedder = OpenAIEmbedder(
            name="test",
            model_id="text-embedding-3-small",
            dimension=1536,
        )
        assert embedder.batch_size == 256

    def test_init_custom_batch_size(self):
        embedder = OpenAIEmbedder(
            name="test",
            model_id="text-embedding-3-small",
            dimension=1536,
            batch_size=64,
        )
        assert embedder.batch_size == 64

    def test_init_sets_api_key_env(self):
        embedder = OpenAIEmbedder(
            name="test",
            model_id="text-embedding-3-small",
            dimension=1536,
            api_key_env="MY_KEY",
        )
        assert embedder.api_key_env == "MY_KEY"


class TestOpenAIClient:
    def test_get_client_raises_without_key(self):
        import os
        orig = os.environ.pop("OPENAI_API_KEY", None)
        try:
            if not OPENAI_AVAILABLE:
                pytest.skip("openai not installed")
            embedder = OpenAIEmbedder(
                name="test",
                model_id="text-embedding-3-small",
                dimension=1536,
            )
            with pytest.raises(ValueError, match="API key not found"):
                embedder._get_client()
        finally:
            if orig is not None:
                os.environ["OPENAI_API_KEY"] = orig

    def test_get_client_returns_none_without_package(self):
        embedder = OpenAIEmbedder(
            name="test",
            model_id="text-embedding-3-small",
            dimension=1536,
        )
        # Without openai installed, _get_client returns None
        with patch("corpus_forge.embedders.openai.OPENAI_AVAILABLE", False):
            result = embedder._get_client()
            assert result is None


class TestOpenAIEncode:
    @pytest.fixture
    def embedder_with_mocked_client(self):
        """Create an embedder with a mocked OpenAI client."""
        if not OPENAI_AVAILABLE:
            pytest.skip("openai not installed")
        import os
        os.environ["OPENAI_API_KEY"] = "fake-key"
        embedder = OpenAIEmbedder(
            name="test",
            model_id="text-embedding-3-small",
            dimension=1536,
        )
        # Mock the client
        mock_item = MagicMock()
        mock_item.embedding = [0.1] * 1536
        mock_response = MagicMock()
        mock_response.data = [mock_item, mock_item]
        embedder._client = MagicMock()
        embedder._client.embeddings.create.return_value = mock_response
        return embedder

    def test_encode_returns_numpy_array(self, embedder_with_mocked_client):
        result = embedder_with_mocked_client.encode(["hello", "world"])
        assert isinstance(result, np.ndarray)
        assert result.shape == (2, 1536)

    def test_encode_normalizes_by_default(self, embedder_with_mocked_client):
        embedder_with_mocked_client.normalized = True
        # Create embeddings with known values
        mock_item = MagicMock()
        mock_item.embedding = [1.0] * 1536
        mock_response = MagicMock()
        mock_response.data = [mock_item, mock_item]
        embedder_with_mocked_client._client.embeddings.create.return_value = mock_response

        result = embedder_with_mocked_client.encode(["a", "b"])
        # Check that vectors are normalized (norm ≈ 1)
        norms = np.linalg.norm(result, axis=1)
        assert np.allclose(norms, 1.0)

    def test_encode_does_not_normalize_when_disabled(self, embedder_with_mocked_client):
        embedder_with_mocked_client.normalized = False
        mock_item = MagicMock()
        mock_item.embedding = [2.0] * 1536
        mock_response = MagicMock()
        mock_response.data = [mock_item]
        embedder_with_mocked_client._client.embeddings.create.return_value = mock_response

        result = embedder_with_mocked_client.encode(["hello"])
        # Should not be normalized
        norm = np.linalg.norm(result[0])
        assert norm != pytest.approx(1.0)

    def test_encode_batches_large_input(self, embedder_with_mocked_client):
        """Test that large inputs are processed in batches."""
        embedder_with_mocked_client.batch_size = 2

        def make_response(*args, **kwargs):
            batch_input = kwargs.get("input", args[0] if args else [])
            mock_items = [MagicMock() for _ in batch_input]
            for item in mock_items:
                item.embedding = [0.1] * 1536
            resp = MagicMock()
            resp.data = mock_items
            return resp

        embedder_with_mocked_client._client.embeddings.create.side_effect = make_response

        # 5 texts with batch_size=2 should make 3 calls (2+2+1)
        result = embedder_with_mocked_client.encode(["t" + str(i) for i in range(5)])
        assert len(result) == 5
        assert embedder_with_mocked_client._client.embeddings.create.call_count == 3

    def test_encode_dimension_mismatch_raises(self):
        """Test that wrong dimension raises ValueError."""
        if not OPENAI_AVAILABLE:
            pytest.skip("openai not installed")
        import os
        os.environ["OPENAI_API_KEY"] = "fake-key"
        embedder = OpenAIEmbedder(
            name="test",
            model_id="text-embedding-3-small",
            dimension=512,  # wrong dimension
        )
        mock_item = MagicMock()
        mock_item.embedding = [0.1] * 1536  # actual dim != expected
        mock_response = MagicMock()
        mock_response.data = [mock_item]
        embedder._client = MagicMock()
        embedder._client.embeddings.create.return_value = mock_response

        with pytest.raises(ValueError, match="produced embeddings of dimension"):
            embedder.encode(["hello"])

    def test_encode_raises_when_no_client(self):
        """Test encode raises RuntimeError when client is None."""
        embedder = OpenAIEmbedder(
            name="test",
            model_id="text-embedding-3-small",
            dimension=1536,
        )
        # Ensure openai is available so _get_client won't short-circuit
        with patch("corpus_forge.embedders.openai.OPENAI_AVAILABLE", True):
            # Set a fake API key so _get_client doesn't raise ValueError
            import os
            orig = os.environ.pop("OPENAI_API_KEY", None)
            try:
                os.environ["OPENAI_API_KEY"] = "fake"
                embedder._client = None
                # _get_client will create a real client with fake key
                # which will fail on encode, but we need to test the
                # RuntimeError path specifically
                with patch.object(embedder, "_get_client", return_value=None):
                    with pytest.raises(RuntimeError, match="Failed to initialize OpenAI client"):
                        embedder.encode(["hello"])
            finally:
                if orig is not None:
                    os.environ["OPENAI_API_KEY"] = orig

    def test_encode_raises_when_openai_not_installed(self):
        """Test encode raises ImportError when openai package missing."""
        embedder = OpenAIEmbedder(
            name="test",
            model_id="text-embedding-3-small",
            dimension=1536,
        )
        # Mock OPENAI_AVAILABLE as False
        with patch("corpus_forge.embedders.openai.OPENAI_AVAILABLE", False):
            with pytest.raises(ImportError, match="openai package is required"):
                embedder.encode(["hello"])
