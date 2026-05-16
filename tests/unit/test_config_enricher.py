"""Phase H (H-04) — :class:`EnricherConfig` + :attr:`Config.code_enricher`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
    EnricherConfig,
)


def _build_config(**enricher_kwargs):
    return Config(
        backend=BackendConfig(kind="postgres", dsn="postgresql://localhost/test"),
        daemon=DaemonConfig(),
        datasets=[
            DatasetConfig(
                name="d",
                kind="text",
                sources=[DatasetSourceConfig(plugin="markdown_vault", chunker="markdown")],
            )
        ],
        embedders=[
            EmbedderConfig(name="e", provider="sentence_transformers", model_id="any", dimension=8)
        ],
        code_enricher=EnricherConfig(**enricher_kwargs),
    )


# ── Defaults ────────────────────────────────────────────────────────────


def test_defaults() -> None:
    e = EnricherConfig()
    assert e.backend == "none"
    assert e.local_model == "qwen3.6:35b-a3b-instruct"
    assert str(e.local_url).rstrip("/") == "http://localhost:11434"
    assert e.remote_model == "qwen3.6:35b-a3b-instruct"
    assert str(e.remote_url).rstrip("/") == "http://localhost:11434"
    assert e.remote_api_shape == "ollama"
    assert e.remote_api_key_env == "OLLAMA_API_KEY"
    assert e.timeout_s == 180.0
    assert e.temperature == 0.1


def test_config_default_has_code_enricher_block() -> None:
    cfg = _build_config()
    assert isinstance(cfg.code_enricher, EnricherConfig)
    assert cfg.code_enricher.backend == "none"


# ── Backend enum ────────────────────────────────────────────────────────


@pytest.mark.parametrize("backend", ["none", "local", "remote"])
def test_backend_accepts_three_values(backend: str) -> None:
    e = EnricherConfig(backend=backend)
    assert e.backend == backend


def test_backend_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        EnricherConfig(backend="frobnicator")


# ── api_shape enum ──────────────────────────────────────────────────────


@pytest.mark.parametrize("shape", ["ollama", "openai"])
def test_api_shape_accepts_known_values(shape: str) -> None:
    e = EnricherConfig(remote_api_shape=shape)
    assert e.remote_api_shape == shape


def test_api_shape_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        EnricherConfig(remote_api_shape="grpc")


# ── timeout_s ───────────────────────────────────────────────────────────


def test_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        EnricherConfig(timeout_s=0.0)
    with pytest.raises(ValidationError):
        EnricherConfig(timeout_s=-1.0)


# ── temperature ─────────────────────────────────────────────────────────


def test_temperature_lower_bound() -> None:
    EnricherConfig(temperature=0.0)
    with pytest.raises(ValidationError):
        EnricherConfig(temperature=-0.1)


def test_temperature_upper_bound() -> None:
    EnricherConfig(temperature=2.0)
    with pytest.raises(ValidationError):
        EnricherConfig(temperature=2.1)


# ── local_url / remote_url ──────────────────────────────────────────────


def test_local_url_accepts_custom_host() -> None:
    e = EnricherConfig(local_url="http://gpu.local:11434")
    assert "gpu.local" in str(e.local_url)


def test_remote_url_accepts_https() -> None:
    e = EnricherConfig(remote_url="https://api.together.xyz/v1")
    assert "together.xyz" in str(e.remote_url)


def test_local_url_rejects_garbage() -> None:
    with pytest.raises(ValidationError):
        EnricherConfig(local_url="not-a-url")


def test_remote_url_rejects_garbage() -> None:
    with pytest.raises(ValidationError):
        EnricherConfig(remote_url="not-a-url")


# ── remote_api_key_env validation ───────────────────────────────────────


@pytest.mark.parametrize(
    "name", ["OPENAI_API_KEY", "OLLAMA_API_KEY", "MY_KEY", "_HIDDEN", "ABC123"]
)
def test_remote_api_key_env_accepts_valid_posix(name: str) -> None:
    e = EnricherConfig(remote_api_key_env=name)
    assert e.remote_api_key_env == name


@pytest.mark.parametrize("bad", ["MY KEY", "123KEY", "MY-KEY", "MY.KEY", ""])
def test_remote_api_key_env_rejects_invalid_posix(bad: str) -> None:
    with pytest.raises(ValidationError):
        EnricherConfig(remote_api_key_env=bad)


# ── extra="forbid" ──────────────────────────────────────────────────────


def test_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        EnricherConfig(unknown_field="value")  # type: ignore[call-arg]


# ── resolve_code_enricher_api_key ───────────────────────────────────────


def test_resolve_api_key_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_QWEN_KEY", "secret-xyz")
    cfg = _build_config(remote_api_key_env="TEST_QWEN_KEY")
    assert cfg.resolve_code_enricher_api_key() == "secret-xyz"


def test_resolve_api_key_returns_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEVER_SET_KEY", raising=False)
    cfg = _build_config(remote_api_key_env="NEVER_SET_KEY")
    assert cfg.resolve_code_enricher_api_key() is None
