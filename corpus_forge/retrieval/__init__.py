"""corpus_forge.retrieval — public retrieval surface.

Phase R1 lands the type surface (``Hit``, ``SearchOptions``,
``RetrievalMetrics``).  Phase R2 adds the fusion primitives
(``reciprocal_rank_fusion``, ``alpha_blend``, ``min_max``) and the
``HybridRetriever`` / ``Retriever`` pair.  R4 will plug in a cross-encoder
reranker.  R5 wires the retriever into the MCP server.

The module stays import-light: numpy is imported lazily inside the
retriever class; no backend imports happen here.  Consumers grab types
here, then call ``backend.search_dense(...)`` / ``backend.search_lexical(...)``
through the ``StorageBackend`` protocol — the R1 lift discipline holds.
"""

from corpus_forge.retrieval.fusion import alpha_blend, reciprocal_rank_fusion
from corpus_forge.retrieval.normalize import min_max
from corpus_forge.retrieval.types import Hit, RetrievalMetrics, SearchOptions

__all__ = [
    "Hit",
    "RetrievalMetrics",
    "SearchOptions",
    "alpha_blend",
    "min_max",
    "reciprocal_rank_fusion",
]
