"""Phase R2 + R4 — `Retriever` protocol + `HybridRetriever` implementation.

`HybridRetriever` is the user-facing entry point for hybrid (dense +
lexical) retrieval.  It composes:

- a ``StorageBackend`` (calls ``search_dense`` and ``search_lexical`` on
  the protocol — never reaches around to ``_execute``);
- an ``Embedder`` (calls ``encode_query``, not ``encode`` — see R2 plan);
- an ``embedder_id`` (the backend's row id for the embedder; the dense
  search is per-embedder);
- an optional ``reranker`` (R4 wires this; default ``None`` — even when
  configured, the reranker is consulted only when ``options.rerank=True``).

Fusion strategy is per-call via ``SearchOptions.fusion``:

- ``"rrf"`` (default): rank-only reciprocal rank fusion.  Because RRF is
  scale-free, no score normalisation is needed — sidesteps the R1
  carry-over about SQLite/Postgres score-scale mismatch.
- ``"alpha"``: per-list min-max normalisation of each backend's scores,
  then linear combination via ``alpha * dense + (1 - alpha) * lexical``.

Dataset filter (``SearchOptions.dataset``) is resolved through
``backend.find_dataset_id_by_name(name)`` and pushed straight to both
backend calls.  An unresolvable dataset name returns an empty list (does
NOT silently leak across datasets).

Rerank path (R4)
----------------

When ``options.rerank=True`` AND ``self.reranker is not None``:

1. Fuse the dense + lexical lists as above, but materialise the top
   ``options.rerank_top_n`` (NOT ``options.k``) fused hits instead of
   just the top-k.  ``rerank_top_n`` defaults to 50.
2. Pass those hits to ``self.reranker.rerank(query, hits, top_n=options.k)``.
3. Return the reranker's output verbatim — HybridRetriever does NOT
   re-sort, re-truncate, or otherwise massage what the reranker
   returns.  Source label flips to ``"reranked"`` (set by the reranker).

When ``options.rerank=True`` AND ``self.reranker is None``: behave as if
``rerank`` were false — return the fused top-k.  No crash.  This lets a
caller toggle rerank purely via the search options without having to
also tear down the retriever.

When ``options.rerank=False`` (the default): the reranker is never
consulted, even if one is configured.  This is the surprise-free default
that prevents accidental 600 MB model downloads.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import replace as _dc_replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

from corpus_forge.retrieval.fusion import alpha_blend, reciprocal_rank_fusion
from corpus_forge.retrieval.normalize import min_max
from corpus_forge.retrieval.query_shape import is_symbol_shaped
from corpus_forge.retrieval.types import Hit, SearchOptions, SearchResponse

if TYPE_CHECKING:
    from corpus_forge.backends.base import StorageBackend
    from corpus_forge.config import RetrievalConfig
    from corpus_forge.embedders.base import Embedder


# Multiplier applied to ``k`` before calling each backend search.  Standard
# hybrid-retrieval practice: pull ~2-4x more from each backend so fusion has
# headroom before truncating to top-k.
_OVERFETCH_MULTIPLIER = 2


class Retriever(Protocol):
    """A pluggable retriever.

    Implementations promise: given a query string and ``SearchOptions``,
    return a ``SearchResponse`` whose ``results`` list has length ≤
    ``options.k``.
    """

    def search(self, query: str, options: SearchOptions) -> SearchResponse: ...


class HybridRetriever:
    """Hybrid (dense + lexical) retriever with pluggable fusion.

    Args:
        backend: any ``StorageBackend``.  Calls ``search_dense`` /
            ``search_lexical`` / ``find_dataset_id_by_name`` through the
            protocol — never reaches around to backend-private members.
        embedder: any ``Embedder``.  The query path calls
            ``embedder.encode_query(...)`` (NOT ``encode``); for symmetric
            models this delegates to ``encode``, for Qwen3 it prepends the
            documented instruction prompt.
        embedder_id: row id of the embedder in the backend.  Same value as
            ``backend.register_embedder(embedder)``.
        reranker: R4 plumbing.  Stored but never called in R2; the search
            path is purely fusion + truncation.
    """

    def __init__(
        self,
        backend: StorageBackend,
        embedder: Embedder,
        embedder_id: int,
        reranker: Any | None = None,
        config: RetrievalConfig | None = None,
        *,
        fast_embedder: Embedder | None = None,
        fast_embedder_id: int | None = None,
    ) -> None:
        # Lazy-import so the retriever module doesn't pull in the full
        # Config pydantic graph just to construct a retriever without an
        # explicit config (also avoids a potential circular import with
        # any future cross-references in `corpus_forge.config`).
        if config is None:
            from corpus_forge.config import RetrievalConfig as _RC  # noqa: PLC0415

            config = _RC()
        self.backend = backend
        self.embedder = embedder
        self.embedder_id = embedder_id
        self.reranker = reranker
        self.config = config
        # Phase N Wave 3 — optional static-tier fast embedder.  Stored
        # but never queried when ``SearchOptions.fast_tier_mode ==
        # "skip"`` (the default), so wiring a fast embedder up front
        # is a pure pre-flight cost — the search path stays identical
        # to pre-Wave-3 behaviour until the caller flips the mode.
        self.fast_embedder = fast_embedder
        self.fast_embedder_id = fast_embedder_id

    # ── public API ───────────────────────────────────────────────────────

    def search(self, query: str, options: SearchOptions) -> SearchResponse:
        """Run the hybrid search.

        Steps:
        1. Resolve the dataset name (if any) → dataset_id; bail out empty
           if the name is unknown so we never leak across datasets.
        2. Phase N Wave 3 fast-tier dispatch (mode ∈ ``skip / shortcut / only``).
           When ``"only"`` we short-circuit here and return; when
           ``"shortcut"`` we compute the candidate-id pool that gates
           steps 3+; when ``"skip"`` (default) we drop straight through.
        3. Encode the query via ``embedder.encode_query``.
        4. Fan out ``backend.search_dense`` + ``backend.search_lexical``,
           each over-fetching by ``_OVERFETCH_MULTIPLIER * k`` to give
           fusion room.  In shortcut mode, both are pre-filtered to the
           Wave 3 candidate pool.
        5. Fuse: RRF or alpha-blend per ``options.fusion``.
        6. Materialise into ``Hit`` objects with ``source="fused"`` and
           return the top-``k`` wrapped in a ``SearchResponse``.
        """
        # Capture provenance metadata upfront — before any I/O so the
        # started_at timestamp reflects the true start of the call.
        started_at = datetime.now(UTC)
        query_id = uuid.uuid4().hex

        # ── Step 1: dataset resolution ─────────────────────────────────
        dataset_id: int | None = None
        if options.dataset is not None:
            dataset_id = self.backend.find_dataset_id_by_name(options.dataset)
            if dataset_id is None:
                # Unknown dataset name → empty result.  Do NOT call the
                # backend with dataset_id=None — that would silently
                # search across all datasets, which is the leak the
                # caller is explicitly trying to prevent.
                return SearchResponse(
                    query_id=query_id,
                    results=[],
                    query=query,
                    dataset_id=None,
                    started_at=started_at,
                )

        # ── Step 2: Phase N Wave 3 — fast-tier dispatch ────────────────
        # ``"only"`` short-circuits here.  ``"shortcut"`` computes the
        # candidate-id pool that gates the main fan-out.  ``"skip"``
        # (default) leaves ``candidate_ids = None`` and drops through
        # to the pre-Wave-3 code path.
        candidate_ids: frozenset[int] | None = None
        if options.fast_tier_mode != "skip":
            if self.fast_embedder is None or self.fast_embedder_id is None:
                raise ValueError(
                    "SearchOptions.fast_tier_mode="
                    f"{options.fast_tier_mode!r} requires a fast_embedder "
                    "wired at HybridRetriever construction time.  Pass "
                    "fast_embedder=... and fast_embedder_id=... to the "
                    "HybridRetriever(...) call (see Phase N Wave 3 docs)."
                )
            # Both shortcut + only paths need to verify the backend
            # supports the chunk_ids filter.  The only-mode path will
            # also pass chunk_ids=None to the dense call, but having
            # the kwarg signature consistent across backends is what
            # we're enforcing.
            self._require_chunk_ids_filter()
            if options.fast_tier_mode == "only":
                return self._search_fast_only(
                    query,
                    options,
                    dataset_id=dataset_id,
                    query_id=query_id,
                    started_at=started_at,
                )
            # shortcut: build the candidate pool.
            candidate_ids = self._fast_tier_candidate_ids(query, options, dataset_id=dataset_id)

        # ── Step 3: encode the query ──────────────────────────────────
        # `encode_query` is the asymmetric-aware entry point.  For symmetric
        # models BaseEmbedder.encode_query just delegates to encode.
        query_vectors = self.embedder.encode_query([query])
        # Shape: (1, dim).  Reduce to a 1-D vector for the backend call.
        qvec = query_vectors[0]

        # ── Step 4: fan out the two backend searches ──────────────────
        overfetch_k = max(options.k * _OVERFETCH_MULTIPLIER, options.k)
        # In shortcut mode, ``chunk_ids`` restricts BOTH search calls to
        # the fast-tier candidate pool — that's the load-bearing
        # latency win (the cross-encoder downstream sees only the
        # candidate slice).
        dense_kwargs: dict[str, Any] = {
            "k": overfetch_k,
            "dataset_id": dataset_id,
        }
        lex_kwargs: dict[str, Any] = {
            "k": overfetch_k,
            "dataset_id": dataset_id,
        }
        if candidate_ids is not None:
            dense_kwargs["chunk_ids"] = candidate_ids
            lex_kwargs["chunk_ids"] = candidate_ids
        dense_hits = self.backend.search_dense(
            self.embedder_id,
            qvec,
            **dense_kwargs,
        )
        lexical_hits = self.backend.search_lexical(
            query,
            **lex_kwargs,
        )

        # Build chunk_id → Hit map for quick lookup post-fusion.  When the
        # same chunk_id appears in both lists, prefer the dense Hit's
        # metadata (arbitrary but stable choice).
        hits_by_id: dict[int, Hit] = {}
        for h in lexical_hits:
            hits_by_id[h.chunk_id] = h
        for h in dense_hits:
            hits_by_id[h.chunk_id] = h

        # ── Step 4: fuse ───────────────────────────────────────────────
        fused_scores: dict[int, float]
        if options.fusion == "rrf":
            # RRF is rank-only — there's no alpha to bump on this path.
            # The Wave 1 adaptive lexical-weight knob is a no-op here.
            dense_ranking = [h.chunk_id for h in dense_hits]
            lexical_ranking = [h.chunk_id for h in lexical_hits]
            fused_scores = reciprocal_rank_fusion([dense_ranking, lexical_ranking])
        else:
            # alpha path: per-list min-max normalise THEN blend.
            dense_norm = self._normalize_to_dict(dense_hits)
            lexical_norm = self._normalize_to_dict(lexical_hits)
            # Phase N Wave 1 — adaptive lexical-weight bump.  When the
            # caller has opted in via config AND the query "looks like"
            # a code symbol, lower the effective alpha so the lexical
            # (BM25) signal contributes more to the blend.  Default
            # config keeps the bump disabled, preserving pre-Wave-1
            # behaviour exactly.
            effective_alpha = options.alpha
            if self.config.adaptive_lexical_weight and is_symbol_shaped(query):
                effective_alpha = self.config.symbol_query_alpha
            fused_scores = alpha_blend(dense_norm, lexical_norm, alpha=effective_alpha)

        # ── Step 4b: Phase N Wave 2 — pre-rerank definition boost ─────
        # Applied AFTER fusion, BEFORE the rerank-slice truncation, so
        # the boost can lift a definition into the slice the reranker
        # later sees.  Wave 1's bench finding (cross-encoder washes out
        # fusion-stage signal) means this alone isn't enough — the load-
        # bearing application is the post-rerank pass below.  Both are
        # gated by the same config flag but tuned with independent
        # multipliers so a follow-up experiment can compare them.
        #
        # The boost ONLY fires on symbol-shaped queries (Wave 1's
        # heuristic, applied to the whole query string).  Natural-
        # language queries like "how does the watch debounce work"
        # tokenise to common English words that frequently overlap
        # with identifier names — applying the boost there produced
        # collateral damage on concept-category MRR during the Wave 2
        # gate's RED phase.  Gating on `is_symbol_shaped(query)` keeps
        # the boost on the queries it was designed for (identifier /
        # accessor lookups).
        boost_fires = self.config.definition_boost_enabled and is_symbol_shaped(query)
        if boost_fires:
            self._apply_definition_boost(
                query,
                fused_scores,
                hits_by_id,
                multiplier=self.config.definition_boost_factor_pre_rerank,
            )

        # ── Step 5: materialise & truncate ────────────────────────────
        # Sort descending by fused score; ties broken by chunk_id (stable).
        ordered = sorted(fused_scores.items(), key=lambda kv: (-kv[1], kv[0]))

        # When rerank is active, we materialise top-`rerank_top_n` fused
        # hits so the reranker has headroom.  Otherwise just top-k.
        rerank_active = options.rerank and self.reranker is not None
        materialise_count = options.rerank_top_n if rerank_active else options.k

        top: list[Hit] = []
        for cid, score in ordered[:materialise_count]:
            src = hits_by_id.get(cid)
            if src is None:
                continue
            top.append(
                Hit(
                    chunk_id=cid,
                    score=float(score),
                    text=src.text,
                    document_id=src.document_id,
                    source_uri=src.source_uri,
                    title=src.title,
                    dataset_id=src.dataset_id,
                    metadata=src.metadata,
                    source="fused",
                )
            )

        # ── Step 6: rerank (R4) ───────────────────────────────────────
        # Only when the caller explicitly asks AND a reranker is wired.
        # We pass `top_n=options.k` so the reranker returns the final
        # top-k.  HybridRetriever does NOT re-sort the reranker's output
        # — the reranker is the authority on the final order *unless*
        # the Phase N Wave 2 post-rerank boost is enabled, in which
        # case the boost re-sorts after applying its multiplier (see
        # Step 7 below).
        if rerank_active:
            # `self.reranker` is not None here (checked in `rerank_active`).
            assert self.reranker is not None
            reranked = self.reranker.rerank(query, top, top_n=options.k)

            # ── Step 7: Phase N Wave 2 — post-rerank definition boost ─
            # The cross-encoder reranker emits its own score scale and
            # discards the upstream fused score.  Applying the boost
            # here lets the definition flag survive the reranker's
            # signal flattening — the load-bearing application per
            # Wave 1's bench finding.  Same symbol-shape gate as the
            # pre-rerank pass.
            if boost_fires:
                boosted = self._apply_post_rerank_boost(
                    query,
                    reranked,
                    multiplier=self.config.definition_boost_factor_post_rerank,
                )
                return SearchResponse(
                    query_id=query_id,
                    results=boosted,
                    query=query,
                    dataset_id=dataset_id,
                    started_at=started_at,
                )
            return SearchResponse(
                query_id=query_id,
                results=reranked,
                query=query,
                dataset_id=dataset_id,
                started_at=started_at,
            )

        # No rerank → return the fused top-k.  `top` is already truncated
        # to `options.k` in the materialisation step above.
        return SearchResponse(
            query_id=query_id,
            results=top,
            query=query,
            dataset_id=dataset_id,
            started_at=started_at,
        )

    # ── internals ────────────────────────────────────────────────────────

    @staticmethod
    def _normalize_to_dict(hits: Sequence[Hit]) -> dict[int, float]:
        """Per-list min-max normalise hit scores into a chunk_id → score dict."""
        if not hits:
            return {}
        scores = [h.score for h in hits]
        normalised = min_max(scores)
        return {h.chunk_id: s for h, s in zip(hits, normalised, strict=True)}

    @staticmethod
    def _apply_definition_boost(
        query: str,
        scores: dict[int, float],
        hits_by_id: dict[int, Hit],
        *,
        multiplier: float,
    ) -> None:
        """Phase N Wave 2 — multiply fused scores for matching definitions.

        Mutates ``scores`` in place.  For each chunk_id whose source hit
        carries ``metadata.is_definition is True`` AND whose
        ``metadata.name`` (case-folded) is one of the query's identifier
        tokens, multiply the score by ``multiplier``.

        Tokenisation is whole-word: a query token must EQUAL the name
        after case-folding; substring matches do NOT trigger the boost.
        That keeps ``"directory"`` from spuriously lifting
        ``"directory_pruned"`` definitions when the user is asking
        about something else.
        """
        if multiplier == 1.0:
            return  # no-op fast path — useful for the A/B "one knob on" experiments
        tokens = _tokenize_for_boost(query)
        if not tokens:
            return
        for cid, score in list(scores.items()):
            src = hits_by_id.get(cid)
            if src is None:
                continue
            md = src.metadata or {}
            if not md.get("is_definition"):
                continue
            name = md.get("name")
            if not isinstance(name, str):
                continue
            if name.lower() in tokens:
                scores[cid] = score * multiplier

    # ── Phase N Wave 3 — fast-tier helpers ────────────────────────────

    def _require_chunk_ids_filter(self) -> None:
        """Verify the backend's ``search_dense`` accepts ``chunk_ids=``.

        Wave 3's shortcut + only modes require the backend's dense
        (and, for shortcut, lexical) search to accept the candidate
        filter.  We inspect the signature ONCE per search-call so a
        misconfigured backend surfaces a clear error rather than a
        downstream ``TypeError`` from ``search_dense(chunk_ids=...)``.

        Raises:
            ValueError: when the backend's ``search_dense`` doesn't
                accept ``chunk_ids`` — points at the Phase N Wave 3
                backend filter as the missing piece.
        """
        import inspect  # noqa: PLC0415 — cold path

        sig = inspect.signature(self.backend.search_dense)
        if "chunk_ids" not in sig.parameters:
            raise ValueError(
                "Phase N Wave 3 fast-tier requires the backend's "
                "search_dense() to accept chunk_ids=frozenset[int]; "
                f"the configured backend ({type(self.backend).__name__}) "
                "does not.  Upgrade the backend or set "
                "SearchOptions.fast_tier_mode='skip'."
            )

    def _fast_tier_candidate_ids(
        self,
        query: str,
        options: SearchOptions,
        *,
        dataset_id: int | None,
    ) -> frozenset[int]:
        """Run the fast embedder; return the top-``fast_tier_top_n`` chunk_ids.

        Used by the ``"shortcut"`` mode to seed the candidate pool the
        main dense + lexical fan-out then restricts to.  Called inside
        ``search()`` with the fast embedder + id already confirmed
        non-None by the caller.
        """
        assert self.fast_embedder is not None
        assert self.fast_embedder_id is not None
        fast_qvec = self.fast_embedder.encode_query([query])[0]
        # Pull top-N from the fast tier's dense table.  We do NOT pass
        # ``chunk_ids`` here — this IS the candidate-selection step.
        fast_hits = self.backend.search_dense(
            self.fast_embedder_id,
            fast_qvec,
            k=options.fast_tier_top_n,
            dataset_id=dataset_id,
        )
        return frozenset(h.chunk_id for h in fast_hits)

    def _search_fast_only(
        self,
        query: str,
        options: SearchOptions,
        *,
        dataset_id: int | None,
        query_id: str,
        started_at: datetime,
    ) -> SearchResponse:
        """Implement the ``"only"`` mode — fast tier + nothing else.

        No lexical fan-out, no reranker, no Wave 1 alpha bump, no
        Wave 2 boost — the static tier serves the entire query path.
        ``Hit.source`` is set to ``"fused"`` for downstream
        normalisation parity even though no fusion happened (no
        lexical list to fuse against).  The score stays as the dense
        backend's ``1 - cosine_distance`` value.
        """
        assert self.fast_embedder is not None
        assert self.fast_embedder_id is not None
        fast_qvec = self.fast_embedder.encode_query([query])[0]
        # Caller's ``k`` is the final result count; over-fetch isn't
        # needed (no fusion stage to shrink the list).
        hits = self.backend.search_dense(
            self.fast_embedder_id,
            fast_qvec,
            k=options.k,
            dataset_id=dataset_id,
        )
        # Rebuild with source="fused" for downstream parity.
        out: list[Hit] = []
        for h in hits:
            out.append(
                Hit(
                    chunk_id=h.chunk_id,
                    score=h.score,
                    text=h.text,
                    document_id=h.document_id,
                    source_uri=h.source_uri,
                    title=h.title,
                    dataset_id=h.dataset_id,
                    metadata=h.metadata,
                    source="fused",
                )
            )
        return SearchResponse(
            query_id=query_id,
            results=out,
            query=query,
            dataset_id=dataset_id,
            started_at=started_at,
        )

    @staticmethod
    def _apply_post_rerank_boost(
        query: str,
        hits: list[Hit],
        *,
        multiplier: float,
    ) -> list[Hit]:
        """Phase N Wave 2 — boost reranked hits, then re-sort.

        Rebuilds the ``Hit`` list with boosted scores (``Hit`` is frozen
        so we can't mutate the input).  Resorts descending by score with
        chunk_id as a stable tie-break.
        """
        if multiplier == 1.0:
            return hits
        tokens = _tokenize_for_boost(query)
        if not tokens:
            return hits
        out: list[Hit] = []
        for h in hits:
            md = h.metadata or {}
            name = md.get("name")
            if md.get("is_definition") and isinstance(name, str) and name.lower() in tokens:
                out.append(_dc_replace(h, score=h.score * multiplier))
            else:
                out.append(h)
        out.sort(key=lambda h: (-h.score, h.chunk_id))
        return out


# ── Phase N Wave 2 — query tokenisation for the definition boost ──────────


# Non-identifier characters: anything that isn't [A-Za-z0-9_].  Splits a
# query like ``"Foo.bar"`` into {"foo", "bar"}.  Stays ASCII because the
# definition names we boost against are Python / JS / Go / Rust identifiers
# — Unicode identifier extras (CJK / accents) would never match a Python
# function name.
_TOKEN_SPLIT_RE = re.compile(r"[^A-Za-z0-9_]+")


def _tokenize_for_boost(query: str) -> set[str]:
    """Lowercase the query and split on non-identifier chars.

    Private helper — not part of the public retrieval API.  Tests live
    in :mod:`tests.unit.test_retrieval_definition_boost` (see the
    "tokenisation contract" block).
    """
    if not query:
        return set()
    return {tok for tok in _TOKEN_SPLIT_RE.split(query.lower()) if tok}
