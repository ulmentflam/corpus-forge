"""R2-01 — `HybridRetriever` unit pins.

`HybridRetriever` is the user-facing entry point for hybrid retrieval.
Composition (constructor):
- `backend`: any `StorageBackend` (we use a small fake here).
- `embedder`: any `Embedder` (we use a small recording fake).
- `embedder_id`: int — the row id in the backend's embedders table.
- `reranker`: `Reranker | None = None` — R4 wires this; R2 must accept the
  param but MUST NOT call it (no R4 surface yet).

Operation (`search(query, options)`):
- Build a dense query vector by calling **`embedder.encode_query(...)`**
  (NOT `embedder.encode`).
- Call `backend.search_dense(embedder_id, qvec, k=options.k * 2, dataset_id=…)`
  and `backend.search_lexical(query, k=options.k * 2, dataset_id=…)` — fetching
  ~2k from each list before fusion is standard practice.
- Fuse: RRF when `options.fusion == "rrf"`, else `alpha_blend` after per-list
  min-max normalisation of both lists' scores (R1 carry-over #1).
- Returns the top-`options.k` `Hit` objects with `source="fused"`.

Dataset filter: `options.dataset` (a name) is resolved via
`backend.find_dataset_id_by_name(...)` and the resulting id is passed to both
backend calls.  `None` → no filter.

In R2 the reranker is not called even when present (R4 owns it).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from corpus_forge.retrieval.types import Hit, SearchOptions

# ── module presence + re-exports ──────────────────────────────────────────


def test_module_importable():
    import corpus_forge.retrieval.retriever  # noqa: F401


def test_retriever_protocol_reexported():
    from corpus_forge.retrieval import Retriever  # noqa: F401


def test_hybrid_retriever_reexported():
    from corpus_forge.retrieval import HybridRetriever  # noqa: F401


# ── tiny fakes ────────────────────────────────────────────────────────────


class _FakeEmbedder:
    name = "fake-rt"
    provider = "fake"
    model_id = "fake/rt"
    dimension = 4
    normalized = True
    distance = "cosine"

    def __init__(self) -> None:
        self.encode_calls: list[tuple[list[str], dict]] = []
        self.encode_query_calls: list[tuple[list[str], dict]] = []

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        self.encode_calls.append((list(texts), {"batch_size": batch_size}))
        return np.ones((len(texts), self.dimension), dtype=np.float32)

    def encode_query(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        self.encode_query_calls.append((list(texts), {"batch_size": batch_size}))
        return np.full((len(texts), self.dimension), 0.5, dtype=np.float32)

    def warmup(self) -> None:
        pass


def _hit(cid: int, score: float, source: str = "dense", dataset_id: int = 1) -> Hit:
    return Hit(
        chunk_id=cid,
        score=score,
        text=f"text-{cid}",
        document_id=None,
        source_uri=f"u://{cid}",
        title=f"t-{cid}",
        dataset_id=dataset_id,
        metadata={},
        source=source,  # type: ignore[arg-type]
    )


class _FakeBackend:
    """Records calls and returns canned hit lists."""

    def __init__(
        self,
        *,
        dense_hits: list[Hit] | None = None,
        lexical_hits: list[Hit] | None = None,
        dataset_ids: dict[str, int] | None = None,
    ) -> None:
        self.dense_hits = dense_hits or []
        self.lexical_hits = lexical_hits or []
        self.dataset_ids = dataset_ids or {}
        self.search_dense_calls: list[dict[str, Any]] = []
        self.search_lexical_calls: list[dict[str, Any]] = []
        self.find_dataset_calls: list[str] = []

    def search_dense(
        self,
        embedder_id: int,
        query_vector: np.ndarray,
        *,
        k: int,
        dataset_id: int | None = None,
    ) -> list[Hit]:
        self.search_dense_calls.append(
            {
                "embedder_id": embedder_id,
                "query_vector": np.asarray(query_vector).copy(),
                "k": k,
                "dataset_id": dataset_id,
            }
        )
        return list(self.dense_hits[:k])

    def search_lexical(
        self,
        query: str,
        *,
        k: int,
        dataset_id: int | None = None,
    ) -> list[Hit]:
        self.search_lexical_calls.append({"query": query, "k": k, "dataset_id": dataset_id})
        return list(self.lexical_hits[:k])

    def find_dataset_id_by_name(self, name: str) -> int | None:
        self.find_dataset_calls.append(name)
        return self.dataset_ids.get(name)

    def get_chunk(self, chunk_id: int) -> dict | None:
        return None

    def list_datasets(self) -> list[dict]:
        return []

    @contextmanager
    def lock_source(self, key: str) -> Iterator[None]:
        yield


# ── construction ──────────────────────────────────────────────────────────


class TestConstruction:
    def test_hybrid_retriever_accepts_required_args(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend()
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=42)
        assert r is not None

    def test_hybrid_retriever_accepts_reranker_param_default_none(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend()
        em = _FakeEmbedder()
        # The reranker param must exist (R4 plumbing) and default to None.
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        assert getattr(r, "reranker", "UNSET") is None

    def test_hybrid_retriever_stores_reranker_when_passed(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend()
        em = _FakeEmbedder()
        sentinel = MagicMock()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, reranker=sentinel)
        assert r.reranker is sentinel


# ── search() — dispatch and call shape ────────────────────────────────────


class TestSearchDispatch:
    def test_search_calls_encode_query_not_encode(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(dense_hits=[_hit(1, 0.9)], lexical_hits=[_hit(2, 0.5, "lexical")])
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        r.search("how does lock_source work", SearchOptions(k=5))

        assert len(em.encode_query_calls) == 1
        assert em.encode_query_calls[0][0] == ["how does lock_source work"]
        # encode() must NOT be called by the query path
        assert em.encode_calls == []

    def test_search_calls_both_backends(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(dense_hits=[_hit(1, 0.9)], lexical_hits=[_hit(2, 0.5, "lexical")])
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=7)
        r.search("q", SearchOptions(k=3))

        assert len(be.search_dense_calls) == 1
        assert len(be.search_lexical_calls) == 1
        assert be.search_dense_calls[0]["embedder_id"] == 7

    def test_search_overfetches_for_fusion(self):
        """Both backends are asked for >= k results to give fusion room."""
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(dense_hits=[], lexical_hits=[])
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        r.search("q", SearchOptions(k=5))

        assert be.search_dense_calls[0]["k"] >= 5
        assert be.search_lexical_calls[0]["k"] >= 5

    def test_search_passes_query_string_to_lexical(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend()
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        r.search("brown fox", SearchOptions(k=2))

        assert be.search_lexical_calls[0]["query"] == "brown fox"


# ── search() — fusion paths ───────────────────────────────────────────────


class TestSearchFusion:
    def test_fused_hits_have_source_fused(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(
            dense_hits=[_hit(1, 0.9), _hit(2, 0.7)],
            lexical_hits=[_hit(1, 0.8, "lexical"), _hit(3, 0.5, "lexical")],
        )
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        out = r.search("q", SearchOptions(k=3, fusion="rrf"))

        assert isinstance(out, list)
        for h in out:
            assert h.source == "fused"

    def test_fused_topk_returned(self):
        from corpus_forge.retrieval import HybridRetriever

        dense = [_hit(i, 1.0 / i) for i in range(1, 6)]
        lex = [_hit(i, 0.5, "lexical") for i in range(3, 8)]
        be = _FakeBackend(dense_hits=dense, lexical_hits=lex)
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        out = r.search("q", SearchOptions(k=3))
        assert len(out) == 3

    def test_rrf_top_hit_is_jointly_ranked(self):
        """The id ranked top in both lists must win under RRF."""
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(
            dense_hits=[_hit(7, 0.9), _hit(1, 0.5)],
            lexical_hits=[_hit(7, 0.9, "lexical"), _hit(2, 0.5, "lexical")],
        )
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        out = r.search("q", SearchOptions(k=2, fusion="rrf"))
        assert out[0].chunk_id == 7

    def test_alpha_fusion_top_hit_dense_when_alpha_high(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(
            dense_hits=[_hit(1, 1.0), _hit(2, 0.0)],
            lexical_hits=[_hit(3, 1.0, "lexical"), _hit(1, 0.0, "lexical")],
        )
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        out = r.search("q", SearchOptions(k=1, fusion="alpha", alpha=0.99))
        assert out[0].chunk_id == 1

    def test_alpha_fusion_top_hit_lexical_when_alpha_low(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(
            dense_hits=[_hit(1, 1.0), _hit(2, 0.0)],
            lexical_hits=[_hit(3, 1.0, "lexical"), _hit(1, 0.0, "lexical")],
        )
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        out = r.search("q", SearchOptions(k=1, fusion="alpha", alpha=0.01))
        assert out[0].chunk_id == 3

    def test_alpha_monotonic_in_alpha(self):
        """Increasing alpha must monotonically push the top result toward the dense favourite."""
        from corpus_forge.retrieval import HybridRetriever

        # Dense favours 1; lexical favours 2.
        be_factory = lambda: _FakeBackend(  # noqa: E731
            dense_hits=[_hit(1, 1.0), _hit(2, 0.0)],
            lexical_hits=[_hit(2, 1.0, "lexical"), _hit(1, 0.0, "lexical")],
        )
        em = _FakeEmbedder()
        r_low = HybridRetriever(backend=be_factory(), embedder=em, embedder_id=1)
        r_high = HybridRetriever(backend=be_factory(), embedder=em, embedder_id=1)
        top_low = r_low.search("q", SearchOptions(k=1, fusion="alpha", alpha=0.0))[0]
        top_high = r_high.search("q", SearchOptions(k=1, fusion="alpha", alpha=1.0))[0]
        assert top_low.chunk_id == 2
        assert top_high.chunk_id == 1


# ── dataset filter passthrough ────────────────────────────────────────────


class TestDatasetFilter:
    def test_dataset_filter_resolved_and_passed_through(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(
            dense_hits=[_hit(1, 0.9, dataset_id=99)],
            lexical_hits=[_hit(2, 0.5, "lexical", dataset_id=99)],
            dataset_ids={"my-ds": 99},
        )
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        r.search("q", SearchOptions(k=2, dataset="my-ds"))

        assert be.find_dataset_calls == ["my-ds"]
        assert be.search_dense_calls[0]["dataset_id"] == 99
        assert be.search_lexical_calls[0]["dataset_id"] == 99

    def test_dataset_none_passes_none(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend()
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        r.search("q", SearchOptions(k=2, dataset=None))
        assert be.search_dense_calls[0]["dataset_id"] is None
        assert be.search_lexical_calls[0]["dataset_id"] is None
        # find_dataset_id_by_name should NOT be called when dataset is None
        assert be.find_dataset_calls == []

    def test_unknown_dataset_returns_empty(self):
        """Unknown dataset name → empty list; do NOT silently leak across datasets."""
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(dataset_ids={})  # unknown name → None
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        out = r.search("q", SearchOptions(k=2, dataset="nope"))
        assert out == []
        # Backend search must not be called when the dataset name is unresolvable.
        assert be.search_dense_calls == []
        assert be.search_lexical_calls == []


# ── reranker not yet called (R4 owns) ─────────────────────────────────────


class TestRerankerNotCalledYet:
    def test_reranker_not_invoked_even_when_set(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(
            dense_hits=[_hit(1, 0.9)],
            lexical_hits=[_hit(2, 0.5, "lexical")],
        )
        em = _FakeEmbedder()
        rr = MagicMock()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, reranker=rr)
        r.search("q", SearchOptions(k=2, rerank=True))
        # R2 does not call rerank yet — even with rerank=True.  R4 wires it.
        rr.rerank.assert_not_called()
        rr.assert_not_called()

    def test_reranker_default_none_no_error(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(dense_hits=[_hit(1, 0.9)])
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        # No exception even with rerank=True (silent no-op until R4).
        r.search("q", SearchOptions(k=1, rerank=True))


# ── edge cases ────────────────────────────────────────────────────────────


class TestEdges:
    def test_empty_lists_returns_empty(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(dense_hits=[], lexical_hits=[])
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        out = r.search("q", SearchOptions(k=10))
        assert out == []

    def test_dense_only_results(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(dense_hits=[_hit(1, 0.9), _hit(2, 0.5)], lexical_hits=[])
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        out = r.search("q", SearchOptions(k=2))
        assert len(out) == 2
        assert {h.chunk_id for h in out} == {1, 2}

    def test_lexical_only_results(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(
            dense_hits=[],
            lexical_hits=[_hit(3, 0.9, "lexical"), _hit(4, 0.5, "lexical")],
        )
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        out = r.search("q", SearchOptions(k=2))
        assert len(out) == 2
        assert {h.chunk_id for h in out} == {3, 4}

    def test_k_larger_than_pool(self):
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(
            dense_hits=[_hit(1, 0.9)],
            lexical_hits=[_hit(2, 0.5, "lexical")],
        )
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        out = r.search("q", SearchOptions(k=99))
        # Only 2 distinct chunks across the two lists → at most 2 fused hits.
        assert len(out) == 2

    def test_search_accepts_string_query(self):
        """Smoke: a plain str query goes through without error."""
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(dense_hits=[_hit(1, 0.5)])
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        out = r.search("plain str", SearchOptions(k=1))
        assert isinstance(out, list)


# ── Retriever protocol smoke ──────────────────────────────────────────────


def test_retriever_protocol_runtime_checkable_against_hybrid():
    """HybridRetriever satisfies the Retriever protocol structurally."""
    from corpus_forge.retrieval import HybridRetriever, Retriever

    be = _FakeBackend()
    em = _FakeEmbedder()
    r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
    # Duck check; Retriever is a Protocol.  isinstance only works if it's
    # @runtime_checkable, so we just verify the method exists and the type is
    # a Protocol class.
    assert hasattr(r, "search")
    assert callable(r.search)
    # Smoke: Retriever class is importable and is a class (Protocol).
    assert Retriever is not None


# ── pytest collection sanity ──────────────────────────────────────────────


def test_imports_dont_pull_in_torch_or_sentence_transformers():
    """The retriever module must be import-light — no heavy ML imports."""
    import sys

    # Force a clean import path
    for mod in list(sys.modules):
        if mod.startswith("corpus_forge.retrieval.retriever"):
            del sys.modules[mod]

    # We don't ban torch outright (existing modules may have pulled it in by
    # this test's execution time), but we do require the retriever module
    # itself to not import torch top-level.  Approximate by checking the
    # module file source.
    import inspect

    import corpus_forge.retrieval.retriever

    src = inspect.getsource(corpus_forge.retrieval.retriever)
    assert "import torch" not in src, "retriever.py must not import torch"
    assert "from sentence_transformers" not in src, "retriever.py must not import ST"


# ── R4-05: reranker wire-up inside HybridRetriever ────────────────────────


class _RecordingReranker:
    """Spy reranker that records the inputs it was called with and returns
    its output verbatim (no real model)."""

    name = "spy-reranker"
    model_id = "spy://rerank"

    def __init__(self, output: list[Hit] | None = None) -> None:
        self.output: list[Hit] = output or []
        self.warmup_calls = 0
        self.rerank_calls: list[dict[str, Any]] = []

    def warmup(self) -> None:
        self.warmup_calls += 1

    def rerank(self, query: str, hits: list[Hit], *, top_n: int | None = None) -> list[Hit]:
        self.rerank_calls.append({"query": query, "hits": list(hits), "top_n": top_n})
        # If the user supplied an `output`, return it.  Else echo the input
        # with source flipped to "reranked".
        if self.output:
            return list(self.output)
        return [
            Hit(
                chunk_id=h.chunk_id,
                score=h.score,
                text=h.text,
                document_id=h.document_id,
                source_uri=h.source_uri,
                title=h.title,
                dataset_id=h.dataset_id,
                metadata=h.metadata,
                source="reranked",
            )
            for h in hits
        ]


class TestRerankWireUp:
    """`HybridRetriever.search` honours `options.rerank` + `self.reranker`."""

    def test_default_rerank_false_ignores_reranker(self):
        """A configured reranker is NOT consulted when `rerank=False`."""
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(
            dense_hits=[_hit(1, 0.9), _hit(2, 0.5)],
            lexical_hits=[_hit(1, 0.8, "lexical"), _hit(3, 0.5, "lexical")],
        )
        em = _FakeEmbedder()
        spy = _RecordingReranker()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, reranker=spy)
        out = r.search("q", SearchOptions(k=3, rerank=False))

        # Reranker MUST NOT be called.
        assert spy.rerank_calls == []
        # Output is the standard fused list.
        for h in out:
            assert h.source == "fused"

    def test_rerank_true_calls_reranker(self):
        """`rerank=True` + configured reranker → rerank invoked with fused hits."""
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(
            dense_hits=[_hit(1, 0.9), _hit(2, 0.7), _hit(3, 0.5)],
            lexical_hits=[_hit(1, 0.9, "lexical"), _hit(4, 0.6, "lexical")],
        )
        em = _FakeEmbedder()
        spy = _RecordingReranker()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, reranker=spy)
        out = r.search(
            "q",
            SearchOptions(k=2, rerank=True, rerank_top_n=10),
        )

        assert len(spy.rerank_calls) == 1
        call = spy.rerank_calls[0]
        assert call["query"] == "q"
        # `top_n` argument passed to rerank == options.k
        assert call["top_n"] == 2
        # All hits passed to rerank carry source="fused" (the upstream)
        for h in call["hits"]:
            assert h.source == "fused"
        # Output source flipped to "reranked"
        assert all(h.source == "reranked" for h in out)

    def test_rerank_true_without_reranker_returns_fused(self):
        """When rerank=True but `self.reranker is None`, behave as no-op."""
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(
            dense_hits=[_hit(1, 0.9), _hit(2, 0.5)],
            lexical_hits=[_hit(3, 0.6, "lexical")],
        )
        em = _FakeEmbedder()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, reranker=None)
        out = r.search("q", SearchOptions(k=2, rerank=True))

        # Should not crash; output is the fused list (source="fused").
        for h in out:
            assert h.source == "fused"

    def test_rerank_top_n_caps_input_to_reranker(self):
        """The reranker receives at most `options.rerank_top_n` hits."""
        from corpus_forge.retrieval import HybridRetriever

        # 20 distinct chunks across the two lists.
        dense = [_hit(i, 1.0 - i * 0.01) for i in range(1, 21)]
        lex = [_hit(i, 1.0 - i * 0.01, "lexical") for i in range(21, 41)]
        be = _FakeBackend(dense_hits=dense, lexical_hits=lex)
        em = _FakeEmbedder()
        spy = _RecordingReranker()
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, reranker=spy)
        r.search(
            "q",
            SearchOptions(k=5, rerank=True, rerank_top_n=8),
        )

        assert len(spy.rerank_calls) == 1
        passed_in = spy.rerank_calls[0]["hits"]
        # rerank_top_n=8 → at most 8 fused hits passed to the reranker.
        assert len(passed_in) <= 8

    def test_rerank_returns_reranker_output_verbatim(self):
        """HybridRetriever does NOT re-sort the reranker's output."""
        from corpus_forge.retrieval import HybridRetriever

        be = _FakeBackend(
            dense_hits=[_hit(1, 0.9), _hit(2, 0.5)],
            lexical_hits=[_hit(3, 0.6, "lexical")],
        )
        em = _FakeEmbedder()
        # Reranker returns a specific order (chunk 3 first, then 1, then 2).
        canned = [
            _hit(3, 0.99),
            _hit(1, 0.50),
            _hit(2, 0.10),
        ]
        # Flip their `source` to "reranked" so the contract is honoured.
        canned = [
            Hit(
                chunk_id=h.chunk_id,
                score=h.score,
                text=h.text,
                document_id=h.document_id,
                source_uri=h.source_uri,
                title=h.title,
                dataset_id=h.dataset_id,
                metadata=h.metadata,
                source="reranked",
            )
            for h in canned
        ]
        spy = _RecordingReranker(output=canned)
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, reranker=spy)
        out = r.search("q", SearchOptions(k=3, rerank=True))

        # HybridRetriever must return the reranker's output verbatim
        # (same order, same scores).
        assert [h.chunk_id for h in out] == [3, 1, 2]
        assert [h.score for h in out] == [0.99, 0.50, 0.10]


# Suppress an unused-import warning on AbstractContextManager.
_ = AbstractContextManager
_ = pytest
