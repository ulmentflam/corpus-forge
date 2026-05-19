"""Phase M Wave 5 — unit tests for ``tests.perf.metrics``.

Ungated: these tests run without semble installed and without
``CF_SEMBLE_BENCH``.  They cover the metric primitives and the top-level
``compute_metrics`` aggregator with hand-rolled hit lists.
"""

from __future__ import annotations

from types import SimpleNamespace

from tests.perf.metrics import (
    compute_metrics,
    hit_matches_ground_truth,
    mrr_at_k,
    percentile,
    recall_at_k,
)

# ── primitives ──────────────────────────────────────────────────────────


class TestMrrAtK:
    def test_first_hit_relevant_gives_one(self) -> None:
        assert mrr_at_k([True, False, False], k=10) == 1.0

    def test_second_hit_relevant_gives_half(self) -> None:
        assert mrr_at_k([False, True, False], k=10) == 0.5

    def test_third_hit_relevant_gives_one_third(self) -> None:
        assert abs(mrr_at_k([False, False, True], k=10) - (1.0 / 3.0)) < 1e-9

    def test_no_relevant_in_top_k_returns_zero(self) -> None:
        assert mrr_at_k([False, False, False, True], k=3) == 0.0

    def test_empty_input_returns_zero(self) -> None:
        assert mrr_at_k([], k=10) == 0.0

    def test_k_zero_returns_zero(self) -> None:
        assert mrr_at_k([True], k=0) == 0.0

    def test_k_negative_returns_zero(self) -> None:
        assert mrr_at_k([True], k=-1) == 0.0


class TestRecallAtK:
    def test_all_relevant_recovered_returns_one(self) -> None:
        # 2 ground-truth, 2 hits at rank 1 & 2, both relevant
        assert recall_at_k([True, True, False], total_relevant=2, k=5) == 1.0

    def test_half_recovered_returns_half(self) -> None:
        # 2 ground-truth, 1 relevant in top-5
        assert recall_at_k([True, False, False, False, False], total_relevant=2, k=5) == 0.5

    def test_relevant_outside_k_does_not_count(self) -> None:
        # 1 ground-truth, relevant at rank 10, k=5 → recall 0
        flags = [False, False, False, False, False, False, False, False, False, True]
        assert recall_at_k(flags, total_relevant=1, k=5) == 0.0

    def test_zero_ground_truth_returns_zero(self) -> None:
        assert recall_at_k([True, True], total_relevant=0, k=5) == 0.0

    def test_clamped_to_one_when_duplicate_relevant(self) -> None:
        # 1 ground-truth chunk, but two hits both overlap it → 2/1 → clamped to 1.0
        assert recall_at_k([True, True], total_relevant=1, k=5) == 1.0

    def test_empty_input_returns_zero(self) -> None:
        assert recall_at_k([], total_relevant=3, k=5) == 0.0


class TestPercentile:
    def test_p50_of_odd_list(self) -> None:
        assert percentile([10.0, 20.0, 30.0], 50.0) == 20.0

    def test_p95_of_simple_list(self) -> None:
        # 20 samples 1..20; nearest-rank p95 = sample at rank ceil(0.95*20) = 19
        samples = [float(i) for i in range(1, 21)]
        assert percentile(samples, 95.0) == 19.0

    def test_p0_returns_min(self) -> None:
        # rank = max(1, round(0)) = 1 → samples[0] after sort
        assert percentile([3.0, 1.0, 2.0], 0.0) == 1.0

    def test_p100_returns_max(self) -> None:
        assert percentile([3.0, 1.0, 2.0], 100.0) == 3.0

    def test_empty_returns_zero(self) -> None:
        assert percentile([], 50.0) == 0.0

    def test_invalid_pct_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="pct must be"):
            percentile([1.0], 101.0)


# ── overlap check ───────────────────────────────────────────────────────


class TestHitMatchesGroundTruth:
    def test_exact_match_overlaps(self) -> None:
        truth = [{"file": "a.py", "byte_start": 100, "byte_end": 200}]
        assert hit_matches_ground_truth("a.py", 100, 200, truth)

    def test_partial_overlap_above_threshold(self) -> None:
        truth = [{"file": "a.py", "byte_start": 100, "byte_end": 200}]
        # overlap = min(180, 200) - max(150, 100) = 30; default threshold 32 → fail
        assert not hit_matches_ground_truth("a.py", 150, 180, truth)
        # widen the hit so overlap >= 32
        assert hit_matches_ground_truth("a.py", 150, 200, truth)

    def test_no_overlap(self) -> None:
        truth = [{"file": "a.py", "byte_start": 100, "byte_end": 200}]
        assert not hit_matches_ground_truth("a.py", 300, 400, truth)

    def test_different_file_never_matches(self) -> None:
        truth = [{"file": "a.py", "byte_start": 100, "byte_end": 200}]
        assert not hit_matches_ground_truth("b.py", 100, 200, truth)

    def test_leading_dot_slash_normalised(self) -> None:
        truth = [{"file": "a.py", "byte_start": 0, "byte_end": 64}]
        assert hit_matches_ground_truth("./a.py", 0, 64, truth)

    def test_backslash_to_forward_slash(self) -> None:
        truth = [{"file": "dir/a.py", "byte_start": 0, "byte_end": 64}]
        assert hit_matches_ground_truth("dir\\a.py", 0, 64, truth)

    def test_threshold_respected_when_explicit(self) -> None:
        truth = [{"file": "a.py", "byte_start": 0, "byte_end": 100}]
        # 5-byte overlap → fails default 32-byte threshold
        assert not hit_matches_ground_truth("a.py", 95, 105, truth)
        # Same hit + threshold of 4 succeeds
        assert hit_matches_ground_truth("a.py", 95, 105, truth, min_overlap_bytes=4)

    def test_inverted_hit_span_does_not_match(self) -> None:
        truth = [{"file": "a.py", "byte_start": 0, "byte_end": 100}]
        assert not hit_matches_ground_truth("a.py", 200, 100, truth)

    def test_multiple_truth_entries_any_match_succeeds(self) -> None:
        truth = [
            {"file": "a.py", "byte_start": 0, "byte_end": 100},
            {"file": "b.py", "byte_start": 200, "byte_end": 400},
        ]
        assert hit_matches_ground_truth("b.py", 250, 350, truth)


# ── top-level aggregator ────────────────────────────────────────────────


def _mkhit(file: str, start: int, end: int) -> SimpleNamespace:
    """Helper: build a Hit-like SimpleNamespace with metadata dict."""
    return SimpleNamespace(
        metadata={"file_path": file, "byte_start": start, "byte_end": end}
    )


class TestComputeMetrics:
    def test_perfect_retrieval(self) -> None:
        # Two queries, each with one ground-truth chunk; both retrieved
        # at rank 1.
        ground_truth = {
            "q1": [{"file": "a.py", "byte_start": 0, "byte_end": 100}],
            "q2": [{"file": "b.py", "byte_start": 200, "byte_end": 400}],
        }
        hits = {
            "q1": {"hits": [_mkhit("a.py", 0, 100)], "latency_ms": 5.0},
            "q2": {"hits": [_mkhit("b.py", 200, 400)], "latency_ms": 7.0},
        }
        m = compute_metrics(hits, ground_truth)
        assert m["mrr_at_10"] == 1.0
        assert m["recall_at_5"] == 1.0
        assert m["n_queries"] == 2
        # p50 of [5.0, 7.0] under nearest-rank = lower median = 5.0
        assert m["p50_latency_ms"] == 5.0
        # p95 of [5.0, 7.0] = max
        assert m["p95_latency_ms"] == 7.0

    def test_missing_query_in_hits_scored_as_zero(self) -> None:
        ground_truth = {
            "q1": [{"file": "a.py", "byte_start": 0, "byte_end": 100}],
        }
        # No hits for q1 at all
        m = compute_metrics({}, ground_truth)
        assert m["mrr_at_10"] == 0.0
        assert m["recall_at_5"] == 0.0
        assert m["n_queries"] == 1
        assert m["per_query"][0]["n_hits"] == 0

    def test_irrelevant_hits_score_zero_mrr(self) -> None:
        ground_truth = {
            "q1": [{"file": "a.py", "byte_start": 0, "byte_end": 100}],
        }
        hits = {
            "q1": {
                "hits": [
                    _mkhit("x.py", 0, 100),  # wrong file
                    _mkhit("a.py", 500, 600),  # wrong range
                ],
                "latency_ms": 3.0,
            }
        }
        m = compute_metrics(hits, ground_truth)
        assert m["mrr_at_10"] == 0.0
        assert m["recall_at_5"] == 0.0

    def test_second_rank_relevant_gives_half_mrr(self) -> None:
        ground_truth = {
            "q1": [{"file": "a.py", "byte_start": 0, "byte_end": 200}],
        }
        hits = {
            "q1": {
                "hits": [
                    _mkhit("x.py", 0, 100),
                    _mkhit("a.py", 0, 200),
                ],
                "latency_ms": 4.0,
            }
        }
        m = compute_metrics(hits, ground_truth)
        assert m["mrr_at_10"] == 0.5
        assert m["recall_at_5"] == 1.0

    def test_keys_are_dynamic_per_k(self) -> None:
        m = compute_metrics({}, {"q1": [{"file": "a", "byte_start": 0, "byte_end": 64}]},
                            k_mrr=20, k_recall=10)
        assert "mrr_at_20" in m
        assert "recall_at_10" in m

    def test_per_query_block_populated(self) -> None:
        ground_truth = {"q1": [{"file": "a.py", "byte_start": 0, "byte_end": 100}]}
        hits = {"q1": {"hits": [_mkhit("a.py", 0, 100)], "latency_ms": 12.5}}
        m = compute_metrics(hits, ground_truth)
        pq = m["per_query"][0]
        assert pq["query_id"] == "q1"
        assert pq["n_hits"] == 1
        assert pq["n_relevant_hits"] == 1
        assert pq["latency_ms"] == 12.5

    def test_query_with_no_ground_truth_excluded_from_means(self) -> None:
        # If a query has empty ground truth, including it would unfairly
        # drag means to 0.  We exclude it from the means but keep it in
        # per_query for transparency.
        ground_truth = {"q1": [{"file": "a.py", "byte_start": 0, "byte_end": 100}],
                        "q2": []}
        hits = {
            "q1": {"hits": [_mkhit("a.py", 0, 100)], "latency_ms": 1.0},
            "q2": {"hits": [_mkhit("z.py", 0, 100)], "latency_ms": 1.0},
        }
        m = compute_metrics(hits, ground_truth)
        # Mean MRR computed only over q1 → 1.0
        assert m["mrr_at_10"] == 1.0
        assert m["n_queries"] == 2  # but both still in the per_query list

    def test_hit_missing_metadata_treated_as_irrelevant(self) -> None:
        # A hit without metadata keys must not crash.
        bad_hit = SimpleNamespace(metadata={})
        ground_truth = {"q1": [{"file": "a.py", "byte_start": 0, "byte_end": 100}]}
        m = compute_metrics(
            {"q1": {"hits": [bad_hit], "latency_ms": 0.0}}, ground_truth
        )
        assert m["mrr_at_10"] == 0.0
