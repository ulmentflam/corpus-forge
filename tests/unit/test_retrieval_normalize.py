"""R2-01 — `corpus_forge.retrieval.normalize` unit pins.

The normalise helper turns a list of raw scores (whose scale depends on the
producing backend — see R1 close-out notes) into a list of normalised
similarities in ``[0, 1]`` suitable for alpha-weighted fusion.

Contract:
- ``min_max(scores)`` returns a new list of the same length.
- All-equal input → all-zero output (no division-by-zero blowup).
- Single-element input → ``[0.0]`` (degenerate; nothing to normalise against).
- NaN inputs are treated as the minimum (so they sink to 0.0).
- Empty input → empty output.
- Negative inputs are accepted (sqlite-vec returned `1 - distance` which can
  go below 0 when distance > 1; R1 polish clipped this, but we still want to
  guard).
"""

from __future__ import annotations

import math

import pytest


# ── module presence ───────────────────────────────────────────────────────


def test_module_importable():
    import corpus_forge.retrieval.normalize  # noqa: F401


def test_min_max_reexported_from_package():
    """``min_max`` should be exported from the retrieval package root."""
    from corpus_forge.retrieval import min_max  # noqa: F401


# ── min-max behaviour ─────────────────────────────────────────────────────


class TestMinMax:
    def _fn(self):
        from corpus_forge.retrieval.normalize import min_max

        return min_max

    def test_typical_case_maps_to_unit_interval(self):
        out = self._fn()([1.0, 2.0, 3.0])
        assert out == [0.0, 0.5, 1.0]

    def test_returns_list_of_floats(self):
        out = self._fn()([1.0, 2.0, 3.0])
        assert isinstance(out, list)
        assert all(isinstance(x, float) for x in out)

    def test_empty_input(self):
        assert self._fn()([]) == []

    def test_single_element(self):
        out = self._fn()([0.7])
        assert out == [0.0]

    def test_all_equal_values(self):
        """All-equal → all-zero (avoids div-by-zero; keeps deterministic ranks)."""
        out = self._fn()([0.5, 0.5, 0.5])
        assert out == [0.0, 0.0, 0.0]

    def test_negative_inputs_normalised(self):
        out = self._fn()([-1.0, 0.0, 1.0])
        assert out == [0.0, 0.5, 1.0]

    def test_nan_inputs_sink_to_zero(self):
        out = self._fn()([float("nan"), 1.0, 2.0])
        # nan must NOT propagate; treat as the floor.
        assert not math.isnan(out[0])
        assert out[0] == pytest.approx(0.0)
        assert out[1] == pytest.approx(0.0)
        assert out[2] == pytest.approx(1.0)

    def test_does_not_mutate_input(self):
        src = [3.0, 1.0, 2.0]
        _ = self._fn()(src)
        assert src == [3.0, 1.0, 2.0]

    def test_preserves_order(self):
        """min_max preserves positional ordering of the input."""
        out = self._fn()([3.0, 1.0, 2.0])
        # min=1 max=3 → (3-1)/2=1.0, 0.0, 0.5
        assert out == [1.0, 0.0, 0.5]
