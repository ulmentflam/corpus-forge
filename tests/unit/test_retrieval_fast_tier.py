"""Phase N Wave 3 — fast-tier retrieval branching pins.

The Wave 3 fast tier wires an OPTIONAL second embedder
(``model2vec`` / ``potion-code-16M``, but the type is generic) into
``HybridRetriever`` as a candidate-generator pre-pass.

Three modes (per ``SearchOptions.fast_tier_mode``):

- ``"skip"`` (default): current behaviour.  The fast embedder is never
  queried even when wired.  Pre-Wave-3 behaviour preserved verbatim.
- ``"shortcut"``: fast embedder runs first → top-``fast_tier_top_n``
  chunk_ids seed a candidate pool → main dense + lexical run filtered
  to that pool → fuse + rerank + Wave 2 boost compose normally.  Best
  quality preserved + p50 drops because the reranker only sees the
  candidate slice.
- ``"only"``: fast embedder runs alone → top-k.  Lowest latency.  No
  lexical, no rerank.  Lower quality acceptable.

Pins exercised here:

1. ``SearchOptions.fast_tier_mode`` defaults to ``"skip"`` and
   ``fast_tier_top_n`` defaults to ``200``.  The literal is
   ``{"skip","shortcut","only"}``.  ``fast_tier_top_n`` validated >= 1.

2. ``HybridRetriever`` accepts an optional ``fast_embedder`` constructor
   kwarg.  Default ``None``.

3. ``"skip"`` mode (with or without ``fast_embedder``): the fast
   embedder is NEVER called.  Spy on it.

4. ``"only"`` mode: only the fast embedder's ``encode_query`` is called;
   ``self.embedder.encode_query`` is NOT called; only
   ``backend.search_dense(fast_embedder_id, ...)`` is called (no
   ``search_lexical``); no rerank even if a reranker is wired.

5. ``"shortcut"`` mode: fast embedder runs first → top-``top_n``
   chunk_ids are passed as ``chunk_ids=`` to BOTH
   ``backend.search_dense(main_id, ..., chunk_ids=...)`` AND
   ``backend.search_lexical(..., chunk_ids=...)``.  Rerank fires if a
   reranker is wired.  Wave 2 boost (when enabled) still composes.

6. Construction-time guards:
   - ``fast_tier_mode != "skip"`` AND ``fast_embedder is None`` → raise
     a clear error pointing at how to wire it (at search time, not
     construction — same pattern as ``rerank=True`` + ``reranker=None``
     which gracefully degrades.  Wave 3 picks the safer "hard error"
     side for the fast tier — silently downgrading to skip would mask
     a misconfigured production deploy).

7. Backend doesn't support ``chunk_ids`` filter + shortcut mode →
   hard error.  Detected by ``inspect.signature`` on the backend's
   ``search_dense`` method.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import numpy as np
import pytest

from corpus_forge.retrieval.types import Hit, SearchOptions

# ── tiny fakes (mirror test_retrieval_retriever.py) ───────────────────────


class _FakeEmbedder:
    """Recording embedder.  Distinct embedding values per instance so
    the spy can prove which one was called."""

    def __init__(self, *, name: str = "fake", dim: int = 4, fill: float = 0.5) -> None:
        self.name = name
        self.provider = "fake"
        self.model_id = f"fake/{name}"
        self.dimension = dim
        self.normalized = True
        self.distance = "cosine"
        self._fill = fill
        self.encode_calls: list[list[str]] = []
        self.encode_query_calls: list[list[str]] = []

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        self.encode_calls.append(list(texts))
        return np.full((len(texts), self.dimension), self._fill, dtype=np.float32)

    def encode_query(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        self.encode_query_calls.append(list(texts))
        return np.full((len(texts), self.dimension), self._fill, dtype=np.float32)

    def warmup(self) -> None:  # pragma: no cover — protocol only
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


class _FilterAwareFakeBackend:
    """Fake backend whose ``search_dense`` / ``search_lexical`` accept
    ``chunk_ids``.  Records every call so the test can assert the
    filter was passed through."""

    def __init__(
        self,
        *,
        dense_by_embedder: dict[int, list[Hit]] | None = None,
        lexical_hits: list[Hit] | None = None,
    ) -> None:
        self.dense_by_embedder = dense_by_embedder or {}
        self.lexical_hits = lexical_hits or []
        self.search_dense_calls: list[dict[str, Any]] = []
        self.search_lexical_calls: list[dict[str, Any]] = []

    def search_dense(
        self,
        embedder_id: int,
        query_vector: np.ndarray,
        *,
        k: int,
        dataset_id: int | None = None,
        chunk_ids: frozenset[int] | None = None,
    ) -> list[Hit]:
        self.search_dense_calls.append(
            {
                "embedder_id": embedder_id,
                "k": k,
                "dataset_id": dataset_id,
                "chunk_ids": (frozenset(chunk_ids) if chunk_ids is not None else None),
            }
        )
        hits = self.dense_by_embedder.get(embedder_id, [])
        if chunk_ids is not None:
            hits = [h for h in hits if h.chunk_id in chunk_ids]
        return list(hits[:k])

    def search_lexical(
        self,
        query: str,
        *,
        k: int,
        dataset_id: int | None = None,
        chunk_ids: frozenset[int] | None = None,
    ) -> list[Hit]:
        self.search_lexical_calls.append(
            {
                "query": query,
                "k": k,
                "dataset_id": dataset_id,
                "chunk_ids": (frozenset(chunk_ids) if chunk_ids is not None else None),
            }
        )
        hits = self.lexical_hits
        if chunk_ids is not None:
            hits = [h for h in hits if h.chunk_id in chunk_ids]
        return list(hits[:k])

    def find_dataset_id_by_name(self, name: str) -> int | None:
        return None

    def get_chunk(self, chunk_id: int) -> dict | None:
        return None

    def list_datasets(self) -> list[dict]:
        return []

    @contextmanager
    def lock_source(self, key: str) -> Iterator[None]:
        yield


class _LegacyFakeBackend:
    """Mirror of ``_FilterAwareFakeBackend`` but WITHOUT ``chunk_ids``
    on ``search_dense`` / ``search_lexical``.  Used to pin the
    graceful-failure path when the backend can't filter."""

    def __init__(self) -> None:
        self.search_dense_calls: list[dict[str, Any]] = []
        self.search_lexical_calls: list[dict[str, Any]] = []

    def search_dense(
        self,
        embedder_id: int,
        query_vector: np.ndarray,
        *,
        k: int,
        dataset_id: int | None = None,
    ) -> list[Hit]:
        self.search_dense_calls.append(
            {"embedder_id": embedder_id, "k": k, "dataset_id": dataset_id}
        )
        return []

    def search_lexical(
        self,
        query: str,
        *,
        k: int,
        dataset_id: int | None = None,
    ) -> list[Hit]:
        self.search_lexical_calls.append({"query": query, "k": k, "dataset_id": dataset_id})
        return []

    def find_dataset_id_by_name(self, name: str) -> int | None:
        return None

    def get_chunk(self, chunk_id: int) -> dict | None:
        return None

    def list_datasets(self) -> list[dict]:
        return []

    @contextmanager
    def lock_source(self, key: str) -> Iterator[None]:
        yield


class _SpyReranker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[Hit], int]] = []

    def rerank(self, query: str, hits: list[Hit], *, top_n: int) -> list[Hit]:
        self.calls.append((query, list(hits), top_n))
        out: list[Hit] = []
        for h in hits[:top_n]:
            out.append(
                Hit(
                    chunk_id=h.chunk_id,
                    score=h.score,  # passthrough — order is what we test
                    text=h.text,
                    document_id=h.document_id,
                    source_uri=h.source_uri,
                    title=h.title,
                    dataset_id=h.dataset_id,
                    metadata=h.metadata,
                    source="reranked",  # type: ignore[arg-type]
                )
            )
        return out


# ── SearchOptions surface ─────────────────────────────────────────────────


class TestSearchOptionsFastTierKnobs:
    def test_default_mode_is_skip(self) -> None:
        opts = SearchOptions()
        assert opts.fast_tier_mode == "skip"

    def test_default_top_n_is_200(self) -> None:
        opts = SearchOptions()
        assert opts.fast_tier_top_n == 200

    def test_mode_accepts_shortcut(self) -> None:
        opts = SearchOptions(fast_tier_mode="shortcut")
        assert opts.fast_tier_mode == "shortcut"

    def test_mode_accepts_only(self) -> None:
        opts = SearchOptions(fast_tier_mode="only")
        assert opts.fast_tier_mode == "only"

    def test_top_n_overridable(self) -> None:
        opts = SearchOptions(fast_tier_top_n=50)
        assert opts.fast_tier_top_n == 50


# ── construction ──────────────────────────────────────────────────────────


class TestConstruction:
    def test_accepts_fast_embedder_kwarg(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        main = _FakeEmbedder(name="main", fill=0.5)
        fast = _FakeEmbedder(name="fast", fill=0.9)
        be = _FilterAwareFakeBackend()
        r = HybridRetriever(
            backend=be,
            embedder=main,
            embedder_id=1,
            fast_embedder=fast,
            fast_embedder_id=2,
        )
        assert r.fast_embedder is fast
        assert r.fast_embedder_id == 2

    def test_fast_embedder_defaults_to_none(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        main = _FakeEmbedder(name="main")
        be = _FilterAwareFakeBackend()
        r = HybridRetriever(backend=be, embedder=main, embedder_id=1)
        assert r.fast_embedder is None
        assert r.fast_embedder_id is None


# ── skip mode (default) ───────────────────────────────────────────────────


class TestSkipMode:
    def test_skip_default_never_calls_fast(self) -> None:
        """Default behaviour: fast embedder is wired but never called."""
        from corpus_forge.retrieval import HybridRetriever

        main = _FakeEmbedder(name="main", fill=0.5)
        fast = _FakeEmbedder(name="fast", fill=0.9)
        be = _FilterAwareFakeBackend(
            dense_by_embedder={1: [_hit(7, 0.9)]},
            lexical_hits=[_hit(7, 0.8, "lexical")],
        )
        r = HybridRetriever(
            backend=be,
            embedder=main,
            embedder_id=1,
            fast_embedder=fast,
            fast_embedder_id=2,
        )
        r.search("q", SearchOptions(k=3))  # default fast_tier_mode="skip"

        # Fast embedder never queried.
        assert fast.encode_query_calls == []
        # Main embedder used as before.
        assert main.encode_query_calls != []
        # Only the main embedder's dense table was hit.
        ids_hit = {c["embedder_id"] for c in be.search_dense_calls}
        assert ids_hit == {1}

    def test_skip_explicit_never_calls_fast(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        main = _FakeEmbedder(name="main")
        fast = _FakeEmbedder(name="fast")
        be = _FilterAwareFakeBackend()
        r = HybridRetriever(
            backend=be,
            embedder=main,
            embedder_id=1,
            fast_embedder=fast,
            fast_embedder_id=2,
        )
        r.search("q", SearchOptions(k=3, fast_tier_mode="skip"))
        assert fast.encode_query_calls == []


# ── only mode ─────────────────────────────────────────────────────────────


class TestOnlyMode:
    def test_only_calls_fast_not_main(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        main = _FakeEmbedder(name="main", fill=0.5)
        fast = _FakeEmbedder(name="fast", fill=0.9)
        be = _FilterAwareFakeBackend(
            dense_by_embedder={2: [_hit(5, 0.9), _hit(7, 0.8)]},
        )
        r = HybridRetriever(
            backend=be,
            embedder=main,
            embedder_id=1,
            fast_embedder=fast,
            fast_embedder_id=2,
        )
        out = r.search("q", SearchOptions(k=2, fast_tier_mode="only"))

        # Fast embedder used.
        assert len(fast.encode_query_calls) == 1
        # Main embedder NOT used.
        assert main.encode_query_calls == []
        # Only the fast embedder's table queried.
        assert len(be.search_dense_calls) == 1
        assert be.search_dense_calls[0]["embedder_id"] == 2
        # No lexical fan-out.
        assert be.search_lexical_calls == []
        # Top-k results land.
        assert len(out) == 2
        # Hits attributed to fast tier (label preserved or "fused" — pin
        # the latter since that's the source-string already documented
        # for non-reranked hits).
        for h in out:
            assert h.source in {"fused", "dense"}

    def test_only_skips_rerank_even_when_wired(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        main = _FakeEmbedder(name="main")
        fast = _FakeEmbedder(name="fast")
        rr = _SpyReranker()
        be = _FilterAwareFakeBackend(
            dense_by_embedder={2: [_hit(1, 0.9)]},
        )
        r = HybridRetriever(
            backend=be,
            embedder=main,
            embedder_id=1,
            fast_embedder=fast,
            fast_embedder_id=2,
            reranker=rr,
        )
        r.search(
            "q",
            SearchOptions(k=2, fast_tier_mode="only", rerank=True),
        )
        # Reranker NOT called in only mode.
        assert rr.calls == []


# ── shortcut mode ─────────────────────────────────────────────────────────


class TestShortcutMode:
    def test_shortcut_passes_chunk_ids_to_dense_and_lexical(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        main = _FakeEmbedder(name="main")
        fast = _FakeEmbedder(name="fast")
        # Fast tier returns chunk_ids {3, 5, 7}.
        be = _FilterAwareFakeBackend(
            dense_by_embedder={
                2: [_hit(3, 0.99), _hit(5, 0.98), _hit(7, 0.97)],
                1: [_hit(3, 0.8), _hit(5, 0.7), _hit(7, 0.6), _hit(11, 0.5)],
            },
            lexical_hits=[
                _hit(3, 0.5, "lexical"),
                _hit(5, 0.4, "lexical"),
                _hit(7, 0.3, "lexical"),
                _hit(11, 0.2, "lexical"),
            ],
        )
        r = HybridRetriever(
            backend=be,
            embedder=main,
            embedder_id=1,
            fast_embedder=fast,
            fast_embedder_id=2,
        )
        r.search(
            "q",
            SearchOptions(k=3, fast_tier_mode="shortcut", fast_tier_top_n=3),
        )

        # Both embedders queried — fast for the candidate set, main for
        # the filtered dense search.
        assert len(fast.encode_query_calls) == 1
        assert len(main.encode_query_calls) == 1

        # Backend dense was called twice: once for the fast tier (no
        # chunk_ids), once for the main tier (with chunk_ids set).
        embedder_ids_called = [c["embedder_id"] for c in be.search_dense_calls]
        assert embedder_ids_called.count(2) == 1  # fast tier
        assert embedder_ids_called.count(1) == 1  # main tier

        # Find the main-tier dense call; its chunk_ids must be the
        # fast tier's top-N.
        main_call = next(c for c in be.search_dense_calls if c["embedder_id"] == 1)
        assert main_call["chunk_ids"] == frozenset({3, 5, 7})

        # Lexical call must carry the same chunk_ids filter.
        assert len(be.search_lexical_calls) == 1
        assert be.search_lexical_calls[0]["chunk_ids"] == frozenset({3, 5, 7})

    def test_shortcut_returns_only_candidate_pool(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        main = _FakeEmbedder(name="main")
        fast = _FakeEmbedder(name="fast")
        # Fast tier's top-N picks {3, 5}.  Main dense would otherwise
        # return chunk 11, but the chunk_ids filter must exclude it.
        be = _FilterAwareFakeBackend(
            dense_by_embedder={
                2: [_hit(3, 0.99), _hit(5, 0.95)],
                1: [_hit(3, 0.8), _hit(5, 0.7), _hit(11, 0.5)],
            },
            lexical_hits=[_hit(3, 0.5, "lexical"), _hit(11, 0.4, "lexical")],
        )
        r = HybridRetriever(
            backend=be,
            embedder=main,
            embedder_id=1,
            fast_embedder=fast,
            fast_embedder_id=2,
        )
        out = r.search(
            "q",
            SearchOptions(k=5, fast_tier_mode="shortcut", fast_tier_top_n=2),
        )
        returned_ids = {h.chunk_id for h in out}
        # 11 must be excluded — it was outside the candidate pool.
        assert 11 not in returned_ids
        # The candidates that survived the fast tier remain.
        assert returned_ids.issubset({3, 5})

    def test_shortcut_runs_reranker(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        main = _FakeEmbedder(name="main")
        fast = _FakeEmbedder(name="fast")
        rr = _SpyReranker()
        be = _FilterAwareFakeBackend(
            dense_by_embedder={
                2: [_hit(3, 0.9), _hit(5, 0.8)],
                1: [_hit(3, 0.8), _hit(5, 0.7)],
            },
            lexical_hits=[_hit(3, 0.5, "lexical")],
        )
        r = HybridRetriever(
            backend=be,
            embedder=main,
            embedder_id=1,
            fast_embedder=fast,
            fast_embedder_id=2,
            reranker=rr,
        )
        r.search(
            "q",
            SearchOptions(k=2, fast_tier_mode="shortcut", fast_tier_top_n=2, rerank=True),
        )
        # Reranker IS called in shortcut mode.
        assert len(rr.calls) == 1


# ── guards ────────────────────────────────────────────────────────────────


class TestGuards:
    def test_missing_fast_embedder_raises_on_only(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        main = _FakeEmbedder(name="main")
        be = _FilterAwareFakeBackend()
        r = HybridRetriever(backend=be, embedder=main, embedder_id=1)
        with pytest.raises((ValueError, RuntimeError), match=r"fast_embedder"):
            r.search("q", SearchOptions(k=3, fast_tier_mode="only"))

    def test_missing_fast_embedder_raises_on_shortcut(self) -> None:
        from corpus_forge.retrieval import HybridRetriever

        main = _FakeEmbedder(name="main")
        be = _FilterAwareFakeBackend()
        r = HybridRetriever(backend=be, embedder=main, embedder_id=1)
        with pytest.raises((ValueError, RuntimeError), match=r"fast_embedder"):
            r.search("q", SearchOptions(k=3, fast_tier_mode="shortcut"))

    def test_shortcut_against_legacy_backend_raises(self) -> None:
        """Backend without ``chunk_ids`` filter support → hard error.

        Silently falling back to skip would mask a misconfigured prod
        deploy.  Hard error keeps the wire honest.
        """
        from corpus_forge.retrieval import HybridRetriever

        main = _FakeEmbedder(name="main")
        fast = _FakeEmbedder(name="fast")
        be = _LegacyFakeBackend()
        r = HybridRetriever(
            backend=be,
            embedder=main,
            embedder_id=1,
            fast_embedder=fast,
            fast_embedder_id=2,
        )
        with pytest.raises((ValueError, RuntimeError, TypeError), match=r"chunk_ids"):
            r.search(
                "q",
                SearchOptions(k=2, fast_tier_mode="shortcut", fast_tier_top_n=3),
            )


# ── config plumbing ───────────────────────────────────────────────────────


class TestRetrievalConfigFastTierName:
    def test_default_fast_tier_embedder_name_is_none(self) -> None:
        from corpus_forge.config import RetrievalConfig

        rc = RetrievalConfig()
        assert rc.fast_tier_embedder_name is None

    def test_accepts_name(self) -> None:
        from corpus_forge.config import RetrievalConfig

        rc = RetrievalConfig(fast_tier_embedder_name="fast-tier")
        assert rc.fast_tier_embedder_name == "fast-tier"

    def test_validator_requires_name_present_in_embedders(self) -> None:
        """``Config(...)`` must reject a fast_tier_embedder_name that
        isn't in the ``[[embedders]]`` list — otherwise the runtime
        registry lookup would silently return ``None``."""
        from pydantic import ValidationError

        from corpus_forge.config import Config

        toml = {
            "backend": {"kind": "sqlite", "dsn": "/tmp/x.db"},
            "daemon": {},
            "datasets": [
                {
                    "name": "ds",
                    "kind": "text",
                    "sources": [{"plugin": "vault", "vault_root": "/tmp", "chunker": "markdown"}],
                }
            ],
            "embedders": [
                {
                    "name": "main",
                    "provider": "sentence_transformers",
                    "model_id": "x/y",
                    "dimension": 384,
                }
            ],
            "retrieval": {"fast_tier_embedder_name": "does-not-exist"},
        }
        with pytest.raises(ValidationError, match=r"fast_tier_embedder_name"):
            Config(**toml)

    def test_validator_accepts_name_present_in_embedders(self) -> None:
        from corpus_forge.config import Config

        toml = {
            "backend": {"kind": "sqlite", "dsn": "/tmp/x.db"},
            "daemon": {},
            "datasets": [
                {
                    "name": "ds",
                    "kind": "text",
                    "sources": [{"plugin": "vault", "vault_root": "/tmp", "chunker": "markdown"}],
                }
            ],
            "embedders": [
                {
                    "name": "main",
                    "provider": "sentence_transformers",
                    "model_id": "x/y",
                    "dimension": 384,
                },
                {
                    "name": "fast",
                    "provider": "model2vec",
                    "model_id": "minishlab/potion-code-16M",
                    "dimension": 256,
                },
            ],
            "retrieval": {"fast_tier_embedder_name": "fast"},
        }
        cfg = Config(**toml)
        assert cfg.retrieval.fast_tier_embedder_name == "fast"
