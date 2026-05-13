"""Pure-NumPy retrieval metrics — Phase R3.

Three functions, each accepting a list of retriever-ranked chunk ids and a
set/list of ground-truth relevant ids:

- ``ndcg_at_k(ranked_ids, relevant_ids, k, *, graded=None) -> float``
- ``mrr_at_k(ranked_ids, relevant_ids, k) -> float``
- ``recall_at_k(ranked_ids, relevant_ids, k) -> float``

Conventions
-----------

- All three return a Python ``float`` in ``[0.0, 1.0]``.
- Edge cases (empty ranking, empty relevant, ``k == 0``) → ``0.0``.
- ``k > len(ranked_ids)`` is tolerated: we score what we have.

NDCG details
------------

Gain function: ``gain(grade) = 2**grade - 1`` (industry-standard).  For
binary relevance (``graded=None``) this collapses to ``1`` for hits and
``0`` for misses.

Discount: ``1 / log2(rank + 1)`` where ``rank`` is 1-indexed.

IDCG uses the same gain function over the top-``k`` grades sorted in
descending order.

Graded keys
~~~~~~~~~~~

``graded`` may be keyed by ``str`` (typical for JSON-decoded dicts) OR
``int``.  We normalise to ``int`` internally so the dataset loader and any
programmatic caller both work.  A chunk_id present in ``relevant_ids`` but
absent from ``graded`` is treated as grade 1 (binary fallback) — this is
the loader-side ergonomic choice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

import numpy as np

# Graded relevance map: chunk_id → grade.  Keys may be str (JSON-decoded)
# OR int (programmatic).  We type as ``Mapping[Any, int]`` to side-step
# Python's invariant generics on Mapping key types — the value normaliser
# handles the coercion safely.
GradedMap = Mapping[Any, int]


# ── helpers ────────────────────────────────────────────────────────────────


def _normalise_relevant(relevant_ids: Iterable[int] | set[int]) -> set[int]:
    """Coerce ``relevant_ids`` to a ``set[int]`` regardless of input shape."""
    if isinstance(relevant_ids, set):
        return {int(x) for x in relevant_ids}
    return {int(x) for x in relevant_ids}


def _normalise_graded(graded: GradedMap | None) -> dict[int, int]:
    """Coerce ``graded`` keys to ``int``; return ``{}`` for ``None``."""
    if graded is None:
        return {}
    return {int(k): int(v) for k, v in graded.items()}


def _gain(grade: int) -> float:
    """Industry-standard NDCG gain: ``2**grade - 1``."""
    return float((2**grade) - 1)


# ── NDCG ──────────────────────────────────────────────────────────────────


def ndcg_at_k(
    ranked_ids: list[int],
    relevant_ids: Iterable[int] | set[int],
    k: int,
    *,
    graded: GradedMap | None = None,
) -> float:
    """Normalised Discounted Cumulative Gain at rank ``k``."""
    if k <= 0 or not ranked_ids:
        return 0.0

    relevant = _normalise_relevant(relevant_ids)
    grades = _normalise_graded(graded)

    # Build the effective relevant set: items either in `relevant` (binary
    # grade=1 fallback) OR with a strictly-positive grade in `graded`.
    effective_relevant: set[int] = set(relevant)
    for cid, g in grades.items():
        if g > 0:
            effective_relevant.add(cid)

    if not effective_relevant:
        return 0.0

    def grade_of(cid: int) -> int:
        if cid in grades:
            return grades[cid]
        if cid in relevant:
            return 1
        return 0

    # Truncate ranking to top-k.
    truncated = ranked_ids[:k]

    # DCG: sum gain(grade) * 1/log2(rank+1) over the truncated ranking.
    ranks = np.arange(1, len(truncated) + 1, dtype=np.float64)
    discounts = 1.0 / np.log2(ranks + 1.0)
    gains = np.array([_gain(grade_of(cid)) for cid in truncated], dtype=np.float64)
    dcg = float(np.sum(gains * discounts))

    # IDCG: same shape but with the top-k grades in descending order from
    # the universe of relevant ids (which may be larger than k).
    universe_grades = sorted(
        (grade_of(cid) for cid in effective_relevant),
        reverse=True,
    )
    ideal_top = universe_grades[:k]
    ideal_gains = np.array([_gain(g) for g in ideal_top], dtype=np.float64)
    ideal_ranks = np.arange(1, len(ideal_top) + 1, dtype=np.float64)
    ideal_discounts = 1.0 / np.log2(ideal_ranks + 1.0)
    idcg = float(np.sum(ideal_gains * ideal_discounts))

    if idcg == 0.0:
        return 0.0
    return dcg / idcg


# ── MRR ───────────────────────────────────────────────────────────────────


def mrr_at_k(
    ranked_ids: list[int],
    relevant_ids: Iterable[int] | set[int],
    k: int,
) -> float:
    """Reciprocal rank of the first relevant hit within the top-``k``."""
    if k <= 0 or not ranked_ids:
        return 0.0
    relevant = _normalise_relevant(relevant_ids)
    if not relevant:
        return 0.0
    for rank, cid in enumerate(ranked_ids[:k], start=1):
        if cid in relevant:
            return 1.0 / float(rank)
    return 0.0


# ── Recall ────────────────────────────────────────────────────────────────


def recall_at_k(
    ranked_ids: list[int],
    relevant_ids: Iterable[int] | set[int],
    k: int,
) -> float:
    """Fraction of relevant items recovered within the top-``k``."""
    if k <= 0 or not ranked_ids:
        return 0.0
    relevant = _normalise_relevant(relevant_ids)
    if not relevant:
        return 0.0
    top = set(ranked_ids[:k])
    hits = top & relevant
    return float(len(hits)) / float(len(relevant))
