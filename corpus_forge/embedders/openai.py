"""OpenAI embedder implementation.

The ``base_url`` constructor kwarg accepts any OpenAI-compatible
endpoint (vLLM, llama.cpp's OpenAI shim, LiteLLM, etc.), so the same
embedder can point at a local-substituted URL or a hosted API by config
alone. ``api_key_env`` names the environment variable the bearer token
is read from at first call; when the env var is unset the embedder
falls back to the literal string ``"local-no-auth"`` so local
zero-auth proxies keep working without polluting ``secrets.env``.
"""

import contextlib
import logging
from collections.abc import Sequence

import numpy as np
from openai import OpenAI

from .base import BaseEmbedder

logger = logging.getLogger(__name__)


class OpenAIEmbedder(BaseEmbedder):
    """OpenAI / OpenAI-compatible HTTP embedder."""

    def __init__(
        self,
        name: str,
        model_id: str,
        dimension: int,
        normalized: bool = True,
        distance: str = "cosine",
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
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
        self.base_url = base_url
        self.batch_size = batch_size
        self._client = None

    def _get_client(self):
        """Get the OpenAI client, initializing on first call.

        ``api_key_env`` names the env var the bearer is read from. When
        ``base_url`` is set (local-substitution mode pointing at a
        local OpenAI-compatible proxy) the env var is optional and we
        fall back to ``"local-no-auth"``. With the default base URL,
        a missing env var is a hard configuration failure.
        """
        if self._client is None:
            import os  # noqa: PLC0415

            api_key = os.getenv(self.api_key_env)
            if not api_key:
                if not self.base_url:
                    raise ValueError(
                        f"API key not found in environment variable {self.api_key_env}"
                    )
                api_key = "local-no-auth"
            kwargs: dict = {"api_key": api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def warmup(self) -> None:
        """Warm up the embedder (no-op for OpenAI as it's served remotely)."""
        # For OpenAI, we could make a dummy call, but that would cost money
        # So we just ensure the client can be initialized
        with contextlib.suppress(Exception):
            self._get_client()

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        """Encode texts into embeddings using OpenAI API.

        Dimension handling: ``self.dimension`` is forwarded as the
        ``dimensions`` request field, which OpenAI's
        ``text-embedding-3-*`` models honour server-side via Matryoshka
        truncation. Local OpenAI-compatible servers vary — Ollama's
        ``/v1/embeddings`` shim currently ignores the field and always
        returns the full native width of the model (e.g. 4096 for
        ``qwen3-embedding:8b``). Because Matryoshka-trained models are
        prefix-coherent (the first N dims ARE the N-dim embedding),
        truncating client-side produces the same vector the server
        would have returned. We slice + renormalise instead of raising.
        """
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

            # Call OpenAI API. ``dimensions=`` is the official
            # server-side Matryoshka knob; servers that don't know
            # the field just ignore it (Ollama) — that's fine because
            # the client-side truncation below produces the same
            # result for Matryoshka-trained models.
            response = client.embeddings.create(
                model=self.model_id,
                input=batch,
                encoding_format="float",
                dimensions=self.dimension,
            )

            # Extract embeddings
            batch_embeddings = [item.embedding for item in response.data]
            all_embeddings.extend(batch_embeddings)

        # Convert to numpy array
        embeddings = np.array(all_embeddings, dtype=np.float32)

        # Dimension check — accept exact match (server honoured
        # ``dimensions=``) OR a longer native vector that we can
        # truncate (Matryoshka prefix). A SHORTER vector is a real
        # config mismatch and stays a hard error.
        actual_dim = embeddings.shape[1]
        if actual_dim < self.dimension:
            raise ValueError(
                f"Model {self.model_id} produced embeddings of dimension "
                f"{actual_dim}, expected {self.dimension}"
            )
        if actual_dim > self.dimension:
            logger.debug(
                "Server returned %d-dim embeddings; truncating to configured "
                "%d (Matryoshka prefix). To silence this, configure the "
                "embedder with the model's native width or upgrade the "
                "server so ``dimensions=`` is honoured.",
                actual_dim,
                self.dimension,
            )
            embeddings = embeddings[:, : self.dimension]

        # Normalize if requested (renorm is REQUIRED after truncation
        # — a Matryoshka prefix is not unit-length even if the full
        # vector was).
        if self.normalized:
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
            # Avoid division by zero
            norms = np.maximum(norms, 1e-12)
            embeddings = embeddings / norms

        return embeddings
