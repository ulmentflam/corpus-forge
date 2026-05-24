"""Unit tests for `LLMClassifier`.

Phase E / Wave 3 — C-10/11.

All HTTP traffic is mocked via :func:`unittest.mock.patch` — these
tests stay fast, deterministic, and run on every machine (no daemon
required). The live-Ollama round-trip lives in
``tests/integration/test_classify_llm_e2e.py`` under the
``requires_ollama_text`` marker.

Coverage matrix (one test class per concern):

- Happy path: well-formed JSON-in-JSON response.
- Output validation: invalid ``class`` falls back to ``other`` 0.2.
- Output validation: out-of-range ``confidence`` clamped to [0, 1].
- Output validation: inner JSON unparseable → graceful fallback.
- Output validation: outer body not JSON → :class:`ClassifierResponseError`.
- Transport: timeout → :class:`ClassifierTimeoutError`.
- Transport: connection refused → :class:`ClassifierUnavailableError`.
- Transport: 4xx / 5xx → :class:`ClassifierResponseError`.
- URL composition: non-default URL is honoured.
- Prompt construction: head + tail when over budget; full text under.
- Prompt construction: format labels appear; all 9 enum values appear.
- Class metadata: ``name == "llm"``.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.classifiers.base import (
    ALLOWED_CLASS_VALUES,
    ClassifiableDocument,
    ClassifierResponseError,
    ClassifierTimeoutError,
    ClassifierUnavailableError,
    ClassLabel,
)
from corpus_forge.classifiers.llm import LLMClassifier

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _doc(text: str = "hello world", **overrides: object) -> ClassifiableDocument:
    """Build a default :class:`ClassifiableDocument` for unit tests."""
    defaults: dict[str, object] = {
        "document_id": 1,
        "source_uri": "file:///x/y.md",
        "title": "A document",
        "text": text,
        "format_labels": [("format", "markdown")],
        "metadata": {},
    }
    defaults.update(overrides)
    return ClassifiableDocument(**defaults)  # type: ignore[arg-type]


def _ok_response(body: dict[str, object]) -> MagicMock:
    """Build a 200 OK response with a parseable JSON body."""
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


def _err_response(status: int, body: str = "boom") -> MagicMock:
    """Build a non-2xx response with a plain-text body."""
    resp = MagicMock()
    resp.ok = False
    resp.status_code = status
    resp.text = body
    resp.json.side_effect = ValueError("not json")
    return resp


def _ollama_payload(*, cls: str = "book", conf: float = 0.82, rationale: str = "r") -> dict:
    """Build an Ollama-shaped response: outer JSON with a stringified inner JSON."""
    inner = json.dumps({"class": cls, "confidence": conf, "rationale": rationale})
    return {"response": inner}


# ---------------------------------------------------------------------------
# Class metadata
# ---------------------------------------------------------------------------


class TestLLMClassifierMetadata:
    def test_name_is_llm(self) -> None:
        clf = LLMClassifier()
        assert clf.name == "llm"

    def test_defaults_match_plan(self) -> None:
        clf = LLMClassifier()
        assert clf.model == "qwen2.5:7b-instruct"
        assert clf.llm_url == "http://localhost:11434"
        assert clf.timeout_s == 60.0
        assert clf.temperature == 0.0
        assert clf.excerpt_chars == 2000

    def test_trailing_slash_stripped(self) -> None:
        clf = LLMClassifier(llm_url="http://localhost:11434/")
        assert clf.llm_url == "http://localhost:11434"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestLLMClassifierHappyPath:
    def test_classify_returns_well_formed_label(self) -> None:
        clf = LLMClassifier()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response(
                _ollama_payload(cls="book", conf=0.82, rationale="long-form fiction"),
            )
            label = clf.classify(_doc(text="A long story about a knight..."))
        assert isinstance(label, ClassLabel)
        assert label.value == "book"
        assert label.confidence == pytest.approx(0.82)
        assert label.rationale == "long-form fiction"

    def test_request_payload_shape(self) -> None:
        """The POST body must carry model, prompt, stream=False, format=json."""
        clf = LLMClassifier(model="qwen2.5:7b-instruct", temperature=0.0)
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response(_ollama_payload())
            clf.classify(_doc())
        # call_args is (args, kwargs)
        kwargs = mock_post.call_args.kwargs
        payload = kwargs.get("json")
        assert payload is not None, "POST should be called with json=<dict>"
        assert payload["model"] == "qwen2.5:7b-instruct"
        assert payload["stream"] is False
        assert payload["format"] == "json"
        assert isinstance(payload["prompt"], str)
        assert payload["options"]["temperature"] == 0.0
        assert payload["options"]["num_ctx"] == 8192


# ---------------------------------------------------------------------------
# Output validation
# ---------------------------------------------------------------------------


class TestLLMClassifierOutputValidation:
    def test_invalid_class_value_falls_back_to_other(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        clf = LLMClassifier()
        with (
            patch("requests.post") as mock_post,
            caplog.at_level("WARNING", logger="corpus_forge.classifiers.llm"),
        ):
            mock_post.return_value = _ok_response(
                _ollama_payload(cls="not-a-class", conf=0.9, rationale="r"),
            )
            label = clf.classify(_doc())
        assert isinstance(label, ClassLabel)
        assert label.value == "other"
        assert label.confidence == pytest.approx(0.2)
        assert "invalid llm output" in label.rationale.lower()
        # WARNING log surfaces the bad payload for debugging.
        assert any("invalid" in rec.message.lower() for rec in caplog.records)

    def test_confidence_above_one_is_clamped(self) -> None:
        clf = LLMClassifier()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response(
                _ollama_payload(cls="article", conf=1.5, rationale="r"),
            )
            label = clf.classify(_doc())
        assert isinstance(label, ClassLabel)
        assert label.confidence == pytest.approx(1.0)

    def test_confidence_below_zero_is_clamped(self) -> None:
        clf = LLMClassifier()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response(
                _ollama_payload(cls="article", conf=-0.3, rationale="r"),
            )
            label = clf.classify(_doc())
        assert isinstance(label, ClassLabel)
        assert label.confidence == pytest.approx(0.0)

    def test_inner_json_unparseable_falls_back_to_other(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        clf = LLMClassifier()
        with (
            patch("requests.post") as mock_post,
            caplog.at_level("WARNING", logger="corpus_forge.classifiers.llm"),
        ):
            # Ollama returned the outer JSON envelope, but the inner
            # `response` field is plain prose (the model failed to
            # honour ``format=json``).
            mock_post.return_value = _ok_response({"response": "not-json oops"})
            label = clf.classify(_doc())
        assert isinstance(label, ClassLabel)
        assert label.value == "other"
        assert label.confidence == pytest.approx(0.2)
        assert "invalid llm output" in label.rationale.lower()

    def test_outer_body_not_json_raises_response_error(self) -> None:
        clf = LLMClassifier()
        with patch("requests.post") as mock_post:
            resp = MagicMock()
            resp.ok = True
            resp.status_code = 200
            resp.json.side_effect = ValueError("not json")
            resp.text = "definitely not json"
            mock_post.return_value = resp
            with pytest.raises(ClassifierResponseError):
                clf.classify(_doc())

    def test_outer_body_missing_response_key_raises(self) -> None:
        clf = LLMClassifier()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response({"something_else": "x"})
            with pytest.raises(ClassifierResponseError):
                clf.classify(_doc())

    def test_missing_class_field_falls_back_to_other(self) -> None:
        clf = LLMClassifier()
        with patch("requests.post") as mock_post:
            inner = json.dumps({"confidence": 0.5, "rationale": "no class"})
            mock_post.return_value = _ok_response({"response": inner})
            label = clf.classify(_doc())
        assert isinstance(label, ClassLabel)
        assert label.value == "other"
        assert label.confidence == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# Transport-layer failure mapping
# ---------------------------------------------------------------------------


class TestLLMClassifierTransportErrors:
    def test_timeout_maps_to_classifier_timeout(self) -> None:
        import requests

        clf = LLMClassifier(timeout_s=5.0)
        with (
            patch("requests.post", side_effect=requests.Timeout("slow")),
            pytest.raises(ClassifierTimeoutError),
        ):
            clf.classify(_doc())

    def test_connection_error_maps_to_unavailable(self) -> None:
        import requests

        clf = LLMClassifier()
        with (
            patch("requests.post", side_effect=requests.ConnectionError("nope")),
            pytest.raises(ClassifierUnavailableError),
        ):
            clf.classify(_doc())

    def test_request_exception_maps_to_unavailable(self) -> None:
        """Any other ``requests.RequestException`` is treated as unavailable."""
        import requests

        clf = LLMClassifier()
        with (
            patch("requests.post", side_effect=requests.RequestException("weird")),
            pytest.raises(ClassifierUnavailableError),
        ):
            clf.classify(_doc())

    def test_4xx_maps_to_response_error(self) -> None:
        clf = LLMClassifier()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _err_response(404, "not found")
            with pytest.raises(ClassifierResponseError) as exc_info:
                clf.classify(_doc())
        assert "404" in str(exc_info.value)

    def test_5xx_maps_to_response_error(self) -> None:
        clf = LLMClassifier()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _err_response(500, "internal error")
            with pytest.raises(ClassifierResponseError) as exc_info:
                clf.classify(_doc())
        assert "500" in str(exc_info.value)


# ---------------------------------------------------------------------------
# URL composition / non-default URL
# ---------------------------------------------------------------------------


class TestLLMClassifierUrlComposition:
    def test_non_default_url_is_honoured(self) -> None:
        clf = LLMClassifier(llm_url="https://hosted.example.com")
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response(_ollama_payload())
            clf.classify(_doc())
        # The first positional arg is the URL.
        called_url = mock_post.call_args.args[0]
        assert called_url == "https://hosted.example.com/api/generate"

    def test_default_url_targets_localhost(self) -> None:
        clf = LLMClassifier()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response(_ollama_payload())
            clf.classify(_doc())
        called_url = mock_post.call_args.args[0]
        assert called_url == "http://localhost:11434/api/generate"


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def _captured_prompt(mock_post: MagicMock) -> str:
    """Pull the prompt string out of the last POST call."""
    payload = mock_post.call_args.kwargs["json"]
    return payload["prompt"]


class TestLLMClassifierPromptConstruction:
    def test_short_text_is_included_whole(self) -> None:
        clf = LLMClassifier(excerpt_chars=2000)
        body = "tiny doc body"
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response(_ollama_payload())
            clf.classify(_doc(text=body))
        prompt = _captured_prompt(mock_post)
        assert body in prompt

    def test_long_text_keeps_head_and_tail_drops_middle(self) -> None:
        clf = LLMClassifier(excerpt_chars=200)
        # Craft a long body where a unique middle marker is far enough
        # from both ends that it must be excluded under a head+tail
        # budget of 200.
        head = "HEADMARK" + "a" * 1000
        middle = "MIDDLEMARK" + "b" * 1000
        tail = "c" * 1000 + "TAILMARK"
        body = head + middle + tail
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response(_ollama_payload())
            clf.classify(_doc(text=body))
        prompt = _captured_prompt(mock_post)
        assert "HEADMARK" in prompt
        assert "TAILMARK" in prompt
        assert "MIDDLEMARK" not in prompt

    def test_format_labels_appear_in_prompt(self) -> None:
        clf = LLMClassifier()
        labels = [("format", "pdf"), ("language", "python")]
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response(_ollama_payload())
            clf.classify(_doc(format_labels=labels))
        prompt = _captured_prompt(mock_post)
        for k, v in labels:
            assert k in prompt
            assert v in prompt

    def test_all_nine_enum_values_appear_in_prompt(self) -> None:
        clf = LLMClassifier()
        with patch("requests.post") as mock_post:
            mock_post.return_value = _ok_response(_ollama_payload())
            clf.classify(_doc())
        prompt = _captured_prompt(mock_post)
        for v in ALLOWED_CLASS_VALUES:
            assert v in prompt, f"enum value {v!r} missing from prompt"
