"""Configuration management for corpus-forge."""

import os
import socket
from pathlib import Path
from typing import Annotated, Literal

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found]
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.functional_validators import AfterValidator


def expand_user(path: str) -> str:
    """Expand ~ to user's home directory."""
    return str(Path(path).expanduser())


def interpolate_env_vars(value: str) -> str:
    """Interpolate ${VAR} style environment variables."""
    return os.path.expandvars(value)


# Type aliases with validation
ExpandedPath = Annotated[str, AfterValidator(expand_user)]
EnvInterpolatedStr = Annotated[str, AfterValidator(interpolate_env_vars)]


# Re-export for testing
ExpandUser = expand_user


class BackendConfig(BaseModel):
    """Backend configuration."""

    kind: str = Field(default="postgres", pattern="^(postgres|sqlite)$")
    dsn: EnvInterpolatedStr
    schema: str = Field(default="corpus")  # pyrefly: ignore[bad-override-mutable-attribute]  # Pydantic v2's BaseModel.schema() is deprecated; field shadow is intentional


class DaemonConfig(BaseModel):
    """Daemon configuration."""

    debounce_seconds: float = Field(default=2.0, gt=0)
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")
    log_format: str = Field(default="text", pattern="^(text|json)$")
    # Sync fields
    host_id: str = ""
    trash_dir: ExpandedPath = "~/.local/share/corpus-forge/trash"
    conflict_dir: ExpandedPath = ""
    sync_poll_interval_s: float = Field(default=5.0, gt=0)
    sync_use_listen_notify: bool = False


class DatasetSourceConfig(BaseModel):
    """Configuration for a dataset source."""

    plugin: str
    # Text source fields
    vault_root: ExpandedPath | None = None
    exclude_globs: list[str] = Field(default_factory=lambda: [".obsidian/**", ".trash/**", ".*"])
    vault_names: list[str] = Field(default_factory=list)
    # Chat source fields
    projects_root: ExpandedPath | None = None
    include_subagents: bool = Field(default=True)
    storage_root: ExpandedPath | None = None
    # Chunker configuration
    chunker: str = Field(pattern="^(markdown|conversation)$")
    chunker_config: dict = Field(default_factory=dict)


class DatasetConfig(BaseModel):
    """Configuration for a dataset."""

    name: str
    kind: str = Field(pattern="^(text|chat)$")
    description: str | None = None
    sources: list[DatasetSourceConfig]
    sync_enabled: bool = False

    @model_validator(mode="after")
    def _validate_sync_enabled(self):
        """Reject sync_enabled=True for non-text datasets."""
        if self.sync_enabled and self.kind != "text":
            raise ValueError(
                f"Dataset '{self.name}': sync_enabled is only allowed for kind='text', "
                f"not kind='{self.kind}'"
            )
        return self


class EmbedderConfig(BaseModel):
    """Configuration for an embedder."""

    name: str
    provider: str = Field(pattern="^(sentence_transformers|openai)$")
    model_id: str
    dimension: int = Field(gt=0)
    normalize: bool = Field(default=True)
    distance: str = Field(default="cosine", pattern="^(cosine|l2|ip)$")
    active: bool = Field(default=True)
    batch_size: int = Field(default=32, gt=0)
    device: str = Field(default="auto")
    api_key_env: str = Field(default="OPENAI_API_KEY")


class RerankerConfig(BaseModel):
    """Phase R4 — reranker shape under ``RetrievalConfig.reranker``.

    Defaults match the master-plan-locked decision: cross-encoder kind,
    ``BAAI/bge-reranker-v2-m3`` (multilingual, ~600 MB).  Override
    ``model_id`` to ``cross-encoder/ms-marco-MiniLM-L-12-v2`` for the
    lighter English-only alternate, or set ``kind="ollama"`` + a chat
    model tag for the score-via-completion fallback.

    The reranker is constructed only when the user enables it (CLI
    ``--rerank`` or ``Config.retrieval.rerank_enabled = True``).  Default
    is to ship the config but NOT instantiate the reranker, so the
    600 MB model download never happens by accident.
    """

    kind: Literal["cross_encoder", "ollama"] = "cross_encoder"
    model_id: str = "BAAI/bge-reranker-v2-m3"
    device: str = "auto"
    batch_size: int = Field(default=32, gt=0)
    max_length: int = Field(default=512, gt=0)


class RetrievalConfig(BaseModel):
    """Phase R2 + R4 — hybrid retrieval knobs.

    Defaults match the master plan verbatim:

    - ``alpha = 0.5`` (50/50 blend when ``fusion="alpha"``).
    - ``fusion = "rrf"`` (rank-based reciprocal-rank fusion; default).
    - ``default_k = 10`` (top-k returned to callers when unset).
    - ``rerank_top_n = 50`` (R4 reranker cap; how many fused hits go to
      the reranker BEFORE truncation to k).
    - ``rerank_enabled = False`` (the rerank path is opt-in; default
      behaviour does not call the reranker even if it's configured).
    - ``reranker`` (R4): nested config of the cross-encoder / Ollama
      reranker, used only when rerank is enabled.
    """

    alpha: float = Field(default=0.5, ge=0.0, le=1.0)
    fusion: Literal["rrf", "alpha"] = "rrf"
    default_k: int = Field(default=10, gt=0)
    rerank_top_n: int = Field(default=50, gt=0)
    rerank_enabled: bool = False
    reranker: RerankerConfig = Field(default_factory=RerankerConfig)


class Config(BaseModel):
    """Main configuration for corpus-forge."""

    backend: BackendConfig
    daemon: DaemonConfig
    datasets: list[DatasetConfig]
    embedders: list[EmbedderConfig]
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)

    model_config = ConfigDict(
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    @model_validator(mode="after")
    def validate_dataset_sources(self):
        """Validate that each dataset has at least one source."""
        for dataset in self.datasets:
            if not dataset.sources:
                raise ValueError(f"Dataset '{dataset.name}' must have at least one source")
        return self

    @model_validator(mode="after")
    def validate_sync_gate(self):
        """Reject sync_enabled=True for any dataset when backend is sqlite.

        SQLite is single-host; cross-host sync requires the Postgres backend.
        """
        if self.backend.kind == "sqlite" and any(ds.sync_enabled for ds in self.datasets):
            raise ValueError(
                "Cross-host sync requires the postgres backend; SQLite is single-host. "
                "Set sync_enabled = false or switch backend.kind to 'postgres'."
            )
        return self

    def host_id(self) -> str:
        if self.daemon.host_id:
            return self.daemon.host_id
        host_id_path = Path.home() / ".config" / "corpus-forge" / "host_id"
        if host_id_path.exists():
            return host_id_path.read_text().strip()
        hostname = socket.gethostname()
        host_id_path.parent.mkdir(parents=True, exist_ok=True)
        host_id_path.write_text(hostname)
        return hostname

    @classmethod
    def load(cls, config_path: Path | None = None, secrets_path: Path | None = None) -> "Config":
        """Load configuration from TOML file and optional secrets file.

        Resolution order for ``config_path``:

        1. Explicit ``config_path`` argument (used by tests).
        2. ``CORPUS_FORGE_CONFIG`` environment variable.  Added in
           Phase R5 so subprocess-driven smoke tests (and Claude Desktop
           launchers that already set env vars) can point at a custom
           config without writing to ``~/.config``.
        3. ``~/.config/corpus-forge/config.toml`` (the default).
        """
        if config_path is None:
            env_path = os.environ.get("CORPUS_FORGE_CONFIG")
            if env_path:
                config_path = Path(env_path)
            else:
                config_path = Path.home() / ".config" / "corpus-forge" / "config.toml"

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        # Load main config
        with config_path.open("rb") as f:
            config_data = tomllib.load(f)

        # Load secrets if provided and exists
        if secrets_path is None:
            secrets_path = Path.home() / ".config" / "corpus-forge" / "secrets.env"

        if secrets_path.exists():
            # Load secrets as environment variables
            with secrets_path.open() as f:
                for _line in f:
                    line = _line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        os.environ[key.strip()] = value.strip()

        return cls(**config_data)


# Global config instance (lazy loaded)
_config: Config | None = None


def get_config() -> Config:
    """Get the global config instance, loading it if necessary."""
    # pyrefly: ignore PLW0603
    global _config  # noqa: PLW0603
    if _config is None:
        _config = Config.load()
    return _config


def reload_config() -> Config:
    """Force reload of the configuration."""
    # pyrefly: ignore PLW0603
    global _config  # noqa: PLW0603
    _config = Config.load()
    return _config
