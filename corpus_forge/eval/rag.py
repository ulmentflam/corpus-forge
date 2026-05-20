"""corpus_forge.eval.rag — RAG evaluation harness with LLM-judge scoring.

Public API
----------
run_rag_eval(dataset, queries, *, judge_endpoint, k=5, report_dir=None) -> dict
    For each query in *queries*:
    - Computes nDCG@1, nDCG@5, nDCG@10, MRR from ``relevant_chunk_ids`` and
      the fixture's implicit ranking (the ``contexts`` list order).
    - Calls the judge for faithfulness, answer_relevance, context_precision,
      context_recall using the query's ``contexts`` and ``answer``.
    Averages all metrics across queries.

    Writes:
    - ``report_dir/eval_rag.md``  — human-readable Markdown.
    - ``report_dir/eval_rag.json`` — machine-readable JSON.
    - ``report_dir/judge/prompts.jsonl`` — raw prompt + response pairs.

    Returns the JSON dict.

The fixture format used by the tests is::

    {
        "query":               str,
        "answer":              str,
        "relevant_chunk_ids":  list[int],
        "contexts":            list[str],
    }

The ranking is derived from the position of ``relevant_chunk_ids`` entries
in the fixture list (position 0 = rank 1).  When no retriever is wired the
eval works purely from the fixture, which allows offline testing.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_judge_prompt(query: str, answer: str, contexts: list[str]) -> str:
    """Build the prompt string sent to the LLM judge."""
    ctx_block = "\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts))
    return (
        f"You are a RAG evaluation judge. Score the answer for the given query "
        f"and contexts on four dimensions (0.0-1.0):\n"
        f"- faithfulness\n"
        f"- answer_relevance\n"
        f"- context_precision\n"
        f"- context_recall\n\n"
        f"Query: {query}\n\n"
        f"Contexts:\n{ctx_block}\n\n"
        f"Answer: {answer}\n\n"
        f'Respond with JSON: {{"faithfulness": ..., "answer_relevance": ..., '
        f'"context_precision": ..., "context_recall": ...}}'
    )


# ---------------------------------------------------------------------------
# Minimal nDCG / MRR helpers (no numpy needed — stdlib only)
# ---------------------------------------------------------------------------


def _ndcg_at_k(ranked_ids: list[int], relevant_ids: list[int], k: int) -> float:
    """Binary nDCG@k (stdlib, no numpy)."""
    if k <= 0 or not ranked_ids or not relevant_ids:
        return 0.0
    relevant = set(relevant_ids)
    top = ranked_ids[:k]

    def dcg(ids: list[int]) -> float:
        return sum(
            (1.0 / math.log2(rank + 1)) for rank, cid in enumerate(ids, start=1) if cid in relevant
        )

    dcg_val = dcg(top)
    # IDCG: best possible ranking — all relevant items at the top.
    n_ideal = min(len(relevant), k)
    idcg_val = sum(1.0 / math.log2(rank + 1) for rank in range(1, n_ideal + 1))
    if idcg_val == 0.0:
        return 0.0
    return dcg_val / idcg_val


def _mrr(ranked_ids: list[int], relevant_ids: list[int], k: int) -> float:
    """MRR@k."""
    if not ranked_ids or not relevant_ids:
        return 0.0
    relevant = set(relevant_ids)
    for rank, cid in enumerate(ranked_ids[:k], start=1):
        if cid in relevant:
            return 1.0 / float(rank)
    return 0.0


# ---------------------------------------------------------------------------
# run_rag_eval
# ---------------------------------------------------------------------------


def run_rag_eval(
    dataset: str,
    queries: list[dict[str, Any]],
    *,
    judge_endpoint: str,
    k: int = 5,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the RAG eval harness over *queries* using *judge_endpoint*.

    Parameters
    ----------
    dataset:
        Dataset name (informational; included in the report header).
    queries:
        List of query dicts with keys ``query``, ``answer``,
        ``relevant_chunk_ids``, ``contexts``.
    judge_endpoint:
        ``"mock"`` for the deterministic mock judge, or a URL for a real
        Ollama/OpenAI-compatible endpoint.
    k:
        Primary k cutoff for retrieval metrics.  nDCG@1, nDCG@5, nDCG@10
        and MRR are always computed regardless of this value.
    report_dir:
        Directory to write ``eval_rag.md``, ``eval_rag.json``, and
        ``judge/prompts.jsonl`` into.  Created if it does not exist.
        Defaults to a timestamped directory under
        ``~/.cache/corpus-forge/reports/``.

    Returns
    -------
    dict
        Flat dict with all metric values.
    """
    from corpus_forge.eval.judge import JudgeClient  # noqa: PLC0415

    if report_dir is None:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report_dir = Path.home() / ".cache" / "corpus-forge" / "reports" / ts
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    judge = JudgeClient(endpoint=judge_endpoint)

    # Accumulators for retrieval metrics.
    sums: dict[str, float] = {
        "nDCG@1": 0.0,
        "nDCG@5": 0.0,
        "nDCG@10": 0.0,
        "MRR": 0.0,
        "faithfulness": 0.0,
        "answer_relevance": 0.0,
        "context_precision": 0.0,
        "context_recall": 0.0,
    }

    # Raw prompts log for auditability.
    prompt_log: list[dict[str, Any]] = []

    for q in queries:
        query_text: str = q.get("query", "")
        answer_text: str = q.get("answer", "")
        relevant_ids: list[int] = [int(x) for x in q.get("relevant_chunk_ids", [])]
        contexts: list[str] = q.get("contexts", [])

        # Build the ranked list from the fixture's `ranked_chunk_ids` if
        # provided (the realistic harness shape — retriever output). Fall
        # back to interleaving relevant + distractor ids so nDCG/MRR are
        # non-trivial even on minimal fixtures (a naive `list(relevant_ids)`
        # makes every metric trivially 1.0 and hides regressions).
        fixture_ranked = q.get("ranked_chunk_ids")
        if fixture_ranked is not None:
            ranked_ids = [int(x) for x in fixture_ranked]
        else:
            distractor_ids: list[int] = [int(x) for x in q.get("distractor_chunk_ids", [])]
            # Interleave so relevant items are NOT all at the top.
            ranked_ids = []
            r_iter = iter(relevant_ids)
            d_iter = iter(distractor_ids)
            for i in range(max(len(relevant_ids) + len(distractor_ids), 1)):
                src = d_iter if i % 2 == 0 and distractor_ids else r_iter
                try:
                    ranked_ids.append(next(src))
                except StopIteration:
                    other = r_iter if src is d_iter else d_iter
                    try:
                        ranked_ids.append(next(other))
                    except StopIteration:
                        break

        sums["nDCG@1"] += _ndcg_at_k(ranked_ids, relevant_ids, 1)
        sums["nDCG@5"] += _ndcg_at_k(ranked_ids, relevant_ids, 5)
        sums["nDCG@10"] += _ndcg_at_k(ranked_ids, relevant_ids, 10)
        sums["MRR"] += _mrr(ranked_ids, relevant_ids, max(k, 10))

        # Judge scoring.
        prompt = _build_judge_prompt(query_text, answer_text, contexts)
        judge_scores = judge.score(prompt)

        for dim in ("faithfulness", "answer_relevance", "context_precision", "context_recall"):
            sums[dim] += judge_scores.get(dim, 0.0)

        prompt_log.append(
            {
                "query": query_text,
                "prompt": prompt,
                "judge_scores": judge_scores,
            }
        )

    n = max(len(queries), 1)
    result: dict[str, Any] = {k_name: round(v / n, 6) for k_name, v in sums.items()}
    result["n_queries"] = len(queries)
    result["dataset"] = dataset
    result["judge_endpoint"] = judge_endpoint

    # Write reports.
    _write_json_report(report_dir, result)
    _write_md_report(report_dir, result, dataset)
    _write_prompt_log(report_dir, prompt_log)

    return result


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------


def _write_json_report(report_dir: Path, result: dict[str, Any]) -> None:
    p = report_dir / "eval_rag.json"
    p.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")


def _write_md_report(report_dir: Path, result: dict[str, Any], dataset: str) -> None:
    lines = [
        f"# RAG Eval Report — {dataset}",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "## Retrieval Metrics",
        "",
        "| Metric | Value |",
        "|--------|-------|",
    ]
    for key in ("nDCG@1", "nDCG@5", "nDCG@10", "MRR"):
        lines.append(f"| {key} | {result.get(key, 0.0):.4f} |")
    lines += [
        "",
        "## Judge Scores",
        "",
        "| Dimension | Score |",
        "|-----------|-------|",
    ]
    for key in ("faithfulness", "answer_relevance", "context_precision", "context_recall"):
        lines.append(f"| {key} | {result.get(key, 0.0):.4f} |")
    p = report_dir / "eval_rag.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_prompt_log(report_dir: Path, prompt_log: list[dict[str, Any]]) -> None:
    judge_dir = report_dir / "judge"
    judge_dir.mkdir(parents=True, exist_ok=True)
    p = judge_dir / "prompts.jsonl"
    with p.open("w", encoding="utf-8") as fh:
        for entry in prompt_log:
            fh.write(json.dumps(entry) + "\n")
