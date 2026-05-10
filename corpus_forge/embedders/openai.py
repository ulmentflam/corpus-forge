"""OpenAI embedder implementation."""

import contextlib
from collections.abc import Sequence

import numpy as np

try:
    # pyrefly: ignore[missing-import]  # optional dep, install via [openai] extra
    from openai import OpenAI

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

from .base import BaseEmbedder


class OpenAIEmbedder(BaseEmbedder):
    """OpenAI embedder."""

    def __init__(
        self,
        name: str,
        model_id: str,
        dimension: int,
        normalized: bool = True,
        distance: str = "cosine",
        api_key_env: str = "OPENAI_API_KEY",
        batch_size: int = 256,
    ):
        super().__init__(
            name=name,
            provider="openai",
            model_id=model_id,
            dimension=dimension,
            normalized=normalized,
            distance=distance,
        )
        self.api_key_env = api_key_env
        self.batch_size = batch_size
        self._client = None

    def _get_client(self):
        """Get OpenAI client, initializing if needed."""
        if self._client is None and OPENAI_AVAILABLE:
            # Import here to avoid circular dependency with openai package
            import os  # noqa: PLC0415

            api_key = os.getenv(self.api_key_env)
            if not api_key:
                raise ValueError(f"API key not found in environment variable {self.api_key_env}")
            self._client = OpenAI(api_key=api_key)
        return self._client

    def warmup(self) -> None:
        """Warm up the embedder (no-op for OpenAI as it's served remotely)."""
        # For OpenAI, we could make a dummy call, but that would cost money
        # So we just ensure the client can be initialized
        with contextlib.suppress(Exception):
            self._get_client()

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        """Encode texts into embeddings using OpenAI API."""
        if not OPENAI_AVAILABLE:
            raise ImportError("openai package is required")

        client = self._get_client()
        if client is None:
            raise RuntimeError("Failed to initialize OpenAI client")

        # Use the provided batch_size or fallback to instance batch_size
        _DEFAULT_BATCH_SIZE = 32
        actual_batch_size = batch_size if batch_size != _DEFAULT_BATCH_SIZE else self.batch_size

        # Process in batches
        all_embeddings = []
        texts_list = list(texts)

        for i in range(0, len(texts_list), actual_batch_size):
            batch = texts_list[i : i + actual_batch_size]

            # Call OpenAI API
            response = client.embeddings.create(
                model=self.model_id, input=batch, encoding_format="float"
            )

            # Extract embeddings
            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)

        # Convert to numpy array
        embeddings = np.array(all_embeddings, dtype=np.float32)

        # Ensure correct dimension
        if embeddings.shape[1] != self.dimension:
            raise ValueError(
                f"Model {self.model_id} produced embeddings of dimension "
                f"{embeddings.shape[1]}, expected {self.dimension}"
            )

        # Normalize if requested (though OpenAI embeddings are typically already normalized)
        if self.normalized:
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            # Avoid division by zero
            norms = np.maximum(norms, 1e-12)
            embeddings = embeddings / norms

        return embeddings
