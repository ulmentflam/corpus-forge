"""Phase R2 — list-fusion primitives for hybrid retrieval.

Two strategies:

1. ``reciprocal_rank_fusion(rankings, k_rrf=60)`` — rank-only, score-free.
   For each ranking (an ordered list of ids) every id contributes
   ``1 / (k_rrf + rank_within_ranking_starting_at_1)`` to its fused score.
   The default ``k_rrf=60`` is the value from Cormack, Clarke & Buettcher's
   original RRF paper.  Because RRF only looks at ranks it sidesteps the
   score-scale mismatch between SQLite and Postgres (see Phase R1
   close-out): no normalisation needed.

2. ``alpha_blend(dense, lexical, alpha)`` — linear score combination.
   Both inputs are ``dict[int, float]`` mapping chunk_id → score.  Scores
   MUST already be on a common scale — callers run ``normalize.min_max`` on
   each list before invoking this function.  Returns
   ``{id: alpha * dense.get(id, 0) + (1 - alpha) * lexical.get(id, 0)}``.

Both functions are pure: they neither mutate inputs nor depend on any I/O.
"""

from __future__ import annotations

from collections.abc import Sequence

_DEFAULT_K_RRF = 60


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[int]],
    k_rrf: int = _DEFAULT_K_RRF,
) -> dict[int, float]:
    """Fuse multiple rankings into a single score dict via RRF.

    Args:
        rankings: a sequence of rankings, each ranking an ordered sequence
            of chunk_ids (best first).  Lists need not be the same length
            and ids may overlap.
        k_rrf: the RRF dampening constant.  Default 60 matches the
            original paper; higher values flatten the contribution curve.

    Returns:
        ``dict[int, float]``: id → fused score.  Higher is better.  Ids
        absent from every ranking are absent from the result.

    The fused score for id ``i`` is::

        sum( 1 / (k_rrf + rank_in_list(i)) ) over lists that contain i

    where rank starts at 1 for the top of each list.
    """
    fused: dict[int, float] = {}
    for ranking in rankings:
        for rank_idx, cid in enumerate(ranking, start=1):
            contribution = 1.0 / (k_rrf + rank_idx)
            fused[cid] = fused.get(cid, 0.0) + contribution
    return fused


def alpha_blend(
    dense: dict[int, float],
    lexical: dict[int, float],
    alpha: float,
) -> dict[int, float]:
    """Linearly blend two normalised score dicts.

    Args:
        dense: id → score.  Scores assumed in a common scale with lexical
            (callers run ``min_max`` on each list first).
        lexical: id → score.  Same scale as ``dense``.
        alpha: weight on the dense list.  Must be in ``[0, 1]``.

    Returns:
        ``dict[int, float]``: id → blended score.  Higher is better.  Ids
        present in only one input contribute their score weighted by that
        side; ids absent from both never appear.

    Raises:
        ValueError: if ``alpha`` is outside ``[0, 1]``.
    """
    if not (0.0 <= alpha <= 1.0):
        raise ValueError(f"alpha must be in [0, 1], got {alpha!r}")

    out: dict[int, float] = {}
    for cid, score in dense.items():
        out[cid] = alpha * score
    for cid, score in lexical.items():
        out[cid] = out.get(cid, 0.0) + (1.0 - alpha) * score
    return out
