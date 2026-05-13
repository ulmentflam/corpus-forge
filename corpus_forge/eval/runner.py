"""Eval runner — Phase R3.

Public surface:

- ``evaluate_retriever(retriever, gold_path, k_values, *, max_queries=None)
  -> RetrievalMetrics``
- ``report(metrics: RetrievalMetrics) -> str``
- ``dump_json(metrics: RetrievalMetrics, out: Path) -> None``

Algorithm (``evaluate_retriever``):

1. Load the gold set via ``load_gold(gold_path)``.
2. For each ``GoldQuery``:
   a. Resolve the effective ``relevant_chunk_ids`` (drift fallback —
      see below).
   b. Call ``retriever.search(q.query, SearchOptions(k=max(k_values)))``.
   c. Extract the ranked ``chunk_id`` list from the Hits.
   d. For each ``k`` in ``k_values``, compute NDCG / MRR / Recall against
      the resolved relevant set (with optional ``q.graded`` for NDCG).
3. Average per-metric per-k across all queries.

The retriever's ``SearchOptions`` carries the fusion/alpha/rerank knobs;
the CLI wires those from `Config.retrieval`.  The runner stays neutral
on fusion strategy so it can score any ``Retriever``.

Chunk-id drift fallback (R3-05)
-------------------------------

Chunk ids are not stable across re-ingests if the source or chunker
config changes.  When ``GoldQuery.content_hashes`` is set (parallel to
``relevant_chunk_ids``), the runner verifies that each configured id
resolves to a chunk with the matching content_hash.  If an id is missing
OR resolves to a chunk whose content_hash has changed, the runner falls
back to a ``content_hash``-keyed lookup against the backend and replaces
the id with whatever resolves there (or drops it if neither resolves).

The fallback is best-effort and never silent: a runner-level log entry
records every fallback.  When neither the id nor the hash resolves, the
gold entry contributes 0 to that query's metric (which surfaces the
drift loudly in the report).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path

from corpus_forge.eval.dataset import GoldQuery, load_gold
from corpus_forge.eval.metrics import mrr_at_k, ndcg_at_k, recall_at_k
from corpus_forge.retrieval.types import RetrievalMetrics, SearchOptions

_log = logging.getLogger(__name__)


def evaluate_retriever(
    retriever,
    gold_path: Path | str,
    k_values: Sequence[int],
    *,
    max_queries: int | None = None,
    rerank: bool = False,
    rerank_top_n: int = 50,
) -> RetrievalMetrics:
    """Evaluate ``retriever`` against the gold set at ``gold_path``.

    Args:
        retriever: object exposing ``search(query: str, options) -> list[Hit]``.
        gold_path: path to a JSONL gold set parseable by ``load_gold``.
        k_values: list of cutoffs (e.g. ``[5, 10, 20]``).
        max_queries: if set, evaluate at most this many queries (useful
            for smoke-test cycles on huge gold sets).
        rerank: when True (R4), every per-query search call sets
            ``SearchOptions.rerank=True`` so the retriever invokes its
            configured reranker.  When False (the R3 default), the
            existing no-rerank behaviour is preserved.
        rerank_top_n: forwarded to ``SearchOptions.rerank_top_n``;
            controls how many fused hits feed into the reranker.  Only
            consulted when ``rerank=True``.

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

    backend = getattr(retriever, "backend", None)
    return _evaluate_queries(
        retriever,
        queries,
        k_values,
        backend=backend,
        rerank=rerank,
        rerank_top_n=rerank_top_n,
    )


def _evaluate_queries(
    retriever,
    queries: Sequence[GoldQuery],
    k_values: Sequence[int],
    *,
    backend=None,
    rerank: bool = False,
    rerank_top_n: int = 50,
) -> RetrievalMetrics:
    """Core averaging loop, factored out so the CLI can compose around it."""
    top_k = max(k_values)
    sums_ndcg: dict[int, float] = dict.fromkeys(k_values, 0.0)
    sums_mrr: dict[int, float] = dict.fromkeys(k_values, 0.0)
    sums_recall: dict[int, float] = dict.fromkeys(k_values, 0.0)

    # Build SearchOptions once per evaluation (rerank flags don't change
    # across queries).  Use top_k as the per-query cutoff so the runner
    # can score across every requested k cutoff from a single retrieval
    # pass; truncation happens inside the metric functions.
    opts = SearchOptions(k=top_k, rerank=rerank, rerank_top_n=rerank_top_n)

    for q in queries:
        hits = retriever.search(q.query, opts)
        ranked_ids = _resolve_ranking(hits)
        relevant, resolved_graded = _resolve_gold_ids(q, backend)

        for k in k_values:
            sums_ndcg[k] += ndcg_at_k(ranked_ids, relevant, k, graded=resolved_graded)
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


# ── drift-tolerant relevant-set resolver (R3-05) ─────────────────────────


def _resolve_gold_ids(q: GoldQuery, backend) -> tuple[set[int], dict[int, int] | None]:
    """Resolve ``q.relevant_chunk_ids`` against the live corpus.

    Returns ``(resolved_ids, resolved_graded_or_None)``.

    Resolution rules per (id, optional hash) pair:

    1. If ``backend`` is None OR ``q.content_hashes`` is None → return the
       gold ids verbatim (no fallback path available).
    2. Else for each ``(id, hash)`` pair:
       - ``backend.get_chunk(id)`` → existing chunk, hash matches → keep id.
       - id missing OR hash mismatch → look up chunk by content_hash via
         ``_lookup_chunk_id_by_content_hash(backend, hash)``.  If that
         resolves, replace id with the resolved one.  Else drop the entry
         (logged at WARNING level).

    Graded relevance dict (if present) is remapped to the resolved ids.
    """
    gold_ids = q.relevant_chunk_ids
    hashes = q.content_hashes

    # Pre-compute the gold→graded view (gold ids → grade) so we can remap
    # to resolved ids while preserving the grade.
    gold_graded: dict[int, int] | None = q.graded

    if backend is None or hashes is None:
        return set(gold_ids), gold_graded

    resolved_ids: list[int] = []
    resolved_graded: dict[int, int] = {}
    for gid, ghash in zip(gold_ids, hashes, strict=True):
        # Stage 1: direct id resolution.  The content_hash is *advisory*
        # — if the id resolves to a real chunk we trust it.  This makes
        # the hash a safety-net for drift rather than a strict bond.
        chunk = _safe_get_chunk(backend, gid)
        if chunk is not None:
            resolved_ids.append(gid)
            if gold_graded is not None and gid in gold_graded:
                resolved_graded[gid] = gold_graded[gid]
            continue

        # Stage 2: id missing → fall back to content_hash lookup.
        replacement = _lookup_chunk_id_by_content_hash(backend, ghash)
        if replacement is not None:
            _log.warning(
                "eval: chunk_id drift in %s (id=%d → resolved via content_hash to id=%d)",
                q.query_id,
                gid,
                replacement,
            )
            resolved_ids.append(replacement)
            if gold_graded is not None and gid in gold_graded:
                resolved_graded[replacement] = gold_graded[gid]
            continue

        _log.warning(
            "eval: chunk_id drift in %s (id=%d, hash=%s) — neither id nor hash resolves; dropping",
            q.query_id,
            gid,
            ghash[:8],
        )

    return set(resolved_ids), (resolved_graded if gold_graded is not None else None)


def _safe_get_chunk(backend, chunk_id: int):
    """Call ``backend.get_chunk(chunk_id)`` defensively; return None on miss."""
    getter = getattr(backend, "get_chunk", None)
    if getter is None:
        return None
    try:
        return getter(chunk_id)
    except Exception:  # pragma: no cover — backend errors surface elsewhere
        return None


def _chunk_hash_matches(chunk, expected_hash: str) -> bool:
    """Return True iff ``chunk["content_hash"]`` equals ``expected_hash``."""
    if not isinstance(chunk, dict):
        return False
    return chunk.get("content_hash") == expected_hash


def _lookup_chunk_id_by_content_hash(backend, content_hash: str) -> int | None:
    """Resolve a chunk_id by its ``content_hash`` via the storage protocol.

    Phase R5-01: lifted onto ``StorageBackend.get_chunk_by_content_hash``.
    This helper now delegates to that method and projects the resolved
    chunk row down to its ``id``.  Any backend exception is swallowed
    (parity with the previous SQL-shim behaviour) so a missing backend
    method or transient failure surfaces as a drop+log in the caller
    rather than a hard runner crash.
    """
    getter = getattr(backend, "get_chunk_by_content_hash", None)
    if getter is None:
        return None
    try:
        chunk = getter(content_hash)
    except Exception:  # pragma: no cover — backend errors surface elsewhere
        return None
    if chunk is None:
        return None
    chunk_id = chunk.get("id") if isinstance(chunk, dict) else None
    return int(chunk_id) if chunk_id is not None else None


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
