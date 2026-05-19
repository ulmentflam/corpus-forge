"""Perf-tier retrieval metrics keyed by ``(file, byte_start, byte_end)``.

Phase M Wave 5 introduces this module for the semble bench, but the
helpers are deliberately retriever-agnostic so they can be reused by any
side-by-side comparison that needs to score hits across heterogenous
chunking schemes (line-keyed, byte-keyed, semantic-keyed).  This file is
**not** under ``experiments/`` — it is ungated, unit-tested production-
adjacent test infrastructure.

Public surface
--------------

- :func:`compute_metrics` — top-level entry point.  Takes a mapping
  ``query_id -> list[Hit-like]`` and a ground-truth list and returns
  ``{"mrr_at_10", "recall_at_5", "p50_latency_ms", "p95_latency_ms",
  "per_query": [...]}``.
- :func:`mrr_at_k`, :func:`recall_at_k` — primitives operating on a list
  of booleans (``is_relevant`` per rank).
- :func:`percentile` — pure-numpy-free percentile (so the helpers can be
  imported without optional ``[retrieval]`` numpy installed).
- :func:`hit_matches_ground_truth` — geometric overlap check.

Ground-truth schema
-------------------

A single ground-truth entry is a dict::

    {"file": "corpus_forge/retrieval/retriever.py",
     "byte_start": 3232,
     "byte_end": 6703}

A query has one or more such entries.  A retriever hit is "relevant" iff
its ``(file, byte span)`` **overlaps** any ground-truth entry by at
least :data:`_MIN_OVERLAP_BYTES` bytes (default 32).  Overlap is the
right primitive because semble chunks files by tree-sitter constructs
(class, function, ...) while corpus-forge chunks by FastCDC content-
defined boundaries — the two will produce different absolute spans even
when "answering the same question".

Hit representation
------------------

The helpers expect each hit to expose ``metadata["file_path"]``,
``metadata["byte_start"]``, and ``metadata["byte_end"]``  (the
:class:`SembleHit` shape from ``experiments.semble_adapter``).  For
hits that only carry line numbers (semble's native ``Chunk`` does), the
bench harness is responsible for resolving line→byte by re-reading the
file; that's a bench concern, not a metrics concern, so this module
trusts byte spans on the hit.

Latency
-------

Each hit list comes with a wall-clock duration in ms (the bench harness
times ``Retriever.search`` per query).  We compute the p50 / p95 of
those durations across queries.  Percentile is computed via a sorted-
index lookup (the nearest-rank method — see
https://en.wikipedia.org/wiki/Percentile#The_nearest-rank_method).  We
do NOT depend on numpy here so this module is import-safe in a
minimal venv.
"""

from __future__ import annotations

from typing import Any

_MIN_OVERLAP_BYTES = 32


# ── primitives ──────────────────────────────────────────────────────────


def mrr_at_k(relevant_flags: list[bool], k: int) -> float:
    """Reciprocal rank of the first relevant hit within the top-``k``.

    Args:
        relevant_flags: list of booleans, one per rank (rank 1 = index 0).
        k: cutoff.  ``k <= 0`` or empty input returns 0.0.

    Returns:
        ``1.0 / rank`` of the first ``True`` within ``relevant_flags[:k]``,
        or ``0.0`` when no relevant hit lands in the top-``k``.
    """
    if k <= 0 or not relevant_flags:
        return 0.0
    for rank, flag in enumerate(relevant_flags[:k], start=1):
        if flag:
            return 1.0 / float(rank)
    return 0.0


def recall_at_k(
    relevant_flags: list[bool], total_relevant: int, k: int
) -> float:
    """Fraction of relevant items recovered within the top-``k``.

    Args:
        relevant_flags: as in :func:`mrr_at_k`.
        total_relevant: ``len(ground_truth)`` — the denominator.  When
            ``0``, returns 0.0 (no relevant items to find).
        k: cutoff.

    Returns:
        ``hits / total_relevant`` clamped into ``[0, 1]``.  When the
        retriever returns the same ground-truth entry multiple times
        (e.g. two chunks both overlap one canonical answer) the duplicates
        each count toward the numerator — callers wanting strict per-
        ground-truth uniqueness should dedupe before passing in.
    """
    if k <= 0 or not relevant_flags or total_relevant <= 0:
        return 0.0
    hits = sum(1 for f in relevant_flags[:k] if f)
    return min(float(hits) / float(total_relevant), 1.0)


def percentile(samples: list[float], pct: float) -> float:
    """Nearest-rank percentile (no numpy dep).

    Args:
        samples: list of floats.  Empty input returns 0.0.
        pct: percentile in ``[0, 100]``.

    Returns:
        Nearest-rank percentile.  For ``pct=50`` on an even-length input
        this returns the lower median (rank-method floor), not the linear
        interpolation — fine for ms-latency stats where we want a real
        observation, not a synthetic point.
    """
    if not samples:
        return 0.0
    if pct < 0.0 or pct > 100.0:
        raise ValueError(f"pct must be in [0, 100], got {pct!r}")
    s = sorted(samples)
    # Rank is 1-based; clamp into [1, len(s)].
    rank = max(1, round((pct / 100.0) * len(s)))
    rank = min(rank, len(s))
    return float(s[rank - 1])


# ── overlap check ───────────────────────────────────────────────────────


def hit_matches_ground_truth(
    hit_file: str,
    hit_start: int,
    hit_end: int,
    truth_entries: list[dict[str, Any]],
    *,
    min_overlap_bytes: int = _MIN_OVERLAP_BYTES,
) -> bool:
    """True iff the hit overlaps any ground-truth entry by ≥ ``min_overlap_bytes``.

    Both spans are half-open ``[start, end)``.  File paths are compared
    after stripping any leading ``./`` and normalising to POSIX
    separators so that callers can mix absolute paths and repo-relative
    paths freely.
    """
    if hit_end <= hit_start:
        return False
    norm_hit = _normalise_path(hit_file)
    for t in truth_entries:
        if _normalise_path(t["file"]) != norm_hit:
            continue
        ts = int(t["byte_start"])
        te = int(t["byte_end"])
        overlap = min(hit_end, te) - max(hit_start, ts)
        if overlap >= min_overlap_bytes:
            return True
    return False


def _normalise_path(path: str) -> str:
    p = path.replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    return p


# ── top-level ───────────────────────────────────────────────────────────


def compute_metrics(
    hits_by_query: dict[str, dict[str, Any]],
    ground_truth: dict[str, list[dict[str, Any]]],
    *,
    k_mrr: int = 10,
    k_recall: int = 5,
) -> dict[str, Any]:
    """Aggregate metrics over a bench run.

    Args:
        hits_by_query: ``{query_id: {"hits": list[hit], "latency_ms": float}}``
            where each ``hit`` carries ``metadata["file_path"]``,
            ``metadata["byte_start"]``, ``metadata["byte_end"]``.  Hits
            are assumed already ranked best-first.
        ground_truth: ``{query_id: list[ground_truth_entry]}``.  Queries
            present in ``hits_by_query`` but absent here are skipped (they
            cannot be scored).  Queries present in ``ground_truth`` but
            absent in ``hits_by_query`` are treated as zero-hit lists
            (MRR=0, Recall=0).
        k_mrr: cutoff for MRR.
        k_recall: cutoff for Recall.

    Returns:
        Dict with::

            {
              "mrr_at_<k>": float,
              "recall_at_<k>": float,
              "p50_latency_ms": float,
              "p95_latency_ms": float,
              "n_queries": int,
              "per_query": [
                {
                  "query_id": str,
                  "mrr_at_<k>": float,
                  "recall_at_<k>": float,
                  "latency_ms": float,
                  "n_hits": int,
                  "n_relevant_hits": int,
                  "n_ground_truth": int,
                },
                ...
              ],
            }

        MRR / Recall keys are dynamic (e.g. ``mrr_at_10`` when
        ``k_mrr=10``) so callers can sweep cutoffs without colliding.
    """
    mrr_key = f"mrr_at_{k_mrr}"
    rec_key = f"recall_at_{k_recall}"

    per_query: list[dict[str, Any]] = []
    latencies: list[float] = []

    # Iterate over the *union* of ids so missing-hit queries still
    # appear in the report (and pull MRR/Recall toward 0 as they should).
    all_ids = sorted(set(hits_by_query.keys()) | set(ground_truth.keys()))

    for qid in all_ids:
        truth = ground_truth.get(qid, [])
        record = hits_by_query.get(qid, {"hits": [], "latency_ms": 0.0})
        hits = record.get("hits", [])
        latency = float(record.get("latency_ms", 0.0))

        flags = [_hit_is_relevant(h, truth) for h in hits]
        n_rel = sum(1 for f in flags if f)

        per_query.append(
            {
                "query_id": qid,
                mrr_key: mrr_at_k(flags, k_mrr),
                rec_key: recall_at_k(flags, len(truth), k_recall),
                "latency_ms": latency,
                "n_hits": len(hits),
                "n_relevant_hits": n_rel,
                "n_ground_truth": len(truth),
            }
        )
        latencies.append(latency)

    # Average MRR / Recall over queries that actually had ground truth
    # (queries with empty ground truth would otherwise drag the means to
    # 0 unfairly).  Queries with ground truth but no retrieved hits stay
    # in the mean — that's a genuine 0 we want to reflect.
    scored = [r for r in per_query if r["n_ground_truth"] > 0]
    n = len(scored) if scored else 1  # avoid div-by-zero
    mean_mrr = sum(r[mrr_key] for r in scored) / n if scored else 0.0
    mean_rec = sum(r[rec_key] for r in scored) / n if scored else 0.0

    return {
        mrr_key: mean_mrr,
        rec_key: mean_rec,
        "p50_latency_ms": percentile(latencies, 50.0),
        "p95_latency_ms": percentile(latencies, 95.0),
        "n_queries": len(per_query),
        "per_query": per_query,
    }


def _hit_is_relevant(hit: Any, truth: list[dict[str, Any]]) -> bool:
    meta = getattr(hit, "metadata", None) or {}
    fp = meta.get("file_path")
    bs = meta.get("byte_start")
    be = meta.get("byte_end")
    if fp is None or bs is None or be is None:
        return False
    return hit_matches_ground_truth(str(fp), int(bs), int(be), truth)
