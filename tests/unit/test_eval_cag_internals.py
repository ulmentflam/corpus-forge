"""Unit tests for ``corpus_forge.eval.cag``.

Covers the prompt builder, _NullRetriever stub, and ``run_cag_eval`` with a
mock judge — cache files seeded under ``tmp_path`` so the selector's cache
hit/miss matrix is exercised offline.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from corpus_forge.eval.cag import (
    _build_cag_judge_prompt,
    _NullRetriever,
    run_cag_eval,
)

# ─────────────────────────────────────────────────────────────────────────────
# _NullRetriever
# ─────────────────────────────────────────────────────────────────────────────


def test_null_retriever_returns_empty_list() -> None:
    r = _NullRetriever()
    assert r.search("anything") == []
    # Extra args / kwargs are accepted and ignored.
    assert r.search("query", "extra", k=5, foo="bar") == []


# ─────────────────────────────────────────────────────────────────────────────
# _build_cag_judge_prompt
# ─────────────────────────────────────────────────────────────────────────────


def test_build_cag_prompt_includes_route_query_answer_contexts() -> None:
    prompt = _build_cag_judge_prompt(
        "what is q?",
        "the answer",
        ["ctx-a", "ctx-b"],
        route="cache",
    )
    assert "route='cache'" in prompt
    assert "what is q?" in prompt
    assert "the answer" in prompt
    assert "ctx-a" in prompt
    assert "ctx-b" in prompt
    assert "[1]" in prompt
    assert "[2]" in prompt


# ─────────────────────────────────────────────────────────────────────────────
# run_cag_eval — all-miss path (root not provided → all RAG)
# ─────────────────────────────────────────────────────────────────────────────


def test_run_cag_eval_no_root_all_misses(tmp_path: Path) -> None:
    queries = [
        {"query": "q1", "answer": "a1", "contexts": ["c1"]},
        {"query": "q2", "answer": "a2", "contexts": ["c2"]},
    ]
    result = run_cag_eval(
        "test-ds",
        queries,
        judge_endpoint="mock",
        report_dir=tmp_path,
    )
    assert result["cache_hit_count"] == 0
    assert result["rag_count"] == 2
    assert result["cache_quality_score"] is None
    assert result["rag_quality_score"] is not None
    assert 0.0 <= result["rag_quality_score"] <= 1.0
    # Delta requires both quality scores; without cache hits it's None.
    assert result["cache_vs_rag_delta"] is None
    assert result["n_queries"] == 2
    assert result["dataset"] == "test-ds"
    # Reports written.
    assert (tmp_path / "eval_cag.json").is_file()
    assert (tmp_path / "eval_cag.md").is_file()


# ─────────────────────────────────────────────────────────────────────────────
# run_cag_eval — cache hit path
# ─────────────────────────────────────────────────────────────────────────────


def _seed_cache_file(root: Path, dataset: str, query: str, payload: dict) -> None:
    """Mirror corpus_forge.cag.selector._derive_key: sha256 of sorted JSON."""
    key_input = json.dumps(
        {"dataset": dataset, "template": "default", "query": query},
        sort_keys=True,
    ).encode()
    key = hashlib.sha256(key_input).hexdigest()
    target_dir = root / dataset
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / f"{key}.json").write_text(json.dumps(payload))


def test_run_cag_eval_seeded_cache_hits_returns_quality_scores(tmp_path: Path) -> None:
    cache_root = tmp_path / "cag-cache"
    _seed_cache_file(
        cache_root,
        "demo",
        "what is the capital of France?",
        {
            "cached_answer": "Paris.",
            "contexts": ["France has Paris as its capital."],
        },
    )
    queries = [
        {
            "query": "what is the capital of France?",
            "answer": "Paris",
            "contexts": ["France has Paris as its capital."],
        },
        {
            "query": "another query without a cache file",
            "answer": "x",
            "contexts": ["y"],
        },
    ]
    result = run_cag_eval(
        "demo",
        queries,
        judge_endpoint="mock",
        root=cache_root,
        report_dir=tmp_path / "reports",
    )
    assert result["cache_hit_count"] == 1
    assert result["rag_count"] == 1
    assert result["cache_quality_score"] is not None
    assert result["rag_quality_score"] is not None
    # Delta exists when both sides have scores.
    assert result["cache_vs_rag_delta"] is not None
    assert isinstance(result["cache_vs_rag_delta"], float)


# ─────────────────────────────────────────────────────────────────────────────
# run_cag_eval — empty queries
# ─────────────────────────────────────────────────────────────────────────────


def test_run_cag_eval_empty_queries(tmp_path: Path) -> None:
    result = run_cag_eval(
        "ds",
        [],
        judge_endpoint="mock",
        report_dir=tmp_path,
    )
    assert result["cache_hit_count"] == 0
    assert result["rag_count"] == 0
    assert result["cache_quality_score"] is None
    assert result["rag_quality_score"] is None
    assert result["cache_vs_rag_delta"] is None
    assert result["n_queries"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# run_cag_eval — default report dir under ~/.cache
# ─────────────────────────────────────────────────────────────────────────────


def test_run_cag_eval_default_report_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    run_cag_eval(
        "ds",
        [{"query": "q", "answer": "a", "contexts": []}],
        judge_endpoint="mock",
    )
    reports_root = home / ".cache" / "corpus-forge" / "reports"
    assert reports_root.is_dir()
