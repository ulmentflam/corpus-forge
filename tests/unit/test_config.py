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


def test_expand_user_validator():
    """Test that ~ expansion works."""
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

    # Mock home directory to point to tmp_path
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
