"""Phase N Wave 3 — embedder registry dispatch for the new ``model2vec`` provider.

Splits out the registry's provider-dispatch behaviour from the
historical ``test_embedders.py`` so the Wave 3 additions land in a
focused file.  Pins:

- ``EmbedderRegistry.register(provider="model2vec", ...)`` succeeds and
  constructs a :class:`~corpus_forge.embedders.model2vec.Model2VecEmbedder`.
- The ``Config.embedders[*].provider`` Pydantic pattern accepts
  ``"model2vec"`` (already covered by ``test_config_*`` but pinned here
  too as a regression net against accidental tightening of the regex).
- An unknown provider still raises ``ValueError`` — re-asserted so the
  Wave 3 widening of the dispatch dict doesn't silently turn typos
  into no-ops.
"""

from __future__ import annotations

import pytest


def test_registry_dispatches_model2vec_provider() -> None:
    """``register(provider="model2vec", ...)`` returns a Model2VecEmbedder."""
    from corpus_forge.embedders.model2vec import Model2VecEmbedder
    from corpus_forge.embedders.registry import EmbedderRegistry

    registry = EmbedderRegistry()
    embedder = registry.register(
        name="fast-tier",
        provider="model2vec",
        model_id="minishlab/potion-code-16M",
        dimension=256,
    )
    assert isinstance(embedder, Model2VecEmbedder)
    assert embedder.provider == "model2vec"
    assert embedder.dimension == 256
    assert embedder.model_id == "minishlab/potion-code-16M"


def test_registry_model2vec_carries_default_distance() -> None:
    """Model2Vec embedders default to cosine + normalized."""
    from corpus_forge.embedders.registry import EmbedderRegistry

    registry = EmbedderRegistry()
    embedder = registry.register(
        name="fast-tier",
        provider="model2vec",
        model_id="minishlab/potion-code-16M",
        dimension=256,
    )
    assert embedder.distance == "cosine"
    assert embedder.normalized is True


def test_registry_unknown_provider_still_rejected() -> None:
    """The Wave 3 dispatch widening must not turn typos into no-ops."""
    from corpus_forge.embedders.registry import EmbedderRegistry

    registry = EmbedderRegistry()
    with pytest.raises(ValueError, match=r"Unknown embedder provider"):
        registry.register(
            name="typo",
            provider="modelvec",  # missing the "2"
            model_id="minishlab/potion-code-16M",
            dimension=256,
        )


def test_registry_lists_model2vec_alongside_others() -> None:
    """Mixing providers in one registry works."""
    from corpus_forge.embedders.registry import EmbedderRegistry

    registry = EmbedderRegistry()
    registry.register(
        name="main",
        provider="sentence_transformers",
        model_id="some/model",
        dimension=384,
    )
    registry.register(
        name="fast",
        provider="model2vec",
        model_id="minishlab/potion-code-16M",
        dimension=256,
    )
    names = set(registry.list_names())
    assert names == {"main", "fast"}


def test_config_embedder_provider_accepts_model2vec() -> None:
    """``EmbedderConfig.provider`` regex must accept ``"model2vec"``."""
    from corpus_forge.config import EmbedderConfig

    cfg = EmbedderConfig(
        name="fast",
        provider="model2vec",
        model_id="minishlab/potion-code-16M",
        dimension=256,
    )
    assert cfg.provider == "model2vec"
