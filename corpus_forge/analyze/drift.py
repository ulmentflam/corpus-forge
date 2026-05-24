"""Distribution drift metrics for corpus chunk sets (Phase O Wave 2).

Public surface
--------------
- ``compare_distributions(chunks_a, chunks_b, *, methods=None) -> dict``
- ``ks_token_length(a, b) -> tuple[float, float]``
- ``js_embedding_centroid(a, b) -> float``

Lazy-import contract
--------------------
``scipy`` and ``numpy`` are imported *inside* function bodies, not at module
top level.  Importing this module does not pull in either package, keeping the
``corpus-forge --help`` cold-start budget unaffected.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any  # retained for numpy-array ``_softmax`` shim + heterogeneous

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_token_count(chunk: Mapping[str, object]) -> int:
    """Extract token count from a chunk, falling back to text-length estimate."""
    tc = chunk.get("token_count")
    if tc is not None:
        return int(tc)  # type: ignore[arg-type]
    text = chunk.get("text", "")
    if not text:
        return 0
    if not isinstance(text, str):
        text = str(text)
    return max(1, len(text) // 4)


def _has_embeddings(chunks: Sequence[Mapping[str, object]]) -> bool:
    """Return True if at least one chunk has an ``'embedding'`` key."""
    return any("embedding" in c for c in chunks)


def _extract_embeddings(chunks: Sequence[Mapping[str, object]]) -> list[list[float]]:
    """Return the embedding lists from all chunks that carry one."""
    return [c["embedding"] for c in chunks if "embedding" in c]  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def ks_token_length(
    a: list[Any],
    b: list[Any],
) -> tuple[float, float]:
    """Run a two-sample KS test on the token-length distributions.

    Args:
        a: Either a list of raw token-count integers *or* a list of chunk
           dicts (each carrying a ``"token_count"`` key).
        b: Same shape as *a*.

    Returns:
        ``(statistic, p_value)`` as a tuple of two floats.  Raises
        ``ValueError`` if either input is empty (caller is responsible for
        the empty-guard; ``compare_distributions`` wraps this safely).
    """
    from scipy.stats import ks_2samp  # lazy import

    # Accept both raw int lists and chunk-dict lists.
    counts_a = (
        [_get_token_count(c) for c in a] if a and isinstance(a[0], dict) else [int(v) for v in a]
    )
    counts_b = (
        [_get_token_count(c) for c in b] if b and isinstance(b[0], dict) else [int(v) for v in b]
    )

    result = ks_2samp(counts_a, counts_b)
    return float(result.statistic), float(result.pvalue)


def js_embedding_centroid(
    a: list[Any],
    b: list[Any],
) -> float:
    """Compute Jensen-Shannon divergence between the centroid embeddings of two sets.

    Softmax normalization is applied before computing the divergence so that
    raw float embeddings (not necessarily summing to 1) work correctly.

    Args:
        a: Either a list of raw embedding vectors (lists of floats) *or* a
           list of chunk dicts each carrying an ``'embedding'`` key.
        b: Same shape as *a*.

    Returns:
        JS divergence in ``[0.0, ln(2)]`` (base *e*).
    """
    import numpy as np  # lazy import
    from scipy.spatial.distance import jensenshannon  # lazy import

    # Accept both raw embedding lists and chunk-dict lists.
    vecs_a = _extract_embeddings(a) if a and isinstance(a[0], dict) else list(a)
    vecs_b = _extract_embeddings(b) if b and isinstance(b[0], dict) else list(b)

    arr_a = np.array(vecs_a, dtype=float)
    arr_b = np.array(vecs_b, dtype=float)

    # Compute mean embedding (centroid) for each side.
    centroid_a = arr_a.mean(axis=0)
    centroid_b = arr_b.mean(axis=0)

    # Apply softmax to convert raw floats to a probability distribution.
    def _softmax(v: Any) -> Any:
        e = np.exp(v - v.max())
        return e / e.sum()

    p = _softmax(centroid_a)
    q = _softmax(centroid_b)

    # jensenshannon returns sqrt of JS divergence; we want the divergence
    # itself (not its square root).  scipy.spatial.distance.jensenshannon
    # returns the *square root* by default — squaring gives the divergence.
    # Guard NaN: when p ≈ q, scipy's internal sqrt(js/2) may hit a tiny
    # negative from float roundoff and return NaN — treat as exact zero.
    import math

    js_sqrt = float(jensenshannon(p, q))
    if math.isnan(js_sqrt):
        return 0.0
    return float(js_sqrt**2)


def compare_distributions(
    chunks_a: list[dict[str, Any]],
    chunks_b: list[dict[str, Any]],
    *,
    methods: list[str] | None = None,
) -> dict[str, Any]:
    """Compare the distributions of two chunk sets.

    Args:
        chunks_a: First set of chunk dicts.
        chunks_b: Second set of chunk dicts.
        methods:  Which tests to run.  Supported values: ``"ks"`` (KS test on
                  token lengths), ``"js"`` (Jensen-Shannon divergence on
                  embedding centroids).  Defaults to ``["ks", "js"]``.

    Returns:
        ``{"ks_token_length": ..., "js_embedding_centroid": ..., "n_a": int, "n_b": int}``

        - ``ks_token_length``: ``{"statistic": float, "p_value": float}`` when
          KS was requested and both sides are non-empty, otherwise ``None``.
        - ``js_embedding_centroid``: float in ``[0, ln(2)]`` when JS was
          requested and both sides contain at least one chunk with an
          ``"embedding"`` key, otherwise ``None``.
    """
    if methods is None:
        methods = ["ks", "js"]

    n_a = len(chunks_a)
    n_b = len(chunks_b)

    # --- KS test ---
    ks_result: dict[str, float] | None = None
    if "ks" in methods and n_a > 0 and n_b > 0:
        try:
            stat, pval = ks_token_length(chunks_a, chunks_b)
            ks_result = {"statistic": stat, "p_value": pval}
        except Exception:
            ks_result = None

    # --- JS divergence ---
    js_result: float | None = None
    if "js" in methods and _has_embeddings(chunks_a) and _has_embeddings(chunks_b):
        try:
            js_result = js_embedding_centroid(
                _extract_embeddings(chunks_a),
                _extract_embeddings(chunks_b),
            )
        except Exception:
            js_result = None

    return {
        "ks_token_length": ks_result,
        "js_embedding_centroid": js_result,
        "n_a": n_a,
        "n_b": n_b,
    }
