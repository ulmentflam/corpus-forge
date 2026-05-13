"""corpus_forge.retrieval.rerank — Phase R4 reranker surface.

Public exports:

- ``Reranker`` (Protocol)
- ``CrossEncoderReranker`` — wraps ``sentence_transformers.CrossEncoder``;
  default model ``BAAI/bge-reranker-v2-m3``.  **Lazy-loaded** on first
  call so importing this package does NOT trigger a model download.
- ``OllamaReranker`` — score-via-completion fallback for Ollama-served
  models.  No default ``model_id``; caller must specify.

Importing this package is intentionally side-effect-free:

- ``sentence_transformers.CrossEncoder`` is NOT imported here.  It is
  imported lazily inside ``CrossEncoderReranker._get_model`` (mirror of
  the ``SentenceTransformersEmbedder._load_model`` discipline).
- ``openai.OpenAI`` is NOT imported here.  It is imported lazily inside
  ``OllamaReranker._get_client``.

This lets ``from corpus_forge.retrieval.rerank import CrossEncoderReranker``
succeed even when the user hasn't installed the heavy ML deps — the
ImportError surfaces only when they actually try to call ``rerank()``.
"""

from __future__ import annotations

from corpus_forge.retrieval.rerank.base import Reranker
from corpus_forge.retrieval.rerank.cross_encoder import CrossEncoderReranker
from corpus_forge.retrieval.rerank.ollama import OllamaReranker

__all__ = [
    "CrossEncoderReranker",
    "OllamaReranker",
    "Reranker",
]
