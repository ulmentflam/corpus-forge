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
from openai import APIConnectionError, APITimeoutError, OpenAI

from .base import BaseEmbedder

logger = logging.getLogger(__name__)


# Status-code threshold helpers — named so the ``_is_recoverable_exception``
# below isn't peppered with magic numbers.
_HTTP_CLIENT_ERROR_FLOOR = 400
_HTTP_SERVER_ERROR_FLOOR = 500
_HTTP_RATE_LIMITED = 429


def _is_recoverable_exception(exc: BaseException) -> bool:
    """Return ``True`` when bisecting / retrying ``exc`` might help.

    Bisection is the recovery for content-specific failures (one bad
    chunk in a batch poisons the response) and intermittent upstream
    glitches (5xx, rate-limit, connection blip). It's the wrong tool
    for **deterministic** failures: a wrong model name (404), a bad
    API key (401), a forbidden endpoint (403), or a request-shape
    mismatch (400/422) won't change between attempts, and bisecting
    them just wastes O(N) requests per batch on the same error. For
    those, we re-raise immediately so the caller sees the real cause.

    Policy:

    - 4xx **except** ``429`` (rate-limited) → non-recoverable; re-raise.
    - 5xx and ``429`` → recoverable; let the bisection logic try.
    - ``openai.APIConnectionError`` / ``openai.APITimeoutError``
      (transport-level failures with no ``status_code``) → recoverable.
    - **Any other** ``status_code``-less exception → non-recoverable.
      We used to treat all status-less exceptions as recoverable,
      but a programming bug that raised (say) ``KeyError`` from our
      own response-parsing code would then be bisected through
      O(N) pointless retries before the operator ever saw it. Narrow
      to known transport exception types so unexpected errors
      surface immediately.

    The HTTP-status check is duck-typed on a ``.status_code``
    attribute so it works against the OpenAI SDK's
    ``APIStatusError`` family without importing every concrete
    subclass.
    """

    status = getattr(exc, "status_code", None)
    if status is None:
        # No status code → recoverable only for known transport-level
        # SDK exceptions; everything else (programming bugs, decoded-
        # response shape errors, …) bubbles up.
        return isinstance(exc, (APIConnectionError, APITimeoutError))
    try:
        status_int = int(status)
    except (TypeError, ValueError):
        return True
    return not (
        _HTTP_CLIENT_ERROR_FLOOR <= status_int < _HTTP_SERVER_ERROR_FLOOR
        and status_int != _HTTP_RATE_LIMITED
    )


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
        logged at WARNING with non-PII metadata (orig_idx, chars,
        sha256 prefix), then skipped — the rest of the batch's good rows
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
            # Row-count sanity check — if the provider returned fewer
            # (or more) rows than we asked for we can't map vectors
            # back to inputs, so treat the whole batch as failed and
            # let the bisection isolate the offender. Surfaces broken
            # server-side responses (the OpenAI / Ollama SDKs trust
            # the response shape and would silently misalign otherwise).
            if len(row_vecs) != len(texts):
                logger.warning(
                    "Embed batch row-count mismatch: expected %d, got %d — "
                    "treating batch as failed and bisecting to isolate",
                    len(texts),
                    len(row_vecs),
                )
                if len(texts) == 1:
                    return [], list(orig_indices)
                # Fall through to bisection — drop the partial response.
            else:
                # Local NaN check — Ollama in particular sometimes
                # returns 2xx with NaN-laced floats; the SDK happily
                # passes those through. Treat any NaN-containing row
                # as a failure for the bisection.
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
            # Re-raise non-recoverable errors immediately — bisecting a
            # wrong-model-name or bad-API-key error just produces N
            # rounds of the same 4xx and hides the real cause. The
            # helper inspects ``exc.status_code`` (duck-typed against
            # the OpenAI SDK's ``APIStatusError`` family) and only
            # treats 4xx-except-429 as deterministic.
            if not _is_recoverable_exception(exc):
                logger.error(
                    "Non-recoverable embed error (status=%s): %s — re-raising",
                    getattr(exc, "status_code", "?"),
                    str(exc)[:200],
                )
                raise
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
            # NOTE: we intentionally do NOT log chunk text here — even
            # short previews leak PII for users ingesting personal
            # vaults / chat history. ``sha256`` is enough to reproduce
            # the failure out-of-band (look up the chunk by hash in
            # ``corpus.chunks`` and replay against the embedder) and
            # ``orig_idx`` + ``chars`` give operators enough context
            # to find the corpus-forge log entry that processed it.
            logger.warning(
                "Skipping chunk that the embedder cannot encode "
                "(orig_idx=%d, chars=%d, sha256=%s). "
                "The chunk stays in chunks_missing_embedding; a future "
                "ingest pass will retry it once the embedder recovers.",
                orig_indices[0],
                len(single_text),
                sha,
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
