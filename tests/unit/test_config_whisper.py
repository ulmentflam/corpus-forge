"""Phase G (G-04) — :class:`WhisperConfig` + :attr:`Config.whisper`."""

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
    WhisperConfig,
)


def _build_config(**whisper_kwargs):
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
        whisper=WhisperConfig(**whisper_kwargs),
    )


# ── Defaults ────────────────────────────────────────────────────────────


def test_defaults() -> None:
    w = WhisperConfig()
    assert w.backend == "none"
    assert w.model == "small"
    assert w.local_compute_type == "auto"
    assert str(w.remote_base_url).rstrip("/") == "https://api.openai.com/v1"
    assert w.remote_api_key_env == "OPENAI_API_KEY"
    assert w.timeout_s == 300.0
    assert w.language == ""


def test_config_default_has_whisper_block() -> None:
    cfg = _build_config()
    assert isinstance(cfg.whisper, WhisperConfig)
    assert cfg.whisper.backend == "none"


# ── Backend enum ────────────────────────────────────────────────────────


@pytest.mark.parametrize("backend", ["none", "local", "remote"])
def test_backend_accepts_three_values(backend: str) -> None:
    w = WhisperConfig(backend=backend)
    assert w.backend == backend


def test_backend_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        WhisperConfig(backend="frobnicator")


# ── compute_type enum ───────────────────────────────────────────────────


@pytest.mark.parametrize("ct", ["auto", "float16", "int8", "int8_float16"])
def test_compute_type_accepts_known_values(ct: str) -> None:
    w = WhisperConfig(local_compute_type=ct)
    assert w.local_compute_type == ct


def test_compute_type_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError):
        WhisperConfig(local_compute_type="bf16")


# ── timeout_s ───────────────────────────────────────────────────────────


def test_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        WhisperConfig(timeout_s=0.0)
    with pytest.raises(ValidationError):
        WhisperConfig(timeout_s=-1.0)


# ── remote_base_url ─────────────────────────────────────────────────────


def test_remote_base_url_accepts_groq() -> None:
    w = WhisperConfig(remote_base_url="https://api.groq.com/openai/v1")
    assert "groq.com" in str(w.remote_base_url)


def test_remote_base_url_accepts_self_hosted_http() -> None:
    w = WhisperConfig(remote_base_url="http://whisper.internal.example.com:8080/v1")
    assert "whisper.internal" in str(w.remote_base_url)


def test_remote_base_url_rejects_garbage() -> None:
    with pytest.raises(ValidationError):
        WhisperConfig(remote_base_url="not-a-url")


# ── remote_api_key_env validation ───────────────────────────────────────


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "GROQ_API_KEY", "MY_KEY", "_HIDDEN", "ABC123"])
def test_remote_api_key_env_accepts_valid_posix(name: str) -> None:
    w = WhisperConfig(remote_api_key_env=name)
    assert w.remote_api_key_env == name


@pytest.mark.parametrize("bad", ["MY KEY", "123KEY", "MY-KEY", "MY.KEY", ""])
def test_remote_api_key_env_rejects_invalid_posix(bad: str) -> None:
    with pytest.raises(ValidationError):
        WhisperConfig(remote_api_key_env=bad)


# ── extra="forbid" ──────────────────────────────────────────────────────


def test_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        WhisperConfig(unknown_field="value")  # type: ignore[call-arg]
