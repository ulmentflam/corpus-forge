"""RFC fleet-4 items 2-3 — ``[tailscale]`` block, ``EndpointUrl``, and the
load-time ``ts://``-while-disabled validator.

Covers:
- ``TailscaleConfig`` defaults + ``extra="forbid"``.
- Legacy configs (no ``[tailscale]`` block, plain URLs) validate
  byte-identically and default to ``enabled=False``.
- The ``EndpointUrl`` accept/reject matrix (http(s) + ts:// accepted,
  everything else rejected) on every RFC-named field.
- The load-time validator: a ``ts://`` endpoint while disabled fails
  validation naming the offending field; the same config with
  ``enabled=true`` loads cleanly (resolution stays lazy).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from corpus_forge.config import (
    ClassifierConfig,
    Config,
    EmbedderConfig,
    OllamaConfig,
    TailscaleConfig,
    VLMConfig,
    WhisperConfig,
)


def _load_config(toml_text: str, tmp_path: Path) -> Config:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(textwrap.dedent(toml_text), encoding="utf-8")
    return Config.load(config_path=cfg_path)


_BASE_TOML = """
[backend]
kind = "postgres"
dsn = "postgresql://localhost/forge"

[daemon]

[[datasets]]
name = "x"
kind = "text"
sources = [{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}]

[[embedders]]
name = "e"
provider = "sentence_transformers"
model_id = "m"
dimension = 1
"""


# ── TailscaleConfig block ───────────────────────────────────────────────


class TestTailscaleConfigDefaults:
    def test_defaults(self) -> None:
        ts = TailscaleConfig()
        assert ts.enabled is False
        assert ts.prefer_magicdns is True

    def test_extra_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            TailscaleConfig(unexpected=True)  # type: ignore[call-arg]

    def test_config_without_block_defaults_disabled(self, tmp_path: Path) -> None:
        cfg = _load_config(_BASE_TOML, tmp_path)
        assert isinstance(cfg.tailscale, TailscaleConfig)
        assert cfg.tailscale.enabled is False

    def test_config_with_block(self, tmp_path: Path) -> None:
        cfg = _load_config(
            _BASE_TOML + "\n[tailscale]\nenabled = true\nprefer_magicdns = false\n", tmp_path
        )
        assert cfg.tailscale.enabled is True
        assert cfg.tailscale.prefer_magicdns is False


# ── EndpointUrl accept/reject matrix ────────────────────────────────────


class TestEndpointUrlMatrix:
    @pytest.mark.parametrize(
        "value",
        [
            "http://localhost:11434",
            "https://api.openai.com/v1",
            "ts://gb10",
            "ts://gb10:11434",
            "ts://gb10:5432/corpus",
            "ts://my-host.dotted/v1",
        ],
    )
    def test_accepts_http_and_ts(self, value: str) -> None:
        # Use ollama.base_url as the representative EndpointUrl field.
        cfg = OllamaConfig(base_url=value)  # type: ignore[arg-type]
        assert str(cfg.base_url) == value

    @pytest.mark.parametrize(
        "value",
        ["ftp://nope", "ts://", "ts://:5432", "ts:///x", "not a url", "tcp://h:1"],
    )
    def test_rejects_other(self, value: str) -> None:
        with pytest.raises(ValidationError):
            OllamaConfig(base_url=value)  # type: ignore[arg-type]

    def test_ts_accepted_on_every_named_field(self) -> None:
        # Each RFC-named EndpointUrl field accepts ts:// at parse time.
        assert (
            EmbedderConfig(
                name="e", provider="openai", model_id="m", dimension=1, base_url="ts://gb10:8000"
            ).base_url
            == "ts://gb10:8000"
        )  # type: ignore[comparison-overlap]
        assert VLMConfig(ollama_url="ts://gb10:11434").ollama_url == "ts://gb10:11434"  # type: ignore[comparison-overlap]
        assert VLMConfig(mistral_base_url="ts://gb10/v1").mistral_base_url == "ts://gb10/v1"  # type: ignore[comparison-overlap]
        assert WhisperConfig(remote_base_url="ts://gb10/v1").remote_base_url == "ts://gb10/v1"  # type: ignore[comparison-overlap]
        assert ClassifierConfig(llm_url="ts://gb10:11434").llm_url == "ts://gb10:11434"  # type: ignore[comparison-overlap]


# ── load-time ts://-while-disabled validator ────────────────────────────


class TestTailscaleLoadValidator:
    def test_ts_dsn_disabled_rejected_names_field(self, tmp_path: Path) -> None:
        body = _BASE_TOML.replace(
            'dsn = "postgresql://localhost/forge"', 'dsn = "ts://gb10:5432/corpus"'
        )
        with pytest.raises(ValidationError) as ei:
            _load_config(body, tmp_path)
        msg = str(ei.value)
        assert "backend.dsn" in msg
        assert "enabled = true" in msg

    def test_ts_classifier_disabled_rejected_names_field(self, tmp_path: Path) -> None:
        body = _BASE_TOML + '\n[classifier]\nllm_url = "ts://gb10:11434"\n'
        with pytest.raises(ValidationError) as ei:
            _load_config(body, tmp_path)
        assert "classifier.llm_url" in str(ei.value)

    def test_ts_embedder_disabled_rejected_names_indexed_field(self, tmp_path: Path) -> None:
        body = _BASE_TOML + textwrap.dedent(
            """
            [[embedders]]
            name = "remote"
            provider = "openai"
            model_id = "m"
            dimension = 1
            base_url = "ts://gb10:8000"
            """
        )
        with pytest.raises(ValidationError) as ei:
            _load_config(body, tmp_path)
        assert "embedders[1].base_url" in str(ei.value)

    def test_ts_enabled_loads_clean(self, tmp_path: Path) -> None:
        body = (
            _BASE_TOML.replace(
                'dsn = "postgresql://localhost/forge"', 'dsn = "ts://gb10:5432/corpus"'
            )
            + "\n[tailscale]\nenabled = true\n"
        )
        cfg = _load_config(body, tmp_path)
        # Config stays inert — the DSN is NOT resolved at load time.
        assert str(cfg.backend.dsn) == "ts://gb10:5432/corpus"
        assert cfg.tailscale.enabled is True

    def test_plain_urls_disabled_load_clean(self, tmp_path: Path) -> None:
        # The whole point of the no-Tailscale bar: plain URLs while
        # disabled validate exactly as before.
        body = (
            _BASE_TOML
            + '\n[ollama]\nbase_url = "http://localhost:11434"\n'
            + '\n[classifier]\nllm_url = "https://hosted.example.com"\n'
        )
        cfg = _load_config(body, tmp_path)
        assert cfg.tailscale.enabled is False
        assert str(cfg.ollama.base_url) == "http://localhost:11434"
