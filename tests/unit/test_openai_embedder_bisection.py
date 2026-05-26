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
``OpenAIEmbedder``. Three signals trigger a recursive halve-and-
retry of the batch:

1. A NaN value in any returned vector (the SDK returns 2xx but
   the floats are NaN-laced).
2. A row-count mismatch between the input batch and the response
   (provider returns fewer or more rows than requested — we can't
   map vectors back to chunk_ids).
3. A recoverable transport-level exception
   (``openai.APIConnectionError`` / ``openai.APITimeoutError``,
   5xx, or HTTP 429).

For all three signals the batch is recursively halved until the
offending chunk(s) are isolated, then skipped. The skipped
original-text-list positions are recorded on
``embedder.last_failed_indices`` so the caller (``ingest_one`` /
``backfill_embedder``) can filter those chunk_ids out of the
``write_embeddings`` pair list. Skipped chunks stay in
``chunks_missing_embedding`` for the next ingest pass to retry.

Privacy guarantee
-----------------
The WARNING log emitted at the base case (``len(batch) == 1``)
deliberately does **not** include any text preview from the
chunk. Only non-PII metadata — ``orig_idx``, ``chars`` count, and
a sha256 prefix — appears in the log. Users ingesting personal
vaults or chat history can therefore safely surface these
warnings in shared dashboards without worrying about leakage.
The sha256 is enough to look up the chunk by hash in
``corpus.chunks`` and reproduce the failure out-of-band.

Non-recoverable errors (4xx other than 429 — auth, missing model,
bad request) are **not** bisected; they re-raise immediately so
the operator sees the real cause instead of N rounds of the same
failure.

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
        to a crawl. Pin the constructor kwarg.

        Coupling note for future maintainers
        ------------------------------------
        We monkey-patch the ``OpenAI`` symbol on the
        ``corpus_forge.embedders.openai`` module (the exact attribute
        ``_get_client`` reads) rather than ``openai.OpenAI`` directly,
        because ``_get_client`` does
        ``from openai import OpenAI`` once at module-import time and
        binds the result as ``openai_mod.OpenAI``. Patching the
        global ``openai`` package wouldn't intercept the cached
        binding. If ``_get_client`` is ever refactored to look up
        ``openai.OpenAI`` lazily, switch this spy to patch there
        instead.
        """

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
        """The WARNING log must carry enough metadata to reproduce the
        failure out-of-band, but must NOT include the chunk text (PII).

        We validate the metadata flexibly — an integer ``orig_idx``,
        an integer ``chars`` count, and a hex sha256 prefix — rather
        than asserting on literal tag substrings, so the log message
        can be rephrased without breaking the test. The hex regex
        accepts the current 12-char truncation and any reasonable
        length up to a full 64-char sha256.
        """

        import logging
        import re

        emb = _make_embedder()
        client = MagicMock()

        # Use distinctive PII-shaped content so we can also assert
        # it does NOT leak into the log.
        secret_text = "SSN: 123-45-6789 — email: alice@example.com"

        def fake_create(*, model, input, encoding_format, dimensions):  # type: ignore[no-untyped-def]
            return _embeddings_response([[float("nan"), float("nan"), float("nan"), float("nan")]])

        client.embeddings.create.side_effect = fake_create
        emb._client = client

        with caplog.at_level(logging.WARNING, logger="corpus_forge.embedders.openai"):
            result = emb.encode([secret_text])

        assert result.shape == (0, 4)
        assert emb.last_failed_indices == [0]
        warning_records = [r for r in caplog.records if "Skipping chunk" in r.message]
        assert warning_records, "expected a 'Skipping chunk' WARNING in caplog"
        log_msg = warning_records[0].message

        # Flexible metadata checks — survive reasonable rephrasings.
        assert re.search(r"orig[_\s]?idx[=:]?\s*\d+", log_msg, flags=re.IGNORECASE), log_msg
        assert re.search(r"chars[=:]?\s*\d+", log_msg, flags=re.IGNORECASE), log_msg
        assert re.search(r"sha(?:256)?[=:]?\s*[0-9a-f]{8,64}", log_msg, flags=re.IGNORECASE), (
            log_msg
        )

        # PII guard — neither the SSN nor the email may appear.
        assert "123-45-6789" not in log_msg
        assert "alice@example.com" not in log_msg
        # No raw text preview at all (PII-shaped or otherwise).
        assert "SSN" not in log_msg

    def test_single_chunk_5xx_logs_and_skips(self) -> None:
        emb = _make_embedder()
        client = MagicMock()
        # Use a typed 5xx (carries ``.status_code``) so the narrowed
        # ``_is_recoverable_exception`` policy lets it through to the
        # bisection base case. A bare RuntimeError would (correctly)
        # re-raise under the post-review policy.
        client.embeddings.create.side_effect = _FakeStatusError(500, "internal")
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
                raise _FakeStatusError(500, "simulated upstream wedge")
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
        # Typed 5xx — recoverable per the narrowed
        # ``_is_recoverable_exception`` policy.
        client.embeddings.create.side_effect = _FakeStatusError(500, "internal")
        emb._client = client

        result = emb.encode(["a", "b", "c"])
        assert result.shape == (0, 4)
        assert set(emb.last_failed_indices) == {0, 1, 2}


# ─────────────────────────────────────────────────────────────────────
# Recoverable-vs-non-recoverable triage
# ─────────────────────────────────────────────────────────────────────


class _FakeStatusError(Exception):
    """Stands in for ``openai.APIStatusError`` — carries ``.status_code``."""

    def __init__(self, status_code: int, message: str = "fake") -> None:
        super().__init__(message)
        self.status_code = status_code


class TestExceptionTriage:
    """4xx (except 429) must NOT be bisected — they're deterministic
    config / protocol errors, and re-trying them just wastes O(N)
    requests per batch on the same error. Re-raise immediately so the
    caller sees the real cause.

    5xx, 429, and connection-level errors (no ``status_code``) still
    bisect — they're recoverable.
    """

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_4xx_non_recoverable_reraises_immediately(self, status: int) -> None:
        emb = _make_embedder()
        client = MagicMock()
        client.embeddings.create.side_effect = _FakeStatusError(status, f"{status} bad")
        emb._client = client

        with pytest.raises(_FakeStatusError) as excinfo:
            emb.encode(["a", "b", "c", "d"])
        assert excinfo.value.status_code == status
        # And the SDK should have been called exactly once — no
        # bisection rounds wasted on a deterministic failure.
        assert client.embeddings.create.call_count == 1

    def test_429_is_recoverable_and_bisects(self) -> None:
        """Rate-limited is a transient condition; bisection still
        eventually lands on the per-chunk path which logs + skips."""

        emb = _make_embedder()
        client = MagicMock()
        client.embeddings.create.side_effect = _FakeStatusError(429, "rate limited")
        emb._client = client

        # No raise — bisection runs to the base case and skips.
        result = emb.encode(["a", "b"])
        assert result.shape == (0, 4)
        assert set(emb.last_failed_indices) == {0, 1}
        # Bisection: 1 (whole batch) + 2 (each half) = 3 calls.
        assert client.embeddings.create.call_count == 3

    def test_5xx_is_recoverable_and_bisects(self) -> None:
        emb = _make_embedder()
        client = MagicMock()
        client.embeddings.create.side_effect = _FakeStatusError(500, "internal")
        emb._client = client

        result = emb.encode(["a", "b"])
        assert result.shape == (0, 4)
        assert set(emb.last_failed_indices) == {0, 1}

    def test_api_connection_error_is_recoverable(self) -> None:
        """``openai.APIConnectionError`` carries no ``status_code`` but
        is a known transport-level failure — bisect, don't bubble."""

        from openai import APIConnectionError

        # ``APIConnectionError.__init__`` requires a ``request`` kwarg.
        # Side-step it via ``Exception.__init__`` so the test stays
        # focused on the type-check path in ``_is_recoverable_exception``.
        class _FakeConnError(APIConnectionError):
            def __init__(self) -> None:  # type: ignore[no-untyped-def]
                Exception.__init__(self, "fake ECONNRESET")

        emb = _make_embedder()
        client = MagicMock()
        client.embeddings.create.side_effect = _FakeConnError()
        emb._client = client

        result = emb.encode(["a"])
        assert result.shape == (0, 4)
        assert emb.last_failed_indices == [0]

    def test_api_timeout_error_is_recoverable(self) -> None:
        """``openai.APITimeoutError`` is the other known transport-level
        exception class without a ``status_code`` — also bisects."""

        from openai import APITimeoutError

        class _FakeTimeoutError(APITimeoutError):
            def __init__(self) -> None:  # type: ignore[no-untyped-def]
                Exception.__init__(self, "fake timeout")

        emb = _make_embedder()
        client = MagicMock()
        client.embeddings.create.side_effect = _FakeTimeoutError()
        emb._client = client

        result = emb.encode(["a"])
        assert result.shape == (0, 4)
        assert emb.last_failed_indices == [0]

    def test_random_exception_with_no_status_is_non_recoverable(self) -> None:
        """A status-less exception that is NOT a known transport class
        (e.g. a programming bug raising ``KeyError`` from response
        parsing) is treated as non-recoverable. Without this guard,
        the bisection would chew through O(N) calls before the
        operator saw the real error."""

        emb = _make_embedder()
        client = MagicMock()
        client.embeddings.create.side_effect = KeyError("unexpected response shape")
        emb._client = client

        with pytest.raises(KeyError):
            emb.encode(["a", "b", "c", "d"])
        # SDK was called exactly once — no bisection rounds wasted.
        assert client.embeddings.create.call_count == 1


# ─────────────────────────────────────────────────────────────────────
# Row-count mismatch
# ─────────────────────────────────────────────────────────────────────


class TestRowCountMismatch:
    """The provider must return exactly ``len(input)`` rows. If it
    doesn't, we can't map vectors back to chunk_ids and have to treat
    the batch as failed for the bisection."""

    def test_short_response_triggers_bisection(self) -> None:
        """Server returns fewer rows than asked → bisect to isolate."""

        emb = _make_embedder()
        client = MagicMock()
        # 4 inputs, 3 rows back. Bisection should still recover the
        # subset that does respond cleanly when batched alone.
        call_count = {"n": 0}

        def fake_create(*, model, input, encoding_format, dimensions):  # type: ignore[no-untyped-def]
            call_count["n"] += 1
            if len(input) == 4:
                # Top-level short response.
                return _embeddings_response([[1.0, 0.0, 0.0, 0.0]] * 3)
            return _embeddings_response([[1.0, 0.0, 0.0, 0.0]] * len(input))

        client.embeddings.create.side_effect = fake_create
        emb._client = client

        result = emb.encode(["a", "b", "c", "d"])
        # All 4 succeed once they're split into halves of 2 (no mismatch).
        assert result.shape == (4, 4)
        assert emb.last_failed_indices == []

    def test_single_chunk_short_response_skips(self) -> None:
        """A response with zero rows for a single-chunk input → skip."""

        emb = _make_embedder()
        client = MagicMock()
        client.embeddings.create.return_value = _embeddings_response([])  # 0 rows
        emb._client = client

        result = emb.encode(["alone"])
        assert result.shape == (0, 4)
        assert emb.last_failed_indices == [0]
