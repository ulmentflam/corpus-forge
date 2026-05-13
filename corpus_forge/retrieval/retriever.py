"""Phase R2 — `Retriever` protocol + `HybridRetriever` implementation.

`HybridRetriever` is the user-facing entry point for hybrid (dense +
lexical) retrieval.  It composes:

- a ``StorageBackend`` (calls ``search_dense`` and ``search_lexical`` on
  the protocol — never reaches around to ``_execute``);
- an ``Embedder`` (calls ``encode_query``, not ``encode`` — see R2 plan);
- an ``embedder_id`` (the backend's row id for the embedder; the dense
  search is per-embedder);
- an optional ``reranker`` (R4 wires this; R2 stores it but never calls it).

Fusion strategy is per-call via ``SearchOptions.fusion``:

- ``"rrf"`` (default): rank-only reciprocal rank fusion.  Because RRF is
  scale-free, no score normalisation is needed — sidesteps the R1
  carry-over about SQLite/Postgres score-scale mismatch.
- ``"alpha"``: per-list min-max normalisation of each backend's scores,
  then linear combination via ``alpha * dense + (1 - alpha) * lexical``.

Dataset filter (``SearchOptions.dataset``) is resolved through
``backend.find_dataset_id_by_name(name)`` and pushed straight to both
backend calls.  An unresolvable dataset name returns an empty list (does
NOT silently leak across datasets).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from corpus_forge.retrieval.fusion import alpha_blend, reciprocal_rank_fusion
from corpus_forge.retrieval.normalize import min_max
from corpus_forge.retrieval.types import Hit, SearchOptions

if TYPE_CHECKING:
    from corpus_forge.backends.base import StorageBackend
    from corpus_forge.embedders.base import Embedder


# Multiplier applied to ``k`` before calling each backend search.  Standard
# hybrid-retrieval practice: pull ~2-4x more from each backend so fusion has
# headroom before truncating to top-k.
_OVERFETCH_MULTIPLIER = 2


class Retriever(Protocol):
    """A pluggable retriever.

    Implementations promise: given a query string and ``SearchOptions``,
    return a ranked ``list[Hit]`` of length ≤ ``options.k``.
    """

    def search(self, query: str, options: SearchOptions) -> list[Hit]: ...


class HybridRetriever:
    """Hybrid (dense + lexical) retriever with pluggable fusion.

    Args:
        backend: any ``StorageBackend``.  Calls ``search_dense`` /
            ``search_lexical`` / ``find_dataset_id_by_name`` through the
            protocol — never reaches around to backend-private members.
        embedder: any ``Embedder``.  The query path calls
            ``embedder.encode_query(...)`` (NOT ``encode``); for symmetric
            models this delegates to ``encode``, for Qwen3 it prepends the
            documented instruction prompt.
        embedder_id: row id of the embedder in the backend.  Same value as
            ``backend.register_embedder(embedder)``.
        reranker: R4 plumbing.  Stored but never called in R2; the search
            path is purely fusion + truncation.
    """

    def __init__(
        self,
        backend: "StorageBackend",
        embedder: "Embedder",
        embedder_id: int,
        reranker: Any | None = None,
    ) -> None:
        self.backend = backend
        self.embedder = embedder
        self.embedder_id = embedder_id
        self.reranker = reranker

    # ── public API ───────────────────────────────────────────────────────

    def search(self, query: str, options: SearchOptions) -> list[Hit]:
        """Run the hybrid search.

        Steps:
        1. Resolve the dataset name (if any) → dataset_id; bail out empty
           if the name is unknown so we never leak across datasets.
        2. Encode the query via ``embedder.encode_query``.
        3. Fan out ``backend.search_dense`` + ``backend.search_lexical``,
           each over-fetching by ``_OVERFETCH_MULTIPLIER * k`` to give
           fusion room.
        4. Fuse: RRF or alpha-blend per ``options.fusion``.
        5. Materialise into ``Hit`` objects with ``source="fused"`` and
           return the top-``k``.
        """
        # ── Step 1: dataset resolution ─────────────────────────────────
        dataset_id: int | None = None
        if options.dataset is not None:
            dataset_id = self.backend.find_dataset_id_by_name(options.dataset)
            if dataset_id is None:
                # Unknown dataset name → empty result.  Do NOT call the
                # backend with dataset_id=None — that would silently
                # search across all datasets, which is the leak the
                # caller is explicitly trying to prevent.
                return []

        # ── Step 2: encode the query ──────────────────────────────────
        # `encode_query` is the asymmetric-aware entry point.  For symmetric
        # models BaseEmbedder.encode_query just delegates to encode.
        query_vectors = self.embedder.encode_query([query])
        # Shape: (1, dim).  Reduce to a 1-D vector for the backend call.
        qvec = query_vectors[0]

        # ── Step 3: fan out the two backend searches ──────────────────
        overfetch_k = max(options.k * _OVERFETCH_MULTIPLIER, options.k)
        dense_hits = self.backend.search_dense(
            self.embedder_id,
            qvec,
            k=overfetch_k,
            dataset_id=dataset_id,
        )
        lexical_hits = self.backend.search_lexical(
            query,
            k=overfetch_k,
            dataset_id=dataset_id,
        )

        # Build chunk_id → Hit map for quick lookup post-fusion.  When the
        # same chunk_id appears in both lists, prefer the dense Hit's
        # metadata (arbitrary but stable choice).
        hits_by_id: dict[int, Hit] = {}
        for h in lexical_hits:
            hits_by_id[h.chunk_id] = h
        for h in dense_hits:
            hits_by_id[h.chunk_id] = h

        # ── Step 4: fuse ───────────────────────────────────────────────
        fused_scores: dict[int, float]
        if options.fusion == "rrf":
            dense_ranking = [h.chunk_id for h in dense_hits]
            lexical_ranking = [h.chunk_id for h in lexical_hits]
            fused_scores = reciprocal_rank_fusion([dense_ranking, lexical_ranking])
        else:
            # alpha path: per-list min-max normalise THEN blend.
            dense_norm = self._normalize_to_dict(dense_hits)
            lexical_norm = self._normalize_to_dict(lexical_hits)
            fused_scores = alpha_blend(dense_norm, lexical_norm, alpha=options.alpha)

        # ── Step 5: materialise & truncate ────────────────────────────
        # Sort descending by fused score; ties broken by chunk_id (stable).
        ordered = sorted(fused_scores.items(), key=lambda kv: (-kv[1], kv[0]))
        top: list[Hit] = []
        for cid, score in ordered[: options.k]:
            src = hits_by_id.get(cid)
            if src is None:
                continue
            top.append(
                Hit(
                    chunk_id=cid,
                    score=float(score),
                    text=src.text,
                    document_id=src.document_id,
                    source_uri=src.source_uri,
                    title=src.title,
                    dataset_id=src.dataset_id,
                    metadata=src.metadata,
                    source="fused",
                )
            )

        # NB: the reranker is intentionally NOT called here.  R4 wires it.
        # The presence of `options.rerank=True` + a non-None `self.reranker`
        # is a no-op in R2 (documented in the plan).

        return top

    # ── internals ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_to_dict(hits: Sequence[Hit]) -> dict[int, float]:
        """Per-list min-max normalise hit scores into a chunk_id → score dict."""
        if not hits:
            return {}
        scores = [h.score for h in hits]
        normalised = min_max(scores)
        return {h.chunk_id: s for h, s in zip(hits, normalised, strict=True)}
