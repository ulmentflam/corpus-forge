"""R4-04 (lazy-load slice) — `CrossEncoderReranker` must never download
the BAAI/bge-reranker-v2-m3 model during normal Python imports.

This is the SAFETY GATE for CI runners: the bge-reranker-v2-m3 archive
is ~600 MB.  CI grabbing it on every cold run would be a disaster.

Distinct from ``test_reranker_protocol.py::TestNoGreedyTorchOrSentenceTransformerImport``
which pins the SUB-PACKAGE level discipline (importing
``corpus_forge.retrieval.rerank``).  This file pins the CLASS level:

- Instantiating ``CrossEncoderReranker()`` (with the default model_id)
  triggers ZERO calls to ``sentence_transformers.CrossEncoder``.
- The `_model` attribute is None right after construction.
- Calling `_get_model` exactly once memoises the result.
"""

from __future__ import annotations

import sys
from unittest.mock import patch


def test_module_import_does_not_load_cross_encoder():
    """Importing the module containing ``CrossEncoderReranker`` does NOT
    pull ``sentence_transformers.CrossEncoder``.

    Snapshots `sys.modules` before evicting + re-importing so other tests
    in the suite continue to share the same `CrossEncoderReranker` class
    object (otherwise `patched_cross_encoder` fixtures would patch a
    different class than the tests import from `corpus_forge.retrieval.rerank.cross_encoder`).
    """
    to_evict = [
        name
        for name in list(sys.modules)
        if name.startswith("sentence_transformers.cross_encoder")
        or name.startswith("corpus_forge.retrieval.rerank.cross_encoder")
    ]
    snapshot = {name: sys.modules.pop(name) for name in to_evict}
    try:
        import corpus_forge.retrieval.rerank.cross_encoder  # noqa: F401

        offenders = [n for n in sys.modules if n.startswith("sentence_transformers.cross_encoder")]
        assert offenders == [], f"importing cross_encoder.py greedily loaded CE: {offenders}"
    finally:
        # Restore the original module objects so other tests' references
        # (e.g. `from corpus_forge.retrieval.rerank.cross_encoder import
        # CrossEncoderReranker`) still target the original class.
        for name, mod in snapshot.items():
            sys.modules[name] = mod


def test_default_instantiation_does_not_load_model():
    """Constructing ``CrossEncoderReranker()`` (default bge model) must
    not download the 600 MB archive."""
    from corpus_forge.retrieval.rerank.cross_encoder import CrossEncoderReranker

    with patch.object(CrossEncoderReranker, "_get_model") as mock:
        r1 = CrossEncoderReranker()
        r2 = CrossEncoderReranker(model_id="custom/m")
        r3 = CrossEncoderReranker(batch_size=64, max_length=256, device="cpu")
        assert mock.call_count == 0, (
            f"default __init__ must not call _get_model; was called {mock.call_count}x"
        )
        # `_model` attribute initialised to None — proof that no caching
        # has happened.
        assert r1._model is None
        assert r2._model is None
        assert r3._model is None


def test_repeated_rerank_calls_memoise_model():
    """First `rerank` triggers one model load; subsequent calls reuse it."""
    from corpus_forge.retrieval.rerank.cross_encoder import CrossEncoderReranker
    from corpus_forge.retrieval.types import Hit

    def _hit(cid: int) -> Hit:
        return Hit(
            chunk_id=cid,
            score=0.5,
            text=f"chunk {cid}",
            document_id=None,
            source_uri=None,
            title=None,
            dataset_id=1,
            metadata={},
            source="fused",
        )

    # Patch _get_model to return a stub once, then assert it's called once
    # across multiple rerank() invocations.
    call_counter = {"n": 0}

    class _StubModel:
        def predict(self, pairs, *, batch_size=None):
            return [0.5] * len(pairs)

    def _fake_get_model(self):
        if self._model is None:
            call_counter["n"] += 1
            self._model = _StubModel()
        return self._model

    with patch.object(CrossEncoderReranker, "_get_model", _fake_get_model):
        r = CrossEncoderReranker()
        r.rerank("q1", [_hit(1)])
        r.rerank("q2", [_hit(2)])
        r.rerank("q3", [_hit(3), _hit(4)])

    assert call_counter["n"] == 1, (
        f"Expected exactly one model construction; got {call_counter['n']}"
    )


def test_warmup_then_rerank_does_not_reload():
    """Calling `warmup()` then `rerank()` triggers exactly one model load."""
    from corpus_forge.retrieval.rerank.cross_encoder import CrossEncoderReranker
    from corpus_forge.retrieval.types import Hit

    call_counter = {"n": 0}

    class _StubModel:
        def predict(self, pairs, *, batch_size=None):
            return [0.5] * len(pairs)

    def _fake_get_model(self):
        if self._model is None:
            call_counter["n"] += 1
            self._model = _StubModel()
        return self._model

    def _hit(cid: int) -> Hit:
        return Hit(
            chunk_id=cid,
            score=0.5,
            text=f"chunk {cid}",
            document_id=None,
            source_uri=None,
            title=None,
            dataset_id=1,
            metadata={},
            source="fused",
        )

    with patch.object(CrossEncoderReranker, "_get_model", _fake_get_model):
        r = CrossEncoderReranker()
        r.warmup()
        r.rerank("q", [_hit(1)])
        r.rerank("q", [_hit(2)])

    assert call_counter["n"] == 1
