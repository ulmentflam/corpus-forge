"""Phase L Wave 5 — embedder-fingerprint stable hash + drift compare.

Tests the pure functions in :mod:`corpus_forge.embedders.fingerprint`:

- ``embedder_fingerprint(cfg)`` returns a stable SHA-256 over
  ``(provider, model_id, dimension, normalize, distance)``.
- The result exposes ``short`` (16-char prefix) + ``full`` (64-char hex).
- Whitespace on string fields is canonicalised (stripped) so cosmetic
  edits to ``config.toml`` don't trigger spurious drift.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def embedder_cfg():
    """Build a fresh, identical EmbedderConfig per test."""

    from corpus_forge.config import EmbedderConfig

    return EmbedderConfig(
        name="qwen3_8b",
        provider="sentence_transformers",
        model_id="Qwen/Qwen3-Embedding-8B",
        dimension=1024,
        normalize=True,
        distance="cosine",
    )


def test_fingerprint_identical_configs(embedder_cfg):
    """Two identical EmbedderConfigs produce identical fingerprints."""

    from corpus_forge.config import EmbedderConfig
    from corpus_forge.embedders.fingerprint import embedder_fingerprint

    other = EmbedderConfig(
        name="qwen3_8b",
        provider="sentence_transformers",
        model_id="Qwen/Qwen3-Embedding-8B",
        dimension=1024,
        normalize=True,
        distance="cosine",
    )

    fp_a = embedder_fingerprint(embedder_cfg)
    fp_b = embedder_fingerprint(other)

    assert fp_a.full == fp_b.full
    assert fp_a.short == fp_b.short


def test_fingerprint_changes_with_dimension(embedder_cfg):
    """Flipping ``dimension`` flips the fingerprint."""

    from corpus_forge.config import EmbedderConfig
    from corpus_forge.embedders.fingerprint import embedder_fingerprint

    different = EmbedderConfig(
        name=embedder_cfg.name,
        provider=embedder_cfg.provider,
        model_id=embedder_cfg.model_id,
        dimension=768,
        normalize=embedder_cfg.normalize,
        distance=embedder_cfg.distance,
    )

    assert embedder_fingerprint(embedder_cfg).full != embedder_fingerprint(different).full


def test_fingerprint_changes_with_model_id(embedder_cfg):
    """Flipping ``model_id`` flips the fingerprint."""

    from corpus_forge.config import EmbedderConfig
    from corpus_forge.embedders.fingerprint import embedder_fingerprint

    different = EmbedderConfig(
        name=embedder_cfg.name,
        provider=embedder_cfg.provider,
        model_id="BAAI/bge-m3",
        dimension=embedder_cfg.dimension,
        normalize=embedder_cfg.normalize,
        distance=embedder_cfg.distance,
    )

    assert embedder_fingerprint(embedder_cfg).full != embedder_fingerprint(different).full


def test_fingerprint_whitespace_stable(embedder_cfg):
    """Whitespace differences on free-text string fields don't change the fingerprint.

    Pydantic regex-validated fields (``distance``) reject padded input
    before our canonicaliser sees them — the policy here covers
    free-text fields like ``model_id`` that a careless ``config.toml``
    edit might pad.
    """

    from corpus_forge.config import EmbedderConfig
    from corpus_forge.embedders.fingerprint import embedder_fingerprint

    padded = EmbedderConfig(
        name=embedder_cfg.name,
        provider=embedder_cfg.provider,
        model_id="   Qwen/Qwen3-Embedding-8B   ",
        dimension=embedder_cfg.dimension,
        normalize=embedder_cfg.normalize,
        distance=embedder_cfg.distance,
    )

    assert embedder_fingerprint(embedder_cfg).full == embedder_fingerprint(padded).full


def test_fingerprint_short_is_prefix_of_full(embedder_cfg):
    """The ``short`` form is the first 16 hex chars of ``full``."""

    from corpus_forge.embedders.fingerprint import embedder_fingerprint

    fp = embedder_fingerprint(embedder_cfg)
    assert len(fp.short) == 16
    assert len(fp.full) == 64
    assert fp.full.startswith(fp.short)


def test_fingerprint_changes_with_normalize(embedder_cfg):
    """Flipping ``normalize`` flips the fingerprint."""

    from corpus_forge.config import EmbedderConfig
    from corpus_forge.embedders.fingerprint import embedder_fingerprint

    different = EmbedderConfig(
        name=embedder_cfg.name,
        provider=embedder_cfg.provider,
        model_id=embedder_cfg.model_id,
        dimension=embedder_cfg.dimension,
        normalize=False,
        distance=embedder_cfg.distance,
    )

    assert embedder_fingerprint(embedder_cfg).full != embedder_fingerprint(different).full


def test_fingerprint_changes_with_distance(embedder_cfg):
    """Flipping ``distance`` flips the fingerprint."""

    from corpus_forge.config import EmbedderConfig
    from corpus_forge.embedders.fingerprint import embedder_fingerprint

    different = EmbedderConfig(
        name=embedder_cfg.name,
        provider=embedder_cfg.provider,
        model_id=embedder_cfg.model_id,
        dimension=embedder_cfg.dimension,
        normalize=embedder_cfg.normalize,
        distance="l2",
    )

    assert embedder_fingerprint(embedder_cfg).full != embedder_fingerprint(different).full
