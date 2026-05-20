"""Learned and heuristic quality scoring for corpus chunks.

Public API
----------
``score_chunk_quality(chunk, *, model_path=None) -> float``
    Score a single chunk in ``[0.0, 1.0]``.  Uses a fast heuristic by default;
    optionally loads a joblib-persisted sklearn estimator keyed by ``model_path``.

``score_chunks_batch(chunks, *, model_path=None) -> list[float]``
    Batch version — same ordering as the input list.

``persist_quality_signals(conn, chunk_ids, scores, *, source="heuristic_v1") -> int``
    Write scores to ``chunk_quality_signals`` (idempotent; returns rows inserted).

Lazy-import contract
--------------------
``sklearn`` and ``joblib`` are **not** imported at module level.  They are
imported inside ``_load_trained_model()`` only when a valid ``model_path`` is
passed.  This keeps corpus-forge's cold-start time budget intact for users who
do not have the ``[analyze]`` extra installed.

Cross-reference: ``.planning/tdd/phase_o_eda_cleaning.md`` § Wave O3.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Module-level cache: {model_path_str -> model_object}
# Populated on first use; never evicted (process lifetime).
# ---------------------------------------------------------------------------

_MODEL_CACHE: dict[str, Any] = {}

# ---------------------------------------------------------------------------
# Heuristic constants
# ---------------------------------------------------------------------------

# Text length thresholds (characters)
_SHORT_TEXT_THRESHOLD: int = 100
_LONG_TEXT_THRESHOLD: int = 5000

# Sub-score weights — must sum to 1.0
_WEIGHT_ADEQUACY: float = 0.60
_WEIGHT_LABEL: float = 0.20
_WEIGHT_METADATA: float = 0.20

# Minimum non-trivial metadata fields to earn the full metadata bonus
_META_RICHNESS_THRESHOLD: int = 3


# ---------------------------------------------------------------------------
# Heuristic scorer internals
# ---------------------------------------------------------------------------


def _count_nontrivial_metadata_fields(metadata: dict[str, Any]) -> int:
    """Return count of metadata fields with non-empty, non-None values."""
    return sum(1 for v in metadata.values() if v is not None and str(v).strip() != "")


def _heuristic_score(chunk: dict[str, Any]) -> float:
    """Compute a heuristic quality score in [0.0, 1.0].

    Sub-scores:
    - adequacy (0.60 weight): text length/token adequacy with short/long penalties.
    - label presence (0.20 weight): 1.0 if ``classifier_label`` key present, else 0.0.
    - metadata richness (0.20 weight): fraction of richness threshold satisfied.
    """
    text: str = chunk.get("text", "") or ""

    # Empty / whitespace-only text → hard zero.
    if not text.strip():
        return 0.0

    text_len = len(text)

    # Adequacy sub-score based on text length.
    if text_len < _SHORT_TEXT_THRESHOLD:
        # Short-chunk penalty: caps adequacy sub-score so combined score <= 0.3
        # (since label and metadata bonuses are absent for a plain short chunk).
        adequacy = 0.0
    elif text_len > _LONG_TEXT_THRESHOLD:
        # Long-chunk penalty: partial adequacy — oversized splits are a quality
        # signal that chunking wasn't ideal.
        adequacy = 0.5
    else:
        # Comfortable range — full adequacy score.
        adequacy = 1.0

    # Label presence sub-score.
    label_score = 1.0 if chunk.get("classifier_label") else 0.0

    # Metadata richness sub-score.
    metadata: dict[str, Any] = chunk.get("metadata") or {}
    nontrivial = _count_nontrivial_metadata_fields(metadata)
    meta_score = min(1.0, nontrivial / _META_RICHNESS_THRESHOLD)

    combined = (
        _WEIGHT_ADEQUACY * adequacy + _WEIGHT_LABEL * label_score + _WEIGHT_METADATA * meta_score
    )
    # Clamp for safety (floating-point arithmetic should never exceed bounds,
    # but belt-and-suspenders).
    return float(max(0.0, min(1.0, combined)))


# ---------------------------------------------------------------------------
# Model loading (lazy)
# ---------------------------------------------------------------------------


def _load_trained_model(model_path: Path) -> Any | None:
    """Load a joblib-persisted sklearn model, caching at module level.

    Returns the model object, or ``None`` if the file does not exist
    (triggers silent heuristic fallback in the caller).

    ``joblib`` is imported here — NOT at module level — to satisfy the
    lazy-import contract (no sklearn/joblib on module load).
    """
    path_str = str(model_path)
    if path_str in _MODEL_CACHE:
        return _MODEL_CACHE[path_str]

    if not model_path.exists():
        return None

    import joblib  # noqa: PLC0415

    model = joblib.load(model_path)
    _MODEL_CACHE[path_str] = model
    return model


# ---------------------------------------------------------------------------
# Public API: score_chunk_quality
# ---------------------------------------------------------------------------


def score_chunk_quality(
    chunk: dict[str, Any],
    *,
    model_path: Path | None = None,
) -> float:
    """Score a single chunk's quality, returning a float in ``[0.0, 1.0]``.

    Args:
        chunk: Dict with at minimum ``"text"`` (str) and ``"token_count"`` (int).
            Optional keys: ``"classifier_label"`` (str), ``"metadata"`` (dict).
        model_path: Path to a joblib-persisted sklearn estimator.  When ``None``
            (default) or when the file does not exist, the heuristic scorer is
            used.  When a valid path is supplied, ``model.predict_proba`` is
            called and the result is clamped to ``[0.0, 1.0]``.

    Returns:
        float in ``[0.0, 1.0]``.  Always finite.  Deterministic in heuristic
        mode (no PRNG).
    """
    if model_path is not None:
        model = _load_trained_model(model_path)
        if model is not None:
            # Build a minimal feature vector from the chunk.
            text = chunk.get("text", "") or ""
            token_count = int(chunk.get("token_count") or 0)
            features = [[len(text), token_count]]
            proba = model.predict_proba(features)
            # predict_proba returns shape (n_samples, n_classes).
            # We take the probability of the positive class (index -1).
            raw_score = float(proba[0][-1])
            return float(max(0.0, min(1.0, raw_score)))
        # File does not exist — fall through to heuristic silently.

    return _heuristic_score(chunk)


# ---------------------------------------------------------------------------
# Public API: score_chunks_batch
# ---------------------------------------------------------------------------


def score_chunks_batch(
    chunks: list[dict[str, Any]],
    *,
    model_path: Path | None = None,
) -> list[float]:
    """Score a list of chunks, returning scores in the same order as input.

    Thin wrapper over :func:`score_chunk_quality` — applies it to each element
    in order.  For large batches and a real model, callers may want to batch
    ``predict_proba`` calls directly; this implementation prioritises
    correctness over micro-optimisation.

    Args:
        chunks: List of chunk dicts (same schema as :func:`score_chunk_quality`).
        model_path: Forwarded to :func:`score_chunk_quality`.

    Returns:
        ``list[float]`` of the same length as *chunks*, each in ``[0.0, 1.0]``.
    """
    return [score_chunk_quality(c, model_path=model_path) for c in chunks]


# ---------------------------------------------------------------------------
# Public API: persist_quality_signals
# ---------------------------------------------------------------------------


def persist_quality_signals(
    conn: Any,
    chunk_ids: list[int],
    scores: list[float],
    *,
    source: str = "heuristic_v1",
) -> int:
    """Write quality scores to ``chunk_quality_signals``.

    Idempotent: rows keyed on ``(chunk_id, signal_name, source)`` are only
    inserted when that triple does not already exist.  A second call with the
    same arguments returns 0 and leaves the table unchanged.

    Uses the migration 0012 ``computed_at`` server-side default — callers do
    not supply a timestamp.

    Args:
        conn: DB-API 2.0 connection — ``sqlite3.Connection`` or a psycopg
            connection.  Dialect detected by type.
        chunk_ids: Ordered list of chunk primary-key IDs.
        scores: Quality scores corresponding to *chunk_ids* (same length).
        source: Label written to the ``source`` column (default ``"heuristic_v1"``).

    Returns:
        Number of rows actually inserted.  Re-runs return 0 for already-present
        triples.

    Raises:
        ValueError: if ``len(chunk_ids) != len(scores)``.
    """
    if not chunk_ids:
        return 0

    if len(chunk_ids) != len(scores):
        raise ValueError(
            f"chunk_ids and scores must have the same length; "
            f"got {len(chunk_ids)} and {len(scores)}"
        )

    _SIGNAL_NAME = "learned_quality"

    is_sqlite = isinstance(conn, sqlite3.Connection)

    if is_sqlite:
        insert_sql = (
            "INSERT INTO chunk_quality_signals "
            "(chunk_id, signal_name, signal_value, source) "
            "SELECT ?, ?, ?, ? "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM chunk_quality_signals "
            "WHERE chunk_id = ? AND signal_name = ? AND source = ?"
            ")"
        )
    else:
        insert_sql = (
            "INSERT INTO corpus.chunk_quality_signals "
            "(chunk_id, signal_name, signal_value, source) "
            "SELECT %s, %s, %s, %s "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM corpus.chunk_quality_signals "
            "WHERE chunk_id = %s AND signal_name = %s AND source = %s"
            ")"
        )

    inserted = 0

    if is_sqlite:
        cur = conn.cursor()
        try:
            for chunk_id, score in zip(chunk_ids, scores, strict=True):
                cur.execute(
                    insert_sql,
                    (
                        int(chunk_id),
                        _SIGNAL_NAME,
                        float(score),
                        source,
                        int(chunk_id),
                        _SIGNAL_NAME,
                        source,
                    ),
                )
                inserted += cur.rowcount if cur.rowcount > 0 else 0
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    else:
        with conn.cursor() as cur:
            try:
                for chunk_id, score in zip(chunk_ids, scores, strict=True):
                    cur.execute(
                        insert_sql,
                        (
                            int(chunk_id),
                            _SIGNAL_NAME,
                            float(score),
                            source,
                            int(chunk_id),
                            _SIGNAL_NAME,
                            source,
                        ),
                    )
                    inserted += cur.rowcount if cur.rowcount > 0 else 0
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    return inserted
