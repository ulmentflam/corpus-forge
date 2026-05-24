"""corpus_forge.eval.distill — preprocessing-health metrics over the SDFT capture set.

Public API
----------
run_distill_eval(dataset, *, template, report_dir, backend) -> dict
    Computes preprocessing-health metrics over the SDFT demonstrations captured
    for *dataset*.  NO LLM judge calls — purely stats.

Metrics
-------
coverage
    sum(len(target) over SDFT rows) / max(sum(len(text) over chunks), 1).
    Clipped to [0, 1].

source_mix
    dict[str, int] with all 8 SDFTSource keys, zero-filled for absent sources.

template_fidelity
    Render each row's student+teacher messages through ``corpus_forge.templates.render``.
    Reports n_rows, n_rendered_ok, n_truncated (rendered length > MAX_TOKENS*4 chars),
    n_failed (template render raised).

token_stats
    p50, p95, max, mean, total over rough target token counts
    (len(target) // 4 — matches analyze/stats.py convention).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, cast


class _DistillBackend(Protocol):
    """Minimal duck-typed shape used by :func:`run_distill_eval`.

    Concrete callers pass ``SQLiteBackend`` or ``PostgresBackend``; tests pass
    in-memory doubles.  The protocol omits the wider ``StorageBackend``
    surface to keep the test-double burden small.
    """

    def find_dataset_id_by_name(self, name: str) -> int | None: ...
    def list_sdft_demonstrations(self, dataset_id: int) -> list[dict]: ...
    def _execute(self, sql: str, params: Sequence[object] = ()) -> Sequence[dict]: ...


# Token count proxy: one token ≈ 4 characters (same as analyze/stats.py convention).
_CHARS_PER_TOKEN = 4

# Truncation threshold: rendered output exceeding this many characters is flagged.
_MAX_TOKENS = 4096
_MAX_CHARS = _MAX_TOKENS * _CHARS_PER_TOKEN

# All 8 SDFT source values (zero-filled in source_mix output).
_ALL_SDFT_SOURCES = [
    "curation_commit",
    "rate_search_result",
    "record_demonstration",
    "cli_feedback",
    "claude_code",
    "gemini",
    "opencode",
    "codex",
]


# ---------------------------------------------------------------------------
# Backend factory (monkeypatched by tests)
# ---------------------------------------------------------------------------


def _build_backend() -> _DistillBackend:
    """Return the default backend from the loaded config.

    Tests monkeypatch this to inject an in-memory SQLiteBackend.

    Return type is the local :class:`_DistillBackend` Protocol, which
    captures only the three methods this module needs (``find_dataset_id_by_name``,
    ``list_sdft_demonstrations``, ``_execute``) — keeping the test-double
    surface narrow.
    """
    from corpus_forge.config import Config  # noqa: PLC0415

    config = Config.load()
    kind = getattr(getattr(config, "backend", None), "kind", "postgres")
    if kind == "sqlite":
        from corpus_forge.backends.sqlite import SQLiteBackend  # noqa: PLC0415

        return cast(
            _DistillBackend,
            SQLiteBackend(
                path=config.backend.dsn,
                schema=getattr(config.backend, "schema", "") or "",
            ),
        )
    from corpus_forge.backends.postgres import PostgresBackend  # noqa: PLC0415

    return cast(
        _DistillBackend,
        PostgresBackend(dsn=config.backend.dsn, schema=config.backend.schema),
    )


def _get_backend() -> _DistillBackend:
    """Alias for ``_build_backend``; both are monkeypatched by tests."""
    return _build_backend()


# ---------------------------------------------------------------------------
# Core metric helpers
# ---------------------------------------------------------------------------


def _compute_coverage(sdft_rows: list[dict], chunks: list[dict]) -> float:
    """Compute coverage: fraction of corpus tokens represented in the SDFT set."""
    sdft_char_sum = sum(len(row.get("target") or "") for row in sdft_rows)
    chunk_char_sum = sum(len(chunk.get("text") or "") for chunk in chunks)
    if chunk_char_sum == 0:
        return 0.0
    raw = sdft_char_sum / chunk_char_sum
    return min(raw, 1.0)


def _compute_source_mix(sdft_rows: list[dict]) -> dict[str, int]:
    """Count rows per source; zero-fill all 8 SDFTSource keys."""
    counts: dict[str, int] = dict.fromkeys(_ALL_SDFT_SOURCES, 0)
    for row in sdft_rows:
        src = row.get("source") or ""
        if src in counts:
            counts[src] += 1
        else:
            counts[src] = counts.get(src, 0) + 1
    return counts


def _compute_template_fidelity(
    sdft_rows: list[dict],
    template: str,
) -> dict[str, int]:
    """Render each row's messages and report fidelity stats."""
    from corpus_forge.templates import render  # noqa: PLC0415

    n_rows = len(sdft_rows)
    n_rendered_ok = 0
    n_truncated = 0
    n_failed = 0

    for row in sdft_rows:
        student = row.get("student_messages") or []
        teacher = row.get("teacher_messages") or []
        # Combine student + teacher messages for the round-trip fidelity check.
        all_messages: list[dict] = []
        if isinstance(student, str):
            try:
                student = json.loads(student)
            except (json.JSONDecodeError, TypeError):
                student = []
        if isinstance(teacher, str):
            try:
                teacher = json.loads(teacher)
            except (json.JSONDecodeError, TypeError):
                teacher = []
        all_messages = list(student) + list(teacher)

        try:
            rendered = render(template, all_messages)
            if len(rendered) > _MAX_CHARS:
                n_truncated += 1
            else:
                n_rendered_ok += 1
        except Exception:
            n_failed += 1

    return {
        "n_rows": n_rows,
        "n_rendered_ok": n_rendered_ok,
        "n_truncated": n_truncated,
        "n_failed": n_failed,
    }


def _compute_token_stats(sdft_rows: list[dict]) -> dict[str, object]:
    """Compute p50/p95/max/mean/total over rough target token counts."""
    if not sdft_rows:
        return {"p50": 0, "p95": 0, "max": 0, "mean": 0.0, "total": 0}

    token_counts = [len(row.get("target") or "") // _CHARS_PER_TOKEN for row in sdft_rows]
    sorted_counts = sorted(token_counts)
    n = len(sorted_counts)
    total = sum(sorted_counts)
    mean = total / n if n > 0 else 0.0

    def _percentile(data: list[int], pct: float) -> int:
        if not data:
            return 0
        idx = int(pct / 100.0 * len(data))
        idx = min(idx, len(data) - 1)
        return data[idx]

    return {
        "p50": _percentile(sorted_counts, 50),
        "p95": _percentile(sorted_counts, 95),
        "max": sorted_counts[-1] if sorted_counts else 0,
        "mean": round(mean, 6),
        "total": total,
    }


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------


def _write_md_report(report_dir: Path, result: dict[str, object], dataset: str) -> None:
    """Write a human-readable Markdown report."""
    lines = [
        f"# Distill Eval Report — {dataset}",
        "",
        "## Coverage",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| coverage | {result['coverage']:.6f} |",
        "",
        "## Source Mix",
        "",
        "| Source | Count |",
        "|--------|-------|",
    ]
    source_mix = cast(dict[str, int], result["source_mix"])
    for src, count in sorted(source_mix.items()):
        lines.append(f"| {src} | {count} |")
    lines += [
        "",
        "## Template Fidelity",
        "",
        "| Metric | Value |",
        "|--------|-------|",
    ]
    fidelity = cast(dict[str, int], result["template_fidelity"])
    for key in ("n_rows", "n_rendered_ok", "n_truncated", "n_failed"):
        lines.append(f"| {key} | {fidelity.get(key, 0)} |")
    lines += [
        "",
        "## Token Stats",
        "",
        "| Metric | Value |",
        "|--------|-------|",
    ]
    stats = cast(dict[str, object], result["token_stats"])
    for key in ("p50", "p95", "max", "mean", "total"):
        lines.append(f"| {key} | {stats.get(key, 0)} |")
    p = report_dir / "eval_distill.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_json_report(report_dir: Path, result: dict[str, object]) -> None:
    """Write a machine-readable JSON report (excludes generated_at for determinism)."""
    # Exclude generated_at from the persisted JSON so cross-run comparison is stable.
    storable = {k: v for k, v in result.items() if k != "generated_at"}
    p = report_dir / "eval_distill.json"
    p.write_text(json.dumps(storable, indent=2, sort_keys=True), encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_distill_eval(
    dataset: str,
    *,
    template: str = "chatml",
    report_dir: Path | None = None,
    backend: _DistillBackend | None = None,
) -> dict[str, object]:
    """Run preprocessing-health metrics over the SDFT capture set.

    Parameters
    ----------
    dataset:
        Dataset name.  Must exist in the backend; raises ``ValueError`` if not.
    template:
        Chat template name for the fidelity check.  Defaults to ``"chatml"``.
    report_dir:
        Directory to write ``eval_distill.md`` and ``eval_distill.json``.
        Created automatically if it does not exist.  Defaults to a timestamped
        directory under ``~/.cache/corpus-forge/reports/``.
    backend:
        Backend instance.  Defaults to ``_get_backend()`` (monkeypatched in
        tests to inject an in-memory SQLiteBackend).

    Returns
    -------
    dict
        Keys: ``coverage``, ``source_mix``, ``template_fidelity``, ``token_stats``,
        ``dataset``.  Does NOT include ``generated_at`` to keep the return value
        deterministic across runs.

    Raises
    ------
    ValueError
        When *dataset* does not exist in the backend.
    """
    if backend is None:
        backend = _get_backend()

    # Resolve dataset_id; raise on unknown dataset.
    dataset_id = backend.find_dataset_id_by_name(dataset)
    if dataset_id is None:
        raise ValueError(f"Dataset {dataset!r} not found in backend.")

    # Read SDFT demonstrations for this dataset.
    sdft_rows = backend.list_sdft_demonstrations(dataset_id)

    # Read all chunks for the dataset (for coverage denominator).
    chunks = _list_chunks_for_dataset(backend, dataset_id)

    # Compute metrics.
    coverage = _compute_coverage(sdft_rows, chunks)
    source_mix = _compute_source_mix(sdft_rows)
    template_fidelity = _compute_template_fidelity(sdft_rows, template)
    token_stats = _compute_token_stats(sdft_rows)

    result: dict[str, object] = {
        "coverage": coverage,
        "dataset": dataset,
        "source_mix": source_mix,
        "template_fidelity": template_fidelity,
        "token_stats": token_stats,
    }

    # Write reports.
    if report_dir is None:
        from datetime import UTC, datetime  # noqa: PLC0415

        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        report_dir = Path.home() / ".cache" / "corpus-forge" / "reports" / ts
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    _write_md_report(report_dir, result, dataset)
    _write_json_report(report_dir, result)

    return result


# ---------------------------------------------------------------------------
# Internal: chunk listing helper
# ---------------------------------------------------------------------------


def _list_chunks_for_dataset(backend: _DistillBackend, dataset_id: int) -> list[dict]:
    """Return all chunks for *dataset_id* as a list of dicts with a ``text`` key.

    Fetches via a raw SQL query that joins chunks → documents → dataset.
    Works for both SQLiteBackend and PostgresBackend (placeholder style
    detected from the backend type).
    """
    conn_type = type(backend).__name__

    if "Postgres" in conn_type:
        # PostgresBackend: %s placeholders + a configurable schema prefix
        # (defaults to "corpus"). Resolve via the backend attribute so
        # non-default schemas work; fall back to "corpus" for backward compat.
        schema = getattr(backend, "schema", None) or "corpus"
        prefix = f"{schema}."
        rows = backend._execute(
            f"SELECT c.text FROM {prefix}chunks c"
            f" JOIN {prefix}documents d ON d.id = c.document_id"
            " WHERE d.dataset_id = %s",
            (dataset_id,),
        )
    else:
        # SQLiteBackend: uses ? placeholders, no schema prefix.
        rows = backend._execute(
            "SELECT c.text FROM chunks c"
            " JOIN documents d ON d.id = c.document_id"
            " WHERE d.dataset_id = ?",
            (dataset_id,),
        )

    return list(rows)
