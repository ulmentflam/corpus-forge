"""Phase R2 — score-list normalisation helper.

Backend score scales differ (see Phase R1 close-out):

- SQLite returns ``score = relevance / (1 + relevance)`` ∈ ``[0, 1)``
  for lexical (FTS5 BM25) and ``1 - distance`` for dense (sqlite-vec cosine
  distance, where ``distance ∈ [0, 2]``).
- Postgres returns ``ts_rank_cd`` clipped to ``[0, 1]`` for lexical and
  ``1 - cosine_distance`` for dense.

For alpha-weighted fusion to be meaningful both lists must be on the same
scale.  RRF is rank-based and sidesteps the issue entirely, but the alpha
path normalises each list independently before combining.

The chosen strategy is **per-list min-max** — robust to absolute scale,
preserves intra-list ordering, and degenerates gracefully on the typical
edge cases (all-equal, single element, empty, NaN).
"""

from __future__ import annotations

import math
from collections.abc import Iterable


def min_max(scores: Iterable[float]) -> list[float]:
    """Return a new list with `scores` rescaled to ``[0, 1]`` via min-max.

    Behaviour:
    - Empty input → empty output.
    - Single element → ``[0.0]`` (no useful spread).
    - All-equal input → all-zeros (avoids division-by-zero; deterministic).
    - NaN inputs are coerced to the minimum (they sink to 0.0 in the output).
    - Negative inputs are accepted; the result is shifted so the minimum is 0.
    - Input is never mutated; a new list is always returned.

    The function is pure and does not import numpy — the lists touched here
    are small (top-k of two backend calls, typically ≤200 items).
    """
    # Materialise the iterable so we can scan twice without exhausting it.
    raw: list[float] = [float(x) for x in scores]
    if not raw:
        return []

    # Replace NaN with the minimum-finite value so they sink to 0 below.
    finite: list[float] = [x for x in raw if not math.isnan(x)]
    if not finite:
        return [0.0 for _ in raw]
    lo = min(finite)
    hi = max(finite)

    if len(raw) == 1:
        return [0.0]

    spread = hi - lo
    if spread == 0.0:
        return [0.0 for _ in raw]

    out: list[float] = []
    for x in raw:
        # NaN sinks to lo (i.e. 0.0 after the shift).
        effective = lo if math.isnan(x) else x
        out.append((effective - lo) / spread)
    return out
