"""Phase L Wave 5 — compare_active drift detection against a stubbed backend.

Exercises :func:`corpus_forge.embedders.fingerprint.compare_active`:

- No stored row (embedder never registered) → no drift.
- Stored row with matching fingerprint → no drift.
- Stored row with diverging fingerprint → one ``EmbedderDrift`` with the
  expected ``was_*`` / ``now_*`` fields + ``chunks_to_rerun`` summed
  from ``count_existing_embeddings`` and ``count_chunks_missing_embedding``.
- Multiple active embedders are handled independently.
- ``active=False`` entries are skipped.
- ``save_active_fingerprint`` writes the new fingerprint back via
  ``backend.update_embedder_config_blob``.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


def _make_cfg(**overrides):
    from corpus_forge.config import EmbedderConfig

    base = {
        "name": "qwen3_8b",
        "provider": "sentence_transformers",
        "model_id": "Qwen/Qwen3-Embedding-8B",
        "dimension": 1024,
        "normalize": True,
        "distance": "cosine",
        "active": True,
    }
    base.update(overrides)
    return EmbedderConfig(**base)


def _stored_row_matching(cfg) -> dict:
    """Stored embedder row whose config blob matches the given EmbedderConfig."""

    return {
        "id": 7,
        "name": cfg.name,
        "provider": cfg.provider,
        "model_id": cfg.model_id,
        "dimension": cfg.dimension,
        "normalized": cfg.normalize,
        "distance": cfg.distance,
        "table_name": f"embeddings_{cfg.name.replace('-', '_')}",
        "config": {
            "provider": cfg.provider,
            "model_id": cfg.model_id,
            "dimension": cfg.dimension,
            "normalize": cfg.normalize,
            "distance": cfg.distance,
        },
    }


def _stored_row_legacy(cfg, *, model_id_override: str | None = None) -> dict:
    """Stored row using the pre-Wave-5 two-key config blob shape."""

    stored_model_id = model_id_override or cfg.model_id
    return {
        "id": 7,
        "name": cfg.name,
        "provider": cfg.provider,
        "model_id": stored_model_id,
        "dimension": cfg.dimension,
        "normalized": cfg.normalize,
        "distance": cfg.distance,
        "table_name": f"embeddings_{cfg.name.replace('-', '_')}",
        # Legacy two-key shape — must still fingerprint correctly via
        # the top-level column fallback.
        "config": {"provider": cfg.provider, "model_id": stored_model_id},
    }


def _stub_config(*cfgs):
    cfg = MagicMock()
    cfg.embedders = list(cfgs)
    return cfg


def test_compare_active_no_stored_row_returns_empty():
    """Embedder not yet registered → no drift to report."""

    from corpus_forge.embedders.fingerprint import compare_active

    cfg = _make_cfg()
    backend = MagicMock()
    backend.find_embedder_row_by_name.return_value = None

    drifts = compare_active(_stub_config(cfg), backend)

    assert drifts == []


def test_compare_active_matching_fingerprint_returns_empty():
    """Stored row with identical fields → no drift."""

    from corpus_forge.embedders.fingerprint import compare_active

    cfg = _make_cfg()
    backend = MagicMock()
    backend.find_embedder_row_by_name.return_value = _stored_row_matching(cfg)

    drifts = compare_active(_stub_config(cfg), backend)

    assert drifts == []


def test_compare_active_diverging_fingerprint_returns_drift():
    """Stored row differs → one EmbedderDrift with summed chunks."""

    from corpus_forge.embedders.fingerprint import compare_active

    cfg = _make_cfg()  # NOW=Qwen3
    # Stored row was bge-m3 (different model_id, legacy config blob).
    stored = _stored_row_legacy(cfg, model_id_override="BAAI/bge-m3")
    backend = MagicMock()
    backend.find_embedder_row_by_name.return_value = stored
    backend.count_existing_embeddings.return_value = 12000
    backend.count_chunks_missing_embedding.return_value = 481

    drifts = compare_active(_stub_config(cfg), backend)

    assert len(drifts) == 1
    drift = drifts[0]
    assert drift.name == "qwen3_8b"
    assert drift.was_model_id == "BAAI/bge-m3"
    assert drift.now_model_id == "Qwen/Qwen3-Embedding-8B"
    assert drift.was_dimension == 1024
    assert drift.now_dimension == 1024
    assert drift.chunks_to_rerun == 12000 + 481
    # est_seconds = chunks_to_rerun * default 0.034
    assert drift.est_seconds == pytest.approx((12000 + 481) * 0.034, rel=1e-6)
    # Fingerprints are non-empty short forms.
    assert drift.fingerprint_was and drift.fingerprint_now
    assert drift.fingerprint_was != drift.fingerprint_now


def test_compare_active_handles_multiple_actives():
    """Two active embedders with mixed drift state — only diverging ones returned."""

    from corpus_forge.embedders.fingerprint import compare_active

    cfg_a = _make_cfg(name="qwen3_8b")
    cfg_b = _make_cfg(name="bge_m3", model_id="BAAI/bge-m3")

    def _lookup(name):
        if name == "qwen3_8b":
            # Matching stored row.
            return _stored_row_matching(cfg_a)
        if name == "bge_m3":
            # Stored model_id differs from current.
            return _stored_row_legacy(cfg_b, model_id_override="BAAI/bge-base-en-v1.5")
        return None

    backend = MagicMock()
    backend.find_embedder_row_by_name.side_effect = _lookup
    backend.count_existing_embeddings.return_value = 10
    backend.count_chunks_missing_embedding.return_value = 3

    drifts = compare_active(_stub_config(cfg_a, cfg_b), backend)

    assert len(drifts) == 1
    assert drifts[0].name == "bge_m3"
    assert drifts[0].was_model_id == "BAAI/bge-base-en-v1.5"


def test_compare_active_inactive_skipped():
    """``active=False`` entries are silently skipped."""

    from corpus_forge.embedders.fingerprint import compare_active

    cfg = _make_cfg(active=False)
    backend = MagicMock()
    backend.find_embedder_row_by_name.return_value = _stored_row_legacy(
        cfg, model_id_override="something-else"
    )

    drifts = compare_active(_stub_config(cfg), backend)

    assert drifts == []
    # Backend should not be consulted for inactive embedders.
    backend.find_embedder_row_by_name.assert_not_called()


def test_compare_active_env_override_changes_est_seconds(monkeypatch):
    """``CF_REEMBED_SECONDS_PER_CHUNK`` env tunes ``est_seconds``."""

    from corpus_forge.embedders.fingerprint import compare_active

    monkeypatch.setenv("CF_REEMBED_SECONDS_PER_CHUNK", "0.1")

    cfg = _make_cfg()
    stored = _stored_row_legacy(cfg, model_id_override="BAAI/bge-m3")
    backend = MagicMock()
    backend.find_embedder_row_by_name.return_value = stored
    backend.count_existing_embeddings.return_value = 100
    backend.count_chunks_missing_embedding.return_value = 0

    drifts = compare_active(_stub_config(cfg), backend)

    assert len(drifts) == 1
    assert drifts[0].est_seconds == pytest.approx(100 * 0.1, rel=1e-6)


def test_compare_active_handles_json_string_config_blob():
    """SQLite stores ``config`` as a JSON string — must parse correctly."""

    from corpus_forge.embedders.fingerprint import compare_active

    cfg = _make_cfg()
    stored = _stored_row_matching(cfg)
    # sqlite-style: serialize config to a JSON string.
    stored["config"] = json.dumps(stored["config"])
    backend = MagicMock()
    backend.find_embedder_row_by_name.return_value = stored

    drifts = compare_active(_stub_config(cfg), backend)

    # JSON-string config still matches → no drift.
    assert drifts == []


def test_save_active_fingerprint_writes_config_blob():
    """``save_active_fingerprint`` updates the stored config blob."""

    from corpus_forge.embedders.fingerprint import (
        embedder_fingerprint,
        save_active_fingerprint,
    )

    cfg = _make_cfg()
    backend = MagicMock()
    backend.find_embedder_row_by_name.return_value = _stored_row_matching(cfg)

    save_active_fingerprint(_stub_config(cfg), backend)

    backend.update_embedder_config_blob.assert_called_once()
    args, _ = backend.update_embedder_config_blob.call_args
    embedder_id, blob = args
    assert embedder_id == 7
    assert blob["provider"] == cfg.provider
    assert blob["model_id"] == cfg.model_id
    assert blob["dimension"] == cfg.dimension
    assert blob["normalize"] == cfg.normalize
    assert blob["distance"] == cfg.distance
    # Fingerprint embedded for future debug / audit.
    assert blob["fingerprint"] == embedder_fingerprint(cfg).full


def test_compare_active_returns_list_never_none():
    """``compare_active`` returns an empty list, never None."""

    from corpus_forge.embedders.fingerprint import compare_active

    backend = MagicMock()
    backend.find_embedder_row_by_name.return_value = None

    result = compare_active(_stub_config(), backend)

    assert result == []
    assert isinstance(result, list)
