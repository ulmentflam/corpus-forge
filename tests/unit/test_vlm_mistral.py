"""Phase D / Wave 4 (E-03) — :class:`MistralOCR` HTTP behaviour.

Mirrors :mod:`tests.unit.test_vlm_ollama` — the public surface is the
same Protocol, the failure modes map to the same custom exceptions, the
HTTP layer is fully mocked. No live network, no live API key.

Mistral OCR is the remote fallback for batch / accuracy-critical jobs
and for environments where the local Ollama daemon can't be reached.
The unit tests pin the request shape (single-image data-URL,
``Authorization: Bearer`` header), the response parse path
(``pages[*].markdown`` concatenated with ``\\n\\n``), and the
exception-mapping table.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.vlm import (
    VLMResponseError,
    VLMTimeoutError,
    VLMUnavailableError,
)
from corpus_forge.vlm.mistral import MistralOCR

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
    def test_name_is_mistral(self):
        assert MistralOCR(api_key="sk-test").name == "mistral"

    def test_default_model(self):
        v = MistralOCR(api_key="sk-test")
        assert v.model == "mistral-ocr-2503"

    def test_default_base_url(self):
        v = MistralOCR(api_key="sk-test")
        assert v.base_url == "https://api.mistral.ai/v1"

    def test_default_timeout(self):
        v = MistralOCR(api_key="sk-test")
        assert v.timeout_s == 120.0

    def test_missing_api_key_raises_unavailable(self):
        """Empty / None api_key must raise at construction time — the
        backend cannot do anything without it."""
        with pytest.raises(VLMUnavailableError, match=r"(?i)api key"):
            MistralOCR(api_key="")

    def test_override_all_args(self):
        v = MistralOCR(
            api_key="sk-X",
            model="mistral-ocr-experimental",
            base_url="https://eu.mistral.ai/v1",
            timeout_s=60.0,
        )
        assert v.api_key == "sk-X"
        assert v.model == "mistral-ocr-experimental"
        assert v.base_url == "https://eu.mistral.ai/v1"
        assert v.timeout_s == 60.0

    def test_construction_does_not_call_requests(self):
        """``__init__`` must NOT make network calls — the docstring is
        explicit that warmup is a no-op so no money is spent."""
        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            MistralOCR(api_key="sk-test")
            assert mock_post.call_count == 0
            assert mock_get.call_count == 0


# ── warmup() — no-op ───────────────────────────────────────────────────


class TestWarmup:
    def test_warmup_makes_no_network_call(self):
        """Mistral OCR has no cheap health-check endpoint; warmup must
        not contact the network (it would cost money)."""
        with patch("requests.post") as mock_post, patch("requests.get") as mock_get:
            v = MistralOCR(api_key="sk-test")
            v.warmup()
            assert mock_post.call_count == 0
            assert mock_get.call_count == 0

    def test_warmup_returns_none(self):
        v = MistralOCR(api_key="sk-test")
        assert v.warmup() is None


# ── describe_image() ──────────────────────────────────────────────────


class TestDescribeImage:
    _PNG = b"\x89PNG\r\n\x1a\nFAKE"

    def test_happy_path_returns_concatenated_markdown(self):
        body = {"pages": [{"markdown": "# Heading\n\nbody"}]}
        with patch("requests.post", return_value=_ok_response(body)):
            v = MistralOCR(api_key="sk-test")
            out = v.describe_image(self._PNG)
            assert out == "# Heading\n\nbody"

    def test_multi_page_response_joined_with_blank_line(self):
        """Mistral OCR returns one ``pages[]`` entry per page; when more
        than one is present we concatenate with a blank line so the
        Markdown chunker can see page boundaries."""
        body = {"pages": [{"markdown": "page 1"}, {"markdown": "page 2"}]}
        with patch("requests.post", return_value=_ok_response(body)):
            v = MistralOCR(api_key="sk-test")
            out = v.describe_image(self._PNG)
            assert out == "page 1\n\npage 2"

    def test_request_targets_ocr_endpoint(self):
        body = {"pages": [{"markdown": "ok"}]}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = MistralOCR(api_key="sk-test", base_url="https://api.mistral.ai/v1")
            v.describe_image(self._PNG)
            url = mock_post.call_args[0][0]
            assert url == "https://api.mistral.ai/v1/ocr"

    def test_base_url_trailing_slash_tolerated(self):
        body = {"pages": [{"markdown": "ok"}]}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = MistralOCR(api_key="sk-test", base_url="https://api.mistral.ai/v1/")
            v.describe_image(self._PNG)
            url = mock_post.call_args[0][0]
            assert url.count("/ocr") == 1
            assert "//ocr" not in url

    def test_authorization_header_present(self):
        body = {"pages": [{"markdown": "ok"}]}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = MistralOCR(api_key="sk-secret-XYZ")
            v.describe_image(self._PNG)
            headers = mock_post.call_args.kwargs.get("headers") or {}
            assert headers.get("Authorization") == "Bearer sk-secret-XYZ"

    def test_request_body_carries_data_url_image(self):
        body = {"pages": [{"markdown": "ok"}]}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = MistralOCR(api_key="sk-test")
            v.describe_image(self._PNG)
            payload = mock_post.call_args.kwargs.get("json")
            assert payload is not None
            doc = payload.get("document", {})
            assert doc.get("type") == "image_url"
            url = doc.get("image_url", "")
            # Accept both raw-string and dict-shaped image_url; the
            # documented shape is a raw string per the wave plan.
            if isinstance(url, dict):
                url = url.get("url", "")
            assert url.startswith("data:image/png;base64,")

    def test_request_body_carries_model(self):
        body = {"pages": [{"markdown": "ok"}]}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = MistralOCR(api_key="sk-test", model="mistral-ocr-2503")
            v.describe_image(self._PNG)
            payload = mock_post.call_args.kwargs["json"]
            assert payload["model"] == "mistral-ocr-2503"

    def test_timeout_passed_to_requests(self):
        body = {"pages": [{"markdown": "ok"}]}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = MistralOCR(api_key="sk-test", timeout_s=45.0)
            v.describe_image(self._PNG)
            assert mock_post.call_args.kwargs.get("timeout") == 45.0

    def test_caller_prompt_is_accepted_and_ignored(self):
        """Mistral OCR doesn't take a user prompt today; the override
        is logged at DEBUG and otherwise ignored. The request body
        must NOT contain the prompt text."""
        body = {"pages": [{"markdown": "ok"}]}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = MistralOCR(api_key="sk-test")
            out = v.describe_image(self._PNG, prompt="Describe this in haiku.")
            assert out == "ok"
            payload = mock_post.call_args.kwargs["json"]
            assert "haiku" not in str(payload)


# ── extract_page() ─────────────────────────────────────────────────────


class TestExtractPage:
    _PNG = b"\x89PNG"

    def test_happy_path(self):
        body = {"pages": [{"markdown": "page-3-content"}]}
        with patch("requests.post", return_value=_ok_response(body)):
            v = MistralOCR(api_key="sk-test")
            out = v.extract_page(self._PNG, page_number=3)
            assert out == "page-3-content"

    def test_image_carried_as_data_url(self):
        body = {"pages": [{"markdown": "ok"}]}
        with patch("requests.post", return_value=_ok_response(body)) as mock_post:
            v = MistralOCR(api_key="sk-test")
            v.extract_page(self._PNG, page_number=1)
            payload = mock_post.call_args.kwargs["json"]
            doc = payload["document"]
            url = doc.get("image_url")
            if isinstance(url, dict):
                url = url.get("url", "")
            assert "base64," in url


# ── Exception mapping ──────────────────────────────────────────────────


class TestExceptionMapping:
    _PNG = b"\x89PNG"

    def test_timeout_maps_to_vlm_timeout(self):
        import requests as _requests

        with patch("requests.post", side_effect=_requests.Timeout("slow")):
            v = MistralOCR(api_key="sk-test")
            with pytest.raises(VLMTimeoutError):
                v.extract_page(self._PNG, page_number=1)

    def test_connection_error_maps_to_unavailable(self):
        import requests as _requests

        with patch("requests.post", side_effect=_requests.ConnectionError("refused")):
            v = MistralOCR(api_key="sk-test")
            with pytest.raises(VLMUnavailableError):
                v.extract_page(self._PNG, page_number=1)

    def test_401_maps_to_unavailable_api_key_rejected(self):
        with patch("requests.post", return_value=_err_response(401, "unauthorized")):
            v = MistralOCR(api_key="sk-test")
            with pytest.raises(VLMUnavailableError, match=r"(?i)api key|rejected|unauth"):
                v.extract_page(self._PNG, page_number=1)

    def test_403_maps_to_unavailable_api_key_rejected(self):
        with patch("requests.post", return_value=_err_response(403, "forbidden")):
            v = MistralOCR(api_key="sk-test")
            with pytest.raises(VLMUnavailableError, match=r"(?i)api key|forbidden|rejected"):
                v.extract_page(self._PNG, page_number=1)

    def test_429_maps_to_response_error(self):
        """Rate-limit is a transient response failure (not unavailable
        and not a timeout). Callers can implement retry/backoff at a
        higher layer."""
        with patch("requests.post", return_value=_err_response(429, "too many")):
            v = MistralOCR(api_key="sk-test")
            with pytest.raises(VLMResponseError, match="429"):
                v.extract_page(self._PNG, page_number=1)

    def test_500_maps_to_response_error(self):
        with patch("requests.post", return_value=_err_response(500, "boom")):
            v = MistralOCR(api_key="sk-test")
            with pytest.raises(VLMResponseError, match="500"):
                v.extract_page(self._PNG, page_number=1)

    def test_malformed_response_maps_to_response_error(self):
        """Missing ``pages`` key in the body → :class:`VLMResponseError`."""
        body = {"unexpected": "shape"}
        with patch("requests.post", return_value=_ok_response(body)):
            v = MistralOCR(api_key="sk-test")
            with pytest.raises(VLMResponseError):
                v.extract_page(self._PNG, page_number=1)

    def test_pages_empty_list_maps_to_response_error(self):
        body = {"pages": []}
        with patch("requests.post", return_value=_ok_response(body)):
            v = MistralOCR(api_key="sk-test")
            with pytest.raises(VLMResponseError):
                v.extract_page(self._PNG, page_number=1)

    def test_pages_missing_markdown_maps_to_response_error(self):
        body = {"pages": [{"unexpected": True}]}
        with patch("requests.post", return_value=_ok_response(body)):
            v = MistralOCR(api_key="sk-test")
            with pytest.raises(VLMResponseError):
                v.extract_page(self._PNG, page_number=1)

    def test_invalid_json_response_maps_to_response_error(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.ok = True
        resp.text = "<html>503</html>"
        resp.json.side_effect = ValueError("not JSON")
        with patch("requests.post", return_value=resp):
            v = MistralOCR(api_key="sk-test")
            with pytest.raises(VLMResponseError):
                v.extract_page(self._PNG, page_number=1)

    def test_generic_request_exception_maps_to_unavailable(self):
        import requests as _requests

        with patch("requests.post", side_effect=_requests.RequestException("weird")):
            v = MistralOCR(api_key="sk-test")
            with pytest.raises(VLMUnavailableError):
                v.extract_page(self._PNG, page_number=1)

    def test_describe_image_timeout_maps(self):
        import requests as _requests

        with (
            patch("requests.post", side_effect=_requests.Timeout()),
            pytest.raises(VLMTimeoutError),
        ):
            MistralOCR(api_key="sk-test").describe_image(self._PNG)

    def test_describe_image_connection_error_maps(self):
        import requests as _requests

        with (
            patch("requests.post", side_effect=_requests.ConnectionError()),
            pytest.raises(VLMUnavailableError),
        ):
            MistralOCR(api_key="sk-test").describe_image(self._PNG)


# ── Protocol satisfaction ──────────────────────────────────────────────


def test_mistral_satisfies_protocol():
    from corpus_forge.vlm import VLMBackend

    assert isinstance(MistralOCR(api_key="sk-test"), VLMBackend)


# ── Lazy-import discipline ─────────────────────────────────────────────


def test_module_does_not_import_requests_at_top_level():
    import sys
    from pathlib import Path

    mod = sys.modules.get("corpus_forge.vlm.mistral")
    if mod is None:
        from corpus_forge.vlm import mistral as mod
    assert mod.__file__ is not None
    source = Path(mod.__file__).read_text()
    for line in source.splitlines():
        stripped = line.strip()
        if not line.startswith(" ") and not line.startswith("\t"):
            assert not stripped.startswith("import requests"), (
                "mistral.py imports requests at module scope"
            )
            assert not stripped.startswith("from requests"), (
                "mistral.py imports from requests at module scope"
            )
