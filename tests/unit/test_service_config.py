"""Unit tests for ``ServiceConfig`` + the ``[embed]`` drain knobs.

RFC ``rfc-fleet-5-service-embed-drain`` adds two surfaces to config:

* a new ``[service]`` block (``embed_drain`` / ``ingest_watch``) that
  governs what the supervised daemon runs, and
* ``[embed] drain_idle_min`` / ``drain_idle_max`` — the bounded
  exponential-backoff window for the embed-drain loop.

The hard backcompat bar (RFC): a config with NO ``[service]`` block must
still validate and reproduce today's daemon exactly — ingest watcher on,
no drain loop — which means ``embed_drain`` defaults ``False`` and
``ingest_watch`` defaults ``True``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from corpus_forge.config import Config, EmbedConfig, ServiceConfig


def _build_service_config(**kwargs: Any) -> ServiceConfig:
    """Indirection for *negative* tests (defeats the static typechecker)."""
    return ServiceConfig(**kwargs)


def _build_embed_config(**kwargs: Any) -> EmbedConfig:
    return EmbedConfig(**kwargs)


# A minimal valid full-Config TOML with NO [service] / [embed] block.
_LEGACY_TOML = """\
[backend]
kind = "postgres"
dsn = "postgresql://user:pass@localhost/db"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "test-dataset"
kind = "text"
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "~/test-vault"
  chunker = "markdown"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
"""


class TestServiceConfigDefaults:
    """Defaults reproduce today's daemon (backcompat bar)."""

    def test_embed_drain_defaults_off(self) -> None:
        # A laptop already embeds at ingest time; no surprise GPU loop.
        assert ServiceConfig().embed_drain is False

    def test_ingest_watch_defaults_on(self) -> None:
        assert ServiceConfig().ingest_watch is True

    def test_explicit_values_accepted(self) -> None:
        sc = ServiceConfig(embed_drain=True, ingest_watch=False)
        assert sc.embed_drain is True
        assert sc.ingest_watch is False

    def test_drain_only_box_shape(self) -> None:
        # The pure-drain GPU box: drain on, ingest watcher off.
        sc = ServiceConfig(embed_drain=True, ingest_watch=False)
        assert (sc.embed_drain, sc.ingest_watch) == (True, False)


class TestServiceConfigExtraForbid:
    """``extra='forbid'`` catches typos in a ``[service]`` block."""

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _build_service_config(embed_drainn=True)


class TestServiceConfigLegacyBackcompat:
    """A config with NO ``[service]`` block still validates with defaults."""

    def test_legacy_config_validates_with_default_service(self, tmp_path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_LEGACY_TOML)

        config = Config.load(config_path=config_file)

        assert isinstance(config.service, ServiceConfig)
        assert config.service.embed_drain is False
        assert config.service.ingest_watch is True

    def test_explicit_service_block_is_honoured(self, tmp_path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            _LEGACY_TOML + "\n[service]\nembed_drain = true\ningest_watch = false\n"
        )

        config = Config.load(config_path=config_file)
        assert config.service.embed_drain is True
        assert config.service.ingest_watch is False


class TestDrainIdleWindow:
    """``[embed] drain_idle_min`` / ``drain_idle_max`` knobs + validation."""

    def test_defaults(self) -> None:
        ec = EmbedConfig()
        assert ec.drain_idle_min == 5.0
        assert ec.drain_idle_max == 300.0

    def test_explicit_window_accepted(self) -> None:
        ec = EmbedConfig(drain_idle_min=2.0, drain_idle_max=60.0)
        assert ec.drain_idle_min == 2.0
        assert ec.drain_idle_max == 60.0

    def test_equal_bounds_accepted(self) -> None:
        # A fixed (non-doubling) idle interval is a legitimate config.
        ec = EmbedConfig(drain_idle_min=10.0, drain_idle_max=10.0)
        assert ec.drain_idle_min == ec.drain_idle_max == 10.0

    def test_inverted_window_rejected(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            _build_embed_config(drain_idle_min=10.0, drain_idle_max=5.0)
        msg = str(exc_info.value)
        assert "drain_idle_max" in msg
        assert "drain_idle_min" in msg

    def test_zero_min_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _build_embed_config(drain_idle_min=0)

    def test_negative_max_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _build_embed_config(drain_idle_max=-1)

    def test_legacy_config_gets_default_window(self, tmp_path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_LEGACY_TOML)
        config = Config.load(config_path=config_file)
        assert config.embed.drain_idle_min == 5.0
        assert config.embed.drain_idle_max == 300.0
