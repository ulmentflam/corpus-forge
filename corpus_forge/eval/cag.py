"""corpus_forge.eval.cag — CAG evaluation harness with LLM-judge scoring.

Public API
----------
run_cag_eval(dataset, queries, *, judge_endpoint, root=None, report_dir=None) -> dict
    For each query in *queries*:
    - Calls ``corpus_forge.cag.selector.select`` to determine cache hit / miss.
    - Judges the response (cached payload or RAG fall-through) for quality.
    Aggregates: cache_hit_count, rag_count, cache_quality_score,
    rag_quality_score, cache_vs_rag_delta.

    Returns a flat dict with all CAG comparison values.

Fixture format (same as RAG eval)::

    {
        "query":               str,
        "answer":              str,
        "relevant_chunk_ids":  list[int],
        "contexts":            list[str],
    }

When ``root`` is provided, the selector checks ``<root>/<dataset>/<key>.json``
for each query.  A matching file is a cache hit; otherwise it's a RAG miss.
No real retriever is needed — misses return an empty context response.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Stub retriever for offline testing
# ---------------------------------------------------------------------------


class _NullRetriever:
    """Stub retriever that always returns an empty result.

    Used when no real retriever is available (offline / unit-test mode).
    The selector delegates to ``.search(query)`` on cache misses.
    """

    def search(self, query: str, *args: object, **kwargs: object) -> list[object]:  # noqa: ARG002
        return []


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _build_cag_judge_prompt(
    query: str,
    answer: str,
    contexts: list[str],
    route: str,
) -> str:
    """Build a judge prompt for a CAG response."""
    ctx_block = "\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts))
    return (
        f"You are a CAG evaluation judge (route={route!r}). "
        f"Score the answer for the given query and contexts on four dimensions (0.0-1.0):\n"
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
# run_cag_eval
# ---------------------------------------------------------------------------


def run_cag_eval(
    dataset: str,
    queries: list[dict[str, Any]],
    *,
    judge_endpoint: str,
    root: Path | None = None,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    """Run the CAG eval harness over *queries*.

    Parameters
    ----------
    dataset:
        Dataset name; used as the subdirectory segment under *root*.
    queries:
        List of query dicts with keys ``query``, ``answer``,
        ``relevant_chunk_ids``, ``contexts``.
    judge_endpoint:
        ``"mock"`` or a URL for a real Ollama/OpenAI-compatible endpoint.
    root:
        CAG cache root directory.  ``None`` means no cache lookup (all misses).
    report_dir:
        Directory to write ``eval_cag.json`` and ``eval_cag.md`` into.
        Defaults to a timestamped directory under
        ``~/.cache/corpus-forge/reports/``.

    Returns
    -------
    dict
        Keys: ``cache_hit_count``, ``rag_count``, ``cache_quality_score``,
        ``rag_quality_score``, ``cache_vs_rag_delta``.
    """
    from corpus_forge.cag.selector import select  # noqa: PLC0415
    from corpus_forge.eval.judge import JudgeClient  # noqa: PLC0415

    if report_dir is None:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report_dir = Path.home() / ".cache" / "corpus-forge" / "reports" / ts
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    judge = JudgeClient(endpoint=judge_endpoint)
    retriever = _NullRetriever()

    cache_scores: list[float] = []
    rag_scores: list[float] = []
    cache_hit_count = 0
    rag_count = 0

    for q in queries:
        query_text: str = q.get("query", "")
        answer_text: str = q.get("answer", "")
        contexts: list[str] = q.get("contexts", [])

        route, payload = select(
            query_text,
            dataset,
            retriever=retriever,
            root=root,
        )

        if route == "cache":
            cache_hit_count += 1
            # Extract contexts from cached payload if present.
            cached_contexts: list[str] = (
                payload.get("contexts", contexts) if isinstance(payload, dict) else contexts
            )
            cached_answer: str = (
                payload.get("cached_answer", answer_text)
                if isinstance(payload, dict)
                else answer_text
            )
            prompt = _build_cag_judge_prompt(query_text, cached_answer, cached_contexts, route)
            scores = judge.score(prompt)
            avg = sum(scores.values()) / len(scores)
            cache_scores.append(avg)
        else:
            rag_count += 1
            prompt = _build_cag_judge_prompt(query_text, answer_text, contexts, route)
            scores = judge.score(prompt)
            avg = sum(scores.values()) / len(scores)
            rag_scores.append(avg)

    cache_quality_score: float | None = (
        round(sum(cache_scores) / len(cache_scores), 6) if cache_scores else None
    )
    rag_quality_score: float | None = (
        round(sum(rag_scores) / len(rag_scores), 6) if rag_scores else None
    )

    if cache_quality_score is not None and rag_quality_score is not None:
        cache_vs_rag_delta: float | None = round(cache_quality_score - rag_quality_score, 6)
    else:
        cache_vs_rag_delta = None

    result: dict[str, Any] = {
        "cache_hit_count": cache_hit_count,
        "rag_count": rag_count,
        "cache_quality_score": cache_quality_score,
        "rag_quality_score": rag_quality_score,
        "cache_vs_rag_delta": cache_vs_rag_delta,
        "dataset": dataset,
        "judge_endpoint": judge_endpoint,
        "n_queries": len(queries),
    }

    _write_cag_json_report(report_dir, result)
    _write_cag_md_report(report_dir, result, dataset)

    return result


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------


def _write_cag_json_report(report_dir: Path, result: dict[str, Any]) -> None:
    p = report_dir / "eval_cag.json"
    p.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")


def _write_cag_md_report(report_dir: Path, result: dict[str, Any], dataset: str) -> None:
    lines = [
        f"# CAG Eval Report — {dataset}",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "",
        "## CAG vs RAG Comparison",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| cache_hit_count | {result.get('cache_hit_count', 0)} |",
        f"| rag_count | {result.get('rag_count', 0)} |",
        f"| cache_quality_score | {result.get('cache_quality_score')} |",
        f"| rag_quality_score | {result.get('rag_quality_score')} |",
        f"| cache_vs_rag_delta | {result.get('cache_vs_rag_delta')} |",
    ]
    p = report_dir / "eval_cag.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
