"""Topic clustering for corpus chunk sets (Phase O Wave 3).

Public surface
--------------
- ``cluster_topics(embeddings, *, min_cluster_size, method) -> dict``
- ``top_terms_per_cluster(texts, cluster_assignments, *, top_n) -> dict``

Lazy-import contract
--------------------
``bertopic``, ``hdbscan``, ``umap``, and ``sklearn`` are imported *inside*
function bodies, not at module top level.  Importing this module does not pull
in any of these packages, keeping the ``corpus-forge --help`` cold-start budget
unaffected.

Cross-reference: ``.planning/tdd/phase_o_eda_cleaning.md`` § Wave O3.
"""

from __future__ import annotations

from typing import Any

# Minimum number of points required to attempt clustering (HDBSCAN constraint).
_MIN_POINTS_TO_CLUSTER: int = 2


# ---------------------------------------------------------------------------
# cluster_topics
# ---------------------------------------------------------------------------


def cluster_topics(
    embeddings: list[list[float]],
    *,
    min_cluster_size: int = 10,
    method: str = "hdbscan",
) -> dict[str, Any]:
    """Cluster embedding vectors into topics using HDBSCAN (or BERTopic).

    Args:
        embeddings: List of embedding vectors (lists of floats).  All vectors
            must have the same dimensionality.
        min_cluster_size: Minimum number of points to form a cluster.
            Points in groups smaller than this are labeled as noise (-1).
            Must be >= 2 for HDBSCAN.
        method: ``"hdbscan"`` (default) or ``"bertopic"``.  When
            ``"bertopic"`` is requested but the package is not installed,
            the function falls back to ``"hdbscan"`` and adds
            ``"_fell_back": True`` to the result.

    Returns:
        Dict with exactly four keys (plus optional ``_fell_back``):

        - ``"cluster_assignments"``: list of ints, same length as
          *embeddings*; -1 means noise.
        - ``"n_clusters"``: int — number of distinct clusters, excluding
          the noise bucket (-1).
        - ``"method"``: str — the method actually used.
        - ``"noise_count"``: int — number of -1 assignments.

    Notes:
        Empty input or input smaller than *min_cluster_size* returns an
        all-noise result without raising.
    """
    n = len(embeddings)

    # --- Guard: empty input → zero-cluster result ---
    if n == 0:
        return {
            "cluster_assignments": [],
            "n_clusters": 0,
            "method": "hdbscan",
            "noise_count": 0,
        }

    # --- Guard: too-small input → all noise, no clustering ---
    if n < min_cluster_size or n < _MIN_POINTS_TO_CLUSTER:
        return {
            "cluster_assignments": [-1] * n,
            "n_clusters": 0,
            "method": "hdbscan",
            "noise_count": n,
        }

    # --- BERTopic path (falls back to HDBSCAN when unavailable) ---
    fell_back = False
    actual_method = method

    if method == "bertopic":
        try:
            import numpy as np  # lazy import
            from bertopic import BERTopic  # lazy import

            topic_model = BERTopic(min_topic_size=min_cluster_size)
            arr = np.array(embeddings, dtype=float)
            # BERTopic.fit_transform expects documents + optional embeddings.
            # We pass dummy doc strings and pre-computed embeddings.
            dummy_docs = [str(i) for i in range(n)]
            topics, _ = topic_model.fit_transform(dummy_docs, arr)
            assignments = [int(t) for t in topics]
            # BERTopic uses -1 for outliers (noise), same convention.
            n_clusters = len({t for t in assignments if t != -1})
            noise_count = sum(1 for a in assignments if a == -1)
            return {
                "cluster_assignments": assignments,
                "n_clusters": n_clusters,
                "method": "bertopic",
                "noise_count": noise_count,
            }
        except Exception:
            # BERTopic unavailable or failed — fall back to HDBSCAN.
            fell_back = True
            actual_method = "hdbscan"

    # --- HDBSCAN path ---
    import hdbscan as hdbscan_lib  # lazy import
    import numpy as np  # lazy import

    arr = np.array(embeddings, dtype=float)
    clusterer = hdbscan_lib.HDBSCAN(
        min_cluster_size=max(_MIN_POINTS_TO_CLUSTER, min_cluster_size),
        metric="euclidean",
        allow_single_cluster=True,
    )
    clusterer.fit(arr)
    assignments = [int(label) for label in clusterer.labels_]
    n_clusters = len({a for a in assignments if a != -1})
    noise_count = sum(1 for a in assignments if a == -1)

    result: dict[str, Any] = {
        "cluster_assignments": assignments,
        "n_clusters": n_clusters,
        "method": actual_method,
        "noise_count": noise_count,
    }
    if fell_back:
        result["_fell_back"] = True
    return result


# ---------------------------------------------------------------------------
# top_terms_per_cluster
# ---------------------------------------------------------------------------


def top_terms_per_cluster(
    texts: list[str],
    cluster_assignments: list[int],
    *,
    top_n: int = 10,
) -> dict[int, list[tuple[str, float]]]:
    """Compute the top terms per cluster using c-TF-IDF (class-based TF-IDF).

    Cluster -1 (noise) is always skipped.

    Args:
        texts: List of document strings, parallel to *cluster_assignments*.
        cluster_assignments: List of cluster ids (ints), one per text.
            -1 marks noise and is excluded from the output.
        top_n: Maximum number of terms to return per cluster (default 10).

    Returns:
        Dict mapping cluster id (int) to a list of ``(term, weight)``
        tuples, sorted by weight descending.  Clusters are present only
        when they have at least one non-noise member.  Returns ``{}`` on
        empty input or when all assignments are -1.
    """
    if not texts or not cluster_assignments:
        return {}

    # Group texts by cluster, skipping noise.
    cluster_texts: dict[int, list[str]] = {}
    for text, assignment in zip(texts, cluster_assignments, strict=False):
        if assignment == -1:
            continue
        cluster_texts.setdefault(assignment, []).append(text)

    if not cluster_texts:
        return {}

    # Lazy import sklearn — must NOT appear at module top level.
    from sklearn.feature_extraction.text import TfidfVectorizer  # lazy import

    # Build one TF-IDF matrix over per-cluster concatenated documents so we get
    # IDF weights from the corpus of clusters (c-TF-IDF approximation).
    cluster_ids = sorted(cluster_texts.keys())
    # Each "document" for TF-IDF is the concatenation of all texts in that cluster.
    corpus = [" ".join(cluster_texts[cid]) for cid in cluster_ids]

    vectorizer = TfidfVectorizer(max_features=None)
    try:
        tfidf_matrix = vectorizer.fit_transform(corpus)
    except ValueError:
        # Can happen when vocabulary is empty (all stop words, etc.)
        return {cid: [] for cid in cluster_ids}

    feature_names: list[str] = vectorizer.get_feature_names_out().tolist()

    result: dict[int, list[tuple[str, float]]] = {}
    for row_idx, cid in enumerate(cluster_ids):
        row = tfidf_matrix[row_idx]
        # Convert sparse row to dense array.
        dense = row.toarray().flatten()
        # Sort by weight descending, pick top_n.
        ranked_indices = dense.argsort()[::-1][:top_n]
        terms = [
            (feature_names[i], float(dense[i])) for i in ranked_indices if float(dense[i]) > 0.0
        ]
        result[cid] = terms

    return result
