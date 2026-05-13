"""corpus_forge.retrieval — public retrieval surface.

Phase R1 lands the type surface (``Hit``, ``SearchOptions``,
``RetrievalMetrics``).  R2 will add a ``HybridRetriever`` that fuses dense
and lexical hits via RRF / alpha-weighted score combination.  R4 will plug
in a cross-encoder reranker.  R5 wires the retriever into the MCP server.

This module deliberately stays import-light: no numpy, no backend imports.
Consumers grab types here, then call ``backend.search_dense(...)`` /
``backend.search_lexical(...)`` directly on the ``StorageBackend`` protocol.
"""

from corpus_forge.retrieval.types import Hit, RetrievalMetrics, SearchOptions

__all__ = ["Hit", "RetrievalMetrics", "SearchOptions"]
