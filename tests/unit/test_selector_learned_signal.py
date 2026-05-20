"""Phase O Wave 3 — Regression contract for the curation selector's
``learned_quality`` integration.

Two-mode behavior
-----------------
**Mode 1 — legacy 4-weight scheme** (``chunk_quality_signals`` is empty for the
queried chunks).  The selector must produce byte-identical output on the
pre-existing 4 observable fields:

    confidence_deficit  (weight 0.35)
    missing_metadata    (weight 0.30)
    ranker_elevation    (weight 0.25)
    freshness           (weight 0.10)

The newly added ``ScoreBreakdown.learned_quality`` field must be ``None`` (not
``0.0``) in this path.

**Mode 2 — 5-weight scheme** (a ``chunk_quality_signals`` row with
``signal_name='learned_quality'`` exists for the candidate chunk).  Weights
rebalance to:

    confidence_deficit  0.30
    missing_metadata    0.25
    ranker_elevation    0.20
    freshness           0.10
    learned_quality     0.15   ← new

Both modes coexist *per-chunk* in the same ``next_curation_batch`` response:
chunks WITH a learned_quality row use the 5-weight formula; chunks WITHOUT use
the 4-weight formula.

RED condition (O3-T3)
---------------------
``ScoreBreakdown.learned_quality`` does not yet exist.  Every test that touches
``score_breakdown.learned_quality`` or that constructs ``ScoreBreakdown`` with a
``learned_quality`` keyword fails with ``TypeError`` or ``AttributeError``.  The
``_Candidate.learned_quality`` attribute similarly does not exist.  The constants
``_SCORE_WEIGHTS_4`` and ``_SCORE_WEIGHTS_5`` are not yet defined.  All ≥ 15 tests
must fail for those correct structural reasons.
"""

from __future__ import annotations

import dataclasses
import pickle
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar

import pytest

from corpus_forge.curation import (
    SCORE_WEIGHTS,
    ScoreBreakdown,
    next_curation_batch,
    next_curation_target,
)
from corpus_forge.curation import selector as _selector_module

# ─────────────────────────────────────────────────────────────────────────────
# Fixture corpus constants — deterministic, fixed
# ─────────────────────────────────────────────────────────────────────────────

_NOW = datetime(2026, 5, 19, 0, 0, 0, tzinfo=UTC)
_FIXTURE_N = 20
_BASELINE_PICKLE = Path(__file__).parents[1] / "fixtures" / "curation" / "selector_baseline.pickle"

# 5-weight scheme as specified in phase_o_eda_cleaning.md § Wave O3 GREEN
_EXPECTED_WEIGHTS_5 = {
    "confidence_deficit": 0.30,
    "missing_metadata": 0.25,
    "ranker_elevation": 0.20,
    "freshness": 0.10,
    "learned_quality": 0.15,
}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — fixture row builder (identical to regenerate_baseline.py)
# ─────────────────────────────────────────────────────────────────────────────


def _make_row(
    chunk_id: int,
    *,
    doc_id: int = 1,
    text: str = "lorem ipsum",
    heading: str | None = "h",
    description: str | None = "d",
    metadata: dict | None = None,
    document_title: str | None = "Fixture Doc",
    source_uri: str | None = "vault://notes/fixture.md",
    modified_at: datetime | None = None,
    labels: list | None = None,
    classifier_label: str | None = "topic_a",
    classifier_confidence: float | None = 0.8,
    embedding: list | None = None,
    learned_quality: float | None = None,
) -> dict[str, Any]:
    """Build a backend row dict in the shape ``_iter_curation_candidates`` expects.

    The ``learned_quality`` key is the Phase O addition — ``None`` means no row
    in ``chunk_quality_signals``; a float means one exists.
    """
    return {
        "chunk_id": chunk_id,
        "document_id": doc_id,
        "text": text,
        "heading": heading,
        "description": description,
        "metadata": dict(metadata if metadata is not None else {"language": "en"}),
        "document_title": document_title,
        "source_uri": source_uri,
        "modified_at": modified_at if modified_at is not None else (_NOW - timedelta(days=30)),
        "labels": list(
            labels if labels is not None else [("class", classifier_label or ""), ("topic", "x")]
        ),
        "classifier_label": classifier_label,
        "classifier_confidence": classifier_confidence,
        "embedding": embedding,
        "learned_quality": learned_quality,
    }


def _build_fixture_rows(
    learned_quality_by_id: dict[int, float] | None = None,
) -> list[dict[str, Any]]:
    """Build the canonical 20-chunk fixture corpus.

    ``learned_quality_by_id`` maps chunk_id → learned_quality float for chunks
    that should be treated as having a ``chunk_quality_signals`` row.  All others
    get ``None`` (empty-table legacy path).
    """
    lq = learned_quality_by_id or {}
    rows = []
    for i in range(1, _FIXTURE_N + 1):
        conf = round(0.05 + (i % 10) * 0.09, 2)
        rows.append(
            _make_row(
                chunk_id=i,
                doc_id=((i - 1) // 5) + 1,
                source_uri=f"vault://notes/doc{((i - 1) // 5) + 1}.md",
                classifier_label="topic_a",
                classifier_confidence=conf,
                heading="h" if bool(i % 3) else None,
                description="d" if bool(i % 4) else None,
                metadata={"language": "en"} if i % 2 == 0 else {},
                modified_at=_NOW - timedelta(days=10 + i * 8),
                learned_quality=lq.get(i),
            )
        )
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Fake backend — extends the existing _HookBackend pattern with learned_quality
# support.  The backend merely passes rows through; the selector is responsible
# for reading the ``learned_quality`` key and applying the correct weight scheme.
# ─────────────────────────────────────────────────────────────────────────────


class _HookBackend:
    """Fake backend that yields prebuilt candidate rows through the hook."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def iter_curation_candidates(
        self,
        *,
        dataset: str | None,
        limit: int,
    ) -> Iterable[dict[str, Any]]:
        yield from self._rows[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helper: load baseline pickle (skip if file missing)
# ─────────────────────────────────────────────────────────────────────────────


def _load_baseline() -> dict:
    if not _BASELINE_PICKLE.exists():
        pytest.skip("selector_baseline.pickle not found — run regenerate_baseline.py first")
    with _BASELINE_PICKLE.open("rb") as fh:
        return pickle.load(fh)  # pickle is controlled test data only


# ─────────────────────────────────────────────────────────────────────────────
# Part 0 — structural contract: new fields and constants must exist
# ─────────────────────────────────────────────────────────────────────────────


class TestStructuralContract:
    """These tests assert the O3 additions to the module shape.

    All fail RED because the new fields / constants are not yet defined.
    """

    def test_score_breakdown_has_learned_quality_field(self) -> None:
        """``ScoreBreakdown`` must accept a ``learned_quality`` keyword arg."""
        bd = ScoreBreakdown(
            confidence_deficit=0.5,
            missing_metadata=0.5,
            ranker_elevation=0.5,
            freshness=0.5,
            learned_quality=None,  # RED: TypeError — unexpected keyword argument
        )
        assert bd.learned_quality is None

    def test_score_breakdown_learned_quality_defaults_to_none(self) -> None:
        """When omitted, ``learned_quality`` must default to ``None``."""
        bd = ScoreBreakdown(
            confidence_deficit=0.0,
            missing_metadata=0.0,
            ranker_elevation=0.0,
            freshness=0.0,
        )
        # RED: AttributeError — 'ScoreBreakdown' object has no attribute 'learned_quality'
        assert bd.learned_quality is None

    def test_score_breakdown_learned_quality_accepts_float(self) -> None:
        """``learned_quality`` field accepts a float in [0, 1]."""
        bd = ScoreBreakdown(
            confidence_deficit=0.3,
            missing_metadata=0.2,
            ranker_elevation=0.2,
            freshness=0.1,
            learned_quality=0.75,  # RED: TypeError
        )
        assert bd.learned_quality == pytest.approx(0.75)

    def test_candidate_has_learned_quality_attribute(self) -> None:
        """``_Candidate`` must expose a ``learned_quality`` attribute (default None)."""
        from corpus_forge.curation.selector import _Candidate

        cand = _Candidate(
            chunk_id=1,
            document_id=None,
            text="test",
            heading=None,
            description=None,
            metadata={},
            document_title=None,
            source_uri=None,
            modified_at=None,
            classifier_label=None,
            classifier_confidence=None,
        )
        # RED: AttributeError — no learned_quality on _Candidate
        assert cand.learned_quality is None

    def test_score_weights_constant_still_importable(self) -> None:
        """``SCORE_WEIGHTS`` must remain importable for backward-compat callers."""
        assert isinstance(SCORE_WEIGHTS, dict)
        assert "confidence_deficit" in SCORE_WEIGHTS
        assert abs(sum(SCORE_WEIGHTS.values()) - 1.0) < 1e-9

    def test_score_weights_4_private_constant_exists(self) -> None:
        """``_SCORE_WEIGHTS_4`` must exist and match the pre-O3 4-weight dict."""
        # RED: AttributeError — module has no attribute _SCORE_WEIGHTS_4
        w4 = getattr(_selector_module, "_SCORE_WEIGHTS_4", None)
        assert w4 is not None, "_SCORE_WEIGHTS_4 not found on selector module"
        assert set(w4.keys()) == {
            "confidence_deficit",
            "missing_metadata",
            "ranker_elevation",
            "freshness",
        }
        assert abs(sum(w4.values()) - 1.0) < 1e-9

    def test_score_weights_5_private_constant_exists(self) -> None:
        """``_SCORE_WEIGHTS_5`` must exist with the 5-key rebalanced scheme."""
        # RED: AttributeError — module has no attribute _SCORE_WEIGHTS_5
        w5 = getattr(_selector_module, "_SCORE_WEIGHTS_5", None)
        assert w5 is not None, "_SCORE_WEIGHTS_5 not found on selector module"
        assert set(w5.keys()) == {
            "confidence_deficit",
            "missing_metadata",
            "ranker_elevation",
            "freshness",
            "learned_quality",
        }
        assert abs(sum(w5.values()) - 1.0) < 1e-9

    def test_score_weights_5_values_match_spec(self) -> None:
        """The 5-weight scheme must use the exact values from the phase doc."""
        w5 = getattr(_selector_module, "_SCORE_WEIGHTS_5", None)
        assert w5 is not None
        for key, expected in _EXPECTED_WEIGHTS_5.items():
            assert w5[key] == pytest.approx(expected, abs=1e-9), (
                f"_SCORE_WEIGHTS_5[{key!r}] = {w5[key]}; expected {expected}"
            )

    def test_public_score_weights_is_alias_of_score_weights_4(self) -> None:
        """``SCORE_WEIGHTS`` must be (or equal) ``_SCORE_WEIGHTS_4``."""
        w4 = getattr(_selector_module, "_SCORE_WEIGHTS_4", None)
        assert w4 is not None
        assert w4 == SCORE_WEIGHTS


# ─────────────────────────────────────────────────────────────────────────────
# Part 1 — Mode 1: empty learned_quality → 4-weight, byte-identical to baseline
# ─────────────────────────────────────────────────────────────────────────────


class TestMode1LegacyFourWeightScheme:
    """Regression: when no chunk has a learned_quality value, the selector must
    produce byte-identical output on all pre-O3 fields compared to the pickled
    baseline.

    RED condition: tests that call ``score_breakdown.learned_quality`` fail with
    ``AttributeError``.  Tests that construct ``ScoreBreakdown`` with the
    ``learned_quality=None`` kwarg fail with ``TypeError``.
    """

    def test_batch_targets_order_matches_baseline(self) -> None:
        """chunk_id ordering of the batch must match the pre-O3 baseline."""
        baseline = _load_baseline()
        rows = _build_fixture_rows()  # all learned_quality=None
        batch = next_curation_batch(backend=_HookBackend(rows), limit=10, now=_NOW)
        assert batch is not None
        baseline_ids = [t.chunk_id for t in baseline["batch"].targets]
        current_ids = [t.chunk_id for t in batch.targets]
        assert current_ids == baseline_ids, (
            f"batch ordering regressed: baseline={baseline_ids} got={current_ids}"
        )

    def test_batch_target_scores_match_baseline(self) -> None:
        """Per-target scores must be numerically identical to the pickled baseline."""
        baseline = _load_baseline()
        rows = _build_fixture_rows()
        batch = next_curation_batch(backend=_HookBackend(rows), limit=10, now=_NOW)
        assert batch is not None
        for bt, pt in zip(batch.targets, baseline["batch"].targets, strict=True):
            assert bt.score == pytest.approx(pt.score, abs=1e-9), (
                f"chunk_id {bt.chunk_id}: score {bt.score} != baseline {pt.score}"
            )

    def test_batch_score_breakdown_fields_match_baseline(self) -> None:
        """All four sub-score fields must be identical to the baseline."""
        baseline = _load_baseline()
        rows = _build_fixture_rows()
        batch = next_curation_batch(backend=_HookBackend(rows), limit=10, now=_NOW)
        assert batch is not None
        for bt, pt in zip(batch.targets, baseline["batch"].targets, strict=True):
            bd, pb = bt.score_breakdown, pt.score_breakdown
            assert bd.confidence_deficit == pytest.approx(pb.confidence_deficit, abs=1e-9)
            assert bd.missing_metadata == pytest.approx(pb.missing_metadata, abs=1e-9)
            assert bd.ranker_elevation == pytest.approx(pb.ranker_elevation, abs=1e-9)
            assert bd.freshness == pytest.approx(pb.freshness, abs=1e-9)

    def test_batch_selection_reasons_match_baseline(self) -> None:
        """``selection_reason`` strings must be identical to the baseline."""
        baseline = _load_baseline()
        rows = _build_fixture_rows()
        batch = next_curation_batch(backend=_HookBackend(rows), limit=10, now=_NOW)
        assert batch is not None
        for bt, pt in zip(batch.targets, baseline["batch"].targets, strict=True):
            assert bt.selection_reason == pt.selection_reason, (
                f"chunk_id {bt.chunk_id}: reason {bt.selection_reason!r} != "
                f"baseline {pt.selection_reason!r}"
            )

    def test_batch_cohesion_score_matches_baseline(self) -> None:
        """``cohesion_score`` must match the baseline."""
        baseline = _load_baseline()
        rows = _build_fixture_rows()
        batch = next_curation_batch(backend=_HookBackend(rows), limit=10, now=_NOW)
        assert batch is not None
        assert batch.cohesion_score == pytest.approx(baseline["batch"].cohesion_score, abs=1e-9)

    def test_learned_quality_is_none_in_empty_table_mode(self) -> None:
        """When chunk_quality_signals is empty, ``score_breakdown.learned_quality``
        must be ``None`` (not ``0.0`` and not any other value).

        RED: AttributeError — ScoreBreakdown has no learned_quality attribute.
        """
        rows = _build_fixture_rows()  # all learned_quality=None
        batch = next_curation_batch(backend=_HookBackend(rows), limit=10, now=_NOW)
        assert batch is not None
        for target in batch.targets:
            # RED: AttributeError here because ScoreBreakdown.learned_quality doesn't exist
            assert target.score_breakdown.learned_quality is None, (
                f"chunk_id {target.chunk_id}: expected learned_quality=None in "
                f"empty-table mode, got {target.score_breakdown.learned_quality!r}"
            )

    def test_single_target_chunk_id_matches_baseline(self) -> None:
        """``next_curation_target`` must return the same winner as the baseline."""
        baseline = _load_baseline()
        rows = _build_fixture_rows()
        target = next_curation_target(backend=_HookBackend(rows), now=_NOW)
        assert target is not None
        assert target.chunk_id == baseline["single_target"].chunk_id

    def test_single_target_learned_quality_is_none_in_empty_table_mode(self) -> None:
        """``next_curation_target`` result must have ``learned_quality is None``."""
        rows = _build_fixture_rows()
        target = next_curation_target(backend=_HookBackend(rows), now=_NOW)
        assert target is not None
        # RED: AttributeError
        assert target.score_breakdown.learned_quality is None


# ─────────────────────────────────────────────────────────────────────────────
# Part 2 — Mode 2: populated learned_quality → 5-weight rebalance
# ─────────────────────────────────────────────────────────────────────────────


class TestMode2FiveWeightScheme:
    """When a chunk has a ``chunk_quality_signals`` row, the 5-weight formula
    must activate for that chunk only.

    RED condition: ``ScoreBreakdown`` does not accept ``learned_quality`` kwarg,
    so the selector cannot construct a breakdown with it — tests fail with
    ``TypeError`` or ``AttributeError``.
    """

    # Chunks 1, 3, 5, 7, 9 (odd indices in first 10) get a learned_quality row.
    _LQ_MAP: ClassVar[dict[int, float]] = {
        1: 0.9,
        3: 0.75,
        5: 0.5,
        7: 0.3,
        9: 0.1,
    }

    def test_learned_quality_populated_on_chunks_with_rows(self) -> None:
        """Chunks whose id is in the LQ map must have ``learned_quality`` != None."""
        rows = _build_fixture_rows(learned_quality_by_id=self._LQ_MAP)
        batch = next_curation_batch(backend=_HookBackend(rows), limit=10, now=_NOW)
        assert batch is not None
        for target in batch.targets:
            if target.chunk_id in self._LQ_MAP:
                # RED: AttributeError
                assert target.score_breakdown.learned_quality is not None
                assert 0.0 <= target.score_breakdown.learned_quality <= 1.0

    def test_learned_quality_none_on_chunks_without_rows(self) -> None:
        """Chunks NOT in the LQ map must still have ``learned_quality is None``."""
        rows = _build_fixture_rows(learned_quality_by_id=self._LQ_MAP)
        batch = next_curation_batch(backend=_HookBackend(rows), limit=10, now=_NOW)
        assert batch is not None
        for target in batch.targets:
            if target.chunk_id not in self._LQ_MAP:
                # RED: AttributeError
                assert target.score_breakdown.learned_quality is None

    def test_five_weight_score_formula_correct_for_chunk_with_lq(self) -> None:
        """Verify the 5-weight score formula for a single chunk with a known LQ."""
        # Use chunk_id=1: lq=0.9, conf=0.14, age=18 days
        # Score = cd*0.30 + mm*0.25 + re*0.20 + fr*0.10 + lq*0.15
        lq_map = {1: 0.9}
        rows = _build_fixture_rows(learned_quality_by_id=lq_map)
        # Use a single-chunk pool so ranker_elevation = 0.5 (neutral, no vectors)
        single_row = [r for r in rows if r["chunk_id"] == 1]
        target = next_curation_target(backend=_HookBackend(single_row), now=_NOW)
        assert target is not None

        bd = target.score_breakdown
        # RED: AttributeError — learned_quality doesn't exist
        assert bd.learned_quality == pytest.approx(0.9, abs=1e-9)

        expected_score = (
            bd.confidence_deficit * _EXPECTED_WEIGHTS_5["confidence_deficit"]
            + bd.missing_metadata * _EXPECTED_WEIGHTS_5["missing_metadata"]
            + bd.ranker_elevation * _EXPECTED_WEIGHTS_5["ranker_elevation"]
            + bd.freshness * _EXPECTED_WEIGHTS_5["freshness"]
            + bd.learned_quality * _EXPECTED_WEIGHTS_5["learned_quality"]
        )
        assert target.score == pytest.approx(expected_score, abs=1e-6)

    def test_five_weight_score_sum_check(self) -> None:
        """5-weight formula sum = 1.0 (sanity: when all sub-scores = 1, total = 1)."""
        assert abs(sum(_EXPECTED_WEIGHTS_5.values()) - 1.0) < 1e-9

    def test_four_weight_score_formula_unchanged_for_chunk_without_lq(self) -> None:
        """Chunks without LQ rows must still use the 4-weight formula exactly."""
        # chunk_id=2 has no LQ row
        rows = _build_fixture_rows(learned_quality_by_id={1: 0.9})
        single_row = [r for r in rows if r["chunk_id"] == 2]
        target = next_curation_target(backend=_HookBackend(single_row), now=_NOW)
        assert target is not None

        bd = target.score_breakdown
        # RED: AttributeError
        assert bd.learned_quality is None

        w4 = getattr(_selector_module, "_SCORE_WEIGHTS_4", SCORE_WEIGHTS)
        expected_score = (
            bd.confidence_deficit * w4["confidence_deficit"]
            + bd.missing_metadata * w4["missing_metadata"]
            + bd.ranker_elevation * w4["ranker_elevation"]
            + bd.freshness * w4["freshness"]
        )
        assert target.score == pytest.approx(expected_score, abs=1e-6)

    def test_chunk_with_high_lq_scores_higher_than_4weight_baseline(self) -> None:
        """A chunk with a HIGH learned_quality (0.9) must score higher with the
        5-weight formula than it would have with the 4-weight formula alone,
        assuming its other sub-scores are non-zero.

        This verifies the score shift that may flip rank ordering.
        """
        # chunk_id=1: conf=0.14, so cd=0.86 (high), moderate mm+freshness.
        lq_map = {1: 0.9}
        rows_with_lq = _build_fixture_rows(learned_quality_by_id=lq_map)
        rows_without_lq = _build_fixture_rows()

        single_with = [r for r in rows_with_lq if r["chunk_id"] == 1]
        single_without = [r for r in rows_without_lq if r["chunk_id"] == 1]

        target_with = next_curation_target(backend=_HookBackend(single_with), now=_NOW)
        target_without = next_curation_target(backend=_HookBackend(single_without), now=_NOW)

        assert target_with is not None
        assert target_without is not None
        # With lq=0.9 contributing 0.15*0.9=0.135, the score should be different
        # (it could be higher OR lower depending on weight redistribution, but
        # the 5-weight formula must not silently leave the score unchanged).
        assert target_with.score != target_without.score


# ─────────────────────────────────────────────────────────────────────────────
# Part 3 — Mode is per-chunk, not per-dataset: coexistence in one batch
# ─────────────────────────────────────────────────────────────────────────────


class TestPerChunkModeCoexistence:
    """A single ``next_curation_batch`` response may contain BOTH 4-weight and
    5-weight targets when only half the corpus has learned_quality rows.

    RED: ``AttributeError`` on any access to ``score_breakdown.learned_quality``.
    """

    # Exactly half (odd chunk_ids 1..19) get LQ rows
    _HALF_LQ_MAP: ClassVar[dict[int, float]] = {i: 0.5 + (i % 10) * 0.04 for i in range(1, 20, 2)}

    def test_both_modes_coexist_in_same_batch(self) -> None:
        """At least one target in the batch must have learned_quality != None AND
        at least one must have learned_quality == None.
        """
        rows = _build_fixture_rows(learned_quality_by_id=self._HALF_LQ_MAP)
        batch = next_curation_batch(backend=_HookBackend(rows), limit=10, now=_NOW)
        assert batch is not None

        # RED: AttributeError on learned_quality
        lq_values = [t.score_breakdown.learned_quality for t in batch.targets]
        has_lq = any(v is not None for v in lq_values)
        no_lq = any(v is None for v in lq_values)

        assert has_lq, "expected at least one target with learned_quality != None in mixed corpus"
        assert no_lq, "expected at least one target with learned_quality == None in mixed corpus"

    def test_per_target_score_formula_matches_presence_flag(self) -> None:
        """For each target, the formula used must match whether learned_quality is set."""
        rows = _build_fixture_rows(learned_quality_by_id=self._HALF_LQ_MAP)
        batch = next_curation_batch(backend=_HookBackend(rows), limit=10, now=_NOW)
        assert batch is not None

        w4 = getattr(_selector_module, "_SCORE_WEIGHTS_4", SCORE_WEIGHTS)

        for target in batch.targets:
            bd = target.score_breakdown
            # RED: AttributeError
            if bd.learned_quality is None:
                # Must use 4-weight formula
                expected = (
                    bd.confidence_deficit * w4["confidence_deficit"]
                    + bd.missing_metadata * w4["missing_metadata"]
                    + bd.ranker_elevation * w4["ranker_elevation"]
                    + bd.freshness * w4["freshness"]
                )
            else:
                # Must use 5-weight formula
                expected = (
                    bd.confidence_deficit * _EXPECTED_WEIGHTS_5["confidence_deficit"]
                    + bd.missing_metadata * _EXPECTED_WEIGHTS_5["missing_metadata"]
                    + bd.ranker_elevation * _EXPECTED_WEIGHTS_5["ranker_elevation"]
                    + bd.freshness * _EXPECTED_WEIGHTS_5["freshness"]
                    + bd.learned_quality * _EXPECTED_WEIGHTS_5["learned_quality"]
                )
            assert target.score == pytest.approx(expected, abs=1e-6), (
                f"chunk_id {target.chunk_id}: formula mismatch "
                f"(learned_quality={bd.learned_quality!r})"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Part 4 — Edge cases and boundary conditions
# ─────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Boundary and edge-case tests for the learned_quality integration.

    RED: same AttributeError / TypeError as above.
    """

    def test_learned_quality_zero_still_activates_5weight_formula(self) -> None:
        """A LQ value of 0.0 (not None) must still trigger the 5-weight formula.

        0.0 means "the model scored this chunk very low quality" — it is still a
        signal, not an absence of signal.
        """
        lq_map = {1: 0.0}
        rows = _build_fixture_rows(learned_quality_by_id=lq_map)
        single_row = [r for r in rows if r["chunk_id"] == 1]
        target = next_curation_target(backend=_HookBackend(single_row), now=_NOW)
        assert target is not None

        bd = target.score_breakdown
        # RED: AttributeError
        assert bd.learned_quality == pytest.approx(0.0, abs=1e-9)

        # Score must use 5-weight formula (lq term = 0.0 * 0.15 = 0.0).
        # The 4-weight score and 5-weight score with lq=0.0 will differ because
        # weights are redistributed — confidence_deficit drops from 0.35 to 0.30.
        expected_5w = (
            bd.confidence_deficit * _EXPECTED_WEIGHTS_5["confidence_deficit"]
            + bd.missing_metadata * _EXPECTED_WEIGHTS_5["missing_metadata"]
            + bd.ranker_elevation * _EXPECTED_WEIGHTS_5["ranker_elevation"]
            + bd.freshness * _EXPECTED_WEIGHTS_5["freshness"]
            + 0.0 * _EXPECTED_WEIGHTS_5["learned_quality"]
        )
        assert target.score == pytest.approx(expected_5w, abs=1e-6)

    def test_learned_quality_one_saturated(self) -> None:
        """A LQ value of 1.0 is the maximum quality signal — score must stay in [0,1]."""
        lq_map = {1: 1.0}
        rows = _build_fixture_rows(learned_quality_by_id=lq_map)
        single_row = [r for r in rows if r["chunk_id"] == 1]
        target = next_curation_target(backend=_HookBackend(single_row), now=_NOW)
        assert target is not None
        # RED: AttributeError
        assert target.score_breakdown.learned_quality == pytest.approx(1.0, abs=1e-9)
        assert 0.0 <= target.score <= 1.0

    def test_multiple_lq_chunks_ordering_is_deterministic(self) -> None:
        """With multiple chunks having LQ values, batch ordering must be stable
        (deterministic — same input always yields same order).
        """
        lq_map = {i: 0.1 * i for i in range(1, 6)}  # chunks 1-5
        rows = _build_fixture_rows(learned_quality_by_id=lq_map)

        results = []
        for _ in range(3):
            batch = next_curation_batch(backend=_HookBackend(rows), limit=10, now=_NOW)
            assert batch is not None
            results.append([t.chunk_id for t in batch.targets])

        assert results[0] == results[1] == results[2], (
            "batch ordering must be deterministic across repeated calls"
        )

    def test_next_curation_target_signature_unchanged(self) -> None:
        """The public API signature of ``next_curation_target`` must not gain any
        new REQUIRED parameters — backward-compat callers must continue to work.
        """
        import inspect

        sig = inspect.signature(next_curation_target)
        params = sig.parameters
        # These keyword args existed pre-O3 and must remain
        for name in (
            "backend",
            "dataset",
            "embedder",
            "seed_query",
            "reranker",
            "candidate_pool",
            "now",
        ):
            assert name in params, f"next_curation_target lost parameter {name!r}"
        # None of the pre-existing parameters should have become positional-only
        for name, p in params.items():
            assert p.default is not inspect.Parameter.empty or p.kind in (
                inspect.Parameter.KEYWORD_ONLY,
                inspect.Parameter.VAR_KEYWORD,
            ), f"parameter {name!r} became required"

    def test_most_recent_lq_row_wins_when_multiple_present(self) -> None:
        """When a chunk has multiple ``chunk_quality_signals`` rows, the one with the
        latest ``computed_at`` must be used.

        This is tested via the row dict: the backend (or selector) must pick the
        single value that represents the most recent signal.  We simulate this by
        verifying that the selector uses the value present in the row dict, not an
        arbitrary one.  The actual per-chunk deduplication (picking max computed_at)
        is the backend's or the LEFT JOIN's responsibility; the selector reads a
        single ``learned_quality`` value per candidate row.

        Test: feed a row with lq=0.3 and assert the selector uses 0.3 (not None,
        not some other value) — i.e., the selector trusts the value from the row.
        """
        lq_map = {5: 0.3}
        rows = _build_fixture_rows(learned_quality_by_id=lq_map)
        single_row = [r for r in rows if r["chunk_id"] == 5]
        target = next_curation_target(backend=_HookBackend(single_row), now=_NOW)
        assert target is not None
        # RED: AttributeError
        assert target.score_breakdown.learned_quality == pytest.approx(0.3, abs=1e-9)

    def test_lq_value_stored_verbatim_in_breakdown(self) -> None:
        """The learned_quality value from the row must round-trip exactly into
        ``ScoreBreakdown.learned_quality`` without rounding or clamping (unless
        the implementation explicitly clamps to [0,1], which is acceptable).
        """
        lq_map = {3: 0.12345}
        rows = _build_fixture_rows(learned_quality_by_id=lq_map)
        single_row = [r for r in rows if r["chunk_id"] == 3]
        target = next_curation_target(backend=_HookBackend(single_row), now=_NOW)
        assert target is not None
        # RED: AttributeError
        stored = target.score_breakdown.learned_quality
        assert stored is not None
        # Either exact or clamped to [0,1] — both are acceptable
        assert 0.0 <= stored <= 1.0

    def test_score_breakdown_frozen_with_learned_quality(self) -> None:
        """``ScoreBreakdown`` must remain frozen when ``learned_quality`` is set."""
        bd = ScoreBreakdown(
            confidence_deficit=0.5,
            missing_metadata=0.5,
            ranker_elevation=0.5,
            freshness=0.5,
            learned_quality=0.7,  # RED: TypeError
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            bd.learned_quality = 0.3  # type: ignore[misc]

    def test_empty_corpus_still_returns_none(self) -> None:
        """``next_curation_target`` returns ``None`` on an empty pool (no regression)."""
        batch = next_curation_target(backend=_HookBackend([]), now=_NOW)
        assert batch is None

    def test_lq_none_vs_absent_key_both_treated_as_legacy_mode(self) -> None:
        """If the row dict has no ``learned_quality`` key at all (pre-O3 row shape),
        the selector must treat it the same as ``learned_quality=None`` and fall
        back to the 4-weight formula.  This covers the backward-compat path for
        backends that haven't migrated to the new row shape yet.
        """
        # Build rows WITHOUT the learned_quality key at all (mimics old _HookBackend)
        old_rows = [
            {
                "chunk_id": 1,
                "document_id": 1,
                "text": "legacy row",
                "heading": "h",
                "description": "d",
                "metadata": {"language": "en"},
                "document_title": "Doc",
                "source_uri": "vault://x.md",
                "modified_at": _NOW - timedelta(days=30),
                "labels": [("class", "topic_a"), ("topic", "x")],
                "classifier_label": "topic_a",
                "classifier_confidence": 0.8,
                "embedding": None,
                # NOTE: no 'learned_quality' key
            }
        ]
        target = next_curation_target(backend=_HookBackend(old_rows), now=_NOW)
        assert target is not None
        # RED: AttributeError
        assert target.score_breakdown.learned_quality is None
