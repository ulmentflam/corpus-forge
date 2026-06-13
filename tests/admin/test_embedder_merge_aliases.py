"""RFC fleet-6 item 4 (detection half) — `embedder merge-aliases` split finder.

`_split_identity_groups` is read-only: it groups `corpus.models` rows by the
canonical identity the active alias config resolves them to, and returns the
canonicals recorded under more than one `model_key` (the same model split
across two provider names — what the operator declares aliases to unify).
"""

from __future__ import annotations

from types import SimpleNamespace

from corpus_forge.admin.embedder import _split_identity_groups
from corpus_forge.config import EmbedderConfig, ModelAlias


def _config_with_alias():
    ec = EmbedderConfig(
        name="nomic",
        provider="openai",
        model_id="text-nomic",
        dimension=768,
        model_aliases=[ModelAlias(provider="llama-cpp", model_id="nomic-code")],
    )
    # canonical = min{(openai,text-nomic),(llama-cpp,nomic-code)} = llama-cpp:nomic-code
    return SimpleNamespace(embedders=[ec])


class _StubBackend:
    def __init__(self, rows):
        self._rows = rows

    def list_models_with_latest_benchmark(self):
        return self._rows


def test_detects_split_across_alias_names():
    cfg = _config_with_alias()
    backend = _StubBackend(
        [
            {"model_key": "openai:text-nomic", "provider": "openai", "model_id": "text-nomic"},
            {
                "model_key": "llama-cpp:nomic-code",
                "provider": "llama-cpp",
                "model_id": "nomic-code",
            },
        ]
    )
    splits = _split_identity_groups(cfg, backend)
    assert set(splits) == {"llama-cpp:nomic-code"}  # both rows fold to the canonical
    assert len(splits["llama-cpp:nomic-code"]) == 2


def test_no_split_when_single_key():
    cfg = _config_with_alias()
    backend = _StubBackend(
        [{"model_key": "llama-cpp:nomic-code", "provider": "llama-cpp", "model_id": "nomic-code"}]
    )
    assert _split_identity_groups(cfg, backend) == {}


def test_unaliased_distinct_models_are_not_a_split():
    # Two genuinely different models (no alias relationship) → not grouped.
    cfg = SimpleNamespace(
        embedders=[
            EmbedderConfig(name="a", provider="openai", model_id="m-a", dimension=768),
            EmbedderConfig(name="b", provider="openai", model_id="m-b", dimension=768),
        ]
    )
    backend = _StubBackend(
        [
            {"model_key": "openai:m-a", "provider": "openai", "model_id": "m-a"},
            {"model_key": "openai:m-b", "provider": "openai", "model_id": "m-b"},
        ]
    )
    assert _split_identity_groups(cfg, backend) == {}
