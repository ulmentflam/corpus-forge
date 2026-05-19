"""Phase N Wave 3 — ``Model2VecEmbedder`` unit pins.

The static-embedding "fast tier" embedder is a NEW provider keyed
``"model2vec"`` and shipped behind the optional ``[fast-tier]`` extra.
It conforms to the :class:`~corpus_forge.embedders.base.Embedder`
protocol so existing pipelines (registry, fingerprint, backend
register/search) accept it without protocol widening.

Tests pinned here:

- Class is importable from ``corpus_forge.embedders.model2vec``.
- All Protocol attributes / methods present.
- ``provider == "model2vec"`` (verbatim — the registry dispatches on
  this exact string).
- ``dimension == 256`` and ``normalized is True`` and ``distance ==
  "cosine"`` for the ``minishlab/potion-code-16M`` default.  Other
  ``model_id``s are constructable but those three identity fields are
  the load-bearing claims the fast tier rides on.
- ``encode_query`` delegates to ``encode`` (symmetric, per the
  model2vec contract).
- With ``model2vec`` mocked absent, the constructor STILL accepts the
  config (so configs round-trip on machines without ``[fast-tier]``).
  Only ``encode()`` raises a clear ``ImportError`` naming the extra.
- With a tiny fake ``StaticModel`` injected, ``encode(["hello",
  "world"])`` returns shape ``(2, 256)``.
- ``warmup()`` loads the model + does a single dummy encode.

Test isolation: no network, no real model load.  The fake
``StaticModel`` is plumbed in via ``patch`` against the lazy-load
attribute on the embedder instance — exactly the pattern
:mod:`tests.unit.test_sentence_transformers_embedder` uses.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# ── module presence ───────────────────────────────────────────────────────


def test_module_importable() -> None:
    """The provider module must import without ``model2vec`` installed.

    The ``import model2vec`` call lives inside the lazy-load branch
    of ``_load_model``, so the module-level import path is safe even
    on minimal installs.
    """
    import corpus_forge.embedders.model2vec  # noqa: F401


def test_class_importable() -> None:
    from corpus_forge.embedders.model2vec import Model2VecEmbedder  # noqa: F401


# ── identity / Protocol conformance ───────────────────────────────────────


class TestIdentity:
    """``Model2VecEmbedder`` claims about ``provider`` / ``dimension`` etc."""

    def _make(self) -> object:
        from corpus_forge.embedders.model2vec import Model2VecEmbedder

        return Model2VecEmbedder(
            name="potion-code-16M",
            model_id="minishlab/potion-code-16M",
            dimension=256,
        )

    def test_provider_string(self) -> None:
        e = self._make()
        assert getattr(e, "provider", None) == "model2vec"

    def test_name(self) -> None:
        e = self._make()
        assert e.name == "potion-code-16M"  # type: ignore[attr-defined]

    def test_model_id(self) -> None:
        e = self._make()
        assert e.model_id == "minishlab/potion-code-16M"  # type: ignore[attr-defined]

    def test_dimension_default_256(self) -> None:
        e = self._make()
        assert e.dimension == 256  # type: ignore[attr-defined]

    def test_normalized_default_true(self) -> None:
        e = self._make()
        # model2vec's StaticModel emits L2-normalised vectors; the
        # default propagates that to the metadata field.
        assert e.normalized is True  # type: ignore[attr-defined]

    def test_distance_default_cosine(self) -> None:
        e = self._make()
        assert e.distance == "cosine"  # type: ignore[attr-defined]

    def test_has_encode(self) -> None:
        e = self._make()
        assert callable(getattr(e, "encode", None))

    def test_has_encode_query(self) -> None:
        e = self._make()
        assert callable(getattr(e, "encode_query", None))

    def test_has_warmup(self) -> None:
        e = self._make()
        assert callable(getattr(e, "warmup", None))


# ── lazy import / absent extra ────────────────────────────────────────────


class TestLazyImport:
    """The ``import model2vec`` call must be lazy."""

    def test_constructor_works_without_model2vec(self) -> None:
        """Constructor must not import model2vec.

        Even on a machine missing the ``[fast-tier]`` extra, building
        the embedder from config (e.g. for ``corpus-forge doctor``
        introspection) must not raise.
        """
        from corpus_forge.embedders import model2vec as mod

        with patch.object(mod, "MODEL2VEC_AVAILABLE", False):
            embedder = mod.Model2VecEmbedder(
                name="potion-code-16M",
                model_id="minishlab/potion-code-16M",
                dimension=256,
            )
            # Identity fields still resolve cleanly.
            assert embedder.provider == "model2vec"

    def test_encode_raises_clear_importerror_without_model2vec(self) -> None:
        """``encode()`` must raise ImportError pointing at ``[fast-tier]``."""
        from corpus_forge.embedders import model2vec as mod

        embedder = mod.Model2VecEmbedder(
            name="potion-code-16M",
            model_id="minishlab/potion-code-16M",
            dimension=256,
        )
        with (
            patch.object(mod, "MODEL2VEC_AVAILABLE", False),
            pytest.raises(ImportError, match=r"fast-tier"),
        ):
            embedder.encode(["hello"])

    def test_warmup_noop_without_model2vec(self) -> None:
        """``warmup()`` is a no-op when the extra isn't installed.

        Mirrors :class:`SentenceTransformersEmbedder.warmup` — calling
        ``warmup`` on a doctor / introspection path shouldn't crash
        even if the user hasn't installed the extra yet.
        """
        from corpus_forge.embedders import model2vec as mod

        embedder = mod.Model2VecEmbedder(
            name="potion-code-16M",
            model_id="minishlab/potion-code-16M",
            dimension=256,
        )
        with patch.object(mod, "MODEL2VEC_AVAILABLE", False):
            embedder.warmup()
            assert embedder._model is None


# ── encode() with a fake StaticModel ─────────────────────────────────────


class TestEncode:
    """``encode()`` shape + call-shape pins."""

    def _build_with_fake_model(self, *, dim: int = 256) -> tuple:
        """Construct an embedder with ``_model`` already set to a fake."""
        from corpus_forge.embedders.model2vec import Model2VecEmbedder

        e = Model2VecEmbedder(
            name="potion-code-16M",
            model_id="minishlab/potion-code-16M",
            dimension=dim,
        )
        fake = MagicMock()
        # StaticModel.encode returns an (N, D) ndarray (its actual API).
        fake.encode.return_value = np.zeros((2, dim), dtype=np.float32)
        e._model = fake
        return e, fake

    def test_encode_returns_ndarray(self) -> None:
        e, _ = self._build_with_fake_model()
        out = e.encode(["hello", "world"])
        assert isinstance(out, np.ndarray)

    def test_encode_shape_two_by_dim(self) -> None:
        e, _ = self._build_with_fake_model()
        out = e.encode(["hello", "world"])
        assert out.shape == (2, 256)

    def test_encode_empty_input_returns_empty_array(self) -> None:
        e, fake = self._build_with_fake_model()
        out = e.encode([])
        # Mirror SentenceTransformersEmbedder's empty fast-path:
        # never reach the underlying model.encode().
        assert out.shape == (0, 256)
        fake.encode.assert_not_called()

    def test_encode_query_delegates_to_encode(self) -> None:
        """Symmetric model: encode_query == encode (no instruction prompt)."""
        e, fake = self._build_with_fake_model()
        # encode_query is the asymmetric-aware entry point.  For
        # model2vec it must forward to encode without a prefix.
        fake.encode.return_value = np.full((1, 256), 0.25, dtype=np.float32)
        out = e.encode_query(["any query string"])
        assert out.shape == (1, 256)
        # Underlying StaticModel.encode was called with the raw string.
        call_args = fake.encode.call_args
        # Mirror the relaxed shape check used in the ST tests — the
        # first positional arg (or `texts=` kwarg) is the list of texts.
        passed = list(call_args.args[0]) if call_args.args else list(call_args.kwargs["texts"])
        assert passed == ["any query string"]

    def test_encode_dimension_mismatch_raises(self) -> None:
        """Wrong dim on the underlying model surfaces a clear error.

        Mirrors :class:`SentenceTransformersEmbedder`'s dim-mismatch
        guard.  Keeps a misconfigured ``[[embedders]]`` block from
        silently writing bogus 128-dim vectors into a 256-dim table.
        """
        from corpus_forge.embedders.model2vec import Model2VecEmbedder

        e = Model2VecEmbedder(
            name="bad",
            model_id="minishlab/potion-code-16M",
            dimension=256,
        )
        fake = MagicMock()
        # Underlying model says 128 but config claims 256.
        fake.encode.return_value = np.zeros((1, 128), dtype=np.float32)
        e._model = fake
        with pytest.raises(ValueError, match=r"dimension"):
            e.encode(["hello"])


# ── warmup ────────────────────────────────────────────────────────────────


class TestWarmup:
    """``warmup()`` triggers the lazy load + a single dummy encode."""

    def test_warmup_loads_then_dummy_encode(self) -> None:
        from corpus_forge.embedders import model2vec as mod

        # Patch StaticModel.from_pretrained to a fake that records calls.
        fake_static_model = MagicMock()
        fake_static_model.encode.return_value = np.zeros((1, 256), dtype=np.float32)
        with (
            patch.object(mod, "MODEL2VEC_AVAILABLE", True),
            patch.object(mod, "_load_static_model", return_value=fake_static_model) as load_p,
        ):
            e = mod.Model2VecEmbedder(
                name="potion-code-16M",
                model_id="minishlab/potion-code-16M",
                dimension=256,
            )
            assert e._model is None
            e.warmup()
            # Loader called exactly once during warmup.
            assert load_p.call_count == 1
            # Dummy encode happened.
            assert fake_static_model.encode.call_count == 1
            # And the model handle is now cached on the instance.
            assert e._model is fake_static_model

    def test_warmup_idempotent(self) -> None:
        """A second ``warmup()`` call must not re-load the model."""
        from corpus_forge.embedders import model2vec as mod

        fake_static_model = MagicMock()
        fake_static_model.encode.return_value = np.zeros((1, 256), dtype=np.float32)
        with (
            patch.object(mod, "MODEL2VEC_AVAILABLE", True),
            patch.object(mod, "_load_static_model", return_value=fake_static_model) as load_p,
        ):
            e = mod.Model2VecEmbedder(
                name="potion-code-16M",
                model_id="minishlab/potion-code-16M",
                dimension=256,
            )
            e.warmup()
            e.warmup()
            assert load_p.call_count == 1
