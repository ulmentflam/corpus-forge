"""Phase D / Wave 4 (E-02) — :class:`OllamaVLM` HTTP behaviour.

Every HTTP call goes through ``requests``. All tests in this module
patch ``requests.post`` / ``requests.get`` via ``unittest.mock`` — no
live network is ever required, no Ollama daemon is contacted.

The backend's failure modes are the highest-risk surface in Phase D
because OCR happens at ingest-time and a hard failure means a document
silently goes missing. Every ``requests`` exception type the backend
can encounter is mapped to a custom :class:`VLMError` subclass and
asserted here.
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.vlm import (
    VLMResponseError,
    VLMTimeoutError,
    VLMUnavailableError,
)
from corpus_forge.vlm.ollama import OllamaVLM

# ── Helpers ────────────────────────────────────────────────────────────


def _ok_response(body: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.text = json.dumps(body)
    resp.json.return_value = body
    return resp


def _err_response(status: int, body: str = "internal error") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.ok = 200 <= status < 300
    resp.text = body
    resp.json.side_effect = ValueError("not JSON")
    return resp


# ── Construction ───────────────────────────────────────────────────────


class TestConstruction:
    def test_name_is_ollama(self):
        assert OllamaVLM().name == "ollama"

    def test_default_model(self):
        v = OllamaVLM()
        assert v.model == "qwen2.5vl:7b"

    def test_default_url(self):
        v = OllamaVLM()
        assert v.ollama_url == "http://localhost:11434"

    def test_default_timeout(self):
        v = OllamaVLM()
        assert v.timeout_s == 120.0

    def test_default_temperature(self):
        v = OllamaVLM()
        assert v.temperature == 0.0

    def test_override_all_args(self):
        v = OllamaVLM(
            model="qwen2.5vl:32b",
            ollama_url="http://gpu:11434",
            timeout_s=60.0,
            temperature=0.2,
        )
        assert v.model == "qwen2.5vl:32b"
        assert v.ollama_url == "http://gpu:11434"
        assert v.timeout_s == 60.0
        assert v.temperature == 0.2

    def test_construction_does_not_call_requests(self):
        """``__init__`` must NOT make any network calls — caller controls
        when ``warmup()`` runs."""
        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            OllamaVLM()
            assert mock_post.call_count == 0
            assert mock_get.call_count == 0


# ── warmup() ───────────────────────────────────────────────────────────


class TestWarmup:
    def test_happy_path_pings_api_tags(self):
        """``warmup()`` GETs ``/api/tags`` and verifies the configured
        model appears in the response."""
        body = {
            "models": [
                {"name": "qwen2.5vl:7b", "size": 1234},
                {"name": "llama3:8b", "size": 5678},
            ]
        }
        with patch("requests.get", return_value=_ok_response(body)) as mock_get:
            v = OllamaVLM(model="qwen2.5vl:7b")
            v.warmup()
            mock_get.assert_called_once()
            url = mock_get.call_args[0][0]
            assert url.endswith("/api/tags")

    def test_warmup_uses_configured_url(self):
        body = {"models": [{"name": "qwen2.5vl:7b"}]}
        with patch("requests.get", return_value=_ok_response(body)) as mock_get:
            v = OllamaVLM(model="qwen2.5vl:7b", ollama_url="http://gpu.local:11434")
            v.warmup()
            url = mock_get.call_args[0][0]
            assert url.startswith("http://gpu.local:11434")

    def test_warmup_strips_trailing_slash_in_url(self):
        """The constructor must tolerate trailing slashes in the URL —
        ``http://localhost:11434/`` and ``http://localhost:11434`` both
        produce the same GET target."""
        body = {"models": [{"name": "qwen2.5vl:7b"}]}
        with patch("requests.get", return_value=_ok_response(body)) as mock_get:
            v = OllamaVLM(model="qwen2.5vl:7b", ollama_url="http://localhost:11434/")
            v.warmup()
            url = mock_get.call_args[0][0]
            # Exactly one slash before /api/tags
            assert url.count("/api/tags") == 1
            assert "//api/tags" not in url

    def test_model_missing_raises_unavailable(self):
        """If the configured model isn't installed in the daemon, raise
        :class:`VLMUnavailableError` with a helpful message."""
        body = {"models": [{"name": "llama3:8b"}]}
        with patch("requests.get", return_value=_ok_response(body)):
            v = OllamaVLM(model="qwen2.5vl:7b")
            with pytest.raises(VLMUnavailableError, match=r"qwen2\.5vl:7b"):
                v.warmup()

    def test_empty_model_list_raises_unavailable(self):
        with patch("requests.get", return_value=_ok_response({"models": []})):
            v = OllamaVLM(model="qwen2.5vl:7b")
            with pytest.raises(VLMUnavailableError):
                v.warmup()

    def test_connection_error_raises_unavailable(self):
        import requests as _requests

        with patch("requests.get", side_effect=_requests.ConnectionError("refused")):
            v = OllamaVLM()
            with pytest.raises(VLMUnavailableError, match=r"(?i)connect|unreachable|down"):
                v.warmup()

    def test_timeout_during_warmup_raises_unavailable(self):
        """Health-check timeout signals "daemon not reachable" → map to
        :class:`VLMUnavailableError`, not :class:`VLMTimeoutError`. The
        Timeout class is reserved for actual generation requests where
        the budget matters."""
        import requests as _requests

        with patch("requests.get", side_effect=_requests.Timeout("slow")):
            v = OllamaVLM()
            with pytest.raises(VLMUnavailableError):
                v.warmup()

    def test_non_2xx_during_warmup_raises_unavailable(self):
        with patch("requests.get", return_value=_err_response(500)):
            v = OllamaVLM()
            with pytest.raises(VLMUnavailableError):
                v.warmup()

    def test_generic_request_exception_during_warmup_raises_unavailable(self):
        """Catch-all branch: any other ``requests.RequestException`` at
        warmup (e.g. ``InvalidURL``) is also "daemon unreachable"."""
        import requests as _requests

        with patch("requests.get", side_effect=_requests.RequestException("weird")):
            v = OllamaVLM()
            with pytest.raises(VLMUnavailableError):
                v.warmup()

    def test_malformed_json_during_warmup_raises_unavailable(self):
        """``/api/tags`` returning non-JSON is also a "daemon unhealthy"
        signal, not a generation-time response error."""
        resp = MagicMock()
        resp.status_code = 200
        resp.ok = True
        resp.text = "<html>nginx says hi</html>"
        resp.json.side_effect = ValueError("not JSON")
        with patch("requests.get", return_value=resp):
            v = OllamaVLM()
            with pytest.raises(VLMUnavailableError):
                v.warmup()


# ── describe_image() ───────────────────────────────────────────────────


class TestDescribeImage:
    _PNG = b"\x89PNG\r\n\x1a\nFAKE_IMAGE_BYTES"

    def test_happy_path_returns_response_text(self):
        body = {"response": "# Heading\n\nbody text", "done": True}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = OllamaVLM()
            out = v.describe_image(self._PNG)
            assert out == "# Heading\n\nbody text"
            mock_post.assert_called_once()

    def test_request_targets_generate_endpoint(self):
        body = {"response": "ok"}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = OllamaVLM(ollama_url="http://x:11434")
            v.describe_image(self._PNG)
            url = mock_post.call_args[0][0]
            assert url == "http://x:11434/api/generate"

    def test_request_body_contains_base64_image(self):
        body = {"response": "ok"}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = OllamaVLM()
            v.describe_image(self._PNG)
            payload = mock_post.call_args.kwargs.get("json")
            assert payload is not None
            expected_b64 = base64.b64encode(self._PNG).decode("ascii")
            assert payload["images"] == [expected_b64]

    def test_request_body_carries_model_and_stream_false(self):
        body = {"response": "ok"}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = OllamaVLM(model="qwen2.5vl:32b")
            v.describe_image(self._PNG)
            payload = mock_post.call_args.kwargs["json"]
            assert payload["model"] == "qwen2.5vl:32b"
            assert payload["stream"] is False

    def test_request_body_pins_temperature_and_num_ctx(self):
        body = {"response": "ok"}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = OllamaVLM(temperature=0.0)
            v.describe_image(self._PNG)
            payload = mock_post.call_args.kwargs["json"]
            options = payload.get("options", {})
            assert options.get("temperature") == 0.0
            # num_ctx must be set so long pages don't truncate.
            assert options.get("num_ctx", 0) >= 8192

    def test_default_prompt_used_when_none_passed(self):
        body = {"response": "ok"}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = OllamaVLM()
            v.describe_image(self._PNG)
            payload = mock_post.call_args.kwargs["json"]
            prompt = payload["prompt"]
            # Pinned default phrasing — "transcribe" + "Markdown"
            # discipline.
            assert "transcribe" in prompt.lower()
            assert "markdown" in prompt.lower()

    def test_caller_prompt_overrides_default(self):
        body = {"response": "ok"}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = OllamaVLM()
            v.describe_image(self._PNG, prompt="Describe this in haiku.")
            payload = mock_post.call_args.kwargs["json"]
            assert payload["prompt"] == "Describe this in haiku."

    def test_timeout_passed_to_requests(self):
        body = {"response": "ok"}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = OllamaVLM(timeout_s=33.0)
            v.describe_image(self._PNG)
            assert mock_post.call_args.kwargs.get("timeout") == 33.0


# ── extract_page() ─────────────────────────────────────────────────────


class TestExtractPage:
    _PNG = b"\x89PNG\r\n\x1a\nPAGE_IMAGE"

    def test_happy_path_returns_markdown(self):
        body = {"response": "# Page 3\n\nbody"}
        with patch("requests.post", return_value=_ok_response(body)):
            v = OllamaVLM()
            out = v.extract_page(self._PNG, page_number=3)
            assert out == "# Page 3\n\nbody"

    def test_prompt_includes_page_number(self):
        body = {"response": "ok"}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = OllamaVLM()
            v.extract_page(self._PNG, page_number=7)
            payload = mock_post.call_args.kwargs["json"]
            assert "7" in payload["prompt"]

    def test_prompt_biases_toward_faithful_markdown(self):
        body = {"response": "ok"}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = OllamaVLM()
            v.extract_page(self._PNG, page_number=1)
            payload = mock_post.call_args.kwargs["json"]
            prompt = payload["prompt"].lower()
            # Pinned phrasing markers — the prompt must instruct the
            # model to preserve structure and emit Markdown, not
            # summarise.
            assert "markdown" in prompt
            assert ("summari" not in prompt) or ("do not summari" in prompt)

    def test_request_carries_image_bytes(self):
        body = {"response": "ok"}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = OllamaVLM()
            v.extract_page(self._PNG, page_number=2)
            payload = mock_post.call_args.kwargs["json"]
            expected_b64 = base64.b64encode(self._PNG).decode("ascii")
            assert payload["images"] == [expected_b64]


# ── Exception mapping ──────────────────────────────────────────────────


class TestExceptionMapping:
    """Every ``requests`` exception type maps to a custom VLM error."""

    _PNG = b"\x89PNG"

    def test_timeout_maps_to_vlm_timeout(self):
        import requests as _requests

        with patch("requests.post", side_effect=_requests.Timeout("slow")):
            v = OllamaVLM()
            with pytest.raises(VLMTimeoutError):
                v.extract_page(self._PNG, page_number=1)

    def test_connection_error_maps_to_unavailable(self):
        import requests as _requests

        with patch("requests.post", side_effect=_requests.ConnectionError("refused")):
            v = OllamaVLM()
            with pytest.raises(VLMUnavailableError):
                v.extract_page(self._PNG, page_number=1)

    def test_http_500_maps_to_response_error(self):
        with patch("requests.post", return_value=_err_response(500, "boom")):
            v = OllamaVLM()
            with pytest.raises(VLMResponseError, match="500"):
                v.extract_page(self._PNG, page_number=1)

    def test_http_400_maps_to_response_error(self):
        with patch("requests.post", return_value=_err_response(400, "bad model")):
            v = OllamaVLM()
            with pytest.raises(VLMResponseError, match="400"):
                v.extract_page(self._PNG, page_number=1)

    def test_response_body_truncated_in_error_message(self):
        """A 200-char body must be safely truncated — the error must
        not leak megabytes of binary into the log."""
        long_body = "x" * 5000
        with patch("requests.post", return_value=_err_response(500, long_body)):
            v = OllamaVLM()
            with pytest.raises(VLMResponseError) as ei:
                v.extract_page(self._PNG, page_number=1)
            msg = str(ei.value)
            assert len(msg) < 400  # well under 5000

    def test_missing_response_key_maps_to_response_error(self):
        body = {"done": True}  # missing "response"
        with patch("requests.post", return_value=_ok_response(body)):
            v = OllamaVLM()
            with pytest.raises(VLMResponseError, match=r"(?i)response|malformed|missing"):
                v.extract_page(self._PNG, page_number=1)

    def test_malformed_json_maps_to_response_error(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.ok = True
        resp.text = "not json at all"
        resp.json.side_effect = ValueError("malformed")
        with patch("requests.post", return_value=resp):
            v = OllamaVLM()
            with pytest.raises(VLMResponseError):
                v.extract_page(self._PNG, page_number=1)

    def test_generic_request_exception_maps_to_unavailable(self):
        """Anything else under ``requests.RequestException`` (e.g.
        ``InvalidURL``) is treated as "backend not usable"."""
        import requests as _requests

        with patch("requests.post", side_effect=_requests.RequestException("weird")):
            v = OllamaVLM()
            with pytest.raises(VLMUnavailableError):
                v.extract_page(self._PNG, page_number=1)


# ── describe_image exception mapping (parity with extract_page) ────────


class TestDescribeImageExceptionMapping:
    _PNG = b"\x89PNG"

    def test_timeout(self):
        import requests as _requests

        with (
            patch("requests.post", side_effect=_requests.Timeout()),
            pytest.raises(VLMTimeoutError),
        ):
            OllamaVLM().describe_image(self._PNG)

    def test_connection_error(self):
        import requests as _requests

        with (
            patch("requests.post", side_effect=_requests.ConnectionError()),
            pytest.raises(VLMUnavailableError),
        ):
            OllamaVLM().describe_image(self._PNG)

    def test_500(self):
        with (
            patch("requests.post", return_value=_err_response(500)),
            pytest.raises(VLMResponseError),
        ):
            OllamaVLM().describe_image(self._PNG)


# ── Protocol satisfaction ──────────────────────────────────────────────


def test_ollama_vlm_satisfies_protocol():
    from corpus_forge.vlm import VLMBackend

    assert isinstance(OllamaVLM(), VLMBackend)


# ── Lazy-import discipline ─────────────────────────────────────────────


def test_module_does_not_import_requests_at_top_level():
    """``import corpus_forge.vlm.ollama`` must NOT trigger
    ``import requests`` (mirrors :mod:`corpus_forge.embedders.openai`)."""
    import sys
    from pathlib import Path

    mod = sys.modules.get("corpus_forge.vlm.ollama")
    if mod is None:
        from corpus_forge.vlm import ollama as mod
    assert mod.__file__ is not None
    source = Path(mod.__file__).read_text()
    for line in source.splitlines():
        stripped = line.strip()
        # Allow lazy imports inside function bodies (indented).
        if not line.startswith(" ") and not line.startswith("\t"):
            assert not stripped.startswith("import requests"), (
                "ollama.py imports requests at module scope"
            )
            assert not stripped.startswith("from requests"), (
                "ollama.py imports from requests at module scope"
            )
