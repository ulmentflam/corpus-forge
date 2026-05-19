"""R2-01 — `RetrievalConfig` pydantic model pins.

The Phase R2 plan adds a `RetrievalConfig` model to `corpus_forge.config`:

```python
class RetrievalConfig(BaseModel):
    alpha: float = 0.5
    fusion: Literal["rrf", "alpha"] = "rrf"
    default_k: int = 10
    rerank_top_n: int = 50
    rerank_enabled: bool = False
```

…and attaches it to the top-level `Config` model as
`retrieval: RetrievalConfig = RetrievalConfig()`.

This test file pins:
- model importable from `corpus_forge.config`
- defaults match the plan verbatim
- alpha validated to `[0, 1]`
- fusion is a `Literal["rrf", "alpha"]`
- `Config(...).retrieval` defaults to a `RetrievalConfig()` if omitted
- `Config(...)` round-trips `retrieval={"alpha": 0.7, ...}` correctly
"""

from __future__ import annotations

import typing
from typing import get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

# ── presence ──────────────────────────────────────────────────────────────


def test_retrieval_config_importable():
    from corpus_forge.config import RetrievalConfig  # noqa: F401


# ── defaults ──────────────────────────────────────────────────────────────


class TestRetrievalConfigDefaults:
    def _cls(self):
        from corpus_forge.config import RetrievalConfig

        return RetrievalConfig

    def test_defaults_match_plan(self):
        rc = self._cls()()
        assert rc.alpha == 0.5
        assert rc.fusion == "rrf"
        assert rc.default_k == 10
        assert rc.rerank_top_n == 50
        assert rc.rerank_enabled is False
        # Phase N Wave 1 — adaptive lexical-weight knobs default OFF.
        assert rc.adaptive_lexical_weight is False
        assert rc.symbol_query_alpha == 0.3
        # Phase N Wave 2 — definition-boost knobs default OFF.
        assert rc.definition_boost_enabled is False
        assert rc.definition_boost_factor_pre_rerank == 1.5
        assert rc.definition_boost_factor_post_rerank == 1.2

    def test_field_set(self):
        rc = self._cls()
        fields = set(rc.model_fields.keys())
        # R4 adds `reranker` (RerankerConfig nested); Phase N Wave 1 adds
        # `adaptive_lexical_weight` + `symbol_query_alpha`; Phase N Wave 2
        # adds the three definition-boost knobs.  Phase N Wave 3 adds
        # `fast_tier_embedder_name` for the static fast-tier candidate
        # generator.  The R2 fields stay.
        assert fields == {
            "alpha",
            "fusion",
            "default_k",
            "rerank_top_n",
            "rerank_enabled",
            "reranker",
            "adaptive_lexical_weight",
            "symbol_query_alpha",
            "definition_boost_enabled",
            "definition_boost_factor_pre_rerank",
            "definition_boost_factor_post_rerank",
            "fast_tier_embedder_name",
        }

    def test_fusion_literal_values(self):
        rc = self._cls()
        hints = get_type_hints(rc, include_extras=False)
        fusion_t = hints["fusion"]
        allowed = set(get_args(fusion_t)) if get_origin(fusion_t) is typing.Literal else set()
        assert allowed == {"rrf", "alpha"}


# ── validation ────────────────────────────────────────────────────────────


class TestRetrievalConfigValidation:
    def _cls(self):
        from corpus_forge.config import RetrievalConfig

        return RetrievalConfig

    def test_alpha_in_range(self):
        rc = self._cls()(alpha=0.0)
        assert rc.alpha == 0.0
        rc = self._cls()(alpha=1.0)
        assert rc.alpha == 1.0

    def test_alpha_too_high_rejected(self):
        with pytest.raises(ValidationError):
            self._cls()(alpha=1.5)

    def test_alpha_negative_rejected(self):
        with pytest.raises(ValidationError):
            self._cls()(alpha=-0.1)

    def test_fusion_invalid_rejected(self):
        with pytest.raises(ValidationError):
            self._cls()(fusion="cosine")  # type: ignore[arg-type]

    def test_default_k_positive(self):
        with pytest.raises(ValidationError):
            self._cls()(default_k=0)
        with pytest.raises(ValidationError):
            self._cls()(default_k=-1)

    def test_rerank_top_n_positive(self):
        with pytest.raises(ValidationError):
            self._cls()(rerank_top_n=0)

    # ── Phase N Wave 1 — adaptive lexical-weight bump ──────────────────────

    def test_adaptive_lexical_weight_accepts_bool(self):
        rc = self._cls()(adaptive_lexical_weight=True)
        assert rc.adaptive_lexical_weight is True
        rc = self._cls()(adaptive_lexical_weight=False)
        assert rc.adaptive_lexical_weight is False

    def test_symbol_query_alpha_in_range(self):
        rc = self._cls()(symbol_query_alpha=0.0)
        assert rc.symbol_query_alpha == 0.0
        rc = self._cls()(symbol_query_alpha=1.0)
        assert rc.symbol_query_alpha == 1.0

    def test_symbol_query_alpha_negative_rejected(self):
        with pytest.raises(ValidationError):
            self._cls()(symbol_query_alpha=-0.1)

    def test_symbol_query_alpha_too_high_rejected(self):
        with pytest.raises(ValidationError):
            self._cls()(symbol_query_alpha=1.5)

    # ── Phase N Wave 2 — definition-boost knobs ────────────────────────────

    def test_definition_boost_enabled_accepts_bool(self):
        rc = self._cls()(definition_boost_enabled=True)
        assert rc.definition_boost_enabled is True
        rc = self._cls()(definition_boost_enabled=False)
        assert rc.definition_boost_enabled is False

    def test_definition_boost_factor_pre_rerank_in_range(self):
        rc = self._cls()(definition_boost_factor_pre_rerank=1.0)
        assert rc.definition_boost_factor_pre_rerank == 1.0
        rc = self._cls()(definition_boost_factor_pre_rerank=5.0)
        assert rc.definition_boost_factor_pre_rerank == 5.0

    def test_definition_boost_factor_pre_rerank_below_one_rejected(self):
        # The boost is a multiplier — < 1.0 would be a penalty, not a boost.
        with pytest.raises(ValidationError):
            self._cls()(definition_boost_factor_pre_rerank=0.5)

    def test_definition_boost_factor_pre_rerank_too_high_rejected(self):
        with pytest.raises(ValidationError):
            self._cls()(definition_boost_factor_pre_rerank=5.5)

    def test_definition_boost_factor_post_rerank_in_range(self):
        rc = self._cls()(definition_boost_factor_post_rerank=1.0)
        assert rc.definition_boost_factor_post_rerank == 1.0
        rc = self._cls()(definition_boost_factor_post_rerank=5.0)
        assert rc.definition_boost_factor_post_rerank == 5.0

    def test_definition_boost_factor_post_rerank_below_one_rejected(self):
        with pytest.raises(ValidationError):
            self._cls()(definition_boost_factor_post_rerank=0.99)

    def test_definition_boost_factor_post_rerank_too_high_rejected(self):
        with pytest.raises(ValidationError):
            self._cls()(definition_boost_factor_post_rerank=5.1)


# ── attaches to top-level Config ──────────────────────────────────────────


_MINIMAL_TOML = {
    "backend": {
        "kind": "sqlite",
        "dsn": "/tmp/test.db",
    },
    "daemon": {},
    "datasets": [
        {
            "name": "ds",
            "kind": "text",
            "sources": [
                {
                    "plugin": "vault",
                    "vault_root": "/tmp",
                    "chunker": "markdown",
                }
            ],
        }
    ],
    "embedders": [
        {
            "name": "e1",
            "provider": "sentence_transformers",
            "model_id": "test/m",
            "dimension": 8,
        }
    ],
}


class TestConfigAttachment:
    def test_config_has_retrieval_attribute(self):
        from corpus_forge.config import Config, RetrievalConfig

        cfg = Config(**_MINIMAL_TOML)
        assert isinstance(cfg.retrieval, RetrievalConfig)

    def test_config_retrieval_defaults_when_omitted(self):
        from corpus_forge.config import Config

        cfg = Config(**_MINIMAL_TOML)
        assert cfg.retrieval.alpha == 0.5
        assert cfg.retrieval.fusion == "rrf"
        assert cfg.retrieval.default_k == 10
        assert cfg.retrieval.rerank_top_n == 50
        assert cfg.retrieval.rerank_enabled is False
        # Phase N Wave 1 defaults — bump OFF, default value parsed but unused.
        assert cfg.retrieval.adaptive_lexical_weight is False
        assert cfg.retrieval.symbol_query_alpha == 0.3
        # Phase N Wave 2 defaults — boost OFF, multipliers documented.
        assert cfg.retrieval.definition_boost_enabled is False
        assert cfg.retrieval.definition_boost_factor_pre_rerank == 1.5
        assert cfg.retrieval.definition_boost_factor_post_rerank == 1.2

    def test_config_retrieval_phase_n_wave2_overrides_via_toml(self):
        from corpus_forge.config import Config

        toml = dict(_MINIMAL_TOML)
        toml["retrieval"] = {  # type: ignore[assignment]
            "definition_boost_enabled": True,
            "definition_boost_factor_pre_rerank": 1.8,
            "definition_boost_factor_post_rerank": 1.3,
        }
        cfg = Config(**toml)
        assert cfg.retrieval.definition_boost_enabled is True
        assert cfg.retrieval.definition_boost_factor_pre_rerank == 1.8
        assert cfg.retrieval.definition_boost_factor_post_rerank == 1.3

    def test_config_retrieval_phase_n_overrides_via_toml(self):
        from corpus_forge.config import Config

        toml = dict(_MINIMAL_TOML)
        toml["retrieval"] = {  # type: ignore[assignment]
            "adaptive_lexical_weight": True,
            "symbol_query_alpha": 0.25,
        }
        cfg = Config(**toml)
        assert cfg.retrieval.adaptive_lexical_weight is True
        assert cfg.retrieval.symbol_query_alpha == 0.25

    def test_config_retrieval_overridable_via_toml(self):
        from corpus_forge.config import Config

        toml = dict(_MINIMAL_TOML)
        toml["retrieval"] = {  # type: ignore[assignment]
            "alpha": 0.8,
            "fusion": "alpha",
            "default_k": 25,
            "rerank_top_n": 100,
            "rerank_enabled": True,
        }
        cfg = Config(**toml)
        assert cfg.retrieval.alpha == 0.8
        assert cfg.retrieval.fusion == "alpha"
        assert cfg.retrieval.default_k == 25
        assert cfg.retrieval.rerank_top_n == 100
        assert cfg.retrieval.rerank_enabled is True


# ── R4-02: `RerankerConfig` (cross-encoder + ollama shape) ────────────────


class TestRerankerConfigDefaults:
    """Pin the new `RerankerConfig` model added in R4."""

    def _cls(self):
        from corpus_forge.config import RerankerConfig

        return RerankerConfig

    def test_importable(self):
        from corpus_forge.config import RerankerConfig  # noqa: F401

    def test_defaults_match_plan(self):
        rc = self._cls()()
        # Default model is the locked decision: BAAI/bge-reranker-v2-m3.
        assert rc.kind == "cross_encoder"
        assert rc.model_id == "BAAI/bge-reranker-v2-m3"
        assert rc.device == "auto"
        assert rc.batch_size == 32
        assert rc.max_length == 512

    def test_field_set(self):
        rc = self._cls()
        fields = set(rc.model_fields.keys())
        assert fields == {"kind", "model_id", "device", "batch_size", "max_length"}

    def test_kind_literal_values(self):
        rc = self._cls()
        hints = get_type_hints(rc, include_extras=False)
        kind_t = hints["kind"]
        allowed = set(get_args(kind_t)) if get_origin(kind_t) is typing.Literal else set()
        assert allowed == {"cross_encoder", "ollama"}, (
            f"RerankerConfig.kind Literal must be {{cross_encoder, ollama}}; got {allowed!r}"
        )


class TestRerankerConfigValidation:
    def _cls(self):
        from corpus_forge.config import RerankerConfig

        return RerankerConfig

    def test_kind_invalid_rejected(self):
        with pytest.raises(ValidationError):
            self._cls()(kind="not_a_real_kind")  # type: ignore[arg-type]

    def test_batch_size_positive(self):
        with pytest.raises(ValidationError):
            self._cls()(batch_size=0)
        with pytest.raises(ValidationError):
            self._cls()(batch_size=-1)

    def test_max_length_positive(self):
        with pytest.raises(ValidationError):
            self._cls()(max_length=0)

    def test_model_id_overridable(self):
        rc = self._cls()(model_id="cross-encoder/ms-marco-MiniLM-L-12-v2")
        assert rc.model_id == "cross-encoder/ms-marco-MiniLM-L-12-v2"

    def test_ollama_kind_accepted(self):
        rc = self._cls()(kind="ollama", model_id="qwen3:8b")
        assert rc.kind == "ollama"
        assert rc.model_id == "qwen3:8b"


class TestRetrievalConfigEmbedsReranker:
    """`RetrievalConfig` now carries a `reranker: RerankerConfig` field."""

    def test_retrieval_config_has_reranker_attr(self):
        from corpus_forge.config import RerankerConfig, RetrievalConfig

        rc = RetrievalConfig()
        assert isinstance(rc.reranker, RerankerConfig)

    def test_retrieval_config_default_reranker_defaults(self):
        from corpus_forge.config import RetrievalConfig

        rc = RetrievalConfig()
        # Round-trip the locked default through both layers.
        assert rc.reranker.kind == "cross_encoder"
        assert rc.reranker.model_id == "BAAI/bge-reranker-v2-m3"

    def test_retrieval_config_reranker_overridable(self):
        from corpus_forge.config import RetrievalConfig

        rc = RetrievalConfig(reranker={"kind": "cross_encoder", "model_id": "X/Y", "batch_size": 8})
        assert rc.reranker.kind == "cross_encoder"
        assert rc.reranker.model_id == "X/Y"
        assert rc.reranker.batch_size == 8

    def test_config_round_trips_nested_reranker(self):
        from corpus_forge.config import Config

        toml = dict(_MINIMAL_TOML)
        toml["retrieval"] = {  # type: ignore[assignment]
            "alpha": 0.5,
            "fusion": "rrf",
            "reranker": {
                "kind": "cross_encoder",
                "model_id": "cross-encoder/ms-marco-MiniLM-L-12-v2",
                "device": "cpu",
                "batch_size": 16,
                "max_length": 256,
            },
        }
        cfg = Config(**toml)
        assert cfg.retrieval.reranker.kind == "cross_encoder"
        assert cfg.retrieval.reranker.model_id == "cross-encoder/ms-marco-MiniLM-L-12-v2"
        assert cfg.retrieval.reranker.device == "cpu"
        assert cfg.retrieval.reranker.batch_size == 16
        assert cfg.retrieval.reranker.max_length == 256
