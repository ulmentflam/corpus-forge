"""Embedder protocol and base classes for corpus-forge."""

from collections.abc import Sequence
from typing import Protocol

import numpy as np


class Embedder(Protocol):
    """Pluggable embedder. Implementations live behind this Protocol."""

    name: str
    provider: str
    model_id: str
    dimension: int
    normalized: bool
    distance: str

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray: ...
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
