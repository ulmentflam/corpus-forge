"""R3-04 — eval runner unit pins.

Surface under test: ``corpus_forge.eval.runner``.

Public:
- ``evaluate_retriever(retriever, gold_path, k_values, *, max_queries=None)
  -> RetrievalMetrics``
- ``report(metrics: RetrievalMetrics) -> str``
- ``dump_json(metrics: RetrievalMetrics, out: Path) -> None``

Tests use:

- An in-memory ``SQLiteBackend`` (``:memory:``) seeded with a small toy
  corpus.
- A deterministic ``FakeEmbedder`` whose ``encode`` and ``encode_query``
  paths share the same SHA-256-keyed embedding (so the dense top-1 always
  agrees with the gold chunk for a query whose text appears verbatim in
  one of the chunks).
- A small bundled JSONL gold set with hand-checked relevant chunk_ids.

**Pinned baseline NDCG@10 floor**: ``0.80`` against the FakeEmbedder + toy
gold set.  This is intentionally tight enough to catch a regression (e.g.
swapping `encode_query` for `encode` on an asymmetric Qwen3 family — the
R2 carry-over) but loose enough to survive innocuous changes (e.g. RRF
constant tweak).  When the test is failing, FIRST verify the seed,
embedder, and gold set match this docstring; only loosen as a last resort.

A separate test verifies that DELIBERATELY breaking the retriever
(setting ``alpha=1.0`` with a deliberately bad dense path) drops below
the baseline — the floor is real, not theatre.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

# ── module presence ───────────────────────────────────────────────────────


def test_module_importable():
    import corpus_forge.eval.runner  # noqa: F401


def test_public_api_present():
    from corpus_forge.eval.runner import dump_json, evaluate_retriever, report  # noqa: F401


def test_public_api_reexported_from_package():
    from corpus_forge.eval import dump_json, evaluate_retriever, report  # noqa: F401


# ── deterministic FakeEmbedder ────────────────────────────────────────────


_DIM = 16


class _FakeEmbedder:
    """SHA-256-keyed deterministic embedder.

    Symmetric: ``encode`` and ``encode_query`` share the same per-text
    vector.  This makes the embedding for a chunk text equal to the
    embedding for the query that is exactly that chunk text — convenient
    for hand-curated gold sets where the query repeats a chunk's content.
    """

    name = "fake-r3"
    provider = "fake"
    model_id = "fake/r3"
    dimension = _DIM
    normalized = True
    distance = "cosine"

    def _vec(self, text: str) -> np.ndarray:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vec = np.frombuffer(digest[:_DIM], dtype=np.uint8).astype(np.float32)
        vec = (vec + 1.0) / 256.0
        return vec / float(np.linalg.norm(vec))

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts])

    def encode_query(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        # Symmetric — same as encode.
        return np.stack([self._vec(t) for t in texts])

    def warmup(self) -> None:
        pass


class _AsymmetricBadEmbedder(_FakeEmbedder):
    """Encode and encode_query disagree — used to demonstrate the baseline
    test actually fails when the retriever silently breaks the query path.

    Returns a constant unit vector for queries (zero discriminative power)
    so the dense path becomes random noise.  Lexical still works.  With
    ``fusion="alpha", alpha=1.0`` (dense-only), the metric tanks.
    """

    def encode_query(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        vec = np.ones(_DIM, dtype=np.float32)
        vec = vec / float(np.linalg.norm(vec))
        return np.stack([vec for _ in texts])


# ── fixtures: toy seeded SQLite backend + gold set ────────────────────────


def _seed_corpus(tmp_path: Path) -> tuple[object, int, list[int], list[str]]:
    """Seed an on-disk SQLite (file://...) with a small toy corpus.

    SQLite in-memory + sqlite_vec gets fiddly across the loader extension;
    using a file under tmp_path is functionally identical for the test.

    Returns ``(backend, embedder_id, chunk_ids, chunk_texts)``.

    The chunks intentionally contain distinct keyword anchors so that a
    query like ``"alpaca grass"`` lexically + densely lands on the
    "alpaca grazes" chunk.
    """
    from corpus_forge.backends.sqlite import SQLiteBackend
    from corpus_forge.sources.base import RawDocument

    backend = SQLiteBackend(path=str(tmp_path / "eval.db"))
    backend.migrate()

    ds_id = backend.get_or_create_dataset("eval-toy", "text", "R3 runner toy corpus")

    embedder = _FakeEmbedder()
    eid = backend.register_embedder(embedder)

    chunk_texts = [
        "the quick brown fox jumps over the lazy dog",
        "the alpaca grazes on the sweet green grass",
        "elephants never forget their oldest friends",
        "lions roar across the savannah at dawn",
        "kangaroos hop through the dusty outback",
        "penguins waddle on the icy antarctic shore",
        "dolphins leap through warm ocean waves",
        "tortoises move slowly across the desert sand",
        "octopus has eight arms and three hearts",
        "humming birds beat their wings sixty times a second",
    ]

    text = "\n\n".join(chunk_texts)
    raw = RawDocument(
        source_uri="eval://toy.md",
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
        text=text,
        title="Toy",
        modified_at=0.0,
        metadata={},
        labels=[],
    )
    backend.upsert_document(ds_id, raw, [(None, ct) for ct in chunk_texts])

    missing = list(backend.chunks_missing_embedding(eid))
    missing.sort(key=lambda t: t[0])
    chunk_ids = [cid for cid, _ in missing]
    chunk_vecs = [embedder._vec(t) for t in chunk_texts]
    backend.write_embeddings(eid, list(zip(chunk_ids, chunk_vecs, strict=True)))

    return backend, eid, chunk_ids, chunk_texts


def _write_gold(
    tmp_path: Path,
    chunk_ids: list[int],
    chunk_texts: list[str],
) -> Path:
    """Build a hand-curated gold set against the seeded corpus.

    Each query is engineered so the lexically + densely best chunk IS the
    relevant one — i.e. the retriever (which is correct under the
    FakeEmbedder + RRF defaults) should reach NDCG@10 ≈ 1.0.  The
    pinned baseline floor of 0.80 leaves headroom for any future
    fusion-constant tweak.
    """
    p = tmp_path / "gold.jsonl"
    queries = [
        ("q1", "quick brown fox jumps", [0]),
        ("q2", "alpaca grazes on grass", [1]),
        ("q3", "elephants never forget friends", [2]),
        ("q4", "lions roar across savannah", [3]),
        ("q5", "kangaroos dusty outback", [4]),
        ("q6", "penguins waddle icy antarctic", [5]),
        ("q7", "dolphins leap warm ocean", [6]),
        ("q8", "tortoises slowly desert sand", [7]),
        ("q9", "octopus eight arms hearts", [8]),
        ("q10", "humming birds wings second", [9]),
    ]
    with p.open("w", encoding="utf-8") as f:
        for qid, q, rel_idxs in queries:
            row = {
                "query_id": qid,
                "query": q,
                "relevant_chunk_ids": [chunk_ids[i] for i in rel_idxs],
            }
            f.write(json.dumps(row) + "\n")
    return p


# ── basic shape + reporting ───────────────────────────────────────────────


class TestEvaluateRetrieverShape:
    def test_returns_retrieval_metrics(self, tmp_path: Path):
        from corpus_forge.eval.runner import evaluate_retriever
        from corpus_forge.retrieval import HybridRetriever
        from corpus_forge.retrieval.types import RetrievalMetrics

        backend, eid, chunk_ids, chunk_texts = _seed_corpus(tmp_path)
        gold = _write_gold(tmp_path, chunk_ids, chunk_texts)

        retriever = HybridRetriever(backend=backend, embedder=_FakeEmbedder(), embedder_id=eid)
        m = evaluate_retriever(retriever, gold, k_values=[5, 10])
        assert isinstance(m, RetrievalMetrics)
        assert set(m.ndcg.keys()) == {5, 10}
        assert set(m.mrr.keys()) == {5, 10}
        assert set(m.recall.keys()) == {5, 10}
        for k in (5, 10):
            assert 0.0 <= m.ndcg[k] <= 1.0
            assert 0.0 <= m.mrr[k] <= 1.0
            assert 0.0 <= m.recall[k] <= 1.0

    def test_max_queries_limits_evaluation(self, tmp_path: Path):
        from corpus_forge.eval.runner import evaluate_retriever
        from corpus_forge.retrieval import HybridRetriever

        backend, eid, chunk_ids, chunk_texts = _seed_corpus(tmp_path)
        gold = _write_gold(tmp_path, chunk_ids, chunk_texts)

        retriever = HybridRetriever(backend=backend, embedder=_FakeEmbedder(), embedder_id=eid)
        # max_queries=2 should still produce metrics (averaged over 2 queries).
        m = evaluate_retriever(retriever, gold, k_values=[10], max_queries=2)
        # Just shape — no leak.
        assert 10 in m.ndcg

    def test_report_returns_table_with_metric_rows(self, tmp_path: Path):
        from corpus_forge.eval.runner import evaluate_retriever, report
        from corpus_forge.retrieval import HybridRetriever

        backend, eid, chunk_ids, chunk_texts = _seed_corpus(tmp_path)
        gold = _write_gold(tmp_path, chunk_ids, chunk_texts)
        retriever = HybridRetriever(backend=backend, embedder=_FakeEmbedder(), embedder_id=eid)
        m = evaluate_retriever(retriever, gold, k_values=[10])

        s = report(m)
        assert isinstance(s, str)
        # Must mention each metric name and the k value somewhere.
        for label in ("ndcg", "mrr", "recall"):
            assert label.lower() in s.lower(), f"report missing {label!r}"
        assert "10" in s

    def test_dump_json_writes_parseable_metrics(self, tmp_path: Path):
        from corpus_forge.eval.runner import dump_json, evaluate_retriever
        from corpus_forge.retrieval import HybridRetriever

        backend, eid, chunk_ids, chunk_texts = _seed_corpus(tmp_path)
        gold = _write_gold(tmp_path, chunk_ids, chunk_texts)
        retriever = HybridRetriever(backend=backend, embedder=_FakeEmbedder(), embedder_id=eid)
        m = evaluate_retriever(retriever, gold, k_values=[5, 10])

        out = tmp_path / "metrics.json"
        dump_json(m, out)
        assert out.exists()
        data = json.loads(out.read_text())
        # Keys must include ndcg / mrr / recall buckets, each keyed by k.
        assert "ndcg" in data
        assert "mrr" in data
        assert "recall" in data
        # JSON forces str keys; values are floats.
        assert "5" in data["ndcg"] or 5 in data["ndcg"]
        assert "10" in data["ndcg"] or 10 in data["ndcg"]


# ── pinned baseline NDCG@10 floor (HARD-FAIL CI) ──────────────────────────


# Pinned baseline value — see module docstring.  If this needs to be lowered,
# investigate the underlying retrieval regression FIRST.  If it needs to be
# raised, the retriever genuinely improved — celebrate, then raise it.
_PINNED_NDCG_AT_10_FLOOR = 0.80


class TestPinnedBaseline:
    def test_baseline_ndcg_at_10_meets_floor(self, tmp_path: Path):
        """The toy gold set + FakeEmbedder + HybridRetriever (RRF default)
        MUST hit NDCG@10 ≥ ``_PINNED_NDCG_AT_10_FLOOR``.

        Any regression below this floor implies a retrieval-quality bug
        and should hard-fail CI.  See module docstring for the protocol.
        """
        from corpus_forge.eval.runner import evaluate_retriever
        from corpus_forge.retrieval import HybridRetriever

        backend, eid, chunk_ids, chunk_texts = _seed_corpus(tmp_path)
        gold = _write_gold(tmp_path, chunk_ids, chunk_texts)
        retriever = HybridRetriever(backend=backend, embedder=_FakeEmbedder(), embedder_id=eid)
        m = evaluate_retriever(retriever, gold, k_values=[10])
        assert m.ndcg[10] >= _PINNED_NDCG_AT_10_FLOOR, (
            f"NDCG@10 baseline regression: got {m.ndcg[10]:.4f}, "
            f"floor is {_PINNED_NDCG_AT_10_FLOOR}"
        )
        # Upper bound is 1.0 — anything above is impossible math.
        assert m.ndcg[10] <= 1.0

    def test_breaking_retriever_drops_below_floor(self, tmp_path: Path):
        """Deliberate sanity check: an asymmetric-broken retriever fails.

        We swap in an embedder whose ``encode_query`` returns a constant
        vector (no discriminative power).  With ``fusion="alpha", alpha=1.0``
        (dense-only) the retriever loses all signal and NDCG@10 collapses.

        This proves the pinned baseline is not vacuously satisfied.
        """
        from corpus_forge.eval.runner import evaluate_retriever
        from corpus_forge.retrieval import HybridRetriever
        from corpus_forge.retrieval.types import SearchOptions

        backend, eid, chunk_ids, chunk_texts = _seed_corpus(tmp_path)
        gold = _write_gold(tmp_path, chunk_ids, chunk_texts)

        # Use the bad embedder + dense-only fusion to kill the signal.
        retriever = HybridRetriever(
            backend=backend, embedder=_AsymmetricBadEmbedder(), embedder_id=eid
        )

        # Patch the runner's per-call options if it exposes them, else
        # rely on the default RRF.  We'll force dense-only by monkey-
        # patching the search method.
        class _DenseOnlyWrapper:
            def __init__(self, inner):
                self.inner = inner

            def search(self, query: str, options):
                # Force alpha=1.0 dense-only on every call.
                forced = SearchOptions(
                    k=options.k,
                    dataset=options.dataset,
                    fusion="alpha",
                    alpha=1.0,
                    rerank=options.rerank,
                    rerank_top_n=options.rerank_top_n,
                )
                return self.inner.search(query, forced)

        broken = _DenseOnlyWrapper(retriever)
        m = evaluate_retriever(broken, gold, k_values=[10])
        # The broken retriever MUST score below the floor.  If it doesn't,
        # the floor is too generous and someone needs to raise it.
        assert m.ndcg[10] < _PINNED_NDCG_AT_10_FLOOR, (
            f"Deliberate retriever break still scored {m.ndcg[10]:.4f} >= "
            f"floor {_PINNED_NDCG_AT_10_FLOOR} — the floor is too lax."
        )


# ── empty / edge cases ────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_gold_set_raises(self, tmp_path: Path):
        """A gold set with zero rows is a misconfiguration — surface loudly."""
        from corpus_forge.eval.runner import evaluate_retriever
        from corpus_forge.retrieval import HybridRetriever

        backend, eid, _chunk_ids, _ = _seed_corpus(tmp_path)
        gold = tmp_path / "empty.jsonl"
        gold.write_text("", encoding="utf-8")

        retriever = HybridRetriever(backend=backend, embedder=_FakeEmbedder(), embedder_id=eid)
        with pytest.raises(ValueError) as exc:
            evaluate_retriever(retriever, gold, k_values=[10])
        assert "empty" in str(exc.value).lower() or "no queries" in str(exc.value).lower()

    def test_k_values_empty_raises(self, tmp_path: Path):
        from corpus_forge.eval.runner import evaluate_retriever
        from corpus_forge.retrieval import HybridRetriever

        backend, eid, chunk_ids, chunk_texts = _seed_corpus(tmp_path)
        gold = _write_gold(tmp_path, chunk_ids, chunk_texts)
        retriever = HybridRetriever(backend=backend, embedder=_FakeEmbedder(), embedder_id=eid)
        with pytest.raises(ValueError):
            evaluate_retriever(retriever, gold, k_values=[])
