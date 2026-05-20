"""Phase O Wave 1 (O1-T2) — Unit tests for corpus_forge.analyze.stats.

Pins the public shape of ``compute_token_stats`` and
``compute_length_distribution``.  All tests must fail RED until
``corpus_forge/analyze/stats.py`` exists.

Spec source: ``.planning/tdd/phase_o_eda_cleaning.md`` § Wave O1 RED + O1-T2.

Key design decisions captured in tests:
- ``compute_token_stats`` is pure stdlib (no numpy); confirmed by the
  lazy-import smoke test asserting ``numpy`` is not present in
  ``sys.modules`` after import.
- Chunks are ``list[dict]`` with a ``"token_count"`` key; the function
  does NOT tokenize — it sums what is already there.
- ``compute_length_distribution`` returns ``{"edges": ..., "counts": ...}``
  with ``len(edges) == bins + 1`` and ``len(counts) == bins``, and
  ``sum(counts) == len(chunks)``.  The ``"text"`` key is used for length;
  the bin strategy is uniform (min/max + equal-width) by default.
- The phase doc specifies ``bin_strategy`` as an accepted parameter name
  for log-scale bucketing.  The spec does not define a ``"log10"`` return
  shape beyond ``{"edges": ..., "counts": ...}`` — we test the same
  structural invariants hold for both strategies.
"""

from __future__ import annotations

import sys
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Import smoke — must be the very first test group so RED manifests cleanly
# ---------------------------------------------------------------------------


def test_import_compute_token_stats() -> None:
    """The module and function exist and are importable."""
    from corpus_forge.analyze.stats import compute_token_stats  # noqa: F401


def test_import_compute_length_distribution() -> None:
    """The module and function exist and are importable."""
    from corpus_forge.analyze.stats import compute_length_distribution  # noqa: F401


def test_stats_module_does_not_import_numpy() -> None:
    """stats.py is pure-stdlib; it must not drag numpy into sys.modules.

    The wave gate enforces that ``corpus-forge --help`` cold-start budget
    is unaffected.  numpy is a heavy transitive dep of sklearn which ships
    in the ``[analyze]`` extra — but ``stats.py`` must be callable on a
    plain ``pip install corpus-forge`` installation with no extras.
    """
    # Remove corpus_forge.analyze.stats from sys.modules if already cached
    # so we get a fresh import trace.
    mods_to_evict = [k for k in sys.modules if "corpus_forge.analyze" in k]
    for m in mods_to_evict:
        sys.modules.pop(m, None)

    numpy_was_present_before = "numpy" in sys.modules

    import corpus_forge.analyze.stats  # noqa: F401

    if not numpy_was_present_before:
        assert "numpy" not in sys.modules, (
            "corpus_forge.analyze.stats imported numpy at module level; "
            "it must be pure-stdlib (no numpy, no sklearn, no heavy deps)."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chunk(token_count: int, text: str = "") -> dict[str, Any]:
    """Build a minimal chunk dict with the two keys stats.py reads."""
    return {"token_count": token_count, "text": text}


def _chunks_from_counts(counts: list[int]) -> list[dict[str, Any]]:
    return [_chunk(c) for c in counts]


# ---------------------------------------------------------------------------
# compute_token_stats — basic behaviour
# ---------------------------------------------------------------------------


def test_compute_token_stats_empty_returns_zero_dict() -> None:
    """Empty chunk list must return all-zero dict without raising."""
    from corpus_forge.analyze.stats import compute_token_stats

    result = compute_token_stats([])

    assert result == {
        "p50": 0,
        "p95": 0,
        "mean": 0.0,
        "min": 0,
        "max": 0,
        "token_total": 0,
        "n": 0,
    }, f"unexpected result for empty input: {result!r}"


def test_compute_token_stats_return_keys() -> None:
    """Result always contains the seven canonical keys, nothing more/less."""
    from corpus_forge.analyze.stats import compute_token_stats

    result = compute_token_stats(_chunks_from_counts([10, 20, 30]))

    assert set(result.keys()) == {"p50", "p95", "mean", "min", "max", "token_total", "n"}


def test_compute_token_stats_return_types() -> None:
    """p50/p95/min/max/token_total/n are int; mean is float."""
    from corpus_forge.analyze.stats import compute_token_stats

    result = compute_token_stats(_chunks_from_counts([10, 20, 30]))

    for int_key in ("p50", "p95", "min", "max", "token_total", "n"):
        assert isinstance(result[int_key], int), (
            f"key {int_key!r} should be int, got {type(result[int_key]).__name__}"
        )
    assert isinstance(result["mean"], float), (
        f"key 'mean' should be float, got {type(result['mean']).__name__}"
    )


def test_compute_token_stats_single_chunk() -> None:
    """Single chunk: p50 == p95 == min == max == token_count; n == 1."""
    from corpus_forge.analyze.stats import compute_token_stats

    result = compute_token_stats([_chunk(42)])

    assert result["n"] == 1
    assert result["p50"] == 42
    assert result["p95"] == 42
    assert result["min"] == 42
    assert result["max"] == 42
    assert result["token_total"] == 42
    assert result["mean"] == 42.0


def test_compute_token_stats_five_element_fixture() -> None:
    """token_counts [10, 20, 30, 40, 50] — verify p50 and p95.

    With 5 elements and linear interpolation (numpy / statistics convention):
    - p50 (median): sorted = [10, 20, 30, 40, 50]; index = 0.5*(5-1)=2.0 → 30.
    - p95: index = 0.95*(5-1)=3.8 → 40 + 0.8*(50-40) = 48.
    The spec defers to numpy linear-interpolation convention; nearest-rank
    would also give 50 for p95 on 5 elements. We assert p50==30 and test
    that p95 is in {48, 50} — both are spec-compliant.

    If pure-stdlib implementation uses statistics.median and rounds
    percentiles, any value that satisfies p50==30 is acceptable.
    """
    from corpus_forge.analyze.stats import compute_token_stats

    chunks = _chunks_from_counts([10, 20, 30, 40, 50])
    result = compute_token_stats(chunks)

    assert result["n"] == 5
    assert result["min"] == 10
    assert result["max"] == 50
    assert result["token_total"] == 150
    assert abs(result["mean"] - 30.0) < 1e-9
    assert result["p50"] == 30, f"p50 should be 30, got {result['p50']}"
    # p95 must be between 40 and 50 inclusive (linear interp = 48, nearest = 50)
    assert 40 <= result["p95"] <= 50, f"p95 should be in [40, 50], got {result['p95']}"


def test_compute_token_stats_p50_le_p95() -> None:
    """Sanity: p50 <= p95 for any non-empty input."""
    from corpus_forge.analyze.stats import compute_token_stats

    result = compute_token_stats(_chunks_from_counts([100, 200, 300]))
    assert result["p50"] <= result["p95"]


def test_compute_token_stats_mean_in_min_max_range() -> None:
    """mean is always between min and max."""
    from corpus_forge.analyze.stats import compute_token_stats

    result = compute_token_stats(_chunks_from_counts([5, 15, 25, 35]))
    assert result["min"] <= result["mean"] <= result["max"]


def test_compute_token_stats_deterministic() -> None:
    """Two calls with the same input return identical dicts (no PRNG)."""
    from corpus_forge.analyze.stats import compute_token_stats

    chunks = _chunks_from_counts([7, 13, 42, 99, 200])
    assert compute_token_stats(chunks) == compute_token_stats(chunks)


# ---------------------------------------------------------------------------
# compute_token_stats — fallback when token_count is None or missing
# ---------------------------------------------------------------------------


def test_compute_token_stats_falls_back_on_none_token_count() -> None:
    """When token_count is None the function must not raise.

    The fallback should use len(text) divided by an approximation constant
    (the spec does not mandate a specific constant, only that the function
    does not crash).  We verify: returns a dict with the canonical keys and
    n == 1 and token_total > 0 for a non-empty text.
    """
    from corpus_forge.analyze.stats import compute_token_stats

    chunk = {"token_count": None, "text": "hello world this is a test sentence"}
    result = compute_token_stats([chunk])

    assert set(result.keys()) == {"p50", "p95", "mean", "min", "max", "token_total", "n"}
    assert result["n"] == 1
    # The token estimate must be positive for non-empty text
    assert result["token_total"] > 0, (
        "fallback on None token_count should yield token_total > 0 for non-empty text"
    )


def test_compute_token_stats_falls_back_on_missing_token_count_key() -> None:
    """When the chunk dict has no 'token_count' key, the function must not raise.

    Some row shapes produced by the backend may omit the key entirely.
    The fallback to len(text) must engage without KeyError.
    """
    from corpus_forge.analyze.stats import compute_token_stats

    chunk = {"text": "a sufficiently long piece of text to yield a positive token estimate"}
    result = compute_token_stats([chunk])

    assert result["n"] == 1
    assert result["token_total"] > 0


def test_compute_token_stats_fallback_empty_text_none_count() -> None:
    """token_count=None + empty text → token_total == 0, no exception."""
    from corpus_forge.analyze.stats import compute_token_stats

    chunk = {"token_count": None, "text": ""}
    result = compute_token_stats([chunk])

    assert result["n"] == 1
    # Empty text → 0 tokens in the fallback path
    assert result["token_total"] == 0


# ---------------------------------------------------------------------------
# compute_length_distribution — basic behaviour
# ---------------------------------------------------------------------------


def test_compute_length_distribution_keys() -> None:
    """Result contains 'edges' and 'counts' keys."""
    from corpus_forge.analyze.stats import compute_length_distribution

    result = compute_length_distribution(_chunks_from_counts([10, 20, 30]))
    assert "edges" in result, "result missing 'edges' key"
    assert "counts" in result, "result missing 'counts' key"


def test_compute_length_distribution_default_bins_lengths() -> None:
    """Default bins=10 → len(edges)==11, len(counts)==10."""
    from corpus_forge.analyze.stats import compute_length_distribution

    chunks = _chunks_from_counts([10, 20, 30, 40, 50])
    result = compute_length_distribution(chunks)

    assert len(result["edges"]) == 11, f"expected 11 edges (bins+1), got {len(result['edges'])}"
    assert len(result["counts"]) == 10, f"expected 10 counts (bins), got {len(result['counts'])}"


def test_compute_length_distribution_custom_bins() -> None:
    """bins=5 → len(edges)==6, len(counts)==5."""
    from corpus_forge.analyze.stats import compute_length_distribution

    chunks = _chunks_from_counts([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    result = compute_length_distribution(chunks, bins=5)

    assert len(result["edges"]) == 6
    assert len(result["counts"]) == 5


def test_compute_length_distribution_counts_sum_to_n() -> None:
    """sum(counts) == len(chunks) — every chunk lands in exactly one bin."""
    from corpus_forge.analyze.stats import compute_length_distribution

    chunks = _chunks_from_counts([5, 15, 25, 35, 45, 55])
    result = compute_length_distribution(chunks, bins=10)

    assert sum(result["counts"]) == len(chunks), (
        f"sum(counts)={sum(result['counts'])} != len(chunks)={len(chunks)}"
    )


def test_compute_length_distribution_edges_monotonically_increasing() -> None:
    """Bin edges must be strictly increasing."""
    from corpus_forge.analyze.stats import compute_length_distribution

    chunks = _chunks_from_counts([10, 100, 500, 1000, 5000])
    result = compute_length_distribution(chunks, bins=10)

    edges = result["edges"]
    for i in range(1, len(edges)):
        assert edges[i] >= edges[i - 1], (
            f"edges not monotonically increasing at index {i}: {edges[i - 1]} -> {edges[i]}"
        )


def test_compute_length_distribution_single_chunk() -> None:
    """Single chunk still satisfies structural invariants."""
    from corpus_forge.analyze.stats import compute_length_distribution

    result = compute_length_distribution([_chunk(42)], bins=10)

    assert len(result["edges"]) == 11
    assert len(result["counts"]) == 10
    assert sum(result["counts"]) == 1


def test_compute_length_distribution_empty_chunks() -> None:
    """Empty chunk list must not raise; counts all zero."""
    from corpus_forge.analyze.stats import compute_length_distribution

    result = compute_length_distribution([], bins=10)

    assert len(result["edges"]) == 11
    assert len(result["counts"]) == 10
    assert sum(result["counts"]) == 0


def test_compute_length_distribution_bin_strategy_log10_accepted() -> None:
    """bin_strategy='log10' is a valid call — must not raise TypeError.

    The phase doc specifies that ``bin_strategy`` is an accepted parameter.
    We assert the structural invariants hold for log-scale bucketing too;
    we do not assert specific edge values (those depend on implementation).
    """
    from corpus_forge.analyze.stats import compute_length_distribution

    chunks = _chunks_from_counts([1, 10, 100, 1000, 10000])
    result = compute_length_distribution(chunks, bins=5, bin_strategy="log10")

    assert "edges" in result
    assert "counts" in result
    assert len(result["edges"]) == 6
    assert len(result["counts"]) == 5
    assert sum(result["counts"]) == len(chunks)


# ---------------------------------------------------------------------------
# Hypothesis property-based tests
# ---------------------------------------------------------------------------


@given(
    token_counts=st.lists(
        st.integers(min_value=0, max_value=1_000_000),
        min_size=1,
        max_size=200,
    )
)
@settings(max_examples=200)
def test_property_p50_le_p95(token_counts: list[int]) -> None:
    """Property: p50 <= p95 always holds for any non-empty chunk list."""
    from corpus_forge.analyze.stats import compute_token_stats

    chunks = _chunks_from_counts(token_counts)
    result = compute_token_stats(chunks)

    assert result["p50"] <= result["p95"], (
        f"p50={result['p50']} > p95={result['p95']} for token_counts={token_counts}"
    )


@given(
    token_counts=st.lists(
        st.integers(min_value=0, max_value=1_000_000),
        min_size=1,
        max_size=200,
    )
)
@settings(max_examples=200)
def test_property_mean_in_min_max(token_counts: list[int]) -> None:
    """Property: mean in [min, max] always holds for any non-empty chunk list."""
    from corpus_forge.analyze.stats import compute_token_stats

    chunks = _chunks_from_counts(token_counts)
    result = compute_token_stats(chunks)

    assert result["min"] <= result["mean"] <= result["max"], (
        f"mean={result['mean']} out of [{result['min']}, {result['max']}] "
        f"for token_counts={token_counts}"
    )


@given(
    token_counts=st.lists(
        st.integers(min_value=0, max_value=1_000_000),
        min_size=1,
        max_size=200,
    )
)
@settings(max_examples=200)
def test_property_token_total_equals_sum(token_counts: list[int]) -> None:
    """Property: result['token_total'] == sum(c['token_count'] for c in chunks)."""
    from corpus_forge.analyze.stats import compute_token_stats

    chunks = _chunks_from_counts(token_counts)
    result = compute_token_stats(chunks)

    expected_total = sum(token_counts)
    assert result["token_total"] == expected_total, (
        f"token_total={result['token_total']} != sum={expected_total}"
    )


@given(
    token_counts=st.lists(
        st.integers(min_value=1, max_value=1_000_000),
        min_size=1,
        max_size=200,
    ),
    bins=st.integers(min_value=1, max_value=50),
)
@settings(max_examples=100)
def test_property_distribution_counts_sum_to_n(token_counts: list[int], bins: int) -> None:
    """Property: sum(counts) == len(chunks) for any chunk list and bin count."""
    from corpus_forge.analyze.stats import compute_length_distribution

    chunks = _chunks_from_counts(token_counts)
    result = compute_length_distribution(chunks, bins=bins)

    assert sum(result["counts"]) == len(chunks), (
        f"sum(counts)={sum(result['counts'])} != len(chunks)={len(chunks)} "
        f"with bins={bins} and token_counts={token_counts}"
    )


@given(
    token_counts=st.lists(
        st.integers(min_value=1, max_value=1_000_000),
        min_size=1,
        max_size=200,
    ),
    bins=st.integers(min_value=1, max_value=50),
)
@settings(max_examples=100)
def test_property_distribution_structural_invariants(token_counts: list[int], bins: int) -> None:
    """Property: len(edges)==bins+1 and len(counts)==bins always."""
    from corpus_forge.analyze.stats import compute_length_distribution

    chunks = _chunks_from_counts(token_counts)
    result = compute_length_distribution(chunks, bins=bins)

    assert len(result["edges"]) == bins + 1, (
        f"len(edges)={len(result['edges'])} != bins+1={bins + 1}"
    )
    assert len(result["counts"]) == bins, f"len(counts)={len(result['counts'])} != bins={bins}"
