"""Retrieval types — Phase R1.

Frozen dataclasses describing the public retrieval surface:

- ``Hit``: a single search result (dense, lexical, fused, or reranked).
- ``SearchOptions``: caller-side knobs (k, dataset filter, fusion strategy,
  alpha for the alpha-fusion variant, rerank toggle + top-n).
- ``RetrievalMetrics``: evaluation block (k → score for nDCG / MRR / Recall).

R1 emits ``Hit.source`` of "dense" and "lexical".  R2 (HybridRetriever) adds
"fused" and R4 adds "reranked".  The Literal is forward-compat from day one
so downstream consumers (R2, R5) need no type widening when the new sources
land.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class Hit:
    """A single retrieval result.

    Attributes:
        chunk_id:     Primary key into ``chunks``.
        score:        Higher = better.  Normalised to [0, 1] for fused hits.
                      For pure dense / lexical hits, scale depends on the
                      backend (sqlite-vec returns cosine distance which the
                      sqlite backend maps to ``1 - distance``; ts_rank_cd
                      from Postgres is already higher-is-better).
        text:         The chunk text body.
        document_id:  Document the chunk belongs to (None for message-only
                      conversation chunks).
        source_uri:   ``vault://…`` / ``claude-code://…`` etc.  None mirrors
                      document_id.
        title:        Document title if available.  None for chat chunks.
        dataset_id:   Dataset the chunk belongs to.
        metadata:     Free-form structured metadata.  R1 always passes ``{}``;
                      kept for forward-compat with rerank scoring blocks.
        source:       Producer label.  R1 emits "dense" and "lexical".
                      R2's HybridRetriever emits "fused".  R4's cross-encoder
                      reranker emits "reranked".
    """

    chunk_id: int
    score: float
    text: str
    document_id: int | None
    source_uri: str | None
    title: str | None
    dataset_id: int
    metadata: dict[str, Any]
    source: Literal["dense", "lexical", "fused", "reranked"]


@dataclass(frozen=True)
class SearchOptions:
    """Caller-side knobs for a search call.

    Defaults match the Phase R1 plan verbatim — see the master plan for
    rationale on the fusion strategy and reranker toggle.
    """

    k: int = 10
    dataset: str | None = None
    fusion: Literal["rrf", "alpha"] = "rrf"
    alpha: float = 0.5
    rerank: bool = False
    rerank_top_n: int = 50


@dataclass(frozen=True)
class RetrievalMetrics:
    """Evaluation block emitted by the eval phase.

    Each field is a ``dict[int, float]`` from k → score, so a single
    ``RetrievalMetrics`` object can capture nDCG@5, nDCG@10, nDCG@20, ... at
    once.
    """

    ndcg: dict[int, float] = field(default_factory=dict)
    mrr: dict[int, float] = field(default_factory=dict)
    recall: dict[int, float] = field(default_factory=dict)
