"""Phase N Wave 2 — definition-boost integration pins.

Wave 1 measured but observed no headline lift because the cross-encoder
reranker washed out the fusion-stage signal nudge.  Wave 2's boost MUST
fire in TWO places to survive that:

1. **Pre-rerank** (after fusion, before the rerank-slice truncation):
   any hit whose ``metadata.is_definition is True`` AND whose
   ``metadata.name`` is a token in the query gets its score multiplied
   by ``definition_boost_factor_pre_rerank`` (default 1.5).
2. **Post-rerank** (immediately after the reranker returns):
   the same logic, with the smaller ``definition_boost_factor_post_rerank``
   (default 1.2).  This is the load-bearing application — the Wave 1
   investigation proved that fusion-stage signals are flattened by the
   cross-encoder.

The boost only fires when both conditions hold:
- ``hit.metadata.get("is_definition") is True``
- ``hit.metadata.get("name")`` (case-folded) is an EXACT token in the
  query tokenisation (``re.split(r"[^A-Za-z0-9_]+", query.lower())``).

Substring matches do NOT trigger the boost — only equality after token
splitting.  References to a definition by its name in the body of
another chunk are NOT boosted (no ``is_definition`` flag on the
reference's metadata).

The boost is config-gated on ``RetrievalConfig.definition_boost_enabled``
(default ``False``).  Default-OFF preserves pre-Wave-2 behaviour exactly.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import numpy as np
import pytest

from corpus_forge.config import RetrievalConfig
from corpus_forge.retrieval.types import Hit, SearchOptions

# ── tiny fakes (shape mirrors test_retrieval_adaptive_alpha.py) ───────────


class _FakeEmbedder:
    name = "fake-db"
    provider = "fake"
    model_id = "fake/db"
    dimension = 4
    normalized = True
    distance = "cosine"

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        return np.ones((len(texts), self.dimension), dtype=np.float32)

    def encode_query(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        return np.full((len(texts), self.dimension), 0.5, dtype=np.float32)

    def warmup(self) -> None:
        pass


def _hit(
    cid: int,
    score: float,
    source: str = "dense",
    metadata: dict[str, Any] | None = None,
    dataset_id: int = 1,
) -> Hit:
    return Hit(
        chunk_id=cid,
        score=score,
        text=f"text-{cid}",
        document_id=None,
        source_uri=f"u://{cid}",
        title=f"t-{cid}",
        dataset_id=dataset_id,
        metadata=dict(metadata or {}),
        source=source,  # type: ignore[arg-type]
    )


class _FakeBackend:
    def __init__(
        self,
        *,
        dense_hits: list[Hit] | None = None,
        lexical_hits: list[Hit] | None = None,
    ) -> None:
        self.dense_hits = dense_hits or []
        self.lexical_hits = lexical_hits or []

    def search_dense(
        self,
        embedder_id: int,
        query_vector: np.ndarray,
        *,
        k: int,
        dataset_id: int | None = None,
    ) -> list[Hit]:
        return list(self.dense_hits[:k])

    def search_lexical(
        self,
        query: str,
        *,
        k: int,
        dataset_id: int | None = None,
    ) -> list[Hit]:
        return list(self.lexical_hits[:k])

    def find_dataset_id_by_name(self, name: str) -> int | None:
        return None

    def get_chunk(self, chunk_id: int) -> dict | None:
        return None

    def list_datasets(self) -> list[dict]:
        return []

    @contextmanager
    def lock_source(self, key: str) -> Iterator[None]:
        yield


class _PassthroughReranker:
    """Reranker that returns a caller-controlled order/score.

    Wave 2 needs the post-rerank boost to be applied AFTER the reranker
    flattens scores — so the fixture sets up a reranker that emits a
    KNOWN order with known scores, then we assert the boost lifts the
    definition hit back above the reference.
    """

    name = "fake-passthrough"
    model_id = "fake/passthrough"

    def __init__(self, *, output: list[Hit]) -> None:
        self._output = output
        self.calls: list[tuple[str, list[Hit], int | None]] = []

    def warmup(self) -> None:
        pass

    def rerank(
        self,
        query: str,
        hits: list[Hit],
        *,
        top_n: int | None = None,
    ) -> list[Hit]:
        self.calls.append((query, list(hits), top_n))
        # Return the pre-baked output verbatim; tests build it to model
        # the "cross-encoder flattened the fused score" outcome.
        return list(self._output)


# ── presence + defaults pin ───────────────────────────────────────────────


class TestRetrievalConfigDefinitionBoost:
    def test_definition_boost_enabled_default_false(self) -> None:
        rc = RetrievalConfig()
        assert rc.definition_boost_enabled is False

    def test_definition_boost_factor_pre_rerank_default(self) -> None:
        rc = RetrievalConfig()
        assert rc.definition_boost_factor_pre_rerank == 1.5

    def test_definition_boost_factor_post_rerank_default(self) -> None:
        rc = RetrievalConfig()
        assert rc.definition_boost_factor_post_rerank == 1.2


# ── pre-rerank boost ──────────────────────────────────────────────────────


class TestPreRerankBoost:
    """The pre-rerank boost lifts a definition above a reference."""

    def _make_backend(self) -> _FakeBackend:
        # THREE hits so min-max normalisation gives the middle (chunk 1,
        # the definition) a non-zero normalised score that the boost
        # can amplify.  Without the third hit the min-max collapses
        # chunk 1's normalised score to 0, blocking the boost regardless
        # of multiplier (see "min-max degenerate range" note in the
        # ``_normalize_to_dict`` body).
        #
        # Layout (descending raw score):
        #   chunk 3 — highest-scoring reference (no is_definition flag)
        #   chunk 1 — middle-scoring definition (matches the query token)
        #   chunk 9 — lowest-scoring reference (the min anchor)
        #
        # Pre-boost: chunk 3 wins on raw score (1.0 vs 0.6).
        # Boost x 2.0 lifts chunk 1's blended score above chunk 3.
        dense = [
            _hit(3, 1.0, metadata={"kind": "Function", "name": "high_ref"}),
            _hit(
                1,
                0.6,
                metadata={
                    "is_definition": True,
                    "definition_kind": "Function",
                    "name": "directory_pruned",
                    "kind": "Function",
                },
            ),
            _hit(9, 0.1, metadata={"kind": "Function", "name": "low_ref"}),
        ]
        lexical = [
            _hit(3, 1.0, source="lexical", metadata={"kind": "Function", "name": "high_ref"}),
            _hit(
                1,
                0.6,
                source="lexical",
                metadata={
                    "is_definition": True,
                    "definition_kind": "Function",
                    "name": "directory_pruned",
                    "kind": "Function",
                },
            ),
            _hit(9, 0.1, source="lexical", metadata={"kind": "Function", "name": "low_ref"}),
        ]
        return _FakeBackend(dense_hits=dense, lexical_hits=lexical)

    def test_definition_boost_lifts_definition_above_reference(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        be = self._make_backend()
        cfg = RetrievalConfig(
            definition_boost_enabled=True,
            definition_boost_factor_pre_rerank=2.0,  # big enough to flip
            definition_boost_factor_post_rerank=1.0,  # neutralise post path
        )
        r = HybridRetriever(
            backend=be,
            embedder=_FakeEmbedder(),
            embedder_id=1,
            config=cfg,
        )
        out = r.search(
            "directory_pruned",
            SearchOptions(k=5, fusion="alpha", alpha=0.5, rerank=False),
        )
        assert len(out) >= 2
        # Boost must flip the order — chunk_id 1 (definition) now beats
        # chunk_id 3 (high reference).
        assert out[0].chunk_id == 1, (
            f"expected definition chunk 1 at top; got order={[h.chunk_id for h in out]}"
        )

    def test_definition_boost_disabled_preserves_baseline_order(self) -> None:
        """With the flag OFF, scores are untouched and the reference wins."""
        from corpus_forge.retrieval import HybridRetriever

        be = self._make_backend()
        cfg = RetrievalConfig(
            definition_boost_enabled=False,
            definition_boost_factor_pre_rerank=2.0,
        )
        r = HybridRetriever(
            backend=be,
            embedder=_FakeEmbedder(),
            embedder_id=1,
            config=cfg,
        )
        out = r.search(
            "directory_pruned",
            SearchOptions(k=5, fusion="alpha", alpha=0.5, rerank=False),
        )
        assert len(out) >= 2
        # Baseline ordering: chunk_id 3 (highest pre-boost score) wins.
        assert out[0].chunk_id == 3, (
            f"flag off must preserve order; got {[h.chunk_id for h in out]}"
        )

    def test_definition_boost_reference_with_name_in_text_not_boosted(self) -> None:
        """A chunk that mentions the query token in its text but lacks
        ``is_definition`` does NOT get the boost.  Only the metadata flag
        gates the multiplier — body-text matches don't.
        """
        from corpus_forge.retrieval import HybridRetriever

        # THREE-hit fixture (same min-max anchoring rationale as the
        # boost-lift test).  Chunk 3 carries the query token as a
        # metadata name but is NOT a definition (no is_definition flag).
        # Chunk 1 IS a definition.  Chunk 9 is the min-anchor.
        # The boost must NOT lift chunk 3 (no flag); it MUST lift chunk 1.
        dense = [
            _hit(
                3,
                1.0,
                metadata={"kind": "Function", "name": "directory_pruned"},
                # Note: no is_definition flag.
            ),
            _hit(
                1,
                0.6,
                metadata={
                    "is_definition": True,
                    "definition_kind": "Function",
                    "name": "directory_pruned",
                    "kind": "Function",
                },
            ),
            _hit(9, 0.1, metadata={"kind": "Function", "name": "low_ref"}),
        ]
        lexical = [
            _hit(
                3,
                1.0,
                source="lexical",
                metadata={"kind": "Function", "name": "directory_pruned"},
            ),
            _hit(
                1,
                0.6,
                source="lexical",
                metadata={
                    "is_definition": True,
                    "definition_kind": "Function",
                    "name": "directory_pruned",
                    "kind": "Function",
                },
            ),
            _hit(9, 0.1, source="lexical", metadata={"kind": "Function", "name": "low_ref"}),
        ]
        be = _FakeBackend(dense_hits=dense, lexical_hits=lexical)
        cfg = RetrievalConfig(
            definition_boost_enabled=True,
            definition_boost_factor_pre_rerank=2.0,
            definition_boost_factor_post_rerank=1.0,
        )
        r = HybridRetriever(
            backend=be,
            embedder=_FakeEmbedder(),
            embedder_id=1,
            config=cfg,
        )
        out = r.search(
            "directory_pruned",
            SearchOptions(k=5, fusion="alpha", alpha=0.5, rerank=False),
        )
        # The flagged definition (chunk 1) wins; the unflagged
        # name-match reference (chunk 3) does NOT get boosted.
        assert out[0].chunk_id == 1


# ── post-rerank boost ─────────────────────────────────────────────────────


class TestPostRerankBoost:
    """The post-rerank boost is the load-bearing application.

    The cross-encoder reranker emits its own scores and discards the
    upstream fused score (Wave 1 finding).  The Wave 2 retriever applies
    the boost AFTER the reranker returns so the multiplier survives.
    """

    def test_post_rerank_boost_lifts_definition_above_reference(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        # Reranker emits chunk 2 (reference) ahead of chunk 1
        # (definition) — flat scores, like the real cross-encoder when
        # the signal is borderline.  The post-rerank boost must re-sort
        # so chunk 1 lands on top.
        definition_hit = _hit(
            1,
            0.50,
            source="reranked",
            metadata={
                "is_definition": True,
                "definition_kind": "Function",
                "name": "directory_pruned",
                "kind": "Function",
            },
        )
        reference_hit = _hit(
            2,
            0.55,
            source="reranked",
            metadata={"kind": "Function", "name": "neighbour_fn"},
        )
        reranker = _PassthroughReranker(output=[reference_hit, definition_hit])

        # Backend just needs to return SOMETHING for the dense/lexical
        # legs; the reranker will short-circuit the order anyway.
        dense = [
            _hit(
                1,
                0.6,
                metadata={
                    "is_definition": True,
                    "definition_kind": "Function",
                    "name": "directory_pruned",
                    "kind": "Function",
                },
            ),
            _hit(2, 0.5, metadata={"kind": "Function", "name": "neighbour_fn"}),
        ]
        lexical = [
            _hit(
                1,
                0.6,
                source="lexical",
                metadata={
                    "is_definition": True,
                    "definition_kind": "Function",
                    "name": "directory_pruned",
                    "kind": "Function",
                },
            ),
            _hit(
                2,
                0.5,
                source="lexical",
                metadata={"kind": "Function", "name": "neighbour_fn"},
            ),
        ]
        be = _FakeBackend(dense_hits=dense, lexical_hits=lexical)
        cfg = RetrievalConfig(
            definition_boost_enabled=True,
            definition_boost_factor_pre_rerank=1.0,  # neutralise pre path
            definition_boost_factor_post_rerank=1.5,
        )
        r = HybridRetriever(
            backend=be,
            embedder=_FakeEmbedder(),
            embedder_id=1,
            reranker=reranker,
            config=cfg,
        )
        out = r.search(
            "directory_pruned",
            SearchOptions(
                k=5,
                fusion="alpha",
                alpha=0.5,
                rerank=True,
                rerank_top_n=50,
            ),
        )
        assert len(out) >= 2
        assert out[0].chunk_id == 1, (
            "post-rerank boost must lift the definition above the reference "
            f"after the reranker flattens scores; got {[h.chunk_id for h in out]}"
        )
        # The boost multiplier is the contract.
        boosted = out[0].score
        # 0.50 * 1.5 = 0.75
        assert boosted == pytest.approx(0.75, rel=1e-6), (
            f"expected boosted score 0.50*1.5=0.75; got {boosted!r}"
        )

    def test_post_rerank_boost_disabled_preserves_reranker_order(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        # Reranker says: reference first, definition second.  Flag off
        # — output must mirror the reranker verbatim.
        definition_hit = _hit(
            1,
            0.50,
            source="reranked",
            metadata={
                "is_definition": True,
                "definition_kind": "Function",
                "name": "directory_pruned",
                "kind": "Function",
            },
        )
        reference_hit = _hit(
            2,
            0.55,
            source="reranked",
            metadata={"kind": "Function", "name": "neighbour_fn"},
        )
        reranker = _PassthroughReranker(output=[reference_hit, definition_hit])

        be = _FakeBackend(
            dense_hits=[
                _hit(
                    1,
                    0.6,
                    metadata={
                        "is_definition": True,
                        "definition_kind": "Function",
                        "name": "directory_pruned",
                    },
                ),
                _hit(2, 0.5, metadata={"name": "neighbour_fn"}),
            ],
            lexical_hits=[
                _hit(
                    1,
                    0.6,
                    source="lexical",
                    metadata={
                        "is_definition": True,
                        "definition_kind": "Function",
                        "name": "directory_pruned",
                    },
                ),
                _hit(2, 0.5, source="lexical", metadata={"name": "neighbour_fn"}),
            ],
        )
        cfg = RetrievalConfig(definition_boost_enabled=False)
        r = HybridRetriever(
            backend=be,
            embedder=_FakeEmbedder(),
            embedder_id=1,
            reranker=reranker,
            config=cfg,
        )
        out = r.search(
            "directory_pruned",
            SearchOptions(k=5, fusion="alpha", alpha=0.5, rerank=True, rerank_top_n=50),
        )
        assert len(out) == 2
        # Reranker order preserved verbatim — reference first.
        assert [h.chunk_id for h in out] == [2, 1]


# ── tokenisation contract ─────────────────────────────────────────────────


class TestTokenisationContract:
    """The query is tokenised by splitting on non-identifier chars.

    ``"Foo.bar"`` → {"foo", "bar"}; both names match independently.
    Case-insensitive — ``"DirectoryPruned"`` matches a definition named
    ``"directorypruned"``.  Substring matches do NOT boost.
    """

    def _make_backend(self, *, def_name: str, ref_name: str) -> _FakeBackend:
        # THREE-hit fixture so min-max normalisation gives chunk 1
        # (middle) a non-zero normalised score the boost can amplify.
        dense = [
            _hit(3, 1.0, metadata={"name": ref_name, "kind": "Function"}),
            _hit(
                1,
                0.6,
                metadata={
                    "is_definition": True,
                    "definition_kind": "Function",
                    "name": def_name,
                    "kind": "Function",
                },
            ),
            _hit(9, 0.1, metadata={"name": "low_ref", "kind": "Function"}),
        ]
        lexical = [
            _hit(3, 1.0, source="lexical", metadata={"name": ref_name, "kind": "Function"}),
            _hit(
                1,
                0.6,
                source="lexical",
                metadata={
                    "is_definition": True,
                    "definition_kind": "Function",
                    "name": def_name,
                    "kind": "Function",
                },
            ),
            _hit(9, 0.1, source="lexical", metadata={"name": "low_ref", "kind": "Function"}),
        ]
        return _FakeBackend(dense_hits=dense, lexical_hits=lexical)

    def test_dotted_query_tokens_both_match(self) -> None:
        """``Foo.bar`` tokenises to {"foo","bar"} — either matches."""
        from corpus_forge.retrieval import HybridRetriever

        be = self._make_backend(def_name="bar", ref_name="other_fn")
        cfg = RetrievalConfig(
            definition_boost_enabled=True,
            definition_boost_factor_pre_rerank=2.0,
            definition_boost_factor_post_rerank=1.0,
        )
        r = HybridRetriever(
            backend=be,
            embedder=_FakeEmbedder(),
            embedder_id=1,
            config=cfg,
        )
        out = r.search(
            "Foo.bar",
            SearchOptions(k=5, fusion="alpha", alpha=0.5),
        )
        # `bar` is one of the query tokens; the definition with name="bar"
        # is boosted and lands on top.
        assert out[0].chunk_id == 1

    def test_case_insensitive_match(self) -> None:
        """Tokenisation lowers the case; ``DirectoryPruned`` matches
        a definition named ``directorypruned`` (after lowering)."""
        from corpus_forge.retrieval import HybridRetriever

        be = self._make_backend(def_name="directorypruned", ref_name="other_fn")
        cfg = RetrievalConfig(
            definition_boost_enabled=True,
            definition_boost_factor_pre_rerank=2.0,
            definition_boost_factor_post_rerank=1.0,
        )
        r = HybridRetriever(
            backend=be,
            embedder=_FakeEmbedder(),
            embedder_id=1,
            config=cfg,
        )
        out = r.search(
            "DirectoryPruned",
            SearchOptions(k=5, fusion="alpha", alpha=0.5),
        )
        assert out[0].chunk_id == 1

    def test_substring_does_not_boost(self) -> None:
        """Query token must EQUAL the name token (case-insensitive).

        Query ``directory`` does NOT boost a definition named
        ``directory_pruned`` — only equality after splitting matches.
        """
        from corpus_forge.retrieval import HybridRetriever

        be = self._make_backend(def_name="directory_pruned", ref_name="other_fn")
        cfg = RetrievalConfig(
            definition_boost_enabled=True,
            definition_boost_factor_pre_rerank=2.0,
            definition_boost_factor_post_rerank=1.0,
        )
        r = HybridRetriever(
            backend=be,
            embedder=_FakeEmbedder(),
            embedder_id=1,
            config=cfg,
        )
        out = r.search(
            "directory",  # NOT a token of "directory_pruned" after lowercase split
            SearchOptions(k=5, fusion="alpha", alpha=0.5),
        )
        # No boost fired because "directory" != "directory_pruned" as
        # a whole-name comparison.  Reference (chunk 3) keeps its
        # higher fused score.
        assert out[0].chunk_id == 3


# ── RRF path: boost still fires (it works on the fused score dict) ────────


class TestRrfPathBoost:
    """RRF fusion produces a score dict too; the boost reads `metadata`
    on the materialised hits, not the fusion math.  Boost fires whether
    fusion is RRF or alpha.  Verified end-to-end here so the wave gate
    test can run under either fusion (the gate uses alpha, but Wave 1's
    paired-retriever test fixture leaves the choice to operators)."""

    def test_rrf_path_also_boosts(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        dense = [
            _hit(
                2,
                1.0,
                metadata={"name": "neighbour_fn", "kind": "Function"},
            ),
            _hit(
                1,
                0.6,
                metadata={
                    "is_definition": True,
                    "definition_kind": "Function",
                    "name": "directory_pruned",
                    "kind": "Function",
                },
            ),
        ]
        # Lexical mirrors dense so RRF combines fairly.
        lexical = [
            _hit(
                2,
                1.0,
                source="lexical",
                metadata={"name": "neighbour_fn", "kind": "Function"},
            ),
            _hit(
                1,
                0.6,
                source="lexical",
                metadata={
                    "is_definition": True,
                    "definition_kind": "Function",
                    "name": "directory_pruned",
                    "kind": "Function",
                },
            ),
        ]
        be = _FakeBackend(dense_hits=dense, lexical_hits=lexical)
        cfg = RetrievalConfig(
            definition_boost_enabled=True,
            definition_boost_factor_pre_rerank=3.0,  # ensure flip on RRF too
            definition_boost_factor_post_rerank=1.0,
        )
        r = HybridRetriever(
            backend=be,
            embedder=_FakeEmbedder(),
            embedder_id=1,
            config=cfg,
        )
        out = r.search(
            "directory_pruned",
            SearchOptions(k=5, fusion="rrf"),
        )
        assert out[0].chunk_id == 1
