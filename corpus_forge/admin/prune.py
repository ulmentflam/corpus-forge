"""Admin-level dataset pruning — first step of ``rfc-corpus-growth-controls``.

The module exposes a single entry point — :func:`prune_dataset` — that
walks the configured candidate pool, scores every chunk under the
:data:`_PRUNE_WEIGHTS` rubric, and surfaces the bottom-percentile rows
as :class:`PruneCandidate` objects in a :class:`PruneReport`.

Scoring **reuses** the existing curation primitives in
:mod:`corpus_forge.curation.selector` so the two surfaces stay aligned
on what "weak / interesting" looks like for a chunk:

- :func:`_compute_confidence_deficit` — high when the classifier is
  unsure (or never touched the chunk).
- :func:`_compute_missing_metadata` — fraction of the six well-known
  metadata fields that are empty.
- :func:`_compute_freshness` — curation treats *fresh* as "needs help"
  (positive signal); for pruning we **invert** it so newly-ingested
  rows are *preserved* rather than swept.

Two new prune-only sub-scores live here:

- :func:`_duplicate_density` — fraction of the candidate pool considered
  a near-duplicate by the MinHash-backed quality signals from
  ``rfc-nlp-data-quality-signals.md``. Detection is import-driven: if
  ``corpus_forge.quality.minhash`` can't be imported, every candidate
  scores ``0.0`` and :attr:`PruneReport.duplicate_density_available`
  is set to ``False`` so callers know the rubric ran in degraded mode.
- :func:`_feedback_drag` — ``1.0`` when the chunk has a feedback row
  with ``kind == "rejected"`` or ``rating < 0``; ``0.0`` otherwise.
  Reads via a best-effort backend hook (``iter_chunk_feedback``)
  before falling back to a direct ``corpus.chunk_feedback`` /
  ``corpus.feedback`` walk. Any failure is logged and treated as "no
  feedback" so a missing table never breaks the prune run.

The default is **dry-run** — :func:`prune_dataset` only deletes when
``apply=True`` is passed explicitly, mirroring the safety net the RFC
calls for. When ``apply=True``, the function prefers a dedicated
``delete_chunks_by_ids`` backend hook (if present) and falls back to a
single bulk DELETE statement otherwise. Postgres uses
``WHERE id = ANY(%s)``; SQLite falls through to a chunked ``IN (...)``
sweep.
"""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from corpus_forge.curation.selector import (
    _Candidate,
    _compute_confidence_deficit,
    _compute_freshness,
    _compute_missing_metadata,
    _iter_curation_candidates,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────


_PRUNE_WEIGHTS: dict[str, float] = {
    "confidence_deficit": 0.20,
    "missing_metadata": 0.20,
    "freshness_inverted": 0.15,  # 1.0 - freshness
    "duplicate_density": 0.25,
    "feedback_drag": 0.20,
}
"""Weight table for the five prune sub-scores. Sums to 1.0.

A change here ripples directly into :func:`prune_dataset` (no per-row
weight selection — the rubric is fixed across the pool) and the
``test_score_ordering_invariants`` lock-test in
``tests/unit/test_prune_scorer.py``.
"""


# Default pool size pulled from the backend; larger pools cost more memory
# but smooth out the duplicate-density normalisation step.
_DEFAULT_CANDIDATE_POOL: int = 2000


# Default percentile selected when the caller doesn't pass one.
_DEFAULT_PERCENTILE: int = 10


# Valid percentile range — full [0, 100] interval. ``0`` is a useful
# no-op (returns the score breakdown without selecting anything); ``100``
# selects the entire pool.
_MIN_PERCENTILE: int = 0
_MAX_PERCENTILE: int = 100


# ─────────────────────────────────────────────────────────────────────────
# Public dataclasses
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PruneCandidate:
    """One chunk surfaced as a prune candidate.

    ``prune_score`` lives in ``[0, 1]`` — higher means "prune more
    eagerly." ``sub_scores`` carries the per-rubric breakdown so callers
    (CLI, MCP, tests) can introspect *why* a chunk was selected.
    """

    chunk_id: int
    document_id: int | None
    source_uri: str | None
    prune_score: float
    sub_scores: dict[str, float]
    reason: str


@dataclass(frozen=True)
class PruneReport:
    """Outcome of one :func:`prune_dataset` invocation.

    The report doubles as a dry-run preview (``applied=False``) and an
    apply-mode receipt (``applied=True, deleted=N``). The candidate list
    is sorted *worst-first* — i.e. the head of ``selected`` is the
    chunk the rubric most wants gone.

    ``duplicate_density_available`` is ``True`` when the MinHash-backed
    quality module was importable for this run and every candidate got
    a real ``duplicate_density`` sub-score; ``False`` when the module
    was unavailable and every candidate carried ``duplicate_density =
    0.0`` (rubric ran in degraded mode). Promoted to the report so the
    flag isn't shape-leaked onto one element of ``selected``.
    """

    dataset: str | None
    percentile: int
    considered: int
    selected: list[PruneCandidate]
    applied: bool
    deleted: int
    summary_by_source: dict[str, int] = field(default_factory=dict)
    duplicate_density_available: bool = False


# ─────────────────────────────────────────────────────────────────────────
# Duplicate-density sub-score
# ─────────────────────────────────────────────────────────────────────────


def _minhash_available() -> bool:
    """Return ``True`` when the MinHash-backed quality signals module is importable.

    The check is intentionally cheap — we don't want to pay a full
    sentence-transformers / datasketch boot just to ask "is the feature
    flag on?" — so we probe for a sentinel symbol and discard the
    import on success.
    """

    try:
        from corpus_forge.quality.minhash import (  # noqa: F401
            jaccard_neighbor_distance,
        )
    except ImportError:
        return False
    return True


def _duplicate_density(
    candidate: _Candidate,
    *,
    minhash_module: Any | None,
) -> float:
    """Per-chunk duplicate-density score in ``[0, 1]``.

    When ``minhash_module`` is ``None`` (the feature flag from
    ``rfc-nlp-data-quality-signals.md`` is unavailable in this build),
    the score is ``0.0`` for every candidate. Otherwise we ask the
    module for the neighbor distance and translate ``low distance ==
    high density``.
    """

    if minhash_module is None:
        return 0.0
    try:
        distance = float(
            minhash_module.jaccard_neighbor_distance(
                chunk_id=candidate.chunk_id,
                text=candidate.text,
            )
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("minhash.jaccard_neighbor_distance raised %r; treating as 0.0", exc)
        return 0.0
    # Distance is in [0, 1]; density is the inversion clamped to [0, 1].
    return max(0.0, min(1.0, 1.0 - distance))


# ─────────────────────────────────────────────────────────────────────────
# Feedback-drag sub-score
# ─────────────────────────────────────────────────────────────────────────


def _coerce_to_dict_list(raw: Any) -> list[dict[str, Any]]:
    """Materialise an opaque backend result into ``list[dict[str, Any]]``.

    Hidden behind a helper so the call sites stay readable and pyrefly's
    ``Any`` propagation narrows correctly. Non-dict rows are filtered out
    (defensive — the backends always emit dicts).
    """

    out: list[dict[str, Any]] = []
    for row in raw:
        if isinstance(row, dict):
            out.append(row)
    return out


def _load_feedback_by_chunk_id(backend: Any) -> dict[int, list[dict[str, Any]]]:
    """Best-effort load of the per-chunk feedback rows.

    Preferred path: a backend-supplied ``iter_chunk_feedback`` hook that
    yields dicts with at least ``chunk_id``, ``kind``, and ``rating``.

    Fallback: a single ``_execute("SELECT chunk_id, kind, rating FROM
    corpus.chunk_feedback")`` (the schema name from the RFC). The query
    is also tried against ``corpus.feedback WHERE entity_type='chunk'``
    for backends that ship the unified ``feedback`` table. Any failure
    is swallowed — feedback drag silently degrades to 0.0 for every
    chunk, which matches the plan's "missing hook → 0.0" contract.
    """

    by_chunk: dict[int, list[dict[str, Any]]] = {}

    hook: Any = getattr(backend, "iter_chunk_feedback", None)
    if callable(hook):
        try:
            rows: Any = hook()
            for row in rows:
                cid_raw = row.get("chunk_id")
                if cid_raw is None:
                    continue
                cid = int(cid_raw)
                by_chunk.setdefault(cid, []).append(dict(row))
            return by_chunk
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("iter_chunk_feedback hook raised %r; falling back to SQL probe", exc)
            by_chunk.clear()

    execute: Any = getattr(backend, "_execute", None)
    if not callable(execute):
        return by_chunk

    # Try the RFC's table name first.
    rows_typed: list[dict[str, Any]] = []
    try:
        rows_typed = _coerce_to_dict_list(
            execute("SELECT chunk_id, kind, rating FROM corpus.chunk_feedback")
        )
    except Exception as exc:
        logger.debug("corpus.chunk_feedback probe failed (%r) — trying corpus.feedback", exc)
        try:
            rows_typed = _coerce_to_dict_list(
                execute(
                    "SELECT entity_id AS chunk_id, kind, rating FROM corpus.feedback "
                    "WHERE entity_type = 'chunk'"
                )
            )
        except Exception as exc2:
            logger.debug("corpus.feedback probe also failed (%r); no feedback drag signal", exc2)
            return by_chunk

    for row in rows_typed:
        cid_raw = row.get("chunk_id")
        if cid_raw is None:
            continue
        cid = int(cid_raw)
        by_chunk.setdefault(cid, []).append(dict(row))
    return by_chunk


def _feedback_drag(
    candidate: _Candidate,
    feedback_rows_by_chunk_id: dict[int, list[dict[str, Any]]],
) -> float:
    """``1.0`` when any feedback row signals rejection; ``0.0`` otherwise."""

    rows = feedback_rows_by_chunk_id.get(candidate.chunk_id, [])
    for row in rows:
        kind = row.get("kind")
        if isinstance(kind, str) and kind.lower() == "rejected":
            return 1.0
        rating = row.get("rating")
        if isinstance(rating, (int, float)) and rating < 0:
            return 1.0
    return 0.0


# ─────────────────────────────────────────────────────────────────────────
# Reason picker
# ─────────────────────────────────────────────────────────────────────────


def _prune_reason(sub_scores: dict[str, float]) -> str:
    """Name the top weighted contributor in a human one-liner."""

    weighted = {
        "confidence_deficit": sub_scores.get("confidence_deficit", 0.0)
        * _PRUNE_WEIGHTS["confidence_deficit"],
        "missing_metadata": sub_scores.get("missing_metadata", 0.0)
        * _PRUNE_WEIGHTS["missing_metadata"],
        "freshness_inverted": sub_scores.get("freshness_inverted", 0.0)
        * _PRUNE_WEIGHTS["freshness_inverted"],
        "duplicate_density": sub_scores.get("duplicate_density", 0.0)
        * _PRUNE_WEIGHTS["duplicate_density"],
        "feedback_drag": sub_scores.get("feedback_drag", 0.0) * _PRUNE_WEIGHTS["feedback_drag"],
    }
    top = max(weighted, key=lambda k: weighted[k])
    if weighted[top] <= 0.0:
        return "all signals at minimum (tie-break selection)"
    return {
        "confidence_deficit": "low / missing classifier confidence",
        "missing_metadata": "metadata gaps",
        "freshness_inverted": "stale content",
        "duplicate_density": "near-duplicate of other chunks",
        "feedback_drag": "user feedback flagged rejection",
    }[top]


# ─────────────────────────────────────────────────────────────────────────
# Deletion helpers
# ─────────────────────────────────────────────────────────────────────────


_SQLITE_BATCH_SIZE: int = 500


def _is_postgres_like(backend: Any) -> bool:
    """Return ``True`` when ``backend`` should be driven via Postgres SQL.

    Capability probe (no isinstance — keeps the prune module decoupled
    from the concrete backend classes and friendly to test doubles):

    1. If the backend exposes ``_paramstyle``, treat ``"pyformat"`` as
       Postgres-shaped (psycopg) and anything else (``"qmark"`` for
       sqlite3, ``"named"`` for some drivers) as SQLite-shaped.
    2. Otherwise fall back to inspecting ``type(backend).__name__`` —
       names containing ``"Postgres"`` (case-insensitive) are Postgres.

    The two checks are independent and either one can promote a backend
    to the bulk-``ANY(%s)`` path. This way a real
    ``PostgresBackend`` (no ``_paramstyle`` today) and a stubbed test
    double exposing only ``_paramstyle = "pyformat"`` both work.
    """

    paramstyle = getattr(backend, "_paramstyle", None)
    if isinstance(paramstyle, str) and paramstyle == "pyformat":
        return True
    return "postgres" in type(backend).__name__.lower()


def _delete_chunks(backend: Any, chunk_ids: list[int]) -> int:
    """Delete the chunks identified by ``chunk_ids``; return rows actually removed.

    The order of preference is:

    1. A backend-supplied ``delete_chunks_by_ids`` hook (best-fit — keeps
       SQL inside the backend module).
    2. A Postgres-shaped bulk DELETE via ``_execute`` + ``ANY(%s)``.
    3. A SQLite-shaped chunked DELETE via ``_execute`` + ``IN (...)``.
    """

    if not chunk_ids:
        return 0

    hook: Any = getattr(backend, "delete_chunks_by_ids", None)
    if callable(hook):
        result: Any = hook(chunk_ids)
        return int(result)

    execute: Any = getattr(backend, "_execute", None)
    if not callable(execute):
        raise RuntimeError(
            f"backend {type(backend).__name__!r} cannot delete chunks — "
            "exposes neither `delete_chunks_by_ids` nor `_execute`"
        )

    if _is_postgres_like(backend):
        try:
            execute(
                "DELETE FROM corpus.chunks WHERE id = ANY(%s)",
                (list(chunk_ids),),
            )
        except Exception:
            logger.exception("postgres bulk delete failed for %d ids", len(chunk_ids))
            raise
        return len(chunk_ids)

    # SQLite (and any other backend with `_execute` but no Postgres tag) —
    # chunk the IN-list so we don't blow past parameter limits.
    # NOTE: the SQLite backend stores tables in the connection-default
    # schema, so the table is `chunks` here — NOT `corpus.chunks` like on
    # Postgres. The schema-prefix asymmetry is intentional, not a bug.
    deleted = 0
    for i in range(0, len(chunk_ids), _SQLITE_BATCH_SIZE):
        batch = chunk_ids[i : i + _SQLITE_BATCH_SIZE]
        placeholders = ",".join("?" * len(batch))
        try:
            execute(
                f"DELETE FROM chunks WHERE id IN ({placeholders})",
                tuple(batch),
            )
        except Exception:
            logger.exception("sqlite batch delete failed for %d ids", len(batch))
            raise
        deleted += len(batch)
    return deleted


# ─────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────


def prune_dataset(
    backend: Any,
    *,
    dataset: str | None,
    percentile: int = _DEFAULT_PERCENTILE,
    apply: bool = False,
    candidate_pool: int = _DEFAULT_CANDIDATE_POOL,
    now: datetime | None = None,
) -> PruneReport:
    """Score the configured candidate pool and (optionally) prune the worst rows.

    Args:
        backend: A backend exposing ``_execute`` (Postgres or SQLite).
            MAY also expose ``iter_curation_candidates``,
            ``iter_chunk_feedback``, and ``delete_chunks_by_ids`` hooks
            for the dedicated fast paths.
        dataset: Optional dataset name to scope the candidate walk.
        percentile: Bottom percentile of the pool selected for pruning,
            in ``[0, 100]``. The plan's RFC default is ``10``.
        apply: When ``True``, the selected rows are deleted. The default
            (``False``) returns the same report with ``deleted=0`` and
            ``applied=False`` — i.e. dry-run.
        candidate_pool: Max rows pulled from the backend (default 2000).
            Larger pools cost more memory but smooth out the
            duplicate-density normalisation.
        now: Optional override for "current" timestamp used by the
            freshness sub-score (test seam).

    Returns:
        A :class:`PruneReport` containing the (worst-first) candidate
        list, the actual percentile applied, and the deletion receipt.

    Raises:
        ValueError: When ``percentile`` is outside ``[0, 100]``, or when
            ``dataset`` is non-None and the backend exposes
            ``find_dataset_id_by_name`` but that lookup returns ``None``
            (unknown dataset name). Guarding here is critical under
            ``apply=True`` — without the check, a typo'd dataset name
            would silently walk *every* dataset's candidates.
    """

    if not _MIN_PERCENTILE <= percentile <= _MAX_PERCENTILE:
        raise ValueError(
            f"percentile must be in [{_MIN_PERCENTILE}, {_MAX_PERCENTILE}]; got {percentile!r}"
        )

    # Unknown-dataset safety: when the caller named a dataset that the
    # backend doesn't know, refuse to fall through to "walk everything"
    # (especially under apply=True — that would delete from the wrong
    # scope). dataset=None is the explicit "walk all datasets" form and
    # is preserved.
    if dataset is not None:
        resolver: Any = getattr(backend, "find_dataset_id_by_name", None)
        if callable(resolver):
            try:
                resolved = resolver(dataset)
            except Exception:  # pragma: no cover — defensive
                logger.exception("find_dataset_id_by_name(%r) raised", dataset)
                resolved = "<unknown>"
            if resolved is None:
                raise ValueError(f"dataset {dataset!r} not found")

    candidates: list[_Candidate] = list(
        _iter_curation_candidates(backend, dataset=dataset, limit=candidate_pool)
    )
    considered = len(candidates)

    if considered == 0:
        return PruneReport(
            dataset=dataset,
            percentile=percentile,
            considered=0,
            selected=[],
            applied=False,
            deleted=0,
            summary_by_source={},
            duplicate_density_available=False,
        )

    # Resolve optional sub-score data sources ONCE for the whole pool.
    minhash_module: Any | None
    if _minhash_available():
        from corpus_forge.quality import minhash as _mh

        minhash_module = _mh
    else:
        minhash_module = None
    duplicate_density_available = minhash_module is not None

    feedback_rows = _load_feedback_by_chunk_id(backend)

    # Score every candidate under the fixed rubric.
    scored: list[PruneCandidate] = []
    for cand in candidates:
        deficit = _compute_confidence_deficit(cand.classifier_confidence)
        missing_score, _missing_fields = _compute_missing_metadata(cand)
        fresh = _compute_freshness(cand.modified_at, now=now)
        fresh_inv = 1.0 - fresh
        dup_density = _duplicate_density(cand, minhash_module=minhash_module)
        fb_drag = _feedback_drag(cand, feedback_rows)

        score = (
            deficit * _PRUNE_WEIGHTS["confidence_deficit"]
            + missing_score * _PRUNE_WEIGHTS["missing_metadata"]
            + fresh_inv * _PRUNE_WEIGHTS["freshness_inverted"]
            + dup_density * _PRUNE_WEIGHTS["duplicate_density"]
            + fb_drag * _PRUNE_WEIGHTS["feedback_drag"]
        )
        score = max(0.0, min(1.0, score))

        sub_scores: dict[str, float] = {
            "confidence_deficit": deficit,
            "missing_metadata": missing_score,
            "freshness_inverted": fresh_inv,
            "duplicate_density": dup_density,
            "feedback_drag": fb_drag,
        }
        scored.append(
            PruneCandidate(
                chunk_id=cand.chunk_id,
                document_id=cand.document_id,
                source_uri=cand.source_uri,
                prune_score=score,
                sub_scores=sub_scores,
                reason=_prune_reason(sub_scores),
            )
        )

    # Sort worst-first (highest prune_score → most prunable).
    scored.sort(key=lambda c: c.prune_score, reverse=True)

    top_n = math.ceil(considered * percentile / 100)
    selected = scored[:top_n]

    # Per-source summary grouping by the source URI's filename stem.
    summary_counter: Counter[str] = Counter()
    for cand in selected:
        stem = (Path(cand.source_uri).stem or "<unknown>") if cand.source_uri else "<unknown>"
        summary_counter[stem] += 1
    summary_by_source: dict[str, int] = dict(summary_counter)

    applied = False
    deleted = 0
    if apply and selected:
        ids_to_delete = [c.chunk_id for c in selected]
        deleted = _delete_chunks(backend, ids_to_delete)
        applied = True

    return PruneReport(
        dataset=dataset,
        percentile=percentile,
        considered=considered,
        selected=selected,
        applied=applied,
        deleted=deleted,
        summary_by_source=summary_by_source,
        duplicate_density_available=duplicate_density_available,
    )


__all__ = [
    "PruneCandidate",
    "PruneReport",
    "prune_dataset",
]
