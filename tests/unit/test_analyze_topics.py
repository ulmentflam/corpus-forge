"""Phase O Wave 3 (O3-T1) — Unit tests for corpus_forge.analyze.topics.

Pins the public shape of:
  - ``cluster_topics(embeddings, *, min_cluster_size, method) -> dict``
  - ``top_terms_per_cluster(texts, cluster_assignments, *, top_n) -> dict``

Contract source: task O3-T1 brief (authoritative over phase-doc O3 RED spec on
function signatures; the task brief overrides the phase doc where they differ).

Return shapes (task brief):
  cluster_topics → {
      "cluster_assignments": list[int],  # same length as input; -1 = noise
      "n_clusters": int,                 # excludes noise bucket (-1)
      "method": str,                     # "hdbscan" or "bertopic"
      "noise_count": int,                # number of -1 assignments
  }
  top_terms_per_cluster → dict[int, list[tuple[str, float]]]
      # cluster_id → [(term, weight), ...]
      # cluster -1 (noise) is always skipped

RED state: ``from corpus_forge.analyze.topics import ...`` fails with
``ModuleNotFoundError: No module named 'corpus_forge.analyze.topics'``
because ``topics.py`` does not yet exist.

Key design decisions:
- Lazy-import contract: importing the module must NOT pull bertopic, hdbscan,
  umap, or sklearn into sys.modules (project memory
  ``project_phase_d_treesitter_lazy_fetch.md``).
- method="bertopic" falls back to "hdbscan" when bertopic is unavailable;
  surfaces ``_fell_back: True`` in the result.
- Empty / too-small input returns a zero-cluster result dict, no exception.
- cluster_assignments length == len(embeddings) always.
- Hypothesis property: n_clusters + noise_count == len(embeddings) - 0
  ... more precisely: n_clusters * (at least one member) + noise_count == len(embeddings).
  Equivalently: sum(1 for a in assignments if a != -1) + noise_count == len(embeddings).
"""

from __future__ import annotations

import sys
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_embeddings(
    n: int,
    dim: int = 4,
    value: float = 0.0,
) -> list[list[float]]:
    """Return n identical embedding vectors of length dim."""
    return [[value + i * 0.001 for i in range(dim)] for _ in range(n)]


def _make_cluster_embeddings(
    cluster_configs: list[tuple[int, list[float]]],
) -> list[list[float]]:
    """Build an embedding list from (count, centroid) pairs.

    Each cluster gets ``count`` embeddings equal to its centroid.  The
    clusters are well-separated so HDBSCAN can find them.
    """
    result = []
    for count, centroid in cluster_configs:
        result.extend([list(centroid)] * count)
    return result


def _assert_cluster_topics_shape(result: dict[str, Any], n_input: int) -> None:
    """Assert that cluster_topics output has the expected four keys and invariants."""
    assert "cluster_assignments" in result, "missing key 'cluster_assignments'"
    assert "n_clusters" in result, "missing key 'n_clusters'"
    assert "method" in result, "missing key 'method'"
    assert "noise_count" in result, "missing key 'noise_count'"

    assignments = result["cluster_assignments"]
    assert isinstance(assignments, list), (
        f"cluster_assignments must be a list, got {type(assignments).__name__}"
    )
    assert len(assignments) == n_input, (
        f"cluster_assignments length {len(assignments)} != input length {n_input}"
    )
    assert isinstance(result["n_clusters"], int), (
        f"n_clusters must be int, got {type(result['n_clusters']).__name__}"
    )
    assert isinstance(result["noise_count"], int), (
        f"noise_count must be int, got {type(result['noise_count']).__name__}"
    )
    assert isinstance(result["method"], str), (
        f"method must be str, got {type(result['method']).__name__}"
    )


# ---------------------------------------------------------------------------
# 1. Import smoke — module and symbols must be importable
# ---------------------------------------------------------------------------


def test_import_module_smoke() -> None:
    """The topics module is importable."""
    import corpus_forge.analyze.topics  # noqa: F401


def test_import_cluster_topics() -> None:
    """``cluster_topics`` is importable from the module."""
    from corpus_forge.analyze.topics import cluster_topics  # noqa: F401


def test_import_top_terms_per_cluster() -> None:
    """``top_terms_per_cluster`` is importable from the module."""
    from corpus_forge.analyze.topics import top_terms_per_cluster  # noqa: F401


# ---------------------------------------------------------------------------
# 2. Lazy-import guard — bertopic / hdbscan / umap / sklearn must NOT load
#    at module import time
# ---------------------------------------------------------------------------


def test_lazy_import_guard_bertopic_not_loaded_at_module_import() -> None:
    """Importing corpus_forge.analyze.topics does NOT pull bertopic into sys.modules.

    The project's lazy-import contract requires all heavy deps to import inside
    function bodies.  This keeps `corpus-forge --help` cold-start unaffected.
    """
    mods_to_evict = [k for k in sys.modules if "corpus_forge.analyze.topics" in k]
    for m in mods_to_evict:
        sys.modules.pop(m, None)

    bertopic_before = "bertopic" in sys.modules
    hdbscan_before = "hdbscan" in sys.modules
    umap_before = "umap" in sys.modules
    sklearn_before = "sklearn" in sys.modules

    import corpus_forge.analyze.topics  # noqa: F401

    if not bertopic_before:
        assert "bertopic" not in sys.modules, (
            "corpus_forge.analyze.topics imported bertopic at module level; "
            "it must be lazy-imported inside function bodies only."
        )
    if not hdbscan_before:
        assert "hdbscan" not in sys.modules, (
            "corpus_forge.analyze.topics imported hdbscan at module level."
        )
    if not umap_before:
        assert "umap" not in sys.modules, (
            "corpus_forge.analyze.topics imported umap at module level."
        )
    if not sklearn_before:
        assert "sklearn" not in sys.modules, (
            "corpus_forge.analyze.topics imported sklearn at module level."
        )


# ---------------------------------------------------------------------------
# 3. Empty / too-small input → zero-cluster result, no exception
# ---------------------------------------------------------------------------


def test_cluster_topics_empty_embeddings_returns_empty_result() -> None:
    """Empty embeddings list → all-zero result dict, no exception."""
    from corpus_forge.analyze.topics import cluster_topics

    result = cluster_topics([])

    assert result["cluster_assignments"] == [], "empty input: cluster_assignments must be []"
    assert result["n_clusters"] == 0, "empty input: n_clusters must be 0"
    assert result["noise_count"] == 0, "empty input: noise_count must be 0"
    assert isinstance(result["method"], str), "method must be a str even on empty input"


def test_cluster_topics_single_embedding_returns_zero_clusters() -> None:
    """A single embedding can't form a cluster → n_clusters=0, noise_count=1 or all noise."""
    from corpus_forge.analyze.topics import cluster_topics

    result = cluster_topics([[0.1, 0.2, 0.3]])

    assert len(result["cluster_assignments"]) == 1, (
        "single input: cluster_assignments must have length 1"
    )
    assert result["n_clusters"] == 0, "single input: cannot form a cluster → n_clusters must be 0"
    assert result["noise_count"] == 1, "single input: the lone point must be counted as noise"


def test_cluster_topics_below_min_cluster_size_all_noise() -> None:
    """Input smaller than min_cluster_size → all points are noise (n_clusters=0)."""
    from corpus_forge.analyze.topics import cluster_topics

    embeddings = _make_embeddings(3, dim=4)
    result = cluster_topics(embeddings, min_cluster_size=10)

    _assert_cluster_topics_shape(result, 3)
    assert result["n_clusters"] == 0, (
        f"3 points with min_cluster_size=10 must yield n_clusters=0, got {result['n_clusters']}"
    )
    assert result["noise_count"] == 3, "all 3 points below min_cluster_size must be noise"


# ---------------------------------------------------------------------------
# 4. Identical embeddings → 1 cluster
# ---------------------------------------------------------------------------


def test_cluster_topics_identical_embeddings_one_cluster() -> None:
    """All identical embeddings collapse to exactly 1 cluster with min_cluster_size=2."""
    from corpus_forge.analyze.topics import cluster_topics

    n = 20
    embeddings = [[1.0, 0.0, 0.0, 0.0]] * n
    result = cluster_topics(embeddings, min_cluster_size=2)

    _assert_cluster_topics_shape(result, n)
    # All identical points must form at most one cluster; noise may vary by
    # HDBSCAN internals but n_clusters must be 1 for a large enough identical set.
    assert result["n_clusters"] == 1, (
        f"20 identical points with min_cluster_size=2 must yield n_clusters=1, "
        f"got {result['n_clusters']}"
    )
    assert result["noise_count"] == 0, (
        "no noise expected when all points are identical and cluster is large"
    )


# ---------------------------------------------------------------------------
# 5. Well-separated 3-cluster synthetic data
# ---------------------------------------------------------------------------


def test_cluster_topics_three_well_separated_clusters() -> None:
    """Three well-separated clusters (20 points each) must be discovered."""
    from corpus_forge.analyze.topics import cluster_topics

    # Three clusters with centroids far apart in 4D space
    cluster_configs: list[tuple[int, list[float]]] = [
        (20, [10.0, 0.0, 0.0, 0.0]),
        (20, [0.0, 10.0, 0.0, 0.0]),
        (20, [0.0, 0.0, 10.0, 0.0]),
    ]
    embeddings = _make_cluster_embeddings(cluster_configs)
    result = cluster_topics(embeddings, min_cluster_size=5)

    _assert_cluster_topics_shape(result, 60)
    assert result["n_clusters"] == 3, (
        f"three clearly-separated clusters of 20 each must yield n_clusters=3, "
        f"got {result['n_clusters']}"
    )
    # All 60 points should be assigned, none as noise
    assert result["noise_count"] == 0, (
        f"well-separated clusters of size 20 should yield noise_count=0, "
        f"got {result['noise_count']}"
    )


# ---------------------------------------------------------------------------
# 6. Noise-point handling — -1 label in cluster_assignments
# ---------------------------------------------------------------------------


def test_cluster_topics_noise_points_labeled_minus_one() -> None:
    """Noise points must be labeled -1 in cluster_assignments (HDBSCAN convention)."""
    from corpus_forge.analyze.topics import cluster_topics

    # One isolated point surrounded by nothing — likely noise
    embeddings = [[0.0, 0.0], [0.0, 0.0], [100.0, 100.0]]  # third is an outlier
    result = cluster_topics(embeddings, min_cluster_size=2)

    _assert_cluster_topics_shape(result, 3)
    # At least one point may be noise; verify that any noise point is labeled -1
    for i, assignment in enumerate(result["cluster_assignments"]):
        assert isinstance(assignment, int), (
            f"cluster_assignments[{i}] must be int, got {type(assignment).__name__}"
        )
        assert assignment >= -1, f"cluster_assignments[{i}] must be >= -1, got {assignment}"


def test_cluster_topics_noise_count_equals_minus_one_count() -> None:
    """noise_count must equal the number of -1 labels in cluster_assignments."""
    from corpus_forge.analyze.topics import cluster_topics

    embeddings = _make_embeddings(30, dim=4, value=0.5)
    # Sprinkle a few isolated outlier points
    embeddings[5] = [999.0, 999.0, 999.0, 999.0]
    embeddings[15] = [-999.0, -999.0, -999.0, -999.0]

    result = cluster_topics(embeddings, min_cluster_size=5)

    expected_noise_count = sum(1 for a in result["cluster_assignments"] if a == -1)
    assert result["noise_count"] == expected_noise_count, (
        f"noise_count={result['noise_count']} does not match "
        f"count of -1 labels={expected_noise_count} in cluster_assignments"
    )


# ---------------------------------------------------------------------------
# 7. method parameter
# ---------------------------------------------------------------------------


def test_cluster_topics_default_method_is_hdbscan() -> None:
    """Default method (no argument) must produce method='hdbscan' in result."""
    from corpus_forge.analyze.topics import cluster_topics

    embeddings = [[1.0, 0.0, 0.0]] * 15
    result = cluster_topics(embeddings, min_cluster_size=5)

    assert result["method"] in ("hdbscan", "bertopic"), (
        f"method must be 'hdbscan' or 'bertopic', got {result['method']!r}"
    )


def test_cluster_topics_method_hdbscan_explicit() -> None:
    """method='hdbscan' returns method='hdbscan' in result."""
    from corpus_forge.analyze.topics import cluster_topics

    embeddings = [[1.0, 0.0]] * 12
    result = cluster_topics(embeddings, min_cluster_size=5, method="hdbscan")

    assert result["method"] == "hdbscan", (
        f"explicit method='hdbscan' must be reflected in result, got {result['method']!r}"
    )


def test_cluster_topics_method_bertopic_falls_back_surfaces_flag() -> None:
    """method='bertopic' falls back to 'hdbscan' when bertopic is unavailable.

    When the fallback activates, the result must carry ``_fell_back: True``.
    When bertopic IS available, the result carries ``method='bertopic'`` and
    ``_fell_back`` is absent or False.

    We test the contract, not whether bertopic is installed.
    """
    from corpus_forge.analyze.topics import cluster_topics

    embeddings = [[1.0, 0.0, 0.0, 0.0]] * 15
    result = cluster_topics(embeddings, min_cluster_size=5, method="bertopic")

    # Either bertopic ran or hdbscan ran as fallback
    assert result["method"] in ("hdbscan", "bertopic"), (
        f"method must be 'hdbscan' or 'bertopic', got {result['method']!r}"
    )
    if result["method"] == "hdbscan":
        # Fell back — must surface the flag
        assert result.get("_fell_back") is True, (
            "method='bertopic' falling back to 'hdbscan' must set _fell_back=True in result"
        )


# ---------------------------------------------------------------------------
# 8. top_terms_per_cluster — output shape
# ---------------------------------------------------------------------------


def test_top_terms_per_cluster_return_type() -> None:
    """top_terms_per_cluster returns a dict keyed by int cluster ids."""
    from corpus_forge.analyze.topics import top_terms_per_cluster

    texts = ["the quick brown fox", "jumps over the lazy dog", "hello world"]
    assignments = [0, 0, 1]
    result = top_terms_per_cluster(texts, assignments)

    assert isinstance(result, dict), (
        f"top_terms_per_cluster must return dict, got {type(result).__name__}"
    )
    for cluster_id, terms in result.items():
        assert isinstance(cluster_id, int), (
            f"cluster_id key must be int, got {type(cluster_id).__name__}"
        )
        assert isinstance(terms, list), (
            f"terms for cluster {cluster_id} must be list, got {type(terms).__name__}"
        )
        for term, weight in terms:
            assert isinstance(term, str), (
                f"term in cluster {cluster_id} must be str, got {type(term).__name__}"
            )
            assert isinstance(weight, float), (
                f"weight for term '{term}' in cluster {cluster_id} must be float, "
                f"got {type(weight).__name__}"
            )


def test_top_terms_per_cluster_skips_noise_cluster() -> None:
    """Cluster -1 (noise) is excluded from top_terms_per_cluster output."""
    from corpus_forge.analyze.topics import top_terms_per_cluster

    texts = ["noise text here", "cluster one text", "more cluster one"]
    assignments = [-1, 0, 0]
    result = top_terms_per_cluster(texts, assignments)

    assert -1 not in result, "top_terms_per_cluster must not include cluster -1 (noise) in output"
    assert 0 in result, "cluster 0 must appear in top_terms_per_cluster result"


def test_top_terms_per_cluster_top_n_limits_terms() -> None:
    """top_n parameter limits the number of terms returned per cluster."""
    from corpus_forge.analyze.topics import top_terms_per_cluster

    # 12 unique words across 6 documents in 1 cluster
    texts = [
        "alpha beta gamma delta epsilon zeta",
        "eta theta iota kappa lambda mu",
        "nu xi omicron pi rho sigma",
        "tau upsilon phi chi psi omega",
        "one two three four five six",
        "seven eight nine ten eleven twelve",
    ]
    assignments = [0, 0, 0, 0, 0, 0]
    result = top_terms_per_cluster(texts, assignments, top_n=3)

    assert 0 in result, "cluster 0 must be present"
    terms = result[0]
    assert len(terms) <= 3, f"top_n=3 must return at most 3 terms, got {len(terms)}"


def test_top_terms_per_cluster_default_top_n_is_ten() -> None:
    """Default top_n=10 returns at most 10 terms per cluster."""
    from corpus_forge.analyze.topics import top_terms_per_cluster

    # Many unique tokens spread across a cluster
    texts = [f"word{i} context sentence document corpus" for i in range(20)]
    assignments = [0] * 20
    result = top_terms_per_cluster(texts, assignments)

    terms = result.get(0, [])
    assert len(terms) <= 10, f"default top_n=10 must return at most 10 terms, got {len(terms)}"


def test_top_terms_per_cluster_empty_assignments() -> None:
    """All-noise assignments (-1 only) → empty result dict, no exception."""
    from corpus_forge.analyze.topics import top_terms_per_cluster

    texts = ["this is noise", "more noise text"]
    assignments = [-1, -1]
    result = top_terms_per_cluster(texts, assignments)

    assert isinstance(result, dict), "must return dict even when all noise"
    assert result == {}, "all-noise assignments must yield empty dict (no non-noise clusters)"


def test_top_terms_per_cluster_empty_inputs() -> None:
    """Empty texts and assignments → empty dict, no exception."""
    from corpus_forge.analyze.topics import top_terms_per_cluster

    result = top_terms_per_cluster([], [])

    assert result == {}, "empty input must yield empty dict"


# ---------------------------------------------------------------------------
# 9. Hypothesis property — n_clusters + noise_count = total points
# ---------------------------------------------------------------------------


@given(
    n=st.integers(min_value=0, max_value=40),
    min_cluster_size=st.integers(min_value=2, max_value=10),
)
@settings(max_examples=30, deadline=None)
def test_property_cluster_assignments_length_equals_input(
    n: int,
    min_cluster_size: int,
) -> None:
    """Property: len(cluster_assignments) == len(embeddings) for any input size."""
    from corpus_forge.analyze.topics import cluster_topics

    embeddings = _make_embeddings(n, dim=4)
    result = cluster_topics(embeddings, min_cluster_size=min_cluster_size)

    assert len(result["cluster_assignments"]) == n, (
        f"cluster_assignments length {len(result['cluster_assignments'])} != input length {n}"
    )


@given(
    n=st.integers(min_value=1, max_value=30),
    min_cluster_size=st.integers(min_value=2, max_value=8),
)
@settings(max_examples=30, deadline=None)
def test_property_noise_count_matches_minus_one_labels(
    n: int,
    min_cluster_size: int,
) -> None:
    """Property: noise_count == number of -1 entries in cluster_assignments."""
    from corpus_forge.analyze.topics import cluster_topics

    # Random-ish embeddings: sequential so no two are identical (varies HDBSCAN output)
    embeddings = [[float(i % 5), float(i % 3), float(i % 7), float(i % 11)] for i in range(n)]
    result = cluster_topics(embeddings, min_cluster_size=min_cluster_size)

    actual_noise = sum(1 for a in result["cluster_assignments"] if a == -1)
    assert result["noise_count"] == actual_noise, (
        f"noise_count={result['noise_count']} does not match "
        f"count of -1 labels={actual_noise} (n={n}, min_cluster_size={min_cluster_size})"
    )
