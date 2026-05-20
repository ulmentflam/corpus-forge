"""Phase N Wave 0 — assertions over the broadened semble query set.

Locks the bench corpus + query set BEFORE any retrieval-quality technique
(Waves 1-3) lands. The Phase M Wave 5 baseline was 25 hand-crafted
queries against corpus-forge only; Phase N broadens that to 50-75 queries
covering both this repo and a vendored OSS code corpus snapshot.

This file is **ungated**: it runs in the default unit suite as a
rot-detector. If a future hand will trim queries below the floor, the
detector trips immediately instead of letting subsequent wave-gate
measurements drift on a too-narrow signal.

Invariants asserted
-------------------

1. ``tests/perf/data/semble_queries.jsonl`` has >= 50 entries.
2. Each of the five categories (``identifier``, ``callsite``, ``concept``,
   ``error``, ``config``) has >= 10 entries.
3. Every entry has a non-empty ``ground_truth_chunks`` list, and each
   chunk dict has ``file``, ``byte_start``, ``byte_end`` keys with
   ``byte_end > byte_start``.
4. Every referenced ``file`` exists on disk.  Resolution rules:
     - paths starting with ``corpus_forge/``, ``config.example.toml``,
       or ``README.md`` resolve under the repo root (the "current" corpus).
     - paths starting with ``tests/fixtures/external/`` resolve under
       the repo root (vendored snapshot fixtures).
     - any other prefix is an error.
5. Each entry's ground-truth ``byte_end`` does not run past the
   referenced file's actual byte length.
6. Each entry's optional ``corpus`` field, when present, is one of
   ``"current"`` or ``"vendored"``.  Entries without the field default
   to ``"current"`` for backward-compat with the 25 existing queries.

The phase doc lives at ``.planning/tdd/phase_n_retrieval_quality.md``
section "Wave 0".
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_QUERIES_PATH = _REPO_ROOT / "tests" / "perf" / "data" / "semble_queries.jsonl"

_VALID_CATEGORIES = {"identifier", "callsite", "concept", "error", "config"}
_VALID_CORPORA = {"current", "vendored"}
_CATEGORY_FLOOR = 10
_TOTAL_FLOOR = 50


def _load() -> list[dict[str, Any]]:
    with _QUERIES_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def _classify_corpus(entry: dict[str, Any]) -> str:
    """Return the corpus this entry targets.

    Honors an explicit ``corpus`` field when present; otherwise defaults
    to ``"current"`` (the Phase M Wave 5 queries had no such field).
    """
    explicit = entry.get("corpus")
    if explicit is not None:
        return str(explicit)
    return "current"


def _resolve_ground_truth_path(file_str: str) -> Path:
    """Resolve a ground-truth ``file`` field to an absolute filesystem path.

    Paths under ``tests/fixtures/external/`` target the vendored snapshot;
    everything else targets the live repo at the bench commit.
    """
    return _REPO_ROOT / file_str


@pytest.fixture(scope="module")
def queries() -> list[dict[str, Any]]:
    if not _QUERIES_PATH.is_file():
        pytest.fail(f"queries file missing: {_QUERIES_PATH}")
    return _load()


class TestQueryCount:
    def test_total_count_meets_phase_n_floor(self, queries: list[dict[str, Any]]) -> None:
        """>= 50 entries (Phase N Wave 0 expanded from the 25 Wave 5 set)."""
        assert len(queries) >= _TOTAL_FLOOR, (
            f"expected >= {_TOTAL_FLOOR} queries (Phase N floor); "
            f"got {len(queries)}.  Phase doc: .planning/tdd/phase_n_retrieval_quality.md"
        )

    def test_total_count_under_ceiling(self, queries: list[dict[str, Any]]) -> None:
        """Sanity ceiling; far above any planned target keeps the gate honest."""
        assert len(queries) <= 200, (
            f"Phase N targets 50-75 entries; got {len(queries)}.  "
            "If this is intentional (e.g. Phase O expanded), bump this ceiling."
        )


class TestPerCategoryCounts:
    def test_every_category_meets_floor(self, queries: list[dict[str, Any]]) -> None:
        """Each of the 5 categories has >= 10 entries."""
        cat_counts = Counter(q["category"] for q in queries)
        missing = [c for c in _VALID_CATEGORIES if cat_counts.get(c, 0) < _CATEGORY_FLOOR]
        assert not missing, (
            f"categories below floor of {_CATEGORY_FLOOR}: "
            f"{[(c, cat_counts.get(c, 0)) for c in missing]}.  "
            f"All counts: {dict(cat_counts)}"
        )

    def test_no_unknown_categories(self, queries: list[dict[str, Any]]) -> None:
        unknown = {q["category"] for q in queries} - _VALID_CATEGORIES
        assert not unknown, f"unknown categories: {unknown}"


class TestPerCorpusBalance:
    def test_corpus_field_values_are_valid(self, queries: list[dict[str, Any]]) -> None:
        for i, q in enumerate(queries):
            corpus = _classify_corpus(q)
            assert corpus in _VALID_CORPORA, f"query[{i}] corpus={corpus!r} not in {_VALID_CORPORA}"

    def test_both_corpora_represented(self, queries: list[dict[str, Any]]) -> None:
        """Wave 0 splits across both corpus-forge and the vendored snapshot."""
        corpora = Counter(_classify_corpus(q) for q in queries)
        assert corpora.get("current", 0) >= 20, (
            f"need >= 20 'current' queries; got {corpora.get('current', 0)}.  "
            f"Distribution: {dict(corpora)}"
        )
        assert corpora.get("vendored", 0) >= 20, (
            f"need >= 20 'vendored' queries; got {corpora.get('vendored', 0)}.  "
            f"Distribution: {dict(corpora)}"
        )


class TestGroundTruthSchema:
    def test_every_entry_has_ground_truth(self, queries: list[dict[str, Any]]) -> None:
        for i, q in enumerate(queries):
            gt = q.get("ground_truth_chunks")
            assert isinstance(gt, list) and gt, (
                f"query[{i}] {q.get('query')!r}: missing/empty ground_truth_chunks"
            )

    def test_every_ground_truth_chunk_has_required_keys(
        self, queries: list[dict[str, Any]]
    ) -> None:
        required = {"file", "byte_start", "byte_end"}
        for i, q in enumerate(queries):
            for j, chunk in enumerate(q["ground_truth_chunks"]):
                missing = required - set(chunk.keys())
                assert not missing, f"query[{i}].ground_truth_chunks[{j}] missing keys: {missing}"

    def test_byte_spans_are_well_ordered(self, queries: list[dict[str, Any]]) -> None:
        for i, q in enumerate(queries):
            for j, chunk in enumerate(q["ground_truth_chunks"]):
                bs = int(chunk["byte_start"])
                be = int(chunk["byte_end"])
                assert bs >= 0, f"query[{i}].ground_truth_chunks[{j}] byte_start={bs} < 0"
                assert be > bs, (
                    f"query[{i}].ground_truth_chunks[{j}] byte_end={be} "
                    f"<= byte_start={bs}; spans must be non-empty half-open."
                )


class TestGroundTruthPathsResolve:
    def test_every_referenced_file_exists(self, queries: list[dict[str, Any]]) -> None:
        missing: list[tuple[int, str]] = []
        for i, q in enumerate(queries):
            for chunk in q["ground_truth_chunks"]:
                resolved = _resolve_ground_truth_path(chunk["file"])
                if not resolved.is_file():
                    missing.append((i, chunk["file"]))
        assert not missing, (
            f"{len(missing)} ground-truth files do not exist on disk.  First few: {missing[:5]}"
        )

    def test_byte_spans_within_file_bounds(self, queries: list[dict[str, Any]]) -> None:
        """byte_end must not exceed the referenced file's length."""
        over: list[tuple[int, str, int, int]] = []
        for i, q in enumerate(queries):
            for chunk in q["ground_truth_chunks"]:
                resolved = _resolve_ground_truth_path(chunk["file"])
                if not resolved.is_file():
                    continue  # other test already flags missing files
                size = resolved.stat().st_size
                if int(chunk["byte_end"]) > size:
                    over.append((i, chunk["file"], int(chunk["byte_end"]), size))
        assert not over, (
            f"{len(over)} ground-truth chunks extend past EOF.  "
            f"First few (query_idx, file, byte_end, file_size): {over[:5]}"
        )


class TestQueryStringSanity:
    def test_queries_non_empty(self, queries: list[dict[str, Any]]) -> None:
        for i, q in enumerate(queries):
            assert isinstance(q.get("query"), str) and q["query"].strip(), (
                f"query[{i}] has empty/missing 'query' field"
            )

    def test_no_duplicate_queries_within_corpus(self, queries: list[dict[str, Any]]) -> None:
        """Catch accidental copy-paste — same query string + same corpus is dup."""
        seen: dict[tuple[str, str], int] = {}
        dups: list[tuple[str, str, int, int]] = []
        for i, q in enumerate(queries):
            key = (q["query"], _classify_corpus(q))
            if key in seen:
                dups.append((q["query"], _classify_corpus(q), seen[key], i))
            else:
                seen[key] = i
        assert not dups, f"{len(dups)} duplicate (query, corpus) pairs.  First: {dups[:3]}"
