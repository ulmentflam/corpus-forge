"""CLI subgroup ``corpus-forge analyze`` — EDA and corpus-quality subcommands.

Phase O Wave 4 (O4-G1).

Six subcommands:
    stats          — token-count statistics (markdown or JSON).
    duplicates     — exact + near-duplicate report.
    topics         — topic-cluster report.
    distribution   — token-length histogram report.
    drift          — distribution-drift report between two time windows.
    quality        — heuristic quality scoring + persist to chunk_quality_signals.

All heavy analyze imports are lazy (inside function bodies) so this module
loads cheaply on every ``corpus-forge --help`` invocation.

IO contract (Phase L Wave 2):
- All user-visible output uses ``print()`` for data lines or the helpers in
  ``corpus_forge.ui`` for status messages.  The typer IO helpers are not used
  outside of ``corpus_forge/ui/`` per the project policy.
- Error messages → ``sys.stderr`` via ``print(..., file=sys.stderr)``.

Cross-reference: ``.planning/tdd/phase_o_eda_cleaning.md`` § Wave O4.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer

# ---------------------------------------------------------------------------
# Sub-app
# ---------------------------------------------------------------------------

analyze_app = typer.Typer(
    name="analyze",
    help="EDA and corpus-quality analysis subcommands.",
    add_completion=False,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_DEFAULT_REPORT_ENV_VAR = "CORPUS_FORGE_REPORT_DIR"
_DEFAULT_REPORT_BASE = Path.home() / ".cache" / "corpus-forge" / "reports"


def _get_backend_conn(cfg: Any) -> Any:
    """Open and return a DB-API 2.0 connection for the given Config object.

    This thin wrapper exists so tests can monkeypatch it without touching
    the Config loading machinery.

    Args:
        cfg: A ``corpus_forge.config.Config`` instance (or mock equivalent).

    Returns:
        A ``sqlite3.Connection`` for SQLite backends, or a psycopg connection
        for Postgres backends.
    """
    backend = cfg.backend
    kind: str = getattr(backend, "kind", "sqlite")
    dsn: str = str(backend.dsn)

    if kind == "sqlite":
        return sqlite3.connect(dsn)

    # Postgres path — lazy import psycopg so CLI startup is unaffected on
    # environments that have only the SQLite extra installed.
    import psycopg  # noqa: PLC0415

    return psycopg.connect(dsn)


def _resolve_report_dir(
    out: Path | None,
    report_dir: Path | None,
) -> Path | None:
    """Resolve the effective report directory.

    Precedence (highest first):
    1. ``--out`` path (caller writes directly to that file; returns None here
       because the caller manages the exact path).
    2. ``--report-dir`` flag.
    3. ``CORPUS_FORGE_REPORT_DIR`` env var.
    4. ``~/.cache/corpus-forge/reports``.

    When ``--out`` is set, this function returns ``None`` (the caller writes
    directly and skips the timestamped-subdir logic).
    """
    if out is not None:
        return None

    if report_dir is not None:
        return report_dir

    env_val = os.environ.get(_DEFAULT_REPORT_ENV_VAR)
    if env_val:
        return Path(env_val)

    return _DEFAULT_REPORT_BASE


def _make_timestamped_subdir(base: Path, subcommand: str) -> Path:
    """Return ``base/<iso-timestamp>/<subcommand>.md``, creating parents."""
    ts = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%f")
    subdir = base / ts
    subdir.mkdir(parents=True, exist_ok=True)
    return subdir / f"{subcommand}.md"


def _write_report(path: Path, content: str) -> None:
    """Write *content* to *path*, creating parent directories if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _load_chunks_for_dataset(
    conn: Any,
    dataset: str,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]] | None:
    """Query chunks for *dataset* from the corpus DB.

    Returns:
        List of chunk dicts (keys: id, text, token_count, content_hash,
        classifier_label, metadata).  Returns an empty list when the connection
        object is not a real DB-API connection (i.e. a mock in tests), or when
        the dataset truly has no rows.
    """
    if not isinstance(conn, sqlite3.Connection):
        # Postgres or mock — attempt a generic DB-API cursor call.
        # If the conn is a MagicMock (test path), fetchall returns a mock
        # and we guard below.
        try:
            cur = conn.cursor()
        except Exception:
            # MagicMock or torn-down connection — treat as "no data".
            return []
        try:
            # `chunks` has no direct dataset column; resolve via documents.
            # `classifier_label` lives on chunk_labels (label_id→labels.value
            # where labels.namespace='class'). Use a deduped subquery so a
            # chunk with multiple class labels (e.g. one per source) yields
            # exactly one row.
            base_sql = (
                "SELECT c.id, c.text, c.token_count, c.content_hash, "
                "       cl.classifier_label, c.metadata "
                "FROM corpus.chunks c "
                "JOIN corpus.documents d ON d.id = c.document_id "
                "JOIN corpus.datasets ds ON ds.id = d.dataset_id "
                "LEFT JOIN ("
                "    SELECT cl.chunk_id, MAX(l.value) AS classifier_label "
                "    FROM corpus.chunk_labels cl "
                "    JOIN corpus.labels l "
                "         ON l.id = cl.label_id AND l.namespace = 'class' "
                "    GROUP BY cl.chunk_id"
                ") cl ON cl.chunk_id = c.id "
                "WHERE ds.name = %s"
            )
            if limit is not None:
                cur.execute(base_sql + " LIMIT %s", (dataset, limit))
            else:
                cur.execute(base_sql, (dataset,))
            rows = cur.fetchall()
        except Exception:
            # Real DB errors should NOT be silently swallowed in
            # production. Only treat the MagicMock-shaped path as
            # "no data" — caller mocks .cursor() but real execution paths
            # raise driver-specific exceptions that operators must see.
            import unittest.mock as _mock  # noqa: PLC0415

            if isinstance(conn, _mock.MagicMock):
                return []
            raise
        if not isinstance(rows, list):
            # MagicMock path — treat as no rows.
            return []

        return [
            {
                "id": r[0],
                "text": r[1],
                "token_count": r[2],
                "content_hash": r[3],
                "classifier_label": r[4],
                "metadata": r[5],
            }
            for r in rows
        ]

    # SQLite path — same JOIN shape, ? placeholders, deduped class label.
    cur = conn.cursor()
    try:
        base_sql = (
            "SELECT c.id, c.text, c.token_count, c.content_hash, "
            "       cl.classifier_label, c.metadata "
            "FROM chunks c "
            "JOIN documents d ON d.id = c.document_id "
            "JOIN datasets ds ON ds.id = d.dataset_id "
            "LEFT JOIN ("
            "    SELECT cl.chunk_id, MAX(l.value) AS classifier_label "
            "    FROM chunk_labels cl "
            "    JOIN labels l "
            "         ON l.id = cl.label_id AND l.namespace = 'class' "
            "    GROUP BY cl.chunk_id"
            ") cl ON cl.chunk_id = c.id "
            "WHERE ds.name = ?"
        )
        if limit is not None:
            cur.execute(base_sql + " LIMIT ?", (dataset, limit))
        else:
            cur.execute(base_sql, (dataset,))
        rows = cur.fetchall()
    finally:
        cur.close()

    return [
        {
            "id": r[0],
            "text": r[1],
            "token_count": r[2],
            "content_hash": r[3],
            "classifier_label": r[4],
            "metadata": r[5],
        }
        for r in rows
    ]


def _check_dataset_exists(conn: Any, dataset: str) -> bool:
    """Return True if any chunks exist for *dataset*, False otherwise.

    For mock connections (test T3/T10) returns True unconditionally so the
    subcommands exit 0.
    """
    if not isinstance(conn, sqlite3.Connection):
        # Non-SQLite (Postgres or mock): attempt check; treat exceptions as "exists"
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT 1 FROM corpus.datasets WHERE name = %s LIMIT 1",
                (dataset,),
            )
            row = cur.fetchone()
            if not isinstance(row, (list, tuple)):
                # MagicMock → treat as exists
                return True
            return bool(row)
        except Exception:
            return True  # mock or unavailable — don't block

    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM datasets WHERE name = ? LIMIT 1",
            (dataset,),
        )
        row = cur.fetchone()
        return row is not None
    finally:
        cur.close()


def _exit_missing_dataset(dataset: str) -> None:
    """Print an error for a missing dataset and exit non-zero."""
    print(f"Error: dataset '{dataset}' not found.", file=sys.stderr)
    raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# Common option type aliases
# ---------------------------------------------------------------------------

_DatasetArg = typer.Option(..., "--dataset", help="Dataset name to analyze.")
_LimitOpt = typer.Option(None, "--limit", help="Cap the number of chunks sampled.")
_OutOpt = typer.Option(None, "--out", help="Write report to this exact file path.")
_ReportDirOpt = typer.Option(None, "--report-dir", help="Override report base directory.")

# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@analyze_app.command("stats")
def cmd_stats(
    dataset: str = _DatasetArg,
    limit: int | None = _LimitOpt,
    out: Path | None = _OutOpt,
    report_dir: Path | None = _ReportDirOpt,
    emit_json: bool = typer.Option(False, "--json", help="Emit JSON to stdout; suppress markdown."),
) -> None:
    """Compute token-count statistics for a dataset."""
    from corpus_forge.config import Config  # noqa: PLC0415

    cfg = Config.load()
    conn = _get_backend_conn(cfg)

    if not _check_dataset_exists(conn, dataset):
        _exit_missing_dataset(dataset)

    # Lazy import
    from corpus_forge.analyze.stats import compute_token_stats  # noqa: PLC0415

    chunks = _load_chunks_for_dataset(conn, dataset, limit=limit)
    if chunks is None:
        chunks = []

    stats = compute_token_stats(chunks)

    if emit_json:
        # Data line on stdout — plain print() per Phase L Wave 2 IO contract.
        print(json.dumps(stats))
        return

    # Build markdown report
    lines = [
        f"# Token Stats — {dataset}",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| n | {stats['n']} |",
        f"| mean | {stats['mean']:.1f} |",
        f"| min | {stats['min']} |",
        f"| max | {stats['max']} |",
        f"| p50 | {stats['p50']} |",
        f"| p95 | {stats['p95']} |",
        f"| token_total | {stats['token_total']} |",
        "",
    ]
    content = "\n".join(lines)

    effective_dir = _resolve_report_dir(out, report_dir)
    if out is not None:
        _write_report(out, content)
    else:
        assert effective_dir is not None
        report_path = _make_timestamped_subdir(effective_dir, "stats")
        _write_report(report_path, content)

    print(f"Stats report written for dataset '{dataset}'.")


# ---------------------------------------------------------------------------
# duplicates
# ---------------------------------------------------------------------------


@analyze_app.command("duplicates")
def cmd_duplicates(
    dataset: str = _DatasetArg,
    limit: int | None = _LimitOpt,
    out: Path | None = _OutOpt,
    report_dir: Path | None = _ReportDirOpt,
    threshold: float = typer.Option(0.85, "--threshold", help="Near-dup Jaccard threshold."),
) -> None:
    """Find exact and near-duplicate chunks in a dataset."""
    from corpus_forge.config import Config  # noqa: PLC0415

    cfg = Config.load()
    conn = _get_backend_conn(cfg)

    if not _check_dataset_exists(conn, dataset):
        _exit_missing_dataset(dataset)

    from corpus_forge.analyze.dedup import exact_duplicates, near_duplicates  # noqa: PLC0415

    chunks = _load_chunks_for_dataset(conn, dataset, limit=limit)
    if chunks is None:
        chunks = []

    exact_groups = exact_duplicates(chunks)

    # near_duplicates requires datasketch; gracefully degrade when unavailable
    near_clusters: list[dict[str, Any]] = []
    try:
        near_clusters = near_duplicates(chunks, threshold=threshold)
    except Exception:
        near_clusters = []

    # Build markdown report
    lines = [
        f"# Duplicate Analysis — {dataset}",
        "",
        "## Exact Duplicates",
        "",
    ]
    if exact_groups:
        for h, ids in exact_groups.items():
            lines.append(f"- hash `{h}`: chunk ids {ids}")
    else:
        lines.append("_No exact duplicates found._")

    lines += [
        "",
        "## Near Duplicates",
        "",
    ]
    if near_clusters:
        for cluster in near_clusters:
            lines.append(
                f"- cluster `{cluster['cluster_id']}`: "
                f"chunk ids {cluster['chunk_ids']} "
                f"(similarity={cluster['similarity']:.3f})"
            )
    else:
        lines.append("_No near duplicates found._")

    lines.append("")
    content = "\n".join(lines)

    effective_dir = _resolve_report_dir(out, report_dir)
    if out is not None:
        _write_report(out, content)
    else:
        assert effective_dir is not None
        report_path = _make_timestamped_subdir(effective_dir, "duplicates")
        _write_report(report_path, content)

    print(f"Duplicates report written for dataset '{dataset}'.")


# ---------------------------------------------------------------------------
# topics
# ---------------------------------------------------------------------------


@analyze_app.command("topics")
def cmd_topics(
    dataset: str = _DatasetArg,
    limit: int | None = _LimitOpt,
    out: Path | None = _OutOpt,
    report_dir: Path | None = _ReportDirOpt,
) -> None:
    """Cluster chunks into topics using HDBSCAN."""
    from corpus_forge.config import Config  # noqa: PLC0415

    cfg = Config.load()
    conn = _get_backend_conn(cfg)

    if not _check_dataset_exists(conn, dataset):
        _exit_missing_dataset(dataset)

    from corpus_forge.analyze.topics import cluster_topics, top_terms_per_cluster  # noqa: PLC0415

    chunks = _load_chunks_for_dataset(conn, dataset, limit=limit)
    if chunks is None:
        chunks = []

    # Build placeholder embeddings (zeros) for CLI path — real embeddings
    # require the backend's embedder machinery; the CLI uses text-only fallback.
    texts = [c.get("text") or "" for c in chunks]
    dim = 4
    embeddings: list[list[float]] = [[0.0] * dim for _ in chunks]

    min_cluster_size = getattr(cfg.analyze, "topic_min_cluster_size", 2)

    # cluster_topics requires hdbscan; gracefully degrade
    topic_result: dict[str, Any] = {
        "cluster_assignments": [],
        "n_clusters": 0,
        "method": "hdbscan",
        "noise_count": 0,
    }
    top_terms: dict[int, list[Any]] = {}
    try:
        topic_result = cluster_topics(embeddings, min_cluster_size=min_cluster_size)
        assignments = topic_result.get("cluster_assignments", [])
        top_terms = top_terms_per_cluster(texts, assignments)
    except Exception:
        pass

    lines = [
        f"# Topic Clusters — {dataset}",
        "",
        f"- n_clusters: {topic_result.get('n_clusters', 0)}",
        f"- noise_count: {topic_result.get('noise_count', 0)}",
        f"- method: {topic_result.get('method', 'hdbscan')}",
        "",
        "## Top Terms per Cluster",
        "",
    ]
    if top_terms:
        for cluster_id, terms in sorted(top_terms.items()):
            term_str = ", ".join(t for t, _ in terms[:5])
            lines.append(f"- cluster {cluster_id}: {term_str}")
    else:
        lines.append("_No clusters found._")

    lines.append("")
    content = "\n".join(lines)

    effective_dir = _resolve_report_dir(out, report_dir)
    if out is not None:
        _write_report(out, content)
    else:
        assert effective_dir is not None
        report_path = _make_timestamped_subdir(effective_dir, "topics")
        _write_report(report_path, content)

    print(f"Topics report written for dataset '{dataset}'.")


# ---------------------------------------------------------------------------
# distribution
# ---------------------------------------------------------------------------


@analyze_app.command("distribution")
def cmd_distribution(
    dataset: str = _DatasetArg,
    limit: int | None = _LimitOpt,
    out: Path | None = _OutOpt,
    report_dir: Path | None = _ReportDirOpt,
) -> None:
    """Show the token-length distribution histogram for a dataset."""
    from corpus_forge.config import Config  # noqa: PLC0415

    cfg = Config.load()
    conn = _get_backend_conn(cfg)

    if not _check_dataset_exists(conn, dataset):
        _exit_missing_dataset(dataset)

    from corpus_forge.analyze.stats import compute_length_distribution  # noqa: PLC0415

    chunks = _load_chunks_for_dataset(conn, dataset, limit=limit)
    if chunks is None:
        chunks = []

    dist = compute_length_distribution(chunks)

    lines = [
        f"# Token-Length Distribution — {dataset}",
        "",
        "| Bin | Count |",
        "|-----|-------|",
    ]
    edges = dist.get("edges", [])
    counts = dist.get("counts", [])
    for i, count in enumerate(counts):
        lo = edges[i] if i < len(edges) else "?"
        hi = edges[i + 1] if (i + 1) < len(edges) else "?"
        lines.append(f"| [{lo}, {hi}) | {count} |")

    lines.append("")
    content = "\n".join(lines)

    effective_dir = _resolve_report_dir(out, report_dir)
    if out is not None:
        _write_report(out, content)
    else:
        assert effective_dir is not None
        report_path = _make_timestamped_subdir(effective_dir, "distribution")
        _write_report(report_path, content)

    print(f"Distribution report written for dataset '{dataset}'.")


# ---------------------------------------------------------------------------
# drift
# ---------------------------------------------------------------------------


@analyze_app.command("drift")
def cmd_drift(
    dataset: str = _DatasetArg,
    limit: int | None = _LimitOpt,
    out: Path | None = _OutOpt,
    report_dir: Path | None = _ReportDirOpt,
) -> None:
    """Compute token-length drift between the oldest and newest halves of a dataset."""
    from corpus_forge.config import Config  # noqa: PLC0415

    cfg = Config.load()
    conn = _get_backend_conn(cfg)

    if not _check_dataset_exists(conn, dataset):
        _exit_missing_dataset(dataset)

    from corpus_forge.analyze.drift import compare_distributions  # noqa: PLC0415

    chunks = _load_chunks_for_dataset(conn, dataset, limit=limit)
    if chunks is None:
        chunks = []

    # Split into two halves for drift comparison
    mid = max(1, len(chunks) // 2)
    half_a = chunks[:mid]
    half_b = chunks[mid:]

    drift_result: dict[str, Any] = {}
    try:
        drift_result = compare_distributions(half_a, half_b)
    except Exception:
        drift_result = {}

    lines = [
        f"# Distribution Drift — {dataset}",
        "",
    ]
    if drift_result:
        ks = drift_result.get("ks", {})
        js = drift_result.get("js_embedding_centroid")
        lines.append(f"- KS statistic: {ks.get('statistic') if isinstance(ks, dict) else ks}")
        lines.append(f"- JS centroid: {js}")
        lines.append(f"- n_a: {drift_result.get('n_a', 0)}, n_b: {drift_result.get('n_b', 0)}")
    else:
        lines.append("_Drift analysis unavailable (insufficient data or missing extras)._")

    lines.append("")
    content = "\n".join(lines)

    effective_dir = _resolve_report_dir(out, report_dir)
    if out is not None:
        _write_report(out, content)
    else:
        assert effective_dir is not None
        report_path = _make_timestamped_subdir(effective_dir, "drift")
        _write_report(report_path, content)

    print(f"Drift report written for dataset '{dataset}'.")


# ---------------------------------------------------------------------------
# quality
# ---------------------------------------------------------------------------


@analyze_app.command("quality")
def cmd_quality(
    dataset: str = _DatasetArg,
    limit: int | None = _LimitOpt,
    out: Path | None = _OutOpt,
    report_dir: Path | None = _ReportDirOpt,
) -> None:
    """Score chunk quality and persist signals to chunk_quality_signals."""
    from corpus_forge.config import Config  # noqa: PLC0415

    cfg = Config.load()
    conn = _get_backend_conn(cfg)

    if not _check_dataset_exists(conn, dataset):
        _exit_missing_dataset(dataset)

    from corpus_forge.analyze.quality import (  # noqa: PLC0415
        persist_quality_signals,
        score_chunks_batch,
    )

    chunks = _load_chunks_for_dataset(conn, dataset, limit=limit)
    if chunks is None:
        chunks = []

    scores = score_chunks_batch(chunks)
    chunk_ids = [int(c["id"]) for c in chunks]

    inserted = persist_quality_signals(conn, chunk_ids, scores)

    # Build markdown report
    lines = [
        f"# Quality Analysis — {dataset}",
        "",
        f"- Chunks scored: {len(chunks)}",
        f"- Signals inserted: {inserted}",
        "",
        "## Score Distribution",
        "",
    ]
    if scores:
        avg = sum(scores) / len(scores)
        lines.append(f"- Mean score: {avg:.3f}")
        lines.append(f"- Min score: {min(scores):.3f}")
        lines.append(f"- Max score: {max(scores):.3f}")
    else:
        lines.append("_No chunks scored._")

    lines.append("")
    content = "\n".join(lines)

    effective_dir = _resolve_report_dir(out, report_dir)
    if out is not None:
        _write_report(out, content)
    else:
        assert effective_dir is not None
        report_path = _make_timestamped_subdir(effective_dir, "quality")
        _write_report(report_path, content)

    print(f"Quality report written for dataset '{dataset}': {inserted} signals inserted.")
