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
import hashlib
import logging
import math
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
        # Populated by :meth:`encode` — original-text-list positions
        # that the bisecting embed could not encode (5xx / NaN-shaped
        # response / repeated isolation failure). Callers consume this
        # via ``getattr(embedder, "last_failed_indices", [])`` and
        # skip the matching chunk_ids when writing to the backend so
        # the failed chunks stay in ``chunks_missing_embedding`` for
        # the next ingest pass instead of being permanently dead-marked.
        self.last_failed_indices: list[int] = []

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
            # ``max_retries=0``: the OpenAI SDK retries 5xx responses
            # twice by default with exponential backoff (~1s, 2s, 4s).
            # When the upstream model is wedged and returning
            # NaN-shaped 500s — the failure mode the maintainer hit on
            # 2026-05-22 against Ollama-served ``qwen3-embedding:8b``
            # — that doubles wall-clock per failure and never recovers
            # (the same chunk just keeps failing). We do our own
            # bisection-based recovery in :meth:`encode` instead, so
            # the SDK can short-circuit fast on the first 5xx and let
            # our logic isolate the offender.
            kwargs: dict = {"api_key": api_key, "max_retries": 0}
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

        Bisecting recovery
        ------------------
        Each batch is sent through ``client.embeddings.create`` first.
        On either a transport exception (5xx after the SDK's
        ``max_retries=0``) OR a NaN value detected in the returned
        vectors, the batch is recursively bisected to isolate the
        offending chunk(s). At ``len(batch) == 1`` the bad chunk is
        logged at WARNING with its length, sha256, and first/last 80
        characters, then skipped — the rest of the batch's good rows
        still flow through. The original-text-list positions of
        skipped chunks are stored on :attr:`last_failed_indices` so
        the ingest caller can avoid writing the corresponding
        ``chunk_id`` rows to ``embeddings_<name>``; those chunks stay
        in ``chunks_missing_embedding`` and get re-tried on the next
        ingest pass.

        Why bisect instead of fail-fast the whole batch?
        On the maintainer's host (Ollama-served qwen3-embedding:8b)
        ~3% of chunks intermittently produce NaN, in episodes of 1-3
        sequential batches. A fail-fast policy would either lose the
        other 99% of the batch or trigger an 8-minute SDK retry storm
        per file; bisection costs O(log N) extra calls per failed
        batch (~6 calls for a 256-batch) and recovers everything else.

        Return shape
        ------------
        ``(M, dim)`` where ``M ≤ len(texts)``. ``M == len(texts)`` on
        the happy path. The output never contains NaN rows — those
        are isolated out by the bisection.

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

        # Use the provided batch_size or fallback to instance batch_size.
        # The default kwarg is 32 (legacy); when the caller doesn't
        # override, we honour the instance's ``batch_size`` instead so
        # config-driven values (typically 256) win.
        _DEFAULT_BATCH_SIZE = 32
        actual_batch_size = batch_size if batch_size != _DEFAULT_BATCH_SIZE else self.batch_size

        texts_list = list(texts)
        self.last_failed_indices = []
        good_rows: list[list[float]] = []
        failed_indices: list[int] = []

        for batch_start in range(0, len(texts_list), actual_batch_size):
            batch_end = min(batch_start + actual_batch_size, len(texts_list))
            batch_texts = texts_list[batch_start:batch_end]
            batch_orig_indices = list(range(batch_start, batch_end))

            rows, failures = self._encode_with_bisection(client, batch_texts, batch_orig_indices)
            good_rows.extend(rows)
            failed_indices.extend(failures)

        self.last_failed_indices = failed_indices

        if not good_rows:
            # Every chunk failed — return an empty (0, dim) array so
            # the caller can still ``len(...)`` without crashing.
            return np.zeros((0, self.dimension), dtype=np.float32)

        embeddings = np.array(good_rows, dtype=np.float32)

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

    def _encode_with_bisection(
        self,
        client,
        texts: list[str],
        orig_indices: list[int],
    ) -> tuple[list[list[float]], list[int]]:
        """Encode ``texts``; bisect on transport-error / NaN-shaped responses.

        Returns ``(good_rows, failed_orig_indices)``. ``good_rows`` are
        the row vectors for chunks that successfully embedded (length
        ≤ ``len(texts)``); ``failed_orig_indices`` are positions in
        the ORIGINAL ``texts`` list (the one ``encode`` was called
        with) that hit an unrecoverable failure even at
        ``len(batch) == 1``.

        Bisection terminates either when the whole batch encodes
        cleanly (no NaN, no exception) or when the recursion reaches a
        single chunk and that chunk still fails — in which case the
        chunk is logged with enough detail to reproduce the failure
        out-of-band.
        """

        try:
            response = client.embeddings.create(
                model=self.model_id,
                input=texts,
                encoding_format="float",
                dimensions=self.dimension,
            )
            row_vecs = [item.embedding for item in response.data]
            # Local NaN check — Ollama in particular sometimes returns
            # 2xx with NaN-laced floats; the SDK happily passes those
            # through. Treat any NaN-containing row as a failure for
            # the bisection.
            nan_row_idx_within_batch = [
                i for i, vec in enumerate(row_vecs) if any(math.isnan(x) for x in vec)
            ]
            if not nan_row_idx_within_batch:
                return row_vecs, []
            logger.debug(
                "Embed batch (size=%d) returned %d NaN rows; bisecting to isolate",
                len(texts),
                len(nan_row_idx_within_batch),
            )
            # Fall through to bisection below.
        except Exception as exc:
            logger.debug(
                "Embed batch (size=%d) raised %s: %s — bisecting to isolate",
                len(texts),
                type(exc).__name__,
                str(exc)[:160],
            )
            # Fall through to bisection below.

        if len(texts) == 1:
            single_text = texts[0]
            sha = hashlib.sha256(single_text.encode("utf-8", errors="replace")).hexdigest()[:12]
            _PREVIEW_CHARS = 80  # chars of head/tail to show in the WARNING log
            head = single_text[:_PREVIEW_CHARS].replace("\n", "\\n")
            tail = (
                single_text[-_PREVIEW_CHARS:].replace("\n", "\\n")
                if len(single_text) > _PREVIEW_CHARS
                else ""
            )
            logger.warning(
                "Skipping chunk that the embedder cannot encode "
                "(orig_idx=%d, chars=%d, sha256=%s, first80=%r, last80=%r). "
                "The chunk stays in chunks_missing_embedding; a future "
                "ingest pass will retry it once the embedder recovers.",
                orig_indices[0],
                len(single_text),
                sha,
                head,
                tail,
            )
            return [], [orig_indices[0]]

        mid = len(texts) // 2
        left_rows, left_failed = self._encode_with_bisection(
            client, texts[:mid], orig_indices[:mid]
        )
        right_rows, right_failed = self._encode_with_bisection(
            client, texts[mid:], orig_indices[mid:]
        )
        return left_rows + right_rows, left_failed + right_failed
