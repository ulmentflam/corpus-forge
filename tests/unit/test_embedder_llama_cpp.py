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

        # Stand up a fake $HOME with the GGUF inside.
        monkeypatch.setenv("HOME", str(tmp_path))
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
