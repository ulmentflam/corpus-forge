"""Phase N Wave 0 — broadened retrieval-quality bench harness.

Gated by ``CF_PHASE_N_BENCH=1`` so this test never runs in default CI.
Builds on the Phase M Wave 5 semble bench (``test_semble_bench.py``) but:

1. Iterates **two** corpora — corpus-forge itself (``current``) and a
   vendored OSS code snapshot (``vendored``, currently
   ``tests/fixtures/external/flask-snapshot/``).
2. Runs **only** the current ``HybridRetriever`` (no semble side-by-side)
   so the JSON dump is the canonical Phase N baseline that Waves 1-3
   must beat.
3. Splits the query set by each query's ``corpus`` field and emits
   per-corpus + aggregated metrics in the JSON shape pinned by
   ``test_phase_n_baseline.py``.

The shape is::

    {
      "schema_version": 1,
      "phase": "N",
      "wave": 0,
      "kind": "phase_n_baseline",
      "generated_at": "<isoformat>",
      "repo_root": "<basename>",
      "git_head": "<sha>",
      "n_queries": <int>,
      "corpus_metadata": {
        "current":   {"source": "corpus-forge", "n_files": ..., "n_bytes": ...},
        "vendored":  {"source": "pallets/flask", "upstream_commit": "<sha>",
                      "license": "BSD-3-Clause", "n_files": ..., "n_bytes": ...},
      },
      "by_corpus": {
        "current":  {"mrr_at_10": ..., "recall_at_5": ..., "p50_latency_ms": ...,
                     "p95_latency_ms": ..., "n_queries": ...,
                     "by_category": {<cat>: {<metrics>}, ...},
                     "per_query": [...]},
        "vendored": {... same shape ...},
      },
      "aggregated": {... same metric keys, computed across all queries ...},
    }

Reproduction
------------

::

    CF_PHASE_N_BENCH=1 uv run pytest tests/perf/test_phase_n_bench.py -v

Override corpus selection (default ``all``)::

    CF_PHASE_N_CORPUS=current  uv run pytest ...
    CF_PHASE_N_CORPUS=vendored uv run pytest ...

Each run writes ``tests/perf/out/phase_n_baseline_<ISO>.json``.  Once
satisfied with the numbers, copy the ISO-stamped file to
``tests/perf/out/phase_n_baseline.json`` (canonical baseline that
``test_phase_n_baseline.py`` rot-detects).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# Skip the entire module unless the env var is set.
pytestmark = pytest.mark.skipif(
    os.environ.get("CF_PHASE_N_BENCH") != "1",
    reason="set CF_PHASE_N_BENCH=1 to run the Phase N broadened bench.",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUT_DIR = _REPO_ROOT / "tests" / "perf" / "out"
_QUERIES_PATH = _REPO_ROOT / "tests" / "perf" / "data" / "semble_queries.jsonl"
_VENDORED_SNAPSHOT = _REPO_ROOT / "tests" / "fixtures" / "external" / "flask-snapshot"

# Vendored corpus pin (mirrors ``build_snapshots.py``).  Kept here so the
# bench JSON can record it without re-importing the script.
_VENDORED_UPSTREAM_REPO = "https://github.com/pallets/flask"
_VENDORED_UPSTREAM_COMMIT = "954f5684e4841aad84a8eec7ace7b81a0d3f6831"
_VENDORED_LICENSE = "BSD-3-Clause"

# Suffix filters per corpus.  Aligned with ``build_snapshots.py``'s
# ``KEEP_SUFFIXES`` so the bench indexes exactly what the snapshot ships.
_BENCH_SUFFIXES = frozenset(
    {
        ".py",
        ".rst",
        ".md",
        ".toml",
        ".txt",
        ".cfg",
        ".ini",
        ".yaml",
        ".yml",
        ".json",
        ".html",
        ".in",
        ".sql",
        ".j2",
    }
)

# corpus-forge bench surface — mirrors test_semble_bench.py's selection.
_CURRENT_CORPUS_ROOTS: list[Path] = [_REPO_ROOT / "corpus_forge"]
_CURRENT_EXTRA_FILES: list[Path] = [
    _REPO_ROOT / "config.example.toml",
    _REPO_ROOT / "README.md",
]
_CURRENT_SKIP_REL_PREFIXES = ("schema/",)


# ── utilities ───────────────────────────────────────────────────────────


def _load_queries() -> list[dict[str, Any]]:
    with _QUERIES_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def _classify_corpus(entry: dict[str, Any]) -> str:
    """Return the corpus this entry targets.  Defaults to ``current``."""
    return str(entry.get("corpus") or "current")


def _iter_current_corpus_files() -> Iterator[Path]:
    """Yield bench-corpus files for the corpus-forge half."""
    for root in _CURRENT_CORPUS_ROOTS:
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if any(rel.startswith(prefix) for prefix in _CURRENT_SKIP_REL_PREFIXES):
                continue
            if p.suffix.lower() not in _BENCH_SUFFIXES:
                continue
            yield p
    for p in _CURRENT_EXTRA_FILES:
        if p.is_file():
            yield p


def _iter_vendored_corpus_files() -> Iterator[Path]:
    """Yield bench-corpus files for the vendored snapshot half."""
    if not _VENDORED_SNAPSHOT.is_dir():
        return
    for p in sorted(_VENDORED_SNAPSHOT.rglob("*")):
        if not p.is_file():
            continue
        # LICENSE.txt is the only no-suffix file we keep so the BSD pointer
        # travels with the corpus; let the filter through.
        if p.name == "LICENSE.txt":
            yield p
            continue
        if p.suffix.lower() not in _BENCH_SUFFIXES:
            continue
        yield p


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


# ── HybridRetriever construction ────────────────────────────────────────


def _build_hybrid_retriever(
    files: list[Path],
    *,
    file_to_rel: dict[Path, str],
) -> tuple[Any, dict[int, dict[str, Any]]]:
    """Build a HybridRetriever over ``files``.

    Mirrors ``test_semble_bench._build_hybrid_retriever`` but parameterised
    over the corpus file list so the bench can build one retriever per
    corpus.  ``file_to_rel`` maps each absolute path to the relative
    path that will appear in the ``chunk_index`` (and that ground-truth
    entries reference).

    Returns:
        ``(retriever, chunk_index)``.  ``chunk_index`` maps backend
        ``chunk_id`` to ``{"file_path", "byte_start", "byte_end"}``.
    """
    import numpy as np

    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.chunkers.base import MarkdownChunker, TextChunk
    from corpus_forge.embedders.sentence_transformers import (
        SentenceTransformersEmbedder,
    )
    from corpus_forge.retrieval.rerank.cross_encoder import CrossEncoderReranker
    from corpus_forge.retrieval.retriever import HybridRetriever
    from corpus_forge.sources.base import RawDocument

    backend = SQLiteBackend(path=":memory:")
    backend.migrate()
    dataset_id = backend.get_or_create_dataset(
        name="phase_n_bench", kind="default", description="Phase N Wave 0 bench"
    )

    embedder = SentenceTransformersEmbedder(
        name="all-MiniLM-L6-v2",
        model_id="sentence-transformers/all-MiniLM-L6-v2",
        dimension=384,
        normalized=True,
        distance="cosine",
        device="cpu",
        batch_size=64,
    )
    embedder_id = backend.register_embedder(embedder)

    chunker = MarkdownChunker(max_chars=1500, overlap=200)
    chunk_index: dict[int, dict[str, Any]] = {}

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        raw = path.read_bytes()
        rel = file_to_rel[path]

        chunks: list[TextChunk] = chunker.chunk(text)
        if not chunks:
            continue

        cursor = 0
        chunk_byte_spans: list[tuple[int, int]] = []
        for c in chunks:
            needle = c.text.encode("utf-8")
            idx = raw.find(needle, cursor)
            if idx == -1:
                idx = raw.find(needle)
                if idx == -1:
                    idx = cursor
            chunk_byte_spans.append((idx, idx + len(needle)))
            cursor = idx + max(len(needle) - chunker.overlap, 1)

        doc = RawDocument(
            source_uri=f"file://{rel}",
            content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            text=text,
            title=rel,
            modified_at=path.stat().st_mtime,
            metadata={"path": rel},
            labels=[],
        )
        doc_id = backend.upsert_document(
            dataset_id=dataset_id,
            doc=doc,
            chunks=chunks,
            embedder_ids=[embedder_id],
        )

        rows = backend._execute(  # type: ignore[attr-defined]
            "SELECT id, text FROM chunks WHERE document_id = ? ORDER BY id ASC",
            (doc_id,),
        )
        for row, (bs, be) in zip(rows, chunk_byte_spans, strict=False):
            chunk_index[int(row["id"])] = {
                "file_path": rel,
                "byte_start": int(bs),
                "byte_end": int(be),
            }

        chunk_ids = [int(r["id"]) for r in rows]
        chunk_texts = [r["text"] for r in rows]
        if chunk_texts:
            vecs = embedder.encode(chunk_texts)
            vecs_arr = np.asarray(vecs, dtype=np.float32)
            pairs = [(cid, vecs_arr[i]) for i, cid in enumerate(chunk_ids)]
            backend.write_embeddings(embedder_id, pairs)

    reranker: CrossEncoderReranker | None
    try:
        reranker = CrossEncoderReranker(device="cpu", batch_size=16)
        reranker._get_model()  # type: ignore[attr-defined]  # bench-only access
    except Exception as exc:
        print(
            f"[bench] CrossEncoderReranker load failed ({exc!r}); "
            "running HybridRetriever without rerank."
        )
        reranker = None

    retriever = HybridRetriever(
        backend=backend,
        embedder=embedder,
        embedder_id=embedder_id,
        reranker=reranker,
    )
    return retriever, chunk_index


def _normalise_hybrid_hits(hits: list[Any], chunk_index: dict[int, dict[str, Any]]) -> list[Any]:
    """Attach file_path/byte_start/byte_end to HybridRetriever hits via the
    chunk_index join built at index time."""
    from types import SimpleNamespace

    out: list[Any] = []
    for h in hits:
        info = chunk_index.get(int(h.chunk_id))
        if info is None:
            meta = dict(getattr(h, "metadata", {}) or {})
        else:
            meta = {**(getattr(h, "metadata", {}) or {}), **info}
        out.append(
            SimpleNamespace(
                chunk_id=h.chunk_id,
                score=h.score,
                text=h.text,
                metadata=meta,
            )
        )
    return out


# ── one-corpus bench pass ───────────────────────────────────────────────


def _run_corpus_pass(
    corpus_name: str,
    queries: list[dict[str, Any]],
    files: list[Path],
    file_to_rel: dict[Path, str],
) -> tuple[dict[str, Any], float, dict[str, Any] | None]:
    """Build a HybridRetriever for ``corpus_name`` and run all ``queries``.

    Returns ``(scored_block, build_seconds, reranker_info)``.
    ``scored_block`` is the per-corpus dict that lands under
    ``by_corpus[corpus_name]`` in the JSON dump.
    """
    from corpus_forge.retrieval.types import SearchOptions
    from tests.perf.metrics import compute_metrics

    t0 = time.perf_counter()
    retriever, chunk_index = _build_hybrid_retriever(files, file_to_rel=file_to_rel)
    build_s = time.perf_counter() - t0

    runs: dict[str, dict[str, Any]] = {}
    for i, q in enumerate(queries):
        qid = f"{corpus_name}_q{i + 1:03d}"
        opts = SearchOptions(
            k=10,
            rerank=retriever.reranker is not None,
            rerank_top_n=50,
        )
        t_q = time.perf_counter()
        hits = retriever.search(q["query"], opts)
        latency_ms = (time.perf_counter() - t_q) * 1000.0
        runs[qid] = {
            "hits": _normalise_hybrid_hits(hits, chunk_index),
            "latency_ms": latency_ms,
        }

    # Score
    ground_truth: dict[str, list[dict[str, Any]]] = {}
    by_cat: dict[str, list[str]] = {}
    for i, q in enumerate(queries):
        qid = f"{corpus_name}_q{i + 1:03d}"
        ground_truth[qid] = q["ground_truth_chunks"]
        by_cat.setdefault(q["category"], []).append(qid)

    overall = compute_metrics(runs, ground_truth)

    per_cat: dict[str, dict[str, Any]] = {}
    for cat, qids in by_cat.items():
        sub_gt = {qid: ground_truth[qid] for qid in qids}
        sub_runs = {qid: runs[qid] for qid in qids if qid in runs}
        per_cat[cat] = _strip_per_query(compute_metrics(sub_runs, sub_gt))

    reranker_info: dict[str, Any] | None
    if retriever.reranker is not None:
        reranker_info = {
            "model_id": getattr(retriever.reranker, "model_id", None),
        }
    else:
        reranker_info = None

    scored = {
        "mrr_at_10": overall["mrr_at_10"],
        "recall_at_5": overall["recall_at_5"],
        "p50_latency_ms": overall["p50_latency_ms"],
        "p95_latency_ms": overall["p95_latency_ms"],
        "n_queries": overall["n_queries"],
        "by_category": per_cat,
        "per_query": overall["per_query"],
    }
    return scored, build_s, reranker_info


# ── corpus metadata ─────────────────────────────────────────────────────


def _summarise_files(files: list[Path]) -> tuple[int, int]:
    import contextlib

    n_bytes = 0
    for p in files:
        with contextlib.suppress(OSError):
            n_bytes += p.stat().st_size
    return len(files), n_bytes


def _build_corpus_metadata(
    current_files: list[Path],
    vendored_files: list[Path],
) -> dict[str, Any]:
    cn_files, cn_bytes = _summarise_files(current_files)
    vn_files, vn_bytes = _summarise_files(vendored_files)
    return {
        "current": {
            "source": "corpus-forge",
            "git_head": _git_head(),
            "n_files": cn_files,
            "n_bytes": cn_bytes,
            "roots": ["corpus_forge/", "config.example.toml", "README.md"],
            "skip_prefixes": list(_CURRENT_SKIP_REL_PREFIXES),
        },
        "vendored": {
            "source": "pallets/flask",
            "upstream_repo": _VENDORED_UPSTREAM_REPO,
            "upstream_commit": _VENDORED_UPSTREAM_COMMIT,
            "license": _VENDORED_LICENSE,
            "snapshot_path": "tests/fixtures/external/flask-snapshot",
            "n_files": vn_files,
            "n_bytes": vn_bytes,
        },
    }


# ── aggregated metrics ──────────────────────────────────────────────────


def _aggregate(
    per_corpus_blocks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compute aggregated headline metrics across both corpora.

    MRR / Recall are averaged across all per-query records (weighted by
    count, which is just the unweighted mean since each query contributes
    one record).  Latency percentiles are recomputed over the union of
    latencies, not averaged across corpora — percentiles don't average.
    """
    from tests.perf.metrics import percentile

    all_per_query: list[dict[str, Any]] = []
    latencies: list[float] = []
    for block in per_corpus_blocks.values():
        all_per_query.extend(block.get("per_query", []))
        latencies.extend(float(r["latency_ms"]) for r in block.get("per_query", []))

    if not all_per_query:
        return {
            "mrr_at_10": 0.0,
            "recall_at_5": 0.0,
            "p50_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "n_queries": 0,
            "by_category": {},
        }

    # Mean MRR / Recall over queries with non-zero ground truth.
    scored = [r for r in all_per_query if r.get("n_ground_truth", 0) > 0]
    n = len(scored) if scored else 1
    mean_mrr = sum(r.get("mrr_at_10", 0.0) for r in scored) / n if scored else 0.0
    mean_rec = sum(r.get("recall_at_5", 0.0) for r in scored) / n if scored else 0.0

    # By-category aggregation across both corpora.
    cat_records: dict[str, list[dict[str, Any]]] = {}
    # Map qid prefix -> corpus; pull category from the original queries
    # file would require a re-load.  Instead, the per_corpus_blocks carry
    # by_category — we need to recompute over the union.  Cheapest: re-
    # derive by walking each block's by_category and weighting by n_queries.
    for block in per_corpus_blocks.values():
        for cat, cat_block in block.get("by_category", {}).items():
            cat_records.setdefault(cat, []).append(cat_block)

    by_cat_agg: dict[str, dict[str, Any]] = {}
    for cat, records in cat_records.items():
        total_n = sum(r.get("n_queries", 0) for r in records)
        if total_n == 0:
            by_cat_agg[cat] = {
                "mrr_at_10": 0.0,
                "recall_at_5": 0.0,
                "p50_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "n_queries": 0,
            }
            continue
        # Weighted mean by n_queries
        mrr = sum(r.get("mrr_at_10", 0.0) * r.get("n_queries", 0) for r in records)
        rec = sum(r.get("recall_at_5", 0.0) * r.get("n_queries", 0) for r in records)
        by_cat_agg[cat] = {
            "mrr_at_10": mrr / total_n,
            "recall_at_5": rec / total_n,
            # Latency percentiles can't be combined arithmetically.  We
            # report the max-of-percentiles as a conservative bound; the
            # per-corpus blocks carry the real distributions.
            "p50_latency_ms": max(r.get("p50_latency_ms", 0.0) for r in records),
            "p95_latency_ms": max(r.get("p95_latency_ms", 0.0) for r in records),
            "n_queries": total_n,
        }

    return {
        "mrr_at_10": mean_mrr,
        "recall_at_5": mean_rec,
        "p50_latency_ms": percentile(latencies, 50.0),
        "p95_latency_ms": percentile(latencies, 95.0),
        "n_queries": len(all_per_query),
        "by_category": by_cat_agg,
    }


# ── the bench itself ────────────────────────────────────────────────────


@pytest.mark.timeout(1800)  # 30 min ceiling; bench is CPU-bound rerank on M-series.
def test_phase_n_bench(tmp_path: Path) -> None:
    """Run HybridRetriever over both corpora and dump JSON.

    Test passes iff:
    - Both corpora load (vendored snapshot fixture exists).
    - HybridRetriever builds without error on each corpus.
    - Every query runs to completion.
    - The JSON dump in ``tests/perf/out/`` has the pinned shape.
    """
    selection = os.environ.get("CF_PHASE_N_CORPUS", "all").strip().lower()
    if selection not in {"current", "vendored", "all"}:
        pytest.fail(f"CF_PHASE_N_CORPUS={selection!r} not in {{current,vendored,all}}")

    if not _VENDORED_SNAPSHOT.is_dir() and selection in {"all", "vendored"}:
        pytest.fail(
            f"vendored snapshot missing: {_VENDORED_SNAPSHOT}.  Run "
            "`uv run python tests/fixtures/external/build_snapshots.py` first."
        )

    queries = _load_queries()
    print(f"\n[bench] loaded {len(queries)} queries", flush=True)

    # Split queries by corpus.
    queries_by_corpus: dict[str, list[dict[str, Any]]] = {"current": [], "vendored": []}
    for q in queries:
        queries_by_corpus[_classify_corpus(q)].append(q)

    distribution = Counter(_classify_corpus(q) for q in queries)
    print(f"[bench] per-corpus split: {dict(distribution)}", flush=True)

    # Resolve file lists per corpus.
    current_files = list(_iter_current_corpus_files())
    vendored_files = list(_iter_vendored_corpus_files())

    # Build rel-path maps so chunk_index keys match the ground-truth file paths.
    current_rel = {p: (p.relative_to(_REPO_ROOT).as_posix()) for p in current_files}
    vendored_rel = {p: (p.relative_to(_REPO_ROOT).as_posix()) for p in vendored_files}

    # Run per-corpus passes.
    per_corpus_blocks: dict[str, dict[str, Any]] = {}
    per_corpus_build_s: dict[str, float] = {}
    reranker_info: dict[str, Any] | None = None

    if selection in {"current", "all"}:
        block, build_s, rr = _run_corpus_pass(
            "current",
            queries_by_corpus["current"],
            current_files,
            current_rel,
        )
        per_corpus_blocks["current"] = block
        per_corpus_build_s["current"] = build_s
        reranker_info = reranker_info or rr

    if selection in {"vendored", "all"}:
        block, build_s, rr = _run_corpus_pass(
            "vendored",
            queries_by_corpus["vendored"],
            vendored_files,
            vendored_rel,
        )
        per_corpus_blocks["vendored"] = block
        per_corpus_build_s["vendored"] = build_s
        reranker_info = reranker_info or rr

    aggregated = _aggregate(per_corpus_blocks)
    corpus_metadata = _build_corpus_metadata(current_files, vendored_files)

    # ── persist ────────────────────────────────────────────────────
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    iso = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = _OUT_DIR / f"phase_n_baseline_{iso}.json"

    payload: dict[str, Any] = {
        "schema_version": 1,
        "phase": "N",
        "wave": 0,
        "kind": "phase_n_baseline",
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
        # Use the repo's basename so bench artifacts are portable across machines.
        "repo_root": _REPO_ROOT.name,
        "git_head": _git_head(),
        "n_queries": len(queries),
        "platform": {
            "python": sys.version.split()[0],
            "system": sys.platform,
        },
        "build": {f"{c}_seconds": round(s, 3) for c, s in per_corpus_build_s.items()},
        "hybrid_config": {
            "embedder_model_id": "sentence-transformers/all-MiniLM-L6-v2",
            "dimension": 384,
            "fusion": "rrf",
            "rerank_enabled": reranker_info is not None,
            "reranker_model_id": (reranker_info.get("model_id") if reranker_info else None),
        },
        "corpus_selection": selection,
        "corpus_metadata": corpus_metadata,
        "by_corpus": per_corpus_blocks,
        "aggregated": aggregated,
    }

    out_path.write_text(json.dumps(payload, indent=2, default=_json_default))

    # Final sanity assertions
    assert out_path.is_file()
    re_read = json.loads(out_path.read_text())
    assert re_read["phase"] == "N"
    assert re_read["wave"] == 0
    if selection == "all":
        assert set(re_read["by_corpus"].keys()) == {"current", "vendored"}
        assert re_read["by_corpus"]["current"]["n_queries"] + re_read["by_corpus"]["vendored"][
            "n_queries"
        ] == len(queries)

    print(f"\n[bench] wrote: {out_path}", flush=True)
    for corpus, block in re_read["by_corpus"].items():
        print(
            f"[bench] {corpus:<8} MRR@10={block['mrr_at_10']:.3f}  "
            f"Recall@5={block['recall_at_5']:.3f}  "
            f"p50={block['p50_latency_ms']:.1f}ms  "
            f"p95={block['p95_latency_ms']:.1f}ms  "
            f"n={block['n_queries']}",
            flush=True,
        )
    agg = re_read["aggregated"]
    print(
        f"[bench] AGG      MRR@10={agg['mrr_at_10']:.3f}  "
        f"Recall@5={agg['recall_at_5']:.3f}  "
        f"p50={agg['p50_latency_ms']:.1f}ms  "
        f"p95={agg['p95_latency_ms']:.1f}ms  "
        f"n={agg['n_queries']}",
        flush=True,
    )


def _strip_per_query(metrics: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in metrics.items() if k != "per_query"}


def _json_default(obj: object) -> object:
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"non-serializable: {type(obj)!r}")
