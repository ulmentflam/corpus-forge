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


# ── coverage push: edge branches in _stored_fingerprint / helpers ────────


def test_seconds_per_chunk_env_override_invalid_falls_back(monkeypatch):
    """Bad ``CF_REEMBED_SECONDS_PER_CHUNK`` falls back to the default."""

    from corpus_forge.embedders.fingerprint import _seconds_per_chunk

    monkeypatch.setenv("CF_REEMBED_SECONDS_PER_CHUNK", "not-a-float")
    # Falls back to the default (0.034) when the env value can't parse.
    assert _seconds_per_chunk() == pytest.approx(0.034)


def test_seconds_per_chunk_env_override_valid(monkeypatch):
    """Well-formed env override wins over the default."""

    from corpus_forge.embedders.fingerprint import _seconds_per_chunk

    monkeypatch.setenv("CF_REEMBED_SECONDS_PER_CHUNK", "0.1")
    assert _seconds_per_chunk() == pytest.approx(0.1)


def test_stored_fingerprint_string_blob_json_decoded():
    """SQLite stores ``config`` as a JSON string — must be decoded transparently."""

    import json as _json

    from corpus_forge.embedders.fingerprint import _stored_fingerprint

    row = {
        "provider": "sentence_transformers",
        "model_id": "Qwen/Qwen3-Embedding-8B",
        "dimension": 1024,
        "normalized": 1,  # SQLite INTEGER
        "distance": "cosine",
        "config": _json.dumps(
            {
                "provider": "sentence_transformers",
                "model_id": "Qwen/Qwen3-Embedding-8B",
                "dimension": 1024,
                "normalize": True,
                "distance": "cosine",
            }
        ),
    }
    fp = _stored_fingerprint(row)
    assert len(fp.full) == 64


def test_stored_fingerprint_malformed_json_string_falls_back():
    """Malformed JSON in the ``config`` column falls back to the top-level columns."""

    from corpus_forge.embedders.fingerprint import _stored_fingerprint

    row = {
        "provider": "sentence_transformers",
        "model_id": "Qwen/Qwen3-Embedding-8B",
        "dimension": 1024,
        "normalized": True,
        "distance": "cosine",
        "config": "{not valid json",
    }
    fp = _stored_fingerprint(row)
    # Just make sure we got a real hash without raising.
    assert len(fp.full) == 64


def test_stored_fingerprint_non_dict_config_falls_back():
    """Non-dict ``config`` (e.g. a JSON list) is ignored cleanly."""

    import json as _json

    from corpus_forge.embedders.fingerprint import _stored_fingerprint

    row = {
        "provider": "sentence_transformers",
        "model_id": "Qwen/Qwen3-Embedding-8B",
        "dimension": 1024,
        "normalized": True,
        "distance": "cosine",
        "config": _json.dumps(["unexpected", "shape"]),
    }
    fp = _stored_fingerprint(row)
    assert len(fp.full) == 64


def test_stored_fingerprint_none_dimension_coerces_to_zero():
    """A null dimension column is coerced to 0 rather than raising."""

    from corpus_forge.embedders.fingerprint import _stored_fingerprint

    row = {
        "provider": "sentence_transformers",
        "model_id": "Qwen/Qwen3-Embedding-8B",
        "dimension": None,
        "normalized": True,
        "distance": "cosine",
        "config": {},
    }
    fp = _stored_fingerprint(row)
    assert len(fp.full) == 64


def test_count_existing_swallows_attribute_error():
    """`_count_existing` returns 0 when the backend lacks the helper."""

    from corpus_forge.embedders.fingerprint import _count_existing

    class _NoHelpers:
        pass

    assert _count_existing(_NoHelpers(), 1) == 0


def test_count_missing_swallows_attribute_error():
    """`_count_missing` returns 0 when the backend lacks the helper."""

    from corpus_forge.embedders.fingerprint import _count_missing

    class _NoHelpers:
        pass

    assert _count_missing(_NoHelpers(), 1) == 0


def test_save_active_fingerprint_skips_backend_without_blob_helper():
    """`save_active_fingerprint` silently degrades when the backend is pre-Wave-5."""

    from unittest.mock import MagicMock

    from corpus_forge.embedders.fingerprint import save_active_fingerprint

    class _LegacyBackend:
        def find_embedder_row_by_name(self, name: str):
            return {"id": 1, "name": name}

        # No update_embedder_config_blob method → AttributeError swallowed.

    cfg = MagicMock()
    cfg.embedders = [
        MagicMock(
            name="e1",
            provider="openai",
            model_id="text-embedding-3-small",
            dimension=1536,
            normalize=True,
            distance="cosine",
            active=True,
        )
    ]
    # Configure the MagicMock's ``name`` attribute (MagicMock auto-assigns
    # this from the constructor, so we have to overwrite it manually).
    cfg.embedders[0].name = "e1"
    save_active_fingerprint(cfg, _LegacyBackend())  # must not raise


# ── RFC embedder-eos: append_eos folds into the fingerprint ────────────────


def _cfg(**kw):
    """EmbedderConfig with EOS-relevant defaults, overridable per test."""
    from corpus_forge.config import EmbedderConfig

    base = {
        "name": "nomic-code",
        "provider": "llama-cpp",
        "model_id": "manutic/nomic-embed-code:latest",
        "dimension": 3584,
        "normalize": True,
        "distance": "cosine",
    }
    base.update(kw)
    return EmbedderConfig(**base)


def test_append_eos_true_changes_fingerprint():
    """Enabling append_eos drifts the fingerprint (so a re-embed is forced)."""
    from corpus_forge.embedders.fingerprint import embedder_fingerprint

    off = embedder_fingerprint(_cfg(model_id="some-model:1", append_eos=False))
    on = embedder_fingerprint(_cfg(model_id="some-model:1", append_eos=True))
    assert off.full != on.full


def test_append_eos_false_matches_pre_rfc_five_field_hash():
    """append_eos=False is byte-identical to the legacy 5-field formula —
    no spurious drift for models that never wanted a terminator."""
    from corpus_forge.embedders.fingerprint import _hash, embedder_fingerprint

    cfg = _cfg(model_id="plain-model:1", append_eos=False)
    legacy = _hash("llama-cpp", "plain-model:1", 3584, True, "cosine")
    assert embedder_fingerprint(cfg).full == legacy.full


def test_append_eos_inferred_true_for_nomic_via_registry():
    """With append_eos unset, the nomic family resolves True from the
    known-model registry and so fingerprints differently from a model that
    resolves False."""
    from corpus_forge.embedders.fingerprint import embedder_fingerprint

    nomic = embedder_fingerprint(_cfg(model_id="manutic/nomic-embed-code:latest"))
    # Same five base fields, but an unknown model resolves append_eos False.
    plain = embedder_fingerprint(_cfg(model_id="mystery-embed:1"))
    assert nomic.full != plain.full


def test_stored_fingerprint_reads_append_eos_from_blob():
    """A stored blob carrying append_eos=True recomputes to the True-form,
    so drift settles after a re-embed persists the flag."""
    from corpus_forge.embedders.fingerprint import _stored_fingerprint, embedder_fingerprint

    cfg = _cfg(model_id="x:1", append_eos=True)
    fp_live = embedder_fingerprint(cfg)
    row_with = {
        "provider": "llama-cpp",
        "model_id": "x:1",
        "dimension": 3584,
        "config": {
            "provider": "llama-cpp",
            "model_id": "x:1",
            "dimension": 3584,
            "normalize": True,
            "distance": "cosine",
            "append_eos": True,
        },
    }
    assert _stored_fingerprint(row_with).full == fp_live.full


def test_stored_fingerprint_legacy_blob_drifts_against_enabled_config():
    """A legacy blob (no append_eos) recomputes to the False-form and so
    drifts against a now-append_eos=True config — this is what forces the
    one-time re-embed migration."""
    from corpus_forge.embedders.fingerprint import _stored_fingerprint, embedder_fingerprint

    cfg = _cfg(model_id="x:1", append_eos=True)
    fp_live = embedder_fingerprint(cfg)
    legacy_row = {
        "provider": "llama-cpp",
        "model_id": "x:1",
        "dimension": 3584,
        "config": {
            "provider": "llama-cpp",
            "model_id": "x:1",
            "dimension": 3584,
            "normalize": True,
            "distance": "cosine",
        },
    }
    assert _stored_fingerprint(legacy_row).full != fp_live.full
