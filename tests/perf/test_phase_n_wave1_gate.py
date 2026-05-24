"""Phase N Wave 1 — wave gate against the Wave 0 baseline.

Gated by ``CF_PHASE_N_GATE=1`` so the heavy bench never runs in default
CI.  Re-uses the Wave 0 bench harness internals (``_iter_*_corpus_files``,
``_classify_corpus``, ``_normalise_hybrid_hits``, ``_aggregate``) but
swaps in a paired-retriever experiment for the rigorous comparison the
wave gate needs.

Why two retrievers?  The Wave 0 baseline JSON was captured under
``fusion="rrf"`` (the production default in ``SearchOptions``).  Wave 1's
bump only fires on the ``fusion="alpha"`` path — RRF is rank-only, no
alpha to bump.  So a head-vs-baseline diff would conflate two
independent changes (RRF → alpha fusion AND no-bump → bump).

The gate instead builds the corpus index ONCE per corpus, then runs
the same query set through TWO ``HybridRetriever`` instances sharing
that backend:

- **control**: ``fusion="alpha"``, ``adaptive_lexical_weight=False``.
- **treatment**: ``fusion="alpha"``, ``adaptive_lexical_weight=True``,
  ``symbol_query_alpha=0.3``.

Both use the same reranker (the production cross-encoder) so the only
delta between them is the Wave 1 bump.  Per-category Pareto rules then
read against the *control* — apples-to-apples.  The Wave 0 RRF baseline
is still loaded and reported as a cross-fusion reference in the JSON
dump, but the gate's pass/fail decision rides on treatment-vs-control.

Pareto rule (treatment vs control):

- Identifier MRR@10 / Recall@5  →  ``>= control - 0.0``  (no regression)
- Callsite   MRR@10              →  ``>= control - 0.0``  (no regression)
- Concept    MRR@10 / Recall@5  →  ``>= control - 0.05`` (protect)
- Error      MRR@10 / Recall@5  →  ``>= control - 0.05`` (protect)
- Config     MRR@10              →  ``>= control - 0.05`` (protect)

Thresholds are intentionally permissive on the targeted categories.
Once the heuristic settles, the identifier floor will ratchet up
toward the Wave 5 spec's "+0.10" target.  Protected categories are
never loosened.

The wave-1 JSON result lands at
``tests/perf/out/phase_n_wave1_<ISO>.json`` for audit.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.embedders.base import Embedder
    from corpus_forge.retrieval.rerank.cross_encoder import CrossEncoderReranker
    from corpus_forge.retrieval.retriever import HybridRetriever
    from corpus_forge.retrieval.types import SearchResponse


class _Harness(TypedDict):
    """Lazy-imported references to the Wave 0 bench harness internals."""

    iter_current: Callable[[], Iterator[Path]]
    iter_vendored: Callable[[], Iterator[Path]]
    classify: Callable[[dict[str, object]], str]
    normalise_hits: Callable[[SearchResponse, dict[int, dict[str, object]]], list[object]]
    aggregate: Callable[[dict[str, dict[str, object]]], dict[str, object]]
    strip_per_query: Callable[[dict[str, object]], dict[str, object]]
    json_default: Callable[[object], object]
    vendored_snapshot: Path


pytestmark = pytest.mark.skipif(
    os.environ.get("CF_PHASE_N_GATE") != "1",
    reason="set CF_PHASE_N_GATE=1 to run the Phase N Wave 1 gate.",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUT_DIR = _REPO_ROOT / "tests" / "perf" / "out"
_BASELINE_PATH = _OUT_DIR / "phase_n_baseline.json"
_QUERIES_PATH = _REPO_ROOT / "tests" / "perf" / "data" / "semble_queries.jsonl"

# Pareto floor (positive number = max allowed drop relative to control).
# Identifier / callsite are the TARGETED categories.  Concept / error /
# config are the categories the bump must protect.
_TARGET_DROP = 0.0
_PROTECT_DROP = 0.05


# ── shared harness imports (lazy so the unit suite never pulls these) ──────


def _import_bench_harness() -> _Harness:
    """Import the Wave 0 bench harness's internals lazily.

    Done lazily so the unit suite (which never sets ``CF_PHASE_N_GATE``)
    never imports ``sentence_transformers`` / the cross-encoder reranker.
    """
    from tests.perf import test_phase_n_bench as bench

    return _Harness(
        iter_current=bench._iter_current_corpus_files,
        iter_vendored=bench._iter_vendored_corpus_files,
        classify=bench._classify_corpus,
        normalise_hits=bench._normalise_hybrid_hits,
        aggregate=bench._aggregate,
        strip_per_query=bench._strip_per_query,
        json_default=bench._json_default,
        vendored_snapshot=bench._VENDORED_SNAPSHOT,
    )


# ── one-shot index build (shared by control + treatment) ───────────────────


def _build_indexed_backend(
    files: list[Path],
    *,
    file_to_rel: dict[Path, str],
    corpus_name: str,
) -> tuple[
    SQLiteBackend,
    Embedder,
    int,
    CrossEncoderReranker | None,
    dict[int, dict[str, object]],
]:
    """Build the SQLite backend + embedder + reranker once for ``files``.

    Returns ``(backend, embedder, embedder_id, reranker, chunk_index)``.
    Index is shared between the control retriever (no Wave 1 bump) and
    the treatment retriever (bump on) — so the only difference between
    the two passes is the retriever's ``RetrievalConfig``.
    """
    import hashlib

    import numpy as np

    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.chunkers.base import MarkdownChunker, TextChunk
    from corpus_forge.embedders.sentence_transformers import (
        SentenceTransformersEmbedder,
    )
    from corpus_forge.retrieval.rerank.cross_encoder import CrossEncoderReranker
    from corpus_forge.sources.base import RawDocument

    backend = SQLiteBackend(path=":memory:")
    backend.migrate()
    dataset_id = backend.get_or_create_dataset(
        name=f"phase_n_wave1_gate_{corpus_name}",
        kind="default",
        description=f"Phase N Wave 1 gate ({corpus_name})",
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
    chunk_index: dict[int, dict[str, object]] = {}

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
        reranker._get_model()  # type: ignore[attr-defined]
    except Exception as exc:
        print(
            f"[wave1-gate] CrossEncoderReranker load failed ({exc!r}); "
            "running HybridRetriever without rerank."
        )
        reranker = None

    return backend, embedder, embedder_id, reranker, chunk_index


def _build_retriever(
    *,
    backend: SQLiteBackend,
    embedder: Embedder,
    embedder_id: int,
    reranker: CrossEncoderReranker | None,
    adaptive: bool,
) -> HybridRetriever:
    """Construct a HybridRetriever sharing ``backend`` / ``embedder``.

    ``adaptive=False`` is the **control** (alpha fusion, no bump);
    ``adaptive=True`` is the **treatment** (alpha fusion + Wave 1 bump).
    """
    from corpus_forge.config import RetrievalConfig
    from corpus_forge.retrieval.retriever import HybridRetriever

    cfg = RetrievalConfig(
        fusion="alpha",
        alpha=0.7,
        adaptive_lexical_weight=adaptive,
        symbol_query_alpha=0.3,
    )
    return HybridRetriever(
        backend=backend,
        embedder=embedder,
        embedder_id=embedder_id,
        reranker=reranker,
        config=cfg,
    )


# ── bench pass per arm ─────────────────────────────────────────────────────


def _run_arm(
    arm_name: str,
    corpus_name: str,
    queries: list[dict[str, object]],
    *,
    retriever: HybridRetriever,
    chunk_index: dict[int, dict[str, object]],
    harness: _Harness,
) -> dict[str, object]:
    """Run ``queries`` through ``retriever`` and score per-category."""
    from corpus_forge.retrieval.types import SearchOptions
    from tests.perf.metrics import compute_metrics

    runs: dict[str, dict[str, object]] = {}
    for i, q in enumerate(queries):
        qid = f"{corpus_name}_q{i + 1:03d}"
        opts = SearchOptions(
            k=10,
            fusion="alpha",
            alpha=0.7,
            rerank=retriever.reranker is not None,
            rerank_top_n=50,
        )
        t_q = time.perf_counter()
        hits = retriever.search(str(q["query"]), opts)
        latency_ms = (time.perf_counter() - t_q) * 1000.0
        runs[qid] = {
            "hits": harness["normalise_hits"](hits, chunk_index),
            "latency_ms": latency_ms,
        }

    ground_truth: dict[str, list[dict[str, object]]] = {}
    by_cat: dict[str, list[str]] = {}
    for i, q in enumerate(queries):
        qid = f"{corpus_name}_q{i + 1:03d}"
        gt = q["ground_truth_chunks"]
        assert isinstance(gt, list)
        ground_truth[qid] = gt
        cat = q["category"]
        assert isinstance(cat, str)
        by_cat.setdefault(cat, []).append(qid)

    overall = compute_metrics(runs, ground_truth)

    per_cat: dict[str, dict[str, object]] = {}
    for cat, qids in by_cat.items():
        sub_gt = {qid: ground_truth[qid] for qid in qids}
        sub_runs = {qid: runs[qid] for qid in qids if qid in runs}
        per_cat[cat] = harness["strip_per_query"](compute_metrics(sub_runs, sub_gt))

    return {
        "arm": arm_name,
        "mrr_at_10": overall["mrr_at_10"],
        "recall_at_5": overall["recall_at_5"],
        "p50_latency_ms": overall["p50_latency_ms"],
        "p95_latency_ms": overall["p95_latency_ms"],
        "n_queries": overall["n_queries"],
        "by_category": per_cat,
        "per_query": overall["per_query"],
    }


# ── Pareto comparison ──────────────────────────────────────────────────────


def _category_metric(payload: dict[str, object], category: str, metric: str) -> float | None:
    """Pull ``aggregated.by_category[category][metric]``."""
    agg = payload.get("aggregated", {})
    if not isinstance(agg, dict):
        return None
    by_cat = agg.get("by_category", {})
    if not isinstance(by_cat, dict):
        return None
    block = by_cat.get(category)
    if not isinstance(block, dict):
        return None
    val = block.get(metric)
    if not isinstance(val, (int, float)):
        return None
    return float(val)


def _pareto_violation(
    head: dict[str, object],
    base: dict[str, object],
    category: str,
    metric: str,
    max_drop: float,
) -> str | None:
    """Return a violation message if head[cat][metric] < base[cat][metric] - max_drop."""
    h = _category_metric(head, category, metric)
    b = _category_metric(base, category, metric)
    if h is None or b is None:
        return None
    drop = b - h
    if drop > max_drop + 1e-9:
        return (
            f"{category}.{metric}: treatment={h:.4f}  control={b:.4f}  "
            f"drop={drop:+.4f}  > allowed {max_drop:.4f}"
        )
    return None


# ── the gate ───────────────────────────────────────────────────────────────


@pytest.mark.timeout(1800)
def test_phase_n_wave1_gate() -> None:
    """Build paired retrievers; assert Pareto floor of treatment vs control."""

    if not _BASELINE_PATH.is_file():
        pytest.fail(f"baseline missing: {_BASELINE_PATH}.  Run Wave 0 bench first.")
    rrf_baseline = json.loads(_BASELINE_PATH.read_text())

    harness = _import_bench_harness()
    if not harness["vendored_snapshot"].is_dir():
        pytest.fail(
            f"vendored snapshot missing: {harness['vendored_snapshot']}.  "
            "Run `uv run python tests/fixtures/external/build_snapshots.py` first."
        )

    # Load queries; split by corpus.
    with _QUERIES_PATH.open() as f:
        queries: list[dict[str, object]] = [json.loads(line) for line in f if line.strip()]
    queries_by_corpus: dict[str, list[dict[str, object]]] = {"current": [], "vendored": []}
    for q in queries:
        queries_by_corpus[harness["classify"](q)].append(q)

    current_files = list(harness["iter_current"]())
    vendored_files = list(harness["iter_vendored"]())
    current_rel = {p: (p.relative_to(_REPO_ROOT).as_posix()) for p in current_files}
    vendored_rel = {p: (p.relative_to(_REPO_ROOT).as_posix()) for p in vendored_files}

    # Run paired retriever experiment per corpus.  Index is built ONCE
    # per corpus and shared between control + treatment.
    control_by_corpus: dict[str, dict[str, object]] = {}
    treatment_by_corpus: dict[str, dict[str, object]] = {}
    build_seconds: dict[str, float] = {}
    reranker_info: dict[str, object] | None = None

    for corpus_name, files, rel_map in (
        ("current", current_files, current_rel),
        ("vendored", vendored_files, vendored_rel),
    ):
        t0 = time.perf_counter()
        backend, embedder, eid, reranker, chunk_index = _build_indexed_backend(
            files, file_to_rel=rel_map, corpus_name=corpus_name
        )
        build_seconds[corpus_name] = time.perf_counter() - t0

        control_retriever = _build_retriever(
            backend=backend,
            embedder=embedder,
            embedder_id=eid,
            reranker=reranker,
            adaptive=False,
        )
        treatment_retriever = _build_retriever(
            backend=backend,
            embedder=embedder,
            embedder_id=eid,
            reranker=reranker,
            adaptive=True,
        )

        control_by_corpus[corpus_name] = _run_arm(
            "control",
            corpus_name,
            queries_by_corpus[corpus_name],
            retriever=control_retriever,
            chunk_index=chunk_index,
            harness=harness,
        )
        treatment_by_corpus[corpus_name] = _run_arm(
            "treatment",
            corpus_name,
            queries_by_corpus[corpus_name],
            retriever=treatment_retriever,
            chunk_index=chunk_index,
            harness=harness,
        )
        if reranker is not None and reranker_info is None:
            reranker_info = {"model_id": getattr(reranker, "model_id", None)}

    control_agg = harness["aggregate"](control_by_corpus)
    treatment_agg = harness["aggregate"](treatment_by_corpus)

    # ── persist for audit ──────────────────────────────────────────────
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    iso = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = _OUT_DIR / f"phase_n_wave1_{iso}.json"

    control_payload: dict[str, object] = {
        "by_corpus": control_by_corpus,
        "aggregated": control_agg,
    }
    treatment_payload: dict[str, object] = {
        "by_corpus": treatment_by_corpus,
        "aggregated": treatment_agg,
    }

    payload: dict[str, object] = {
        "schema_version": 1,
        "phase": "N",
        "wave": 1,
        "kind": "phase_n_wave1",
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "repo_root": _REPO_ROOT.name,
        "n_queries": len(queries),
        "platform": {"python": sys.version.split()[0], "system": sys.platform},
        "build": {f"{c}_seconds": round(s, 3) for c, s in build_seconds.items()},
        "hybrid_config": {
            "embedder_model_id": "sentence-transformers/all-MiniLM-L6-v2",
            "dimension": 384,
            "fusion": "alpha",
            "alpha": 0.7,
            "symbol_query_alpha": 0.3,
            "rerank_enabled": reranker_info is not None,
            "reranker_model_id": (reranker_info.get("model_id") if reranker_info else None),
        },
        # Two arms: control = bump off, treatment = bump on.  Gate's
        # Pareto rule compares treatment vs control (apples-to-apples
        # under fusion="alpha").
        "control": control_payload,
        "treatment": treatment_payload,
        # Reference numbers for cross-fusion context.  NOT the gate
        # criterion — the gate rides on treatment vs control.
        "rrf_baseline_reference": {
            "by_corpus": rrf_baseline.get("by_corpus", {}),
            "aggregated": rrf_baseline.get("aggregated", {}),
        },
    }
    out_path.write_text(json.dumps(payload, indent=2, default=harness["json_default"]))
    print(f"\n[wave1-gate] wrote: {out_path}", flush=True)

    # ── Pareto rule: treatment vs control ──────────────────────────────
    violations: list[str] = []

    # Targeted categories — no regression (max drop 0.0).
    for cat in ("identifier", "callsite"):
        v = _pareto_violation(treatment_payload, control_payload, cat, "mrr_at_10", _TARGET_DROP)
        if v is not None:
            violations.append(v)

    # Identifier Recall@5 — no regression.
    v = _pareto_violation(
        treatment_payload, control_payload, "identifier", "recall_at_5", _TARGET_DROP
    )
    if v is not None:
        violations.append(v)

    # Protected categories — Pareto floor 0.05.
    for cat in ("concept", "error", "config"):
        v = _pareto_violation(treatment_payload, control_payload, cat, "mrr_at_10", _PROTECT_DROP)
        if v is not None:
            violations.append(v)

    # Recall@5 protection on concept / error.
    for cat in ("concept", "error"):
        v = _pareto_violation(treatment_payload, control_payload, cat, "recall_at_5", _PROTECT_DROP)
        if v is not None:
            violations.append(v)

    # Audit table — print treatment vs control AND treatment vs RRF baseline.
    print("\n[wave1-gate] per-category aggregated MRR@10:", flush=True)
    print(
        f"  {'category':<11} {'control':>9} {'treatment':>11} "
        f"{'Δvs_ctrl':>10} {'rrf_base':>10} {'Δvs_rrf':>10}",
        flush=True,
    )
    for cat in ("identifier", "callsite", "concept", "error", "config"):
        c = _category_metric(control_payload, cat, "mrr_at_10")
        t = _category_metric(treatment_payload, cat, "mrr_at_10")
        rrf = _category_metric(rrf_baseline, cat, "mrr_at_10")
        if c is None or t is None:
            continue
        rrf_s = f"{rrf:.4f}" if rrf is not None else "    -"
        rrf_d = f"{t - rrf:+.4f}" if rrf is not None else "      -"
        print(
            f"  {cat:<11} {c:>9.4f} {t:>11.4f} {t - c:>+10.4f} {rrf_s:>10} {rrf_d:>10}",
            flush=True,
        )

    print("\n[wave1-gate] per-category aggregated Recall@5:", flush=True)
    print(
        f"  {'category':<11} {'control':>9} {'treatment':>11} "
        f"{'Δvs_ctrl':>10} {'rrf_base':>10} {'Δvs_rrf':>10}",
        flush=True,
    )
    for cat in ("identifier", "callsite", "concept", "error", "config"):
        c = _category_metric(control_payload, cat, "recall_at_5")
        t = _category_metric(treatment_payload, cat, "recall_at_5")
        rrf = _category_metric(rrf_baseline, cat, "recall_at_5")
        if c is None or t is None:
            continue
        rrf_s = f"{rrf:.4f}" if rrf is not None else "    -"
        rrf_d = f"{t - rrf:+.4f}" if rrf is not None else "      -"
        print(
            f"  {cat:<11} {c:>9.4f} {t:>11.4f} {t - c:>+10.4f} {rrf_s:>10} {rrf_d:>10}",
            flush=True,
        )

    if violations:
        pytest.fail(
            "Phase N Wave 1 Pareto rule (treatment vs control) failed:\n  "
            + "\n  ".join(violations)
        )
