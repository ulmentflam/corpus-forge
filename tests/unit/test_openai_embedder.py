"""Unit tests for OpenAI embedder with mocked client."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from corpus_forge.embedders.openai import OpenAIEmbedder


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

    def test_local_base_url_tolerates_missing_env_var(self):
        """Local-substitution mode: when ``base_url`` is set we fall
        back to a placeholder key instead of raising. Lets users point
        the embedder at a local OpenAI-compat proxy (vLLM, llama.cpp)
        without inventing a fake key in secrets.env."""
        import os

        orig = os.environ.pop("OPENAI_API_KEY", None)
        try:
            embedder = OpenAIEmbedder(
                name="test",
                model_id="text-embedding-3-small",
                dimension=1536,
                base_url="http://localhost:8000/v1",
            )
            with patch("corpus_forge.embedders.openai.OpenAI") as mock_ctor:
                embedder._get_client()
            kwargs = mock_ctor.call_args.kwargs
            assert kwargs["base_url"] == "http://localhost:8000/v1"
            assert kwargs["api_key"] == "local-no-auth"
        finally:
            if orig is not None:
                os.environ["OPENAI_API_KEY"] = orig

    def test_base_url_forwarded_when_env_var_present(self):
        """``base_url`` plus a real key forwards both unchanged so a
        hosted authenticated proxy (LiteLLM, Azure) Just Works."""
        import os

        os.environ["MY_KEY"] = "sk-real"
        try:
            embedder = OpenAIEmbedder(
                name="test",
                model_id="text-embedding-3-small",
                dimension=1536,
                api_key_env="MY_KEY",
                base_url="https://proxy.example.com/v1",
            )
            with patch("corpus_forge.embedders.openai.OpenAI") as mock_ctor:
                embedder._get_client()
            kwargs = mock_ctor.call_args.kwargs
            assert kwargs["base_url"] == "https://proxy.example.com/v1"
            assert kwargs["api_key"] == "sk-real"
        finally:
            del os.environ["MY_KEY"]


class TestOpenAIEncode:
    @pytest.fixture
    def embedder_with_mocked_client(self, monkeypatch):
        """Create an embedder with a mocked OpenAI client.

        ``monkeypatch.setenv`` instead of a raw ``os.environ[...] = …``
        so ``OPENAI_API_KEY="fake-key"`` doesn't leak into the
        process env for tests that run after this fixture.
        """
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
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

    def test_encode_truncates_longer_native_vector_matryoshka(self, monkeypatch):
        """Servers that ignore ``dimensions=`` (e.g. Ollama's
        ``/v1/embeddings`` for ``qwen3-embedding:8b``) return the
        model's full native width. Matryoshka-trained models are
        prefix-coherent, so we slice + renormalise client-side instead
        of raising ``ValueError``. Failing this test would mean the
        whole local-Ollama happy path is broken again.
        """
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        embedder = OpenAIEmbedder(
            name="test",
            model_id="qwen3-embedding:8b",
            dimension=2000,  # requested via dimensions=…
        )
        mock_item = MagicMock()
        mock_item.embedding = [0.1] * 4096  # server returned native 4096
        mock_response = MagicMock()
        mock_response.data = [mock_item]
        embedder._client = MagicMock()
        embedder._client.embeddings.create.return_value = mock_response

        result = embedder.encode(["hello"])
        assert result.shape == (1, 2000)
        # Renormalisation is required after truncation; the result
        # should be unit-length when ``normalized=True`` (default).
        norm = np.linalg.norm(result[0])
        assert norm == pytest.approx(1.0)

    def test_encode_forwards_dimensions_to_api(self, monkeypatch):
        """Server-side Matryoshka: ``dimensions=`` is forwarded so any
        OpenAI-shape server that supports the field (real OpenAI,
        TEI) truncates *before* the wire. Local servers that don't
        know the field just ignore it and the client-side slice in
        ``test_encode_truncates_longer_native_vector_matryoshka``
        kicks in.
        """
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        embedder = OpenAIEmbedder(
            name="test",
            model_id="text-embedding-3-small",
            dimension=512,
        )
        mock_item = MagicMock()
        mock_item.embedding = [0.1] * 512  # server honoured dimensions=
        mock_response = MagicMock()
        mock_response.data = [mock_item]
        embedder._client = MagicMock()
        embedder._client.embeddings.create.return_value = mock_response

        embedder.encode(["hello"])
        call = embedder._client.embeddings.create.call_args
        assert call.kwargs["dimensions"] == 512

    def test_encode_raises_when_server_returns_shorter_vector(self, monkeypatch):
        """If the server returns FEWER dims than configured, that's a
        real config / model mismatch (no amount of slicing can recover
        the missing dims). Stays a hard error.
        """
        monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
        embedder = OpenAIEmbedder(
            name="test",
            model_id="some-tiny-model",
            dimension=2000,
        )
        mock_item = MagicMock()
        mock_item.embedding = [0.1] * 384  # too small
        mock_response = MagicMock()
        mock_response.data = [mock_item]
        embedder._client = MagicMock()
        embedder._client.embeddings.create.return_value = mock_response

        with pytest.raises(ValueError, match="produced embeddings of dimension"):
            embedder.encode(["hello"])

    def test_encode_raises_when_no_client(self, monkeypatch):
        """Test encode raises RuntimeError when client is None."""
        embedder = OpenAIEmbedder(
            name="test",
            model_id="text-embedding-3-small",
            dimension=1536,
        )
        # Set a fake API key so ``_get_client`` wouldn't raise
        # ``ValueError`` on its own. We then patch ``_get_client`` to
        # return None so the RuntimeError branch in ``encode`` is
        # exercised. ``monkeypatch.setenv`` cleans up after the test
        # so subsequent tests can't see ``OPENAI_API_KEY=fake``.
        monkeypatch.setenv("OPENAI_API_KEY", "fake")
        embedder._client = None
        with (
            patch.object(embedder, "_get_client", return_value=None),
            pytest.raises(RuntimeError, match="Failed to initialize OpenAI client"),
        ):
            embedder.encode(["hello"])
