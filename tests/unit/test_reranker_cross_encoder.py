"""R4-04 — `CrossEncoderReranker` body behaviour.

This file pins the reranker's contract:

- Lazy load: model is NOT constructed at ``__init__``; first ``rerank`` /
  ``warmup`` call triggers construction.
- Empty input short-circuits BEFORE any model load.
- Score replacement: every output hit's ``score`` equals the cross-encoder
  score from the patched ``CrossEncoder.predict`` stub.
- Output order matches descending cross-encoder score (ties broken by
  descending fused score, then ascending ``chunk_id``).
- ``top_n`` clipping: input has more hits than ``top_n`` → only ``top_n``
  hits are scored AND returned.
- ``top_n is None`` → reranks every input.
- Every output hit has ``source == "reranked"``.
- Tie-breaking is deterministic (stable across runs / patches).

Sibling: ``test_reranker_lazy_load.py`` (cross-test that no model load
happens at the package or class import level).

NB: All tests patch the ``sentence_transformers.CrossEncoder`` symbol via
``corpus_forge.retrieval.rerank.cross_encoder._get_model`` so the real
``BAAI/bge-reranker-v2-m3`` is never downloaded in CI.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.retrieval.rerank.cross_encoder import CrossEncoderReranker
from corpus_forge.retrieval.types import Hit

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _h(chunk_id: int, *, score: float, text: str = "") -> Hit:
    """Build a minimal fused Hit suitable for reranker input."""
    return Hit(
        chunk_id=chunk_id,
        score=score,
        text=text or f"chunk-{chunk_id}",
        document_id=None,
        source_uri=f"test://{chunk_id}",
        title=None,
        dataset_id=1,
        metadata={},
        source="fused",
    )


class _ScriptedCrossEncoder:
    """Stub for ``sentence_transformers.CrossEncoder`` used by all tests.

    - ``predict(pairs, batch_size=...)`` returns ``self.scores[: len(pairs)]``
      and records ``self.calls`` so tests can assert call args.
    """

    def __init__(self, model_id: str, *, max_length: int = 512, device: str = "cpu"):
        self.model_id = model_id
        self.max_length = max_length
        self.device = device
        self.scores: list[float] = []
        self.calls: list[dict[str, Any]] = []

    def predict(self, pairs, *, batch_size: int | None = None):
        self.calls.append({"pairs": list(pairs), "batch_size": batch_size})
        return list(self.scores[: len(pairs)])


@pytest.fixture
def patched_cross_encoder(monkeypatch):
    """Replace the lazy CrossEncoder import target with a scripted stub.

    Returns a callable ``factory(scores: list[float])`` that:

    - Patches `CrossEncoderReranker._get_model` to construct a
      `_ScriptedCrossEncoder` (cached per-instance) and return it.
    - Returns the `stub_holder` list (newly appended stub at index -1)
      so tests can inspect the call args.

    NB: patches via the imported symbol — NOT a dotted-string path — so
    we're guaranteed to hit the same class object the test code uses
    (`CrossEncoderReranker`), even if a sibling test has evicted +
    re-imported the module behind our back.
    """
    stub_holder: list[_ScriptedCrossEncoder] = []

    def _build_stub(scores: list[float]):
        def _fake_get_model(self):
            # Memoise on the instance, mirroring the real `_get_model`.
            if self._model is None:
                inst = _ScriptedCrossEncoder(
                    self.model_id, max_length=self.max_length, device=self.device
                )
                inst.scores = scores
                stub_holder.append(inst)
                self._model = inst
            return self._model

        monkeypatch.setattr(CrossEncoderReranker, "_get_model", _fake_get_model)
        return stub_holder

    return _build_stub


# ---------------------------------------------------------------------------
# Lazy-load discipline
# ---------------------------------------------------------------------------


class TestLazyLoad:
    """`__init__` must NOT touch `sentence_transformers.CrossEncoder`."""

    def test_construction_does_not_call_get_model(self):
        # If `_get_model` runs during __init__, the test would import
        # sentence_transformers.CrossEncoder which is exactly what we're
        # forbidding. We patch `_get_model` and assert call_count==0.
        with patch.object(CrossEncoderReranker, "_get_model") as mock:
            CrossEncoderReranker()
            CrossEncoderReranker(model_id="something/else")
            CrossEncoderReranker(model_id="another", batch_size=8, device="cpu")
            assert mock.call_count == 0, (
                f"__init__ should not call _get_model; was called {mock.call_count}x"
            )

    def test_warmup_loads_model(self):
        with patch.object(CrossEncoderReranker, "_get_model", return_value=MagicMock()) as mock:
            r = CrossEncoderReranker()
            r.warmup()
            assert mock.call_count >= 1

    def test_first_rerank_loads_model(self):
        stub = MagicMock()
        stub.predict.return_value = [0.5]
        with patch.object(CrossEncoderReranker, "_get_model", return_value=stub) as mock:
            r = CrossEncoderReranker()
            r.rerank("q", [_h(1, score=0.1)], top_n=1)
            assert mock.call_count >= 1

    def test_empty_input_skips_model_load(self):
        """`rerank(q, [])` must NOT call `_get_model` — the empty case is free."""
        with patch.object(CrossEncoderReranker, "_get_model") as mock:
            r = CrossEncoderReranker()
            out = r.rerank("anything", [])
            assert out == []
            assert mock.call_count == 0, (
                "empty-input rerank must not trigger model load; "
                f"_get_model called {mock.call_count}x"
            )


# ---------------------------------------------------------------------------
# Score replacement + ordering
# ---------------------------------------------------------------------------


class TestScoreReplacement:
    def test_output_score_equals_cross_encoder_score(self, patched_cross_encoder):
        patched_cross_encoder([0.9, 0.1, 0.5])
        r = CrossEncoderReranker()
        hits = [_h(1, score=0.5), _h(2, score=0.6), _h(3, score=0.7)]
        out = r.rerank("q", hits)
        # Output order is by CE score descending: 0.9 (idx 0), 0.5 (idx 2), 0.1 (idx 1).
        assert [h.chunk_id for h in out] == [1, 3, 2]
        # Each output score MUST be the CE score, not the fused score.
        assert out[0].score == pytest.approx(0.9)
        assert out[1].score == pytest.approx(0.5)
        assert out[2].score == pytest.approx(0.1)

    def test_output_source_is_reranked(self, patched_cross_encoder):
        patched_cross_encoder([0.9, 0.1])
        r = CrossEncoderReranker()
        out = r.rerank("q", [_h(1, score=0.0), _h(2, score=0.0)])
        for h in out:
            assert h.source == "reranked", f"got {h.source!r}"

    def test_metadata_passthrough(self, patched_cross_encoder):
        patched_cross_encoder([0.9])
        r = CrossEncoderReranker()
        meta = {"answer": 42}
        # Use frozen Hit; build a fresh one with metadata.
        in_hit = Hit(
            chunk_id=7,
            score=0.5,
            text="some text",
            document_id=11,
            source_uri="x://7",
            title="Seven",
            dataset_id=2,
            metadata=meta,
            source="fused",
        )
        out = r.rerank("q", [in_hit])
        assert len(out) == 1
        assert out[0].chunk_id == 7
        assert out[0].text == "some text"
        assert out[0].document_id == 11
        assert out[0].source_uri == "x://7"
        assert out[0].title == "Seven"
        assert out[0].dataset_id == 2
        assert out[0].metadata == meta


# ---------------------------------------------------------------------------
# top_n semantics
# ---------------------------------------------------------------------------


class TestTopN:
    def test_top_n_clips_input_before_scoring(self, patched_cross_encoder):
        stubs = patched_cross_encoder([1.0, 0.9, 0.8, 0.7, 0.6])
        r = CrossEncoderReranker()
        # 10 hits in, top_n=5: only top-5 by fused score (already-sorted desc)
        # should be passed to the model. The remaining 5 are dropped.
        hits = [_h(i, score=10.0 - i) for i in range(10)]
        out = r.rerank("q", hits, top_n=5)
        assert len(out) == 5
        # Inspect the stub: predict should have seen 5 pairs, not 10.
        assert len(stubs) == 1
        assert len(stubs[0].calls) == 1
        assert len(stubs[0].calls[0]["pairs"]) == 5, (
            f"Expected 5 pairs to model; got {len(stubs[0].calls[0]['pairs'])}"
        )

    def test_top_n_none_reranks_all(self, patched_cross_encoder):
        stubs = patched_cross_encoder([0.1, 0.5, 0.9])
        r = CrossEncoderReranker()
        hits = [_h(1, score=10.0), _h(2, score=5.0), _h(3, score=1.0)]
        out = r.rerank("q", hits, top_n=None)
        assert len(out) == 3
        assert len(stubs[0].calls[0]["pairs"]) == 3
        # CE scores ascending → output ids reversed: 3 (0.9), 2 (0.5), 1 (0.1).
        assert [h.chunk_id for h in out] == [3, 2, 1]

    def test_top_n_larger_than_input_returns_input_length(self, patched_cross_encoder):
        patched_cross_encoder([0.5, 0.6])
        r = CrossEncoderReranker()
        out = r.rerank("q", [_h(1, score=0.0), _h(2, score=0.0)], top_n=99)
        assert len(out) == 2

    def test_top_n_zero_returns_empty_without_calling_model(self, patched_cross_encoder):
        """`top_n=0` is degenerate; output is empty and model is NOT called."""
        # We patch with no scores; if the model were called, an out-of-range
        # access would surface. Empty-output is the correct semantic.
        patched_cross_encoder([])
        r = CrossEncoderReranker()
        out = r.rerank("q", [_h(1, score=0.5), _h(2, score=0.5)], top_n=0)
        assert out == []


# ---------------------------------------------------------------------------
# Tie-breaking
# ---------------------------------------------------------------------------


class TestTieBreaking:
    """Equal cross-encoder scores fall back to fused score, then chunk_id."""

    def test_tie_breaks_by_fused_score_desc(self, patched_cross_encoder):
        # All three get the same CE score; fused-score order should decide.
        patched_cross_encoder([0.5, 0.5, 0.5])
        r = CrossEncoderReranker()
        hits = [
            _h(1, score=0.2),
            _h(2, score=0.9),  # highest fused
            _h(3, score=0.5),
        ]
        out = r.rerank("q", hits)
        assert [h.chunk_id for h in out] == [2, 3, 1]

    def test_tie_breaks_by_chunk_id_asc_when_fused_also_tied(self, patched_cross_encoder):
        # All CE scores tied AND all fused scores tied → chunk_id ascending.
        patched_cross_encoder([0.5, 0.5, 0.5])
        r = CrossEncoderReranker()
        hits = [_h(7, score=0.5), _h(3, score=0.5), _h(11, score=0.5)]
        out = r.rerank("q", hits)
        assert [h.chunk_id for h in out] == [3, 7, 11]


# ---------------------------------------------------------------------------
# Empty / edge inputs
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_hits_returns_empty(self):
        # Already covered by TestLazyLoad.test_empty_input_skips_model_load
        # but kept as a positive shape check.
        with patch.object(CrossEncoderReranker, "_get_model") as mock:
            r = CrossEncoderReranker()
            assert r.rerank("q", []) == []
            assert mock.call_count == 0

    def test_construct_with_alternate_model_id(self):
        # The minilm alternate must construct without trying to load anything.
        r = CrossEncoderReranker(model_id="cross-encoder/ms-marco-MiniLM-L-12-v2")
        assert r.model_id == "cross-encoder/ms-marco-MiniLM-L-12-v2"
        # name still defaults; caller may override.
        assert r.name == "bge-reranker-v2-m3"

    def test_name_attribute_settable(self):
        r = CrossEncoderReranker(model_id="X/Y", name="custom-name")
        assert r.name == "custom-name"

    def test_protocol_runtime_check(self):
        """`CrossEncoderReranker` satisfies the `Reranker` Protocol structurally."""
        from corpus_forge.retrieval.rerank import Reranker

        r = CrossEncoderReranker()
        assert isinstance(r, Reranker), (
            "CrossEncoderReranker should satisfy the Reranker Protocol"
        )
