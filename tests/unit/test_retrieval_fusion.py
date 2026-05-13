"""R2-01 — `corpus_forge.retrieval.fusion` unit pins.

Two fusion strategies:

1. ``reciprocal_rank_fusion(rankings, k_rrf=60)`` — rank-based, score-free.
   Given a list of rankings (each ranking is an ordered list of ids), returns
   a dict ``{id: fused_score}`` where
   ``fused_score(id) = sum( 1 / (k_rrf + rank(id, ranking_i)) )`` over rankings
   that contain ``id``.  Items absent from a ranking simply don't contribute
   from that ranking.

2. ``alpha_blend(dense, lexical, alpha)`` — score-based.
   ``dense`` and ``lexical`` are ``dict[int, float]`` of already-normalised
   scores (caller responsibility — see ``normalize.min_max``).  Returns a
   ``dict[int, float]`` with
   ``score(id) = alpha * dense.get(id, 0.0) + (1 - alpha) * lexical.get(id, 0.0)``.

Both functions are pure and side-effect free.  They live in
``corpus_forge.retrieval.fusion`` and re-export from ``corpus_forge.retrieval``.
"""

from __future__ import annotations

import pytest

# ── module + re-exports ───────────────────────────────────────────────────


def test_module_importable():
    import corpus_forge.retrieval.fusion  # noqa: F401


def test_rrf_reexported_from_package():
    from corpus_forge.retrieval import reciprocal_rank_fusion  # noqa: F401


def test_alpha_blend_reexported_from_package():
    from corpus_forge.retrieval import alpha_blend  # noqa: F401


# ── reciprocal_rank_fusion ────────────────────────────────────────────────


class TestRRF:
    def _fn(self):
        from corpus_forge.retrieval.fusion import reciprocal_rank_fusion

        return reciprocal_rank_fusion

    def test_single_ranking_identity(self):
        """A single ranking yields scores ordered as the input."""
        out = self._fn()([[10, 20, 30]], k_rrf=60)
        # Ordered by score desc:
        ranked = sorted(out.items(), key=lambda kv: -kv[1])
        assert [k for k, _ in ranked] == [10, 20, 30]
        # Top item has score 1/(60+1) = 1/61
        assert out[10] == pytest.approx(1 / 61)
        assert out[20] == pytest.approx(1 / 62)
        assert out[30] == pytest.approx(1 / 63)

    def test_two_rankings_agreement_boosts(self):
        """An item ranked top in both lists must beat any item ranked in only one."""
        dense = [1, 2, 3, 4]
        lexical = [1, 5, 6, 7]
        out = self._fn()([dense, lexical], k_rrf=60)
        # 1 appears top in both → score 1/61 + 1/61 = 2/61
        assert out[1] == pytest.approx(2 / 61)
        # 2 appears only in dense (rank 1) → 1/62; 5 appears only in lexical
        # (rank 1) → 1/62.  Both lose to 1.
        ranked = sorted(out.items(), key=lambda kv: -kv[1])
        assert ranked[0][0] == 1

    def test_empty_input(self):
        assert self._fn()([]) == {}

    def test_empty_rankings(self):
        assert self._fn()([[], []]) == {}

    def test_rank_stable_across_kr_rf_within_reason(self):
        """The relative ordering must not flip for typical k_rrf values."""
        dense = [1, 2, 3]
        lexical = [3, 1, 2]
        out_a = self._fn()([dense, lexical], k_rrf=60)
        out_b = self._fn()([dense, lexical], k_rrf=100)
        rank_a = sorted(out_a.items(), key=lambda kv: -kv[1])
        rank_b = sorted(out_b.items(), key=lambda kv: -kv[1])
        # Top hit must agree
        assert rank_a[0][0] == rank_b[0][0]

    def test_disjoint_rankings(self):
        """Disjoint ids: each id gets exactly one contribution."""
        dense = [1, 2]
        lexical = [3, 4]
        out = self._fn()([dense, lexical], k_rrf=60)
        assert set(out) == {1, 2, 3, 4}
        assert out[1] == pytest.approx(1 / 61)
        assert out[3] == pytest.approx(1 / 61)

    def test_does_not_mutate_input(self):
        dense = [1, 2, 3]
        _ = self._fn()([dense], k_rrf=60)
        assert dense == [1, 2, 3]

    def test_k_rrf_default_is_60(self):
        """Plan specifies default k_rrf=60."""
        out_default = self._fn()([[1, 2, 3]])
        out_explicit = self._fn()([[1, 2, 3]], k_rrf=60)
        assert out_default == out_explicit


# ── alpha_blend ───────────────────────────────────────────────────────────


class TestAlphaBlend:
    def _fn(self):
        from corpus_forge.retrieval.fusion import alpha_blend

        return alpha_blend

    def test_alpha_zero_returns_lexical_only(self):
        out = self._fn()({1: 0.9, 2: 0.5}, {1: 0.2, 3: 0.7}, alpha=0.0)
        assert out == {1: pytest.approx(0.2), 2: pytest.approx(0.0), 3: pytest.approx(0.7)}

    def test_alpha_one_returns_dense_only(self):
        out = self._fn()({1: 0.9, 2: 0.5}, {1: 0.2, 3: 0.7}, alpha=1.0)
        assert out == {1: pytest.approx(0.9), 2: pytest.approx(0.5), 3: pytest.approx(0.0)}

    def test_alpha_half_averages(self):
        out = self._fn()({1: 1.0}, {1: 0.0}, alpha=0.5)
        assert out[1] == pytest.approx(0.5)

    def test_monotonic_in_alpha(self):
        """As alpha rises, the dense-favoured item's score must rise."""
        dense = {1: 1.0}
        lexical = {1: 0.0}
        a_low = self._fn()(dense, lexical, alpha=0.1)
        a_mid = self._fn()(dense, lexical, alpha=0.5)
        a_hi = self._fn()(dense, lexical, alpha=0.9)
        assert a_low[1] < a_mid[1] < a_hi[1]

    def test_missing_ids_treated_as_zero(self):
        out = self._fn()({1: 0.8}, {2: 0.6}, alpha=0.5)
        # 1 only in dense → 0.5 * 0.8 = 0.4
        assert out[1] == pytest.approx(0.4)
        # 2 only in lexical → 0.5 * 0.6 = 0.3
        assert out[2] == pytest.approx(0.3)

    def test_empty_inputs(self):
        assert self._fn()({}, {}, alpha=0.5) == {}

    def test_alpha_out_of_range_clamped_or_raises(self):
        """alpha outside [0, 1] is invalid input — implementation must raise."""
        with pytest.raises((ValueError, AssertionError)):
            self._fn()({1: 0.5}, {1: 0.5}, alpha=1.5)
        with pytest.raises((ValueError, AssertionError)):
            self._fn()({1: 0.5}, {1: 0.5}, alpha=-0.5)

    def test_does_not_mutate_inputs(self):
        d = {1: 0.5, 2: 0.7}
        x = {1: 0.6, 3: 0.4}
        _ = self._fn()(d, x, alpha=0.5)
        assert d == {1: 0.5, 2: 0.7}
        assert x == {1: 0.6, 3: 0.4}
