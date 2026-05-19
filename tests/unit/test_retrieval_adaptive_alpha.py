"""Phase N Wave 1 — adaptive-alpha integration pins.

When ``RetrievalConfig.adaptive_lexical_weight`` is True AND the query is
symbol-shaped AND the active fusion is ``"alpha"``, the effective alpha
passed to ``alpha_blend`` must drop to ``config.symbol_query_alpha``.
Every other combination must preserve the caller-supplied
``options.alpha``.

The test spies on :func:`corpus_forge.retrieval.retriever.alpha_blend`
(imported into the retriever module's namespace) via monkeypatch so the
captured ``alpha`` argument is the contract — we don't need to inspect
the resulting scores, just confirm the right knob was flipped at the
right gate.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import numpy as np
import pytest

from corpus_forge.config import RetrievalConfig
from corpus_forge.retrieval.types import Hit, SearchOptions

# ── tiny fakes ─────────────────────────────────────────────────────────────


class _FakeEmbedder:
    name = "fake-aa"
    provider = "fake"
    model_id = "fake/aa"
    dimension = 4
    normalized = True
    distance = "cosine"

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        return np.ones((len(texts), self.dimension), dtype=np.float32)

    def encode_query(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
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


@pytest.fixture
def fakes() -> tuple[_FakeBackend, _FakeEmbedder]:
    be = _FakeBackend(
        dense_hits=[_hit(1, 1.0), _hit(2, 0.5)],
        lexical_hits=[_hit(2, 1.0, "lexical"), _hit(3, 0.5, "lexical")],
    )
    return be, _FakeEmbedder()


@pytest.fixture
def spy_alpha_blend() -> Iterator[list[float]]:
    """Replace ``alpha_blend`` in the retriever module with a spy.

    Returns the list the spy appends each effective ``alpha`` argument to,
    in call order.  The spy delegates to the real implementation so
    downstream behaviour is unaffected.

    Resilient to ``sys.modules`` re-imports: another test in
    ``test_retrieval_retriever.py`` (``test_imports_dont_pull_in_torch_*``)
    deletes ``corpus_forge.retrieval.retriever`` from ``sys.modules`` and
    re-imports it.  The package-level re-export of ``HybridRetriever`` in
    ``corpus_forge.retrieval.__init__`` still references the OLD module
    object (the class survived sys.modules deletion via the package
    binding), so we patch ``HybridRetriever.search.__globals__`` — that
    dict IS the module ``__dict__`` the method's name lookup resolves
    ``alpha_blend`` against, regardless of which freshly-re-imported
    sibling sits in ``sys.modules``.
    """
    from corpus_forge.retrieval import HybridRetriever
    from corpus_forge.retrieval.fusion import alpha_blend as real_alpha_blend

    globals_ = HybridRetriever.search.__globals__
    original = globals_["alpha_blend"]

    captured: list[float] = []

    def _spy(
        dense: dict[int, float],
        lexical: dict[int, float],
        alpha: float,
    ) -> dict[int, float]:
        captured.append(float(alpha))
        return real_alpha_blend(dense, lexical, alpha)

    globals_["alpha_blend"] = _spy
    try:
        yield captured
    finally:
        globals_["alpha_blend"] = original


# ── construction contract ─────────────────────────────────────────────────


class TestRetrievalConfigInjection:
    def test_hybrid_retriever_accepts_config_kwarg(
        self,
        fakes: tuple[_FakeBackend, _FakeEmbedder],
    ) -> None:
        from corpus_forge.retrieval import HybridRetriever

        be, em = fakes
        cfg = RetrievalConfig(adaptive_lexical_weight=True, symbol_query_alpha=0.3)
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, config=cfg)
        assert r.config is cfg

    def test_config_defaults_to_retrieval_config(
        self,
        fakes: tuple[_FakeBackend, _FakeEmbedder],
    ) -> None:
        """Constructing without ``config`` keeps default behaviour."""
        from corpus_forge.retrieval import HybridRetriever

        be, em = fakes
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1)
        assert isinstance(r.config, RetrievalConfig)
        # Defaults preserve pre-Wave-1 behaviour
        assert r.config.adaptive_lexical_weight is False
        assert r.config.symbol_query_alpha == 0.3


# ── adaptive-alpha dispatch ───────────────────────────────────────────────


class TestAdaptiveAlphaDispatch:
    def test_symbol_query_lowers_alpha_when_enabled(
        self,
        fakes: tuple[_FakeBackend, _FakeEmbedder],
        spy_alpha_blend: list[float],
    ) -> None:
        from corpus_forge.retrieval import HybridRetriever

        be, em = fakes
        cfg = RetrievalConfig(adaptive_lexical_weight=True, symbol_query_alpha=0.3)
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, config=cfg)
        r.search(
            "HybridRetriever.search",
            SearchOptions(k=5, fusion="alpha", alpha=0.7),
        )
        assert spy_alpha_blend == [0.3], (
            f"expected alpha=0.3 (symbol-shaped bump), got {spy_alpha_blend!r}"
        )

    def test_natural_query_keeps_caller_alpha_when_enabled(
        self,
        fakes: tuple[_FakeBackend, _FakeEmbedder],
        spy_alpha_blend: list[float],
    ) -> None:
        from corpus_forge.retrieval import HybridRetriever

        be, em = fakes
        cfg = RetrievalConfig(adaptive_lexical_weight=True, symbol_query_alpha=0.3)
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, config=cfg)
        r.search(
            "how does the watch debounce work",
            SearchOptions(k=5, fusion="alpha", alpha=0.7),
        )
        assert spy_alpha_blend == [0.7], (
            f"expected alpha=0.7 (no bump on NL query), got {spy_alpha_blend!r}"
        )

    def test_symbol_query_keeps_alpha_when_disabled(
        self,
        fakes: tuple[_FakeBackend, _FakeEmbedder],
        spy_alpha_blend: list[float],
    ) -> None:
        """``adaptive_lexical_weight=False`` (default) → no bump ever fires."""
        from corpus_forge.retrieval import HybridRetriever

        be, em = fakes
        cfg = RetrievalConfig(adaptive_lexical_weight=False, symbol_query_alpha=0.3)
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, config=cfg)
        r.search(
            "HybridRetriever.search",
            SearchOptions(k=5, fusion="alpha", alpha=0.7),
        )
        assert spy_alpha_blend == [0.7], (
            f"expected alpha=0.7 (flag off, no bump), got {spy_alpha_blend!r}"
        )

    def test_natural_query_keeps_alpha_when_disabled(
        self,
        fakes: tuple[_FakeBackend, _FakeEmbedder],
        spy_alpha_blend: list[float],
    ) -> None:
        from corpus_forge.retrieval import HybridRetriever

        be, em = fakes
        cfg = RetrievalConfig(adaptive_lexical_weight=False)
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, config=cfg)
        r.search(
            "how does the watch debounce work",
            SearchOptions(k=5, fusion="alpha", alpha=0.7),
        )
        assert spy_alpha_blend == [0.7]


# ── RRF path is unaffected ────────────────────────────────────────────────


class TestRrfPathUnchanged:
    """``fusion="rrf"`` has no alpha — the bump is a no-op on this path."""

    def test_rrf_does_not_call_alpha_blend_even_for_symbol(
        self,
        fakes: tuple[_FakeBackend, _FakeEmbedder],
        spy_alpha_blend: list[float],
    ) -> None:
        from corpus_forge.retrieval import HybridRetriever

        be, em = fakes
        cfg = RetrievalConfig(adaptive_lexical_weight=True, symbol_query_alpha=0.3)
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, config=cfg)
        r.search(
            "HybridRetriever.search",
            SearchOptions(k=5, fusion="rrf", alpha=0.7),
        )
        assert spy_alpha_blend == [], (
            f"alpha_blend must not be invoked under RRF; got calls={spy_alpha_blend!r}"
        )

    def test_rrf_returns_results_even_with_adaptive_flag(
        self,
        fakes: tuple[_FakeBackend, _FakeEmbedder],
    ) -> None:
        """RRF path stays green end-to-end with the adaptive flag on."""
        from corpus_forge.retrieval import HybridRetriever

        be, em = fakes
        cfg = RetrievalConfig(adaptive_lexical_weight=True, symbol_query_alpha=0.3)
        r = HybridRetriever(backend=be, embedder=em, embedder_id=1, config=cfg)
        out: list[Any] = r.search(
            "HybridRetriever.search",
            SearchOptions(k=3, fusion="rrf"),
        )
        assert isinstance(out, list)
        assert all(h.source == "fused" for h in out)
