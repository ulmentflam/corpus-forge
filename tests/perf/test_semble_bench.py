"""Phase M Wave 5 — semble vs HybridRetriever bench harness.

Gated by ``CF_SEMBLE_BENCH=1`` so this test never runs in CI by default.
The bench:

1. Discovers a corpus by walking ``corpus_forge/``, ``config.example.toml``,
   and a small set of doc files in the repo root.
2. Builds two retrievers over that corpus:
   - :class:`corpus_forge.retrieval.HybridRetriever` using a SQLite
     backend (``:memory:``), a ``MarkdownChunker`` for ``.md``/``.toml``
     and a paragraph-bounded chunker for ``.py``, and the
     ``all-MiniLM-L6-v2`` sentence-transformer (RetrievalConfig defaults
     + rerank=True per the spike spec).
   - :class:`experiments.semble_adapter.SembleRetriever` over the same
     directory list.
3. Runs the 25 hand-crafted queries from
   ``tests/perf/data/semble_queries.jsonl`` against both retrievers.
4. Joins each retriever's hits back to ``(file, byte_start, byte_end)``
   spans so ``tests.perf.metrics.compute_metrics`` can score them
   uniformly.  Semble chunks are line-keyed, so the harness re-reads the
   chunk file and computes the byte span for ``(start_line, end_line)``
   at hit time.  HybridRetriever chunks carry no byte span natively
   either; we look up the chunk text in the original document and
   compute the offset by ``str.find``.
5. Writes the full per-query breakdown to
   ``tests/perf/out/semble_bench_<ISO>.json``.

The test passes iff both retrievers run to completion and the JSON dump
is well-formed.  Quality/latency thresholds live in the decision doc,
not as hard test failures — this is a research spike, not a regression
gate.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# Skip the entire module unless the env var is set.
pytestmark = pytest.mark.skipif(
    os.environ.get("CF_SEMBLE_BENCH") != "1",
    reason="set CF_SEMBLE_BENCH=1 to run the semble bench (research spike).",
)

# Repo root resolution.  __file__ is .../corpus-forge/tests/perf/test_semble_bench.py.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUT_DIR = _REPO_ROOT / "tests" / "perf" / "out"
_QUERIES_PATH = _REPO_ROOT / "tests" / "perf" / "data" / "semble_queries.jsonl"

# Bench corpus surface — the same set of dirs/files the hand-crafted
# query ground truth references.  We intentionally do NOT index the full
# repo (tests/fixtures/multi_format_corpus would balloon the index with
# trivial throwaway code).
_CORPUS_ROOTS: list[Path] = [
    _REPO_ROOT / "corpus_forge",
]
_EXTRA_FILES: list[Path] = [
    _REPO_ROOT / "config.example.toml",
    _REPO_ROOT / "README.md",
]

# Skip these subdirs of corpus_forge/ — they bloat the index without
# being referenced by any ground-truth query.
_SKIP_REL_PREFIXES = ("schema/",)


# ── utilities ───────────────────────────────────────────────────────────


def _load_queries() -> list[dict[str, Any]]:
    with _QUERIES_PATH.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def _iter_corpus_files() -> Iterator[Path]:
    """Yield bench-corpus files in a deterministic order.

    Yields ``corpus_forge/**`` plus a small set of root-level docs.  We
    deliberately exclude ``tests/fixtures/`` (the multi-format corpus has
    thousands of throwaway code samples in many languages that would
    bloat the index and skew the latency numbers).
    """
    for root in _CORPUS_ROOTS:
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if any(rel.startswith(prefix) for prefix in _SKIP_REL_PREFIXES):
                continue
            if p.suffix not in {".py", ".md", ".toml", ".sql", ".j2"}:
                continue
            yield p
    for p in _EXTRA_FILES:
        if p.is_file():
            yield p


def _stage_corpus(staging: Path) -> Path:
    """Copy the bench-corpus files into ``staging`` preserving relative paths.

    Anchoring semble at ``staging`` (instead of the live repo root)
    bounds the scan to exactly the files we want benchmarked and makes
    semble's returned ``file_path`` (relative to ``staging``) match the
    ground-truth's repo-relative paths exactly.
    """
    staging.mkdir(parents=True, exist_ok=True)
    for src in _iter_corpus_files():
        rel = src.relative_to(_REPO_ROOT)
        dst = staging / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Preserve byte-for-byte content so byte offsets line up.
        dst.write_bytes(src.read_bytes())
    return staging


def _git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=_REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _line_to_byte_spans(raw: bytes) -> list[int]:
    """Return ``line_starts`` where ``line_starts[i]`` is the byte offset
    of line ``i+1`` (1-based).  ``line_starts[len_lines]`` is the EOF
    byte offset so callers can compute a span end for the last line.

    Operates on raw bytes (not decoded text) so the resulting offsets
    line up with ground-truth byte offsets from ``Path.read_bytes()``.
    Non-ASCII characters in the file would otherwise shift the
    text-domain offsets by ``len(utf8) - len(str)`` bytes per char.
    """
    starts = [0]
    for i, b in enumerate(raw):
        if b == 0x0A:  # '\n'
            starts.append(i + 1)
    starts.append(len(raw))
    return starts


# ── HybridRetriever construction ────────────────────────────────────────


def _build_hybrid_retriever() -> tuple[Any, dict[int, dict[str, Any]]]:
    """Build a HybridRetriever over the bench corpus.

    Returns:
        ``(retriever, chunk_index)`` where ``chunk_index`` maps the backend's
        integer ``chunk_id`` to a dict ``{"file_path", "byte_start",
        "byte_end"}`` so the bench can join hits back to byte spans for
        ground-truth scoring.  This join must happen at index-build time
        because the backend doesn't store byte spans natively.
    """
    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.chunkers.base import MarkdownChunker, TextChunk
    from corpus_forge.embedders.sentence_transformers import (
        SentenceTransformersEmbedder,
    )
    from corpus_forge.retrieval.retriever import HybridRetriever
    from corpus_forge.sources.base import RawDocument

    backend = SQLiteBackend(path=":memory:")
    backend.migrate()
    dataset_id = backend.get_or_create_dataset(
        name="semble_bench", kind="default", description="Phase M Wave 5 bench"
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

    files = list(_iter_corpus_files())
    # Batch up all chunks first so we can call the embedder once per file
    # (avoids re-loading the model on the second batch).
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        raw = path.read_bytes()
        rel = path.relative_to(_REPO_ROOT).as_posix()

        chunks: list[TextChunk] = chunker.chunk(text)
        if not chunks:
            continue

        # Compute BYTE spans for each chunk by encoding the chunk text to
        # UTF-8 and locating it inside the raw bytes via ``bytes.find``.
        # The chunker emits overlapping slices, so we walk a cursor
        # forward through the byte stream.  Using bytes (not chars) keeps
        # spans comparable to ground-truth byte offsets even when the
        # file contains non-ASCII characters (em-dashes are common here).
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
            # Advance cursor past the chunk's bytes minus overlap (in chars,
            # but used as a coarse lower bound on the next byte search).
            cursor = idx + max(len(needle) - chunker.overlap, 1)

        # Build a RawDocument and upsert.
        import hashlib

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

        # Fetch back the chunk ids (in insertion order).  The simplest
        # path is a direct SELECT — this is bench code, not production.
        rows = backend._execute(  # type: ignore[attr-defined]
            "SELECT id, text FROM chunks WHERE document_id = ? ORDER BY id ASC",
            (doc_id,),
        )
        # rows is len == len(chunks); zip with chunk_byte_spans.
        for row, (bs, be) in zip(rows, chunk_byte_spans, strict=False):
            chunk_index[int(row["id"])] = {
                "file_path": rel,
                "byte_start": int(bs),
                "byte_end": int(be),
            }

        # Compute and write embeddings for this document's chunks.
        chunk_ids = [int(r["id"]) for r in rows]
        chunk_texts = [r["text"] for r in rows]
        if chunk_texts:
            import numpy as np

            vecs = embedder.encode(chunk_texts)
            vecs_arr = np.asarray(vecs, dtype=np.float32)
            pairs = [(cid, vecs_arr[i]) for i, cid in enumerate(chunk_ids)]
            backend.write_embeddings(embedder_id, pairs)

    # Cross-encoder reranker is the production default (RetrievalConfig
    # ships with rerank enabled per the spike spec).  Construction is
    # cheap; the BGE model loads lazily on the first ``rerank()`` call.
    # If sentence-transformers can't load the model (offline / no HF
    # cache), the bench falls back to no-rerank with a warning so the
    # numbers stay reproducible.
    from corpus_forge.retrieval.rerank.cross_encoder import (
        CrossEncoderReranker,
    )

    reranker: Any | None
    try:
        reranker = CrossEncoderReranker(device="cpu", batch_size=16)
        # Force the model load up-front so the first query's latency
        # doesn't include the ~600 MB download.
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


# ── Hit normalisation ───────────────────────────────────────────────────


def _normalise_hybrid_hits(
    hits: list[Any], chunk_index: dict[int, dict[str, Any]]
) -> list[Any]:
    """Attach ``file_path``/``byte_start``/``byte_end`` metadata to a
    HybridRetriever's hits via the ``chunk_index`` join built at index time."""
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


def _normalise_semble_hits(
    hits: list[Any],
    file_byte_cache: dict[str, list[int]],
    source_root: Path,
) -> list[Any]:
    """Convert semble's (file, line_range) hits to (file, byte_range) by
    re-reading each file once (cached in ``file_byte_cache``).

    ``source_root`` is the directory semble was anchored at (the staging
    dir), so its returned ``file_path`` resolves to a real file under it.
    """
    from types import SimpleNamespace

    out: list[Any] = []
    for h in hits:
        meta = dict(getattr(h, "metadata", {}) or {})
        fp = meta.get("file_path")
        sl = meta.get("start_line")
        el = meta.get("end_line")
        bs, be = 0, 0
        if fp is not None and sl is not None and el is not None:
            line_starts = file_byte_cache.get(fp)
            if line_starts is None:
                raw = (source_root / fp).read_bytes()
                line_starts = _line_to_byte_spans(raw)
                file_byte_cache[fp] = line_starts
            sl_idx = max(1, int(sl)) - 1
            el_idx = min(len(line_starts) - 1, int(el))
            if sl_idx >= len(line_starts):
                sl_idx = len(line_starts) - 1
            bs = line_starts[sl_idx]
            be = line_starts[el_idx] if el_idx < len(line_starts) else line_starts[-1]
        meta["byte_start"] = int(bs)
        meta["byte_end"] = int(be)
        out.append(
            SimpleNamespace(
                chunk_id=getattr(h, "chunk_id", -1),
                score=getattr(h, "score", 0.0),
                text=getattr(h, "text", ""),
                metadata=meta,
            )
        )
    return out


# ── the bench itself ────────────────────────────────────────────────────


def test_semble_bench(tmp_path: Path) -> None:
    """Run both retrievers over the query set and dump JSON.

    Test passes iff:
    - Both retrievers initialise (semble installed; HybridRetriever's
      embedder loads).
    - All 25 queries run to completion on both.
    - The JSON dump in ``tests/perf/out/`` is well-formed.

    A retriever construction failure (e.g. semble not installed) skips
    the test with a clear message rather than failing — the decision doc
    documents the failure path.
    """
    from tests.perf.metrics import compute_metrics

    queries = _load_queries()
    assert len(queries) == 25, f"expected 25 queries, got {len(queries)}"

    # Try semble first — if it can't be imported the bench is moot.
    try:
        from experiments.semble_adapter import SembleRetriever
    except Exception as exc:
        pytest.skip(f"semble adapter import failed: {exc!r}")

    # Stage the bench corpus in a tmpdir so semble (which would
    # otherwise index every file under ``_REPO_ROOT``, including the
    # multi-format fixture tree) sees exactly the files we want
    # benchmarked.  File paths semble returns are relative to ``staging``,
    # which matches the repo-relative paths used in the ground truth.
    staging = tmp_path / "bench_corpus"
    _stage_corpus(staging)

    t_semble_build = time.perf_counter()
    try:
        semble_retriever = SembleRetriever.from_path(
            staging,
            include_text_files=True,
        )
    except ImportError as exc:
        pytest.skip(f"semble not installed in this venv: {exc!r}")
    semble_build_s = time.perf_counter() - t_semble_build

    # Build HybridRetriever — heavier (sentence-transformers, embedding write).
    t_hybrid_build = time.perf_counter()
    try:
        hybrid_retriever, chunk_index = _build_hybrid_retriever()
    except Exception as exc:
        # Treat embedder/backend init failures as a hard skip with details
        # rather than fail — bench failures shouldn't redden CI when the
        # bench is gated.  But here the gate already skipped if env var
        # wasn't set, so we DO want to surface this failure.
        raise RuntimeError(f"HybridRetriever build failed: {exc!r}") from exc
    hybrid_build_s = time.perf_counter() - t_hybrid_build

    from corpus_forge.retrieval.types import SearchOptions

    # ── run queries ────────────────────────────────────────────────
    semble_runs: dict[str, dict[str, Any]] = {}
    hybrid_runs: dict[str, dict[str, Any]] = {}
    file_byte_cache: dict[str, list[int]] = {}

    for i, q in enumerate(queries):
        qid = f"q{i + 1:02d}"
        query = q["query"]

        # SEMBLE
        t0 = time.perf_counter()
        sm_hits = semble_retriever.search(query, SearchOptions(k=10))
        sm_latency_ms = (time.perf_counter() - t0) * 1000.0
        sm_norm = _normalise_semble_hits(sm_hits, file_byte_cache, staging)
        semble_runs[qid] = {"hits": sm_norm, "latency_ms": sm_latency_ms}

        # HYBRID — rerank on iff a reranker is wired (production default).
        hy_opts = SearchOptions(
            k=10,
            rerank=hybrid_retriever.reranker is not None,
            rerank_top_n=50,
        )
        t0 = time.perf_counter()
        hy_hits = hybrid_retriever.search(query, hy_opts)
        hy_latency_ms = (time.perf_counter() - t0) * 1000.0
        hy_norm = _normalise_hybrid_hits(hy_hits, chunk_index)
        hybrid_runs[qid] = {"hits": hy_norm, "latency_ms": hy_latency_ms}

    # ── score ──────────────────────────────────────────────────────
    ground_truth: dict[str, list[dict[str, Any]]] = {}
    by_category: dict[str, list[str]] = {}
    for i, q in enumerate(queries):
        qid = f"q{i + 1:02d}"
        ground_truth[qid] = q["ground_truth_chunks"]
        by_category.setdefault(q["category"], []).append(qid)

    semble_metrics = compute_metrics(semble_runs, ground_truth)
    hybrid_metrics = compute_metrics(hybrid_runs, ground_truth)

    # Per-category breakdown — recompute the same metric over the
    # category-restricted query sets.
    semble_by_cat: dict[str, dict[str, Any]] = {}
    hybrid_by_cat: dict[str, dict[str, Any]] = {}
    for cat, qids in by_category.items():
        sub_gt = {qid: ground_truth[qid] for qid in qids}
        sub_sem = {qid: semble_runs[qid] for qid in qids if qid in semble_runs}
        sub_hyb = {qid: hybrid_runs[qid] for qid in qids if qid in hybrid_runs}
        semble_by_cat[cat] = compute_metrics(sub_sem, sub_gt)
        hybrid_by_cat[cat] = compute_metrics(sub_hyb, sub_gt)

    # ── persist ────────────────────────────────────────────────────
    _OUT_DIR.mkdir(parents=True, exist_ok=True)
    iso = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_path = _OUT_DIR / f"semble_bench_{iso}.json"

    payload: dict[str, Any] = {
        "schema_version": 1,
        "phase": "M",
        "wave": 5,
        "kind": "semble_bench",
        "generated_at": _dt.datetime.now(_dt.UTC).isoformat(),
        "repo_root": str(_REPO_ROOT),
        "git_head": _git_head(),
        "n_queries": len(queries),
        "platform": {
            "python": sys.version.split()[0],
            "system": sys.platform,
        },
        "build": {
            "semble_seconds": round(semble_build_s, 3),
            "hybrid_seconds": round(hybrid_build_s, 3),
        },
        "hybrid_config": {
            "embedder_model_id": "sentence-transformers/all-MiniLM-L6-v2",
            "dimension": 384,
            "fusion": "rrf",
            "rerank_enabled": hybrid_retriever.reranker is not None,
            "reranker_model_id": (
                getattr(hybrid_retriever.reranker, "model_id", None)
                if hybrid_retriever.reranker is not None
                else None
            ),
        },
        "summary": {
            "semble": _strip_per_query(semble_metrics),
            "hybrid": _strip_per_query(hybrid_metrics),
        },
        "by_category": {
            cat: {
                "semble": _strip_per_query(semble_by_cat[cat]),
                "hybrid": _strip_per_query(hybrid_by_cat[cat]),
            }
            for cat in by_category
        },
        "per_query": [
            {
                "query_id": f"q{i + 1:02d}",
                "query": q["query"],
                "category": q["category"],
                "ground_truth_chunks": q["ground_truth_chunks"],
                "semble": semble_metrics["per_query"][i],
                "hybrid": hybrid_metrics["per_query"][i],
            }
            for i, q in enumerate(queries)
        ],
    }

    out_path.write_text(json.dumps(payload, indent=2, default=_json_default))

    # Final sanity assertions — JSON well-formed, both retrievers ran on
    # every query, file exists and round-trips.
    assert out_path.is_file()
    re_read = json.loads(out_path.read_text())
    assert re_read["n_queries"] == 25
    assert len(re_read["per_query"]) == 25
    for pq in re_read["per_query"]:
        assert "semble" in pq
        assert "hybrid" in pq

    print(f"\nbench output: {out_path}")
    print(
        f"semble: MRR@10={semble_metrics['mrr_at_10']:.3f}  "
        f"Recall@5={semble_metrics['recall_at_5']:.3f}  "
        f"p50={semble_metrics['p50_latency_ms']:.2f}ms  "
        f"p95={semble_metrics['p95_latency_ms']:.2f}ms"
    )
    print(
        f"hybrid: MRR@10={hybrid_metrics['mrr_at_10']:.3f}  "
        f"Recall@5={hybrid_metrics['recall_at_5']:.3f}  "
        f"p50={hybrid_metrics['p50_latency_ms']:.2f}ms  "
        f"p95={hybrid_metrics['p95_latency_ms']:.2f}ms"
    )


def _strip_per_query(metrics: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in metrics.items() if k != "per_query"}


def _json_default(obj: Any) -> Any:
    # Fallback for SimpleNamespace / dataclasses leaking into the dump.
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"non-serializable: {type(obj)!r}")
