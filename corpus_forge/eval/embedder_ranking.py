"""Embedder-ranking eval harness.

Sweep a set of candidate embedders, evaluate each on the *same* retrieval
gold set, record per-candidate retrieval quality + embedding performance,
and emit a ranked leaderboard envelope.

The module is split into a **pure, injectable core** (:func:`rank_embedders`)
that does no embedding or DB work, and a **real-wiring evaluator factory**
(:func:`make_default_evaluator`) that builds the embedder, embeds the
corpus, and runs :func:`corpus_forge.eval.runner.evaluate_retriever`.  Only
the pure core is exercised by the unit suite — the real path needs models +
a live backend and runs in a separate "on-machine run" task.

Public surface
--------------
- :class:`EmbedderPerf` — embedding throughput / device / GPU-mem record.
- :class:`CandidateResult` — one candidate's retrieval numbers + perf.
- :class:`EmbedderCandidate` — a candidate config row (name/provider/...).
- :func:`rank_embedders` — pure ranking core; takes an injectable
  ``evaluate_fn``, sorts by the primary metric, builds the envelope.
- :func:`make_default_evaluator` — real-wiring ``evaluate_fn`` factory.
- :func:`load_candidates` — parse a TOML candidate manifest.
- :func:`build_envelope` — leaderboard envelope builder.

The leaderboard marshals into the shared
:class:`corpus_forge.eval._schema.EvalOutput` envelope
(``eval_kind = "embedder_ranking"``), like every other ``corpus-forge
eval`` subcommand, so a dashboard can plot all eval kinds on one timeline.
"""

from __future__ import annotations

import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from corpus_forge.eval._schema import EvalKind, EvalOutput
from corpus_forge.retrieval.types import RetrievalMetrics

EVAL_KIND: EvalKind = "embedder_ranking"
DEFAULT_PRIMARY_METRIC = "ndcg@10"
DEFAULT_K_VALUES: tuple[int, ...] = (1, 5, 10)


# ── value objects ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EmbedderPerf:
    """Embedding-time performance record for a single candidate.

    Attributes:
        embed_seconds: Wall-clock seconds spent embedding the corpus.
        chunks_per_sec: Throughput (chunks embedded per second).  ``0.0``
            when ``embed_seconds`` is zero (no division-by-zero blow-up).
        peak_gpu_mb: Peak CUDA memory in MiB during embedding, or ``None``
            when not running on CUDA (CPU / MPS) or torch is unavailable.
        device: Concrete device the embedder ran on (``"cpu"`` / ``"cuda"``
            / ``"mps"``).
    """

    embed_seconds: float
    chunks_per_sec: float
    peak_gpu_mb: float | None
    device: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "embed_seconds": float(self.embed_seconds),
            "chunks_per_sec": float(self.chunks_per_sec),
            "peak_gpu_mb": (None if self.peak_gpu_mb is None else float(self.peak_gpu_mb)),
            "device": self.device,
        }


@dataclass(frozen=True)
class EmbedderCandidate:
    """One candidate embedder, as parsed from the TOML manifest.

    Mirrors the subset of :class:`corpus_forge.config.EmbedderConfig` that
    :func:`corpus_forge.embedders.registry.register_from_config` consumes,
    so a candidate can be handed straight to the registry by the default
    evaluator.  ``register_from_config`` reads these via ``getattr`` with
    sensible defaults, so the optional fields below are forward-compatible.
    """

    name: str
    provider: str
    model_id: str
    dimension: int
    normalize: bool = True
    distance: str = "cosine"
    batch_size: int = 32
    device: str = "auto"


@dataclass(frozen=True)
class CandidateResult:
    """One candidate's evaluation outcome — retrieval numbers + perf.

    The ``recall`` / ``mrr`` / ``ndcg`` dicts are keyed by ``k`` (mirroring
    :class:`~corpus_forge.retrieval.types.RetrievalMetrics`).  ``metrics``
    is the flattened ``"<name>@<k>"`` → score view used for ranking and
    for the ``primary_metric`` lookup.
    """

    name: str
    recall: dict[int, float]
    mrr: dict[int, float]
    ndcg: dict[int, float]
    perf: EmbedderPerf
    metrics: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "recall": {str(k): float(v) for k, v in self.recall.items()},
            "mrr": {str(k): float(v) for k, v in self.mrr.items()},
            "ndcg": {str(k): float(v) for k, v in self.ndcg.items()},
            "metrics": {k: float(v) for k, v in self.metrics.items()},
            "perf": self.perf.to_dict(),
        }


# ── flat-metric helpers ──────────────────────────────────────────────────────


def _flatten_metrics(metrics: RetrievalMetrics) -> dict[str, float]:
    """Flatten a :class:`RetrievalMetrics` into ``"<name>@<k>"`` → score.

    e.g. ``{"ndcg@10": 0.81, "mrr@10": 0.71, "recall@10": 0.64, ...}``.
    """
    flat: dict[str, float] = {}
    for name, bucket in (
        ("ndcg", metrics.ndcg),
        ("mrr", metrics.mrr),
        ("recall", metrics.recall),
    ):
        for k, v in bucket.items():
            flat[f"{name}@{k}"] = float(v)
    return flat


def _candidate_result(name: str, metrics: RetrievalMetrics, perf: EmbedderPerf) -> CandidateResult:
    """Build a :class:`CandidateResult` from a candidate's eval output."""
    return CandidateResult(
        name=name,
        recall=dict(metrics.recall),
        mrr=dict(metrics.mrr),
        ndcg=dict(metrics.ndcg),
        perf=perf,
        metrics=_flatten_metrics(metrics),
    )


# ── envelope builder ──────────────────────────────────────────────────────────


def build_envelope(
    ranking: list[CandidateResult],
    *,
    dataset: str,
    git_commit: str | None,
    primary_metric: str,
    config: dict[str, Any] | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    """Build the leaderboard envelope.

    Shape (matches PR #38's planned ``_schema.py`` ``eval_kind`` envelope)::

        {"eval_kind": "embedder_ranking", "dataset": <str>,
         "git_commit": <str|null>, "ts": <iso8601>,
         "metrics": {"ranking": [<CandidateResult dicts, ranked>],
                     "primary_metric": "ndcg@10"},
         "config": {...}}

    ``ranking`` must already be sorted (descending by ``primary_metric``);
    :func:`rank_embedders` is the canonical caller and does that sort.

    Marshals into the shared :class:`~corpus_forge.eval._schema.EvalOutput`
    envelope and returns ``model_dump()`` so the output stays a plain dict
    for existing consumers. ``ts`` defaults to the envelope's UTC "now" when
    not overridden.
    """
    fields: dict[str, Any] = {
        "eval_kind": EVAL_KIND,
        "dataset": dataset,
        "git_commit": git_commit,
        "metrics": {
            "ranking": [r.to_dict() for r in ranking],
            "primary_metric": primary_metric,
        },
        "config": config if config is not None else {},
    }
    if ts is not None:
        fields["ts"] = ts
    return EvalOutput(**fields).model_dump()


# ── pure injectable core ──────────────────────────────────────────────────────


def rank_embedders(
    candidates: Sequence[EmbedderCandidate],
    *,
    evaluate_fn: Callable[[EmbedderCandidate], tuple[RetrievalMetrics, EmbedderPerf]],
    primary_metric: str = DEFAULT_PRIMARY_METRIC,
    dataset: str = "",
    git_commit: str | None = None,
    config: dict[str, Any] | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    """Rank candidate embedders by retrieval quality on the gold set.

    Pure orchestration: loops ``candidates``, calls ``evaluate_fn`` on each
    (which is the *only* thing that touches models / DB), builds a
    :class:`CandidateResult` per candidate, sorts descending by
    ``primary_metric``, and returns the leaderboard envelope.

    This function does **no** embedding or DB work itself — that is entirely
    in ``evaluate_fn``.  This is what the unit suite exercises with a stub
    ``evaluate_fn``.

    Args:
        candidates: candidate embedders to evaluate, in manifest order.
        evaluate_fn: callable mapping a candidate to its ``(RetrievalMetrics,
            EmbedderPerf)`` pair.  Use :func:`make_default_evaluator` for the
            real wiring, or a stub for tests.
        primary_metric: flat metric key (``"<name>@<k>"``, e.g. ``"ndcg@10"``)
            the leaderboard is ranked by, descending.
        dataset: gold-set label recorded in the envelope.
        git_commit: commit the run was made at (or ``None``).
        config: free-form config block echoed into the envelope.
        ts: ISO-8601 timestamp override (defaults to now, UTC).

    Returns:
        The leaderboard envelope dict (see :func:`build_envelope`).

    Raises:
        ValueError: ``candidates`` is empty.
    """
    if not candidates:
        raise ValueError("candidates must be non-empty")

    results: list[CandidateResult] = []
    for candidate in candidates:
        metrics, perf = evaluate_fn(candidate)
        results.append(_candidate_result(candidate.name, metrics, perf))

    # Sort descending by the primary metric.  A candidate missing the
    # primary metric sorts last (treated as -inf) rather than blowing up,
    # so a partially-failed sweep still produces a usable leaderboard.
    def _key(result: CandidateResult) -> float:
        return result.metrics.get(primary_metric, float("-inf"))

    ranking = sorted(results, key=_key, reverse=True)

    return build_envelope(
        ranking,
        dataset=dataset,
        git_commit=git_commit,
        primary_metric=primary_metric,
        config=config,
        ts=ts,
    )


# ── manifest loader ───────────────────────────────────────────────────────────


def load_candidates(path: Path | str) -> list[EmbedderCandidate]:
    """Parse a TOML candidate manifest into :class:`EmbedderCandidate` rows.

    Manifest shape (a ``[[candidates]]`` array of tables)::

        [[candidates]]
        name = "qwen3-0.6b"
        provider = "sentence_transformers"
        model_id = "Qwen/Qwen3-Embedding-0.6B"
        dimension = 1024

    Required per row: ``name``, ``provider``, ``model_id``, ``dimension``.
    Optional (with defaults matching :class:`EmbedderCandidate`):
    ``normalize``, ``distance``, ``batch_size``, ``device``.

    Raises:
        FileNotFoundError: ``path`` does not exist.
        ValueError: missing ``[[candidates]]`` array or a malformed row.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"candidate manifest not found: {p}")

    data = tomllib.loads(p.read_text(encoding="utf-8"))
    raw_candidates = data.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise ValueError(f"{p}: manifest must define a non-empty `[[candidates]]` array")

    out: list[EmbedderCandidate] = []
    for i, row in enumerate(raw_candidates):
        if not isinstance(row, dict):
            raise ValueError(f"{p}: candidate #{i} must be a table")
        out.append(_parse_candidate(p, i, row))
    return out


def _parse_candidate(path: Path, index: int, row: dict[str, Any]) -> EmbedderCandidate:
    """Validate + coerce one manifest table into an :class:`EmbedderCandidate`."""

    def _require_str(key: str) -> str:
        value = row.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{path}: candidate #{index} missing/empty string `{key}`")
        return value

    dimension = row.get("dimension")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
        raise ValueError(f"{path}: candidate #{index} `dimension` must be a positive int")

    normalize = row.get("normalize", True)
    if not isinstance(normalize, bool):
        raise ValueError(f"{path}: candidate #{index} `normalize` must be a bool")

    distance = row.get("distance", "cosine")
    if not isinstance(distance, str):
        raise ValueError(f"{path}: candidate #{index} `distance` must be a string")

    batch_size = row.get("batch_size", 32)
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size <= 0:
        raise ValueError(f"{path}: candidate #{index} `batch_size` must be a positive int")

    device = row.get("device", "auto")
    if not isinstance(device, str):
        raise ValueError(f"{path}: candidate #{index} `device` must be a string")

    return EmbedderCandidate(
        name=_require_str("name"),
        provider=_require_str("provider"),
        model_id=_require_str("model_id"),
        dimension=int(dimension),
        normalize=normalize,
        distance=distance,
        batch_size=batch_size,
        device=device,
    )


# ── default real-wiring evaluator (NOT run in the unit suite) ─────────────────


def _read_peak_gpu_mb(device: str) -> float | None:
    """Read ``torch.cuda.max_memory_allocated()`` in MiB on a CUDA device.

    Returns ``None`` off CUDA or when torch is unavailable.  The torch
    import is lazy + guarded so this module is importable on hosts without
    the ML stack (mirrors ``corpus_forge._ml_device.detect_device``).
    """
    if device != "cuda":
        return None
    try:
        import torch
    except ImportError:
        return None
    if not torch.cuda.is_available():
        return None
    return float(torch.cuda.max_memory_allocated()) / (1024.0 * 1024.0)


def _reset_peak_gpu_mem(device: str) -> None:
    """Reset CUDA peak-memory stats so the per-candidate reading is isolated."""
    if device != "cuda":
        return
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()


def make_default_evaluator(
    corpus: Sequence[tuple[int, str]],
    gold: Path | str,
    backend: Any,
    registry: Any,
    *,
    k_values: Sequence[int] = DEFAULT_K_VALUES,
) -> Callable[[EmbedderCandidate], tuple[RetrievalMetrics, EmbedderPerf]]:
    """Build the real-wiring ``evaluate_fn`` for :func:`rank_embedders`.

    The returned callable, for one candidate:

    1. Builds + registers the embedder via
       :func:`corpus_forge.embedders.registry.register_from_config`.
    2. Registers it with ``backend`` and embeds ``corpus`` (a list of
       ``(chunk_id, text)`` pairs), timing the embed for throughput and —
       on CUDA — reading peak GPU memory.
    3. Wires a :class:`~corpus_forge.retrieval.HybridRetriever` over the
       embedded corpus and runs
       :func:`corpus_forge.eval.runner.evaluate_retriever` against ``gold``.

    This path is structurally complete but is **not** run in the unit suite
    (needs real models + a live backend); it is correct-by-reading-the-API
    and runs in the later on-machine task.

    Args:
        corpus: ``(chunk_id, text)`` pairs to embed and index.
        gold: gold-set path passed to ``evaluate_retriever``.
        backend: a :class:`~corpus_forge.backends.base.StorageBackend` with
            the corpus already ingested (chunks present for ``chunk_id``s).
        registry: an :class:`~corpus_forge.embedders.registry.EmbedderRegistry`.
        k_values: retrieval cutoffs to score.

    Returns:
        An ``evaluate_fn`` suitable for :func:`rank_embedders`.
    """
    # Lazy imports — keep this module importable on hosts without the
    # retrieval / ML stack installed.  The pure core (rank_embedders,
    # load_candidates) must work everywhere; only this real-wiring path needs
    # them.  PLC0415 (import-outside-top-level) is suppressed for this whole
    # module via [tool.ruff.lint.per-file-ignores] in pyproject.toml — the same
    # mechanism the repo uses for other lazy-import modules (analyze/*, cli.py).
    from corpus_forge._ml_device import resolve_device
    from corpus_forge.embedders.registry import register_from_config
    from corpus_forge.eval.runner import evaluate_retriever
    from corpus_forge.retrieval import HybridRetriever

    texts = [text for _cid, text in corpus]
    chunk_ids = [cid for cid, _text in corpus]

    def _evaluate(candidate: EmbedderCandidate) -> tuple[RetrievalMetrics, EmbedderPerf]:
        embedder = register_from_config(registry, candidate)
        embedder.warmup()

        device = resolve_device(getattr(candidate, "device", "auto"))
        _reset_peak_gpu_mem(device)

        embedder_id = backend.register_embedder(embedder)

        start = time.perf_counter()
        vectors = embedder.encode(texts, batch_size=candidate.batch_size)
        elapsed = time.perf_counter() - start

        backend.write_embeddings(
            embedder_id,
            list(zip(chunk_ids, list(vectors), strict=True)),
        )

        chunks_per_sec = (len(texts) / elapsed) if elapsed > 0 else 0.0
        perf = EmbedderPerf(
            embed_seconds=elapsed,
            chunks_per_sec=chunks_per_sec,
            peak_gpu_mb=_read_peak_gpu_mb(device),
            device=device,
        )

        retriever = HybridRetriever(backend=backend, embedder=embedder, embedder_id=embedder_id)
        metrics = evaluate_retriever(retriever, gold, k_values)
        return metrics, perf

    return _evaluate


__all__ = [
    "DEFAULT_K_VALUES",
    "DEFAULT_PRIMARY_METRIC",
    "EVAL_KIND",
    "CandidateResult",
    "EmbedderCandidate",
    "EmbedderPerf",
    "build_envelope",
    "load_candidates",
    "make_default_evaluator",
    "rank_embedders",
]
