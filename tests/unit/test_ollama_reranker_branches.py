"""Branch tests for OllamaReranker._get_client (lines 111-125, 131).

Lines 111-125:
    111: if self._client is not None: return self._client  (cache hit)
    113-120: try/except ImportError
    122-124: OpenAI(base_url=..., api_key=...) construction
    125: return self._client

Line 131: warmup() calls _get_client()
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from corpus_forge.retrieval.rerank.ollama import OllamaReranker


class TestGetClientBody:
    def test_get_client_caches_instance(self):
        """Second _get_client call returns same instance (line 111)."""
        mock_openai_cls = MagicMock()
        mock_client_instance = MagicMock()
        mock_openai_cls.return_value = mock_client_instance

        with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_cls)}):
            r = OllamaReranker(model_id="llama3.1:8b")
            c1 = r._get_client()
            c2 = r._get_client()

        assert c1 is c2
        # OpenAI constructor should only have been called once
        assert mock_openai_cls.call_count == 1

    def test_get_client_constructs_openai_with_base_url_and_api_key(self):
        """OpenAI is constructed with the configured base_url (lines 122-124)."""
        mock_openai_cls = MagicMock()
        mock_openai_cls.return_value = MagicMock()

        with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_cls)}):
            r = OllamaReranker(model_id="llama3.1:8b", base_url="http://remote:11434/v1")
            r._get_client()

        mock_openai_cls.assert_called_once()
        kwargs = mock_openai_cls.call_args.kwargs
        assert kwargs.get("base_url") == "http://remote:11434/v1"
        assert kwargs.get("api_key") == "ollama-no-auth"

    def test_get_client_stores_instance_on_self(self):
        """After _get_client, self._client is non-None (line 124)."""
        mock_openai_cls = MagicMock()
        mock_client_instance = MagicMock()
        mock_openai_cls.return_value = mock_client_instance

        with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_cls)}):
            r = OllamaReranker(model_id="llama3.1:8b")
            assert r._client is None  # not yet constructed
            r._get_client()
            assert r._client is mock_client_instance


class TestWarmupOllama:
    def test_warmup_calls_get_client(self):
        """warmup() calls _get_client() without any network round-trip (line 131)."""
        with patch.object(OllamaReranker, "_get_client") as mock:
            r = OllamaReranker(model_id="llama3.1:8b")
            r.warmup()
        mock.assert_called_once()

    def test_warmup_uses_real_client_construction_path(self):
        """Verify warmup stores the client on the instance."""
        mock_openai_cls = MagicMock(return_value=MagicMock())
        with patch.dict("sys.modules", {"openai": MagicMock(OpenAI=mock_openai_cls)}):
            r = OllamaReranker(model_id="llama3.1:8b")
            r.warmup()
        assert r._client is not None
