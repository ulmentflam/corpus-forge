"""Unit tests for :class:`QwenCoderRemote` — Phase H / H-03.

Covers both API shapes (``ollama`` and ``openai``), bearer-auth
plumbing, and shared graceful-fallback semantics via
:func:`corpus_forge.enrichers.base._parse_enrichment_response`.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.chunkers.base import TextChunk
from corpus_forge.enrichers.base import (
    CodeChunkEnrichment,
    EnricherResponseError,
    EnricherTimeoutError,
    EnricherUnavailableError,
)
from corpus_forge.enrichers.qwen_remote import QwenCoderRemote


def _chunk(text: str = "def foo():\n    return 1\n") -> TextChunk:
    return TextChunk(text=text, metadata={})


def _inner(
    *,
    docstring: str | None = "Returns 1.",
    summary: str = "Returns the constant 1.",
    symbols: list[str] | None = None,
    confidence: float = 0.8,
) -> str:
    payload: dict[str, Any] = {
        "docstring": docstring,
        "summary": summary,
        "symbols": symbols or [],
        "confidence": confidence,
    }
    return json.dumps(payload)


def _ollama_ok(inner_json: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    body = {"response": inner_json if inner_json is not None else _inner()}
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def _openai_ok(inner_json: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    body = {
        "choices": [{"message": {"content": inner_json if inner_json is not None else _inner()}}]
    }
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def _err_response(status: int, body: str = "boom") -> MagicMock:
    resp = MagicMock()
    resp.ok = False
    resp.status_code = status
    resp.text = body
    resp.json.side_effect = ValueError("not json")
    return resp


# ---------------------------------------------------------------------------
# Construction-time validation
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_name_is_qwen_remote(self) -> None:
        assert QwenCoderRemote().name == "qwen-remote"

    def test_default_api_shape_is_ollama(self) -> None:
        assert QwenCoderRemote().api_shape == "ollama"

    def test_invalid_api_shape_raises(self) -> None:
        with pytest.raises(EnricherUnavailableError, match="api_shape"):
            QwenCoderRemote(api_shape="grpc")  # type: ignore[arg-type]

    def test_openai_without_api_key_raises(self) -> None:
        with pytest.raises(EnricherUnavailableError, match="api_key"):
            QwenCoderRemote(api_shape="openai", api_key=None)

    def test_openai_with_empty_api_key_raises(self) -> None:
        with pytest.raises(EnricherUnavailableError, match="api_key"):
            QwenCoderRemote(api_shape="openai", api_key="")

    def test_openai_with_api_key_constructs(self) -> None:
        q = QwenCoderRemote(api_shape="openai", api_key="sk-test")
        assert q.api_key == "sk-test"

    def test_ollama_tolerates_missing_api_key(self) -> None:
        q = QwenCoderRemote(api_shape="ollama", api_key=None)
        assert q.api_key is None

    def test_trailing_slash_stripped(self) -> None:
        q = QwenCoderRemote(base_url="http://x.example.com:11434/")
        assert q.base_url == "http://x.example.com:11434"


# ---------------------------------------------------------------------------
# Ollama shape
# ---------------------------------------------------------------------------


class TestOllamaShape:
    def test_happy_path(self) -> None:
        q = QwenCoderRemote(
            api_shape="ollama",
            base_url="http://remote.example.com:11434",
            api_key="bearer-token",
        )
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ollama_ok(
                _inner(docstring="d", summary="s", symbols=["foo"], confidence=0.7)
            )
            result = q.enrich(_chunk(), language="python")
        assert isinstance(result, CodeChunkEnrichment)
        assert result.summary == "s"
        # URL composition
        args, kwargs = mock_post.call_args
        assert args[0] == "http://remote.example.com:11434/api/generate"
        # Bearer header attached.
        headers = kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer bearer-token"
        # Payload shape mirrors local.
        payload = kwargs["json"]
        assert payload["stream"] is False
        assert payload["format"] == "json"
        assert payload["options"]["num_ctx"] == 16384

    def test_no_api_key_omits_auth_header(self) -> None:
        q = QwenCoderRemote(api_shape="ollama", api_key=None)
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ollama_ok()
            q.enrich(_chunk(), language="python")
        headers = mock_post.call_args.kwargs.get("headers") or {}
        assert "Authorization" not in headers

    def test_timeout(self) -> None:
        import requests

        q = QwenCoderRemote(api_shape="ollama")
        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.Timeout("slow")
            with pytest.raises(EnricherTimeoutError):
                q.enrich(_chunk(), language="python")

    def test_connection_error(self) -> None:
        import requests

        q = QwenCoderRemote(api_shape="ollama")
        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.ConnectionError("refused")
            with pytest.raises(EnricherUnavailableError):
                q.enrich(_chunk(), language="python")

    def test_non_2xx(self) -> None:
        q = QwenCoderRemote(api_shape="ollama")
        with patch("requests.post") as mock_post:
            mock_post.return_value = _err_response(500)
            with pytest.raises(EnricherResponseError, match="500"):
                q.enrich(_chunk(), language="python")

    def test_malformed_inner_falls_back(self) -> None:
        q = QwenCoderRemote(api_shape="ollama")
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ollama_ok("not json")
            result = q.enrich(_chunk(), language="python")
        assert result.summary == "invalid LLM output"
        assert result.confidence == 0.0

    def test_missing_response_key_raises(self) -> None:
        q = QwenCoderRemote(api_shape="ollama")
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.return_value = {"unexpected": True}
        resp.text = "{}"
        with patch("requests.post") as mock_post:
            mock_post.return_value = resp
            with pytest.raises(EnricherResponseError, match="response"):
                q.enrich(_chunk(), language="python")


# ---------------------------------------------------------------------------
# OpenAI shape
# ---------------------------------------------------------------------------


class TestOpenAIShape:
    def test_happy_path(self) -> None:
        q = QwenCoderRemote(
            api_shape="openai",
            base_url="https://api.openai.com/v1",
            api_key="sk-abc",
        )
        with patch("requests.post") as mock_post:
            mock_post.return_value = _openai_ok(
                _inner(docstring="d", summary="s", symbols=["foo"], confidence=0.5)
            )
            result = q.enrich(_chunk(), language="python")
        assert isinstance(result, CodeChunkEnrichment)
        assert result.summary == "s"
        # URL composition.
        args, kwargs = mock_post.call_args
        assert args[0] == "https://api.openai.com/v1/chat/completions"
        # Bearer header attached.
        headers = kwargs.get("headers") or {}
        assert headers.get("Authorization") == "Bearer sk-abc"
        # Payload shape uses messages + response_format.
        payload = kwargs["json"]
        assert payload["model"] == q.model
        assert isinstance(payload["messages"], list)
        assert payload["messages"][0]["role"] == "user"
        assert payload["response_format"] == {"type": "json_object"}

    def test_timeout(self) -> None:
        import requests

        q = QwenCoderRemote(api_shape="openai", api_key="sk-abc")
        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.Timeout("slow")
            with pytest.raises(EnricherTimeoutError):
                q.enrich(_chunk(), language="python")

    def test_4xx_raises(self) -> None:
        q = QwenCoderRemote(api_shape="openai", api_key="sk-abc")
        with patch("requests.post") as mock_post:
            mock_post.return_value = _err_response(401, "unauthorized")
            with pytest.raises(EnricherResponseError, match="401"):
                q.enrich(_chunk(), language="python")

    def test_missing_choices_raises(self) -> None:
        q = QwenCoderRemote(api_shape="openai", api_key="sk-abc")
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.return_value = {"unexpected": True}
        resp.text = "{}"
        with patch("requests.post") as mock_post:
            mock_post.return_value = resp
            with pytest.raises(EnricherResponseError, match="choices"):
                q.enrich(_chunk(), language="python")

    def test_empty_choices_raises(self) -> None:
        q = QwenCoderRemote(api_shape="openai", api_key="sk-abc")
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.return_value = {"choices": []}
        resp.text = "{}"
        with patch("requests.post") as mock_post:
            mock_post.return_value = resp
            with pytest.raises(EnricherResponseError, match="choices"):
                q.enrich(_chunk(), language="python")

    def test_malformed_inner_content_falls_back(self) -> None:
        q = QwenCoderRemote(api_shape="openai", api_key="sk-abc")
        with patch("requests.post") as mock_post:
            mock_post.return_value = _openai_ok("not json")
            result = q.enrich(_chunk(), language="python")
        assert result.summary == "invalid LLM output"
        assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Warmup
# ---------------------------------------------------------------------------


class TestWarmup:
    def test_ollama_warmup_succeeds_on_200(self) -> None:
        q = QwenCoderRemote(api_shape="ollama")
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.return_value = {"models": []}
        resp.text = "{}"
        with patch("requests.get") as mock_get:
            mock_get.return_value = resp
            q.warmup()

    def test_ollama_warmup_raises_on_non_2xx(self) -> None:
        q = QwenCoderRemote(api_shape="ollama")
        with patch("requests.get") as mock_get:
            mock_get.return_value = _err_response(500)
            with pytest.raises(EnricherUnavailableError, match="500"):
                q.warmup()

    def test_openai_warmup_is_noop(self) -> None:
        q = QwenCoderRemote(api_shape="openai", api_key="sk-x")
        with patch("requests.get") as mock_get:
            q.warmup()
        # No HTTP call attempted.
        assert mock_get.call_count == 0


# ---------------------------------------------------------------------------
# Prompt content
# ---------------------------------------------------------------------------


class TestExtraTransport:
    """Additional transport-failure coverage shared by both shapes."""

    def test_ollama_request_exception_raises_unavailable(self) -> None:
        import requests

        q = QwenCoderRemote(api_shape="ollama")
        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.RequestException("weird")
            with pytest.raises(EnricherUnavailableError):
                q.enrich(_chunk(), language="python")

    def test_openai_connection_error_raises_unavailable(self) -> None:
        import requests

        q = QwenCoderRemote(api_shape="openai", api_key="sk-x")
        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.ConnectionError("refused")
            with pytest.raises(EnricherUnavailableError):
                q.enrich(_chunk(), language="python")

    def test_openai_request_exception_raises_unavailable(self) -> None:
        import requests

        q = QwenCoderRemote(api_shape="openai", api_key="sk-x")
        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.RequestException("weird")
            with pytest.raises(EnricherUnavailableError):
                q.enrich(_chunk(), language="python")

    def test_ollama_malformed_outer_json_raises(self) -> None:
        q = QwenCoderRemote(api_shape="ollama")
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = "not json"
        resp.json.side_effect = ValueError("nope")
        with patch("requests.post") as mock_post:
            mock_post.return_value = resp
            with pytest.raises(EnricherResponseError, match="Malformed outer JSON"):
                q.enrich(_chunk(), language="python")

    def test_openai_malformed_outer_json_raises(self) -> None:
        q = QwenCoderRemote(api_shape="openai", api_key="sk-x")
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = "not json"
        resp.json.side_effect = ValueError("nope")
        with patch("requests.post") as mock_post:
            mock_post.return_value = resp
            with pytest.raises(EnricherResponseError, match="Malformed outer JSON"):
                q.enrich(_chunk(), language="python")

    def test_ollama_non_string_response_field_coerced(self) -> None:
        q = QwenCoderRemote(api_shape="ollama")
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.return_value = {"response": None}
        resp.text = '{"response": null}'
        with patch("requests.post") as mock_post:
            mock_post.return_value = resp
            result = q.enrich(_chunk(), language="python")
        assert result.summary == "invalid LLM output"

    def test_openai_non_string_message_content_coerced(self) -> None:
        q = QwenCoderRemote(api_shape="openai", api_key="sk-x")
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": {"content": None}}]}
        resp.text = "{}"
        with patch("requests.post") as mock_post:
            mock_post.return_value = resp
            result = q.enrich(_chunk(), language="python")
        assert result.summary == "invalid LLM output"

    def test_openai_non_object_choice_raises(self) -> None:
        q = QwenCoderRemote(api_shape="openai", api_key="sk-x")
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.return_value = {"choices": ["not-an-object"]}
        resp.text = "{}"
        with patch("requests.post") as mock_post:
            mock_post.return_value = resp
            with pytest.raises(EnricherResponseError, match="choices\\[0\\]"):
                q.enrich(_chunk(), language="python")

    def test_openai_non_object_message_raises(self) -> None:
        q = QwenCoderRemote(api_shape="openai", api_key="sk-x")
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.return_value = {"choices": [{"message": "not-an-object"}]}
        resp.text = "{}"
        with patch("requests.post") as mock_post:
            mock_post.return_value = resp
            with pytest.raises(EnricherResponseError, match="message"):
                q.enrich(_chunk(), language="python")

    def test_warmup_timeout_raises(self) -> None:
        import requests

        q = QwenCoderRemote(api_shape="ollama")
        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.Timeout("slow")
            with pytest.raises(EnricherUnavailableError):
                q.warmup()

    def test_warmup_connection_error_raises(self) -> None:
        import requests

        q = QwenCoderRemote(api_shape="ollama")
        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.ConnectionError("refused")
            with pytest.raises(EnricherUnavailableError):
                q.warmup()

    def test_warmup_request_exception_raises(self) -> None:
        import requests

        q = QwenCoderRemote(api_shape="ollama")
        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("weird")
            with pytest.raises(EnricherUnavailableError):
                q.warmup()


class TestPromptContent:
    def test_prompt_contains_chunk_text(self) -> None:
        q = QwenCoderRemote(api_shape="ollama")
        chunk = _chunk("def special_marker_qqq(): return 9\n")
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ollama_ok()
            q.enrich(chunk, language="python")
        prompt = mock_post.call_args.kwargs["json"]["prompt"]
        assert "special_marker_qqq" in prompt

    def test_openai_prompt_contains_chunk_text(self) -> None:
        q = QwenCoderRemote(api_shape="openai", api_key="sk-x")
        chunk = _chunk("def special_marker_qqq(): return 9\n")
        with patch("requests.post") as mock_post:
            mock_post.return_value = _openai_ok()
            q.enrich(chunk, language="python")
        msgs = mock_post.call_args.kwargs["json"]["messages"]
        assert "special_marker_qqq" in msgs[0]["content"]
