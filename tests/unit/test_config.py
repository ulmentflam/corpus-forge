"""Unit tests for configuration management."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from corpus_forge.config import (
    Config,
    ExpandUser,
    interpolate_env_vars,
)


@pytest.mark.requires_unix
def test_expand_user_validator():
    """Test that ~ expansion works.

    Marked ``requires_unix``: ``Path("/absolute/path")`` normalises to
    ``\\absolute\\path`` on Windows; the second equality assertion is
    POSIX-only. The macOS / Linux matrix cells cover the ``ExpandUser``
    invariants we care about.
    """
    assert ExpandUser("~/test") == str(Path("~/test").expanduser())
    assert ExpandUser("/absolute/path") == "/absolute/path"


def test_interpolate_env_vars_validator():
    """Test that environment variable interpolation works."""
    os.environ["TEST_VAR"] = "replaced"
    assert interpolate_env_vars("prefix_${TEST_VAR}_suffix") == "prefix_replaced_suffix"
    assert interpolate_env_vars("no_vars_here") == "no_vars_here"
    del os.environ["TEST_VAR"]


def test_config_load_minimal(tmp_path):
    """Test loading a minimal valid configuration."""
    # Create config file
    config_content = """\
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
    config_file = tmp_path / "config.toml"
    config_file.write_text(config_content)

    # Load config
    config = Config.load(config_path=config_file)

    # Assertions
    assert config.backend.kind == "postgres"
    assert config.backend.dsn == "postgresql://user:pass@localhost/db"
    assert len(config.datasets) == 1
    assert config.datasets[0].name == "test-dataset"
    assert len(config.embedders) == 1
    assert config.embedders[0].name == "test-embedder"


def test_config_validation_errors():
    """Test that validation catches errors."""
    # Test invalid backend kind
    with pytest.raises(ValidationError):
        Config(
            backend={"kind": "invalid", "dsn": "test"},
            daemon={"debounce_seconds": 2.0, "log_level": "INFO", "log_format": "text"},
            datasets=[{"name": "test", "kind": "text", "sources": [{"plugin": "test"}]}],
            embedders=[
                {
                    "name": "test",
                    "provider": "sentence_transformers",
                    "model_id": "test",
                    "dimension": 384,
                }
            ],
        )

    # Test missing required fields
    with pytest.raises(ValidationError):
        Config(
            backend={"kind": "postgres"},
            daemon={"debounce_seconds": 2.0, "log_level": "INFO", "log_format": "text"},
            datasets=[{"name": "test", "kind": "text", "sources": [{"plugin": "test"}]}],
            embedders=[
                {
                    "name": "test",
                    "provider": "sentence_transformers",
                    "model_id": "test",
                    "dimension": 384,
                }
            ],
        )  # Missing dsn

    # Test invalid embedder provider
    with pytest.raises(ValidationError):
        Config(
            backend={"kind": "postgres", "dsn": "test"},
            daemon={"debounce_seconds": 2.0, "log_level": "INFO", "log_format": "text"},
            datasets=[{"name": "test", "kind": "text", "sources": [{"plugin": "test"}]}],
            embedders=[
                {"name": "test", "provider": "invalid", "model_id": "test", "dimension": 384}
            ],
        )


def test_config_with_secrets(tmp_path, monkeypatch):
    """Test that secrets file is properly loaded."""
    # Create config file
    config_content = """\
[backend]
kind = "postgres"
dsn = "postgresql://${DB_USER}:${DB_PASS}@localhost/${DB_NAME}"
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
    config_file = tmp_path / "config.toml"
    config_file.write_text(config_content)

    # Create secrets file
    secrets_content = """\
DB_USER=secret_user
DB_PASS=secret_pass
DB_NAME=secret_db
"""
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text(secrets_content)

    # Mock home directory to point to tmp_path. Also clear
    # CORPUS_FORGE_CONFIG: Config.load() resolves that env var BEFORE
    # the home-relative default, so an ambient value (set by a dev
    # shell or an outer test harness) would bypass the tmp config this
    # test stages and read someone else's file.
    monkeypatch.delenv("CORPUS_FORGE_CONFIG", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    # Load config (should find secrets in ~/.config/corpus-forge/secrets.env)
    config_dir = tmp_path / ".config" / "corpus-forge"
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(config_content)
    (config_dir / "secrets.env").write_text(secrets_content)

    config = Config.load()

    # Check that environment variables were interpolated
    expected_dsn = "postgresql://secret_user:secret_pass@localhost/secret_db"
    assert config.backend.dsn == expected_dsn


def test_config_load_uses_cf_config(tmp_path, monkeypatch):
    """Test that Config.load uses CF_CONFIG when CORPUS_FORGE_CONFIG is not set."""
    # Create config file
    config_content = """\
[backend]
kind = "postgres"
dsn = "postgresql://cf_user:cf_pass@localhost/cf_db"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "cf-dataset"
kind = "text"
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "~/cf-vault"
  chunker = "markdown"

[[embedders]]
name = "cf-embedder"
provider = "sentence_transformers"
model_id = "cf-model"
dimension = 384
"""
    config_file = tmp_path / "cf_config.toml"
    config_file.write_text(config_content, encoding="utf-8")

    monkeypatch.delenv("CORPUS_FORGE_CONFIG", raising=False)
    monkeypatch.setenv("CF_CONFIG", str(config_file))

    config = Config.load()
    assert config.backend.dsn == "postgresql://cf_user:cf_pass@localhost/cf_db"
    assert config.datasets[0].name == "cf-dataset"


def test_config_load_prefers_corpus_forge_config(tmp_path, monkeypatch):
    """Test that CORPUS_FORGE_CONFIG takes precedence over CF_CONFIG in Config.load."""
    # Create first config file (for CF_CONFIG)
    config_content_cf = """\
[backend]
kind = "postgres"
dsn = "postgresql://cf_user:cf_pass@localhost/cf_db"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "cf-dataset"
kind = "text"
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "~/cf-vault"
  chunker = "markdown"

[[embedders]]
name = "cf-embedder"
provider = "sentence_transformers"
model_id = "cf-model"
dimension = 384
"""
    # Create second config file (for CORPUS_FORGE_CONFIG)
    config_content_cf_forge = """\
[backend]
kind = "postgres"
dsn = "postgresql://forge_user:forge_pass@localhost/forge_db"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "forge-dataset"
kind = "text"
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "~/forge-vault"
  chunker = "markdown"

[[embedders]]
name = "forge-embedder"
provider = "sentence_transformers"
model_id = "forge-model"
dimension = 384
"""
    config_file_cf = tmp_path / "cf_config.toml"
    config_file_cf.write_text(config_content_cf, encoding="utf-8")

    config_file_forge = tmp_path / "corpus_forge_config.toml"
    config_file_forge.write_text(config_content_cf_forge, encoding="utf-8")

    monkeypatch.setenv("CF_CONFIG", str(config_file_cf))
    monkeypatch.setenv("CORPUS_FORGE_CONFIG", str(config_file_forge))

    config = Config.load()
    assert config.backend.dsn == "postgresql://forge_user:forge_pass@localhost/forge_db"
    assert config.datasets[0].name == "forge-dataset"
