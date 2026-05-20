"""Retrieval types — Phase R1.

Frozen dataclasses describing the public retrieval surface:

- ``Hit``: a single search result (dense, lexical, fused, or reranked).
- ``SearchOptions``: caller-side knobs (k, dataset filter, fusion strategy,
  alpha for the alpha-fusion variant, rerank toggle + top-n).
- ``RetrievalMetrics``: evaluation block (k → score for nDCG / MRR / Recall).
- ``SearchResponse``: wrapper returned by ``HybridRetriever.search()`` (Phase P1).

R1 emits ``Hit.source`` of "dense" and "lexical".  R2 (HybridRetriever) adds
"fused" and R4 adds "reranked".  The Literal is forward-compat from day one
so downstream consumers (R2, R5) need no type widening when the new sources
land.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
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

    Phase N Wave 3 — static fast tier (default OFF):

    - ``fast_tier_mode``:
        - ``"skip"`` (default): pre-Wave-3 behaviour.  Fast embedder
          never called even when wired.
        - ``"shortcut"``: fast embedder runs first → top-N candidates
          seed the main dense + lexical fan-out via the backend's
          ``chunk_ids=`` filter.  Wave 2 boost + reranker still
          compose normally.
        - ``"only"``: fast embedder produces top-k directly.  No
          lexical, no rerank.  Lowest latency, lower quality.
    - ``fast_tier_top_n``: number of candidates the fast tier seeds in
      ``shortcut`` mode.  Default 200; smaller numbers cut latency
      further but risk recall loss.
    """

    k: int = 10
    dataset: str | None = None
    fusion: Literal["rrf", "alpha"] = "rrf"
    alpha: float = 0.5
    rerank: bool = False
    rerank_top_n: int = 50
    fast_tier_mode: Literal["skip", "shortcut", "only"] = "skip"
    fast_tier_top_n: int = 200


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


@dataclass(eq=False)
class SearchResponse(list):  # type: ignore[type-arg]
    """Result envelope returned by ``HybridRetriever.search()`` (Phase P1).

    Wraps the ``list[Hit]`` results with provenance metadata so callers
    can trace a response back to the originating query without coupling
    to a running session.

    Inherits from ``list`` for full backward compatibility with pre-P1 code
    that checked ``isinstance(result, list)`` or compared to ``[]``.
    The list content mirrors ``self.results``; both are kept in sync by
    ``__post_init__``.

    Attributes:
        query_id:    UUID4 hex string generated per call.  Unique across
                     concurrent searches in the same process.
        results:     Ranked hit list — the same objects previously returned
                     directly by ``HybridRetriever.search()``.
        query:       The raw query string passed to ``search()``.
        dataset_id:  Resolved dataset primary key (None when no dataset
                     filter was requested or the name resolved to None).
        started_at:  Wall-clock instant captured at the very start of the
                     ``search()`` call (before dataset resolution).
        session_id:  Optional search-session foreign key (Phase P1
                     ``search_sessions`` table).  Populated by the MCP
                     layer when session recording is enabled; defaults to
                     ``None``.

    Iteration shim — transparent backward compatibility:

        for hit in response:          # same as: for hit in response.results
        n = len(response)             # same as: len(response.results)
        first = response[0]           # same as: response.results[0]
        chunk = response[2:5]         # slice delegation to response.results
        isinstance(response, list)    # True — preserves pre-P1 isinstance checks
        response == []                # True when results is empty

    Existing callers that loop over the search result directly, or that
    checked ``isinstance(result, list)``, continue to work without
    modification.
    """

    query_id: str
    results: list[Hit]
    query: str
    dataset_id: int | None
    started_at: datetime
    session_id: int | None = None

    def __post_init__(self) -> None:
        """Populate the list content with results for backward-compat."""
        # list.__init__ is NOT called by the dataclass-generated __init__,
        # so we call it here to initialise the underlying C-level storage.
        list.__init__(self, self.results)

    # ── serialisation helper ──────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe dict with ``started_at`` as an ISO-8601 string.

        Use this instead of ``dataclasses.asdict`` when you need to serialise
        to JSON directly.  ``dataclasses.asdict`` still works for round-trips
        where the caller handles the datetime conversion themselves.
        """
        import dataclasses  # noqa: PLC0415 — deferred to keep import-time cheap

        raw = dataclasses.asdict(self)
        raw["started_at"] = (
            self.started_at.isoformat()
            if self.started_at.tzinfo is not None
            else self.started_at.replace(tzinfo=None).isoformat()
        )
        return raw
