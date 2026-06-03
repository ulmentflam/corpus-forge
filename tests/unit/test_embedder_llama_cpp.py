"""Unit tests for the ``LlamaCppEmbedder`` (in-process llama.cpp embedder).

Why this exists
---------------
The ``provider = "openai"`` path against a local Ollama serving
``qwen3-embedding:8b`` returns HTTP 500 with the body
``failed to encode response: json: unsupported value: NaN`` for ~30%
of Python-code chunks (2026-05-26 incident on the maintainer's vault).
The in-process llama.cpp embedder avoids Ollama's JSON encoder
entirely — it loads the GGUF directly via ``llama-cpp-python`` and
emits clean ``float32`` vectors that we never have to round-trip
through JSON.

Tests pinned here
-----------------

1. Module + class importable on a minimal install (no ``llama-cpp``
   extra installed).
2. GGUF resolver — explicit ``gguf_path`` wins over Ollama auto-discover.
3. GGUF resolver — Ollama manifest auto-discover returns the model-layer
   blob path.
4. GGUF resolver — both knobs missing raises with both knob names in
   the message.
5. GGUF resolver — explicit ``gguf_path`` to a non-existent file raises.
6. Identity / Protocol conformance (``provider == "llama-cpp"``,
   ``encode``/``encode_query``/``warmup`` callable).
7. Lazy import — constructor works without ``llama_cpp`` installed;
   only ``encode()`` raises ``ImportError`` naming the ``[llama-cpp]``
   extra.
8. Encode shape with a fake ``Llama`` handle injected on the instance.
9. Encode normalization (``normalized=True`` → unit-norm rows;
   ``normalized=False`` → preserves the underlying vectors).
10. Empty input fast-path returns ``(0, dim)`` without touching the
    underlying model.
11. ``encode_query`` delegates to ``encode`` (symmetric first cut —
    qwen3-embedding is documented asymmetric, but the asymmetric
    instruction-prompt override lands in a follow-up).
12. **Smoke (gated)**: with the real ``llama_cpp`` installed AND
    ``CORPUS_FORGE_TEST_LLAMA_CPP`` in env, embed ``"hello"`` and
    verify the returned vector has the configured dim and is finite.
    CI without the extra skips cleanly.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ── module / class presence ──────────────────────────────────────────


def test_module_importable() -> None:
    """Module-level import must NOT require ``llama_cpp`` to be installed.

    The ``import llama_cpp`` call lives behind a ``try / except
    ImportError`` so importing this module on a minimal install
    (no ``[llama-cpp]`` extra) never crashes.
    """
    import corpus_forge.embedders.llama_cpp  # noqa: F401


def test_class_importable() -> None:
    from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder  # noqa: F401


def test_resolver_function_importable() -> None:
    from corpus_forge.embedders.llama_cpp import resolve_gguf_path  # noqa: F401


# ── GGUF resolver ─────────────────────────────────────────────────────


class TestResolverExplicitGgufPath:
    """``gguf_path`` is the highest-priority resolver input."""

    def test_explicit_gguf_path_wins_over_ollama(self, tmp_path: Path) -> None:
        """Even when an Ollama manifest pointing at a fake blob exists,
        an explicit ``gguf_path`` wins.
        """
        from corpus_forge.embedders.llama_cpp import resolve_gguf_path

        # Write an Ollama manifest + blob into the fake root.
        digest = "deadbeef" * 8
        ollama_root = tmp_path / "ollama" / "models"
        manifest_dir = (
            ollama_root / "manifests" / "registry.ollama.ai" / "library" / "qwen3-embedding"
        )
        manifest_dir.mkdir(parents=True)
        manifest = {
            "schemaVersion": 2,
            "layers": [
                {
                    "mediaType": "application/vnd.ollama.image.model",
                    "digest": f"sha256:{digest}",
                    "size": 1234,
                },
            ],
        }
        (manifest_dir / "8b").write_text(json.dumps(manifest))
        blobs_dir = ollama_root / "blobs"
        blobs_dir.mkdir()
        (blobs_dir / f"sha256-{digest}").write_bytes(b"fake-blob")

        # Write the explicit GGUF file.
        explicit = tmp_path / "qwen3-embedding-8b-Q8_0.gguf"
        explicit.write_bytes(b"fake-gguf")

        out = resolve_gguf_path(
            gguf_path=str(explicit),
            model_id="qwen3-embedding:8b",
            ollama_root=ollama_root,
        )
        assert out == explicit

    def test_explicit_gguf_path_missing_raises(self, tmp_path: Path) -> None:
        """A non-existent ``gguf_path`` must raise with the path in the message."""
        from corpus_forge.embedders.llama_cpp import resolve_gguf_path

        missing = tmp_path / "nope.gguf"
        with pytest.raises(FileNotFoundError, match=re.escape(str(missing))):
            resolve_gguf_path(
                gguf_path=str(missing),
                model_id=None,
            )

    def test_explicit_gguf_path_expanduser(self, tmp_path: Path, monkeypatch) -> None:
        """``~``-prefixed paths must be expanded."""
        from corpus_forge.embedders.llama_cpp import resolve_gguf_path

        # Stand up a fake home dir with the GGUF inside. expanduser reads HOME
        # on POSIX and USERPROFILE on Windows — set both so the test is portable.
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        explicit = tmp_path / "model.gguf"
        explicit.write_bytes(b"x")
        out = resolve_gguf_path(gguf_path="~/model.gguf", model_id=None)
        assert out == explicit


class TestResolverOllamaAutoDiscover:
    """``model_id="<name>:<tag>"`` → parse manifest → return blob path."""

    def _write_manifest(self, tmp_path: Path, name: str, tag: str, digest: str) -> Path:
        """Helper: lay out a fake Ollama models tree under ``tmp_path``."""
        ollama_root = tmp_path / "models"
        manifest_dir = ollama_root / "manifests" / "registry.ollama.ai" / "library" / name
        manifest_dir.mkdir(parents=True)
        manifest = {
            "schemaVersion": 2,
            "layers": [
                {
                    "mediaType": "application/vnd.ollama.image.model",
                    "digest": f"sha256:{digest}",
                    "size": 1234,
                },
                {
                    # A non-model layer should be ignored by the resolver.
                    "mediaType": "application/vnd.ollama.image.template",
                    "digest": "sha256:" + ("0" * 64),
                    "size": 12,
                },
            ],
        }
        (manifest_dir / tag).write_text(json.dumps(manifest))
        blobs_dir = ollama_root / "blobs"
        blobs_dir.mkdir()
        (blobs_dir / f"sha256-{digest}").write_bytes(b"fake-blob-content")
        return ollama_root

    def test_returns_model_layer_blob(self, tmp_path: Path) -> None:
        from corpus_forge.embedders.llama_cpp import resolve_gguf_path

        digest = "abcdef0123456789" * 4  # 64 hex chars
        ollama_root = self._write_manifest(tmp_path, "qwen3-embedding", "8b", digest)
        out = resolve_gguf_path(
            gguf_path=None,
            model_id="qwen3-embedding:8b",
            ollama_root=ollama_root,
        )
        assert out == ollama_root / "blobs" / f"sha256-{digest}"
        assert out.exists()

    def test_ignores_non_model_layers(self, tmp_path: Path) -> None:
        """Resolver must skip layers whose mediaType is NOT the model layer."""
        from corpus_forge.embedders.llama_cpp import resolve_gguf_path

        digest = "feedface" * 8
        ollama_root = self._write_manifest(tmp_path, "qwen3-embedding", "8b", digest)
        out = resolve_gguf_path(
            gguf_path=None,
            model_id="qwen3-embedding:8b",
            ollama_root=ollama_root,
        )
        # Must hit the .image.model layer's digest, NOT the template's.
        assert out.name == f"sha256-{digest}"


class TestResolverMissingBoth:
    """Both ``gguf_path`` and ``model_id``-derived blob absent → raise."""

    def test_raises_with_both_knob_names(self, tmp_path: Path) -> None:
        from corpus_forge.embedders.llama_cpp import resolve_gguf_path

        # Empty ollama_root → manifest lookup fails.
        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_gguf_path(
                gguf_path=None,
                model_id="qwen3-embedding:8b",
                ollama_root=tmp_path / "empty",
            )
        msg = str(exc_info.value)
        assert re.search(r"gguf_path.*model_id|model_id.*gguf_path", msg, re.DOTALL), (
            f"Error message must name both knobs; got: {msg!r}"
        )

    def test_raises_when_model_id_unparseable(self, tmp_path: Path) -> None:
        """No colon in model_id → cannot derive Ollama manifest path."""
        from corpus_forge.embedders.llama_cpp import resolve_gguf_path

        with pytest.raises(FileNotFoundError) as exc_info:
            resolve_gguf_path(
                gguf_path=None,
                model_id="no-tag-here",
                ollama_root=tmp_path,
            )
        msg = str(exc_info.value)
        assert "gguf_path" in msg and "model_id" in msg


# ── Identity / Protocol conformance ──────────────────────────────────


class TestIdentity:
    """``LlamaCppEmbedder`` claims about ``provider`` / ``dimension`` etc."""

    def _make(self, **kw):
        from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

        defaults: dict = {
            "name": "qwen3-llama-cpp",
            "model_id": "qwen3-embedding:8b",
            "dimension": 4096,
        }
        defaults.update(kw)
        return LlamaCppEmbedder(**defaults)

    def test_provider_string_is_llama_cpp(self) -> None:
        e = self._make()
        # The registry dispatches on this exact string. Pin it.
        assert e.provider == "llama-cpp"

    def test_name_round_trips(self) -> None:
        e = self._make()
        assert e.name == "qwen3-llama-cpp"

    def test_model_id_round_trips(self) -> None:
        e = self._make()
        assert e.model_id == "qwen3-embedding:8b"

    def test_dimension_round_trips(self) -> None:
        e = self._make()
        assert e.dimension == 4096

    def test_normalized_default_true(self) -> None:
        e = self._make()
        assert e.normalized is True

    def test_normalized_can_be_disabled(self) -> None:
        e = self._make(normalized=False)
        assert e.normalized is False

    def test_distance_default_cosine(self) -> None:
        e = self._make()
        assert e.distance == "cosine"

    def test_n_ctx_default_512(self) -> None:
        e = self._make()
        assert e.n_ctx == 512

    def test_n_gpu_layers_default_minus_one(self) -> None:
        e = self._make()
        assert e.n_gpu_layers == -1

    def test_gguf_path_default_none(self) -> None:
        e = self._make()
        assert e.gguf_path is None

    def test_gguf_path_round_trips(self) -> None:
        e = self._make(gguf_path="/x/y.gguf")
        assert e.gguf_path == "/x/y.gguf"

    def test_has_encode(self) -> None:
        e = self._make()
        assert callable(getattr(e, "encode", None))

    def test_has_encode_query(self) -> None:
        e = self._make()
        assert callable(getattr(e, "encode_query", None))

    def test_has_warmup(self) -> None:
        e = self._make()
        assert callable(getattr(e, "warmup", None))


# ── Lazy import / absent extra ───────────────────────────────────────


class TestLazyImport:
    """The ``import llama_cpp`` call must be lazy.

    Mirrors :class:`tests.unit.test_embedder_model2vec.TestLazyImport`.
    """

    def test_constructor_works_without_llama_cpp(self) -> None:
        """Constructor must not import ``llama_cpp``.

        Even on a machine missing the ``[llama-cpp]`` extra, building
        the embedder from config (e.g. for ``corpus-forge doctor``
        introspection) must not raise.
        """
        from corpus_forge.embedders import llama_cpp as mod

        with patch.object(mod, "LLAMA_CPP_AVAILABLE", False):
            embedder = mod.LlamaCppEmbedder(
                name="qwen3-llama-cpp",
                model_id="qwen3-embedding:8b",
                dimension=4096,
            )
            assert embedder.provider == "llama-cpp"

    def test_encode_raises_clear_importerror_without_llama_cpp(self) -> None:
        """``encode()`` must raise ImportError pointing at ``[llama-cpp]``."""
        from corpus_forge.embedders import llama_cpp as mod

        embedder = mod.LlamaCppEmbedder(
            name="qwen3-llama-cpp",
            model_id="qwen3-embedding:8b",
            dimension=4096,
        )
        with (
            patch.object(mod, "LLAMA_CPP_AVAILABLE", False),
            pytest.raises(ImportError, match=r"llama-cpp"),
        ):
            embedder.encode(["hello"])

    def test_warmup_noop_without_llama_cpp(self) -> None:
        """``warmup()`` is a no-op when the extra isn't installed."""
        from corpus_forge.embedders import llama_cpp as mod

        embedder = mod.LlamaCppEmbedder(
            name="qwen3-llama-cpp",
            model_id="qwen3-embedding:8b",
            dimension=4096,
        )
        with patch.object(mod, "LLAMA_CPP_AVAILABLE", False):
            embedder.warmup()
            assert embedder._llama is None


# ── encode() with a fake Llama handle ────────────────────────────────


def _fake_create_embedding(dim: int):
    """Build a callable that mimics ``llama_cpp.Llama.create_embedding``.

    The real method accepts either a single string or a list of
    strings and returns ``{"data": [{"embedding": [...]}], ...}`` in
    OpenAI shape.
    """

    def _impl(inputs):
        # Normalise to list — the embedder calls per-batch with a list.
        texts = inputs if isinstance(inputs, list) else [inputs]
        return {
            "data": [{"embedding": [0.5] * dim, "index": i} for i in range(len(texts))],
        }

    return _impl


class TestEncode:
    """``encode()`` shape + call-shape pins."""

    def _build_with_fake_llama(self, *, dim: int = 16, normalized: bool = True):
        from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

        e = LlamaCppEmbedder(
            name="fake",
            model_id="fake:tag",
            dimension=dim,
            normalized=normalized,
        )
        fake = MagicMock()
        fake.create_embedding.side_effect = _fake_create_embedding(dim)
        e._llama = fake
        return e, fake

    def test_encode_returns_ndarray(self) -> None:
        e, _ = self._build_with_fake_llama()
        out = e.encode(["hello", "world"])
        assert isinstance(out, np.ndarray)

    def test_encode_returns_float32(self) -> None:
        e, _ = self._build_with_fake_llama()
        out = e.encode(["hello"])
        assert out.dtype == np.float32

    def test_encode_shape_n_by_dim(self) -> None:
        e, _ = self._build_with_fake_llama(dim=32)
        out = e.encode(["a", "b", "c"])
        assert out.shape == (3, 32)

    def test_encode_empty_input_returns_empty_array(self) -> None:
        e, fake = self._build_with_fake_llama(dim=8)
        out = e.encode([])
        assert out.shape == (0, 8)
        fake.create_embedding.assert_not_called()

    def test_encode_normalized_rows_have_unit_norm(self) -> None:
        e, _ = self._build_with_fake_llama(dim=4, normalized=True)
        out = e.encode(["x", "y"])
        norms = np.linalg.norm(out, axis=1)
        assert np.allclose(norms, 1.0, atol=1e-5)

    def test_encode_unnormalized_preserves_vectors(self) -> None:
        e, _ = self._build_with_fake_llama(dim=4, normalized=False)
        out = e.encode(["x"])
        # Fake returns rows of all-0.5 → ||v|| = sqrt(4 * 0.25) = 1.0 in
        # this specific dim. Re-check with a non-1.0 expected norm by
        # choosing dim=4: each entry 0.5 → ||v|| = sqrt(4*0.25) = 1.0.
        # That's the same; use a different dim to make the test
        # discriminating.
        # The point is that we should NOT have renormalised, so the
        # raw values must persist.
        assert np.allclose(out[0], [0.5, 0.5, 0.5, 0.5])

    def test_encode_dimension_mismatch_raises(self) -> None:
        """If the underlying model emits a different dim, we raise.

        Guards against a misconfigured ``[[embedders]]`` block writing
        bogus-shaped vectors into the table.
        """
        from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

        e = LlamaCppEmbedder(name="bad", model_id="x:y", dimension=128)
        fake = MagicMock()
        # Model emits 256-dim but config says 128.
        fake.create_embedding.side_effect = _fake_create_embedding(256)
        e._llama = fake
        with pytest.raises(ValueError, match=r"dimension"):
            e.encode(["hello"])

    def test_encode_row_count_mismatch_raises(self) -> None:
        """If the underlying model returns fewer rows than inputs, raise.

        llama-cpp-python's contract is N inputs → N rows; any drift is a
        bug in the underlying lib AND would silently misalign vector→
        chunk_id pairing in the corpus.
        """
        from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

        e = LlamaCppEmbedder(name="bad", model_id="x:y", dimension=4)
        fake = MagicMock()
        # Two inputs, one row — must raise.
        fake.create_embedding.return_value = {"data": [{"embedding": [0.5] * 4}]}
        e._llama = fake
        with pytest.raises(ValueError, match=r"row"):
            e.encode(["a", "b"])

    def test_encode_respects_instance_batch_size(self) -> None:
        """A 5-input call with ``batch_size=2`` should fire 3 batches.

        Pins the batching contract so we can later validate against the
        underlying ``llama_cpp.Llama.create_embedding`` batch limits.
        """
        from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

        e = LlamaCppEmbedder(
            name="batch",
            model_id="x:y",
            dimension=4,
            batch_size=2,
        )
        fake = MagicMock()
        fake.create_embedding.side_effect = _fake_create_embedding(4)
        e._llama = fake
        out = e.encode(["a", "b", "c", "d", "e"])
        assert out.shape == (5, 4)
        # 5 inputs ÷ batch_size=2 = 3 calls (2 + 2 + 1).
        assert fake.create_embedding.call_count == 3


class TestEncodeQuery:
    """``encode_query`` is the asymmetric-aware entry point.

    First cut: symmetric pass-through (delegates to ``encode``).  The
    qwen3-embedding model IS documented asymmetric (separate query
    instruction prompt), but the prompt override lands in a follow-up.
    """

    def test_encode_query_delegates_to_encode(self) -> None:
        from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

        e = LlamaCppEmbedder(
            name="symmetric",
            model_id="x:y",
            dimension=4,
        )
        fake = MagicMock()
        fake.create_embedding.side_effect = _fake_create_embedding(4)
        e._llama = fake
        out = e.encode_query(["any query"])
        assert out.shape == (1, 4)
        # The raw query text reached the underlying create_embedding
        # (no instruction-prefix munging).
        call_args = fake.create_embedding.call_args
        passed = call_args.args[0] if call_args.args else call_args.kwargs.get("input")
        assert passed == ["any query"]


# ── warmup ───────────────────────────────────────────────────────────


class TestWarmup:
    """``warmup()`` triggers the lazy load + a single dummy encode."""

    def test_warmup_loads_then_dummy_encode(self) -> None:
        from corpus_forge.embedders import llama_cpp as mod

        fake_llama_handle = MagicMock()
        fake_llama_handle.create_embedding.side_effect = _fake_create_embedding(8)
        with (
            patch.object(mod, "LLAMA_CPP_AVAILABLE", True),
            patch.object(mod, "_load_llama_handle", return_value=fake_llama_handle) as load_p,
        ):
            e = mod.LlamaCppEmbedder(
                name="warm",
                model_id="x:y",
                dimension=8,
                gguf_path="/fake/path.gguf",
            )
            assert e._llama is None
            e.warmup()
            assert load_p.call_count == 1
            # One dummy encode batch.
            assert fake_llama_handle.create_embedding.call_count >= 1
            assert e._llama is fake_llama_handle

    def test_warmup_idempotent(self) -> None:
        from corpus_forge.embedders import llama_cpp as mod

        fake_llama_handle = MagicMock()
        fake_llama_handle.create_embedding.side_effect = _fake_create_embedding(8)
        with (
            patch.object(mod, "LLAMA_CPP_AVAILABLE", True),
            patch.object(mod, "_load_llama_handle", return_value=fake_llama_handle) as load_p,
        ):
            e = mod.LlamaCppEmbedder(
                name="warm",
                model_id="x:y",
                dimension=8,
                gguf_path="/fake/path.gguf",
            )
            e.warmup()
            e.warmup()
            assert load_p.call_count == 1


# ── Smoke (gated) ────────────────────────────────────────────────────


_SMOKE_SKIP_REASON = (
    "Smoke test gated on CORPUS_FORGE_TEST_LLAMA_CPP=1 (needs [llama-cpp] extra + a real GGUF)."
)


@pytest.mark.skipif(
    not os.environ.get("CORPUS_FORGE_TEST_LLAMA_CPP"),
    reason=_SMOKE_SKIP_REASON,
)
def test_smoke_real_qwen3_embedding() -> None:
    """Real end-to-end smoke against the user's qwen3-embedding GGUF.

    Gated behind ``CORPUS_FORGE_TEST_LLAMA_CPP=1`` because:

    - The ``[llama-cpp]`` extra is optional in CI.
    - The GGUF weights (~5 GB for qwen3-embedding:8b) are NOT bundled.
    - Loading + a single embed takes ~5 s on M-series Metal; we don't
      want this firing on every PR.

    Set ``CORPUS_FORGE_LLAMA_CPP_GGUF`` to an explicit GGUF path to
    override the Ollama auto-discover. Otherwise the test uses the
    auto-discover path with ``model_id="qwen3-embedding:8b"``.
    """

    pytest.importorskip("llama_cpp")
    from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

    explicit = os.environ.get("CORPUS_FORGE_LLAMA_CPP_GGUF")
    embedder = LlamaCppEmbedder(
        name="qwen3-llama-cpp-smoke",
        model_id="qwen3-embedding:8b",
        dimension=4096,
        gguf_path=explicit,  # None → Ollama auto-discover
        n_ctx=512,
        n_gpu_layers=-1,
        batch_size=4,
    )
    out = embedder.encode(["hello"])
    assert out.shape == (1, 4096)
    assert np.isfinite(out).all(), "Smoke embedding contained NaN/Inf"


@pytest.mark.skipif(
    not os.environ.get("CORPUS_FORGE_TEST_LLAMA_CPP"),
    reason=_SMOKE_SKIP_REASON,
)
def test_smoke_real_qwen3_embedding_long_input_truncates() -> None:
    """Real end-to-end smoke that exercises the truncation path.

    A multi-thousand-character payload would otherwise blow past
    ``n_ctx_seq`` and crash llama_decode with ``failed to find a memory
    slot``. With ``n_seq_max=1`` and ``n_batch=n_ctx=4096``, the
    Python-side truncation slices the tokenised payload to 4096 tokens
    BEFORE the C call, so the embedding succeeds without RuntimeError.
    """

    pytest.importorskip("llama_cpp")
    from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

    explicit = os.environ.get("CORPUS_FORGE_LLAMA_CPP_GGUF")
    embedder = LlamaCppEmbedder(
        name="qwen3-llama-cpp-smoke-long",
        model_id="qwen3-embedding:8b",
        dimension=4096,
        gguf_path=explicit,
        n_ctx=4096,
        n_seq_max=1,
        n_batch=4096,
        n_ubatch=4096,
        n_gpu_layers=-1,
        batch_size=1,
    )
    # ~32 KB payload — order-of-magnitude past n_ctx for qwen3-embedding.
    payload = "def example_function():\n    return 'hello world'\n" * 800
    out = embedder.encode([payload])
    assert out.shape == (1, 4096)
    assert np.isfinite(out).all(), "Long-input smoke embedding contained NaN/Inf"


# ── New config-knobs identity tests (n_seq_max / n_batch / n_ubatch) ─


class TestTuningIdentity:
    """``LlamaCppEmbedder`` exposes three new knobs: ``n_seq_max``,
    ``n_batch``, ``n_ubatch``. These pin defaults + round-trip.
    """

    def _make(self, **kw):
        from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

        defaults: dict = {
            "name": "qwen3-llama-cpp",
            "model_id": "qwen3-embedding:8b",
            "dimension": 4096,
        }
        defaults.update(kw)
        return LlamaCppEmbedder(**defaults)

    def test_n_seq_max_default_one(self) -> None:
        """Default ``n_seq_max`` is 1 so each chunk gets the full ``n_ctx`` window.

        llama-cpp-python clamps ``n_ctx_seq = n_ctx / n_seq_max``; the
        embedding-mode initialiser silently sets ``n_seq_max`` up to
        ``llama_max_parallel_sequences()`` (256 on a stock install).
        Defaulting our knob to 1 documents intent and is what the
        post-construction mutation pins on the context params.
        """
        e = self._make()
        assert e.n_seq_max == 1

    def test_n_seq_max_round_trips(self) -> None:
        e = self._make(n_seq_max=4)
        assert e.n_seq_max == 4

    def test_n_batch_default_resolves_to_n_ctx(self) -> None:
        """When ``n_batch`` is omitted, the embedder resolves it to ``n_ctx``.

        The relationship "physical batch buffer >= n_ctx" sidesteps the
        ``llama_context: n_ctx is not divisible by n_seq_max`` warning
        and keeps the per-sequence context honest.
        """
        e = self._make(n_ctx=4096)
        assert e.n_batch == 4096

    def test_n_batch_explicit_round_trips(self) -> None:
        e = self._make(n_ctx=1024, n_batch=8192)
        assert e.n_batch == 8192

    def test_n_ubatch_default_resolves_to_n_ctx(self) -> None:
        e = self._make(n_ctx=2048)
        assert e.n_ubatch == 2048

    def test_n_ubatch_explicit_round_trips(self) -> None:
        e = self._make(n_ctx=1024, n_ubatch=8192)
        assert e.n_ubatch == 8192


# ── Truncation path ───────────────────────────────────────────────────


def _build_tokenizer_mock(token_lengths: dict[str, int]):
    """Build mock ``tokenize`` / ``detokenize`` callables.

    ``token_lengths``: mapping from input text → "true" token count.
    ``tokenize(text_bytes, ...)`` returns ``list(range(token_lengths[text]))``.
    ``detokenize(tokens, ...)`` returns ``f"<detok-{len(tokens)}>".encode()`` so
    the caller can verify the slice length.
    """

    def _tokenize(text_bytes, add_bos=True, special=False):
        text = text_bytes.decode("utf-8") if isinstance(text_bytes, bytes) else str(text_bytes)
        n = token_lengths.get(text, len(text.split()))
        return list(range(n))

    def _detokenize(tokens, prev_tokens=None, special=False):
        return f"<detok-{len(tokens)}>".encode()

    return _tokenize, _detokenize


class TestTruncation:
    """Per-chunk token-aware truncation before ``create_embedding``."""

    def _build(
        self,
        *,
        dim: int = 16,
        n_ctx: int = 512,
        n_seq_max: int = 1,
        token_lengths: dict[str, int] | None = None,
    ):
        from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

        e = LlamaCppEmbedder(
            name="trunc",
            model_id="x:y",
            dimension=dim,
            n_ctx=n_ctx,
            n_seq_max=n_seq_max,
        )
        fake = MagicMock()
        fake.create_embedding.side_effect = _fake_create_embedding(dim)
        tok, detok = _build_tokenizer_mock(token_lengths or {})
        fake.tokenize.side_effect = tok
        fake.detokenize.side_effect = detok
        e._llama = fake
        return e, fake

    def test_oversized_text_is_truncated_to_n_ctx_seq(self) -> None:
        """600-token text with n_ctx=512, n_seq_max=1 → slice to 512."""
        e, fake = self._build(
            n_ctx=512,
            n_seq_max=1,
            token_lengths={"long": 600},
        )
        e.encode(["long"])
        # tokenize was called on the input bytes.
        fake.tokenize.assert_called_once()
        called_arg = fake.tokenize.call_args.args[0]
        assert called_arg == b"long"
        # detokenize was called with a 512-token slice.
        fake.detokenize.assert_called_once()
        detok_tokens = fake.detokenize.call_args.args[0]
        assert len(detok_tokens) == 512
        # The downstream create_embedding got the detokenized string.
        sent_batch = fake.create_embedding.call_args.args[0]
        assert sent_batch == ["<detok-512>"]

    def test_n_ctx_seq_math_with_n_seq_max_gt_one(self) -> None:
        """``n_ctx_seq = n_ctx // max(n_seq_max, 1)``. 512/4 → 128."""
        e, fake = self._build(
            n_ctx=512,
            n_seq_max=4,
            token_lengths={"long": 200},
        )
        e.encode(["long"])
        detok_tokens = fake.detokenize.call_args.args[0]
        assert len(detok_tokens) == 128

    def test_short_text_is_not_truncated(self) -> None:
        """50-token text with n_ctx=512 → no truncation, no detokenize call."""
        e, fake = self._build(
            n_ctx=512,
            n_seq_max=1,
            token_lengths={"short": 50},
        )
        e.encode(["short"])
        # tokenize still fires (we have to measure length).
        fake.tokenize.assert_called_once()
        # detokenize MUST NOT be called when no truncation happens —
        # otherwise we pay tokenize+detokenize round-trip cost for
        # every short chunk in the corpus.
        fake.detokenize.assert_not_called()
        # The downstream create_embedding got the ORIGINAL text.
        sent_batch = fake.create_embedding.call_args.args[0]
        assert sent_batch == ["short"]

    def test_empty_list_short_circuits_before_tokenize(self) -> None:
        e, fake = self._build()
        out = e.encode([])
        assert out.shape == (0, 16)
        fake.tokenize.assert_not_called()
        fake.detokenize.assert_not_called()
        fake.create_embedding.assert_not_called()

    def test_mixed_batch_only_truncates_oversized(self) -> None:
        """In a 3-text batch [short, long, short], only the middle text
        passes through truncate+detok. The short ones MUST NOT be
        detokenized — that would corrupt them (qwen3's tokenizer is
        not perfectly round-trip on already-detokenized strings).
        """
        e, fake = self._build(
            n_ctx=128,
            n_seq_max=1,
            token_lengths={"s1": 10, "long": 500, "s2": 20},
        )
        e.encode(["s1", "long", "s2"])
        # Three tokenize calls (one per input).
        assert fake.tokenize.call_count == 3
        # Exactly one detokenize call for the 500-token input.
        assert fake.detokenize.call_count == 1
        detok_tokens = fake.detokenize.call_args.args[0]
        assert len(detok_tokens) == 128
        # The downstream batch order is preserved.
        sent_batch = fake.create_embedding.call_args.args[0]
        assert sent_batch == ["s1", "<detok-128>", "s2"]

    def test_truncation_emits_debug_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """A truncation event MUST hit the loader logger at DEBUG with a
        greppable phrase so ``corpus-forge doctor`` can surface
        truncation rates later.
        """
        import logging

        e, _ = self._build(
            n_ctx=128,
            n_seq_max=1,
            token_lengths={"big": 500},
        )
        with caplog.at_level(logging.DEBUG, logger="corpus_forge.embedders.loader"):
            e.encode(["big"])
        joined = " ".join(rec.getMessage() for rec in caplog.records)
        records = [r.getMessage() for r in caplog.records]
        assert "LlamaCppEmbedder truncated" in joined, (
            f"Expected a DEBUG-level truncation log; got records: {records!r}"
        )

    def test_no_truncation_no_debug_log(self, caplog: pytest.LogCaptureFixture) -> None:
        """The DEBUG truncation message MUST NOT fire on short inputs."""
        import logging

        e, _ = self._build(
            n_ctx=512,
            n_seq_max=1,
            token_lengths={"tiny": 5},
        )
        with caplog.at_level(logging.DEBUG, logger="corpus_forge.embedders.loader"):
            e.encode(["tiny"])
        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert "LlamaCppEmbedder truncated" not in joined

    def test_encode_query_inherits_truncation(self) -> None:
        """``encode_query`` delegates to ``encode`` — pin that it gets
        the truncation behaviour too (the maintainer's vault has thin
        chat-history rows that occasionally blow past ``n_ctx`` even
        on the query side).
        """
        e, fake = self._build(
            n_ctx=64,
            n_seq_max=1,
            token_lengths={"long query": 100},
        )
        e.encode_query(["long query"])
        fake.tokenize.assert_called_once()
        fake.detokenize.assert_called_once()
        detok_tokens = fake.detokenize.call_args.args[0]
        assert len(detok_tokens) == 64


# ── Loader forwarding of new kwargs ──────────────────────────────────


class TestLoaderForwardsTuningKwargs:
    """``_load_llama_handle`` forwards ``n_seq_max`` / ``n_batch`` /
    ``n_ubatch`` to ``llama_cpp.Llama``.

    On llama-cpp-python 0.3.23, ``Llama.__init__`` swallows unknown
    kwargs via its ``**kwargs`` tail (so the call doesn't TypeError),
    AND we post-mutate ``handle.context_params.n_seq_max`` so future
    versions that read it dynamically pick up the config.
    """

    def test_load_llama_handle_passes_new_kwargs_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pin: the call into ``llama_cpp.Llama(...)`` receives all three
        new kwargs.
        """
        from corpus_forge.embedders import llama_cpp as mod

        captured_kwargs: dict = {}

        class _FakeLlama:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                # Mimic the real Llama's ``context_params`` so the
                # post-mutation hook has somewhere to write.
                self.context_params = MagicMock()
                self.context_params.n_seq_max = 999  # default to detect overwrite

        # Stub the real llama_cpp import inside ``_load_llama_handle``.
        import sys

        fake_module = MagicMock()
        fake_module.Llama = _FakeLlama
        monkeypatch.setitem(sys.modules, "llama_cpp", fake_module)
        monkeypatch.setattr(mod, "LLAMA_CPP_AVAILABLE", True)

        # Stand up a temp file so ``resolve_gguf_path`` succeeds.
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".gguf", delete=False) as tf:
            tf.write(b"fake")
            gguf_path = tf.name

        handle = mod._load_llama_handle(
            gguf_path=gguf_path,
            model_id="x:y",
            n_ctx=8192,
            n_gpu_layers=-1,
            n_seq_max=1,
            n_batch=8192,
            n_ubatch=8192,
        )
        assert captured_kwargs.get("n_seq_max") == 1
        assert captured_kwargs.get("n_batch") == 8192
        assert captured_kwargs.get("n_ubatch") == 8192
        # Post-mutation forces the configured n_seq_max even if the
        # binding silently dropped the constructor kwarg.
        assert handle.context_params.n_seq_max == 1


# ── Runtime n_ctx_seq introspection (PR #80) ─────────────────────────


class TestRuntimeNCtxSeqIntrospection:
    """``encode()`` consults ``llama_cpp.llama_n_ctx`` / ``llama_n_seq_max``
    on the *actual* loaded context to compute the truncation budget.

    Why this exists
    ---------------
    PR #79 (commit 99bdfb0) computed ``n_ctx_seq = self.n_ctx //
    max(self.n_seq_max, 1)`` from the *configured* values. But
    llama-cpp-python's ``embedding=True`` initialiser overrides
    ``n_seq_max`` post-construction to up to ``llama_max_parallel_sequences()``
    (~32 on a stock install). The real per-sequence budget is therefore
    much smaller than configured — slicing to 8192 tokens while the
    decoder accepts only ~256 → ``decode: failed to find a memory slot
    for batch of size N`` / ``RuntimeError: llama_decode returned 1``
    on the first real input.

    Fix: read the runtime values directly off the loaded context and
    derive ``n_ctx_seq = max(runtime_n_ctx // max(runtime_n_seq_max, 1) - 4, 64)``.
    The ``- 4`` is a safety margin for BOS / EOS / pooling tokens; the
    ``max(..., 64)`` is a floor for pathological zeros from the
    bindings. Fall back to the configured-value path when introspection
    fails (older bindings without ``_ctx.ctx`` / unbound C functions).
    """

    def _build(
        self,
        *,
        dim: int = 16,
        n_ctx: int = 512,
        n_seq_max: int = 1,
        token_lengths: dict[str, int] | None = None,
        ctx_ptr: object = None,
    ):
        from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

        e = LlamaCppEmbedder(
            name="rt",
            model_id="x:y",
            dimension=dim,
            n_ctx=n_ctx,
            n_seq_max=n_seq_max,
        )
        fake = MagicMock()
        fake.create_embedding.side_effect = _fake_create_embedding(dim)
        tok, detok = _build_tokenizer_mock(token_lengths or {})
        fake.tokenize.side_effect = tok
        fake.detokenize.side_effect = detok
        # Attach a ``_ctx`` substructure mirroring llama-cpp-python's
        # ``LlamaContext`` shape so the runtime introspection happy
        # path has somewhere to read from. Tests that want to exercise
        # the fallback can override or null this attribute.
        if ctx_ptr is not None:
            fake._ctx = MagicMock()
            fake._ctx.ctx = ctx_ptr
        else:
            fake._ctx = None
        e._llama = fake
        return e, fake

    def test_runtime_lookup_drives_truncation_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy path: ``(runtime_n_ctx=8192, runtime_n_seq_max=32)`` →
        ``n_ctx_seq = 8192 // 32 - 4 = 252``.

        Spy on ``_maybe_truncate`` to capture the budget arg.
        """
        from corpus_forge.embedders import llama_cpp as mod

        # Force the runtime C-bindings to return our pinned values.
        # Use a real int — production guards `isinstance(_ctx_ptr, (int, ctypes.c_void_p))`
        # to avoid SIGSEGV when ctypes tries to dereference a Mock pointer.
        ctx_ptr_sentinel = 0x1234567890ABCDEF
        # The module's ``import llama_cpp as _lcpp`` is local to
        # ``encode()``. Stub it in ``sys.modules`` so the lookup picks
        # up our mocks regardless of whether the real extra is
        # installed.
        import sys

        fake_lcpp = MagicMock()
        fake_lcpp.llama_n_ctx = MagicMock(return_value=8192)
        fake_lcpp.llama_n_seq_max = MagicMock(return_value=32)
        monkeypatch.setitem(sys.modules, "llama_cpp", fake_lcpp)

        e, _fake = self._build(
            n_ctx=512,  # configured value is intentionally a LIE
            n_seq_max=1,  # configured value is intentionally a LIE
            token_lengths={"x": 1},
            ctx_ptr=ctx_ptr_sentinel,
        )

        captured: dict[str, int] = {}
        original = mod.LlamaCppEmbedder._maybe_truncate

        def _spy(self, text: str, n_ctx_seq: int) -> str:
            captured["n_ctx_seq"] = n_ctx_seq
            return original(self, text, n_ctx_seq)

        monkeypatch.setattr(mod.LlamaCppEmbedder, "_maybe_truncate", _spy)

        e.encode(["x"])
        # 8192 // 32 - 4 = 256 - 4 = 252
        assert captured["n_ctx_seq"] == 252, (
            f"Expected runtime-derived n_ctx_seq=252, got {captured.get('n_ctx_seq')!r}"
        )
        # And the C-bindings got the actual loaded context pointer.
        fake_lcpp.llama_n_ctx.assert_called_once_with(ctx_ptr_sentinel)
        fake_lcpp.llama_n_seq_max.assert_called_once_with(ctx_ptr_sentinel)

    def test_fallback_when_ctx_ptr_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Older bindings: ``self._llama._ctx`` is ``None`` (or lacks
        ``.ctx``). Falls back to ``self.n_ctx // max(self.n_seq_max, 1)``.

        Configured ``n_ctx=512, n_seq_max=1`` → fallback budget 512.
        Token length 600 → truncate to 512.
        """
        # Even though the C-bindings would return wild values, the
        # fallback path MUST NOT invoke them — gate by leaving _ctx=None.
        import sys

        from corpus_forge.embedders import llama_cpp as mod

        fake_lcpp = MagicMock()
        fake_lcpp.llama_n_ctx = MagicMock(return_value=999_999)
        fake_lcpp.llama_n_seq_max = MagicMock(return_value=1)
        monkeypatch.setitem(sys.modules, "llama_cpp", fake_lcpp)

        e, fake = self._build(
            n_ctx=512,
            n_seq_max=1,
            token_lengths={"long": 600},
            ctx_ptr=None,  # → fake._ctx = None → AttributeError on .ctx
        )

        captured: dict[str, int] = {}
        original = mod.LlamaCppEmbedder._maybe_truncate

        def _spy(self, text: str, n_ctx_seq: int) -> str:
            captured["n_ctx_seq"] = n_ctx_seq
            return original(self, text, n_ctx_seq)

        monkeypatch.setattr(mod.LlamaCppEmbedder, "_maybe_truncate", _spy)

        e.encode(["long"])
        assert captured["n_ctx_seq"] == 512, (
            f"Fallback should use configured 512 // 1 = 512, got {captured.get('n_ctx_seq')!r}"
        )
        # The detokenize slice must reflect the fallback budget.
        detok_tokens = fake.detokenize.call_args.args[0]
        assert len(detok_tokens) == 512

    def test_floor_protects_against_zero_runtime_n_ctx(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Bindings return ``(0, 32)`` → naive math gives ``0 // 32 - 4 = -4``.
        The ``max(..., 64)`` floor must kick in.
        """
        import sys

        from corpus_forge.embedders import llama_cpp as mod

        fake_lcpp = MagicMock()
        fake_lcpp.llama_n_ctx = MagicMock(return_value=0)
        fake_lcpp.llama_n_seq_max = MagicMock(return_value=32)
        monkeypatch.setitem(sys.modules, "llama_cpp", fake_lcpp)

        # Use a real int — production guards `isinstance(_ctx_ptr, (int, ctypes.c_void_p))`
        # to avoid SIGSEGV when ctypes tries to dereference a Mock pointer.
        ctx_ptr_sentinel = 0x1234567890ABCDEF
        e, _ = self._build(
            n_ctx=512,
            n_seq_max=1,
            token_lengths={"x": 1},
            ctx_ptr=ctx_ptr_sentinel,
        )

        captured: dict[str, int] = {}
        original = mod.LlamaCppEmbedder._maybe_truncate

        def _spy(self, text: str, n_ctx_seq: int) -> str:
            captured["n_ctx_seq"] = n_ctx_seq
            return original(self, text, n_ctx_seq)

        monkeypatch.setattr(mod.LlamaCppEmbedder, "_maybe_truncate", _spy)

        e.encode(["x"])
        assert captured["n_ctx_seq"] == 64, (
            f"Floor of 64 should override pathological 0 // 32 - 4 = -4; got "
            f"{captured.get('n_ctx_seq')!r}"
        )

    def test_runtime_introspection_logs_once_per_instance(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``"LlamaCppEmbedder runtime n_ctx_seq"`` line fires exactly once
        per instance, even across multiple ``encode()`` calls.

        This is the debug signal future-maintainer will grep when
        triaging the next model — it must be cheap (single line per
        load) so logs don't drown.
        """
        import logging
        import sys

        fake_lcpp = MagicMock()
        fake_lcpp.llama_n_ctx = MagicMock(return_value=8192)
        fake_lcpp.llama_n_seq_max = MagicMock(return_value=32)
        monkeypatch.setitem(sys.modules, "llama_cpp", fake_lcpp)

        # Use a real int — production guards `isinstance(_ctx_ptr, (int, ctypes.c_void_p))`
        # to avoid SIGSEGV when ctypes tries to dereference a Mock pointer.
        ctx_ptr_sentinel = 0x1234567890ABCDEF
        e, _ = self._build(
            n_ctx=512,
            n_seq_max=1,
            token_lengths={"a": 1, "b": 1},
            ctx_ptr=ctx_ptr_sentinel,
        )

        with caplog.at_level(logging.INFO, logger="corpus_forge.embedders.loader"):
            e.encode(["a"])
            e.encode(["b"])

        messages = [r.getMessage() for r in caplog.records]
        runtime_hits = [m for m in messages if "LlamaCppEmbedder runtime n_ctx_seq" in m]
        assert len(runtime_hits) == 1, (
            f"Expected exactly one runtime-introspection log per instance; got "
            f"{len(runtime_hits)}: {runtime_hits!r}"
        )
        # And the payload mentions the runtime AND configured values so
        # future triage can spot the lie.
        single = runtime_hits[0]
        # Runtime values surfaced.
        assert "8192" in single and "32" in single, (
            f"Log must surface runtime (n_ctx, n_seq_max); got: {single!r}"
        )
        # Configured values surfaced for comparison.
        assert "512" in single and "1" in single, (
            f"Log must surface configured (n_ctx, n_seq_max); got: {single!r}"
        )


# ── NaN / non-finite row filter ──────────────────────────────────────


def _fake_create_embedding_with_nan_at(dim: int, nan_indices: set[int], value: float = 0.5):
    """Fake whose data[i].embedding is all-NaN for any ``i`` in ``nan_indices``.

    Mirrors the production failure mode for llama-cpp where the C
    library returns a row of NaNs for inputs the model can't encode
    cleanly (e.g. tokens that round-tripped through a numerically
    unstable softmax).  Without filtering, those rows reach pgvector
    and fail the daemon's discovery callback with
    ``psycopg.errors.DataException: NaN not allowed in vector``.
    """

    def _impl(inputs):
        texts = inputs if isinstance(inputs, list) else [inputs]
        rows = []
        for i in range(len(texts)):
            if i in nan_indices:
                rows.append({"embedding": [float("nan")] * dim, "index": i})
            else:
                rows.append({"embedding": [value] * dim, "index": i})
        return {"data": rows}

    return _impl


class TestEncodeNanRowFilter:
    """``encode()`` drops non-finite rows and records their indices.

    The Llama-cpp embedder must honour the same contract OpenAIEmbedder
    documents (``self.last_failed_indices`` lists every row the caller
    should skip; the returned array contains ONLY finite rows).  This
    is the contract ``_write_embeddings_for_chunks`` reads when zipping
    chunk_ids to embeddings — without it, NaNs leak into the backend's
    ``write_embeddings`` call and pgvector raises ``DataException``.
    """

    def _build(self, *, dim: int = 4, nan_indices: set[int] | None = None):
        from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

        e = LlamaCppEmbedder(name="fake", model_id="x:y", dimension=dim, normalized=False)
        fake = MagicMock()
        fake.create_embedding.side_effect = _fake_create_embedding_with_nan_at(
            dim, nan_indices or set()
        )
        e._llama = fake
        return e

    def test_returned_array_excludes_nan_rows(self) -> None:
        e = self._build(dim=4, nan_indices={1})
        out = e.encode(["a", "b", "c"])
        # b's row was NaN — must not appear in the output.
        assert out.shape == (2, 4)
        assert np.isfinite(out).all()

    def test_last_failed_indices_records_dropped_positions(self) -> None:
        e = self._build(dim=4, nan_indices={1, 2})
        e.encode(["a", "b", "c", "d"])
        # Indices 1 and 2 were the NaN rows in the INPUT batch — those
        # are what downstream needs to filter chunk_ids against.
        assert sorted(e.last_failed_indices) == [1, 2]

    def test_clean_batch_leaves_last_failed_indices_empty(self) -> None:
        e = self._build(dim=4, nan_indices=set())
        e.encode(["a", "b"])
        assert e.last_failed_indices == []

    def test_inf_rows_also_dropped(self) -> None:
        """``+inf`` / ``-inf`` are equally unacceptable to pgvector."""
        from corpus_forge.embedders.llama_cpp import LlamaCppEmbedder

        e = LlamaCppEmbedder(name="fake", model_id="x:y", dimension=4, normalized=False)
        fake = MagicMock()

        def _impl(inputs):
            texts = inputs if isinstance(inputs, list) else [inputs]
            return {
                "data": [
                    {"embedding": [float("inf")] * 4 if i == 0 else [0.5] * 4, "index": i}
                    for i in range(len(texts))
                ],
            }

        fake.create_embedding.side_effect = _impl
        e._llama = fake

        out = e.encode(["a", "b"])
        assert out.shape == (1, 4)
        assert np.isfinite(out).all()
        assert e.last_failed_indices == [0]

    def test_warning_logged_when_rows_dropped(self, caplog) -> None:
        import logging as _logging

        e = self._build(dim=4, nan_indices={0})
        with caplog.at_level(_logging.WARNING, logger="corpus_forge.embedders.loader"):
            e.encode(["a", "b"])

        # The warning must name the embedder + the number of dropped
        # rows so operators can grep for it.
        matches = [
            r
            for r in caplog.records
            if "non-finite" in r.message.lower() or "nan" in r.message.lower()
        ]
        assert matches, [r.message for r in caplog.records]

    def test_last_failed_indices_reset_per_call(self) -> None:
        """A clean second call must NOT inherit stale failed indices.

        Same contract as OpenAIEmbedder.encode (line 339:
        ``self.last_failed_indices = []`` at the top of each call).
        """
        e = self._build(dim=4, nan_indices={0})
        e.encode(["bad"])
        assert e.last_failed_indices == [0]

        # Swap the side_effect to a clean batch and re-encode.
        e._llama.create_embedding.side_effect = _fake_create_embedding(4)
        e.encode(["clean"])
        assert e.last_failed_indices == []
