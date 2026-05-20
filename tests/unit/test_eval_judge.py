"""Unit tests for ``corpus_forge.eval.judge`` (JudgeClient + score_prompt).

Covers the mock pass-through, the env-var fallback, the response parser, and
the failure paths that raise ``JudgeUnavailable`` without hitting the network.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from corpus_forge.eval.judge import (
    JudgeClient,
    JudgeUnavailable,
    _parse_judge_response,
    score_prompt,
)

_KEYS = {"faithfulness", "answer_relevance", "context_precision", "context_recall"}


# ─────────────────────────────────────────────────────────────────────────────
# score_prompt — mock path
# ─────────────────────────────────────────────────────────────────────────────


def test_score_prompt_mock_returns_four_keys() -> None:
    out = score_prompt("mock", "ignored-model", 1.0, "hello")
    assert set(out) == _KEYS
    for v in out.values():
        assert 0.0 <= v <= 1.0


def test_score_prompt_mock_is_deterministic() -> None:
    a = score_prompt("mock", "m", 1.0, "stable")
    b = score_prompt("mock", "m", 1.0, "stable")
    assert a == b


# ─────────────────────────────────────────────────────────────────────────────
# score_prompt — real endpoint, mocked transport
# ─────────────────────────────────────────────────────────────────────────────


class _FakeResponse:
    """Context-manager fake for urllib.request.urlopen."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None


def test_score_prompt_real_endpoint_parses_response() -> None:
    payload = json.dumps(
        {
            "response": json.dumps(
                {
                    "faithfulness": 0.81,
                    "answer_relevance": 0.7,
                    "context_precision": 0.65,
                    "context_recall": 0.9,
                }
            )
        }
    ).encode()

    with patch(
        "urllib.request.urlopen",
        return_value=_FakeResponse(payload),
    ):
        out = score_prompt("http://localhost:11434", "qwen2.5:7b-instruct", 5.0, "q")

    assert set(out) == _KEYS
    assert out["faithfulness"] == 0.81
    assert out["answer_relevance"] == 0.7
    assert out["context_precision"] == 0.65
    assert out["context_recall"] == 0.9


def test_score_prompt_real_endpoint_unreachable_raises_unavailable() -> None:
    with (
        patch("urllib.request.urlopen", side_effect=OSError("connection refused")),
        pytest.raises(JudgeUnavailable, match="unreachable"),
    ):
        score_prompt("http://nope.invalid", "qwen", 0.5, "x")


def test_score_prompt_real_endpoint_other_error_raises_unavailable() -> None:
    """Non-OSError exceptions are also wrapped (e.g. transport-layer 500s)."""
    with (
        patch("urllib.request.urlopen", side_effect=RuntimeError("HTTP 500")),
        pytest.raises(JudgeUnavailable, match="Judge error"),
    ):
        score_prompt("http://nope.invalid", "qwen", 0.5, "x")


# ─────────────────────────────────────────────────────────────────────────────
# _parse_judge_response
# ─────────────────────────────────────────────────────────────────────────────


def test_parse_judge_response_extracts_clean_json() -> None:
    text = (
        'Here are the scores: {"faithfulness": 0.9, "answer_relevance": 0.8, '
        '"context_precision": 0.7, "context_recall": 0.6}'
    )
    out = _parse_judge_response(text)
    assert set(out) == _KEYS
    assert out["faithfulness"] == 0.9


def test_parse_judge_response_clamps_to_unit_interval() -> None:
    text = (
        '{"faithfulness": 1.5, "answer_relevance": -0.2, '
        '"context_precision": 0.5, "context_recall": 0.5}'
    )
    out = _parse_judge_response(text)
    assert out["faithfulness"] == 1.0  # clamped down
    assert out["answer_relevance"] == 0.0  # clamped up


def test_parse_judge_response_fills_missing_keys_with_neutral() -> None:
    out = _parse_judge_response('{"faithfulness": 0.6}')
    assert out["faithfulness"] == 0.6
    assert out["answer_relevance"] == 0.5
    assert out["context_precision"] == 0.5
    assert out["context_recall"] == 0.5


def test_parse_judge_response_no_json_returns_neutral() -> None:
    out = _parse_judge_response("plain text without any JSON")
    assert set(out) == _KEYS
    for value in out.values():
        assert value == 0.5


def test_parse_judge_response_invalid_json_returns_neutral() -> None:
    out = _parse_judge_response('{"faithfulness": not-a-number}')
    assert set(out) == _KEYS
    for value in out.values():
        assert value == 0.5


# ─────────────────────────────────────────────────────────────────────────────
# JudgeClient
# ─────────────────────────────────────────────────────────────────────────────


def test_judge_client_default_endpoint_is_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CF_JUDGE_ENDPOINT", raising=False)
    client = JudgeClient()
    assert client.endpoint == "mock"


def test_judge_client_reads_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CF_JUDGE_ENDPOINT", "http://example.invalid:1234")
    client = JudgeClient()
    assert client.endpoint == "http://example.invalid:1234"


def test_judge_client_explicit_endpoint_wins_over_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CF_JUDGE_ENDPOINT", "http://env.invalid")
    client = JudgeClient(endpoint="mock")
    assert client.endpoint == "mock"


def test_judge_client_score_returns_four_keys() -> None:
    client = JudgeClient(endpoint="mock", model="any", timeout=0.1)
    out = client.score("hello world")
    assert set(out) == _KEYS
    for value in out.values():
        assert 0.0 <= value <= 1.0


def test_judge_client_model_and_timeout_preserved() -> None:
    client = JudgeClient(endpoint="mock", model="my-model", timeout=12.5)
    assert client.model == "my-model"
    assert client.timeout == 12.5
