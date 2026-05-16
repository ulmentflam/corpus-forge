"""Configuration management for corpus-forge."""

import os
import re
import socket
from pathlib import Path
from typing import Annotated, Literal

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found]
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator
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


class ExtractionConfig(BaseModel):
    """Phase D — per-source extractor feature flags.

    Attached optionally to :class:`DatasetSourceConfig` so a heterogeneous
    `filesystem` source can disable expensive extractor families without
    rebuilding the whole pipeline. Defaults to *every flag on* — the
    common case is "ingest everything I have a parser for".

    P1 (VLM/OCR) fields (Wave 5, E-05/E-06):

    - ``ocr_enabled``: master switch for the PDF escalation path and
      the ImageExtractor. ``True`` by default — when the user installed
      ``[ocr]`` and configured a VLM, OCR is on. Setting ``False`` is
      the hard-off switch (overrides the sparse-text-layer signal for
      PDFs and prevents the ImageExtractor from being registered).
    - ``ocr_min_chars_per_page``: average chars-per-page threshold
      below which the PDF extractor escalates to Tier 2 OCR (default
      100). Mirrors the ``_SPARSE_CHARS_PER_PAGE`` constant the D-07
      digital extractor already uses for its ``sparse_text_layer``
      signal — moved into config so users can tune it.
    - ``ocr_dpi``: dots-per-inch passed to ``pdf2image.convert_from_path``
      for the Tier 2 rasterisation pass. 200 is a good balance between
      VLM context budget and recognition quality.
    - ``enable_image``: gate for the ImageExtractor (``.png``, ``.jpg``,
      etc.). When True (default) AND a real VLM is wired in AND
      ``ocr_enabled`` is True, the registry registers the extractor.
    """

    enable_pdf: bool = True
    enable_office: bool = True
    enable_code: bool = True
    enable_html: bool = True
    enable_epub: bool = True
    enable_notebook: bool = True
    enable_csv: bool = True
    enable_image: bool = True
    code_chunker_config: dict = Field(
        default_factory=lambda: {"max_chars": 1500, "min_chars": 100, "overlap": 100}
    )
    # D-12 (Wave 1): row cap for the CsvExtractor. Tables longer than
    # ``csv_max_rows`` are sampled via ``head(csv_max_rows)`` and the
    # resulting ``ExtractedDocument.metadata`` flags ``truncated=True``
    # so callers can decide whether to fan out additional ingestion.
    csv_max_rows: int = Field(default=200, gt=0)
    # D-14 (Wave 2): soft cap for file size in the ``FilesystemSource``
    # walker. Files larger than ``max_bytes`` are skipped with a WARNING
    # log entry — keeps a stray multi-GB log file from blowing up an
    # otherwise-healthy ingest. Default 50 MB.
    max_bytes: int = Field(default=50_000_000, gt=0)
    # E-05 (Wave 5): OCR escalation tunables. See class docstring.
    ocr_enabled: bool = True
    ocr_min_chars_per_page: int = Field(default=100, gt=0)
    ocr_dpi: int = Field(default=200, gt=0)

    model_config = ConfigDict(extra="forbid")


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
    # Phase D / Wave 2 (D-15) — generic root for the ``filesystem`` source.
    # Existing per-plugin path fields (``vault_root``, etc.) stay for
    # backwards compatibility. New ``filesystem`` plugin uses ``root``.
    root: ExpandedPath | None = None
    # Chunker configuration
    chunker: str = Field(pattern="^(markdown|conversation)$")
    chunker_config: dict = Field(default_factory=dict)
    # Phase D — multi-format extractor feature flags. Optional; legacy
    # markdown_vault / chat sources leave this None.
    extraction: ExtractionConfig | None = None


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


_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class VLMConfig(BaseModel):
    """Phase D / Wave 4 (E-04) — VLM (vision-language model) backend config.

    Drives :func:`corpus_forge.vlm.get_active_vlm`. Default ``backend = "none"``
    means the existing markdown_vault / claude_code / opencode flows are
    untouched — no OCR layer is constructed.

    Fields:

    - ``backend``: ``"ollama" | "mistral" | "none"``. Required to opt in to OCR.
    - ``ollama_model``: tag on the local Ollama daemon; default
      ``qwen2.5vl:7b`` (Apache-2.0, ~5 GB, DocVQA 95.7).
    - ``ollama_url``: base URL of the daemon (``/api/generate`` is
      appended by the backend).
    - ``mistral_model``: model id for the Mistral OCR endpoint.
    - ``mistral_base_url``: API base; the backend appends ``/ocr``.
    - ``mistral_api_key_env``: name of the env var holding the key
      (read from ``secrets.env``). Validated as a POSIX identifier.
    - ``timeout_s``: per-request budget for both backends.
    """

    backend: Literal["ollama", "mistral", "none"] = "none"
    ollama_model: str = "qwen2.5vl:7b"
    ollama_url: AnyHttpUrl = AnyHttpUrl("http://localhost:11434")
    mistral_model: str = "mistral-ocr-2503"
    mistral_base_url: AnyHttpUrl = AnyHttpUrl("https://api.mistral.ai/v1")
    mistral_api_key_env: str = "MISTRAL_API_KEY"
    timeout_s: float = Field(120.0, gt=0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_mistral_env_var_name(self) -> "VLMConfig":
        """Reject ``mistral_api_key_env`` values that aren't valid
        POSIX environment variable names.

        Catches typos (``"MY KEY"`` with a space, ``"123KEY"`` starting
        with a digit, ``"MY-KEY"`` with a dash) at config-load time
        instead of silently producing ``None`` at runtime.
        """
        if not _ENV_VAR_NAME_RE.match(self.mistral_api_key_env):
            raise ValueError(
                f"mistral_api_key_env={self.mistral_api_key_env!r} is not a valid "
                "POSIX environment variable name (ASCII letters / digits / "
                "underscore; cannot start with a digit)."
            )
        return self


class WhisperConfig(BaseModel):
    """Phase G — Whisper transcription backend config.

    Drives :func:`corpus_forge.whisper.get_active_whisper`. Default
    ``backend = "none"`` means audio/video files are silently skipped
    on ingest — no transcription layer is constructed.

    Fields:

    - ``backend``: ``"local" | "remote" | "none"``. Required to opt in
      to audio/video transcription.
    - ``model``: Whisper model tag. For ``local``, one of
      ``tiny | base | small | medium | large`` (default ``small``).
      For ``remote``, provider-specific (e.g. ``whisper-1`` for
      OpenAI, ``whisper-large-v3`` for Groq).
    - ``local_compute_type``: precision for ``faster-whisper``.
      ``auto`` lets the backend pick (float16 on CUDA/MPS, int8 on
      CPU). Use ``float16`` / ``int8`` to force.
    - ``remote_base_url``: base URL of any OpenAI-compatible Whisper
      endpoint (the backend appends ``/audio/transcriptions``).
      Default OpenAI; swap to Groq / Replicate / self-hosted
      whisper.cpp without changing code.
    - ``remote_api_key_env``: name of the env var holding the API key
      (read from ``secrets.env``). Validated as a POSIX identifier.
    - ``timeout_s``: per-request HTTP budget for the remote backend
      (also used as the local backend's per-file wall budget).
    - ``language``: ISO-639-1 hint. Empty string ``""`` = auto-detect.

    Cross-cutting: every model client in corpus-forge supports a
    configurable local-or-remote URL — see
    ``project_model_local_or_remote.md`` and the ``[vlm]`` / ``[classifier]``
    blocks. The remote Whisper path shows the same pattern.
    """

    backend: Literal["local", "remote", "none"] = "none"
    model: str = "small"
    local_compute_type: Literal["auto", "float16", "int8", "int8_float16"] = "auto"
    remote_base_url: AnyHttpUrl = AnyHttpUrl("https://api.openai.com/v1")
    remote_api_key_env: str = "OPENAI_API_KEY"
    timeout_s: float = Field(300.0, gt=0)
    language: str = ""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_remote_env_var_name(self) -> "WhisperConfig":
        """Reject ``remote_api_key_env`` values that aren't valid POSIX
        environment variable names.

        Catches typos (``"MY KEY"`` with a space, ``"123KEY"`` starting
        with a digit, ``"MY-KEY"`` with a dash) at config-load time
        instead of silently producing ``None`` at runtime.
        """
        if not _ENV_VAR_NAME_RE.match(self.remote_api_key_env):
            raise ValueError(
                f"remote_api_key_env={self.remote_api_key_env!r} is not a valid "
                "POSIX environment variable name (ASCII letters / digits / "
                "underscore; cannot start with a digit)."
            )
        return self


class ClassifierConfig(BaseModel):
    """Phase E — document-classification chain config.

    Drives :func:`corpus_forge.classifiers.register_default_classifiers`.

    P0 shipped a stdlib rule-based classifier. P1 (C-10/C-11) wires in
    the LLM classifier and flips the default chain to
    ``["rule", "llm"]`` — the rule classifier short-circuits high-
    confidence documents (microseconds/doc) and the LLM picks up the
    weak / ambiguous cases.

    **Cross-cutting: local-or-remote URL.** ``llm_url`` is the base
    URL of any Ollama-compatible endpoint. Default
    ``http://localhost:11434`` (local). Swap to a remote URL to point
    at a hosted Ollama / vLLM / OpenAI-shape proxy without changing
    code. Same principle as :attr:`VLMConfig.ollama_url`.

    Fields:

    - ``chain``: ordered list of classifier names. Each name must be a
      key in :data:`corpus_forge.classifiers._CLASSIFIER_REGISTRY`.
      Default ``["rule", "llm"]``.
    - ``escalation_threshold``: confidence floor used by
      :meth:`ClassifierRegistry.classify`. Below this, the chain walks
      to the next classifier.
    - ``llm_model``: Ollama tag.
    - ``llm_url``: base URL of the Ollama-compatible endpoint
      (``/api/generate`` is appended by the backend). Local-or-remote.
    - ``llm_timeout_s``: per-request HTTP budget.
    - ``llm_temperature``: sampling temperature (``[0.0, 2.0]``).
    - ``llm_excerpt_chars``: total head+tail budget passed to the model.
    """

    chain: list[str] = Field(default_factory=lambda: ["rule", "llm"])
    escalation_threshold: float = Field(default=0.4, ge=0.0, le=1.0)
    # ── LLM fields ────────────────────────────────────────────────────
    llm_model: str = "qwen2.5:7b-instruct"
    # Pydantic v2 quirk: ``AnyHttpUrl`` defaults must be wrapped in the
    # class (not bare strings). Mirrors the proven pattern from
    # :attr:`VLMConfig.ollama_url`.
    llm_url: AnyHttpUrl = AnyHttpUrl("http://localhost:11434")
    llm_timeout_s: float = Field(default=60.0, gt=0)
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_excerpt_chars: int = Field(default=2000, gt=0)

    model_config = ConfigDict(extra="forbid")


class Config(BaseModel):
    """Main configuration for corpus-forge."""

    backend: BackendConfig
    daemon: DaemonConfig
    datasets: list[DatasetConfig]
    embedders: list[EmbedderConfig]
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    # Phase D / Wave 4 (E-04) — VLM backend selector. Default Noop so
    # adding the field doesn't change behaviour for existing configs.
    vlm: VLMConfig = Field(default_factory=VLMConfig)
    # Phase E (C-03) — document-classification chain. Defaults to
    # rule-only so existing configs without a ``[classifier]`` block
    # opt in to the rule classifier transparently.
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    # Phase G (G-04) — Whisper transcription backend selector. Default
    # backend="none" so adding the field doesn't change behaviour for
    # existing configs (audio/video files were unsupported pre-Phase-G;
    # they remain silently skipped until the user opts in).
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)

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

    def resolve_mistral_api_key(self) -> str | None:
        """Read the Mistral API key from the configured env var.

        Returns ``None`` when the env var is unset. The caller (typically
        :func:`corpus_forge.vlm.get_active_vlm`) decides whether the
        absence is fatal — when ``vlm.backend == "mistral"`` it is, when
        ``"none"`` / ``"ollama"`` it isn't.
        """
        return os.environ.get(self.vlm.mistral_api_key_env)

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
