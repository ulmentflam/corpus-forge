"""Unit tests for config.py — extended coverage."""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from corpus_forge.config import (
    BackendConfig,
    Config,
    DaemonConfig,
    DatasetConfig,
    DatasetSourceConfig,
    EmbedderConfig,
    ZoteroSourceConfig,
    expand_user,
    get_config,
    interpolate_env_vars,
    reload_config,
)


class TestExpandUser:
    @pytest.mark.requires_unix
    def test_expand_user_with_home(self):
        """Test that ~ is expanded to home directory.

        Marked ``requires_unix``: the assertion ``result.startswith("/")``
        is a POSIX absolute-path shape; Windows expands ``~`` to
        ``C:\\Users\\<name>``.
        """
        result = expand_user("~/test")
        assert result.startswith("/")
        assert "test" in result

    @pytest.mark.requires_unix
    def test_expand_user_without_home(self):
        """Test that non-tilde POSIX absolute paths are unchanged.

        Marked ``requires_unix``: ``Path("/absolute/path")`` normalises
        to ``\\absolute\\path`` on Windows (it's seen as a drive-
        relative absolute path); the equality check is POSIX-only.
        """
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

    def test_backend_config_schema_shadow_warning_is_suppressed(self):
        """The ``schema`` field shadows ``BaseModel.schema()`` on purpose.

        Pydantic emits a ``UserWarning`` when a model field name shadows
        an attribute on the parent. ``BackendConfig.schema`` is read at
        ~15 call sites and is part of the public TOML surface
        (``[backend] schema = "corpus"``), so renaming is not an
        option. ``corpus_forge.config`` filters the specific message at
        module load — this regression guard asserts no such warning
        leaks on a *fresh* import of the module.

        Subprocess (rather than ``importlib.reload`` in-process) so the
        check doesn't rebind the ``Config`` class object on every other
        in-flight test — under xdist + pytest-randomly that's enough
        to invalidate every ``patch("corpus_forge.config.Config.load",
        ...)`` set up by sibling tests.
        """
        import subprocess
        import sys

        # ``-W error::UserWarning`` makes the subprocess fail if any
        # UserWarning is raised during the import. The filter block in
        # ``corpus_forge/config.py`` must beat ``-W error`` for the
        # known-shadow message specifically; everything else continues
        # to be promoted, so the assertion stays narrow.
        result = subprocess.run(
            [
                sys.executable,
                "-W",
                "error::UserWarning",
                "-c",
                "import corpus_forge.config",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, (
            "fresh `import corpus_forge.config` raised a UserWarning — "
            "the schema-shadow filter in config.py is no longer "
            f"matching the pydantic message. stderr:\n{result.stderr}"
        )
        assert 'Field name "schema" in "BackendConfig"' not in result.stderr


class TestZoteroSourceDefault:
    """Phase M Wave 4 source-nesting bug regression: ``plugin = "zotero"``
    without a ``[datasets.sources.zotero]`` block must default-instantiate
    ``ZoteroSourceConfig`` so doctor's check can see the source instead of
    skipping it with ``"no Zotero source configured"``.
    """

    def test_plugin_zotero_default_instantiates_zotero_block(self):
        """``DatasetSourceConfig(plugin="zotero", chunker=...)`` (no
        ``zotero=...``) must populate ``.zotero`` with a default
        ``ZoteroSourceConfig`` instance."""
        src = DatasetSourceConfig(plugin="zotero", chunker="markdown")
        assert src.zotero is not None, (
            "plugin=zotero without an explicit nested block must default "
            "to ZoteroSourceConfig() so doctor sees the source"
        )
        assert isinstance(src.zotero, ZoteroSourceConfig)
        # Defaults are local-mode.
        assert src.zotero.mode == "local"
        assert src.zotero.library_path is None
        assert src.zotero.api_key_env == "ZOTERO_API_KEY"

    def test_plugin_zotero_with_explicit_block_is_unchanged(self):
        """Explicit ``zotero=...`` is not clobbered by the default."""
        explicit = ZoteroSourceConfig(mode="web", user_id="12345")
        src = DatasetSourceConfig(plugin="zotero", chunker="markdown", zotero=explicit)
        assert src.zotero is explicit
        assert src.zotero.mode == "web"
        assert src.zotero.user_id == "12345"

    def test_plugin_non_zotero_does_not_get_zotero_block(self):
        """Sources with a non-zotero plugin keep ``.zotero == None``."""
        src = DatasetSourceConfig(plugin="markdown_vault", vault_root="/tmp", chunker="markdown")
        assert src.zotero is None


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


class TestDaemonConfigSyncFields:
    """Tests for new DaemonConfig sync fields (P1-03)."""

    def test_daemon_config_sync_default_host_id(self):
        """Test DaemonConfig sync host_id defaults to empty string."""
        config = DaemonConfig(
            debounce_seconds=2.0,
            log_level="INFO",
            log_format="text",
        )
        assert config.host_id == ""

    def test_daemon_config_sync_custom_host_id(self):
        """Test DaemonConfig accepts a custom host_id."""
        config = DaemonConfig(
            debounce_seconds=2.0,
            log_level="INFO",
            log_format="text",
            host_id="host-abc123",
        )
        assert config.host_id == "host-abc123"

    def test_daemon_config_sync_default_trash_dir(self):
        """Test DaemonConfig trash_dir defaults are set."""
        config = DaemonConfig(
            debounce_seconds=2.0,
            log_level="INFO",
            log_format="text",
        )
        assert config.trash_dir is not None

    def test_daemon_config_sync_default_conflict_dir(self):
        """Test DaemonConfig conflict_dir defaults are set."""
        config = DaemonConfig(
            debounce_seconds=2.0,
            log_level="INFO",
            log_format="text",
        )
        assert config.conflict_dir is not None

    def test_daemon_config_sync_trash_dir_expands_tilde(self):
        """Test DaemonConfig trash_dir expands ~ via ExpandedPath."""
        config = DaemonConfig(
            debounce_seconds=2.0,
            log_level="INFO",
            log_format="text",
            trash_dir="~/my-trash",
        )
        assert not config.trash_dir.startswith("~")
        assert "my-trash" in config.trash_dir

    def test_daemon_config_sync_conflict_dir_expands_tilde(self):
        """Test DaemonConfig conflict_dir expands ~ via ExpandedPath."""
        config = DaemonConfig(
            debounce_seconds=2.0,
            log_level="INFO",
            log_format="text",
            conflict_dir="~/conflicts",
        )
        assert not config.conflict_dir.startswith("~")
        assert "conflicts" in config.conflict_dir

    def test_daemon_config_sync_default_poll_interval(self):
        """Test DaemonConfig sync_poll_interval_s defaults to 5.0."""
        config = DaemonConfig(
            debounce_seconds=2.0,
            log_level="INFO",
            log_format="text",
        )
        assert config.sync_poll_interval_s == 5.0

    def test_daemon_config_sync_custom_poll_interval(self):
        """Test DaemonConfig accepts a custom sync_poll_interval_s."""
        config = DaemonConfig(
            debounce_seconds=2.0,
            log_level="INFO",
            log_format="text",
            sync_poll_interval_s=10.0,
        )
        assert config.sync_poll_interval_s == 10.0

    def test_daemon_config_sync_poll_interval_zero_rejected(self):
        """Test DaemonConfig rejects sync_poll_interval_s == 0."""
        with pytest.raises(ValidationError):
            DaemonConfig(
                debounce_seconds=2.0,
                log_level="INFO",
                log_format="text",
                sync_poll_interval_s=0,
            )

    def test_daemon_config_sync_poll_interval_negative_rejected(self):
        """Test DaemonConfig rejects sync_poll_interval_s < 0."""
        with pytest.raises(ValidationError):
            DaemonConfig(
                debounce_seconds=2.0,
                log_level="INFO",
                log_format="text",
                sync_poll_interval_s=-1.0,
            )

    def test_daemon_config_sync_default_listen_notify(self):
        """Test DaemonConfig sync_use_listen_notify defaults to False."""
        config = DaemonConfig(
            debounce_seconds=2.0,
            log_level="INFO",
            log_format="text",
        )
        assert config.sync_use_listen_notify is False

    def test_daemon_config_sync_custom_listen_notify(self):
        """Test DaemonConfig accepts custom sync_use_listen_notify."""
        config = DaemonConfig(
            debounce_seconds=2.0,
            log_level="INFO",
            log_format="text",
            sync_use_listen_notify=True,
        )
        assert config.sync_use_listen_notify is True

    def test_daemon_config_sync_from_minimal_toml(self, temp_dir):
        """Test DaemonConfig parses sync fields from minimal TOML."""
        config_content = """
[backend]
kind = "postgres"
dsn = "postgresql://test@test/memory"
schema = "corpus"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"
host_id = "test-host"
trash_dir = "~/trash"
conflict_dir = "~/conflicts"
sync_poll_interval_s = 8.0
sync_use_listen_notify = true

[[datasets]]
name = "test"
kind = "text"
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "/tmp"
  chunker = "markdown"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
"""
        config_file = temp_dir / "config.toml"
        config_file.write_text(config_content)

        config = Config.load(config_path=config_file)
        assert config.daemon.host_id == "test-host"
        assert "trash" in config.daemon.trash_dir
        assert "conflicts" in config.daemon.conflict_dir
        assert config.daemon.sync_poll_interval_s == 8.0
        assert config.daemon.sync_use_listen_notify is True


class TestDatasetConfigSyncEnabled:
    """Tests for new DatasetConfig sync_enabled field and validator (P1-03)."""

    def test_dataset_config_sync_enabled_default(self):
        """Test DatasetConfig sync_enabled defaults to False."""
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
        assert config.sync_enabled is False

    def test_dataset_config_sync_enabled_text_accepted(self):
        """Test DatasetConfig sync_enabled=True is accepted when kind='text'."""
        config = DatasetConfig(
            name="test",
            kind="text",
            sync_enabled=True,
            sources=[
                DatasetSourceConfig(
                    plugin="markdown_vault",
                    vault_root="/tmp",
                    chunker="markdown",
                )
            ],
        )
        assert config.sync_enabled is True

    def test_dataset_config_sync_enabled_chat_rejected(self):
        """Test DatasetConfig raises when sync_enabled=True and kind='chat'."""
        with pytest.raises(ValidationError):
            DatasetConfig(
                name="test",
                kind="chat",
                sync_enabled=True,
                sources=[
                    DatasetSourceConfig(
                        plugin="claude_code",
                        projects_root="/tmp",
                        chunker="conversation",
                    )
                ],
            )

    def test_dataset_config_sync_enabled_chat_accepted_false(self):
        """Test DatasetConfig sync_enabled=False is accepted for kind='chat'."""
        config = DatasetConfig(
            name="test",
            kind="chat",
            sync_enabled=False,
            sources=[
                DatasetSourceConfig(
                    plugin="claude_code",
                    projects_root="/tmp",
                    chunker="conversation",
                )
            ],
        )
        assert config.sync_enabled is False

    def test_dataset_config_sync_enabled_from_toml_text(self, temp_dir):
        """Test DatasetConfig sync_enabled parsed from TOML with kind='text'."""
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
name = "my-text-dataset"
kind = "text"
sync_enabled = true
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "/tmp"
  chunker = "markdown"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
"""
        config_file = temp_dir / "config.toml"
        config_file.write_text(config_content)

        config = Config.load(config_path=config_file)
        assert config.datasets[0].sync_enabled is True

    def test_dataset_config_sync_enabled_from_toml_chat_rejected(self, temp_dir):
        """Test DatasetConfig sync_enabled=true with kind='chat' raises from TOML."""
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
name = "my-chat-dataset"
kind = "chat"
sync_enabled = true
  [[datasets.sources]]
  plugin = "claude_code"
  projects_root = "/tmp"
  chunker = "conversation"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test-model"
dimension = 384
"""
        config_file = temp_dir / "config.toml"
        config_file.write_text(config_content)

        with pytest.raises(ValidationError):
            Config.load(config_path=config_file)


class TestConfigHostId:
    """Tests for Config.host_id() resolution and persistence (P1-04)."""

    def test_explicit_host_id_from_daemon_config(self, temp_dir):
        """Explicit daemon.host_id is returned without file/hostname fallback."""
        config = Config(
            backend=BackendConfig(dsn="sqlite:///test.db"),
            daemon=DaemonConfig(
                debounce_seconds=2.0,
                log_level="INFO",
                log_format="text",
                host_id="explicit-host",
            ),
            datasets=[],
            embedders=[],
        )
        with patch("pathlib.Path.home", return_value=temp_dir):
            result = config.host_id()
        assert result == "explicit-host"

    def test_host_id_file_when_config_empty(self, temp_dir):
        """When daemon.host_id is empty, file at ~/.config/corpus-forge/host_id is used."""
        host_file = temp_dir / ".config" / "corpus-forge" / "host_id"
        host_file.parent.mkdir(parents=True)
        host_file.write_text("file-host\n")

        config = Config(
            backend=BackendConfig(dsn="sqlite:///test.db"),
            daemon=DaemonConfig(
                debounce_seconds=2.0,
                log_level="INFO",
                log_format="text",
                host_id="",
            ),
            datasets=[],
            embedders=[],
        )
        with patch("pathlib.Path.home", return_value=temp_dir):
            result = config.host_id()
        assert result == "file-host"

    def test_hostname_fallback_persisted(self, temp_dir):
        """When config and file lack host_id, socket.gethostname() is returned & persisted."""
        config = Config(
            backend=BackendConfig(dsn="sqlite:///test.db"),
            daemon=DaemonConfig(
                debounce_seconds=2.0,
                log_level="INFO",
                log_format="text",
                host_id="",
            ),
            datasets=[],
            embedders=[],
        )
        with (
            patch("pathlib.Path.home", return_value=temp_dir),
            patch("socket.gethostname", return_value="my-machine"),
        ):
            result = config.host_id()

        assert result == "my-machine"

        host_file = temp_dir / ".config" / "corpus-forge" / "host_id"
        assert host_file.read_text().strip() == "my-machine"

    def test_persisted_host_id_survives_hostname_change(self, temp_dir):
        """Once persisted, host_id file is read even if hostname changes."""
        host_file = temp_dir / ".config" / "corpus-forge" / "host_id"
        host_file.parent.mkdir(parents=True)
        host_file.write_text("persisted-host\n")

        config = Config(
            backend=BackendConfig(dsn="sqlite:///test.db"),
            daemon=DaemonConfig(
                debounce_seconds=2.0,
                log_level="INFO",
                log_format="text",
                host_id="",
            ),
            datasets=[],
            embedders=[],
        )
        with (
            patch("pathlib.Path.home", return_value=temp_dir),
            patch("socket.gethostname", return_value="different-machine"),
        ):
            result = config.host_id()

        assert result == "persisted-host"


EXACT_ERROR_MSG = (
    "Cross-host sync requires the postgres backend; SQLite is single-host. "
    "Set sync_enabled = false or switch backend.kind to 'postgres'."
)


def _make_text_source() -> DatasetSourceConfig:
    """Return a minimal text DatasetSourceConfig."""
    return DatasetSourceConfig(
        plugin="markdown_vault",
        vault_root="/tmp/vault",
        chunker="markdown",
    )


def _make_embedder() -> EmbedderConfig:
    """Return a minimal EmbedderConfig."""
    return EmbedderConfig(
        name="test-embedder",
        provider="sentence_transformers",
        model_id="test/model",
        dimension=384,
    )


def _sqlite_backend() -> BackendConfig:
    return BackendConfig(kind="sqlite", dsn="~/corpus.db")


def _postgres_backend() -> BackendConfig:
    return BackendConfig(kind="postgres", dsn="postgresql://user:pass@localhost/db")


def _minimal_daemon() -> DaemonConfig:
    return DaemonConfig(debounce_seconds=2.0, log_level="INFO", log_format="text")


class TestSyncGateValidator:
    """B-14: Config rejects sqlite + sync_enabled=True combination.

    Exact error message:
        'Cross-host sync requires the postgres backend; SQLite is single-host.
        Set sync_enabled = false or switch backend.kind to 'postgres'.'
    """

    # ------------------------------------------------------------------ #
    # Happy-path A: sqlite backend, all datasets sync_enabled=False → OK  #
    # ------------------------------------------------------------------ #

    def test_happy_path_sqlite_all_sync_disabled(self):
        """sqlite backend + all datasets sync_enabled=False should construct OK."""
        config = Config(
            backend=_sqlite_backend(),
            daemon=_minimal_daemon(),
            datasets=[
                DatasetConfig(
                    name="ds1",
                    kind="text",
                    sync_enabled=False,
                    sources=[_make_text_source()],
                ),
                DatasetConfig(
                    name="ds2",
                    kind="text",
                    sync_enabled=False,
                    sources=[_make_text_source()],
                ),
            ],
            embedders=[_make_embedder()],
        )
        assert config.backend.kind == "sqlite"
        assert all(not ds.sync_enabled for ds in config.datasets)

    def test_happy_path_sqlite_empty_datasets(self):
        """sqlite backend + no datasets at all should construct OK."""
        config = Config(
            backend=_sqlite_backend(),
            daemon=_minimal_daemon(),
            datasets=[],
            embedders=[_make_embedder()],
        )
        assert config.backend.kind == "sqlite"

    # ------------------------------------------------------------------ #
    # Happy-path B: postgres backend, mixed sync_enabled → OK             #
    # ------------------------------------------------------------------ #

    def test_happy_path_postgres_mixed_sync(self):
        """postgres backend + mixed sync_enabled (true + false) should construct OK."""
        config = Config(
            backend=_postgres_backend(),
            daemon=_minimal_daemon(),
            datasets=[
                DatasetConfig(
                    name="synced",
                    kind="text",
                    sync_enabled=True,
                    sources=[_make_text_source()],
                ),
                DatasetConfig(
                    name="not-synced",
                    kind="text",
                    sync_enabled=False,
                    sources=[_make_text_source()],
                ),
            ],
            embedders=[_make_embedder()],
        )
        assert config.backend.kind == "postgres"

    def test_happy_path_postgres_all_sync_enabled(self):
        """postgres backend + all datasets sync_enabled=True should construct OK."""
        config = Config(
            backend=_postgres_backend(),
            daemon=_minimal_daemon(),
            datasets=[
                DatasetConfig(
                    name="ds1",
                    kind="text",
                    sync_enabled=True,
                    sources=[_make_text_source()],
                ),
                DatasetConfig(
                    name="ds2",
                    kind="text",
                    sync_enabled=True,
                    sources=[_make_text_source()],
                ),
            ],
            embedders=[_make_embedder()],
        )
        assert config.backend.kind == "postgres"

    # ------------------------------------------------------------------ #
    # Rejection: sqlite + single dataset sync_enabled=True                #
    # ------------------------------------------------------------------ #

    def test_rejection_single_dataset_sync_enabled_sqlite(self):
        """sqlite backend + one dataset with sync_enabled=True raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            Config(
                backend=_sqlite_backend(),
                daemon=_minimal_daemon(),
                datasets=[
                    DatasetConfig(
                        name="my-vault",
                        kind="text",
                        sync_enabled=True,
                        sources=[_make_text_source()],
                    )
                ],
                embedders=[_make_embedder()],
            )
        error_str = str(exc_info.value)
        assert EXACT_ERROR_MSG in error_str

    def test_rejection_error_message_exact_text(self):
        """Verify the exact error message text matches the spec."""
        with pytest.raises(ValidationError) as exc_info:
            Config(
                backend=_sqlite_backend(),
                daemon=_minimal_daemon(),
                datasets=[
                    DatasetConfig(
                        name="vault",
                        kind="text",
                        sync_enabled=True,
                        sources=[_make_text_source()],
                    )
                ],
                embedders=[_make_embedder()],
            )
        # Must contain the verbatim spec message
        assert "Cross-host sync requires the postgres backend; SQLite is single-host." in str(
            exc_info.value
        )
        assert "Set sync_enabled = false or switch backend.kind to 'postgres'." in str(
            exc_info.value
        )

    # ------------------------------------------------------------------ #
    # Rejection: sqlite + multiple datasets, only one sync_enabled        #
    # ------------------------------------------------------------------ #

    def test_rejection_multiple_datasets_one_sync_enabled(self):
        """sqlite + two datasets where only one has sync_enabled=True raises ValidationError.

        Confirms the validator scans all datasets, not just the first.
        """
        with pytest.raises(ValidationError) as exc_info:
            Config(
                backend=_sqlite_backend(),
                daemon=_minimal_daemon(),
                datasets=[
                    DatasetConfig(
                        name="ok-dataset",
                        kind="text",
                        sync_enabled=False,
                        sources=[_make_text_source()],
                    ),
                    DatasetConfig(
                        name="bad-dataset",
                        kind="text",
                        sync_enabled=True,
                        sources=[_make_text_source()],
                    ),
                ],
                embedders=[_make_embedder()],
            )
        assert EXACT_ERROR_MSG in str(exc_info.value)

    def test_rejection_second_dataset_triggers_validator(self):
        """Validator triggers even when the FIRST dataset is fine but a later one is not."""
        with pytest.raises(ValidationError) as exc_info:
            Config(
                backend=_sqlite_backend(),
                daemon=_minimal_daemon(),
                datasets=[
                    DatasetConfig(
                        name="safe",
                        kind="text",
                        sync_enabled=False,
                        sources=[_make_text_source()],
                    ),
                    DatasetConfig(
                        name="safe-too",
                        kind="text",
                        sync_enabled=False,
                        sources=[_make_text_source()],
                    ),
                    DatasetConfig(
                        name="offender",
                        kind="text",
                        sync_enabled=True,
                        sources=[_make_text_source()],
                    ),
                ],
                embedders=[_make_embedder()],
            )
        assert EXACT_ERROR_MSG in str(exc_info.value)

    def test_rejection_all_datasets_sync_enabled_sqlite(self):
        """sqlite + all datasets sync_enabled=True raises ValidationError."""
        with pytest.raises(ValidationError):
            Config(
                backend=_sqlite_backend(),
                daemon=_minimal_daemon(),
                datasets=[
                    DatasetConfig(
                        name="a",
                        kind="text",
                        sync_enabled=True,
                        sources=[_make_text_source()],
                    ),
                    DatasetConfig(
                        name="b",
                        kind="text",
                        sync_enabled=True,
                        sources=[_make_text_source()],
                    ),
                ],
                embedders=[_make_embedder()],
            )

    # ------------------------------------------------------------------ #
    # Field-order invariance                                               #
    # ------------------------------------------------------------------ #

    def test_field_order_invariance_datasets_first(self):
        """Config(**dict) with datasets listed before backend still raises.

        Tests that Pydantic's model construction order doesn't mask the error.
        Python dicts preserve insertion order (3.7+) but Pydantic validates all
        fields before running model_validators, so the order should not matter.
        """
        kwargs = {
            "datasets": [
                DatasetConfig(
                    name="vault",
                    kind="text",
                    sync_enabled=True,
                    sources=[_make_text_source()],
                )
            ],
            "backend": _sqlite_backend(),
            "daemon": _minimal_daemon(),
            "embedders": [_make_embedder()],
        }
        with pytest.raises(ValidationError) as exc_info:
            Config(**kwargs)
        assert EXACT_ERROR_MSG in str(exc_info.value)

    def test_field_order_invariance_backend_first(self):
        """Config(**dict) with backend listed before datasets still raises."""
        kwargs = {
            "backend": _sqlite_backend(),
            "datasets": [
                DatasetConfig(
                    name="vault",
                    kind="text",
                    sync_enabled=True,
                    sources=[_make_text_source()],
                )
            ],
            "daemon": _minimal_daemon(),
            "embedders": [_make_embedder()],
        }
        with pytest.raises(ValidationError) as exc_info:
            Config(**kwargs)
        assert EXACT_ERROR_MSG in str(exc_info.value)

    # ------------------------------------------------------------------ #
    # TOML-based rejection                                                 #
    # ------------------------------------------------------------------ #

    def test_rejection_via_toml_load(self, temp_dir):
        """Loading a TOML file with sqlite + sync_enabled=true raises ValidationError."""
        toml_content = """\
[backend]
kind = "sqlite"
dsn = "~/corpus.db"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "my-vault"
kind = "text"
sync_enabled = true
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "~/vault"
  chunker = "markdown"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test/model"
dimension = 384
"""
        config_file = temp_dir / "config.toml"
        config_file.write_text(toml_content)
        with pytest.raises(ValidationError) as exc_info:
            Config.load(config_path=config_file)
        assert EXACT_ERROR_MSG in str(exc_info.value)

    def test_acceptance_via_toml_load_sqlite_sync_disabled(self, temp_dir):
        """Loading a TOML with sqlite + sync_enabled=false is accepted."""
        toml_content = """\
[backend]
kind = "sqlite"
dsn = "~/corpus.db"

[daemon]
debounce_seconds = 2.0
log_level = "INFO"
log_format = "text"

[[datasets]]
name = "my-vault"
kind = "text"
sync_enabled = false
  [[datasets.sources]]
  plugin = "markdown_vault"
  vault_root = "~/vault"
  chunker = "markdown"

[[embedders]]
name = "test-embedder"
provider = "sentence_transformers"
model_id = "test/model"
dimension = 384
"""
        config_file = temp_dir / "config.toml"
        config_file.write_text(toml_content)
        config = Config.load(config_path=config_file)
        assert config.backend.kind == "sqlite"
        assert config.datasets[0].sync_enabled is False

    # ------------------------------------------------------------------ #
    # Optional: naming the offending dataset in the error                 #
    # ------------------------------------------------------------------ #

    @pytest.mark.xfail(
        strict=False,
        reason="Nice-to-have: spec does not require naming the offending dataset in the error.",
    )
    def test_optional_error_names_offending_dataset(self):
        """OPTIONAL: ValidationError message names the offending dataset.

        The spec does not mandate this, but a good implementation might include
        it. If the implementation includes the dataset name, this test will pass;
        if not, it is an acceptable xfail (non-strict).
        """
        with pytest.raises(ValidationError) as exc_info:
            Config(
                backend=_sqlite_backend(),
                daemon=_minimal_daemon(),
                datasets=[
                    DatasetConfig(
                        name="offending-vault",
                        kind="text",
                        sync_enabled=True,
                        sources=[_make_text_source()],
                    )
                ],
                embedders=[_make_embedder()],
            )
        assert "offending-vault" in str(exc_info.value)


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
