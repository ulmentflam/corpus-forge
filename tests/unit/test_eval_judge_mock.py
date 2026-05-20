"""Unit tests for ``corpus_forge.eval.judge_mock``.

The mock judge is the CI-determinism backbone for ``eval rag`` / ``eval cag``
/ ``eval distill``; if its hash-based output stops being deterministic the
integration test suite goes flaky. Pin the contract here directly.
"""

from __future__ import annotations

import pytest

from corpus_forge.eval.judge_mock import score

_KEYS = {"faithfulness", "answer_relevance", "context_precision", "context_recall"}


def test_score_returns_all_four_keys() -> None:
    out = score("a quick prompt")
    assert set(out) == _KEYS


def test_score_values_in_unit_interval() -> None:
    out = score("the quick brown fox jumps over the lazy dog")
    for key, value in out.items():
        assert isinstance(value, float), f"{key} is not a float"
        assert 0.0 <= value <= 1.0, f"{key}={value} out of [0,1]"


def test_score_is_deterministic_across_calls() -> None:
    prompt = "stable input for determinism check"
    first = score(prompt)
    second = score(prompt)
    assert first == second


def test_score_distinct_for_different_prompts() -> None:
    a = score("alpha")
    b = score("beta")
    # Both 4-key dicts; at least one dimension must differ on different inputs.
    assert a != b


def test_score_empty_prompt_still_returns_dict() -> None:
    out = score("")
    assert set(out) == _KEYS
    for value in out.values():
        assert 0.0 <= value <= 1.0


@pytest.mark.parametrize(
    "prompt",
    ["short", "a" * 1024, "with\nnewlines", "with\ttabs", "unicode 漢字 emoji ✨"],
)
def test_score_handles_varied_inputs(prompt: str) -> None:
    out = score(prompt)
    assert set(out) == _KEYS
    for value in out.values():
        assert 0.0 <= value <= 1.0
