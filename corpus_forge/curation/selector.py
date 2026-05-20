"""Phase J / J4 — Curation candidate selector.

Phase O3 adds a fifth signal ``learned_quality`` with a dual-weight scheme:
when a ``chunk_quality_signals`` row exists for a candidate chunk, the selector
switches to ``_SCORE_WEIGHTS_5``; otherwise it falls back to ``_SCORE_WEIGHTS_4``.
The mode is resolved per-chunk so 4-weight and 5-weight targets may coexist in
the same ``next_curation_batch`` response.

The selector answers "what's the next data point that most needs my
help?" by ranking a candidate pool of chunks under weighted signals
(see ``_SCORE_WEIGHTS_4`` / ``_SCORE_WEIGHTS_5`` below):

- ``confidence_deficit`` (weight ``0.35``) — ``1.0`` when no classifier
  has touched the chunk; ``1.0 - classifier_confidence`` otherwise.
  Classifier output lives in the ``chunk_labels.confidence`` column for
  rows whose joined ``labels`` row has ``namespace='class'`` (see Phase
  E surface — both Postgres and SQLite backends store classifier
  decisions there).
- ``missing_metadata`` (weight ``0.30``) — fraction of six well-known
  metadata fields that are empty / missing on the chunk. See
  :data:`MISSING_METADATA_FIELDS`.
- ``ranker_elevation`` (weight ``0.25``) — when ``seed_query`` is
  supplied AND a reranker is available, the reranker's score for
  ``(seed_query, chunk_text)`` normalised to ``[0, 1]`` across the
  pool. When no ``seed_query``, the chunk's cosine *distance* to the
  dataset centroid normalised to ``[0, 1]`` (anomalous = interesting).
  When neither path is workable, the sub-score is a neutral ``0.5``.
- ``freshness`` (weight ``0.10``) — ``1.0`` for chunks whose parent
  document was modified in the last 7 days, decaying linearly to ``0``
  at 180 days, clamped.

Per the project local-or-remote URL invariant, the selector never
constructs a reranker directly — the caller supplies one (a fully-
constructed implementation of the :class:`Reranker` protocol from
``corpus_forge.retrieval.rerank``). The MCP dispatcher reuses the
existing ``_build_reranker_from_config`` factory so any caller-side
URL routing (cross-encoder vs. ollama, local vs. remote) lands here
without modification.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Public constants
# ─────────────────────────────────────────────────────────────────────────


MISSING_METADATA_FIELDS: tuple[str, ...] = (
    "title",
    "heading",
    "labels",
    "description",
    "language",
    "source_uri",
)
"""The six metadata fields the selector inspects for "missing data."

When tuning the score weights or adding a new field, also update the
``CurationTarget.missing_fields`` documentation and the corresponding
unit-test expectations (denominator is ``len(MISSING_METADATA_FIELDS)``).
"""


_SCORE_WEIGHTS_4: dict[str, float] = {
    "confidence_deficit": 0.35,
    "missing_metadata": 0.30,
    "ranker_elevation": 0.25,
    "freshness": 0.10,
}
"""Legacy 4-weight scheme (pre-O3). Used when no ``chunk_quality_signals`` row
exists for a candidate chunk. Sums to 1.0."""

_SCORE_WEIGHTS_5: dict[str, float] = {
    "confidence_deficit": 0.30,
    "missing_metadata": 0.25,
    "ranker_elevation": 0.20,
    "freshness": 0.10,
    "learned_quality": 0.15,
}
"""5-weight scheme (Phase O3). Activated per-chunk when a
``chunk_quality_signals`` row with ``signal_name='learned_quality'`` exists.
Sums to 1.0. Coexists with ``_SCORE_WEIGHTS_4`` in the same batch response."""

SCORE_WEIGHTS: dict[str, float] = _SCORE_WEIGHTS_4
"""Public alias for ``_SCORE_WEIGHTS_4`` — preserved for backward compatibility.

Existing callers that import ``SCORE_WEIGHTS`` continue to see the 4-weight
dict unchanged. Phase O3 introduces ``_SCORE_WEIGHTS_5`` for the per-chunk
mode switch in ``_build_target``."""

# Freshness decay parameters — see :func:`_compute_freshness`.
_FRESHNESS_PLATEAU_DAYS: int = 7
_FRESHNESS_FLOOR_DAYS: int = 180


# ─────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoreBreakdown:
    """Per-target breakdown of the weighted sub-scores.

    The four mandatory fields land in ``[0, 1]``. The final
    ``CurationTarget.score`` is the weighted sum under ``_SCORE_WEIGHTS_4``
    (legacy) or ``_SCORE_WEIGHTS_5`` (Phase O3 per-chunk mode), clipped to
    the same range.

    Phase O3: ``learned_quality`` is the fifth optional sub-score, populated
    when a ``chunk_quality_signals`` row with ``signal_name='learned_quality'``
    exists for the candidate chunk. ``None`` means the 4-weight scheme was used.
    """

    confidence_deficit: float
    missing_metadata: float
    ranker_elevation: float
    freshness: float
    learned_quality: float | None = None


@dataclass(frozen=True)
class CurationTarget:
    """One chunk surfaced as needing curation.

    Returned by :func:`next_curation_target` and embedded in
    :class:`CurationBatch` for the bulk path. The dataclass is frozen so
    callers (CLI, MCP, tests) can safely cache references.
    """

    chunk_id: int
    document_id: int | None
    text: str
    heading: str | None
    current_labels: list[tuple[str, str]]
    current_metadata: dict[str, Any]
    missing_fields: list[str]
    classifier_confidence: float | None
    score: float
    score_breakdown: ScoreBreakdown
    selection_reason: str


@dataclass(frozen=True)
class CurationBatch:
    """A coherent set of curation targets, ratified together.

    ``cohesion_score`` is ``1.0`` for single-target batches; otherwise
    it is ``1.0 / (1.0 + variance(scores))``, so a tight score cluster
    yields a near-``1.0`` cohesion and a spread cluster decays toward
    zero.
    """

    cohesion_score: float
    grouping_key: tuple[str, str]
    targets: list[CurationTarget]


# ─────────────────────────────────────────────────────────────────────────
# Internal data carriers (kept private so the public dataclass schema is
# the only thing that leaks to MCP callers)
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class _Candidate:
    """In-memory candidate row pulled from the backend."""

    chunk_id: int
    document_id: int | None
    text: str
    heading: str | None
    description: str | None
    metadata: dict[str, Any]
    document_title: str | None
    source_uri: str | None
    modified_at: datetime | None
    classifier_label: str | None
    classifier_confidence: float | None
    labels: list[tuple[str, str]] = field(default_factory=list)
    embedding: list[float] | None = None
    learned_quality: float | None = None


# ─────────────────────────────────────────────────────────────────────────
# Backend access — best-effort, supports both Postgres + SQLite shapes
# ─────────────────────────────────────────────────────────────────────────


def _iter_curation_candidates(
    backend: Any,
    *,
    dataset: str | None,
    limit: int,
) -> Iterator[_Candidate]:
    """Yield candidate rows from the backend.

    The backend MAY expose a dedicated ``iter_curation_candidates`` hook
    (preferred — keeps SQL in the backend module). If absent, we fall
    back to a generic walk: ``backend.list_datasets()`` for the dataset
    id, then a ``_execute`` over chunks + labels + documents joined on
    the relevant ids. Both Postgres and SQLite expose ``_execute``.

    When the backend can't supply candidates (no ``_execute``, no list
    helper), we yield nothing and the selector returns ``None``.
    """
    hook = getattr(backend, "iter_curation_candidates", None)
    if callable(hook):
        rows: Any = hook(dataset=dataset, limit=limit)
        for row in rows:
            yield _row_to_candidate(row)
        return

    execute = getattr(backend, "_execute", None)
    if not callable(execute):
        logger.debug(
            "backend %r has no _execute / iter_curation_candidates; selector will return empty",
            type(backend).__name__,
        )
        return

    dataset_id: int | None = None
    if dataset is not None:
        find_id = getattr(backend, "find_dataset_id_by_name", None)
        if callable(find_id):
            raw_id: Any = find_id(dataset)
            dataset_id = None if raw_id is None else int(raw_id)

    yield from _generic_walk(backend, dataset_id=dataset_id, limit=limit)


def _row_to_candidate(row: dict[str, Any]) -> _Candidate:
    """Coerce a generic ``dict`` row to a :class:`_Candidate`."""
    metadata = row.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}

    raw_modified_at = row.get("modified_at")
    modified_at: datetime | None
    if isinstance(raw_modified_at, datetime):
        modified_at = raw_modified_at
    elif isinstance(raw_modified_at, str):
        try:
            modified_at = datetime.fromisoformat(raw_modified_at.replace("Z", "+00:00"))
        except ValueError:
            modified_at = None
    else:
        modified_at = None
    if modified_at is not None and modified_at.tzinfo is None:
        modified_at = modified_at.replace(tzinfo=UTC)

    labels_raw = row.get("labels") or []
    labels: list[tuple[str, str]] = []
    _MIN_LABEL_PAIR = 2  # (namespace, value) minimum
    for entry in labels_raw:
        if isinstance(entry, (list, tuple)) and len(entry) >= _MIN_LABEL_PAIR:
            labels.append((str(entry[0]), str(entry[1])))

    embedding = row.get("embedding")
    embedding_list: list[float] | None
    if embedding is None:
        embedding_list = None
    elif isinstance(embedding, (list, tuple)):
        embedding_list = [float(x) for x in embedding]
    else:
        embedding_list = None

    # Phase O3: read learned_quality from the row; absent key → None (backward-compat).
    raw_lq = row.get("learned_quality")
    learned_quality: float | None = float(raw_lq) if raw_lq is not None else None

    return _Candidate(
        chunk_id=int(row["chunk_id"]),
        document_id=row.get("document_id"),
        text=row.get("text") or "",
        heading=row.get("heading"),
        description=row.get("description"),
        metadata=metadata,
        document_title=row.get("document_title"),
        source_uri=row.get("source_uri"),
        modified_at=modified_at,
        classifier_label=row.get("classifier_label"),
        classifier_confidence=(
            float(row["classifier_confidence"])
            if row.get("classifier_confidence") is not None
            else None
        ),
        labels=labels,
        embedding=embedding_list,
        learned_quality=learned_quality,
    )


def _generic_walk(
    backend: Any,
    *,
    dataset_id: int | None,
    limit: int,
) -> Iterator[_Candidate]:
    """Generic SQL walk over chunks for backends that don't ship a hook.

    Driven through ``backend._execute`` so both Postgres (``%s``
    placeholders) and SQLite (``?`` placeholders) work — we use a
    SQL string with NO bound placeholders and pure-Python filtering
    afterwards. That keeps the helper backend-agnostic without forcing
    every backend to ship the dedicated hook.
    """
    rows = backend._execute(
        "SELECT c.id AS chunk_id, c.document_id, c.text, c.heading, "
        "c.description, c.metadata, "
        "d.title AS document_title, d.source_uri, d.modified_at, d.dataset_id "
        "FROM chunks c "
        "LEFT JOIN documents d ON d.id = c.document_id"
    )
    yielded = 0
    for raw in rows:
        if yielded >= limit:
            break
        if dataset_id is not None and raw.get("dataset_id") != dataset_id:
            continue

        chunk_id = int(raw["chunk_id"])
        label_rows = backend._execute(
            "SELECT l.namespace, l.value, cl.confidence "
            "FROM chunk_labels cl JOIN labels l ON l.id = cl.label_id "
            "WHERE cl.chunk_id = " + str(chunk_id)
        )
        labels: list[tuple[str, str]] = []
        classifier_label: str | None = None
        classifier_confidence: float | None = None
        for lr in label_rows:
            ns = lr["namespace"]
            value = lr["value"]
            labels.append((ns, value))
            if ns == "class" and classifier_confidence is None:
                classifier_label = value
                raw_conf = lr.get("confidence")
                if raw_conf is not None:
                    try:
                        classifier_confidence = float(raw_conf)
                    except (TypeError, ValueError):
                        classifier_confidence = None

        row_dict = dict(raw)
        row_dict["labels"] = labels
        row_dict["classifier_label"] = classifier_label
        row_dict["classifier_confidence"] = classifier_confidence
        yield _row_to_candidate(row_dict)
        yielded += 1


# ─────────────────────────────────────────────────────────────────────────
# Sub-score primitives (pure-function, fully unit-testable)
# ─────────────────────────────────────────────────────────────────────────


def _compute_confidence_deficit(classifier_confidence: float | None) -> float:
    """Score the chunk's classifier-confidence deficit.

    ``None`` (no classifier ever touched it) → ``1.0``. Confidence values
    are clamped to ``[0, 1]`` before subtraction.
    """
    if classifier_confidence is None:
        return 1.0
    clamped = max(0.0, min(1.0, classifier_confidence))
    return 1.0 - clamped


def _compute_missing_metadata(candidate: _Candidate) -> tuple[float, list[str]]:
    """Score how many of the six metadata fields are missing.

    Returns ``(score, missing_field_names)`` where ``score = count / 6``.

    The ``source_uri`` field counts as *missing* when the value is
    ``None`` OR when its file suffix isn't a known extractor extension
    (the J1 estimator's extractor heuristic table is the source of truth
    for "known extension"; we re-use it via
    ``corpus_forge.estimate._classify_extension``).
    """
    missing: list[str] = []

    if not (candidate.document_title or "").strip():
        missing.append("title")
    if not (candidate.heading or "").strip():
        missing.append("heading")
    if not candidate.labels:
        missing.append("labels")
    if not (candidate.description or "").strip():
        missing.append("description")
    language = candidate.metadata.get("language")
    if not isinstance(language, str) or not language.strip():
        missing.append("language")

    if not candidate.source_uri:
        missing.append("source_uri")
    else:
        try:
            from corpus_forge.estimate import (  # noqa: PLC0415
                _classify_extension,
            )

            suffix = Path(candidate.source_uri).suffix.lower()
            if _classify_extension(suffix) is None:
                missing.append("source_uri")
        except ImportError:  # pragma: no cover — estimate ships in J1
            pass

    score = len(missing) / len(MISSING_METADATA_FIELDS)
    return score, missing


def _compute_freshness(modified_at: datetime | None, *, now: datetime | None = None) -> float:
    """Score document recency.

    - ``<= 7`` days old → ``1.0``
    - linearly decays to ``0.0`` at ``180`` days
    - clamped to ``[0, 1]``
    - ``None`` modified_at → ``0.0`` (we can't tell if it's fresh)
    """
    if modified_at is None:
        return 0.0
    now = now or datetime.now(UTC)
    if modified_at.tzinfo is None:
        modified_at = modified_at.replace(tzinfo=UTC)
    age = now - modified_at
    plateau = timedelta(days=_FRESHNESS_PLATEAU_DAYS)
    floor = timedelta(days=_FRESHNESS_FLOOR_DAYS)
    if age <= plateau:
        return 1.0
    if age >= floor:
        return 0.0
    span = (floor - plateau).total_seconds()
    decayed = (floor - age).total_seconds() / span
    return max(0.0, min(1.0, decayed))


def _cosine_distance(a: list[float], b: list[float]) -> float:
    """Cosine distance ``1 - cos_sim`` in pure Python.

    Returns ``0.0`` when either vector is the zero vector — treat that
    as "indistinguishable from centroid" rather than raising.
    """
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    cos_sim = max(-1.0, min(1.0, dot / (norm_a * norm_b)))
    return 1.0 - cos_sim


def _compute_centroid(vectors: Iterable[list[float]]) -> list[float] | None:
    """Element-wise mean of vectors; ``None`` when the iterable is empty
    or vectors have mismatched dims.
    """
    materialised = [v for v in vectors if v]
    if not materialised:
        return None
    dim = len(materialised[0])
    if any(len(v) != dim for v in materialised):
        return None
    n = float(len(materialised))
    centroid = [0.0] * dim
    for v in materialised:
        for i, x in enumerate(v):
            centroid[i] += x
    return [x / n for x in centroid]


_NORMALISE_EPSILON = 1e-12


def _normalise(values: list[float]) -> list[float]:
    """Min-max normalise to ``[0, 1]``. Constant input → all ``0.5``."""
    if not values:
        return values
    lo, hi = min(values), max(values)
    if hi - lo < _NORMALISE_EPSILON:
        return [0.5 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


# ─────────────────────────────────────────────────────────────────────────
# Selection reason picker
# ─────────────────────────────────────────────────────────────────────────


def _selection_reason(
    breakdown: ScoreBreakdown,
    *,
    classifier_confidence: float | None,
    missing_fields: list[str],
    has_seed_query: bool,
    elevation_is_meaningful: bool,
) -> str:
    """Name the top weighted contributor in a human one-liner.

    When the ranker elevation is the neutral fallback (no seed_query
    AND no embeddings), it is excluded from the top-contributor search
    so callers see a more informative reason (e.g. freshness vs.
    missing_metadata) rather than the generic centroid placeholder.
    """
    components = {
        "confidence_deficit": breakdown.confidence_deficit * SCORE_WEIGHTS["confidence_deficit"],
        "missing_metadata": breakdown.missing_metadata * SCORE_WEIGHTS["missing_metadata"],
        "freshness": breakdown.freshness * SCORE_WEIGHTS["freshness"],
    }
    if elevation_is_meaningful:
        components["ranker_elevation"] = (
            breakdown.ranker_elevation * SCORE_WEIGHTS["ranker_elevation"]
        )
    top = max(components, key=lambda k: components[k])
    if top == "confidence_deficit":
        if classifier_confidence is None:
            return "no classifier label yet"
        return f"classifier confidence {classifier_confidence:.2f}"
    if top == "missing_metadata":
        count = len(missing_fields)
        return f"missing {count} of {len(MISSING_METADATA_FIELDS)} metadata fields"
    if top == "ranker_elevation":
        if has_seed_query:
            return "seed-query reranker elevation"
        return "anomalous vs dataset centroid"
    return "newly ingested (<7d)"


# ─────────────────────────────────────────────────────────────────────────
# Public entry points
# ─────────────────────────────────────────────────────────────────────────


def _build_target(
    candidate: _Candidate,
    breakdown: ScoreBreakdown,
    missing_fields: list[str],
    *,
    has_seed_query: bool,
    elevation_is_meaningful: bool,
) -> CurationTarget:
    # Phase O3: per-chunk mode switch — use 5-weight when learned_quality is set.
    if candidate.learned_quality is not None:
        weights = _SCORE_WEIGHTS_5
        lq_term = candidate.learned_quality * weights["learned_quality"]
    else:
        weights = _SCORE_WEIGHTS_4
        lq_term = 0.0
    weighted = (
        breakdown.confidence_deficit * weights["confidence_deficit"]
        + breakdown.missing_metadata * weights["missing_metadata"]
        + breakdown.ranker_elevation * weights["ranker_elevation"]
        + breakdown.freshness * weights["freshness"]
        + lq_term
    )
    score = max(0.0, min(1.0, weighted))
    reason = _selection_reason(
        breakdown,
        classifier_confidence=candidate.classifier_confidence,
        missing_fields=missing_fields,
        has_seed_query=has_seed_query,
        elevation_is_meaningful=elevation_is_meaningful,
    )
    return CurationTarget(
        chunk_id=candidate.chunk_id,
        document_id=candidate.document_id,
        text=candidate.text,
        heading=candidate.heading,
        current_labels=list(candidate.labels),
        current_metadata=dict(candidate.metadata),
        missing_fields=missing_fields,
        classifier_confidence=candidate.classifier_confidence,
        score=score,
        score_breakdown=breakdown,
        selection_reason=reason,
    )


def _ranker_elevation_scores(
    candidates: list[_Candidate],
    *,
    seed_query: str | None,
    reranker: Any | None,
) -> tuple[list[float], bool]:
    """Compute the ranker_elevation sub-score for every candidate.

    Three branches in priority order:
    1. ``seed_query`` + ``reranker`` supplied → score each chunk by the
       reranker, then min-max normalise to ``[0, 1]`` across the pool.
    2. No ``seed_query`` but at least one candidate has an embedding →
       cosine distance from the dataset centroid, normalised.
    3. Neither → neutral ``0.5`` per candidate.

    Returns ``(scores, is_meaningful)`` where ``is_meaningful`` is ``False``
    iff the neutral fallback branch fired.
    """
    n = len(candidates)
    if n == 0:
        return [], True

    if seed_query and reranker is not None:
        from corpus_forge.retrieval.types import Hit  # noqa: PLC0415

        raw_scores: list[float] = []
        for cand in candidates:
            hit = Hit(
                chunk_id=cand.chunk_id,
                score=0.0,
                text=cand.text,
                document_id=cand.document_id,
                source_uri=cand.source_uri,
                title=cand.document_title,
                dataset_id=0,
                metadata=cand.metadata,
                source="fused",
            )
            try:
                ranked = reranker.rerank(seed_query, [hit], top_n=1)
            except Exception as exc:
                logger.debug("reranker raised %r; neutral score", exc)
                raw_scores.append(0.5)
                continue
            if ranked:
                raw_scores.append(float(ranked[0].score))
            else:
                raw_scores.append(0.5)
        return _normalise(raw_scores), True

    vectors = [c.embedding for c in candidates if c.embedding]
    centroid = _compute_centroid(vectors) if vectors else None
    if centroid is not None:
        distances = [
            _cosine_distance(c.embedding, centroid) if c.embedding else 0.0 for c in candidates
        ]
        return _normalise(distances), True

    return [0.5] * n, False


def next_curation_target(
    *,
    backend: Any,
    dataset: str | None = None,
    embedder: str | None = None,  # noqa: ARG001 — accepted for forward-compat
    seed_query: str | None = None,
    reranker: Any | None = None,
    candidate_pool: int = 200,
    now: datetime | None = None,
) -> CurationTarget | None:
    """Return the single highest-scoring curation target.

    Args:
        backend: A backend exposing ``_execute`` (Postgres or SQLite).
            MAY expose ``iter_curation_candidates`` to bypass the
            generic walk.
        dataset: Optional dataset name to scope the candidate walk.
        embedder: Reserved for future per-embedder centroid routing;
            currently accepted-but-ignored.
        seed_query: Optional natural-language query. Triggers the
            reranker path for ``ranker_elevation``.
        reranker: Pre-constructed :class:`Reranker` instance. Only
            consulted when ``seed_query`` is supplied. The caller is
            responsible for honouring the local-or-remote URL invariant
            (typically by calling ``_build_reranker_from_config`` from
            the CLI module).
        candidate_pool: Max rows pulled from the backend (default 200).
            Larger pools cost more memory but smooth out the
            normalisation step.
        now: Optional override for the "current" timestamp used by
            freshness scoring (test seam).

    Returns:
        The highest-scoring :class:`CurationTarget`, or ``None`` when the
        candidate pool is empty.
    """
    candidates = list(_iter_curation_candidates(backend, dataset=dataset, limit=candidate_pool))
    if not candidates:
        return None

    elevation_scores, elevation_is_meaningful = _ranker_elevation_scores(
        candidates,
        seed_query=seed_query,
        reranker=reranker,
    )

    has_seed_query = seed_query is not None
    targets: list[CurationTarget] = []
    for cand, elevation in zip(candidates, elevation_scores, strict=True):
        confidence_deficit = _compute_confidence_deficit(cand.classifier_confidence)
        missing_score, missing_fields = _compute_missing_metadata(cand)
        freshness = _compute_freshness(cand.modified_at, now=now)
        breakdown = ScoreBreakdown(
            confidence_deficit=confidence_deficit,
            missing_metadata=missing_score,
            ranker_elevation=elevation,
            freshness=freshness,
            learned_quality=cand.learned_quality,
        )
        targets.append(
            _build_target(
                cand,
                breakdown,
                missing_fields,
                has_seed_query=has_seed_query,
                elevation_is_meaningful=elevation_is_meaningful,
            )
        )

    targets.sort(key=lambda t: t.score, reverse=True)
    return targets[0]


def next_curation_batch(
    *,
    backend: Any,
    dataset: str | None = None,
    embedder: str | None = None,  # noqa: ARG001 — accepted for forward-compat
    seed_query: str | None = None,
    reranker: Any | None = None,
    candidate_pool: int = 200,
    limit: int = 10,
    now: datetime | None = None,
) -> CurationBatch | None:
    """Group the candidate pool by ``(source_uri stem, classifier_label)``
    and return the highest-mean-score group up to ``limit``.

    Returns:
        The selected :class:`CurationBatch`, or ``None`` when the pool
        is empty.
    """
    if limit < 1:
        raise ValueError(f"limit must be >= 1; got {limit!r}")

    candidates = list(_iter_curation_candidates(backend, dataset=dataset, limit=candidate_pool))
    if not candidates:
        return None

    elevation_scores, elevation_is_meaningful = _ranker_elevation_scores(
        candidates,
        seed_query=seed_query,
        reranker=reranker,
    )

    has_seed_query = seed_query is not None
    targets: list[CurationTarget] = []
    for cand, elevation in zip(candidates, elevation_scores, strict=True):
        confidence_deficit = _compute_confidence_deficit(cand.classifier_confidence)
        missing_score, missing_fields = _compute_missing_metadata(cand)
        freshness = _compute_freshness(cand.modified_at, now=now)
        breakdown = ScoreBreakdown(
            confidence_deficit=confidence_deficit,
            missing_metadata=missing_score,
            ranker_elevation=elevation,
            freshness=freshness,
            learned_quality=cand.learned_quality,
        )
        targets.append(
            _build_target(
                cand,
                breakdown,
                missing_fields,
                has_seed_query=has_seed_query,
                elevation_is_meaningful=elevation_is_meaningful,
            )
        )

    groups: dict[tuple[str, str], list[CurationTarget]] = {}
    for cand, target in zip(candidates, targets, strict=True):
        stem = Path(cand.source_uri).stem if cand.source_uri else "<unknown>"
        class_label = cand.classifier_label or "<unclassified>"
        groups.setdefault((stem, class_label), []).append(target)

    def _mean_score(items: list[CurationTarget]) -> float:
        return statistics.fmean(t.score for t in items) if items else 0.0

    best_key, best_group = max(groups.items(), key=lambda kv: _mean_score(kv[1]))
    best_group.sort(key=lambda t: t.score, reverse=True)
    truncated = best_group[:limit]

    if len(truncated) <= 1:
        cohesion = 1.0
    else:
        variance = statistics.pvariance(t.score for t in truncated)
        cohesion = 1.0 / (1.0 + variance)
        cohesion = max(0.0, min(1.0, cohesion))

    return CurationBatch(
        cohesion_score=cohesion,
        grouping_key=best_key,
        targets=truncated,
    )


__all__ = [
    "MISSING_METADATA_FIELDS",
    "SCORE_WEIGHTS",
    "CurationBatch",
    "CurationTarget",
    "ScoreBreakdown",
    "next_curation_batch",
    "next_curation_target",
]
