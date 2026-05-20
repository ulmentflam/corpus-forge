"""Phase N Wave 1 — `is_symbol_shaped` truth-table pins.

The heuristic gates the adaptive lexical-weight bump in
``HybridRetriever.search``: when the query "looks like" an identifier /
accessor / scope expression we lower the effective alpha so the lexical
(BM25) signal contributes more to the fused score.  Misclassification on
either side has direct quality cost — false positives hurt natural-
language MRR, false negatives leave the identifier MRR plateau in place.

The truth table here is the contract.  Each row was hand-picked from the
Phase N Wave 0 query corpus (or its category neighbours).  Loosening the
heuristic to chase one row must NOT regress any other row — these tests
are the only line of defense between the heuristic and the wave gate's
permissive thresholds.
"""

from __future__ import annotations

import pytest

# ── importability ──────────────────────────────────────────────────────────


def test_module_importable() -> None:
    from corpus_forge.retrieval.query_shape import is_symbol_shaped  # noqa: F401


# ── positive cases — symbol-shaped queries ─────────────────────────────────


class TestSymbolShapedPositives:
    """Queries the heuristic MUST classify as symbol-shaped."""

    @pytest.mark.parametrize(
        "query",
        [
            "HybridRetriever.search",  # dotted accessor (Python)
            "Foo::bar",  # C++ / Rust scope operator
            "foo->bar",  # C / C++ arrow accessor
            "foo/bar/baz",  # path-like
            "_private_helper",  # leading underscore + snake_case
            "myCamelCase",  # camelCase identifier
            "MyClass",  # PascalCase identifier
            "setUp",  # short camelCase (length 5 with uppercase mid)
            "manage_block_sentinels_not_found",  # snake_case (could be a function / var)
            "IgnoreStack.directory_pruned",  # dotted method ref
            "scanner.walker.walk",  # nested dotted path
        ],
    )
    def test_query_classified_as_symbol(self, query: str) -> None:
        from corpus_forge.retrieval.query_shape import is_symbol_shaped

        assert is_symbol_shaped(query) is True, (
            f"expected {query!r} to be classified as symbol-shaped"
        )


# ── negative cases — natural-language / noise queries ──────────────────────


class TestSymbolShapedNegatives:
    """Queries the heuristic MUST classify as NOT symbol-shaped.

    Natural-language wrapping a symbol counts as natural language — the
    wave-5 spec is explicit that whitespace short-circuits the heuristic
    to False to keep the false-positive rate down on conversational queries.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "how does the watch debounce work",  # plain NL question
            "managed block sentinels not found",  # error string with spaces
            "",  # empty
            "   ",  # whitespace only
            "if",  # too short, common keyword
            "setup",  # common word, no camelCase, length 5 but all lower
            "get",  # too short
            "foo",  # too short
            "foo bar.baz",  # whitespace + accessor → still NL
            "what is HybridRetriever",  # one symbol embedded in NL
        ],
    )
    def test_query_classified_as_natural(self, query: str) -> None:
        from corpus_forge.retrieval.query_shape import is_symbol_shaped

        assert is_symbol_shaped(query) is False, (
            f"expected {query!r} to be classified as natural language"
        )


# ── return type contract ───────────────────────────────────────────────────


def test_returns_bool() -> None:
    """The function must return a plain ``bool`` — not a truthy str / None."""
    from corpus_forge.retrieval.query_shape import is_symbol_shaped

    out = is_symbol_shaped("HybridRetriever.search")
    assert isinstance(out, bool), f"expected bool, got {type(out).__name__}"
    out = is_symbol_shaped("how does this work")
    assert isinstance(out, bool), f"expected bool, got {type(out).__name__}"
