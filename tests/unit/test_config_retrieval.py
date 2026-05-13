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

    def test_field_set(self):
        rc = self._cls()
        fields = set(rc.model_fields.keys())
        assert fields == {"alpha", "fusion", "default_k", "rerank_top_n", "rerank_enabled"}

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
