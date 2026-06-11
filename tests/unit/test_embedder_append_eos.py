"""Unit tests for the known-embedder registry + ``append_eos`` config flag
(RFC embedder-eos item 1).

Two layers:

- ``corpus_forge.embedders.known_models`` — model-id normalization across
  the names a single model is served under, and the append-eos resolution
  precedence (explicit flag > registry default > False).
- ``EmbedderConfig.append_eos`` / ``effective_append_eos()`` — the config
  field (three-state default/override/round-trip) and the glue method.
"""

from __future__ import annotations

import pytest

from corpus_forge.config import EmbedderConfig
from corpus_forge.embedders.known_models import (
    KnownEmbedder,
    lookup_known_embedder,
    normalize_model_id,
    resolve_append_eos,
)


def _embedder(model_id: str, append_eos: bool | None = None) -> EmbedderConfig:
    return EmbedderConfig(
        name="t",
        provider="llama-cpp",
        model_id=model_id,
        dimension=768,
        append_eos=append_eos,
    )


# ── normalization ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("served", "expected"),
    [
        ("manutic/nomic-embed-code:latest", "nomic-embed-code"),
        ("nomic-embed-code:7b", "nomic-embed-code"),
        ("text-embedding-nomic-embed-code", "nomic-embed-code"),
        ("nomic-embed-text:v1.5", "nomic-embed-text"),
        ("Qwen/Qwen3-Embedding-8B", "qwen3-embedding-8b"),
        ("  nomic-embed-text  ", "nomic-embed-text"),  # stripped + lowercased
    ],
)
def test_normalize_collapses_served_name_variants(served: str, expected: str) -> None:
    assert normalize_model_id(served) == expected


def test_aliased_names_resolve_to_one_family() -> None:
    # The cross-transport agreement fleet-6's shared lane depends on: the
    # three names for nomic-embed-code all hit the same registry entry.
    entries = {
        lookup_known_embedder(mid)
        for mid in (
            "manutic/nomic-embed-code:latest",
            "nomic-embed-code:7b",
            "text-embedding-nomic-embed-code",
        )
    }
    assert entries == {KnownEmbedder("nomic-embed-code", append_eos=True)}


def test_code_family_wins_over_generic_nomic() -> None:
    # Most-specific-first ordering: a code id must not match the generic
    # nomic-embed entry first.
    entry = lookup_known_embedder("nomic-embed-code:7b")
    assert entry is not None
    assert entry.family == "nomic-embed-code"


def test_unknown_model_has_no_registry_entry() -> None:
    assert lookup_known_embedder("Qwen/Qwen3-Embedding-8B") is None
    assert lookup_known_embedder("text-embedding-3-small") is None


# ── resolution precedence ──────────────────────────────────────────────


def test_resolve_explicit_flag_wins_over_registry() -> None:
    # Explicit False on a nomic model overrides the registry's True.
    assert resolve_append_eos("nomic-embed-code:latest", explicit=False) is False
    assert resolve_append_eos("Qwen/Qwen3-Embedding-8B", explicit=True) is True


def test_resolve_registry_default_when_unset() -> None:
    assert resolve_append_eos("nomic-embed-text:v1.5", explicit=None) is True


def test_resolve_unknown_model_defaults_false() -> None:
    assert resolve_append_eos("text-embedding-3-small", explicit=None) is False


# ── config field + glue method ─────────────────────────────────────────


def test_append_eos_field_defaults_none() -> None:
    assert _embedder("nomic-embed-text").append_eos is None


@pytest.mark.parametrize("value", [True, False])
def test_append_eos_field_accepts_explicit_bool(value: bool) -> None:
    assert _embedder("nomic-embed-text", append_eos=value).append_eos is value


def test_effective_append_eos_consults_registry_when_unset() -> None:
    assert _embedder("manutic/nomic-embed-code:latest").effective_append_eos() is True
    assert _embedder("Qwen/Qwen3-Embedding-8B").effective_append_eos() is False


def test_effective_append_eos_honours_explicit_override() -> None:
    # Explicit False on a nomic model and explicit True on an unknown one.
    assert _embedder("nomic-embed-code:7b", append_eos=False).effective_append_eos() is False
    assert _embedder("Qwen/Qwen3-Embedding-8B", append_eos=True).effective_append_eos() is True


def test_append_eos_round_trips_through_model_dump() -> None:
    cfg = _embedder("nomic-embed-text", append_eos=True)
    restored = EmbedderConfig(**cfg.model_dump())
    assert restored.append_eos is True
    assert restored.effective_append_eos() is True
