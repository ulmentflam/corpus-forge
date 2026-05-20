"""Unit tests for ``corpus_forge.eval.rag``.

Covers the prompt builder, nDCG/MRR helpers, and the full ``run_rag_eval``
flow using the mock judge so no network or backend is touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpus_forge.eval.rag import (
    _build_judge_prompt,
    _mrr,
    _ndcg_at_k,
    run_rag_eval,
)

_JUDGE_DIMS = {"faithfulness", "answer_relevance", "context_precision", "context_recall"}
_METRIC_KEYS = {"nDCG@1", "nDCG@5", "nDCG@10", "MRR"} | _JUDGE_DIMS


# ─────────────────────────────────────────────────────────────────────────────
# _build_judge_prompt
# ─────────────────────────────────────────────────────────────────────────────


def test_build_judge_prompt_contains_all_inputs() -> None:
    prompt = _build_judge_prompt(
        "what is X?",
        "X is a thing",
        ["context A", "context B"],
    )
    assert "what is X?" in prompt
    assert "X is a thing" in prompt
    assert "context A" in prompt
    assert "context B" in prompt
    # Numbered context block markers.
    assert "[1]" in prompt
    assert "[2]" in prompt
    # JSON instruction.
    assert "faithfulness" in prompt


def test_build_judge_prompt_empty_contexts_block_is_empty() -> None:
    prompt = _build_judge_prompt("q", "a", [])
    assert "Query: q" in prompt
    assert "Answer: a" in prompt
    # Empty contexts block should not crash; just no [N] markers.
    assert "[1]" not in prompt


# ─────────────────────────────────────────────────────────────────────────────
# _ndcg_at_k
# ─────────────────────────────────────────────────────────────────────────────


def test_ndcg_perfect_ranking_is_one() -> None:
    assert _ndcg_at_k([1, 2, 3], [1, 2, 3], 3) == pytest.approx(1.0)


def test_ndcg_zero_k_returns_zero() -> None:
    assert _ndcg_at_k([1, 2, 3], [1, 2, 3], 0) == 0.0


def test_ndcg_empty_ranked_returns_zero() -> None:
    assert _ndcg_at_k([], [1, 2, 3], 5) == 0.0


def test_ndcg_empty_relevant_returns_zero() -> None:
    assert _ndcg_at_k([1, 2, 3], [], 5) == 0.0


def test_ndcg_no_overlap_returns_zero() -> None:
    assert _ndcg_at_k([1, 2, 3], [99, 100], 3) == 0.0


def test_ndcg_partial_overlap_in_unit_interval() -> None:
    # Relevant items at ranks 1 and 3 — partial credit, < 1.0 but > 0.
    result = _ndcg_at_k([1, 99, 2, 100], [1, 2], 4)
    assert 0.0 < result < 1.0


# ─────────────────────────────────────────────────────────────────────────────
# _mrr
# ─────────────────────────────────────────────────────────────────────────────


def test_mrr_first_item_relevant_is_one() -> None:
    assert _mrr([1, 2, 3], [1], 10) == 1.0


def test_mrr_third_item_relevant_is_one_third() -> None:
    assert _mrr([99, 100, 1], [1], 10) == pytest.approx(1.0 / 3.0)


def test_mrr_no_overlap_returns_zero() -> None:
    assert _mrr([1, 2, 3], [99], 10) == 0.0


def test_mrr_empty_inputs_return_zero() -> None:
    assert _mrr([], [1], 10) == 0.0
    assert _mrr([1], [], 10) == 0.0


def test_mrr_respects_k_cutoff() -> None:
    # Relevant at position 5 but k=3 — must return 0.
    assert _mrr([99, 99, 99, 99, 1], [1], 3) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# run_rag_eval — full flow with mock judge
# ─────────────────────────────────────────────────────────────────────────────


def test_run_rag_eval_writes_reports_and_returns_metrics(tmp_path: Path) -> None:
    queries = [
        {
            "query": "what is the capital of France?",
            "answer": "Paris",
            "relevant_chunk_ids": [1],
            "contexts": ["Paris is the capital of France."],
            "ranked_chunk_ids": [1, 2, 3],
        },
        {
            "query": "what is the speed of light?",
            "answer": "299,792,458 m/s",
            "relevant_chunk_ids": [4],
            "contexts": ["c = 299,792,458 m/s in vacuum."],
            "ranked_chunk_ids": [4, 5],
        },
    ]
    result = run_rag_eval(
        "test-dataset",
        queries,
        judge_endpoint="mock",
        k=5,
        report_dir=tmp_path,
    )

    # Metric keys present.
    for key in _METRIC_KEYS:
        assert key in result
    assert result["n_queries"] == 2
    assert result["dataset"] == "test-dataset"
    assert result["judge_endpoint"] == "mock"

    # Files written.
    md = tmp_path / "eval_rag.md"
    js = tmp_path / "eval_rag.json"
    prompts = tmp_path / "judge" / "prompts.jsonl"
    assert md.is_file()
    assert js.is_file()
    assert prompts.is_file()

    # JSON round-trips with the same keys.
    loaded = json.loads(js.read_text())
    for key in _METRIC_KEYS:
        assert key in loaded

    # Prompt log has one row per query.
    log_lines = prompts.read_text().strip().splitlines()
    assert len(log_lines) == 2


def test_run_rag_eval_empty_queries_returns_zeros(tmp_path: Path) -> None:
    result = run_rag_eval(
        "empty-ds",
        [],
        judge_endpoint="mock",
        report_dir=tmp_path,
    )
    assert result["n_queries"] == 0
    for key in _METRIC_KEYS:
        assert result[key] == 0.0


def test_run_rag_eval_creates_report_dir_when_missing(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "report"
    assert not target.exists()
    run_rag_eval(
        "ds",
        [
            {
                "query": "q",
                "answer": "a",
                "relevant_chunk_ids": [1],
                "contexts": ["c"],
                "ranked_chunk_ids": [1],
            }
        ],
        judge_endpoint="mock",
        report_dir=target,
    )
    assert target.is_dir()
    assert (target / "eval_rag.json").is_file()


def test_run_rag_eval_default_report_dir_under_cache(tmp_path: Path) -> None:
    # Redirect HOME so we don't pollute the real ~/.cache.
    import os

    home = tmp_path / "home"
    home.mkdir()
    prev = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    try:
        result = run_rag_eval(
            "ds",
            [
                {
                    "query": "q",
                    "answer": "a",
                    "relevant_chunk_ids": [1],
                    "contexts": ["c"],
                    "ranked_chunk_ids": [1],
                }
            ],
            judge_endpoint="mock",
        )
    finally:
        if prev is None:
            del os.environ["HOME"]
        else:
            os.environ["HOME"] = prev

    assert "nDCG@1" in result
    # Default path lives under the tmp_path-rooted home.
    reports_root = home / ".cache" / "corpus-forge" / "reports"
    assert reports_root.is_dir()


def test_run_rag_eval_interleaves_distractors_when_no_ranked(tmp_path: Path) -> None:
    queries = [
        {
            "query": "q",
            "answer": "a",
            "relevant_chunk_ids": [1, 2],
            "distractor_chunk_ids": [99, 100, 101],
            "contexts": ["c"],
        }
    ]
    result = run_rag_eval("ds", queries, judge_endpoint="mock", report_dir=tmp_path)
    # nDCG should be > 0 but < 1 because relevant items are interleaved.
    assert 0.0 < result["nDCG@5"] < 1.0
