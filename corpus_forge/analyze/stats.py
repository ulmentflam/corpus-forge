"""Pure-stdlib token and length distribution statistics for corpus chunks.

No numpy, no sklearn.  Callable on a plain ``pip install corpus-forge``
install with no extras.
"""

from __future__ import annotations

import bisect
import math
import statistics
from typing import Any


def _token_count(chunk: dict[str, Any]) -> int:
    """Return the token count for a chunk, falling back to a text estimate."""
    tc = chunk.get("token_count")
    if tc is not None:
        return int(tc)
    text = chunk.get("text", "")
    if not text:
        return 0
    return max(1, len(text) // 4)


def compute_token_stats(chunks: list[dict[str, Any]]) -> dict[str, int | float]:
    """Compute summary token statistics over a list of chunk dicts.

    Args:
        chunks: Each dict should carry a ``"token_count"`` key (int).  If
            ``token_count`` is ``None`` or absent, a rough estimate
            (``max(1, len(text) // 4)``) is used instead.

    Returns:
        Dict with keys ``p50`` (int), ``p95`` (int), ``mean`` (float),
        ``min`` (int), ``max`` (int), ``token_total`` (int), ``n`` (int).
        Empty input returns all-zero dict.
    """
    if not chunks:
        return {
            "p50": 0,
            "p95": 0,
            "mean": 0.0,
            "min": 0,
            "max": 0,
            "token_total": 0,
            "n": 0,
        }

    data = [_token_count(c) for c in chunks]
    n = len(data)
    token_total = sum(data)
    sorted_data = sorted(data)

    p50 = int(statistics.median(data))

    # p95 via quantiles (inclusive method) when we have at least 2 points;
    # statistics.quantiles divides into n equal groups — we use n=20 so
    # index 18 gives the 95th percentile (18/20 = 0.90 lower bound of that
    # group under "inclusive" is what statistics uses; empirically this
    # satisfies p50 <= p95 for all inputs and the 5-element fixture test).
    # For a single element, p95 == that element.
    _QUANTILE_MIN_N = 2  # statistics.quantiles requires at least 2 data points
    if n >= _QUANTILE_MIN_N:
        # statistics.quantiles(data, n=20, method="inclusive") returns 19
        # cut points; index 18 is the 95th-percentile cut.
        p95 = int(statistics.quantiles(sorted_data, n=20, method="inclusive")[18])
    else:
        p95 = sorted_data[0]

    # Guarantee invariant p50 <= p95 (rounding can rarely invert for tiny lists)
    p95 = max(p95, p50)

    return {
        "p50": p50,
        "p95": p95,
        "mean": float(statistics.mean(data)),
        "min": sorted_data[0],
        "max": sorted_data[-1],
        "token_total": token_total,
        "n": n,
    }


def compute_length_distribution(
    chunks: list[dict[str, Any]],
    bins: int = 10,
    bin_strategy: str = "linear",
) -> dict[str, list[int]]:
    """Compute a histogram of chunk token lengths.

    Args:
        chunks:       List of chunk dicts (same shape as ``compute_token_stats``).
        bins:         Number of histogram bins.  Default 10.
        bin_strategy: ``"linear"`` (equal-width, default) or ``"log10"``
                      (logarithmically spaced).

    Returns:
        ``{"edges": list[int], "counts": list[int]}`` where
        ``len(edges) == bins + 1`` and ``len(counts) == bins`` and
        ``sum(counts) == len(chunks)``.
    """
    if not chunks:
        return {"edges": [0] * (bins + 1), "counts": [0] * bins}

    data = [_token_count(c) for c in chunks]
    lo = min(data)
    hi = max(data)

    if bin_strategy == "log10":
        log_lo = math.log10(max(lo, 1))
        log_hi = math.log10(max(hi, 1))
        if log_lo == log_hi:
            # All values identical (or both map to same log value): fall back
            # to linear edges around that point.
            edges = [lo + i for i in range(bins + 1)]
        else:
            step = (log_hi - log_lo) / bins
            edges = [round(10 ** (log_lo + i * step)) for i in range(bins + 1)]
    # Linear: equal-width bins
    elif lo == hi:
        # All values identical — make trivially increasing edges
        edges = [lo + i for i in range(bins + 1)]
    else:
        width = (hi - lo) / bins
        edges = [round(lo + i * width) for i in range(bins + 1)]

    # Ensure strict monotonicity (rounding can produce equal adjacent edges)
    for i in range(1, len(edges)):
        if edges[i] <= edges[i - 1]:
            edges[i] = edges[i - 1] + 1

    counts = [0] * bins
    for v in data:
        # bisect_right gives the bin index for v; last bin is closed on the right
        idx = bisect.bisect_right(edges, v, lo=1, hi=bins) - 1
        # Clamp to [0, bins-1]: values exactly at edges[0] land in bin 0;
        # values at or beyond edges[bins] land in the last bin.
        idx = max(0, min(bins - 1, idx))
        counts[idx] += 1

    return {"edges": edges, "counts": counts}
