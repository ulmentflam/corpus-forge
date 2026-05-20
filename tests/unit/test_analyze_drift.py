"""Phase O Wave 2 (O2-T3) — Unit tests for corpus_forge.analyze.drift.

Pins the public shape of:
  - ``compare_distributions(chunks_a, chunks_b, *, methods=None) -> dict``
  - ``ks_token_length(a, b) -> tuple[float, float]``
  - ``js_embedding_centroid(a, b) -> float``

Contract source: task O2-T3 brief (overrides the phase-doc O2 RED spec on
function signatures; the task brief is the authoritative contract).

RED state: ``from corpus_forge.analyze.drift import ...`` fails with
``ModuleNotFoundError: No module named 'corpus_forge.analyze.drift'``
because ``drift.py`` does not yet exist.

Key design decisions captured:
- ``compare_distributions`` returns a dict with at minimum:
    {
        "ks_token_length": {"statistic": float, "p_value": float} | None,
        "js_embedding_centroid": float | None,
        "n_a": int,
        "n_b": int,
    }
- ``js_embedding_centroid`` is ``None`` when either input has no ``embedding``
  keys; otherwise a float in ``[0.0, 1.0]`` (JS divergence is bounded by
  ln(2) ≈ 0.693, which is < 1.0 — the spec allows up to 1.0 as the bound).
- ``methods=["ks"]`` selects only the KS test; ``methods=["js"]`` only JS.
  When ``None`` (default) both tests run.
- Empty input on either side → stats are ``None``, n_a / n_b reflect zero.
- Importing the module does NOT trigger scipy / sklearn / numpy loading
  (lazy-import contract; verified via sys.modules snapshot).
- Hypothesis properties:
    * KS statistic ∈ [0, 1] for any two non-empty integer arrays.
    * JS divergence ∈ [0, ln(2)] for any two positive distributions.
"""

from __future__ import annotations

import math
import sys
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LN2 = math.log(2)  # JS divergence upper bound


def _chunk(
    token_count: int,
    embedding: list[float] | None = None,
    content_hash: str | None = None,
) -> dict[str, Any]:
    """Build a minimal chunk dict."""
    c: dict[str, Any] = {"token_count": token_count}
    if embedding is not None:
        c["embedding"] = embedding
    if content_hash is not None:
        c["content_hash"] = content_hash
    return c


def _uniform_embedding(dim: int = 4, value: float = 0.25) -> list[float]:
    """Return a uniform probability vector of length *dim*."""
    return [value] * dim


def _chunks_from_counts(counts: list[int]) -> list[dict[str, Any]]:
    return [_chunk(c) for c in counts]


def _chunks_with_embeddings(
    counts: list[int],
    embeddings: list[list[float]],
) -> list[dict[str, Any]]:
    return [_chunk(c, e) for c, e in zip(counts, embeddings, strict=True)]


# ---------------------------------------------------------------------------
# 1. Import smoke — module and symbols must be importable
# ---------------------------------------------------------------------------


def test_import_module_smoke() -> None:
    """The drift module is importable."""
    import corpus_forge.analyze.drift  # noqa: F401


def test_import_compare_distributions() -> None:
    """``compare_distributions`` is importable from the module."""
    from corpus_forge.analyze.drift import compare_distributions  # noqa: F401


def test_import_ks_token_length() -> None:
    """``ks_token_length`` is importable from the module."""
    from corpus_forge.analyze.drift import ks_token_length  # noqa: F401


def test_import_js_embedding_centroid() -> None:
    """``js_embedding_centroid`` is importable from the module."""
    from corpus_forge.analyze.drift import js_embedding_centroid  # noqa: F401


# ---------------------------------------------------------------------------
# 2. Lazy-import guard — scipy / sklearn / numpy must NOT load at import time
# ---------------------------------------------------------------------------


def test_lazy_import_scipy_not_loaded_at_module_import() -> None:
    """Importing corpus_forge.analyze.drift does NOT pull scipy into sys.modules.

    The project's lazy-import contract (see project memory
    ``project_phase_d_treesitter_lazy_fetch.md``) requires that heavy deps
    load inside function bodies, not at module top-level.  This keeps the
    ``corpus-forge --help`` cold-start budget unaffected.
    """
    # Evict the module from the cache so we get a fresh import trace.
    mods_to_evict = [k for k in sys.modules if "corpus_forge.analyze.drift" in k]
    for m in mods_to_evict:
        sys.modules.pop(m, None)

    scipy_was_present_before = "scipy" in sys.modules
    numpy_was_present_before = "numpy" in sys.modules
    sklearn_was_present_before = "sklearn" in sys.modules

    import corpus_forge.analyze.drift  # noqa: F401

    if not scipy_was_present_before:
        assert "scipy" not in sys.modules, (
            "corpus_forge.analyze.drift imported scipy at module level; "
            "it must be lazy-imported inside function bodies only."
        )
    if not numpy_was_present_before:
        assert "numpy" not in sys.modules, (
            "corpus_forge.analyze.drift imported numpy at module level."
        )
    if not sklearn_was_present_before:
        assert "sklearn" not in sys.modules, (
            "corpus_forge.analyze.drift imported sklearn at module level."
        )


# ---------------------------------------------------------------------------
# 3. compare_distributions — return shape and key presence
# ---------------------------------------------------------------------------


def test_compare_distributions_return_keys_present() -> None:
    """Result dict contains all four required top-level keys."""
    from corpus_forge.analyze.drift import compare_distributions

    chunks_a = _chunks_from_counts([10, 20, 30, 40, 50])
    chunks_b = _chunks_from_counts([12, 22, 32, 42, 52])
    result = compare_distributions(chunks_a, chunks_b)

    assert "ks_token_length" in result, "missing key 'ks_token_length'"
    assert "js_embedding_centroid" in result, "missing key 'js_embedding_centroid'"
    assert "n_a" in result, "missing key 'n_a'"
    assert "n_b" in result, "missing key 'n_b'"


def test_compare_distributions_n_a_n_b_correct() -> None:
    """n_a and n_b reflect the input list lengths."""
    from corpus_forge.analyze.drift import compare_distributions

    chunks_a = _chunks_from_counts([10, 20, 30])
    chunks_b = _chunks_from_counts([5, 15, 25, 35])
    result = compare_distributions(chunks_a, chunks_b)

    assert result["n_a"] == 3
    assert result["n_b"] == 4


def test_compare_distributions_ks_sub_keys() -> None:
    """When KS is computed, its value is a dict with 'statistic' and 'p_value' floats."""
    from corpus_forge.analyze.drift import compare_distributions

    chunks_a = _chunks_from_counts([10, 20, 30, 40, 50])
    chunks_b = _chunks_from_counts([11, 21, 31, 41, 51])
    result = compare_distributions(chunks_a, chunks_b)

    ks = result["ks_token_length"]
    assert ks is not None, "ks_token_length should not be None for non-empty inputs"
    assert "statistic" in ks, "ks_token_length missing 'statistic' sub-key"
    assert "p_value" in ks, "ks_token_length missing 'p_value' sub-key"
    assert isinstance(ks["statistic"], float), (
        f"ks_token_length.statistic must be float, got {type(ks['statistic']).__name__}"
    )
    assert isinstance(ks["p_value"], float), (
        f"ks_token_length.p_value must be float, got {type(ks['p_value']).__name__}"
    )


# ---------------------------------------------------------------------------
# 4. Happy path — identical inputs (KS ≈ 0, JS = 0)
# ---------------------------------------------------------------------------


def test_compare_distributions_identical_inputs_ks_near_zero() -> None:
    """Identical token-length distributions → KS statistic ≈ 0."""
    from corpus_forge.analyze.drift import compare_distributions

    counts = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
    chunks_a = _chunks_from_counts(counts)
    chunks_b = _chunks_from_counts(counts)
    result = compare_distributions(chunks_a, chunks_b)

    ks = result["ks_token_length"]
    assert ks is not None
    # KS statistic for identical samples must be 0.0
    assert ks["statistic"] == pytest.approx(0.0, abs=1e-9), (
        f"Identical inputs should yield KS statistic ≈ 0.0, got {ks['statistic']}"
    )


def test_compare_distributions_identical_embeddings_js_zero() -> None:
    """Identical embeddings → JS divergence = 0.0."""
    from corpus_forge.analyze.drift import compare_distributions

    emb = _uniform_embedding(4)
    chunks_a = _chunks_with_embeddings([10, 20, 30], [emb, emb, emb])
    chunks_b = _chunks_with_embeddings([10, 20, 30], [emb, emb, emb])
    result = compare_distributions(chunks_a, chunks_b)

    js = result["js_embedding_centroid"]
    assert js is not None, "js_embedding_centroid should not be None when embeddings present"
    assert js == pytest.approx(0.0, abs=1e-6), (
        f"Identical embeddings should yield JS divergence ≈ 0.0, got {js}"
    )


# ---------------------------------------------------------------------------
# 5. Disjoint inputs (KS = 1.0)
# ---------------------------------------------------------------------------


def test_compare_distributions_disjoint_inputs_ks_one() -> None:
    """Two non-overlapping token-length distributions → KS statistic = 1.0.

    Corpus A: all tokens in [1, 10]; Corpus B: all tokens in [10_000, 10_010].
    These distributions are completely disjoint (no shared values), so the
    empirical CDFs differ by exactly 1.0 at the gap point.
    """
    from corpus_forge.analyze.drift import compare_distributions

    chunks_a = _chunks_from_counts(list(range(1, 11)))  # 1 .. 10
    chunks_b = _chunks_from_counts(list(range(10_000, 10_010)))  # 10000 .. 10009
    result = compare_distributions(chunks_a, chunks_b)

    ks = result["ks_token_length"]
    assert ks is not None
    assert ks["statistic"] == pytest.approx(1.0, abs=1e-9), (
        f"Fully disjoint distributions should yield KS statistic = 1.0, got {ks['statistic']}"
    )


# ---------------------------------------------------------------------------
# 6. Empty input handling — no exception; stats are None; n reflects zero
# ---------------------------------------------------------------------------


def test_compare_distributions_empty_a_no_exception() -> None:
    """Empty chunks_a must not raise; n_a = 0."""
    from corpus_forge.analyze.drift import compare_distributions

    result = compare_distributions([], _chunks_from_counts([10, 20, 30]))

    assert result["n_a"] == 0
    assert result["n_b"] == 3
    # KS requires at least one sample on each side — should be None or NaN-free
    ks = result["ks_token_length"]
    assert ks is None or (ks["statistic"] is None), (
        "ks_token_length should be None when one side is empty"
    )


def test_compare_distributions_empty_b_no_exception() -> None:
    """Empty chunks_b must not raise; n_b = 0."""
    from corpus_forge.analyze.drift import compare_distributions

    result = compare_distributions(_chunks_from_counts([10, 20, 30]), [])

    assert result["n_a"] == 3
    assert result["n_b"] == 0
    ks = result["ks_token_length"]
    assert ks is None or (ks["statistic"] is None), (
        "ks_token_length should be None when one side is empty"
    )


def test_compare_distributions_both_empty_no_exception() -> None:
    """Both inputs empty — no exception; n_a == n_b == 0; stats are None."""
    from corpus_forge.analyze.drift import compare_distributions

    result = compare_distributions([], [])

    assert result["n_a"] == 0
    assert result["n_b"] == 0
    ks = result["ks_token_length"]
    assert ks is None or (ks["statistic"] is None), (
        "ks_token_length should be None for empty inputs"
    )
    assert result["js_embedding_centroid"] is None, (
        "js_embedding_centroid should be None when no embeddings are present"
    )


# ---------------------------------------------------------------------------
# 7. methods filter — ["ks"] and ["js"] select only that test
# ---------------------------------------------------------------------------


def test_compare_distributions_methods_ks_only_skips_js() -> None:
    """methods=['ks'] → js_embedding_centroid is not computed (None)."""
    from corpus_forge.analyze.drift import compare_distributions

    emb = _uniform_embedding(4)
    chunks_a = _chunks_with_embeddings([10, 20], [emb, emb])
    chunks_b = _chunks_with_embeddings([30, 40], [emb, emb])
    result = compare_distributions(chunks_a, chunks_b, methods=["ks"])

    assert "ks_token_length" in result
    assert result["js_embedding_centroid"] is None, (
        "methods=['ks'] should skip JS; expected js_embedding_centroid=None"
    )


def test_compare_distributions_methods_js_only_skips_ks() -> None:
    """methods=['js'] → ks_token_length is not computed (None)."""
    from corpus_forge.analyze.drift import compare_distributions

    emb = _uniform_embedding(4)
    chunks_a = _chunks_with_embeddings([10, 20], [emb, emb])
    chunks_b = _chunks_with_embeddings([30, 40], [emb, emb])
    result = compare_distributions(chunks_a, chunks_b, methods=["js"])

    assert result["ks_token_length"] is None, (
        "methods=['js'] should skip KS; expected ks_token_length=None"
    )
    assert "js_embedding_centroid" in result


def test_compare_distributions_methods_default_runs_both() -> None:
    """methods=None (default) → both KS and JS are computed when data allows."""
    from corpus_forge.analyze.drift import compare_distributions

    emb_a = [0.5, 0.3, 0.2]
    emb_b = [0.1, 0.6, 0.3]
    chunks_a = _chunks_with_embeddings([10, 20], [emb_a, emb_a])
    chunks_b = _chunks_with_embeddings([100, 200], [emb_b, emb_b])
    result = compare_distributions(chunks_a, chunks_b)

    assert result["ks_token_length"] is not None, "default methods should run KS"
    assert result["js_embedding_centroid"] is not None, "default methods should run JS"


# ---------------------------------------------------------------------------
# 8. No-embedding fallback — js_embedding_centroid is None
# ---------------------------------------------------------------------------


def test_compare_distributions_no_embedding_keys_js_is_none() -> None:
    """js_embedding_centroid is None when chunks lack 'embedding' keys."""
    from corpus_forge.analyze.drift import compare_distributions

    chunks_a = _chunks_from_counts([10, 20, 30])
    chunks_b = _chunks_from_counts([40, 50, 60])
    result = compare_distributions(chunks_a, chunks_b)

    assert result["js_embedding_centroid"] is None, (
        "js_embedding_centroid must be None when neither input has 'embedding' keys"
    )


def test_compare_distributions_one_side_missing_embeddings_js_is_none() -> None:
    """js_embedding_centroid is None when only one side has embeddings."""
    from corpus_forge.analyze.drift import compare_distributions

    emb = _uniform_embedding(4)
    chunks_a = _chunks_with_embeddings([10, 20], [emb, emb])
    chunks_b = _chunks_from_counts([30, 40])  # no embeddings
    result = compare_distributions(chunks_a, chunks_b)

    assert result["js_embedding_centroid"] is None, (
        "js_embedding_centroid must be None when one side lacks 'embedding' keys"
    )


# ---------------------------------------------------------------------------
# 9. js_embedding_centroid is bounded [0.0, 1.0]
# ---------------------------------------------------------------------------


def test_js_embedding_centroid_bounded() -> None:
    """js_embedding_centroid result is in [0.0, 1.0] for non-identical inputs."""
    from corpus_forge.analyze.drift import compare_distributions

    emb_a = [0.7, 0.2, 0.1]
    emb_b = [0.1, 0.1, 0.8]
    chunks_a = _chunks_with_embeddings([10, 20, 30], [emb_a, emb_a, emb_a])
    chunks_b = _chunks_with_embeddings([40, 50, 60], [emb_b, emb_b, emb_b])
    result = compare_distributions(chunks_a, chunks_b)

    js = result["js_embedding_centroid"]
    assert js is not None
    assert 0.0 <= js <= 1.0, f"JS divergence must be in [0, 1.0], got {js}"


# ---------------------------------------------------------------------------
# 10. ks_token_length — standalone function
# ---------------------------------------------------------------------------


def test_ks_token_length_identical_arrays_statistic_zero() -> None:
    """ks_token_length on identical arrays returns statistic ≈ 0.0."""
    from corpus_forge.analyze.drift import ks_token_length

    arr = [10, 20, 30, 40, 50]
    stat, p_value = ks_token_length(arr, arr)

    assert stat == pytest.approx(0.0, abs=1e-9), (
        f"Identical arrays: statistic should be 0.0, got {stat}"
    )
    assert isinstance(p_value, float), f"p_value must be float, got {type(p_value).__name__}"


def test_ks_token_length_disjoint_arrays_statistic_one() -> None:
    """ks_token_length on disjoint arrays returns statistic = 1.0."""
    from corpus_forge.analyze.drift import ks_token_length

    a = list(range(1, 11))  # 1 .. 10
    b = list(range(10_000, 10_010))  # 10000 .. 10009
    stat, _p_value = ks_token_length(a, b)

    assert stat == pytest.approx(1.0, abs=1e-9), (
        f"Disjoint arrays: statistic should be 1.0, got {stat}"
    )


def test_ks_token_length_returns_tuple_of_two_floats() -> None:
    """ks_token_length return type is tuple[float, float]."""
    from corpus_forge.analyze.drift import ks_token_length

    result = ks_token_length([1, 2, 3], [4, 5, 6])

    assert isinstance(result, tuple), f"Expected tuple, got {type(result).__name__}"
    assert len(result) == 2, f"Expected tuple of length 2, got {len(result)}"
    assert isinstance(result[0], float), f"statistic must be float, got {type(result[0]).__name__}"
    assert isinstance(result[1], float), f"p_value must be float, got {type(result[1]).__name__}"


# ---------------------------------------------------------------------------
# 11. js_embedding_centroid — standalone function
# ---------------------------------------------------------------------------


def test_js_embedding_centroid_identical_distributions_zero() -> None:
    """js_embedding_centroid returns 0.0 for identical positive distributions."""
    from corpus_forge.analyze.drift import js_embedding_centroid

    dist = [[0.5, 0.3, 0.2], [0.5, 0.3, 0.2], [0.5, 0.3, 0.2]]
    result = js_embedding_centroid(dist, dist)

    assert result == pytest.approx(0.0, abs=1e-6), (
        f"Identical distributions: JS divergence should be 0.0, got {result}"
    )


def test_js_embedding_centroid_returns_float() -> None:
    """js_embedding_centroid returns a float."""
    from corpus_forge.analyze.drift import js_embedding_centroid

    a = [[0.6, 0.4], [0.7, 0.3]]
    b = [[0.2, 0.8], [0.3, 0.7]]
    result = js_embedding_centroid(a, b)

    assert isinstance(result, float), f"Expected float, got {type(result).__name__}"


def test_js_embedding_centroid_bounded_above_ln2() -> None:
    """js_embedding_centroid result does not exceed ln(2) for 2-element distributions."""
    from corpus_forge.analyze.drift import js_embedding_centroid

    # Most-divergent possible pair: one-hot distributions pointing in opposite directions
    a = [[1.0, 0.0]]
    b = [[0.0, 1.0]]
    result = js_embedding_centroid(a, b)

    assert 0.0 <= result <= _LN2 + 1e-9, (
        f"JS divergence must be in [0, ln(2)≈{_LN2:.4f}], got {result}"
    )


# ---------------------------------------------------------------------------
# 12. Hypothesis property-based tests
# ---------------------------------------------------------------------------


@given(
    a=st.lists(
        st.integers(min_value=1, max_value=100_000),
        min_size=1,
        max_size=100,
    ),
    b=st.lists(
        st.integers(min_value=1, max_value=100_000),
        min_size=1,
        max_size=100,
    ),
)
@settings(max_examples=150)
def test_property_ks_statistic_in_zero_one(a: list[int], b: list[int]) -> None:
    """Property: KS statistic ∈ [0, 1] for any two non-empty integer arrays."""
    from corpus_forge.analyze.drift import ks_token_length

    stat, p_value = ks_token_length(a, b)

    assert 0.0 <= stat <= 1.0, (
        f"KS statistic {stat} is outside [0, 1] for a={a[:5]!r}... b={b[:5]!r}..."
    )
    assert 0.0 <= p_value <= 1.0, (
        f"KS p-value {p_value} is outside [0, 1] for a={a[:5]!r}... b={b[:5]!r}..."
    )


@given(
    # Generate two lists of positive floats that form valid probability distributions
    # after softmax normalization (which the implementation applies internally).
    raw_a=st.lists(
        st.floats(min_value=0.001, max_value=10.0, allow_nan=False, allow_infinity=False),
        min_size=2,
        max_size=20,
    ),
    raw_b=st.lists(
        st.floats(min_value=0.001, max_value=10.0, allow_nan=False, allow_infinity=False),
        min_size=2,
        max_size=20,
    ),
)
@settings(max_examples=100)
def test_property_js_divergence_in_zero_ln2(
    raw_a: list[float],
    raw_b: list[float],
) -> None:
    """Property: JS divergence ∈ [0, ln(2)] for any two positive distributions.

    The distributions must have the same dimension after centroid computation.
    We pad the shorter one to match the longer dimension.
    """
    from corpus_forge.analyze.drift import js_embedding_centroid

    # Pad to equal length so centroid comparison is defined
    dim = max(len(raw_a), len(raw_b))
    a = [raw_a[i % len(raw_a)] for i in range(dim)]
    b = [raw_b[i % len(raw_b)] for i in range(dim)]

    result = js_embedding_centroid([a], [b])

    assert 0.0 <= result <= _LN2 + 1e-9, (
        f"JS divergence {result} is outside [0, ln(2)≈{_LN2:.4f}]; a={a[:4]!r}... b={b[:4]!r}..."
    )
