"""Unit tests for config.py — extended coverage."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.config import (
    Config,
    BackendConfig,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
    ExpandUser,
    expand_user,
    get_config,
    interpolate_env_vars,
    reload_config,
)


class TestExpandUser:
    def test_expand_user_with_home(self):
        """Test that ~ is expanded to home directory."""
        result = expand_user("~/test")
        assert result.startswith("/")
        assert "test" in result

    def test_expand_user_without_home(self):
        """Test that non-tilde paths are unchanged."""
        result = expand_user("/absolute/path")
        assert result == "/absolute/path"

    def test_expand_user_relative(self):
        """Test relative path expansion."""
        result = expand_user("./relative")
        assert result.endswith("relative")


class TestInterpolateEnvVars:
    def test_interpolate_existing_var(self):
        """Test that existing env vars are interpolated."""
        import os
        os.environ["TEST_VAR"] = "interpolated_value"
        try:
            result = interpolate_env_vars("${TEST_VAR}/path")
            assert result == "interpolated_value/path"
        finally:
            del os.environ["TEST_VAR"]

    def test_interpolate_missing_var(self):
        """Test that missing env vars are left as-is."""
        result = interpolate_env_vars("${MISSING_VAR}/path")
        assert result == "${MISSING_VAR}/path"

    def test_interpolate_no_vars(self):
        """Test that strings without vars are unchanged."""
        result = interpolate_env_vars("no vars here")
        assert result == "no vars here"


class TestBackendConfig:
    def test_backend_config_defaults(self):
        """Test BackendConfig with defaults."""
        config = BackendConfig(dsn="postgresql://test@localhost/test")
        assert config.kind == "postgres"
        assert config.schema == "corpus"

    def test_backend_config_custom_kind(self):
        """Test BackendConfig with custom kind."""
        config = BackendConfig(dsn="test.db", kind="sqlite")
        assert config.kind == "sqlite"

    def test_backend_config_invalid_kind(self):
        """Test BackendConfig rejects invalid kind."""
        with pytest.raises(Exception):
            BackendConfig(dsn="test.db", kind="invalid")

    def test_backend_config_interpolates_dsn(self):
        """Test that DSN env vars are interpolated."""
        import os
        os.environ["PG_HOST"] = "interpolated-host"
        try:
            config = BackendConfig(dsn="postgresql://user:${PG_HOST}/db")
            assert "interpolated-host" in config.dsn
        finally:
            del os.environ["PG_HOST"]


class TestDaemonConfig:
    def test_daemon_config_defaults(self):
        """Test DaemonConfig with defaults."""
        config = DaemonConfig(debounce_seconds=2.0, log_level="INFO", log_format="text")
        assert config.debounce_seconds == 2.0
        assert config.log_level == "INFO"
        assert config.log_format == "text"

    def test_daemon_config_custom_values(self):
        """Test DaemonConfig with custom values."""
        config = DaemonConfig(debounce_seconds=5.0, log_level="DEBUG", log_format="json")
        assert config.debounce_seconds == 5.0
        assert config.log_level == "DEBUG"
        assert config.log_format == "json"

    def test_daemon_config_invalid_log_level(self):
        """Test DaemonConfig rejects invalid log level."""
        with pytest.raises(Exception):
            DaemonConfig(debounce_seconds=2.0, log_level="invalid", log_format="text")

    def test_daemon_config_invalid_log_format(self):
        """Test DaemonConfig rejects invalid log format."""
        with pytest.raises(Exception):
            DaemonConfig(debounce_seconds=2.0, log_level="INFO", log_format="invalid")

    def test_daemon_config_debounce_zero(self):
        """Test DaemonConfig rejects zero debounce."""
        with pytest.raises(Exception):
            DaemonConfig(debounce_seconds=0, log_level="INFO", log_format="text")


class TestDatasetSourceConfig:
    def test_source_config_defaults(self):
        """Test DatasetSourceConfig with defaults."""
        config = DatasetSourceConfig(plugin="markdown_vault", vault_root="/tmp", chunker="markdown")
        assert config.exclude_globs == [".obsidian/**", ".trash/**", ".*"]
        assert config.vault_names == []
        assert config.include_subagents is True
        assert config.chunker_config == {}

    def test_source_config_custom_values(self):
        """Test DatasetSourceConfig with custom values."""
        config = DatasetSourceConfig(
            plugin="claude_code",
            projects_root="/tmp/projects",
            include_subagents=False,
            chunker="conversation",
            chunker_config={"mode": "sliding_window"},
        )
        assert config.include_subagents is False
        assert config.chunker_config == {"mode": "sliding_window"}

    def test_source_config_invalid_chunker(self):
        """Test DatasetSourceConfig rejects invalid chunker."""
        with pytest.raises(Exception):
            DatasetSourceConfig(plugin="markdown_vault", vault_root="/tmp", chunker="invalid")


class TestDatasetConfig:
    def test_dataset_config_minimal(self):
        """Test DatasetConfig with minimal fields."""
        config = DatasetConfig(
            name="test",
            kind="text",
            sources=[
                DatasetSourceConfig(
                    plugin="markdown_vault",
                    vault_root="/tmp",
                    chunker="markdown",
                )
            ],
        )
        assert config.name == "test"
        assert config.kind == "text"
        assert config.description is None

    def test_dataset_config_with_description(self):
        """Test DatasetConfig with description."""
        config = DatasetConfig(
            name="test",
            kind="chat",
            description="A test dataset",
            sources=[
                DatasetSourceConfig(
                    plugin="claude_code",
                    projects_root="/tmp",
                    chunker="conversation",
                )
            ],
        )
        assert config.description == "A test dataset"

    def test_dataset_config_invalid_kind(self):
        """Test DatasetConfig rejects invalid kind."""
        with pytest.raises(Exception):
            DatasetConfig(
                name="test",
                kind="invalid",
                sources=[
                    DatasetSourceConfig(
                        plugin="markdown_vault",
                        vault_root="/tmp",
                        chunker="markdown",
                    )
                ],
            )


class TestEmbedderConfig:
    def test_embedder_config_defaults(self):
        """Test EmbedderConfig with defaults."""
        config = EmbedderConfig(
            name="test",
            provider="sentence_transformers",
            model_id="test/model",
            dimension=384,
        )
        assert config.normalize is True
        assert config.distance == "cosine"
        assert config.active is True
        assert config.batch_size == 32
        assert config.device == "auto"
        assert config.api_key_env == "OPENAI_API_KEY"

    def test_embedder_config_custom_values(self):
        """Test EmbedderConfig with custom values."""
        config = EmbedderConfig(
            name="test",
            provider="openai",
            model_id="text-embedding-3-small",
            dimension=1536,
            normalize=False,
            distance="l2",
            active=False,
            batch_size=64,
            device="cuda",
            api_key_env="MY_KEY",
        )
        assert config.normalize is False
        assert config.distance == "l2"
        assert config.active is False
        assert config.batch_size == 64
        assert config.device == "cuda"
        assert config.api_key_env == "MY_KEY"

    def test_embedder_config_invalid_provider(self):
        """Test EmbedderConfig rejects invalid provider."""
        with pytest.raises(Exception):
            EmbedderConfig(
                name="test",
                provider="invalid",
                model_id="test/model",
                dimension=384,
            )

    def test_embedder_config_invalid_dimension(self):
        """Test EmbedderConfig rejects zero dimension."""
        with pytest.raises(Exception):
            EmbedderConfig(
                name="test",
                provider="sentence_transformers",
                model_id="test/model",
                dimension=0,
            )

    def test_embedder_config_invalid_distance(self):
        """Test EmbedderConfig rejects invalid distance."""
        with pytest.raises(Exception):
            EmbedderConfig(
                name="test",
                provider="sentence_transformers",
                model_id="test/model",
                dimension=384,
                distance="invalid",
            )


class TestConfigLoad:
    def test_config_load_minimal(self, temp_dir):
        """Test loading a minimal config file."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "test"
kind = "text"
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "~/test"
  chunker = "markdown"
  chunker_config = { max_chars = 1500, overlap = 200 }

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "config.toml"
        config_file.write_text(config_content)

        config = Config.load(config_path=config_file)
        assert config.backend.kind == "postgres"
        assert len(config.datasets) == 1
        assert len(config.embedders) == 1

    def test_config_load_missing_file(self, temp_dir):
        """Test loading a non-existent config file."""
        missing_file = temp_dir / "nonexistent.toml"
        with pytest.raises(FileNotFoundError):
            Config.load(config_path=missing_file)

    def test_config_load_with_secrets(self, temp_dir):
        """Test loading config with secrets file."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://${PG_USER}@${PG_HOST}/${PG_DB}"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "test"
kind = "text"
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "~/test"
  chunker = "markdown"
  chunker_config = { max_chars = 1500, overlap = 200 }

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "config.toml"
        config_file.write_text(config_content)

        secrets_content = """
PG_USER=secrets_user
PG_HOST=secrets-host
PG_DB=secrets_db
"""
        secrets_file = temp_dir / "secrets.env"
        secrets_file.write_text(secrets_content)

        config = Config.load(config_path=config_file, secrets_path=secrets_file)
        assert "secrets_user" in config.backend.dsn
        assert "secrets-host" in config.backend.dsn
        assert "secrets_db" in config.backend.dsn

    def test_config_load_env_expansion(self, temp_dir):
        """Test that environment variables in DSN are expanded."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://${TEST_PG_HOST}/test"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "test"
kind = "text"
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "~/test"
  chunker = "markdown"
  chunker_config = { max_chars = 1500, overlap = 200 }

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "config.toml"
        config_file.write_text(config_content)

        import os
        os.environ["TEST_PG_HOST"] = "expanded-host"
        try:
            config = Config.load(config_path=config_file)
            assert "expanded-host" in config.backend.dsn
        finally:
            del os.environ["TEST_PG_HOST"]


class TestConfigValidation:
    def test_config_validation_accepts_valid(self, temp_dir):
        """Test that valid config is accepted."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "test"
kind = "text"
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "/tmp"
  chunker = "markdown"
  chunker_config = { max_chars = 1500, overlap = 200 }

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
normalize = true
distance = "cosine"
active = true
"""
        config_file = temp_dir / "config.toml"
        config_file.write_text(config_content)
        # Should not raise
        config = Config.load(config_path=config_file)
        assert config.backend.kind == "postgres"


class TestConfigGetReload:
    def test_get_config_lazy_load(self):
        """Test that get_config lazily loads config."""
        with patch.object(Config, "load") as mock_load:
            from corpus_forge import config as config_module

            # Clear the cached config
            config_module._config = None
            mock_config = MagicMock()
            mock_load.return_value = mock_config

            result = get_config()
            assert result is mock_config
            mock_load.assert_called_once()

    def test_get_config_returns_cached(self):
        """Test that get_config returns cached config."""
        from corpus_forge import config as config_module

        mock_config = MagicMock()
        config_module._config = mock_config

        result = get_config()
        assert result is mock_config

    def test_reload_config_forces_reload(self):
        """Test that reload_config forces a new load."""
        from corpus_forge import config as config_module

        mock_config1 = MagicMock()
        mock_config2 = MagicMock()
        config_module._config = mock_config1

        with patch.object(Config, "load", return_value=mock_config2):
            result = reload_config()
            assert result is mock_config2
            assert config_module._config is mock_config2
