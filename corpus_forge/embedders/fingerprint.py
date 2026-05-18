"""Embedder-fingerprint detection (Phase L Wave 5).

Computes a stable SHA-256 over the five embedder identity fields
(``provider``, ``model_id``, ``dimension``, ``normalize``, ``distance``)
and compares it to the fingerprint reconstructed from the stored
``corpus.embedders`` row.  When the two diverge, the user has swapped
embedder models and the existing vectors need a re-encode pass.

Public surface:

- :func:`embedder_fingerprint` — stable hash for an
  :class:`~corpus_forge.config.EmbedderConfig`.
- :func:`compare_active` — list of :class:`EmbedderDrift` (one per
  diverging active embedder; empty list, never ``None``).
- :func:`save_active_fingerprint` — persist the new fingerprint back
  into the embedder row's ``config`` JSONB after a successful re-embed.

The per-chunk re-embed time estimate (``est_seconds``) defaults to
``0.034 s / chunk`` and can be tuned via
``CF_REEMBED_SECONDS_PER_CHUNK``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from corpus_forge.config import Config, EmbedderConfig

logger = logging.getLogger(__name__)

_DEFAULT_SECONDS_PER_CHUNK = 0.034


class Fingerprint(NamedTuple):
    """Stable hash result — short prefix + full hex."""

    short: str  # 16-char hex prefix for compact display
    full: str  # 64-char SHA-256 hex


@dataclass(frozen=True)
class EmbedderDrift:
    """One diverging embedder ready for the drift panel."""

    name: str
    was_model_id: str
    was_dimension: int
    now_model_id: str
    now_dimension: int
    chunks_to_rerun: int
    est_seconds: float
    fingerprint_was: str  # short form
    fingerprint_now: str  # short form


def _seconds_per_chunk() -> float:
    """Resolve the per-chunk re-embed estimate (env override → default)."""

    raw = os.environ.get("CF_REEMBED_SECONDS_PER_CHUNK")
    if not raw:
        return _DEFAULT_SECONDS_PER_CHUNK
    try:
        return float(raw)
    except ValueError:
        return _DEFAULT_SECONDS_PER_CHUNK


def _hash(
    provider: str, model_id: str, dimension: int, normalize: bool, distance: str
) -> Fingerprint:
    canonical = "|".join(
        [
            provider.strip(),
            model_id.strip(),
            repr(int(dimension)),
            repr(bool(normalize)),
            distance.strip(),
        ]
    )
    full = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return Fingerprint(short=full[:16], full=full)


def embedder_fingerprint(cfg: EmbedderConfig) -> Fingerprint:
    """Stable SHA-256 over ``(provider, model_id, dimension, normalize, distance)``.

    String fields are stripped; bool/int fields are ``repr()``'d.  The
    canonical form is the five fields joined by ``"|"`` then SHA-256'd.
    Returns both the short (16-char) and full (64-char) hex forms.
    """

    return _hash(
        cfg.provider,
        cfg.model_id,
        cfg.dimension,
        cfg.normalize,
        cfg.distance,
    )


def _stored_fingerprint(row: dict) -> Fingerprint:
    """Recompute the fingerprint from a stored embedder row.

    Falls back to the row's top-level columns when the ``config`` JSONB
    is missing fields (legacy 2-key shape pre-Wave-5).  SQLite stores
    ``config`` as a serialized JSON string — decoded transparently.
    """

    cfg_blob = row.get("config") or {}
    if isinstance(cfg_blob, str):
        try:
            cfg_blob = json.loads(cfg_blob)
        except (json.JSONDecodeError, ValueError):
            cfg_blob = {}
    if not isinstance(cfg_blob, dict):
        cfg_blob = {}

    provider = cfg_blob.get("provider") or row["provider"]
    model_id = cfg_blob.get("model_id") or row["model_id"]
    dimension = cfg_blob.get("dimension") or row["dimension"]
    if dimension is None:
        dimension = 0
    # SQLite stores ``normalized`` as INTEGER 0/1; coerce.
    normalize = cfg_blob.get("normalize")
    if normalize is None:
        normalize = bool(row.get("normalized", True))
    distance = cfg_blob.get("distance") or row.get("distance") or "cosine"
    return _hash(str(provider), str(model_id), int(dimension), bool(normalize), str(distance))


def _count_existing(backend, embedder_id: int) -> int:
    """Best-effort lookup of already-embedded chunk count."""

    try:
        return int(backend.count_existing_embeddings(embedder_id))
    except (AttributeError, TypeError):
        return 0


def _count_missing(backend, embedder_id: int) -> int:
    """Best-effort lookup of chunks that never got embedded."""

    try:
        return int(backend.count_chunks_missing_embedding(embedder_id))
    except (AttributeError, TypeError):
        return 0


def compare_active(config: Config, backend) -> list[EmbedderDrift]:
    """For each active EmbedderConfig, return drift info iff fingerprint diverges.

    Returns an empty list (never ``None``) when no drift is detected.
    Inactive embedders (``active=False``) are skipped entirely — the
    backend is not consulted.
    """

    drifts: list[EmbedderDrift] = []
    for cfg in config.embedders:
        if not getattr(cfg, "active", True):
            continue
        row = backend.find_embedder_row_by_name(cfg.name)
        if row is None:
            # Never registered — nothing to migrate.
            continue
        fp_was = _stored_fingerprint(row)
        fp_now = embedder_fingerprint(cfg)
        if fp_was.full == fp_now.full:
            continue

        embedder_id = row["id"]
        existing = _count_existing(backend, embedder_id)
        missing = _count_missing(backend, embedder_id)
        chunks_to_rerun = existing + missing
        est_seconds = chunks_to_rerun * _seconds_per_chunk()

        drifts.append(
            EmbedderDrift(
                name=cfg.name,
                was_model_id=str(row["model_id"]),
                was_dimension=int(row["dimension"]),
                now_model_id=cfg.model_id,
                now_dimension=int(cfg.dimension),
                chunks_to_rerun=chunks_to_rerun,
                est_seconds=est_seconds,
                fingerprint_was=fp_was.short,
                fingerprint_now=fp_now.short,
            )
        )
    return drifts


def save_active_fingerprint(config: Config, backend) -> None:
    """Persist the active embedders' fingerprints back into the DB.

    Called after a successful re-embed run so the next ``compare_active``
    sees no drift.  Silently skips embedders that haven't been
    registered yet (``find_embedder_row_by_name`` → None).
    """

    for cfg in config.embedders:
        if not getattr(cfg, "active", True):
            continue
        row = backend.find_embedder_row_by_name(cfg.name)
        if row is None:
            continue
        fp = embedder_fingerprint(cfg)
        new_blob = {
            "provider": cfg.provider,
            "model_id": cfg.model_id,
            "dimension": int(cfg.dimension),
            "normalize": bool(cfg.normalize),
            "distance": cfg.distance,
            "fingerprint": fp.full,
        }
        try:
            backend.update_embedder_config_blob(row["id"], new_blob)
        except AttributeError:
            # Backend hasn't been upgraded to the Wave 5 helper — log
            # and continue rather than break the user's workflow.
            logger.debug(
                "backend %r missing update_embedder_config_blob; skipping",
                type(backend).__name__,
            )


__all__ = [
    "EmbedderDrift",
    "Fingerprint",
    "compare_active",
    "embedder_fingerprint",
    "save_active_fingerprint",
]
