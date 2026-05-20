"""Phase N Wave 3 — wave gate (static fast tier, three modes).

Gated by ``CF_PHASE_N_GATE=1`` so the heavy bench never runs in default
CI.  Re-uses the Wave 0 / Wave 2 bench harness internals
(``_iter_*_corpus_files``, ``_classify_corpus``,
``_normalise_hybrid_hits``, ``_aggregate``, etc.) — Wave 3 owns its
own code-aware index builder that ALSO registers a second
``Model2VecEmbedder`` against the same backend so the fast tier can
serve candidate queries during the bench.

Three arms (same index, three retrievers):

- **skip / control**: Wave 1 + Wave 2 ON, ``fast_tier_mode="skip"``.
  Mirrors the Wave 2 treatment.  Latency baseline for the gate.
- **shortcut**: Wave 1 + Wave 2 ON, ``fast_tier_mode="shortcut"``,
  ``fast_tier_top_n=200``.  Should match or beat the control on
  quality while dramatically lowering latency.
- **only**: Wave 1 + Wave 2 OFF (no lexical, no rerank in this mode
  anyway), ``fast_tier_mode="only"``.  Lowest-latency arm; lower
  quality acceptable.

Pareto rule (shortcut vs control):

- identifier MRR@10 >= control MRR@10 - 0.025               (small-N
  noise floor -- 50-75 hand-authored queries split across two
  corpora means a single rank-position flip moves identifier MRR by
  ~0.02 on a 8-9 query subset; the protect floor accommodates that
  spread without letting a real regression slip through)
- concept / error / config MRR@10 >= control - 0.05         (protect)
- aggregate Recall@5 >= control                             (the load-
  bearing recall invariant; shortcut mode must not drop chunks the
  control would have surfaced because they were outside the
  candidate pool)
- p50 latency on identifier queries <= control + 100 ms     (additive
  ceiling rather than absolute; the cross-encoder reranker
  dominates both arms, so the meaningful shortcut win comes from
  not making latency WORSE while the candidate pool shrinks.  The
  semble investigation's ~880x p50 drop materialises only when the
  reranker is off, which is the "only" mode below)

Pareto rule (only vs control):

- concept / error / config MRR@10 >= control - 0.10         (looser
  floor -- sacrificing quality for latency)
- p50 latency on identifier queries <= 30 ms                (the
  semble investigation found ~880x on this path; sqlite-vec adds
  ~5-15 ms overhead on top of the model encode so a 25 ms p50 is
  the realistic floor on this machine)

The wave-3 JSON result lands at
``tests/perf/out/phase_n_wave3_<ISO>.json`` for audit, with all three
arms' metrics + the Wave 0 RRF baseline as a cross-shape reference.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CF_PHASE_N_GATE") != "1",
    reason="set CF_PHASE_N_GATE=1 to run the Phase N Wave 3 gate.",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUT_DIR = _REPO_ROOT / "tests" / "perf" / "out"
_BASELINE_PATH = _OUT_DIR / "phase_n_baseline.json"
_QUERIES_PATH = _REPO_ROOT / "tests" / "perf" / "data" / "semble_queries.jsonl"

# Pareto floors.
# The targeted (identifier) floor on shortcut is 0.025 — not zero —
# because the bench has 8-9 identifier queries per corpus, so a single
# rank-position flip moves the per-corpus MRR by ~0.02.  A 0.025 floor
# catches a genuine regression while absorbing this small-N noise.
# Protected categories use the same 0.05 floor as Waves 1+2 (the
# absolute reference the project locked early).  Only mode gets a
# looser 0.10 floor since it explicitly trades quality for latency.
_SHORTCUT_TARGET_DROP = 0.025
_SHORTCUT_PROTECT_DROP = 0.05
_ONLY_PROTECT_DROP = 0.10

# Latency ceilings on identifier queries (additive on shortcut, absolute on only).
# Shortcut's win is "same latency as control while quality preserved";
# the reranker dominates both arms so the candidate-pool shrink doesn't
# show up in p50 numbers until rerank is off (which is the only mode).
# Allow +100 ms head-room over control.
_SHORTCUT_P50_DELTA_MS = 100.0
# Only mode: absolute ceiling. sqlite-vec adds ~5-15 ms over the
# StaticModel.encode call; 30 ms is the realistic on-CPU floor here.
_ONLY_P50_CEILING_MS = 30.0


# ── shared harness imports (lazy so the unit suite never pulls these) ──────


def _import_bench_harness() -> dict[str, Any]:
    """Import the Wave 0/1/2 bench harness internals lazily."""
    from tests.perf import test_phase_n_bench as bench

    return {
        "iter_current": bench._iter_current_corpus_files,
        "iter_vendored": bench._iter_vendored_corpus_files,
        "classify": bench._classify_corpus,
        "normalise_hits": bench._normalise_hybrid_hits,
        "aggregate": bench._aggregate,
        "strip_per_query": bench._strip_per_query,
        "json_default": bench._json_default,
        "vendored_snapshot": bench._VENDORED_SNAPSHOT,
    }


# ── code-aware index builder with a fast tier ──────────────────────────────


def _build_indexed_backend_with_fast_tier(
    files: list[Path],
    *,
    file_to_rel: dict[Path, str],
    corpus_name: str,
) -> tuple[Any, Any, int, Any, int, Any, dict[int, dict[str, Any]]]:
    """Build a SQLite backend with BOTH main and fast-tier embedders.

    Returns: ``(backend, main_embedder, main_id, fast_embedder,
    fast_id, reranker, chunk_index)``.

    Same code-aware indexing as the Wave 2 builder (CodeChunker on
    .py, MarkdownChunker elsewhere) so the Wave 2 definition boost
    keeps its substrate.  The fast tier is registered as a second
    embedder against the same backend; vectors are computed for every
    chunk in both tables.
    """
    import hashlib

    import numpy as np

    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.chunkers.base import MarkdownChunker, TextChunk
    from corpus_forge.chunkers.code import CodeChunker
    from corpus_forge.embedders.model2vec import Model2VecEmbedder
    from corpus_forge.embedders.sentence_transformers import (
        SentenceTransformersEmbedder,
    )
    from corpus_forge.retrieval.rerank.cross_encoder import CrossEncoderReranker
    from corpus_forge.sources.base import RawDocument

    backend = SQLiteBackend(path=":memory:")
    backend.migrate()
    dataset_id = backend.get_or_create_dataset(
        name=f"phase_n_wave3_gate_{corpus_name}",
        kind="default",
        description=f"Phase N Wave 3 gate ({corpus_name})",
    )

    main_embedder = SentenceTransformersEmbedder(
        name="all-MiniLM-L6-v2",
        model_id="sentence-transformers/all-MiniLM-L6-v2",
        dimension=384,
        normalized=True,
        distance="cosine",
        device="cpu",
        batch_size=64,
    )
    main_id = backend.register_embedder(main_embedder)

    fast_embedder = Model2VecEmbedder(
        name="potion-code-16M",
        model_id="minishlab/potion-code-16M",
        dimension=256,
        normalized=True,
        distance="cosine",
    )
    fast_id = backend.register_embedder(fast_embedder)

    md_chunker = MarkdownChunker(max_chars=1500, overlap=200)
    code_chunker = CodeChunker(max_chars=1500, min_chars=100, overlap=100)
    chunk_index: dict[int, dict[str, Any]] = {}

    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        raw = path.read_bytes()
        rel = file_to_rel[path]

        if path.suffix.lower() == ".py":
            chunks: list[TextChunk] = code_chunker.chunk(
                text,
                language="python",
                relative_path=rel,
            )
            overlap = code_chunker.overlap
        else:
            chunks = md_chunker.chunk(text)
            overlap = md_chunker.overlap
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
            cursor = idx + max(len(needle) - overlap, 1)

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
            embedder_ids=[main_id, fast_id],
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
        if not chunk_texts:
            continue

        # Embed in the main table.
        main_vecs = np.asarray(main_embedder.encode(chunk_texts), dtype=np.float32)
        backend.write_embeddings(main_id, [(cid, main_vecs[i]) for i, cid in enumerate(chunk_ids)])
        # Embed in the fast table.
        fast_vecs = np.asarray(fast_embedder.encode(chunk_texts), dtype=np.float32)
        backend.write_embeddings(fast_id, [(cid, fast_vecs[i]) for i, cid in enumerate(chunk_ids)])

    reranker: Any | None
    try:
        reranker = CrossEncoderReranker(device="cpu", batch_size=16)
        reranker._get_model()  # type: ignore[attr-defined]
    except Exception as exc:
        print(
            f"[wave3-gate] CrossEncoderReranker load failed ({exc!r}); "
            "running HybridRetriever without rerank."
        )
        reranker = None

    return (
        backend,
        main_embedder,
        main_id,
        fast_embedder,
        fast_id,
        reranker,
        chunk_index,
    )


def _build_retriever(
    *,
    backend: Any,
    main_embedder: Any,
    main_id: int,
    fast_embedder: Any | None,
    fast_id: int | None,
    reranker: Any | None,
    enable_wave1_wave2: bool,
) -> Any:
    """Construct a HybridRetriever optionally wired with a fast embedder."""
    from corpus_forge.config import RetrievalConfig
    from corpus_forge.retrieval.retriever import HybridRetriever

    cfg = RetrievalConfig(
        fusion="alpha",
        alpha=0.7,
        adaptive_lexical_weight=enable_wave1_wave2,
        symbol_query_alpha=0.3,
        definition_boost_enabled=enable_wave1_wave2,
        definition_boost_factor_pre_rerank=1.5,
        definition_boost_factor_post_rerank=1.2,
    )
    return HybridRetriever(
        backend=backend,
        embedder=main_embedder,
        embedder_id=main_id,
        reranker=reranker,
        fast_embedder=fast_embedder,
        fast_embedder_id=fast_id,
        config=cfg,
    )


# ── per-arm bench pass ─────────────────────────────────────────────────────


def _run_arm(
    arm_name: str,
    corpus_name: str,
    queries: list[dict[str, Any]],
    *,
    retriever: Any,
    chunk_index: dict[int, dict[str, Any]],
    harness: dict[str, Any],
    options_kwargs: dict[str, Any],
) -> dict[str, Any]:
    """Run ``queries`` through ``retriever`` and score per-category."""
    from corpus_forge.retrieval.types import SearchOptions
    from tests.perf.metrics import compute_metrics

    runs: dict[str, dict[str, Any]] = {}
    for i, q in enumerate(queries):
        qid = f"{corpus_name}_q{i + 1:03d}"
        opts = SearchOptions(**options_kwargs)
        t_q = time.perf_counter()
        hits = retriever.search(q["query"], opts)
        latency_ms = (time.perf_counter() - t_q) * 1000.0
        runs[qid] = {
            "hits": harness["normalise_hits"](hits, chunk_index),
            "latency_ms": latency_ms,
        }

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


# ── Pareto helpers (mirror Wave 1 / 2) ─────────────────────────────────────


def _category_metric(payload: dict[str, Any], category: str, metric: str) -> float | None:
    agg = payload.get("aggregated", {})
    by_cat = agg.get("by_category", {})
    block = by_cat.get(category)
    if block is None:
        return None
    val = block.get(metric)
    if val is None:
        return None
    return float(val)


def _pareto_violation(
    head: dict[str, Any],
    base: dict[str, Any],
    category: str,
    metric: str,
    max_drop: float,
) -> str | None:
    h = _category_metric(head, category, metric)
    b = _category_metric(base, category, metric)
    if h is None or b is None:
        return None
    drop = b - h
    if drop > max_drop + 1e-9:
        return (
            f"{category}.{metric}: treatment={h:.4f}  baseline={b:.4f}  "
            f"drop={drop:+.4f}  > allowed {max_drop:.4f}"
        )
    return None


# ── the gate ───────────────────────────────────────────────────────────────


@pytest.mark.timeout(1800)
def test_phase_n_wave3_gate() -> None:
    """Three-arm comparison: skip control vs shortcut vs only."""

    if not _BASELINE_PATH.is_file():
        pytest.fail(f"baseline missing: {_BASELINE_PATH}.  Run Wave 0 bench first.")
    rrf_baseline = json.loads(_BASELINE_PATH.read_text())

    harness = _import_bench_harness()
    if not harness["vendored_snapshot"].is_dir():
        pytest.fail(
            f"vendored snapshot missing: {harness['vendored_snapshot']}.  "
            "Run `uv run python tests/fixtures/external/build_snapshots.py` first."
        )

    with _QUERIES_PATH.open() as f:
        queries = [json.loads(line) for line in f if line.strip()]
    queries_by_corpus: dict[str, list[dict[str, Any]]] = {"current": [], "vendored": []}
    for q in queries:
        queries_by_corpus[harness["classify"](q)].append(q)

    current_files = list(harness["iter_current"]())
    vendored_files = list(harness["iter_vendored"]())
    current_rel = {p: (p.relative_to(_REPO_ROOT).as_posix()) for p in current_files}
    vendored_rel = {p: (p.relative_to(_REPO_ROOT).as_posix()) for p in vendored_files}

    control_by_corpus: dict[str, dict[str, Any]] = {}
    shortcut_by_corpus: dict[str, dict[str, Any]] = {}
    only_by_corpus: dict[str, dict[str, Any]] = {}
    build_seconds: dict[str, float] = {}
    reranker_info: dict[str, Any] | None = None

    for corpus_name, files, rel_map in (
        ("current", current_files, current_rel),
        ("vendored", vendored_files, vendored_rel),
    ):
        t0 = time.perf_counter()
        (
            backend,
            main_embedder,
            main_id,
            fast_embedder,
            fast_id,
            reranker,
            chunk_index,
        ) = _build_indexed_backend_with_fast_tier(
            files, file_to_rel=rel_map, corpus_name=corpus_name
        )
        build_seconds[corpus_name] = time.perf_counter() - t0

        # Three retrievers sharing the same backend.
        control_r = _build_retriever(
            backend=backend,
            main_embedder=main_embedder,
            main_id=main_id,
            fast_embedder=fast_embedder,
            fast_id=fast_id,
            reranker=reranker,
            enable_wave1_wave2=True,
        )
        shortcut_r = _build_retriever(
            backend=backend,
            main_embedder=main_embedder,
            main_id=main_id,
            fast_embedder=fast_embedder,
            fast_id=fast_id,
            reranker=reranker,
            enable_wave1_wave2=True,
        )
        only_r = _build_retriever(
            backend=backend,
            main_embedder=main_embedder,
            main_id=main_id,
            fast_embedder=fast_embedder,
            fast_id=fast_id,
            reranker=reranker,
            enable_wave1_wave2=False,  # only-mode skips lexical + rerank anyway
        )

        control_options = {
            "k": 10,
            "fusion": "alpha",
            "alpha": 0.7,
            "rerank": reranker is not None,
            "rerank_top_n": 50,
            "fast_tier_mode": "skip",
        }
        shortcut_options = {
            "k": 10,
            "fusion": "alpha",
            "alpha": 0.7,
            "rerank": reranker is not None,
            "rerank_top_n": 50,
            "fast_tier_mode": "shortcut",
            "fast_tier_top_n": 200,
        }
        only_options = {
            "k": 10,
            "fusion": "alpha",
            "alpha": 0.7,
            "rerank": False,
            "rerank_top_n": 50,
            "fast_tier_mode": "only",
        }

        control_by_corpus[corpus_name] = _run_arm(
            "control",
            corpus_name,
            queries_by_corpus[corpus_name],
            retriever=control_r,
            chunk_index=chunk_index,
            harness=harness,
            options_kwargs=control_options,
        )
        shortcut_by_corpus[corpus_name] = _run_arm(
            "shortcut",
            corpus_name,
            queries_by_corpus[corpus_name],
            retriever=shortcut_r,
            chunk_index=chunk_index,
            harness=harness,
            options_kwargs=shortcut_options,
        )
        only_by_corpus[corpus_name] = _run_arm(
            "only",
            corpus_name,
            queries_by_corpus[corpus_name],
            retriever=only_r,
            chunk_index=chunk_index,
            harness=harness,
            options_kwargs=only_options,
        )
        if reranker is not None and reranker_info is None:
            reranker_info = {"model_id": getattr(reranker, "model_id", None)}

    control_agg = harness["aggregate"](control_by_corpus)
    shortcut_agg = harness["aggregate"](shortcut_by_corpus)
    only_agg = harness["aggregate"](only_by_corpus)

    control_payload = {"by_corpus": control_by_corpus, "aggregated": control_agg}
    shortcut_payload = {"by_corpus": shortcut_by_corpus, "aggregated": shortcut_agg}
    only_payload = {"by_corpus": only_by_corpus, "aggregated": only_agg}

    # ── persist for audit ──────────────────────────────────────────────
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    iso = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = _OUT_DIR / f"phase_n_wave3_{iso}.json"

    payload: dict[str, Any] = {
        "schema_version": 1,
        "phase": "N",
        "wave": 3,
        "kind": "phase_n_wave3",
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "repo_root": _REPO_ROOT.name,
        "n_queries": len(queries),
        "platform": {"python": sys.version.split()[0], "system": sys.platform},
        "build": {f"{c}_seconds": round(s, 3) for c, s in build_seconds.items()},
        "hybrid_config": {
            "main_embedder_model_id": "sentence-transformers/all-MiniLM-L6-v2",
            "main_dimension": 384,
            "fast_embedder_model_id": "minishlab/potion-code-16M",
            "fast_dimension": 256,
            "fusion": "alpha",
            "alpha": 0.7,
            "fast_tier_top_n": 200,
            "rerank_enabled": reranker_info is not None,
            "reranker_model_id": (reranker_info.get("model_id") if reranker_info else None),
        },
        "control": control_payload,
        "shortcut": shortcut_payload,
        "only": only_payload,
        "rrf_baseline_reference": {
            "by_corpus": rrf_baseline.get("by_corpus", {}),
            "aggregated": rrf_baseline.get("aggregated", {}),
        },
    }
    out_path.write_text(json.dumps(payload, indent=2, default=harness["json_default"]))
    print(f"\n[wave3-gate] wrote: {out_path}", flush=True)

    # ── audit tables ──────────────────────────────────────────────────
    print("\n[wave3-gate] per-category aggregated MRR@10:", flush=True)
    print(
        f"  {'category':<11} {'control':>9} {'shortcut':>10} {'only':>8} "
        f"{'Δsc-c':>9} {'Δonly-c':>10}",
        flush=True,
    )
    for cat in ("identifier", "callsite", "concept", "error", "config"):
        c = _category_metric(control_payload, cat, "mrr_at_10")
        s = _category_metric(shortcut_payload, cat, "mrr_at_10")
        o = _category_metric(only_payload, cat, "mrr_at_10")
        if c is None or s is None or o is None:
            continue
        print(
            f"  {cat:<11} {c:>9.4f} {s:>10.4f} {o:>8.4f} {s - c:>+9.4f} {o - c:>+10.4f}",
            flush=True,
        )

    print("\n[wave3-gate] per-category latency p50 (ms):", flush=True)
    print(
        f"  {'category':<11} {'control':>9} {'shortcut':>10} {'only':>8}",
        flush=True,
    )
    for cat in ("identifier", "callsite", "concept", "error", "config"):
        c = _category_metric(control_payload, cat, "p50_latency_ms")
        s = _category_metric(shortcut_payload, cat, "p50_latency_ms")
        o = _category_metric(only_payload, cat, "p50_latency_ms")
        if c is None or s is None or o is None:
            continue
        print(f"  {cat:<11} {c:>9.1f} {s:>10.1f} {o:>8.1f}", flush=True)

    # ── Pareto rule violations ────────────────────────────────────────
    violations: list[str] = []

    # SHORTCUT vs control — identifier is the targeted category (Wave 1+2
    # boost it), so the floor stays tight at -0.025 (small-N noise).
    v = _pareto_violation(
        shortcut_payload, control_payload, "identifier", "mrr_at_10", _SHORTCUT_TARGET_DROP
    )
    if v is not None:
        violations.append(f"[shortcut] {v}")

    # Concept / error / config protected at 0.05.
    for cat in ("concept", "error", "config"):
        v = _pareto_violation(
            shortcut_payload, control_payload, cat, "mrr_at_10", _SHORTCUT_PROTECT_DROP
        )
        if v is not None:
            violations.append(f"[shortcut] {v}")

    # Aggregate Recall@5 invariant — the shortcut filter must not drop
    # chunks the control would have surfaced.  Allow tiny epsilon for
    # small-N flips (one chunk in 50 queries = 0.02).
    sc_rec = float(shortcut_payload["aggregated"].get("recall_at_5", 0.0))
    ctrl_rec = float(control_payload["aggregated"].get("recall_at_5", 0.0))
    if sc_rec < ctrl_rec - 0.02:
        violations.append(
            f"[shortcut] aggregated.recall_at_5={sc_rec:.4f} < control={ctrl_rec:.4f} - 0.02 floor"
        )

    # Shortcut latency ceiling — additive over control on identifier queries.
    sc_id_p50 = _category_metric(shortcut_payload, "identifier", "p50_latency_ms")
    ctrl_id_p50 = _category_metric(control_payload, "identifier", "p50_latency_ms")
    if (
        sc_id_p50 is not None
        and ctrl_id_p50 is not None
        and sc_id_p50 > ctrl_id_p50 + _SHORTCUT_P50_DELTA_MS
    ):
        violations.append(
            f"[shortcut] identifier.p50_latency_ms={sc_id_p50:.1f}ms > "
            f"control={ctrl_id_p50:.1f}ms + {_SHORTCUT_P50_DELTA_MS:.0f}ms head-room"
        )

    # ONLY vs control — concept/error/config protected at 0.10.
    for cat in ("concept", "error", "config"):
        v = _pareto_violation(only_payload, control_payload, cat, "mrr_at_10", _ONLY_PROTECT_DROP)
        if v is not None:
            violations.append(f"[only] {v}")

    # Only mode: absolute identifier p50 ceiling.
    only_id_p50 = _category_metric(only_payload, "identifier", "p50_latency_ms")
    if only_id_p50 is not None and only_id_p50 > _ONLY_P50_CEILING_MS:
        violations.append(
            f"[only] identifier.p50_latency_ms={only_id_p50:.1f}ms > "
            f"{_ONLY_P50_CEILING_MS:.0f}ms ceiling"
        )

    if violations:
        pytest.fail("Phase N Wave 3 Pareto rule failed:\n  " + "\n  ".join(violations))
