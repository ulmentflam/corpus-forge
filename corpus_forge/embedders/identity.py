"""Canonical model identity across provider/name aliases (RFC fleet-6, item 1).

The same underlying embedding model is often served under different
``model_id`` strings by different local servers (Ollama
``manutic/nomic-embed-code:latest`` vs. LM Studio
``text-embedding-nomic-embed-code``). Same weights, same vector space —
but corpus-forge fingerprints and lane-keys on ``(provider, model_id)``
verbatim, so switching which host serves a lane looks like a model swap
(false 31-hour re-embed) and fragments the fleet into two lanes that
can't cooperate.

This module resolves a **canonical model identity** that the fingerprint
(item 2) and the fleet ``model_key`` (item 3) both key on. An embedder's
identity set is its own ``(provider, model_id)`` pair plus every pair in
its ``model_aliases``; the canonical identity is the
**lexicographically smallest** pair in that set:

* **No aliases** → the canonical pair is exactly ``(provider, model_id)``,
  so an existing single-name corpus fingerprints/keys *byte-identically*
  to today — no false drift, no migration (the hard backcompat bar).
* **Aliased** → every host that declares the same identity set (fleet-3
  federation publishes it) computes the same min pair, independent of
  which name that host serves the model under.

The functions here are pure and duck-typed over the embedder config (they
only read ``provider`` / ``model_id`` / ``model_aliases``), so they import
no config machinery and can be called from the fingerprint, the fleet
``model_key`` resolver, and the config-load guard alike.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from corpus_forge.config import EmbedderConfig

#: One model identity: a ``(provider, model_id)`` pair, both stripped.
ModelIdentity = tuple[str, str]


def _norm(value: str) -> str:
    """Strip surrounding whitespace (mirrors the fingerprint's field handling)."""

    return value.strip()


def model_identity_pairs(cfg: EmbedderConfig) -> set[ModelIdentity]:
    """The set of ``(provider, model_id)`` pairs this embedder declares as one model.

    The embedder's own pair plus every ``model_aliases`` entry. Whitespace
    is stripped so cosmetic differences never split an identity.
    """

    pairs: set[ModelIdentity] = {(_norm(cfg.provider), _norm(cfg.model_id))}
    for alias in getattr(cfg, "model_aliases", None) or []:
        pairs.add((_norm(alias.provider), _norm(alias.model_id)))
    return pairs


def canonical_model_identity(cfg: EmbedderConfig) -> ModelIdentity:
    """The canonical ``(provider, model_id)`` for this embedder.

    The lexicographically smallest pair in :func:`model_identity_pairs`.
    With no aliases this is the embedder's own pair, so today's
    fingerprint / ``model_key`` inputs are unchanged.
    """

    return min(model_identity_pairs(cfg))


def canonical_model_key(cfg: EmbedderConfig) -> str:
    """``"<provider>:<model_id>"`` of the canonical identity (fleet ``model_key`` input)."""

    provider, model_id = canonical_model_identity(cfg)
    return f"{provider}:{model_id}"


__all__ = [
    "ModelIdentity",
    "canonical_model_identity",
    "canonical_model_key",
    "model_identity_pairs",
]
