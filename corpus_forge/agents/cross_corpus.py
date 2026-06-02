"""Cross-corpus pattern query battery — T3.

Given a :class:`ProjectContext`, fire a language-scoped set of canned
queries against a :class:`Retriever` and keep the top-3 hits per
query. The battery is exposed as the module-level mutable dict
:data:`QUERY_BATTERIES` so callers (and downstream features) can
register new language batteries without subclassing or monkey-patching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from corpus_forge.retrieval.types import Hit, SearchOptions

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.agents.detector import ProjectContext
    from corpus_forge.retrieval.retriever import Retriever


# ─────────────────────────────────────────────────────────────────────────
# Query battery (mutable so callers can extend it at import time)
# ─────────────────────────────────────────────────────────────────────────


QUERY_BATTERIES: dict[str, list[str]] = {
    "python": [
        "pytest fixture",
        "logging.getLogger",
        "dataclass",
        "pytest.raises",
        "Path.read_text",
    ],
    "rust": [
        "Result<T, E> error handling",
        "impl block",
        "#[cfg(test)] module",
    ],
    "typescript": [
        "async function",
        "interface declaration",
        "describe / it test block",
    ],
    "javascript": [
        "async function",
        "describe / it test block",
    ],
    "go": [
        "error return value",
        "table-driven tests",
        "context.Context",
    ],
}


_TOP_K = 3


# ─────────────────────────────────────────────────────────────────────────
# Public surface
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CrossCorpusPatterns:
    """Result envelope from :func:`query_corpus_patterns`.

    ``categories`` maps each fired query string to the top-3 hits
    returned by the retriever. A language with no battery (or a
    battery whose queries returned zero hits) is simply absent from
    the dict.
    """

    categories: dict[str, list[Hit]] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _languages_in_scope(context: ProjectContext) -> list[str]:
    """Return all detected languages that have a registered battery.

    Sorted by descending file count so the dominant language fires
    first when a caller streams results.
    """

    ordered = sorted(context.languages.items(), key=lambda kv: kv[1], reverse=True)
    return [lang for (lang, count) in ordered if count > 0 and lang in QUERY_BATTERIES]


def _top_hits(results: list[Hit], k: int = _TOP_K) -> list[Hit]:
    """Sort by score descending, truncate to ``k``."""

    return sorted(results, key=lambda h: h.score, reverse=True)[:k]


# ─────────────────────────────────────────────────────────────────────────
# Public function
# ─────────────────────────────────────────────────────────────────────────


def query_corpus_patterns(
    context: ProjectContext,
    retriever: Retriever,
    *,
    options: SearchOptions | None = None,
) -> CrossCorpusPatterns:
    """Fire the language-scoped query battery and collect top-3 hits.

    Args:
        context: from :func:`detect_project_context`.
        retriever: anything matching the
            :class:`corpus_forge.retrieval.retriever.Retriever` protocol.
        options: optional :class:`SearchOptions`. Defaults to
            ``k=10`` (so each battery query overfetches enough to make
            the top-3 selection meaningful).
    """

    if options is None:
        options = SearchOptions(k=10)

    categories: dict[str, list[Hit]] = {}
    languages = _languages_in_scope(context)
    if not languages:
        return CrossCorpusPatterns(categories={})

    seen_queries: set[str] = set()
    for lang in languages:
        for query in QUERY_BATTERIES.get(lang, []):
            if query in seen_queries:
                continue
            seen_queries.add(query)
            response = retriever.search(query, options)
            hits = list(response.results) if hasattr(response, "results") else list(response)
            top = _top_hits(hits)
            if top:
                categories[query] = top
    return CrossCorpusPatterns(categories=categories)
