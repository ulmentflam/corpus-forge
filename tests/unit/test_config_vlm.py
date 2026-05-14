"""Phase D / Wave 4 (E-04) — :class:`VLMConfig` pydantic surface.

The model is purely additive — it attaches as an optional ``vlm`` field
on :class:`Config` (default-factory'd to a Noop-backed
:class:`VLMConfig`). Existing config tests must remain green; default
config has ``vlm.backend == "none"`` so markdown_vault / claude_code /
opencode pipelines are untouched.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
    VLMConfig,
)

# ── VLMConfig defaults ──────────────────────────────────────────────────


def test_vlm_config_default_backend_is_none():
    cfg = VLMConfig()
    assert cfg.backend == "none"


def test_vlm_config_default_ollama_model():
    cfg = VLMConfig()
    assert cfg.ollama_model == "qwen2.5vl:7b"


def test_vlm_config_default_ollama_url():
    cfg = VLMConfig()
    # pydantic AnyHttpUrl normalises with a trailing slash.
    assert str(cfg.ollama_url).startswith("http://localhost:11434")


def test_vlm_config_default_mistral_model():
    cfg = VLMConfig()
    assert cfg.mistral_model == "mistral-ocr-2503"


def test_vlm_config_default_mistral_base_url():
    cfg = VLMConfig()
    assert str(cfg.mistral_base_url).startswith("https://api.mistral.ai/v1")


def test_vlm_config_default_mistral_api_key_env():
    cfg = VLMConfig()
    assert cfg.mistral_api_key_env == "MISTRAL_API_KEY"


def test_vlm_config_default_timeout():
    cfg = VLMConfig()
    assert cfg.timeout_s == 120.0


# ── Backend literal validation ──────────────────────────────────────────


def test_vlm_config_accepts_ollama():
    cfg = VLMConfig(backend="ollama")
    assert cfg.backend == "ollama"


def test_vlm_config_accepts_mistral():
    cfg = VLMConfig(backend="mistral")
    assert cfg.backend == "mistral"


def test_vlm_config_accepts_none():
    cfg = VLMConfig(backend="none")
    assert cfg.backend == "none"


def test_vlm_config_rejects_unknown_backend():
    with pytest.raises(ValidationError):
        VLMConfig(backend="claude")  # type: ignore[arg-type]


def test_vlm_config_rejects_empty_backend():
    with pytest.raises(ValidationError):
        VLMConfig(backend="")  # type: ignore[arg-type]


# ── timeout_s validation ────────────────────────────────────────────────


def test_vlm_config_rejects_zero_timeout():
    with pytest.raises(ValidationError):
        VLMConfig(timeout_s=0)


def test_vlm_config_rejects_negative_timeout():
    with pytest.raises(ValidationError):
        VLMConfig(timeout_s=-1.0)


def test_vlm_config_accepts_custom_timeout():
    cfg = VLMConfig(timeout_s=60.0)
    assert cfg.timeout_s == 60.0


# ── mistral_api_key_env name validation ─────────────────────────────────


class TestMistralApiKeyEnvValidation:
    """Env var names must be valid POSIX identifiers — ASCII letters,
    digits, underscore; cannot start with a digit."""

    def test_accepts_default(self):
        cfg = VLMConfig(mistral_api_key_env="MISTRAL_API_KEY")
        assert cfg.mistral_api_key_env == "MISTRAL_API_KEY"

    def test_accepts_lowercase(self):
        cfg = VLMConfig(mistral_api_key_env="mistral_key")
        assert cfg.mistral_api_key_env == "mistral_key"

    def test_accepts_mixed_with_digit_suffix(self):
        cfg = VLMConfig(mistral_api_key_env="MY_KEY_V2")
        assert cfg.mistral_api_key_env == "MY_KEY_V2"

    def test_rejects_empty(self):
        with pytest.raises(ValidationError):
            VLMConfig(mistral_api_key_env="")

    def test_rejects_space(self):
        with pytest.raises(ValidationError):
            VLMConfig(mistral_api_key_env="MY KEY")

    def test_rejects_dash(self):
        with pytest.raises(ValidationError):
            VLMConfig(mistral_api_key_env="MY-KEY")

    def test_rejects_dollar_prefix(self):
        with pytest.raises(ValidationError):
            VLMConfig(mistral_api_key_env="$KEY")

    def test_rejects_digit_first(self):
        with pytest.raises(ValidationError):
            VLMConfig(mistral_api_key_env="123KEY")


# ── extra="forbid" ──────────────────────────────────────────────────────


def test_vlm_config_rejects_unknown_field():
    with pytest.raises(ValidationError):
        VLMConfig(ollama_temperature_override=0.7)  # type: ignore[call-arg]


# ── URL validation ──────────────────────────────────────────────────────


def test_vlm_config_rejects_non_http_ollama_url():
    with pytest.raises(ValidationError):
        VLMConfig(ollama_url="ftp://localhost:11434")  # type: ignore[arg-type]


def test_vlm_config_accepts_custom_ollama_url():
    cfg = VLMConfig(ollama_url="http://gpu-box.local:11434")  # type: ignore[arg-type]
    assert "gpu-box.local" in str(cfg.ollama_url)


# ── Config.vlm attachment ───────────────────────────────────────────────


def _build_minimal_config_kwargs() -> dict:
    return {
        "backend": BackendConfig(kind="postgres", dsn="postgresql://localhost/test"),
        "daemon": DaemonConfig(),
        "datasets": [
            DatasetConfig(
                name="d",
                kind="text",
                sources=[DatasetSourceConfig(plugin="markdown_vault", chunker="markdown")],
            )
        ],
        "embedders": [
            EmbedderConfig(name="e", provider="sentence_transformers", model_id="any", dimension=8)
        ],
    }


def test_config_default_vlm_is_noop():
    """Config without a ``vlm`` block defaults to a Noop VLMConfig."""
    cfg = Config(**_build_minimal_config_kwargs())
    assert cfg.vlm.backend == "none"


def test_config_accepts_vlm_block():
    cfg = Config(**_build_minimal_config_kwargs(), vlm=VLMConfig(backend="ollama"))
    assert cfg.vlm.backend == "ollama"


def test_config_accepts_vlm_as_dict():
    cfg = Config(
        **_build_minimal_config_kwargs(),
        vlm={"backend": "ollama", "ollama_model": "qwen2.5vl:32b"},  # type: ignore[arg-type]
    )
    assert cfg.vlm.backend == "ollama"
    assert cfg.vlm.ollama_model == "qwen2.5vl:32b"


# ── resolve_mistral_api_key ─────────────────────────────────────────────


class TestResolveMistralApiKey:
    def test_reads_env_var_when_set(self):
        kwargs = _build_minimal_config_kwargs()
        cfg = Config(**kwargs, vlm=VLMConfig(backend="mistral"))
        with patch.dict(os.environ, {"MISTRAL_API_KEY": "sk-foo"}, clear=False):
            assert cfg.resolve_mistral_api_key() == "sk-foo"

    def test_returns_none_when_unset(self):
        kwargs = _build_minimal_config_kwargs()
        cfg = Config(**kwargs, vlm=VLMConfig(backend="mistral"))
        env = {k: v for k, v in os.environ.items() if k != "MISTRAL_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            assert cfg.resolve_mistral_api_key() is None

    def test_honours_custom_env_var_name(self):
        kwargs = _build_minimal_config_kwargs()
        cfg = Config(
            **kwargs,
            vlm=VLMConfig(backend="mistral", mistral_api_key_env="MY_KEY"),
        )
        with patch.dict(os.environ, {"MY_KEY": "sk-bar"}, clear=False):
            assert cfg.resolve_mistral_api_key() == "sk-bar"

    def test_default_config_returns_none_even_with_var_set(self):
        """When backend is ``none``, the helper still works (it doesn't
        gate on backend) — but the default env var is consulted."""
        kwargs = _build_minimal_config_kwargs()
        cfg = Config(**kwargs)  # default vlm = none
        with patch.dict(os.environ, {"MISTRAL_API_KEY": "sk-baz"}, clear=False):
            # The helper returns the env value verbatim. Whether to USE
            # it is the responsibility of get_active_vlm.
            assert cfg.resolve_mistral_api_key() == "sk-baz"


# ── TOML round-trip shape ───────────────────────────────────────────────


def test_vlm_config_from_toml_like_dict():
    """Pydantic accepts the dict shape that ``tomllib.load`` emits for
    the ``[vlm]`` block in ``config.example.toml``."""
    toml_data = {
        "backend": "ollama",
        "ollama_model": "qwen2.5vl:7b",
        "ollama_url": "http://localhost:11434",
        "mistral_api_key_env": "MISTRAL_API_KEY",
        "timeout_s": 90.0,
    }
    cfg = VLMConfig(**toml_data)
    assert cfg.backend == "ollama"
    assert cfg.timeout_s == 90.0
