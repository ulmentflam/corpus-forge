"""Phase N Wave 0 — assertions over the committed baseline JSON.

This is the **ungated** companion to ``test_phase_n_bench.py`` (which is
gated by ``CF_PHASE_N_BENCH=1`` and produces the JSON).  It runs in the
default unit suite as a rot-detector: if the committed baseline drifts
shape (missing keys, empty headline numbers, mismatched query counts),
the detector trips immediately.

Asserts the shape of ``tests/perf/out/phase_n_baseline.json``:

- Top-level keys present.
- ``by_corpus`` keyed by ``{"current", "vendored"}`` with both populated.
- Per-corpus and aggregated headline metrics (``mrr_at_10``,
  ``recall_at_5``, ``p50_latency_ms``, ``p95_latency_ms``) are numeric.
- ``n_queries`` matches the input ``semble_queries.jsonl`` count.
- Per-category breakdown blocks exist under each corpus.
- ``corpus_metadata`` documents what was indexed (rationale for
  reproducibility).

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
_BASELINE_PATH = _REPO_ROOT / "tests" / "perf" / "out" / "phase_n_baseline.json"
_QUERIES_PATH = _REPO_ROOT / "tests" / "perf" / "data" / "semble_queries.jsonl"

_HEADLINE_KEYS = {"mrr_at_10", "recall_at_5", "p50_latency_ms", "p95_latency_ms"}
_CORPORA = {"current", "vendored"}
_CATEGORIES = {"identifier", "callsite", "concept", "error", "config"}


@pytest.fixture(scope="module")
def baseline() -> dict[str, Any]:
    if not _BASELINE_PATH.is_file():
        pytest.fail(
            f"baseline JSON missing: {_BASELINE_PATH}.  Run the bench with "
            "`CF_PHASE_N_BENCH=1 uv run pytest tests/perf/test_phase_n_bench.py` "
            "and commit the result.  Phase doc: "
            ".planning/tdd/phase_n_retrieval_quality.md section Wave 0."
        )
    return json.loads(_BASELINE_PATH.read_text())


@pytest.fixture(scope="module")
def queries() -> list[dict[str, Any]]:
    with _QUERIES_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


class TestBaselineTopLevelShape:
    def test_top_level_keys(self, baseline: dict[str, Any]) -> None:
        required = {
            "schema_version",
            "phase",
            "wave",
            "kind",
            "generated_at",
            "repo_root",
            "git_head",
            "n_queries",
            "corpus_metadata",
            "by_corpus",
            "aggregated",
        }
        missing = required - set(baseline.keys())
        assert not missing, f"baseline missing top-level keys: {missing}"

    def test_phase_and_wave_pinned(self, baseline: dict[str, Any]) -> None:
        assert baseline["phase"] == "N", f"phase={baseline['phase']!r}, expected 'N'"
        assert baseline["wave"] == 0, f"wave={baseline['wave']!r}, expected 0"
        assert baseline["kind"] == "phase_n_baseline", (
            f"kind={baseline['kind']!r}, expected 'phase_n_baseline'"
        )

    def test_n_queries_matches_input_file(
        self, baseline: dict[str, Any], queries: list[dict[str, Any]]
    ) -> None:
        assert baseline["n_queries"] == len(queries), (
            f"baseline n_queries={baseline['n_queries']} != queries file count={len(queries)}"
        )


class TestPerCorpusShape:
    def test_both_corpora_present(self, baseline: dict[str, Any]) -> None:
        actual = set(baseline["by_corpus"].keys())
        missing = _CORPORA - actual
        assert not missing, f"by_corpus missing corpora: {missing}; got {actual}"

    def test_per_corpus_headline_keys_present_and_numeric(self, baseline: dict[str, Any]) -> None:
        for corpus in _CORPORA:
            block = baseline["by_corpus"][corpus]
            missing = _HEADLINE_KEYS - set(block.keys())
            assert not missing, f"by_corpus[{corpus}] missing headline keys: {missing}"
            for k in _HEADLINE_KEYS:
                assert isinstance(block[k], (int, float)), (
                    f"by_corpus[{corpus}][{k}]={block[k]!r} not numeric"
                )

    def test_per_corpus_n_queries_sums_to_total(
        self, baseline: dict[str, Any], queries: list[dict[str, Any]]
    ) -> None:
        per_corpus_total = sum(baseline["by_corpus"][c].get("n_queries", 0) for c in _CORPORA)
        # Expected per-corpus count from input file
        corpus_counts = Counter(q.get("corpus", "current") for q in queries)
        # Total queries scored must equal total queries authored
        assert per_corpus_total == len(queries), (
            f"sum(by_corpus[*].n_queries)={per_corpus_total} != "
            f"total queries={len(queries)}; per-corpus split: {dict(corpus_counts)}"
        )

    def test_per_corpus_by_category_block(self, baseline: dict[str, Any]) -> None:
        for corpus in _CORPORA:
            block = baseline["by_corpus"][corpus]
            assert "by_category" in block, f"by_corpus[{corpus}] missing 'by_category' block"
            cats = set(block["by_category"].keys())
            # Each corpus's by_category contains at most the global 5; subset OK
            # (e.g. a corpus with 0 'config' queries omits that key).
            unknown = cats - _CATEGORIES
            assert not unknown, f"by_corpus[{corpus}].by_category has unknown categories: {unknown}"


class TestAggregatedShape:
    def test_aggregated_headline_keys_present_and_numeric(self, baseline: dict[str, Any]) -> None:
        block = baseline["aggregated"]
        missing = _HEADLINE_KEYS - set(block.keys())
        assert not missing, f"aggregated missing headline keys: {missing}"
        for k in _HEADLINE_KEYS:
            assert isinstance(block[k], (int, float)), f"aggregated[{k}]={block[k]!r} not numeric"

    def test_aggregated_by_category_present(self, baseline: dict[str, Any]) -> None:
        block = baseline["aggregated"]
        assert "by_category" in block, "aggregated missing 'by_category' block"
        # Aggregated must cover all 5 global categories (queries are authored
        # to put >= 10 entries in each).
        cats = set(block["by_category"].keys())
        missing = _CATEGORIES - cats
        assert not missing, f"aggregated.by_category missing categories: {missing}; got {cats}"


class TestCorpusMetadata:
    def test_metadata_block_keys(self, baseline: dict[str, Any]) -> None:
        meta = baseline["corpus_metadata"]
        # Both corpora must be documented
        for corpus in _CORPORA:
            assert corpus in meta, f"corpus_metadata missing entry for {corpus!r}"
            entry = meta[corpus]
            # Each entry documents the source for reproducibility
            for key in ("source", "n_files", "n_bytes"):
                assert key in entry, f"corpus_metadata[{corpus}] missing key {key!r}"

    def test_vendored_corpus_metadata_has_commit_pin(self, baseline: dict[str, Any]) -> None:
        """Vendored corpus must record the upstream commit hash it was
        snapshotted at, so re-running the bench is deterministic."""
        vendored = baseline["corpus_metadata"]["vendored"]
        assert "upstream_commit" in vendored, (
            "vendored corpus must record upstream_commit for reproducibility"
        )
        commit = vendored["upstream_commit"]
        assert isinstance(commit, str) and len(commit) >= 7, (
            f"upstream_commit={commit!r} not a sensible git sha"
        )
