"""Sentence Transformers embedder implementation."""

from collections.abc import Sequence

import numpy as np

try:
    from sentence_transformers import SentenceTransformer

    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False

from .base import BaseEmbedder


class SentenceTransformersEmbedder(BaseEmbedder):
    """Sentence Transformers embedder."""

    def __init__(  # noqa: PLR0913
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
            import torch  # noqa: PLC0415
            device = self.device
            if device == "auto":
                device = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
            self._model = SentenceTransformer(self.model_id, device=device)

    def warmup(self) -> None:
        """Warm up the embedder by loading the model."""
        self._load_model()
        # Run a dummy inference to warm up
        if self._model is not None:
            self._model.encode(["warmup"], batch_size=self.batch_size)

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        """Encode texts into embeddings."""
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
