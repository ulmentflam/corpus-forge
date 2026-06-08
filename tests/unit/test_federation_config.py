"""Unit tests for ``FederationConfig`` (RFC ``rfc-fleet-3-federated-config-and-setup``).

The ``[federation]`` block gates the daemon's periodic drift WARNING. Its
contract — default ``enabled=False`` (the hard backcompat bar), positive
``drift_check_interval_s``, ``extra='forbid'`` typo-catching, and
legacy-config backcompat (a config with NO ``[federation]`` block must
still validate and default to disabled) — is pinned tight here because
the daemon reads ``config.federation.enabled`` unconditionally at
startup.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from corpus_forge.config import Config, FederationConfig


def _build_federation_config(**kwargs: Any) -> FederationConfig:
    """Indirection for *negative* tests.

    Routing kwargs through ``**`` defeats the static typechecker's
    constructor-signature check, so pyrefly doesn't flag the deliberately
    invalid inputs (non-positive interval, unknown field) the
    ``pytest.raises(ValidationError)`` cases below exercise at runtime.
    Mirrors ``test_embed_config.py``'s ``_build_embed_config`` precedent.
    """
    return FederationConfig(**kwargs)


# ── A minimal, valid full-Config TOML with NO [federation] block — the
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


class TestFederationConfigDefaults:
    """Default-constructed ``FederationConfig`` is the byte-for-byte off case."""

    def test_construction_with_no_arguments(self) -> None:
        cfg = FederationConfig()
        assert cfg.enabled is False
        assert cfg.drift_check_interval_s == 300.0

    def test_explicit_enabled_accepted(self) -> None:
        cfg = FederationConfig(enabled=True, drift_check_interval_s=60.0)
        assert cfg.enabled is True
        assert cfg.drift_check_interval_s == 60.0


class TestDriftCheckIntervalBounds:
    """``drift_check_interval_s`` must be strictly positive."""

    def test_small_positive_accepted(self) -> None:
        assert FederationConfig(drift_check_interval_s=0.5).drift_check_interval_s == 0.5

    def test_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _build_federation_config(drift_check_interval_s=0)

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _build_federation_config(drift_check_interval_s=-5)


class TestExtraForbid:
    """``extra='forbid'`` catches typos in ``[federation]`` blocks."""

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _build_federation_config(enabled=True, intervol_s=10)


class TestLegacyConfigBackcompat:
    """A config with NO ``[federation]`` block validates and is disabled."""

    def test_legacy_config_validates_with_default_federation(self, tmp_path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_LEGACY_TOML)

        config = Config.load(config_path=config_file)

        # The default-constructed FederationConfig is materialised even
        # though the TOML omits the block — backcompat preserved, off.
        assert isinstance(config.federation, FederationConfig)
        assert config.federation.enabled is False
        assert config.federation.drift_check_interval_s == 300.0

    def test_explicit_federation_block_is_honoured(self, tmp_path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            _LEGACY_TOML + "\n[federation]\nenabled = true\ndrift_check_interval_s = 120.0\n"
        )

        config = Config.load(config_path=config_file)
        assert config.federation.enabled is True
        assert config.federation.drift_check_interval_s == 120.0
