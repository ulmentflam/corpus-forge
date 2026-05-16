"""Unit tests for :class:`QwenCoderLocal` — Phase H / H-02.

All HTTP traffic is mocked via :func:`unittest.mock.patch`. Live-Ollama
round-trip lives in ``tests/integration/test_enrich_e2e.py`` under the
``requires_qwen_coder`` marker.

Coverage matrix:

- Class metadata: ``name == "qwen-local"``, defaults match plan.
- Happy path: well-formed JSON-in-JSON response → populated
  :class:`CodeChunkEnrichment`.
- Output validation: malformed inner JSON → graceful fallback (no
  exception).
- Output validation: invalid inner shape → graceful fallback.
- Transport: ``requests.Timeout`` → :class:`EnricherTimeoutError`.
- Transport: ``requests.ConnectionError`` → :class:`EnricherUnavailableError`.
- Transport: non-2xx HTTP (4xx and 5xx) → :class:`EnricherResponseError`.
- Transport: malformed outer JSON → :class:`EnricherResponseError`.
- Transport: missing ``"response"`` key → :class:`EnricherResponseError`.
- URL composition: non-default ``llm_url`` is honoured.
- URL composition: trailing slash stripped.
- Prompt construction: includes the chunk text + language tag + 9-key
  schema instructions.
- Payload shape: ``stream=false``, ``format=json``, ``num_ctx=16384``,
  temperature forwarded.
- ``warmup`` raises ``EnricherUnavailableError`` when model is absent.
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
from corpus_forge.enrichers.qwen_local import QwenCoderLocal


def _chunk(text: str = "def foo():\n    return 1\n") -> TextChunk:
    return TextChunk(text=text, metadata={"kind": "Function", "name": "foo"})


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


def _ok_response(inner_json: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    body = {"response": inner_json if inner_json is not None else _inner()}
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
# Class metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_name_is_qwen_local(self) -> None:
        assert QwenCoderLocal().name == "qwen-local"

    def test_defaults_match_plan(self) -> None:
        q = QwenCoderLocal()
        assert q.model == "qwen3.6:35b-a3b-instruct"
        assert q.llm_url == "http://localhost:11434"
        assert q.timeout_s == 180.0
        assert q.temperature == 0.1

    def test_trailing_slash_stripped(self) -> None:
        q = QwenCoderLocal(llm_url="http://localhost:11434/")
        assert q.llm_url == "http://localhost:11434"

    def test_non_default_url_honoured(self) -> None:
        q = QwenCoderLocal(llm_url="http://gpu.local:11434")
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response()
            q.enrich(_chunk(), language="python")
        args, _kwargs = mock_post.call_args
        # First positional arg is the URL.
        assert args[0] == "http://gpu.local:11434/api/generate"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_returns_well_formed_enrichment(self) -> None:
        q = QwenCoderLocal()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response(
                _inner(
                    docstring="Adds two ints.",
                    summary="Adds.",
                    symbols=["foo", "bar"],
                    confidence=0.91,
                )
            )
            result = q.enrich(_chunk(), language="python")
        assert isinstance(result, CodeChunkEnrichment)
        assert result.docstring == "Adds two ints."
        assert result.summary == "Adds."
        assert result.symbols == ["foo", "bar"]
        assert result.confidence == pytest.approx(0.91)
        assert result.model == "qwen3.6:35b-a3b-instruct"

    def test_payload_shape(self) -> None:
        q = QwenCoderLocal(model="qwen-coder:custom", temperature=0.2)
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response()
            q.enrich(_chunk(), language="rust")
        payload = mock_post.call_args.kwargs.get("json")
        assert payload["model"] == "qwen-coder:custom"
        assert payload["stream"] is False
        assert payload["format"] == "json"
        assert payload["options"]["temperature"] == 0.2
        assert payload["options"]["num_ctx"] == 16384


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestPrompt:
    def test_prompt_contains_chunk_text(self) -> None:
        q = QwenCoderLocal()
        chunk = _chunk("def unique_marker_zzz(): return 7\n")
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response()
            q.enrich(chunk, language="python")
        prompt = mock_post.call_args.kwargs["json"]["prompt"]
        assert "unique_marker_zzz" in prompt

    def test_prompt_contains_language(self) -> None:
        q = QwenCoderLocal()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response()
            q.enrich(_chunk(), language="rust")
        prompt = mock_post.call_args.kwargs["json"]["prompt"]
        assert "rust" in prompt.lower()

    def test_prompt_mentions_schema_keys(self) -> None:
        q = QwenCoderLocal()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response()
            q.enrich(_chunk(), language="python")
        prompt = mock_post.call_args.kwargs["json"]["prompt"]
        # All four output keys appear in the schema docstring.
        for key in ("docstring", "summary", "symbols", "confidence"):
            assert key in prompt

    def test_empty_language_falls_back_to_unknown(self) -> None:
        q = QwenCoderLocal()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response()
            q.enrich(_chunk(), language="")
        prompt = mock_post.call_args.kwargs["json"]["prompt"]
        assert "unknown" in prompt.lower()


# ---------------------------------------------------------------------------
# Output validation — graceful fallback
# ---------------------------------------------------------------------------


class TestOutputValidation:
    def test_malformed_inner_json_falls_back(self, caplog: pytest.LogCaptureFixture) -> None:
        q = QwenCoderLocal()
        with (
            patch("requests.post") as mock_post,
            caplog.at_level("WARNING", logger="corpus_forge.enrichers.base"),
        ):
            mock_post.return_value = _ok_response("not valid json")
            result = q.enrich(_chunk(), language="python")
        assert isinstance(result, CodeChunkEnrichment)
        assert result.summary == "invalid LLM output"
        assert result.confidence == 0.0
        assert any("invalid" in r.message.lower() for r in caplog.records)

    def test_invalid_structure_falls_back(self) -> None:
        q = QwenCoderLocal()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response('["not", "an", "object"]')
            result = q.enrich(_chunk(), language="python")
        assert result.summary == "invalid LLM output"
        assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Transport failures → typed exceptions
# ---------------------------------------------------------------------------


class TestTransport:
    def test_timeout_raises_enricher_timeout(self) -> None:
        import requests

        q = QwenCoderLocal()
        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.Timeout("slow")
            with pytest.raises(EnricherTimeoutError):
                q.enrich(_chunk(), language="python")

    def test_connection_error_raises_unavailable(self) -> None:
        import requests

        q = QwenCoderLocal()
        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.ConnectionError("refused")
            with pytest.raises(EnricherUnavailableError):
                q.enrich(_chunk(), language="python")

    def test_generic_request_exception_raises_unavailable(self) -> None:
        import requests

        q = QwenCoderLocal()
        with patch("requests.post") as mock_post:
            mock_post.side_effect = requests.RequestException("weird")
            with pytest.raises(EnricherUnavailableError):
                q.enrich(_chunk(), language="python")

    def test_4xx_raises_response_error(self) -> None:
        q = QwenCoderLocal()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _err_response(404, "not found")
            with pytest.raises(EnricherResponseError, match="404"):
                q.enrich(_chunk(), language="python")

    def test_5xx_raises_response_error(self) -> None:
        q = QwenCoderLocal()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _err_response(503, "service unavailable")
            with pytest.raises(EnricherResponseError, match="503"):
                q.enrich(_chunk(), language="python")

    def test_outer_non_json_raises_response_error(self) -> None:
        q = QwenCoderLocal()
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = "not json"
        resp.json.side_effect = ValueError("nope")
        with patch("requests.post") as mock_post:
            mock_post.return_value = resp
            with pytest.raises(EnricherResponseError, match=r"(?i)malformed (outer )?json"):
                q.enrich(_chunk(), language="python")

    def test_missing_response_key_raises_response_error(self) -> None:
        q = QwenCoderLocal()
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.return_value = {"unexpected": "envelope"}
        resp.text = "{}"
        with patch("requests.post") as mock_post:
            mock_post.return_value = resp
            with pytest.raises(EnricherResponseError, match="response"):
                q.enrich(_chunk(), language="python")


# ---------------------------------------------------------------------------
# warmup
# ---------------------------------------------------------------------------


class TestWarmup:
    def test_warmup_passes_when_model_present(self) -> None:
        q = QwenCoderLocal()
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.return_value = {"models": [{"name": "qwen3.6:35b-a3b-instruct"}]}
        with patch("requests.get") as mock_get:
            mock_get.return_value = resp
            # Should not raise.
            q.warmup()

    def test_warmup_raises_when_model_missing(self) -> None:
        q = QwenCoderLocal()
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.return_value = {"models": [{"name": "llama3:latest"}]}
        with patch("requests.get") as mock_get:
            mock_get.return_value = resp
            with pytest.raises(EnricherUnavailableError, match="ollama pull"):
                q.warmup()

    def test_warmup_raises_on_connection_error(self) -> None:
        import requests

        q = QwenCoderLocal()
        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.ConnectionError("down")
            with pytest.raises(EnricherUnavailableError):
                q.warmup()

    def test_warmup_raises_on_timeout(self) -> None:
        import requests

        q = QwenCoderLocal()
        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.Timeout("slow")
            with pytest.raises(EnricherUnavailableError):
                q.warmup()

    def test_warmup_raises_on_non_2xx(self) -> None:
        q = QwenCoderLocal()
        with patch("requests.get") as mock_get:
            mock_get.return_value = _err_response(500)
            with pytest.raises(EnricherUnavailableError, match="500"):
                q.warmup()

    def test_warmup_raises_on_generic_request_exception(self) -> None:
        import requests

        q = QwenCoderLocal()
        with patch("requests.get") as mock_get:
            mock_get.side_effect = requests.RequestException("weird")
            with pytest.raises(EnricherUnavailableError):
                q.warmup()

    def test_warmup_raises_on_non_json_tags_body(self) -> None:
        q = QwenCoderLocal()
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = "not json"
        resp.json.side_effect = ValueError("nope")
        with patch("requests.get") as mock_get:
            mock_get.return_value = resp
            with pytest.raises(EnricherUnavailableError, match=r"(?i)malformed json|non-json"):
                q.warmup()


# ---------------------------------------------------------------------------
# Inner response shape oddities (non-string ``response`` field).
# ---------------------------------------------------------------------------


class TestNonStringInner:
    def test_null_response_field_handled(self) -> None:
        """If the model returns ``"response": null`` we coerce to ``""``
        and fall back to the sentinel enrichment rather than crashing.
        """
        q = QwenCoderLocal()
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.return_value = {"response": None}
        resp.text = '{"response": null}'
        with patch("requests.post") as mock_post:
            mock_post.return_value = resp
            result = q.enrich(_chunk(), language="python")
        assert result.summary == "invalid LLM output"
        assert result.confidence == 0.0

    def test_non_string_response_field_coerced(self) -> None:
        q = QwenCoderLocal()
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        # The model returned a dict instead of a string for ``response``.
        resp.json.return_value = {"response": 42}
        resp.text = '{"response": 42}'
        with patch("requests.post") as mock_post:
            mock_post.return_value = resp
            result = q.enrich(_chunk(), language="python")
        # str(42) is "42" — not valid JSON → fallback.
        assert result.summary == "invalid LLM output"
