"""Embedder protocol and base classes for corpus-forge."""

from collections.abc import Sequence
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    """Pluggable embedder. Implementations live behind this Protocol.

    Phase R2 adds the asymmetric ``encode_query`` method.  Symmetric models
    (the majority) can leave the default-impl path on ``BaseEmbedder`` which
    just delegates to ``encode``.  Models with documented asymmetric
    instruction prompts (Qwen3-Embedding, E5, GTE, etc.) override.
    """

    name: str
    provider: str
    model_id: str
    dimension: int
    normalized: bool
    distance: str

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray: ...
    def encode_query(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray: ...
    def warmup(self) -> None: ...


class BaseEmbedder:
    """Base embedder with common functionality."""

    def __init__(
        self,
        name: str,
        provider: str,
        model_id: str,
        dimension: int,
        normalized: bool = True,
        distance: str = "cosine",
    ):
        self.name = name
        self.provider = provider
        self.model_id = model_id
        self.dimension = dimension
        self.normalized = normalized
        self.distance = distance

    def warmup(self) -> None:
        """Warm up the embedder (e.g., load model)."""
        # Base implementation does nothing
        pass

    def encode_query(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        """Encode a query (default: delegate to ``encode``).

        Subclasses MAY override for asymmetric model families.  See
        ``SentenceTransformersEmbedder.encode_query`` for the Qwen3 override.
        """
        # `self.encode` is supplied by concrete subclasses or attribute-
        # injected at runtime (used by a handful of tests and ad-hoc embedders).
        return self.encode(texts, batch_size=batch_size)  # type: ignore[attr-defined]
