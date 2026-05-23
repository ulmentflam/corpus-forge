"""Regression tests for the bisecting :class:`OpenAIEmbedder.encode`.

Background
----------
On 2026-05-22 the maintainer's ingest stalled for ~8 minutes per file
because Ollama-served ``qwen3-embedding:8b`` would intermittently
return NaN-shaped 500s, and the OpenAI SDK's default retry policy
(``max_retries=2``) would hammer the failing batch for the full
backoff window before giving up. The whole file's chunks would then
be marked failed even though only a handful of chunks were actually
bad.

This module pins the bisection-based recovery added to
``OpenAIEmbedder``: on either a transport exception OR a NaN value
detected in the returned vectors, the embedder recursively halves
the batch until the offending chunk is isolated, logs its
length / sha / preview, skips it, and returns the rest. Callers
read ``embedder.last_failed_indices`` to know which original
positions were skipped.

What we pin
-----------
1. ``__init__`` populates ``last_failed_indices = []``.
2. ``_get_client`` builds the OpenAI client with ``max_retries=0``
   (no slow SDK retry storm — we own the recovery).
3. Happy path: a clean batch returns all rows; ``last_failed_indices``
   is empty.
4. Single-chunk NaN return: the chunk is logged and skipped; the
   output shape is ``(N-1, dim)``.
5. Transport exception on a batch: recursion bisects; only the
   actually-failing chunk(s) get marked, the rest succeed.
6. Embedded NaN in a multi-row response: bisection still works (we
   don't rely on the SDK raising).
7. All-fail batch: returns ``(0, dim)`` empty array with every
   original index in ``last_failed_indices``.
8. Multi-batch processing: failures in one batch don't poison
   subsequent batches' ``last_failed_indices`` (the attribute is
   reset on every ``encode`` call).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from corpus_forge.embedders.openai import OpenAIEmbedder


# ─────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────


def _make_embedder(*, dimension: int = 4, batch_size: int = 256) -> OpenAIEmbedder:
    """Construct an embedder bound to a fake local server.

    ``base_url`` is set so the embedder takes the local-no-auth path
    even if ``OLLAMA_API_KEY`` is missing in the test env.
    """

    return OpenAIEmbedder(
        name="test",
        model_id="test-model",
        dimension=dimension,
        normalized=False,  # let tests assert raw row vectors
        api_key_env="DOES_NOT_EXIST",
        base_url="http://localhost:0",
        batch_size=batch_size,
    )


def _embeddings_response(vecs: list[list[float]]):
    """Mimic the openai SDK's ``CreateEmbeddingResponse.data``."""

    return SimpleNamespace(data=[SimpleNamespace(embedding=v) for v in vecs])


# ─────────────────────────────────────────────────────────────────────
# Construction + client config
# ─────────────────────────────────────────────────────────────────────


class TestConstruction:
    def test_last_failed_indices_initialised_empty(self) -> None:
        emb = _make_embedder()
        assert emb.last_failed_indices == []

    def test_client_constructed_with_max_retries_zero(self) -> None:
        """``max_retries=0`` is essential — without it the SDK adds
        ~7s of backoff per failed batch and the bisection loop slows
        to a crawl. Pin the constructor kwarg."""

        emb = _make_embedder()
        import corpus_forge.embedders.openai as openai_mod

        captured_kwargs: list[dict] = []

        class _SpyOpenAI:
            def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
                captured_kwargs.append(kwargs)

        original = openai_mod.OpenAI
        openai_mod.OpenAI = _SpyOpenAI  # type: ignore[assignment]
        try:
            emb._get_client()
        finally:
            openai_mod.OpenAI = original  # type: ignore[assignment]

        assert captured_kwargs, "OpenAI() should have been invoked"
        assert captured_kwargs[0].get("max_retries") == 0


# ─────────────────────────────────────────────────────────────────────
# Happy path
# ─────────────────────────────────────────────────────────────────────


class TestHappyPath:
    def test_clean_batch_returns_all_rows(self) -> None:
        emb = _make_embedder()
        client = MagicMock()
        client.embeddings.create.return_value = _embeddings_response(
            [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0], [9.0, 1.0, 1.0, 1.0]]
        )
        emb._client = client

        result = emb.encode(["a", "b", "c"])
        assert result.shape == (3, 4)
        assert emb.last_failed_indices == []
        # The SDK was called exactly once — no bisection needed.
        assert client.embeddings.create.call_count == 1


# ─────────────────────────────────────────────────────────────────────
# Single-chunk failure
# ─────────────────────────────────────────────────────────────────────


class TestSingleChunkFailure:
    def test_single_chunk_nan_logs_and_skips(self, caplog: pytest.LogCaptureFixture) -> None:
        emb = _make_embedder()
        client = MagicMock()

        def fake_create(*, model, input, encoding_format, dimensions):  # type: ignore[no-untyped-def]
            return _embeddings_response([[float("nan"), float("nan"), float("nan"), float("nan")]])

        client.embeddings.create.side_effect = fake_create
        emb._client = client

        import logging

        with caplog.at_level(logging.WARNING, logger="corpus_forge.embedders.openai"):
            result = emb.encode(["bad-chunk"])
        assert result.shape == (0, 4)
        assert emb.last_failed_indices == [0]
        assert any(
            "Skipping chunk that the embedder cannot encode" in r.message for r in caplog.records
        )
        # The log must include enough detail to reproduce the failure
        log_msg = next(r.message for r in caplog.records if "Skipping chunk" in r.message)
        for tag in ("orig_idx=", "chars=", "sha256=", "first80=", "last80="):
            assert tag in log_msg, f"missing {tag} in WARNING log: {log_msg!r}"

    def test_single_chunk_5xx_logs_and_skips(self) -> None:
        emb = _make_embedder()
        client = MagicMock()
        client.embeddings.create.side_effect = RuntimeError("APIError 500: NaN")
        emb._client = client

        result = emb.encode(["bad-chunk"])
        assert result.shape == (0, 4)
        assert emb.last_failed_indices == [0]


# ─────────────────────────────────────────────────────────────────────
# Bisection on multi-row batch
# ─────────────────────────────────────────────────────────────────────


class TestBisection:
    def test_bisects_to_isolate_single_bad_chunk_in_four(self) -> None:
        """A 4-chunk batch where index 2 fails → bisection finds it."""

        emb = _make_embedder()
        client = MagicMock()
        # Mock policy: any batch containing the bad-text fails (NaN response);
        # other batches return clean rows. Index 2 in the original input is
        # the bad one ("bad").
        good_vecs_by_text = {
            "a": [1.0, 0.0, 0.0, 0.0],
            "b": [0.0, 1.0, 0.0, 0.0],
            "c": [0.0, 0.0, 0.0, 1.0],
        }

        def fake_create(*, model, input, encoding_format, dimensions):  # type: ignore[no-untyped-def]
            if "bad" in input:
                return _embeddings_response(
                    [
                        [float("nan")] * 4 if t == "bad" else good_vecs_by_text.get(t, [0] * 4)
                        for t in input
                    ]
                )
            return _embeddings_response([good_vecs_by_text[t] for t in input])

        client.embeddings.create.side_effect = fake_create
        emb._client = client

        result = emb.encode(["a", "b", "bad", "c"])
        # 3 good rows came back; "bad" got skipped.
        assert result.shape == (3, 4)
        assert emb.last_failed_indices == [2]
        # Output rows should be a, b, c in order — bisection preserves
        # original input order, dropping only the bad one.
        np.testing.assert_array_equal(result[0], good_vecs_by_text["a"])
        np.testing.assert_array_equal(result[1], good_vecs_by_text["b"])
        np.testing.assert_array_equal(result[2], good_vecs_by_text["c"])

    def test_bisects_to_isolate_two_bad_chunks(self) -> None:
        emb = _make_embedder()
        client = MagicMock()
        bad_set = {"bad-1", "bad-2"}

        def fake_create(*, model, input, encoding_format, dimensions):  # type: ignore[no-untyped-def]
            return _embeddings_response(
                [[float("nan")] * 4 if t in bad_set else [1.0, 0.0, 0.0, 0.0] for t in input]
            )

        client.embeddings.create.side_effect = fake_create
        emb._client = client

        result = emb.encode(["a", "bad-1", "b", "c", "bad-2", "d"])
        assert result.shape == (4, 4)
        assert set(emb.last_failed_indices) == {1, 4}

    def test_transport_exception_bisects_not_raises(self) -> None:
        """An ``APIError``-style raise during a batch is treated the
        same as a NaN response — bisect and recover, don't bubble."""

        emb = _make_embedder()
        client = MagicMock()
        good_vec = [0.5, 0.5, 0.5, 0.5]

        def fake_create(*, model, input, encoding_format, dimensions):  # type: ignore[no-untyped-def]
            if "boom" in input:
                raise RuntimeError("APIError 500: simulated upstream wedge")
            return _embeddings_response([good_vec for _ in input])

        client.embeddings.create.side_effect = fake_create
        emb._client = client

        result = emb.encode(["a", "b", "boom", "c", "d"])
        assert result.shape == (4, 4)
        assert emb.last_failed_indices == [2]


# ─────────────────────────────────────────────────────────────────────
# Multi-batch processing
# ─────────────────────────────────────────────────────────────────────


class TestMultiBatch:
    def test_failures_in_first_batch_dont_leak_into_second(self) -> None:
        """Each ``encode`` call resets ``last_failed_indices`` from the
        start, AND failures in one mini-batch within a single call only
        contribute their own indices — not stale ones from a previous
        call."""

        emb = _make_embedder(batch_size=4)
        client = MagicMock()

        def fake_create(*, model, input, encoding_format, dimensions):  # type: ignore[no-untyped-def]
            return _embeddings_response(
                [[float("nan")] * 4 if t == "bad" else [1.0, 1.0, 1.0, 1.0] for t in input]
            )

        client.embeddings.create.side_effect = fake_create
        emb._client = client

        # 2 mini-batches of 4. Bad chunks at orig indices 1 (first batch)
        # and 5 (second batch).
        result = emb.encode(["a", "bad", "b", "c", "d", "bad", "e", "f"])
        assert result.shape == (6, 4)
        assert set(emb.last_failed_indices) == {1, 5}

    def test_encode_resets_last_failed_indices(self) -> None:
        emb = _make_embedder()
        client = MagicMock()
        client.embeddings.create.return_value = _embeddings_response([[1.0, 0.0, 0.0, 0.0]])
        emb._client = client
        # Manually populate to simulate a stale carry-over.
        emb.last_failed_indices = [99, 100]

        emb.encode(["a"])
        assert emb.last_failed_indices == []


# ─────────────────────────────────────────────────────────────────────
# All-fail batch
# ─────────────────────────────────────────────────────────────────────


class TestAllFailBatch:
    def test_every_chunk_fails_returns_empty_with_all_indices_flagged(self) -> None:
        emb = _make_embedder()
        client = MagicMock()
        client.embeddings.create.side_effect = RuntimeError("APIError 500")
        emb._client = client

        result = emb.encode(["a", "b", "c"])
        assert result.shape == (0, 4)
        assert set(emb.last_failed_indices) == {0, 1, 2}
