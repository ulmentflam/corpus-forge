"""Eval runner — Phase R3.

Public surface:

- ``evaluate_retriever(retriever, gold_path, k_values, *, max_queries=None)
  -> RetrievalMetrics``
- ``report(metrics: RetrievalMetrics) -> str``
- ``dump_json(metrics: RetrievalMetrics, out: Path) -> None``

Algorithm (``evaluate_retriever``):

1. Load the gold set via ``load_gold(gold_path)``.
2. For each ``GoldQuery``:
   a. Call ``retriever.search(q.query, SearchOptions(k=max(k_values)))``.
   b. Extract the ranked ``chunk_id`` list from the Hits.
   c. For each ``k`` in ``k_values``, compute NDCG / MRR / Recall against
      ``q.relevant_chunk_ids`` (with optional ``q.graded`` for NDCG).
3. Average per-metric per-k across all queries.

The retriever's ``SearchOptions`` carries the fusion/alpha/rerank knobs;
the CLI wires those from `Config.retrieval`.  The runner stays neutral
on fusion strategy so it can score any ``Retriever``.

Drift fallback (R3-05): when ``GoldQuery.content_hashes`` is set and a
``chunk_id`` is missing from the corpus, the runner falls back to a
content-hash lookup before scoring.  R3-04 implements the bare runner;
the drift fallback is layered in R3-05.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from corpus_forge.eval.dataset import GoldQuery, load_gold
from corpus_forge.eval.metrics import mrr_at_k, ndcg_at_k, recall_at_k
from corpus_forge.retrieval.types import RetrievalMetrics, SearchOptions


def evaluate_retriever(
    retriever,
    gold_path: Path | str,
    k_values: Sequence[int],
    *,
    max_queries: int | None = None,
) -> RetrievalMetrics:
    """Evaluate ``retriever`` against the gold set at ``gold_path``.

    Args:
        retriever: object exposing ``search(query: str, options) -> list[Hit]``.
        gold_path: path to a JSONL gold set parseable by ``load_gold``.
        k_values: list of cutoffs (e.g. ``[5, 10, 20]``).
        max_queries: if set, evaluate at most this many queries (useful
            for smoke-test cycles on huge gold sets).

    Returns:
        ``RetrievalMetrics`` whose ``ndcg`` / ``mrr`` / ``recall`` dicts
        are keyed by ``k`` and averaged over the evaluated queries.

    Raises:
        ValueError: empty gold set OR empty ``k_values``.
    """
    if not k_values:
        raise ValueError("k_values must be non-empty")

    queries = load_gold(gold_path)
    if not queries:
        raise ValueError(f"gold set at {gold_path} is empty (no queries)")

    if max_queries is not None and max_queries > 0:
        queries = queries[:max_queries]

    return _evaluate_queries(retriever, queries, k_values)


def _evaluate_queries(
    retriever,
    queries: Sequence[GoldQuery],
    k_values: Sequence[int],
) -> RetrievalMetrics:
    """Core averaging loop, factored out so the CLI can compose around it."""
    top_k = max(k_values)
    sums_ndcg: dict[int, float] = dict.fromkeys(k_values, 0.0)
    sums_mrr: dict[int, float] = dict.fromkeys(k_values, 0.0)
    sums_recall: dict[int, float] = dict.fromkeys(k_values, 0.0)

    for q in queries:
        hits = retriever.search(q.query, SearchOptions(k=top_k))
        ranked_ids = _resolve_ranking(hits)
        relevant = set(q.relevant_chunk_ids)

        for k in k_values:
            sums_ndcg[k] += ndcg_at_k(ranked_ids, relevant, k, graded=q.graded)
            sums_mrr[k] += mrr_at_k(ranked_ids, relevant, k)
            sums_recall[k] += recall_at_k(ranked_ids, relevant, k)

    n = float(len(queries))
    return RetrievalMetrics(
        ndcg={k: sums_ndcg[k] / n for k in k_values},
        mrr={k: sums_mrr[k] / n for k in k_values},
        recall={k: sums_recall[k] / n for k in k_values},
    )


def _resolve_ranking(hits) -> list[int]:
    """Extract the ``chunk_id`` list in retriever order."""
    return [h.chunk_id for h in hits]


# ── reporting ─────────────────────────────────────────────────────────────


def report(metrics: RetrievalMetrics) -> str:
    """Return a human-readable table summarising ``metrics``.

    Format:

        k    | ndcg  | mrr   | recall
        -----|-------|-------|-------
        10   | 0.812 | 0.711 | 0.640
        20   | 0.901 | 0.745 | 0.812
    """
    ks = sorted(set(metrics.ndcg.keys()) | set(metrics.mrr.keys()) | set(metrics.recall.keys()))
    lines = [
        "k    | ndcg  | mrr   | recall",
        "-----|-------|-------|-------",
    ]
    for k in ks:
        lines.append(
            f"{k:<4} | {metrics.ndcg.get(k, 0.0):.3f} | "
            f"{metrics.mrr.get(k, 0.0):.3f} | "
            f"{metrics.recall.get(k, 0.0):.3f}"
        )
    return "\n".join(lines)


def dump_json(metrics: RetrievalMetrics, out: Path | str) -> None:
    """Write ``metrics`` to ``out`` as JSON.

    JSON forces string keys; consumers parse them back to ``int`` if
    needed.  Schema::

        {
          "ndcg":   {"10": 0.812, "20": 0.901},
          "mrr":    {"10": 0.711, "20": 0.745},
          "recall": {"10": 0.640, "20": 0.812}
        }
    """
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ndcg": {str(k): float(v) for k, v in metrics.ndcg.items()},
        "mrr": {str(k): float(v) for k, v in metrics.mrr.items()},
        "recall": {str(k): float(v) for k, v in metrics.recall.items()},
    }
    p.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
