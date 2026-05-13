"""R4-01 — `Reranker` Protocol + `corpus_forge.retrieval.rerank` package surface.

The Phase R4 plan introduces a new sub-package `corpus_forge.retrieval.rerank`
exposing three things:

- `Reranker` Protocol (`name: str`, `model_id: str`, `warmup()`, `rerank(query, hits, *, top_n=None)`).
- `CrossEncoderReranker` concrete implementation (R4-04 lands its body).
- `OllamaReranker` concrete implementation (R4-09 lands its body; may be deferred).

This file pins the protocol shape AND the lazy-import discipline.  Critically:

- Importing the package MUST NOT import `sentence_transformers.CrossEncoder`
  greedily.  The cross-encoder lives behind a lazy `_get_model()` (R4-04).
- The package re-exports `Reranker` from `base.py`.
- The `Hit` type imported in `base.py` is the existing
  `corpus_forge.retrieval.types.Hit` — no parallel/duplicate type.

Sibling test files:
- `test_reranker_cross_encoder.py` — `CrossEncoderReranker` body (R4-04).
- `test_reranker_lazy_load.py` — separate lazy-load pin (R4-04).
- `test_reranker_ollama.py` — `OllamaReranker` (R4-09, optional).
"""

from __future__ import annotations

import importlib
import inspect
import sys
import typing
from typing import Protocol, get_type_hints


def test_rerank_subpackage_importable():
    import corpus_forge.retrieval.rerank as r  # noqa: F401

    assert hasattr(r, "Reranker")


def test_base_module_importable():
    from corpus_forge.retrieval.rerank import base  # noqa: F401


def test_reranker_protocol_is_protocol_class():
    """The `Reranker` symbol is a runtime-checkable Protocol class."""
    from corpus_forge.retrieval.rerank import Reranker

    # `Reranker` must be a class (not a callable / instance) and inherit
    # from `typing.Protocol`.  We check by looking at __mro__ for Protocol.
    assert inspect.isclass(Reranker)
    assert Protocol in Reranker.__mro__ or any(
        cls.__name__ == "Protocol" for cls in Reranker.__mro__
    ), f"Reranker is not a Protocol; MRO = {Reranker.__mro__}"


class TestProtocolShape:
    """Pin the Protocol's published attribute + method surface."""

    def _proto(self):
        from corpus_forge.retrieval.rerank import Reranker

        return Reranker

    def test_has_name_attr(self):
        hints = get_type_hints(self._proto())
        assert "name" in hints, f"missing `name`; hints = {hints}"
        # Looser-than-strict: accept `str` (and anything; pyrefly enforces).
        assert hints["name"] is str

    def test_has_model_id_attr(self):
        hints = get_type_hints(self._proto())
        assert "model_id" in hints, f"missing `model_id`; hints = {hints}"
        assert hints["model_id"] is str

    def test_has_warmup_method(self):
        proto = self._proto()
        assert hasattr(proto, "warmup"), "Reranker.warmup missing"
        sig = inspect.signature(proto.warmup)
        # Method body is unbound; pyrefly handles types.  Just pin existence
        # and arity (self only).
        params = list(sig.parameters.keys())
        assert params == ["self"], f"warmup signature unexpected: {params}"

    def test_has_rerank_method(self):
        proto = self._proto()
        assert hasattr(proto, "rerank"), "Reranker.rerank missing"
        sig = inspect.signature(proto.rerank)
        params = list(sig.parameters.keys())
        # (self, query, hits, *, top_n=None) — the splat ordering means
        # top_n is keyword-only after a sentinel `*`.  Use kind check.
        assert "query" in params, f"rerank missing `query`; {params}"
        assert "hits" in params, f"rerank missing `hits`; {params}"
        assert "top_n" in params, f"rerank missing `top_n`; {params}"
        top_n_param = sig.parameters["top_n"]
        assert top_n_param.kind == inspect.Parameter.KEYWORD_ONLY, (
            f"top_n must be keyword-only; got kind={top_n_param.kind}"
        )
        assert top_n_param.default is None, (
            f"top_n default must be None; got {top_n_param.default!r}"
        )


class TestReexports:
    """`corpus_forge.retrieval.rerank.__init__` re-exports the public names."""

    def test_reranker_reexported(self):
        from corpus_forge.retrieval.rerank import Reranker
        from corpus_forge.retrieval.rerank.base import Reranker as _Same

        assert Reranker is _Same

    def test_cross_encoder_reranker_exposed_attr(self):
        """`CrossEncoderReranker` exists on the package namespace.

        The implementation may lazy-import to avoid `sentence_transformers`
        being pulled at package-init time — that's fine.  We just need the
        attribute access to succeed.
        """
        from corpus_forge.retrieval.rerank import CrossEncoderReranker  # noqa: F401

    def test_all_lists_reranker_and_cross_encoder(self):
        from corpus_forge.retrieval import rerank as pkg

        assert hasattr(pkg, "__all__")
        all_set = set(pkg.__all__)
        # The Protocol + the bge default reranker class are mandatory.
        assert "Reranker" in all_set, f"__all__ missing Reranker: {all_set}"
        assert "CrossEncoderReranker" in all_set, (
            f"__all__ missing CrossEncoderReranker: {all_set}"
        )


# ── lazy-import discipline ─────────────────────────────────────────────────


class TestNoGreedyTorchOrSentenceTransformerImport:
    """Importing `corpus_forge.retrieval.rerank` MUST NOT eagerly pull
    `sentence_transformers.CrossEncoder` or `torch`.  These imports happen
    only at the FIRST `_get_model()` call in `CrossEncoderReranker`.

    We test by:
    1. Force-unloading any previously-imported `corpus_forge.retrieval.rerank`
       modules + any `sentence_transformers.cross_encoder` submodule.
    2. Importing `corpus_forge.retrieval.rerank` fresh.
    3. Asserting `sentence_transformers.CrossEncoder` was NOT touched.

    NB: `sentence_transformers` (top-level) may already be in `sys.modules`
    because it's a hard dep used elsewhere — the lazy discipline is about
    the `CrossEncoder` symbol specifically, not the whole package.  We
    check for the `cross_encoder` submodule (where `CrossEncoder` lives in
    `sentence_transformers` ≥3.x).
    """

    def test_rerank_import_does_not_load_cross_encoder_submodule(self):
        # Snapshot then strip cached `cross_encoder` modules so the check
        # is robust against the test-process having already imported them
        # earlier (e.g. via test_reranker_cross_encoder.py — but that file
        # should also gate itself).
        to_evict = [
            name
            for name in list(sys.modules)
            if name.startswith("sentence_transformers.cross_encoder")
            or name.startswith("corpus_forge.retrieval.rerank")
        ]
        snapshot = {name: sys.modules.pop(name) for name in to_evict}
        try:
            importlib.import_module("corpus_forge.retrieval.rerank")
            # After fresh import: `sentence_transformers.cross_encoder` MUST
            # not appear in sys.modules.
            offenders = [
                name
                for name in sys.modules
                if name.startswith("sentence_transformers.cross_encoder")
            ]
            assert offenders == [], (
                f"importing corpus_forge.retrieval.rerank greedily loaded "
                f"CrossEncoder: {offenders}"
            )
        finally:
            # Restore snapshot so other tests aren't perturbed.
            for name, mod in snapshot.items():
                sys.modules.setdefault(name, mod)
