"""Unit tests for ``ServiceConfig`` + ``EmbedConfig`` drain-idle window.

RFC fleet-5 item 2 — the ``[service]`` block selects what the managed
daemon does (``embed_drain`` / ``ingest_watch``), and the drain loop's
idle-backoff window lives in ``[embed] drain_idle_min`` /
``drain_idle_max``. This pins the contract: defaults reproduce today's
ingest-only daemon byte-for-byte, ``extra='forbid'`` catches typos, the
idle window is validated (``max >= min``, both > 0), and a config with NO
``[service]`` block still validates (legacy backcompat).

The daemon-lifecycle wiring that *consumes* these toggles is RFC item 2b;
this test covers the schema half only.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from corpus_forge.config import Config, EmbedConfig, ServiceConfig


def _build_service_config(**kwargs: Any) -> ServiceConfig:
    """Indirection for *negative* tests — defeats the static constructor check."""
    return ServiceConfig(**kwargs)


def _build_embed_config(**kwargs: Any) -> EmbedConfig:
    return EmbedConfig(**kwargs)


# ── A minimal, valid full-Config TOML with NO [service] block — the
#    legacy-config backcompat fixture. ───────────────────────────────────
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
    """Default-constructed ``ServiceConfig`` is today's ingest-only daemon."""

    def test_construction_with_no_arguments(self) -> None:
        cfg = ServiceConfig()
        # Backcompat bar: no drain loop, ingest watcher on — today's daemon.
        assert cfg.embed_drain is False
        assert cfg.ingest_watch is True

    def test_explicit_drain_host_accepted(self) -> None:
        # A joined pure-drain GPU box: drain on, ingest watcher off.
        cfg = ServiceConfig(embed_drain=True, ingest_watch=False)
        assert cfg.embed_drain is True
        assert cfg.ingest_watch is False


class TestServiceConfigExtraForbid:
    """``extra='forbid'`` catches typos in ``[service]`` blocks."""

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _build_service_config(embed_drain=True, embeddrain=True)


class TestEmbedDrainIdleWindow:
    """``[embed] drain_idle_min`` / ``drain_idle_max`` bounds + validation."""

    def test_defaults_mirror_loop_constants(self) -> None:
        cfg = EmbedConfig()
        assert cfg.drain_idle_min == 5.0
        assert cfg.drain_idle_max == 300.0

    def test_custom_window_accepted(self) -> None:
        cfg = EmbedConfig(drain_idle_min=1.0, drain_idle_max=10.0)
        assert cfg.drain_idle_min == 1.0
        assert cfg.drain_idle_max == 10.0

    def test_equal_min_max_accepted(self) -> None:
        # max >= min is the bar; equal is a fixed-interval poll.
        assert EmbedConfig(drain_idle_min=5.0, drain_idle_max=5.0).drain_idle_max == 5.0

    def test_max_below_min_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _build_embed_config(drain_idle_min=10.0, drain_idle_max=5.0)

    def test_zero_min_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _build_embed_config(drain_idle_min=0.0)

    def test_negative_max_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _build_embed_config(drain_idle_max=-1.0)


class TestLegacyConfigBackcompat:
    """A config with NO ``[service]`` block validates and is ingest-only."""

    def test_legacy_config_validates_with_default_service(self, tmp_path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_LEGACY_TOML)

        config = Config.load(config_path=config_file)

        # The default-constructed ServiceConfig is materialised even though
        # the TOML omits the block — backcompat preserved.
        assert isinstance(config.service, ServiceConfig)
        assert config.service.embed_drain is False
        assert config.service.ingest_watch is True
        # And the embed drain-idle window defaults are present.
        assert config.embed.drain_idle_min == 5.0
        assert config.embed.drain_idle_max == 300.0

    def test_explicit_service_block_is_honoured(self, tmp_path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            _LEGACY_TOML
            + "\n[service]\nembed_drain = true\ningest_watch = false\n"
            + "\n[embed]\ndrain_idle_min = 2.0\ndrain_idle_max = 60.0\n"
        )

        config = Config.load(config_path=config_file)
        assert config.service.embed_drain is True
        assert config.service.ingest_watch is False
        assert config.embed.drain_idle_min == 2.0
        assert config.embed.drain_idle_max == 60.0

    def test_bad_drain_window_in_toml_rejected(self, tmp_path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            _LEGACY_TOML + "\n[embed]\ndrain_idle_min = 100.0\ndrain_idle_max = 10.0\n"
        )
        with pytest.raises((ValidationError, ValueError)):
            Config.load(config_path=config_file)
