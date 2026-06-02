"""Unit tests for ``corpus_forge.agents.cross_corpus`` — T3.

A fake Retriever feeds canned :class:`Hit` lists per query so we can
assert the battery executes the right queries per language and the
top-3 cutoff is preserved.
"""

from __future__ import annotations

from datetime import UTC, datetime

from corpus_forge.agents.cross_corpus import (
    QUERY_BATTERIES,
    CrossCorpusPatterns,
    query_corpus_patterns,
)
from corpus_forge.agents.detector import ProjectContext
from corpus_forge.retrieval.types import Hit, SearchOptions, SearchResponse

# ─────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────


def _h(chunk_id: int, score: float, source_uri: str, text: str = "snippet") -> Hit:
    return Hit(
        chunk_id=chunk_id,
        score=score,
        text=text,
        document_id=None,
        source_uri=source_uri,
        title=None,
        dataset_id=1,
        metadata={},
        source="fused",
    )


class FakeRetriever:
    """Returns canned hit lists keyed by query string.

    Tracks every call so tests can assert which queries fired.
    """

    def __init__(self, by_query: dict[str, list[Hit]]) -> None:
        self.by_query = by_query
        self.calls: list[tuple[str, SearchOptions]] = []

    def search(self, query: str, options: SearchOptions) -> SearchResponse:
        self.calls.append((query, options))
        hits = self.by_query.get(query, [])
        return SearchResponse(
            query_id="fake",
            results=hits,
            query=query,
            dataset_id=None,
            started_at=datetime.now(UTC),
        )


def _ctx(languages: dict[str, int]) -> ProjectContext:
    return ProjectContext(
        languages=languages,
        package_managers=[],
        test_framework=None,
        build_tool=None,
        existing_agents_md=None,
        existing_claude_md=None,
        existing_readme=None,
        license=None,
        license_header_sample=None,
    )


# ─────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────


def test_query_battery_is_module_level_mutable_dict() -> None:
    """The battery is exposed as a module-level dict; languages map to query lists."""

    assert isinstance(QUERY_BATTERIES, dict)
    assert "python" in QUERY_BATTERIES
    py_q = QUERY_BATTERIES["python"]
    assert isinstance(py_q, list)
    # Minimum coverage per spec
    joined = " | ".join(py_q).lower()
    for required in ("pytest fixture", "logging.getlogger", "dataclass", "pytest.raises"):
        assert required in joined


def test_query_battery_includes_rust() -> None:
    assert "rust" in QUERY_BATTERIES
    assert isinstance(QUERY_BATTERIES["rust"], list) and QUERY_BATTERIES["rust"]


def test_queries_fire_for_each_language_present() -> None:
    """Mixed Py+TS context fires both batteries."""

    queries_seen: dict[str, list[Hit]] = {}
    for q in QUERY_BATTERIES.get("python", []):
        queries_seen[q] = [_h(1, 0.9, f"file://{q}.py")]
    for q in QUERY_BATTERIES.get("typescript", QUERY_BATTERIES.get("javascript", [])):
        queries_seen[q] = [_h(2, 0.8, f"file://{q}.ts")]

    fake = FakeRetriever(queries_seen)
    ctx = _ctx({"python": 5, "typescript": 3})

    result = query_corpus_patterns(ctx, fake)
    assert isinstance(result, CrossCorpusPatterns)
    # Every python query should have fired.
    fired = {q for (q, _) in fake.calls}
    for q in QUERY_BATTERIES["python"]:
        assert q in fired


def test_top_3_cutoff_preserves_chunk_and_uri() -> None:
    """If a query returns >3 hits, only the top 3 (highest score first) are kept."""

    python_q = QUERY_BATTERIES["python"][0]
    canned = [_h(i, score=1.0 - i * 0.05, source_uri=f"file://hit_{i}.py") for i in range(10)]
    fake = FakeRetriever({python_q: canned})
    ctx = _ctx({"python": 1})

    out = query_corpus_patterns(ctx, fake)
    # category keyed by the query
    cat_hits = out.categories.get(python_q, [])
    assert len(cat_hits) == 3
    for hit in cat_hits:
        assert hit.chunk_id is not None
        assert hit.source_uri is not None
    # Ordered by score descending
    scores = [h.score for h in cat_hits]
    assert scores == sorted(scores, reverse=True)


def test_no_matching_language_returns_empty_categories() -> None:
    """A language with no battery yields an empty CrossCorpusPatterns."""

    fake = FakeRetriever({})
    ctx = _ctx({"cobol": 12})
    out = query_corpus_patterns(ctx, fake)
    assert out.categories == {}
    assert fake.calls == []


def test_battery_is_extensible_at_runtime() -> None:
    """The module-level dict is mutable so callers can register new languages."""

    QUERY_BATTERIES.setdefault("python", [])
    sentinel = "sentinel-query-for-tdd"
    if sentinel not in QUERY_BATTERIES["python"]:
        QUERY_BATTERIES["python"].append(sentinel)

    try:
        fake = FakeRetriever({sentinel: [_h(99, 0.5, "file://x.py")]})
        ctx = _ctx({"python": 1})
        out = query_corpus_patterns(ctx, fake)
        assert sentinel in out.categories
        assert out.categories[sentinel][0].chunk_id == 99
    finally:
        if sentinel in QUERY_BATTERIES["python"]:
            QUERY_BATTERIES["python"].remove(sentinel)
