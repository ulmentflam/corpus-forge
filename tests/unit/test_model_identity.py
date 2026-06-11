"""Unit tests for canonical model identity + the alias config-load guard
(RFC fleet-6, item 1).

Two halves:

* the pure resolver (`corpus_forge.embedders.identity`) — identity set,
  canonical (min) pair, order-independence, backcompat for the no-alias case;
* the `Config._check_model_aliases` guard — embedders aliased to one identity
  must agree on dimension/normalize/distance, else config load raises.
"""

from __future__ import annotations

import pytest

from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
    ModelAlias,
)
from corpus_forge.embedders.identity import (
    canonical_model_identity,
    canonical_model_key,
    model_identity_pairs,
)

# ── two names for the same model (providers from EmbedderConfig's allow-set) ──
_OPENAI_NAME = ("openai", "text-embedding-nomic-embed-code")
_LLAMA_NAME = ("llama-cpp", "manutic/nomic-embed-code:latest")


def _emb(
    name: str, provider: str, model_id: str, *, dimension: int = 3584, **kw: object
) -> EmbedderConfig:
    return EmbedderConfig(
        name=name, provider=provider, model_id=model_id, dimension=dimension, **kw
    )


# ---------------------------------------------------------------------------
# resolver
# ---------------------------------------------------------------------------


def test_no_alias_canonical_is_own_pair() -> None:
    cfg = _emb("e1", "openai", "text-embedding-nomic-embed-code")
    assert model_identity_pairs(cfg) == {_OPENAI_NAME}
    # Backcompat bar: no aliases → canonical is exactly (provider, model_id).
    assert canonical_model_identity(cfg) == _OPENAI_NAME
    assert canonical_model_key(cfg) == "openai:text-embedding-nomic-embed-code"


def test_identity_set_includes_aliases() -> None:
    cfg = _emb(
        "e1",
        "openai",
        "text-embedding-nomic-embed-code",
        model_aliases=[
            ModelAlias(provider="llama-cpp", model_id="manutic/nomic-embed-code:latest")
        ],
    )
    assert model_identity_pairs(cfg) == {_OPENAI_NAME, _LLAMA_NAME}


def test_canonical_is_min_pair() -> None:
    cfg = _emb(
        "e1",
        "openai",
        "text-embedding-nomic-embed-code",
        model_aliases=[
            ModelAlias(provider="llama-cpp", model_id="manutic/nomic-embed-code:latest")
        ],
    )
    # "llama-cpp:..." sorts before "openai:..." → it is the canonical pair.
    assert min(_OPENAI_NAME, _LLAMA_NAME) == _LLAMA_NAME
    assert canonical_model_identity(cfg) == _LLAMA_NAME
    assert canonical_model_key(cfg) == "llama-cpp:manutic/nomic-embed-code:latest"


def test_canonical_is_host_order_independent() -> None:
    # Host A serves it via openai, declares the llama-cpp alias.
    host_a = _emb(
        "nomic",
        "openai",
        "text-embedding-nomic-embed-code",
        model_aliases=[
            ModelAlias(provider="llama-cpp", model_id="manutic/nomic-embed-code:latest")
        ],
    )
    # Host B serves it via llama-cpp, declares the openai alias.
    host_b = _emb(
        "nomic",
        "llama-cpp",
        "manutic/nomic-embed-code:latest",
        model_aliases=[ModelAlias(provider="openai", model_id="text-embedding-nomic-embed-code")],
    )
    # Same identity set → same canonical identity regardless of which name
    # each host serves as primary.
    assert model_identity_pairs(host_a) == model_identity_pairs(host_b)
    assert canonical_model_identity(host_a) == canonical_model_identity(host_b)


def test_whitespace_is_stripped() -> None:
    cfg = _emb("e1", "openai", "  spaced-model  ")
    assert canonical_model_identity(cfg) == ("openai", "spaced-model")


# ---------------------------------------------------------------------------
# Config._check_model_aliases guard
# ---------------------------------------------------------------------------


def _config(embedders: list[EmbedderConfig]) -> Config:
    return Config(
        backend=BackendConfig(kind="postgres", dsn="postgresql://h/corpus"),
        daemon=DaemonConfig(host_id="test-host"),
        datasets=[
            DatasetConfig(
                name="notes",
                kind="text",
                sources=[
                    DatasetSourceConfig(
                        plugin="markdown_vault", vault_root="~/Notes", chunker="markdown"
                    )
                ],
            )
        ],
        embedders=embedders,
    )


def test_no_aliases_loads_fine() -> None:
    cfg = _config(
        [
            _emb("a", "openai", "model-a", dimension=4096),
            _emb("b", "sentence_transformers", "model-b", dimension=768),
        ]
    )
    assert len(cfg.embedders) == 2


def test_matching_aliased_embedders_ok() -> None:
    cfg = _config(
        [
            _emb(
                "nomic-a",
                "openai",
                "text-embedding-nomic-embed-code",
                dimension=3584,
                model_aliases=[
                    ModelAlias(provider="llama-cpp", model_id="manutic/nomic-embed-code:latest")
                ],
            ),
            _emb("nomic-b", "llama-cpp", "manutic/nomic-embed-code:latest", dimension=3584),
        ]
    )
    assert len(cfg.embedders) == 2


def test_dimension_mismatch_across_aliases_raises() -> None:
    with pytest.raises(ValueError, match="dimension"):
        _config(
            [
                _emb(
                    "nomic-a",
                    "openai",
                    "text-embedding-nomic-embed-code",
                    dimension=3584,
                    model_aliases=[
                        ModelAlias(provider="llama-cpp", model_id="manutic/nomic-embed-code:latest")
                    ],
                ),
                # Same model declared at a different dimension → space conflict.
                _emb("nomic-b", "llama-cpp", "manutic/nomic-embed-code:latest", dimension=768),
            ]
        )


def test_transitive_alias_mismatch_raises() -> None:
    # A aliases B's pair; B declares no alias back. They still form one identity
    # (transitive), so a dimension disagreement must still be caught.
    with pytest.raises(ValueError, match="dimension"):
        _config(
            [
                _emb(
                    "a",
                    "openai",
                    "shared-model",
                    dimension=1024,
                    model_aliases=[
                        ModelAlias(provider="sentence_transformers", model_id="shared-model")
                    ],
                ),
                _emb("b", "sentence_transformers", "shared-model", dimension=512),
            ]
        )


def test_normalize_mismatch_across_aliases_raises() -> None:
    with pytest.raises(ValueError, match="normalize"):
        _config(
            [
                _emb(
                    "a",
                    "openai",
                    "shared-model",
                    dimension=1024,
                    normalize=True,
                    model_aliases=[ModelAlias(provider="llama-cpp", model_id="shared-model")],
                ),
                _emb("b", "llama-cpp", "shared-model", dimension=1024, normalize=False),
            ]
        )


def test_distance_mismatch_across_aliases_raises() -> None:
    with pytest.raises(ValueError, match="distance"):
        _config(
            [
                _emb(
                    "a",
                    "openai",
                    "shared-model",
                    dimension=1024,
                    distance="cosine",
                    model_aliases=[ModelAlias(provider="llama-cpp", model_id="shared-model")],
                ),
                _emb("b", "llama-cpp", "shared-model", dimension=1024, distance="l2"),
            ]
        )
