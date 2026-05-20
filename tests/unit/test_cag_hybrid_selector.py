"""Phase P Wave 3 (P3-T2) — Unit tests for corpus_forge.cag.selector.

Pins the public contract of:

- ``select(query, dataset, *, retriever, root=None) -> tuple[str, dict | SearchResponse]``
- ``HybridCagSelector(retriever, root=None)`` with ``.select(query, dataset)``

The selector decides whether a query should be answered from a precomputed
cache file (a ``("cache", payload_dict)`` tuple) or by hitting the live
retriever (a ``("rag", SearchResponse)`` tuple).

Cache-key matching is **template-based**, not raw string:
- The selector resolves the query through a configured template, derives
  a SHA-256 cache key, and looks up ``<root>/<dataset>/<key>.json``.
- Raw query string variations that resolve to the same template+dataset
  key produce a cache HIT for the stored key.

Cache-file format expected on disk:
- Standard JSON produced by ``build_cache`` (P3-T1 companion task).
- The payload is the parsed JSON dict verbatim.
- No ``_cf_route`` key is injected into the payload; routing is communicated
  entirely by the tuple's first element (``"cache"`` or ``"rag"``).

RED state: ``from corpus_forge.cag.selector import HybridCagSelector, select``
fails with ``ModuleNotFoundError: No module named 'corpus_forge.cag'``
because the package does not yet exist.

Key design decisions captured in tests
---------------------------------------
- Tuple first element is the authoritative route indicator.
  ``"cache"`` = served from disk; ``"rag"`` = live retriever was called.
- ``retriever.search`` is NEVER called on a cache hit.
- ``retriever.search`` IS called on a cache miss.
- Cache root override: ``select(..., root=tmp_path)`` respects custom root.
- ``HybridCagSelector`` is the stateful wrapper; ``.select`` delegates to
  the same logic as the module-level ``select``.
- Empty cache directory → every query routes ``"rag"``.
- Hit/miss matrix: 3 cache files present, query matching exactly one →
  exactly one cache hit; the other two remain misses.
- Property (hypothesis): same query + same cache files → deterministic
  route and payload on repeated calls.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_search_response(n_hits: int = 2) -> Any:
    """Return a canned SearchResponse with ``n_hits`` fake Hit objects."""
    from datetime import UTC, datetime

    from corpus_forge.retrieval.types import Hit, SearchResponse

    hits = [
        Hit(
            chunk_id=i,
            score=0.9 - i * 0.1,
            text=f"text-{i}",
            document_id=None,
            source_uri=f"u://{i}",
            title=f"title-{i}",
            dataset_id=1,
            metadata={},
            source="fused",
        )
        for i in range(n_hits)
    ]
    return SearchResponse(
        query_id=f"qid-{n_hits:04d}",
        results=hits,
        query="test query",
        dataset_id=1,
        started_at=datetime.now(UTC),
    )


def _make_retriever(response: Any | None = None) -> MagicMock:
    """Build a mock retriever that returns ``response`` from ``.search()``."""
    r = MagicMock()
    r.search.return_value = response if response is not None else _make_search_response()
    return r


def _cache_key(query: str, dataset: str, template: str = "default") -> str:
    """Mirror the cache-key derivation described in the plan:
    sha256((dataset_id, template_name, query)).

    The selector uses template-based resolution. For the unit tests we
    stub the template as the identity (no transformation) and use
    template name ``"default"`` so the key is deterministic.
    """
    raw = json.dumps({"dataset": dataset, "template": template, "query": query}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _write_cache_file(root: Path, dataset: str, query: str, payload: dict[str, Any]) -> Path:
    """Write a cache JSON file at the expected path and return it."""
    dataset_dir = root / dataset
    dataset_dir.mkdir(parents=True, exist_ok=True)
    key = _cache_key(query, dataset)
    path = dataset_dir / f"{key}.json"
    path.write_text(json.dumps(payload))
    return path


def _payload(label: str = "p") -> dict[str, Any]:
    return {"label": label, "content": f"cached answer for {label}"}


# ---------------------------------------------------------------------------
# Import smoke
# ---------------------------------------------------------------------------


def test_import_select():
    """``select`` is importable from ``corpus_forge.cag.selector``."""
    from corpus_forge.cag.selector import select  # noqa: F401


def test_import_hybrid_cag_selector():
    """``HybridCagSelector`` is importable from ``corpus_forge.cag.selector``."""
    from corpus_forge.cag.selector import HybridCagSelector  # noqa: F401


def test_cag_package_importable():
    """``corpus_forge.cag`` package exists and is importable."""
    import corpus_forge.cag  # noqa: F401


# ---------------------------------------------------------------------------
# Module-level ``select`` — cache HIT
# ---------------------------------------------------------------------------


def test_select_cache_hit_returns_cache_tuple(tmp_path):
    """Cache hit: first element of tuple is ``"cache"``."""
    from corpus_forge.cag.selector import select

    query = "what is the capital of France?"
    dataset = "geo"
    payload = _payload("geo-1")
    _write_cache_file(tmp_path, dataset, query, payload)

    retriever = _make_retriever()
    route, _result = select(query, dataset, retriever=retriever, root=tmp_path)

    assert route == "cache"


def test_select_cache_hit_returns_parsed_json(tmp_path):
    """Cache hit: second element is the parsed JSON dict from disk."""
    from corpus_forge.cag.selector import select

    query = "what is the capital of France?"
    dataset = "geo"
    payload = _payload("geo-1")
    _write_cache_file(tmp_path, dataset, query, payload)

    retriever = _make_retriever()
    route, result = select(query, dataset, retriever=retriever, root=tmp_path)

    assert route == "cache"
    assert result == payload


def test_select_cache_hit_does_not_call_retriever(tmp_path):
    """Cache hit: ``retriever.search`` is never invoked."""
    from corpus_forge.cag.selector import select

    query = "cache-only query"
    dataset = "docs"
    _write_cache_file(tmp_path, dataset, query, _payload())

    retriever = _make_retriever()
    select(query, dataset, retriever=retriever, root=tmp_path)

    retriever.search.assert_not_called()


# ---------------------------------------------------------------------------
# Module-level ``select`` — cache MISS
# ---------------------------------------------------------------------------


def test_select_cache_miss_returns_rag_tuple(tmp_path):
    """Cache miss: first element of tuple is ``"rag"``."""
    from corpus_forge.cag.selector import select

    retriever = _make_retriever()
    route, _ = select("uncached query", "empty-ds", retriever=retriever, root=tmp_path)

    assert route == "rag"


def test_select_cache_miss_returns_search_response(tmp_path):
    """Cache miss: second element is the SearchResponse from the retriever."""
    from corpus_forge.cag.selector import select

    from corpus_forge.retrieval.types import SearchResponse

    expected = _make_search_response(3)
    retriever = _make_retriever(expected)
    _, result = select("uncached query", "empty-ds", retriever=retriever, root=tmp_path)

    assert isinstance(result, SearchResponse)
    assert result is expected


def test_select_cache_miss_calls_retriever(tmp_path):
    """Cache miss: ``retriever.search`` IS called exactly once."""
    from corpus_forge.cag.selector import select

    retriever = _make_retriever()
    select("miss query", "nodataset", retriever=retriever, root=tmp_path)

    retriever.search.assert_called_once()


def test_select_empty_cache_directory_always_rag(tmp_path):
    """Empty cache directory (no JSON files) always routes to ``"rag"``."""
    from corpus_forge.cag.selector import select

    dataset_dir = tmp_path / "empty-ds"
    dataset_dir.mkdir()

    retriever = _make_retriever()
    for q in ("first query", "second query", "third query"):
        route, _ = select(q, "empty-ds", retriever=retriever, root=tmp_path)
        assert route == "rag", f"Expected rag for {q!r}, got {route!r}"

    assert retriever.search.call_count == 3


# ---------------------------------------------------------------------------
# Hit / miss matrix: 3 cache files, one matching query
# ---------------------------------------------------------------------------


def test_hit_miss_matrix_exactly_one_hit(tmp_path):
    """With 3 cache files, a query matching exactly one produces one hit."""
    from corpus_forge.cag.selector import select

    dataset = "matrix-ds"
    queries = ["alpha query", "beta query", "gamma query"]
    payloads = {q: _payload(q[:5]) for q in queries}

    for q, p in payloads.items():
        _write_cache_file(tmp_path, dataset, q, p)

    retriever = _make_retriever()

    # Query that matches "beta query" cache file
    route, result = select("beta query", dataset, retriever=retriever, root=tmp_path)

    assert route == "cache"
    assert result == payloads["beta query"]
    # retriever must not have been called for this hit
    retriever.search.assert_not_called()


def test_hit_miss_matrix_non_matching_queries_miss(tmp_path):
    """Non-matching queries route to rag even when other cache files exist."""
    from corpus_forge.cag.selector import select

    dataset = "matrix-ds"
    queries = ["alpha query", "beta query", "gamma query"]
    for q in queries:
        _write_cache_file(tmp_path, dataset, q, _payload(q[:5]))

    retriever = _make_retriever()

    # "delta query" is NOT in the cache
    route, _ = select("delta query", dataset, retriever=retriever, root=tmp_path)

    assert route == "rag"
    retriever.search.assert_called_once()


def test_hit_miss_matrix_all_three_hit_independently(tmp_path):
    """Each of the 3 cached queries independently produces a cache hit."""
    from corpus_forge.cag.selector import select

    dataset = "matrix-ds"
    queries = ["alpha query", "beta query", "gamma query"]
    payloads = {q: _payload(q[:5]) for q in queries}
    for q, p in payloads.items():
        _write_cache_file(tmp_path, dataset, q, p)

    for q in queries:
        retriever = _make_retriever()
        route, result = select(q, dataset, retriever=retriever, root=tmp_path)
        assert route == "cache", f"Expected cache hit for {q!r}"
        assert result == payloads[q]
        retriever.search.assert_not_called()


# ---------------------------------------------------------------------------
# root override respected
# ---------------------------------------------------------------------------


def test_select_respects_root_override(tmp_path):
    """``root`` kwarg is honoured; default root (no kwarg) is a separate space."""
    from corpus_forge.cag.selector import select

    custom_root = tmp_path / "custom_cache"
    custom_root.mkdir()

    query = "root override query"
    dataset = "ds"
    _write_cache_file(custom_root, dataset, query, _payload("custom"))

    retriever = _make_retriever()

    # With the custom root, the file is found → cache hit
    route, result = select(query, dataset, retriever=retriever, root=custom_root)
    assert route == "cache"
    assert result == _payload("custom")

    # With a fresh tmp dir (no files), the same query misses → rag
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    retriever2 = _make_retriever()
    route2, _ = select(query, dataset, retriever=retriever2, root=empty_root)
    assert route2 == "rag"


# ---------------------------------------------------------------------------
# HybridCagSelector stateful class
# ---------------------------------------------------------------------------


def test_hybrid_selector_cache_hit(tmp_path):
    """``HybridCagSelector.select`` returns ``("cache", payload)`` on hit."""
    from corpus_forge.cag.selector import HybridCagSelector

    query = "stateful hit query"
    dataset = "sd"
    payload = _payload("stateful")
    _write_cache_file(tmp_path, dataset, query, payload)

    retriever = _make_retriever()
    selector = HybridCagSelector(retriever, root=tmp_path)
    route, result = selector.select(query, dataset)

    assert route == "cache"
    assert result == payload


def test_hybrid_selector_cache_miss(tmp_path):
    """``HybridCagSelector.select`` returns ``("rag", SearchResponse)`` on miss."""
    from corpus_forge.cag.selector import HybridCagSelector

    from corpus_forge.retrieval.types import SearchResponse

    retriever = _make_retriever()
    selector = HybridCagSelector(retriever, root=tmp_path)
    route, result = selector.select("miss query", "ds-miss")

    assert route == "rag"
    assert isinstance(result, SearchResponse)


def test_hybrid_selector_shares_retriever_across_calls(tmp_path):
    """A single ``HybridCagSelector`` instance reuses the same retriever."""
    from corpus_forge.cag.selector import HybridCagSelector

    retriever = _make_retriever()
    selector = HybridCagSelector(retriever, root=tmp_path)

    # Two separate misses — both go through the same retriever
    selector.select("query-one", "ds-a")
    selector.select("query-two", "ds-b")

    assert retriever.search.call_count == 2


def test_hybrid_selector_hit_does_not_call_retriever(tmp_path):
    """``HybridCagSelector`` does not invoke retriever on cache hit."""
    from corpus_forge.cag.selector import HybridCagSelector

    query = "dont-call-me"
    dataset = "ds"
    _write_cache_file(tmp_path, dataset, query, _payload())

    retriever = _make_retriever()
    selector = HybridCagSelector(retriever, root=tmp_path)
    selector.select(query, dataset)

    retriever.search.assert_not_called()


def test_hybrid_selector_root_defaults_to_none():
    """``HybridCagSelector`` accepts ``root=None`` (uses default cache root)."""
    from corpus_forge.cag.selector import HybridCagSelector

    retriever = _make_retriever()
    # Should not raise at construction time
    selector = HybridCagSelector(retriever, root=None)
    assert selector is not None


# ---------------------------------------------------------------------------
# Route is communicated solely by tuple first element (no _cf_route injection)
# ---------------------------------------------------------------------------


def test_cache_payload_has_no_injected_cf_route_key(tmp_path):
    """The payload dict on cache hit is the verbatim disk JSON, no ``_cf_route`` added."""
    from corpus_forge.cag.selector import select

    query = "verbatim payload test"
    dataset = "ds"
    original_payload = {"answer": "Paris", "confidence": 0.99}
    _write_cache_file(tmp_path, dataset, query, original_payload)

    retriever = _make_retriever()
    route, result = select(query, dataset, retriever=retriever, root=tmp_path)

    assert route == "cache"
    assert result == original_payload
    assert "_cf_route" not in result


# ---------------------------------------------------------------------------
# Hypothesis property: deterministic routing
# ---------------------------------------------------------------------------


@given(
    query=st.text(min_size=1, max_size=120, alphabet=st.characters(blacklist_categories=("Cs",))),
    dataset=st.from_regex(r"[a-z][a-z0-9_-]{0,19}", fullmatch=True),
)
@settings(max_examples=30, deadline=5000)
def test_property_deterministic_route_on_hit(query: str, dataset: str, tmp_path: Path):
    """If a cache file exists for (query, dataset), repeated calls always return cache."""
    from corpus_forge.cag.selector import select

    payload = {"q": query[:20], "ds": dataset}
    _write_cache_file(tmp_path, dataset, query, payload)

    retriever1 = _make_retriever()
    retriever2 = _make_retriever()

    route1, result1 = select(query, dataset, retriever=retriever1, root=tmp_path)
    route2, result2 = select(query, dataset, retriever=retriever2, root=tmp_path)

    assert route1 == "cache"
    assert route2 == "cache"
    assert result1 == result2
    retriever1.search.assert_not_called()
    retriever2.search.assert_not_called()


@given(
    query=st.text(min_size=1, max_size=120, alphabet=st.characters(blacklist_categories=("Cs",))),
    dataset=st.from_regex(r"[a-z][a-z0-9_-]{0,19}", fullmatch=True),
)
@settings(max_examples=30, deadline=5000)
def test_property_deterministic_route_on_miss(query: str, dataset: str, tmp_path: Path):
    """Without a cache file, repeated calls always return rag."""
    from corpus_forge.cag.selector import select

    route1, _ = select(query, dataset, retriever=_make_retriever(), root=tmp_path)
    route2, _ = select(query, dataset, retriever=_make_retriever(), root=tmp_path)

    assert route1 == "rag"
    assert route2 == "rag"
