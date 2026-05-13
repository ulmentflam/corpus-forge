"""Phase R4 — `Reranker` Protocol.

Concrete implementations live in sibling modules:

- :mod:`corpus_forge.retrieval.rerank.cross_encoder` — wraps
  ``sentence_transformers.CrossEncoder`` (default model
  ``BAAI/bge-reranker-v2-m3``).
- :mod:`corpus_forge.retrieval.rerank.ollama` — wraps an Ollama-served
  chat model via the OpenAI-compat client; score-via-completion fallback.

Importing this module is cheap — no heavy ML libraries are loaded here
(the cross-encoder wrapper lazy-loads its model on first use; see R4-04).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from corpus_forge.retrieval.types import Hit


@runtime_checkable
class Reranker(Protocol):
    """A pluggable second-stage reranker.

    Implementations promise: given a query string and a list of fused
    ``Hit`` objects, return a re-sorted ``list[Hit]`` where ``score`` is the
    reranker's own scoring signal (NOT the upstream fused score) and
    ``source == "reranked"``.

    Attributes:
        name: Short human-readable label (e.g. ``"bge-reranker-v2-m3"``).
        model_id: Provider-qualified model identifier (e.g. the HF repo id
            for cross-encoders, or the Ollama tag for Ollama-served models).

    Methods:
        warmup: Eagerly load any heavy state (e.g. download the model).
            Calling rerank without warmup is allowed — the implementation
            must lazy-load on first call.  warmup is for prewarming before
            a latency-sensitive workload.
        rerank: Score and re-sort the input hits.  If ``top_n`` is
            provided, take the top ``top_n`` of the input by fused score
            FIRST, then rerank just those.  If ``top_n is None``, rerank
            every input hit.  Output is sorted descending by the new
            score and truncated to ``top_n`` hits.

    Source convention:
        Every output ``Hit`` carries ``source="reranked"`` so downstream
        consumers (eval, MCP) can distinguish reranked hits from raw
        fused hits.
    """

    name: str
    model_id: str

    def warmup(self) -> None: ...

    def rerank(
        self,
        query: str,
        hits: list[Hit],
        *,
        top_n: int | None = None,
    ) -> list[Hit]: ...
