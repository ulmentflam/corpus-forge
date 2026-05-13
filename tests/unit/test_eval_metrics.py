"""R3-02 — pure-NumPy retrieval metric unit pins.

Three metric functions live in ``corpus_forge.eval.metrics``:

- ``ndcg_at_k(ranked_ids, relevant_ids, k, *, graded=None) -> float``
- ``mrr_at_k(ranked_ids, relevant_ids, k) -> float``
- ``recall_at_k(ranked_ids, relevant_ids, k) -> float``

Discipline:

- All three return a Python float in ``[0.0, 1.0]``.
- ``ranked_ids``: list[int] from the retriever (top-1 first).
- ``relevant_ids``: set[int] | list[int] of ground-truth chunk ids.
- ``graded`` (NDCG only): dict[int, int] of chunk_id → grade (0..G).  Absent
  ⇒ binary relevance (presence in ``relevant_ids`` counts as grade 1).
- Edge cases: empty ranking → 0.0; empty relevant set → 0.0; k > len(ranked)
  uses what's there (no IndexError).
"""

from __future__ import annotations

import math


# ── module presence ───────────────────────────────────────────────────────


def test_module_importable():
    import corpus_forge.eval.metrics  # noqa: F401


def test_public_api_present():
    from corpus_forge.eval.metrics import mrr_at_k, ndcg_at_k, recall_at_k  # noqa: F401


# ── NDCG: binary relevance (known answers) ────────────────────────────────


class TestNDCGBinary:
    def test_perfect_ranking_returns_one(self):
        from corpus_forge.eval.metrics import ndcg_at_k

        # All 3 relevant items in top-3 in any order yield NDCG@3 == 1.0
        # because both DCG and IDCG sum the same gains in the same positions.
        assert ndcg_at_k([1, 2, 3], {1, 2, 3}, k=3) == 1.0

    def test_empty_ranking_returns_zero(self):
        from corpus_forge.eval.metrics import ndcg_at_k

        assert ndcg_at_k([], {1, 2, 3}, k=10) == 0.0

    def test_empty_relevant_returns_zero(self):
        from corpus_forge.eval.metrics import ndcg_at_k

        assert ndcg_at_k([1, 2, 3], set(), k=3) == 0.0

    def test_no_overlap_returns_zero(self):
        from corpus_forge.eval.metrics import ndcg_at_k

        assert ndcg_at_k([4, 5, 6], {1, 2, 3}, k=3) == 0.0

    def test_known_answer_partial_overlap(self):
        """NDCG@3 with binary relevance on [1,2,3] given relevant={1,3}.

        DCG  = 1/log2(2) + 0 + 1/log2(4) = 1.0 + 0 + 0.5 = 1.5
        IDCG = 1/log2(2) + 1/log2(3) + 0 = 1.0 + 1/log2(3)
             = 1.0 + 0.6309297535714574 ≈ 1.6309297535714574
        NDCG = DCG / IDCG ≈ 0.9197...
        """
        from corpus_forge.eval.metrics import ndcg_at_k

        expected = 1.5 / (1.0 + 1.0 / math.log2(3))
        got = ndcg_at_k([1, 2, 3], {1, 3}, k=3)
        assert math.isclose(got, expected, rel_tol=1e-9)

    def test_k_greater_than_len_uses_what_is_there(self):
        from corpus_forge.eval.metrics import ndcg_at_k

        # Ranking [1,2] with k=10, relevant={1,2}.  Should NOT IndexError,
        # and should give 1.0 because the truncated ranking is a perfect prefix.
        assert ndcg_at_k([1, 2], {1, 2}, k=10) == 1.0

    def test_k_zero_returns_zero(self):
        from corpus_forge.eval.metrics import ndcg_at_k

        assert ndcg_at_k([1, 2, 3], {1, 2, 3}, k=0) == 0.0

    def test_truncation_at_k(self):
        """Ranking [1,2,3,4] with relevant={4}, k=3 → 0.0 (item 4 dropped)."""
        from corpus_forge.eval.metrics import ndcg_at_k

        assert ndcg_at_k([1, 2, 3, 4], {4}, k=3) == 0.0


# ── NDCG: graded relevance ────────────────────────────────────────────────


class TestNDCGGraded:
    def test_perfect_graded_ranking_returns_one(self):
        from corpus_forge.eval.metrics import ndcg_at_k

        # Higher grades first → matches ideal → NDCG == 1.0.
        # gain(grade) = 2**grade - 1  → grades [3,2,1] sit at positions 1,2,3.
        graded = {1: 3, 2: 2, 3: 1}
        assert ndcg_at_k([1, 2, 3], {1, 2, 3}, k=3, graded=graded) == 1.0

    def test_graded_partial_known_answer(self):
        """Ranking [1,2,3] with grades {1:3, 2:1, 3:2}, k=3.

        gains = 2**grade - 1  → [7, 1, 3]
        discounts = 1/log2(rank+1) for rank=1..3 → [1.0, 1/log2(3), 0.5]

        DCG  = 7*1.0 + 1*(1/log2(3)) + 3*0.5 = 7 + 0.6309... + 1.5 = 9.1309...

        IDCG: sorted gains desc are [7, 3, 1]
        IDCG = 7*1.0 + 3*(1/log2(3)) + 1*0.5 = 7 + 1.8927... + 0.5 = 9.3927...

        NDCG ≈ 0.9722...
        """
        from corpus_forge.eval.metrics import ndcg_at_k

        graded = {1: 3, 2: 1, 3: 2}
        dcg = 7 + (1.0 / math.log2(3)) + 1.5
        idcg = 7 + 3 * (1.0 / math.log2(3)) + 0.5
        expected = dcg / idcg
        got = ndcg_at_k([1, 2, 3], {1, 2, 3}, k=3, graded=graded)
        assert math.isclose(got, expected, rel_tol=1e-9)

    def test_graded_keys_may_be_str(self):
        """Loader may emit str keys (JSON); metric tolerates either."""
        from corpus_forge.eval.metrics import ndcg_at_k

        graded_str = {"1": 3, "2": 2, "3": 1}
        graded_int = {1: 3, 2: 2, 3: 1}
        a = ndcg_at_k([1, 2, 3], {1, 2, 3}, k=3, graded=graded_str)
        b = ndcg_at_k([1, 2, 3], {1, 2, 3}, k=3, graded=graded_int)
        assert math.isclose(a, b, rel_tol=1e-12)

    def test_graded_falls_back_to_relevant_for_missing_keys(self):
        """If a relevant id is missing from `graded`, treat its grade as 1."""
        from corpus_forge.eval.metrics import ndcg_at_k

        # Relevant {1,2,3}; graded only has {1:3,2:2}.  Item 3 should get grade 1.
        graded = {1: 3, 2: 2}
        # Perfect order is [1,2,3] (gains 7,3,1) → NDCG@3 == 1.0
        assert ndcg_at_k([1, 2, 3], {1, 2, 3}, k=3, graded=graded) == 1.0

    def test_irrelevant_in_graded_is_zero(self):
        """A chunk_id in `graded` with grade 0 contributes 0 gain."""
        from corpus_forge.eval.metrics import ndcg_at_k

        # Ranking [1,2,3]; graded {1:0, 2:0, 3:0}; relevant set is what graded
        # advertises as nonzero -- here, nothing.  NDCG should be 0.
        graded = {1: 0, 2: 0, 3: 0}
        assert ndcg_at_k([1, 2, 3], set(), k=3, graded=graded) == 0.0


# ── MRR ──────────────────────────────────────────────────────────────────


class TestMRR:
    def test_first_hit_at_rank_1(self):
        from corpus_forge.eval.metrics import mrr_at_k

        assert mrr_at_k([1, 2, 3], {1}, k=10) == 1.0

    def test_first_hit_at_rank_5(self):
        from corpus_forge.eval.metrics import mrr_at_k

        # Ranking [1,2,3,4,5] with relevant={5} → MRR = 1/5 = 0.2
        got = mrr_at_k([1, 2, 3, 4, 5], {5}, k=10)
        assert math.isclose(got, 0.2, rel_tol=1e-9)

    def test_no_hit_returns_zero(self):
        from corpus_forge.eval.metrics import mrr_at_k

        assert mrr_at_k([1, 2, 3], {99}, k=10) == 0.0

    def test_first_hit_outside_k_returns_zero(self):
        from corpus_forge.eval.metrics import mrr_at_k

        # Hit lives at rank 5 but k=3 → 0.
        assert mrr_at_k([1, 2, 3, 4, 5], {5}, k=3) == 0.0

    def test_empty_ranking_returns_zero(self):
        from corpus_forge.eval.metrics import mrr_at_k

        assert mrr_at_k([], {1, 2, 3}, k=10) == 0.0

    def test_empty_relevant_returns_zero(self):
        from corpus_forge.eval.metrics import mrr_at_k

        assert mrr_at_k([1, 2, 3], set(), k=10) == 0.0


# ── Recall ───────────────────────────────────────────────────────────────


class TestRecall:
    def test_full_recall(self):
        from corpus_forge.eval.metrics import recall_at_k

        # All 2 relevant items present in top-5 ⇒ recall = 1.0
        assert recall_at_k([1, 2, 3, 4, 5], {1, 2}, k=5) == 1.0

    def test_half_recall(self):
        from corpus_forge.eval.metrics import recall_at_k

        # Relevant {1,2,3}; top-5 ranking has [1,4,5,6,7] → recall = 1/3.
        got = recall_at_k([1, 4, 5, 6, 7], {1, 2, 3}, k=5)
        assert math.isclose(got, 1.0 / 3.0, rel_tol=1e-9)

    def test_no_overlap_returns_zero(self):
        from corpus_forge.eval.metrics import recall_at_k

        assert recall_at_k([4, 5, 6], {1, 2, 3}, k=3) == 0.0

    def test_k_truncation(self):
        from corpus_forge.eval.metrics import recall_at_k

        # Relevant {3}; top-2 of [1,2,3,4] = [1,2] → recall = 0.
        assert recall_at_k([1, 2, 3, 4], {3}, k=2) == 0.0

    def test_empty_ranking_returns_zero(self):
        from corpus_forge.eval.metrics import recall_at_k

        assert recall_at_k([], {1, 2, 3}, k=10) == 0.0

    def test_empty_relevant_returns_zero(self):
        from corpus_forge.eval.metrics import recall_at_k

        assert recall_at_k([1, 2, 3], set(), k=10) == 0.0

    def test_k_greater_than_len_uses_what_is_there(self):
        from corpus_forge.eval.metrics import recall_at_k

        # Ranking [1,2], relevant={1,2}, k=10 → recall = 1.0
        assert recall_at_k([1, 2], {1, 2}, k=10) == 1.0


# ── input-shape tolerance ────────────────────────────────────────────────


class TestInputShapes:
    def test_relevant_as_list_or_set_equivalent(self):
        from corpus_forge.eval.metrics import mrr_at_k, ndcg_at_k, recall_at_k

        ranking = [1, 2, 3, 4, 5]
        as_list = [1, 3]
        as_set = {1, 3}
        for fn in (ndcg_at_k, mrr_at_k, recall_at_k):
            a = fn(ranking, as_list, k=5)
            b = fn(ranking, as_set, k=5)
            assert math.isclose(a, b, rel_tol=1e-12), f"{fn.__name__} differs"

    def test_duplicates_in_ranking_do_not_double_count(self):
        """Defensive: ranking shouldn't contain duplicates but if it does,
        recall must not exceed 1.0."""
        from corpus_forge.eval.metrics import recall_at_k

        got = recall_at_k([1, 1, 1], {1}, k=3)
        assert got <= 1.0
        assert math.isclose(got, 1.0, rel_tol=1e-9)
