"""Sentence Transformers embedder implementation."""

import logging
import time
from collections.abc import Sequence

import numpy as np

try:
    from sentence_transformers import SentenceTransformer

    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

from .base import BaseEmbedder

# Phase L Wave 4 — dedicated logger so model-load events are greppable
# in the rotating log under the documented ``corpus_forge.embedders.loader``
# namespace. INFO at load start + ready; DEBUG-only chatter elsewhere.
loader_logger = logging.getLogger("corpus_forge.embedders.loader")

# Qwen3-Embedding documented query-side instruction prompt.  Prepended to
# every query text in `encode_query` for Qwen3-family models.
_QWEN3_QUERY_INSTRUCT_PREFIX = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query\nQuery: "
)

# Model-id prefixes that trigger the Qwen3 query override.  Detection is
# case-insensitive on the lowercase form.
_QWEN3_LOWER_PREFIXES = ("qwen/qwen3-embedding", "qwen3-embedding")


def _is_qwen3_embedding(model_id: str) -> bool:
    """Return True if `model_id` names a Qwen3-Embedding-family model."""
    if not model_id:
        return False
    lower = model_id.lower()
    return any(lower.startswith(p) for p in _QWEN3_LOWER_PREFIXES)


class SentenceTransformersEmbedder(BaseEmbedder):
    """Sentence Transformers embedder."""

    def __init__(
        self,
        name: str,
        model_id: str,
        dimension: int,
        normalized: bool = True,
        distance: str = "cosine",
        device: str = "auto",
        batch_size: int = 32,
    ):
        super().__init__(
            name=name,
            provider="sentence_transformers",
            model_id=model_id,
            dimension=dimension,
            normalized=normalized,
            distance=distance,
        )
        self.device = device
        self.batch_size = batch_size
        self._model = None

    def _load_model(self):
        """Lazy load the SentenceTransformer model."""
        if self._model is None and SENTENCE_TRANSFORMERS_AVAILABLE:
            from corpus_forge._ml_device import resolve_device  # noqa: PLC0415

            loader_logger.info(
                "Loading embedder %s (sentence-transformers, %d-dim, device=%s)",
                self.name,
                self.dimension,
                self.device,
            )
            started = time.perf_counter()
            self._model = SentenceTransformer(self.model_id, device=resolve_device(self.device))
            loader_logger.info(
                "Embedder %s ready in %.1fs",
                self.name,
                time.perf_counter() - started,
            )

    def warmup(self) -> None:
        """Warm up the embedder by loading the model."""
        self._load_model()
        # Run a dummy inference to warm up
        if self._model is not None:
            self._model.encode(["warmup"], batch_size=self.batch_size)

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        """Encode texts into embeddings."""
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)

        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            raise ImportError("sentence-transformers package is required")

        self._load_model()
        if self._model is None:
            raise RuntimeError("Failed to load SentenceTransformer model")

        # Use the provided batch_size or fallback to instance batch_size
        _DEFAULT_BATCH_SIZE = 32
        actual_batch_size = batch_size if batch_size != _DEFAULT_BATCH_SIZE else self.batch_size

        embeddings = self._model.encode(
            list(texts),
            batch_size=actual_batch_size,
            convert_to_numpy=True,
            normalize_embeddings=self.normalized,
        )

        # Ensure correct dimension
        if embeddings.shape[1] != self.dimension:
            raise ValueError(
                f"Model {self.model_id} produced embeddings of dimension "
                f"{embeddings.shape[1]}, expected {self.dimension}"
            )

        return embeddings

    def encode_query(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        """Encode a query.

        For Qwen3-Embedding-family models (detected by ``model_id`` prefix
        ``Qwen/Qwen3-Embedding`` or the lowercase ``qwen3-embedding`` alias)
        the documented query-side instruction prompt is prepended to every
        text before delegation to ``encode``.

        For all other models, delegates to ``encode`` unchanged so symmetric
        embedders Just Work.
        """
        if not texts:
            return np.empty((0, self.dimension), dtype=np.float32)

        if _is_qwen3_embedding(self.model_id):
            prefixed = [_QWEN3_QUERY_INSTRUCT_PREFIX + t for t in texts]
            return self.encode(prefixed, batch_size=batch_size)

        return self.encode(texts, batch_size=batch_size)
