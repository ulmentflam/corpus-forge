"""Unit tests for ``EmbedConfig`` (RFC ``rfc-fleet-2-distributed-embedding``).

The ``[embed]`` block tunes the distributed claim/release backfill loop
in :func:`corpus_forge.embed.backfill_embedder`. Its contract — default
TTL, validation, ``extra='forbid'`` typo-catching, and legacy-config
backcompat (a config with NO ``[embed]`` block must still validate) — is
pinned tight here because the loop reads ``config.embed.claim_lease_ttl``
unconditionally.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from corpus_forge.config import Config, EmbedConfig


def _build_embed_config(**kwargs: Any) -> EmbedConfig:
    """Indirection for *negative* tests.

    Routing the kwargs through ``**`` defeats the static typechecker's
    constructor-signature check, so pyrefly doesn't flag the deliberately
    invalid inputs (out-of-bounds ints, unknown ``lanes`` field) the
    ``pytest.raises(ValidationError)`` cases below exist to exercise at
    *runtime*. Mirrors the repo's existing ``self._cls()(...)`` precedent
    in ``test_config_retrieval.py``.
    """
    return EmbedConfig(**kwargs)


# ── A minimal, valid full-Config TOML with NO [embed] block — the
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


class TestEmbedConfigDefaults:
    """Default-constructed ``EmbedConfig`` carries the documented TTL."""

    def test_construction_with_no_arguments(self) -> None:
        cfg = EmbedConfig()
        assert cfg.claim_lease_ttl == 600

    def test_explicit_value_accepted(self) -> None:
        assert EmbedConfig(claim_lease_ttl=900).claim_lease_ttl == 900


class TestClaimLeaseTtlBounds:
    """``claim_lease_ttl`` must be a strictly-positive int."""

    def test_one_accepted(self) -> None:
        assert EmbedConfig(claim_lease_ttl=1).claim_lease_ttl == 1

    def test_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _build_embed_config(claim_lease_ttl=0)

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _build_embed_config(claim_lease_ttl=-5)


class TestExtraForbid:
    """``extra='forbid'`` catches typos in ``[embed]`` blocks."""

    def test_unknown_field_rejected(self) -> None:
        # A genuinely-unknown field is still caught by extra='forbid'.
        # (``lanes`` became a real field in RFC item 4 — see TestLanesField.)
        with pytest.raises(ValidationError):
            _build_embed_config(claim_lease_ttl=600, lanez=["a"])


class TestLanesField:
    """RFC fleet-2 item 4 — the ``[embed] lanes`` lane-pinning field."""

    def test_default_is_empty_list(self) -> None:
        # Empty/absent lanes is the backcompat bar: "all active embedders".
        assert EmbedConfig().lanes == []

    def test_explicit_lanes_accepted(self) -> None:
        cfg = EmbedConfig(lanes=["qwen3_8b", "nomic"])
        assert cfg.lanes == ["qwen3_8b", "nomic"]

    def test_lanes_independent_of_ttl(self) -> None:
        cfg = EmbedConfig(claim_lease_ttl=900, lanes=["a"])
        assert cfg.claim_lease_ttl == 900
        assert cfg.lanes == ["a"]


class TestLegacyConfigBackcompat:
    """A config with NO ``[embed]`` block still validates and gets the default."""

    def test_legacy_config_validates_with_default_embed(self, tmp_path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_LEGACY_TOML)

        config = Config.load(config_path=config_file)

        # The default-constructed EmbedConfig is materialised even though
        # the TOML omits the block — backcompat preserved.
        assert isinstance(config.embed, EmbedConfig)
        assert config.embed.claim_lease_ttl == 600

    def test_explicit_embed_block_is_honoured(self, tmp_path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_LEGACY_TOML + "\n[embed]\nclaim_lease_ttl = 1200\n")

        config = Config.load(config_path=config_file)
        assert config.embed.claim_lease_ttl == 1200


class TestEmbedLanesConfigValidation:
    """RFC fleet-2 item 4 — ``Config._check_embed_lanes`` cross-reference."""

    def test_legacy_config_has_empty_lanes(self, tmp_path) -> None:
        # No ``[embed]`` block ⇒ default empty lanes ⇒ all active embedders.
        config_file = tmp_path / "config.toml"
        config_file.write_text(_LEGACY_TOML)
        config = Config.load(config_path=config_file)
        assert config.embed.lanes == []

    def test_known_lane_accepted(self, tmp_path) -> None:
        config_file = tmp_path / "config.toml"
        # ``test-embedder`` is the embedder declared in _LEGACY_TOML.
        config_file.write_text(_LEGACY_TOML + '\n[embed]\nlanes = ["test-embedder"]\n')
        config = Config.load(config_path=config_file)
        assert config.embed.lanes == ["test-embedder"]

    def test_unknown_lane_rejected_with_helpful_message(self, tmp_path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_LEGACY_TOML + '\n[embed]\nlanes = ["nope"]\n')
        with pytest.raises(ValidationError) as exc_info:
            Config.load(config_path=config_file)
        msg = str(exc_info.value)
        # The error names the offending lane AND lists the valid options.
        assert "nope" in msg
        assert "test-embedder" in msg
        assert "embed.lanes" in msg

    def test_partial_unknown_lane_rejected(self, tmp_path) -> None:
        config_file = tmp_path / "config.toml"
        config_file.write_text(_LEGACY_TOML + '\n[embed]\nlanes = ["test-embedder", "bogus"]\n')
        with pytest.raises(ValidationError) as exc_info:
            Config.load(config_path=config_file)
        # Only the bad name is reported; the good one isn't in the "unknown" list.
        msg = str(exc_info.value)
        assert "bogus" in msg
