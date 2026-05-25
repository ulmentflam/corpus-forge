"""Unit tests for corpus_forge.curation.selector.score_for_pruning."""

from __future__ import annotations

from typing import Any

import pytest

from corpus_forge.curation.selector import (
    PRUNE_WEIGHTS,
    _Candidate,
    score_for_pruning,
)


def _candidate(**overrides: Any) -> _Candidate:
    base: dict[str, Any] = {
        "chunk_id": 1,
        "document_id": None,
        "text": "",
        "heading": None,
        "description": None,
        "metadata": {},
        "document_title": None,
        "source_uri": None,
        "modified_at": None,
        "classifier_label": None,
        "classifier_confidence": None,
    }
    base.update(overrides)
    return _Candidate(**base)


def _full_subs(**overrides: float) -> dict[str, float]:
    base: dict[str, float] = dict.fromkeys(PRUNE_WEIGHTS, 0.5)
    base.update(overrides)
    return base


def test_default_weights_sum_to_one() -> None:
    assert abs(sum(PRUNE_WEIGHTS.values()) - 1.0) < 1e-9


def test_final_score_in_unit_interval() -> None:
    score, _ = score_for_pruning(_candidate(), sub_scores=_full_subs())
    assert 0.0 <= score <= 1.0


def test_all_zero_subs_yields_zero() -> None:
    subs = dict.fromkeys(PRUNE_WEIGHTS, 0.0)
    score, _ = score_for_pruning(_candidate(), sub_scores=subs)
    assert score == 0.0


def test_all_one_subs_yields_one() -> None:
    subs = dict.fromkeys(PRUNE_WEIGHTS, 1.0)
    score, _ = score_for_pruning(_candidate(), sub_scores=subs)
    assert score == pytest.approx(1.0)


def test_returns_sub_scores_copy() -> None:
    subs = _full_subs()
    _, returned = score_for_pruning(_candidate(), sub_scores=subs)
    assert returned == subs
    assert returned is not subs  # defensive copy


def test_function_is_pure_independent_of_candidate_shape() -> None:
    """Identical sub_scores → identical final regardless of candidate.text etc."""
    subs = _full_subs(missing_metadata=0.9)
    s1, _ = score_for_pruning(_candidate(text=""), sub_scores=subs)
    s2, _ = score_for_pruning(_candidate(text="hello", chunk_id=99), sub_scores=subs)
    assert s1 == s2


def test_custom_weights_honoured() -> None:
    custom = {
        "confidence_deficit": 0.0,
        "missing_metadata": 1.0,
        "freshness_inverted": 0.0,
        "duplicate_density": 0.0,
        "feedback_drag": 0.0,
    }
    subs = _full_subs(missing_metadata=0.8, confidence_deficit=0.1)
    score, _ = score_for_pruning(_candidate(), sub_scores=subs, weights=custom)
    assert score == pytest.approx(0.8)


def test_weights_not_summing_to_one_raise() -> None:
    bad = dict(PRUNE_WEIGHTS)
    bad["confidence_deficit"] = bad["confidence_deficit"] + 0.5
    with pytest.raises(ValueError, match=r"sum to 1\.0"):
        score_for_pruning(_candidate(), sub_scores=_full_subs(), weights=bad)


def test_unknown_weight_key_raises() -> None:
    bad = dict(PRUNE_WEIGHTS)
    del bad["feedback_drag"]
    bad["future_signal"] = 0.20
    with pytest.raises(ValueError, match="unknown keys"):
        score_for_pruning(_candidate(), sub_scores=_full_subs(), weights=bad)


def test_missing_weight_key_raises() -> None:
    partial = dict(PRUNE_WEIGHTS)
    del partial["feedback_drag"]
    with pytest.raises(ValueError, match="missing keys"):
        score_for_pruning(_candidate(), sub_scores=_full_subs(), weights=partial)


def test_missing_sub_score_key_raises() -> None:
    subs = _full_subs()
    del subs["duplicate_density"]
    with pytest.raises(ValueError, match="sub_scores missing required keys"):
        score_for_pruning(_candidate(), sub_scores=subs)


def test_final_score_clamped_to_unit() -> None:
    """Sub-scores above 1.0 should clamp the final to 1.0 (defensive)."""
    subs = dict.fromkeys(PRUNE_WEIGHTS, 2.0)
    score, _ = score_for_pruning(_candidate(), sub_scores=subs)
    assert score == 1.0
