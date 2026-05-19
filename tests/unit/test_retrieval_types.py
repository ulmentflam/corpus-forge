"""R1-01 — retrieval types unit pins.

Pins the public surface of ``corpus_forge.retrieval.types`` for Phase R1:

- ``Hit``: frozen dataclass with exactly the fields listed in the plan,
  including the forward-compat ``source`` literal of {"dense","lexical",
  "fused","reranked"}.  R1 only emits "dense" and "lexical"; R2 (HybridRetriever)
  fills in "fused" and R4 fills in "reranked".
- ``SearchOptions``: defaults match the plan verbatim (``k=10``, ``dataset=None``,
  ``fusion="rrf"``, ``alpha=0.5``, ``rerank=False``, ``rerank_top_n=50``).
- ``RetrievalMetrics``: three ``dict[int, float]`` fields (ndcg / mrr / recall).

Also pins that ``Hit``, ``SearchOptions`` and ``RetrievalMetrics`` are
re-exported from the package root (``corpus_forge.retrieval``) for ergonomic
consumer imports.
"""

from __future__ import annotations

import dataclasses
import typing
from typing import Any, get_args, get_origin, get_type_hints

import pytest

# ── module presence ───────────────────────────────────────────────────────


def test_module_importable():
    """The package ``corpus_forge.retrieval`` exists."""
    import corpus_forge.retrieval  # noqa: F401


def test_types_module_importable():
    """The submodule ``corpus_forge.retrieval.types`` exists."""
    import corpus_forge.retrieval.types  # noqa: F401


# ── re-exports from package root ─────────────────────────────────────────


def test_hit_reexported_from_package():
    """``Hit`` is importable from ``corpus_forge.retrieval``."""
    from corpus_forge.retrieval import Hit  # noqa: F401


def test_search_options_reexported_from_package():
    """``SearchOptions`` is importable from ``corpus_forge.retrieval``."""
    from corpus_forge.retrieval import SearchOptions  # noqa: F401


def test_retrieval_metrics_reexported_from_package():
    """``RetrievalMetrics`` is importable from ``corpus_forge.retrieval``."""
    from corpus_forge.retrieval import RetrievalMetrics  # noqa: F401


# ── Hit — fields, frozen, source literal ─────────────────────────────────


class TestHit:
    """Hit dataclass contract."""

    def _hit_cls(self):
        from corpus_forge.retrieval.types import Hit

        return Hit

    def test_hit_is_dataclass(self):
        assert dataclasses.is_dataclass(self._hit_cls())

    def test_hit_is_frozen(self):
        """Hit must be frozen — mutation raises FrozenInstanceError."""
        Hit = self._hit_cls()
        h = Hit(
            chunk_id=1,
            score=0.5,
            text="x",
            document_id=None,
            source_uri=None,
            title=None,
            dataset_id=1,
            metadata={},
            source="dense",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            h.score = 0.9  # type: ignore[misc]

    def test_hit_has_exact_field_set(self):
        """The dataclass field names must match the plan exactly."""
        names = {f.name for f in dataclasses.fields(self._hit_cls())}
        assert names == {
            "chunk_id",
            "score",
            "text",
            "document_id",
            "source_uri",
            "title",
            "dataset_id",
            "metadata",
            "source",
        }, f"Unexpected Hit fields: {names}"

    def test_hit_source_literal_includes_all_four(self):
        """Hit.source must accept "dense", "lexical", "fused", "reranked"."""
        hints = get_type_hints(self._hit_cls(), include_extras=False)
        source_t = hints["source"]
        # source: Literal[...]
        allowed = set(get_args(source_t)) if get_origin(source_t) is typing.Literal else set()
        assert allowed == {"dense", "lexical", "fused", "reranked"}, (
            f"Hit.source must be Literal['dense','lexical','fused','reranked'], got {source_t!r}"
        )

    def test_hit_constructible_with_dense_source(self):
        Hit = self._hit_cls()
        h = Hit(
            chunk_id=1,
            score=0.7,
            text="hello",
            document_id=2,
            source_uri="vault://x.md",
            title="X",
            dataset_id=3,
            metadata={"k": "v"},
            source="dense",
        )
        assert h.chunk_id == 1
        assert h.score == 0.7
        assert h.text == "hello"
        assert h.document_id == 2
        assert h.source_uri == "vault://x.md"
        assert h.title == "X"
        assert h.dataset_id == 3
        assert h.metadata == {"k": "v"}
        assert h.source == "dense"

    def test_hit_accepts_none_optionals(self):
        """document_id / source_uri / title may be None (e.g. message chunks)."""
        Hit = self._hit_cls()
        h = Hit(
            chunk_id=10,
            score=0.1,
            text="msg",
            document_id=None,
            source_uri=None,
            title=None,
            dataset_id=1,
            metadata={},
            source="lexical",
        )
        assert h.document_id is None
        assert h.source_uri is None
        assert h.title is None

    def test_hit_field_type_annotations(self):
        """Loosely pin types via dataclass field annotations."""
        Hit = self._hit_cls()
        fields = {f.name: f for f in dataclasses.fields(Hit)}
        # Pin the int / float / str fields by name; the optional unions
        # and the metadata dict are validated below via get_type_hints.
        assert fields["chunk_id"].type in (int, "int")
        assert fields["score"].type in (float, "float")
        assert fields["dataset_id"].type in (int, "int")

    def test_hit_metadata_is_dict_str_any(self):
        """metadata is dict[str, Any]."""
        Hit = self._hit_cls()
        hints = get_type_hints(Hit, include_extras=False)
        meta_t = hints["metadata"]
        origin = get_origin(meta_t)
        # Either dict[str, Any] or its alias forms
        assert origin in (dict,)
        args = get_args(meta_t)
        assert args[0] is str
        assert args[1] is Any


# ── SearchOptions — defaults ─────────────────────────────────────────────


class TestSearchOptions:
    """SearchOptions defaults must match the plan verbatim."""

    def _options_cls(self):
        from corpus_forge.retrieval.types import SearchOptions

        return SearchOptions

    def test_search_options_is_dataclass(self):
        assert dataclasses.is_dataclass(self._options_cls())

    def test_search_options_is_frozen(self):
        SearchOptions = self._options_cls()
        opts = SearchOptions()
        with pytest.raises(dataclasses.FrozenInstanceError):
            opts.k = 50  # type: ignore[misc]

    def test_defaults_match_plan(self):
        SearchOptions = self._options_cls()
        opts = SearchOptions()
        assert opts.k == 10
        assert opts.dataset is None
        assert opts.fusion == "rrf"
        assert opts.alpha == 0.5
        assert opts.rerank is False
        assert opts.rerank_top_n == 50

    def test_fusion_literal_values(self):
        SearchOptions = self._options_cls()
        hints = get_type_hints(SearchOptions, include_extras=False)
        fusion_t = hints["fusion"]
        allowed = set(get_args(fusion_t)) if get_origin(fusion_t) is typing.Literal else set()
        assert allowed == {"rrf", "alpha"}, (
            f"SearchOptions.fusion must be Literal['rrf','alpha'], got {fusion_t!r}"
        )

    def test_search_options_field_set(self):
        SearchOptions = self._options_cls()
        names = {f.name for f in dataclasses.fields(SearchOptions)}
        # Phase N Wave 3 added ``fast_tier_mode`` + ``fast_tier_top_n``
        # for the static-tier candidate generator.
        assert names == {
            "k",
            "dataset",
            "fusion",
            "alpha",
            "rerank",
            "rerank_top_n",
            "fast_tier_mode",
            "fast_tier_top_n",
        }


# ── RetrievalMetrics ─────────────────────────────────────────────────────


class TestRetrievalMetrics:
    """RetrievalMetrics shape contract."""

    def _metrics_cls(self):
        from corpus_forge.retrieval.types import RetrievalMetrics

        return RetrievalMetrics

    def test_retrieval_metrics_is_dataclass(self):
        assert dataclasses.is_dataclass(self._metrics_cls())

    def test_retrieval_metrics_fields(self):
        names = {f.name for f in dataclasses.fields(self._metrics_cls())}
        assert names == {"ndcg", "mrr", "recall"}

    def test_retrieval_metrics_field_types(self):
        """All three fields are dict[int, float]."""
        RetrievalMetrics = self._metrics_cls()
        hints = get_type_hints(RetrievalMetrics, include_extras=False)
        for name in ("ndcg", "mrr", "recall"):
            t = hints[name]
            assert get_origin(t) is dict, f"{name} must be dict[int,float], got {t!r}"
            args = get_args(t)
            assert args == (int, float), f"{name} args={args}"

    def test_retrieval_metrics_constructible(self):
        RetrievalMetrics = self._metrics_cls()
        m = RetrievalMetrics(
            ndcg={5: 0.91, 10: 0.85},
            mrr={5: 0.7},
            recall={10: 0.95, 20: 0.97},
        )
        assert m.ndcg[5] == 0.91
        assert m.mrr[5] == 0.7
        assert m.recall[20] == 0.97

    def test_retrieval_metrics_is_frozen(self):
        RetrievalMetrics = self._metrics_cls()
        m = RetrievalMetrics(ndcg={}, mrr={}, recall={})
        with pytest.raises(dataclasses.FrozenInstanceError):
            m.ndcg = {1: 0.5}  # type: ignore[misc]
