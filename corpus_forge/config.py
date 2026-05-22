"""Configuration management for corpus-forge."""

import os
import re
import socket
import warnings
from pathlib import Path
from typing import Annotated, Literal

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore[import-not-found]

# Pydantic v2 emits a UserWarning when a field name shadows an attribute
# on a parent model — ``BackendConfig.schema`` (TOML key ``schema =
# "corpus"``) shadows the deprecated ``BaseModel.schema()`` method. We
# intentionally keep the field name because it's part of the public TOML
# surface and is read at 15+ call sites as ``config.backend.schema``;
# renaming would break every existing config in the wild. The warning
# itself is informational, so suppress this exact message before the
# class is defined.
warnings.filterwarnings(
    "ignore",
    message=re.escape('Field name "schema" in "BackendConfig"')
    + r'.*shadows an attribute in parent "BaseModel"',
    category=UserWarning,
)

from pydantic import (  # noqa: E402
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic.functional_validators import AfterValidator  # noqa: E402


def expand_user(path: str) -> str:
    """Expand ~ to user's home directory.

    Empty / whitespace-only values are passed through unchanged so they
    can be distinguished from a literal ``"."`` (which is what
    ``Path("").expanduser()`` would otherwise produce, silently aliasing
    "field unset" to "current working directory").
    """
    if not path or not path.strip():
        return path
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
    # pyrefly: ignore[bad-override-mutable-attribute]
    # Pydantic v2's ``BaseModel.schema()`` is deprecated; shadowing it with
    # this field is intentional and the resulting UserWarning is silenced
    # at module load above. See the ``warnings.filterwarnings`` block.
    schema: str = Field(default="corpus")


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


class ZoteroSourceConfig(BaseModel):
    """Phase M Wave 4 — Zotero library connector source config.

    Attached as a nested ``zotero`` field on :class:`DatasetSourceConfig`
    when ``plugin == "zotero"``. Three modes:

    - ``local`` (default) — read ``zotero.sqlite`` directly via
      ``sqlite3.connect("file:...?mode=ro&immutable=1", uri=True)``.
      Requires the user to have a local Zotero install. Picks up the
      default library path automatically when ``library_path`` is unset
      (resolved at source-construction time).
    - ``web`` — talk to ``api.zotero.org``. Requires ``user_id`` and an
      API key in the env var named by ``api_key_env``.
    - ``both`` — read both; reconcile on ``zotero_item_key``. Local wins
      unless the web's ``dateModified`` is strictly newer.

    The API key is indirected through an env-var name (``api_key_env``)
    rather than a literal token field so the same config can be
    committed without leaking credentials. ``ZOTERO_API_KEY`` is the
    canonical default; rename only if it collides with another tool.
    """

    mode: Literal["local", "web", "both"] = "local"
    library_path: ExpandedPath | None = None
    user_id: str | None = None
    api_key_env: str = "ZOTERO_API_KEY"
    library_type: Literal["user", "group"] = "user"
    group_id: str | None = None
    base_url: AnyHttpUrl = AnyHttpUrl("https://api.zotero.org")
    include_attachments: list[str] = Field(default_factory=lambda: ["application/pdf"])
    include_collections: list[str] = Field(default_factory=list)
    exclude_collections: list[str] = Field(default_factory=list)
    cache_dir: ExpandedPath | None = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_mode_credentials(self) -> "ZoteroSourceConfig":
        if self.mode in ("web", "both") and not self.user_id:
            raise ValueError(
                f"ZoteroSourceConfig(mode={self.mode!r}) requires user_id "
                "(numeric Zotero user/group id)."
            )
        if self.library_type == "group" and not self.group_id:
            raise ValueError("ZoteroSourceConfig(library_type='group') requires group_id.")
        _validate_env_var_name("api_key_env", self.api_key_env)
        return self


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
    # Optional ``~/.claude/history.jsonl`` location for the ``claude_code``
    # plugin. When set, the user's typed-prompt log (incl. pasted content)
    # is ingested as additional history-only conversations distinct from
    # the per-session JSONLs under ``projects_root``.
    history_path: ExpandedPath | None = None
    storage_root: ExpandedPath | None = None
    # ``gemini_cli`` walks ``<chats_root>/<projectHash>/chats/*.json``.
    chats_root: ExpandedPath | None = None
    # ``codex_cli`` walks ``<sessions_root>`` recursively for
    # ``rollout-*.jsonl`` (modern) or flat ``*.jsonl`` (legacy).
    sessions_root: ExpandedPath | None = None
    # ``chatgpt_export`` reads ``<export_root>/conversations.json``.
    export_root: ExpandedPath | None = None
    # ``jsonl_chat`` reads either a directory of ``*.jsonl`` files or a
    # single file. Use this generic plugin for any non-CLI chat exports
    # already in ``{role, content, ts}`` shape.
    path: ExpandedPath | None = None
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
    # Phase M Wave 4 — Zotero library connector. Nested block so the
    # large set of mode-conditional fields doesn't pollute the
    # ``DatasetSourceConfig`` namespace.
    zotero: ZoteroSourceConfig | None = None
    # RFC ``rfc-corpus-growth-controls`` — per-source growth caps. When
    # set, the ingest loop enforces these AFTER each batch insert and
    # evicts the lowest-scoring rows from this source (LRU + score) to
    # make room. ``None`` (the default for both) means no cap — the
    # source can grow without bound. The ingest-side eviction loop
    # honours the global ``GrowthConfig.per_source_cap_default_rows``
    # fallback when ``max_rows`` is None but the global default is
    # non-zero. The eviction policy itself lands in a future RFC PR;
    # this field is the storage.
    max_rows: int | None = Field(default=None, gt=0)
    max_bytes: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _default_zotero_block_when_plugin_is_zotero(self) -> "DatasetSourceConfig":
        """When ``plugin == "zotero"`` but no ``[datasets.sources.zotero]``
        block is present, instantiate a default ``ZoteroSourceConfig``.

        Without this default, doctor's Zotero check silently SKIPs a
        source declared as ``plugin = "zotero"`` because its ``zotero``
        attribute is ``None`` — the Phase M Wave 4 source-nesting bug
        the user kept hitting. ``ZoteroSourceConfig`` has sensible
        defaults for local mode (``library_path`` resolves a platform
        default at source-construction time), so an empty block is a
        valid declaration.
        """
        if self.plugin == "zotero" and self.zotero is None:
            self.zotero = ZoteroSourceConfig()
        return self


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
    """Configuration for a single embedder.

    The ``base_url`` field (provider=``openai`` only) accepts any
    OpenAI-compatible endpoint — local vLLM, llama.cpp's OpenAI shim,
    LiteLLM, etc. — so the same embedder swaps between a hosted API
    and a local proxy by config alone. Cross-cutting with VLM /
    Whisper / classifier / enricher config blocks: every model
    integration supports the local-or-remote URL pattern.
    """

    name: str
    # Phase N Wave 3 added ``"model2vec"`` for the static fast-tier
    # provider (potion-code-16M).  Optional ``[fast-tier]`` extra at
    # install time; the dispatch lives in ``embedders/registry.py``.
    provider: str = Field(pattern="^(sentence_transformers|openai|model2vec)$")
    model_id: str
    dimension: int = Field(gt=0)
    normalize: bool = Field(default=True)
    distance: str = Field(default="cosine", pattern="^(cosine|l2|ip)$")
    active: bool = Field(default=True)
    batch_size: int = Field(default=32, gt=0)
    device: str = Field(default="auto")
    api_key_env: str = Field(default="OPENAI_API_KEY")
    base_url: AnyHttpUrl | None = Field(default=None)


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
    """Phase R2 + R4 + Phase N Wave 1 — hybrid retrieval knobs.

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

    Phase N Wave 1 — adaptive lexical-weight bump (default OFF):

    - ``adaptive_lexical_weight``: when True AND ``fusion=="alpha"``
      AND the query is symbol-shaped (per
      :func:`corpus_forge.retrieval.query_shape.is_symbol_shaped`),
      the effective alpha passed to :func:`alpha_blend` drops to
      ``symbol_query_alpha``.  This raises the BM25 contribution on
      identifier / accessor queries where lexical match is the right
      retrieval signal.
    - ``symbol_query_alpha``: the alpha to swap in when the bump fires.
      Validated to ``[0, 1]`` like the headline ``alpha``.  Default
      ``0.3`` was Wave 1's starting guess; the wave-gate test is the
      arbiter on whether to retune.

    Phase N Wave 2 — definition boost on retrieval (default OFF):

    - ``definition_boost_enabled``: when True, any retrieved hit whose
      metadata carries ``is_definition=True`` AND whose ``name`` matches
      a token in the query gets its score multiplied by the boost
      factors below.  The boost fires in TWO places:
        - Pre-rerank, on the fused score dict before the rerank-slice
          truncation (multiplier ``definition_boost_factor_pre_rerank``);
        - Post-rerank, on the reranker's output (multiplier
          ``definition_boost_factor_post_rerank``).
      Wave 1's bench investigation found that the cross-encoder
      reranker emits its own scores and discards upstream fused scores,
      so the post-rerank application is the load-bearing one.  Both are
      enabled by the same flag and tuned independently.
    - ``definition_boost_factor_pre_rerank``: multiplier applied to a
      matching definition's fused score before the rerank slice.
      Validated to ``[1.0, 5.0]`` — below 1.0 would be a penalty, not
      a boost; above 5.0 would dominate fusion math.  Default ``1.5``.
    - ``definition_boost_factor_post_rerank``: multiplier applied to a
      matching definition's reranked score after the reranker returns.
      Same validation window.  Default ``1.2`` (smaller than the
      pre-rerank factor because the reranker's score scale is already
      tight and a heavy multiplier swamps it).
    """

    alpha: float = Field(default=0.5, ge=0.0, le=1.0)
    fusion: Literal["rrf", "alpha"] = "rrf"
    default_k: int = Field(default=10, gt=0)
    rerank_top_n: int = Field(default=50, gt=0)
    rerank_enabled: bool = False
    reranker: RerankerConfig = Field(default_factory=RerankerConfig)
    adaptive_lexical_weight: bool = False
    symbol_query_alpha: float = Field(default=0.3, ge=0.0, le=1.0)
    definition_boost_enabled: bool = False
    definition_boost_factor_pre_rerank: float = Field(default=1.5, ge=1.0, le=5.0)
    definition_boost_factor_post_rerank: float = Field(default=1.2, ge=1.0, le=5.0)
    # Phase N Wave 3 — fast-tier embedder cross-reference.  Names an
    # entry in ``Config.embedders`` that runs as a candidate generator
    # when ``SearchOptions.fast_tier_mode != "skip"``.  Validated at
    # ``Config`` load time (see ``Config._check_fast_tier_embedder``)
    # so a typo surfaces before any search call.
    fast_tier_embedder_name: str | None = None


_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_env_var_name(field_name: str, value: str, *, allow_empty: bool = False) -> str:
    """Reject ``value`` if it isn't a valid POSIX env-var identifier.

    Catches typos (``"MY KEY"`` with a space, ``"123KEY"`` starting with
    a digit, ``"MY-KEY"`` with a dash) at config-load time instead of
    silently producing ``None`` at runtime.

    Pass ``allow_empty=True`` for config fields whose default is empty
    (the classifier's optional ``llm_api_key_env``); the remote
    backends that require a key on opt-in keep ``allow_empty=False``
    so a misconfigured empty string still surfaces a clear error.
    """
    if allow_empty and not value:
        return value
    if not _ENV_VAR_NAME_RE.match(value):
        raise ValueError(
            f"{field_name}={value!r} is not a valid POSIX environment "
            "variable name (ASCII letters / digits / underscore; cannot "
            "start with a digit)."
        )
    return value


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
        _validate_env_var_name("mistral_api_key_env", self.mistral_api_key_env)
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
        _validate_env_var_name("remote_api_key_env", self.remote_api_key_env)
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
    - ``llm_api_key_env``: optional name of an env var holding a bearer
      token (read from ``secrets.env``). Empty string (default) omits
      the ``Authorization`` header — preserves the open-local-Ollama
      shape. Set this to swap the same backend onto a hosted
      authenticated endpoint without touching code.
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
    llm_api_key_env: str = ""
    llm_timeout_s: float = Field(default=60.0, gt=0)
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_excerpt_chars: int = Field(default=2000, gt=0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_llm_api_key_env_name(self) -> "ClassifierConfig":
        _validate_env_var_name("llm_api_key_env", self.llm_api_key_env, allow_empty=True)
        return self


class EnricherConfig(BaseModel):
    """Phase H — code-enrichment backend config.

    Drives :func:`corpus_forge.enrichers.get_active_enricher`. Default
    ``backend = "none"`` keeps legacy configs untouched — Phase H is
    opt-in: no enrichment runs until the user flips this knob.

    **Cross-cutting: local-or-remote URL.** Two concrete backends
    (separate classes, separate config fields):

    - ``"local"`` → :class:`corpus_forge.enrichers.qwen_local.QwenCoderLocal`
      against ``local_url`` (default ``http://localhost:11434``).
    - ``"remote"`` → :class:`corpus_forge.enrichers.qwen_remote.QwenCoderRemote`
      against ``remote_url``; speaks either the Ollama
      ``/api/generate`` shape (default) or the OpenAI chat-completions
      shape via ``remote_api_shape``.

    Fields:

    - ``backend``: ``"none" | "local" | "remote"``. Default ``"none"``.
    - ``local_model``: Ollama tag for the local backend.
    - ``local_url``: base URL of the local Ollama-compatible endpoint.
    - ``remote_model``: model tag for the remote backend.
    - ``remote_url``: base URL of the remote endpoint.
    - ``remote_api_shape``: ``"ollama" | "openai"`` — selects request
      envelope on the remote backend. Local backend always uses Ollama.
    - ``remote_api_key_env``: name of the env var holding the API key
      (read from ``secrets.env``). Validated as a POSIX identifier.
    - ``timeout_s``: per-request HTTP budget.
    - ``temperature``: sampling temperature in ``[0.0, 2.0]``.
    """

    backend: Literal["local", "remote", "none"] = "none"
    local_model: str = "qwen3.6:35b-a3b-instruct"
    local_url: AnyHttpUrl = AnyHttpUrl("http://localhost:11434")
    remote_model: str = "qwen3.6:35b-a3b-instruct"
    remote_url: AnyHttpUrl = AnyHttpUrl("http://localhost:11434")
    remote_api_shape: Literal["ollama", "openai"] = "ollama"
    remote_api_key_env: str = "OLLAMA_API_KEY"
    timeout_s: float = Field(180.0, gt=0)
    temperature: float = Field(0.1, ge=0.0, le=2.0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_remote_env_var_name(self) -> "EnricherConfig":
        _validate_env_var_name("remote_api_key_env", self.remote_api_key_env)
        return self


class OllamaConfig(BaseModel):
    """Phase L Wave 7 — Ollama daemon endpoint.

    Used by the ``corpus-forge ollama ...`` admin verbs (list / get /
    pull / set-url / test).  Other model integrations (VLM, classifier,
    code-enricher) keep their own ``*_url`` fields so the existing
    local-or-remote URL pattern works per-integration; this top-level
    block is just the canonical "where does ``corpus-forge ollama
    pull``  go" pointer.

    Migration: existing configs have no ``[ollama]`` section.  The
    default field below means they continue to validate as-is —
    ``Config(**old_toml)`` simply gets the default-constructed
    ``OllamaConfig`` instance.
    """

    base_url: AnyHttpUrl = AnyHttpUrl("http://localhost:11434")

    model_config = ConfigDict(extra="forbid")


class EstimateConfig(BaseModel):
    """Phase J / J1 — sync storage estimator knobs.

    Drives :func:`corpus_forge.estimate.estimate_sync`. Pure-prediction —
    the estimator never opens the backend, never instantiates an
    extractor, and never calls a model client. The only knob today is
    the TOAST compression ratio applied to text-heavy columns
    (``documents.text``, ``chunks.text``). Users on Postgres ``LZ4``
    toast columns can drop this to ``0.5`` to halve the text-bytes
    estimate; default ``1.0`` is the conservative no-compression
    baseline.

    Fields:

    - ``compression_ratio``: multiplier applied to the bytes attributed
      to text-heavy columns. Must be in ``(0.0, 1.0]``. ``1.0`` =
      uncompressed (the default and the safest over-estimate); ``0.5``
      ≈ typical LZ4 ratio on English prose.
    """

    compression_ratio: float = Field(default=1.0, gt=0.0, le=1.0)

    model_config = ConfigDict(extra="forbid")


class EvalRegressionConfig(BaseModel):
    """RFC ``rfc-eval-framework-expansion`` — tolerance gating for regression eval.

    Drives the future ``corpus-forge eval regression --baseline`` verb:
    given two ``EvalOutput`` JSON files (current run + baseline), the
    runner computes per-metric deltas and exits non-zero if any metric
    moves outside its configured tolerance band.

    The tolerance is a *symmetric* threshold expressed in the metric's
    own units. For ``ndcg@10`` (range 0-1), ``tolerance = 0.02`` means
    "fail if the delta is ``> +0.02`` OR ``< -0.02``." Direction of
    movement isn't policy-encoded here — most eval metrics regress
    when they go DOWN, but classifier *loss* and quality *MAE* regress
    when they go UP. The runner's per-metric "regression direction"
    metadata (out of scope for the config) decides which sign to
    treat as worse; the tolerance is the threshold magnitude either
    way.

    Fields:

    - ``default_tolerance``: applied to any metric not named explicitly
      in ``per_metric``. Default ``0.02`` (2 percentage points for
      normalised metrics; reasonable starting point for noisy
      retrieval / classifier scores).
    - ``per_metric``: optional ``dict[metric_name, tolerance]`` for
      metrics that need a tighter or looser band than the default.
      Metric names match the keys inside ``EvalOutput.metrics`` (e.g.
      ``"ndcg@10"``, ``"macro_f1"``, ``"mae.clarity"``).
    - ``enabled``: master on/off switch. ``True`` by default so a
      configured baseline actually gates the next run; flip to
      ``False`` to record-only without failing CI.
    """

    enabled: bool = True
    default_tolerance: float = Field(default=0.02, ge=0.0, le=1.0)
    per_metric: dict[str, float] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

    @field_validator("per_metric")
    @classmethod
    def _check_per_metric_tolerances(cls, value: dict[str, float]) -> dict[str, float]:
        """Each per-metric tolerance must be in ``[0.0, 1.0]``."""
        bad = {name: tol for name, tol in value.items() if not 0.0 <= tol <= 1.0}
        if bad:
            raise ValueError(f"per_metric tolerances must be in [0.0, 1.0], got: {bad}")
        return value

    def tolerance_for(self, metric_name: str) -> float:
        """Return the configured tolerance for *metric_name*.

        Looks up ``per_metric[metric_name]`` first, falls back to
        ``default_tolerance``. Convenience so the regression runner
        doesn't need to re-implement the lookup logic at every call
        site.
        """
        return self.per_metric.get(metric_name, self.default_tolerance)


class ScanConfig(BaseModel):
    """Phase M Wave 2 — filesystem-walker knobs.

    Drives :func:`corpus_forge.scanner.walker.walk` (the unified walker
    consumed by both the estimator and the `filesystem` source plugin).

    Fields:

    - ``extra_skip_dirs``: additional directory NAMES to prune wholesale,
      stacked on top of the hard-coded baseline (`.git`, `node_modules`,
      `__pycache__`, ...). Per-name match, not per-path.
    - ``follow_symlinks``: when True, symlinked directories are descended
      (default False — keeps the walker from chasing cycles and
      double-counting).
    - ``workers``: API-plumbed but not yet implemented. Must be ``>= 1``;
      values ``> 1`` raise :class:`NotImplementedError` at walk-time.
    """

    extra_skip_dirs: list[str] = Field(default_factory=list)
    follow_symlinks: bool = False
    workers: int = Field(default=1, ge=1)

    model_config = ConfigDict(extra="forbid")


class AnalyzeConfig(BaseModel):
    """Phase O Wave 1 — EDA + corpus-cleaning analysis config.

    Controls the ``corpus_forge.analyze`` subsystem: dedup detection,
    topic clustering, language identification, and an optional LLM-based
    quality judge.

    **Cross-cutting: local-or-remote URL.**  ``judge_endpoint`` is the base
    URL of any Ollama-compatible endpoint.  Default
    ``http://localhost:11434`` (local Ollama).  Swap to a remote URL (e.g.
    ``https://api.openai.com/v1``) to point at a hosted endpoint without
    changing code.  Same principle as :attr:`ClassifierConfig.llm_url`
    (line 492).

    Fields:

    - ``enabled``: master on/off switch.  Default ``False`` so existing
      configs without an ``[analyze]`` block opt in transparently.
    - ``dedup_threshold``: cosine-similarity floor for near-duplicate
      detection (``[0.0, 1.0]``).
    - ``topic_min_cluster_size``: HDBSCAN minimum cluster size (``>= 2``).
    - ``language_detector``: ``"langdetect"`` (pure-Python, default) or
      ``"fasttext"`` (faster binary model).
    - ``judge_endpoint``: base URL of the Ollama-compatible quality-judge
      endpoint.  Local-or-remote.
    - ``judge_model``: Ollama / OpenAI model tag for the quality judge.
    - ``judge_api_key_env``: optional env-var name holding a bearer token.
      Empty string (default) omits the ``Authorization`` header — preserves
      the open-local-Ollama shape.
    - ``judge_timeout_s``: per-request HTTP budget (``> 0``).
    """

    enabled: bool = False
    dedup_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    topic_min_cluster_size: int = Field(default=10, ge=2)
    language_detector: Literal["fasttext", "langdetect"] = "langdetect"
    # ── Judge / LLM fields ────────────────────────────────────────────
    # Pydantic v2 quirk: ``AnyHttpUrl`` defaults must be wrapped in the
    # class (not bare strings). Mirrors the proven pattern from
    # :attr:`ClassifierConfig.llm_url`.
    judge_endpoint: AnyHttpUrl = AnyHttpUrl("http://localhost:11434")
    judge_model: str = "qwen2.5:7b-instruct"
    judge_api_key_env: str = ""
    judge_timeout_s: float = Field(default=60.0, gt=0)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _check_judge_api_key_env_name(self) -> "AnalyzeConfig":
        _validate_env_var_name("judge_api_key_env", self.judge_api_key_env, allow_empty=True)
        return self


# Maps the IEC-style human-readable suffix to its byte multiplier. Lowercased
# match — both `10G` and `10g` are accepted by `GrowthConfig.sync_cap_bytes`.
# IEC convention (1024-based) chosen over SI (1000-based) because the values
# are aimed at disk-footprint budgeting and dataset eviction, where the
# operator's mental model is page/block boundaries, not network throughput.
_BYTES_SUFFIX_MULTIPLIERS: dict[str, int] = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
}


def _parse_bytes(value: int | str) -> int:
    """Parse a human-readable byte size into an integer.

    Accepts plain integers (``1024``), bare numeric strings (``"1024"``),
    and IEC-suffixed strings (``"10G"``, ``"500M"``, ``"1.5T"``). Suffix
    match is case-insensitive and trailing ``B`` (as in ``"10GB"``) is
    optional. Returns the resolved integer byte count.

    Raises:
        ValueError: when the input shape is not recognised or the numeric
            portion is non-positive (zero/negative caps are nonsensical
            and almost certainly a user typo).
    """
    if isinstance(value, int):
        if value <= 0:
            raise ValueError(f"byte size must be positive, got {value}")
        return value
    if not isinstance(value, str):
        raise TypeError(f"byte size must be int or str, got {type(value).__name__}")

    raw = value.strip().lower()
    if not raw:
        raise ValueError("byte size string is empty")

    # Split numeric prefix from suffix.
    # `raw` example shapes: "10g", "100mb", "1.5t", "1024", "1024b".
    i = 0
    while i < len(raw) and (raw[i].isdigit() or raw[i] in "._"):
        i += 1
    num_part = raw[:i].replace("_", "")
    suffix = raw[i:].strip()

    if not num_part:
        raise ValueError(f"byte size {value!r} has no numeric prefix")
    try:
        magnitude = float(num_part)
    except ValueError as exc:
        raise ValueError(f"byte size {value!r} has non-numeric prefix {num_part!r}") from exc
    if magnitude <= 0:
        raise ValueError(f"byte size {value!r} must be positive")
    if suffix not in _BYTES_SUFFIX_MULTIPLIERS:
        raise ValueError(
            f"byte size {value!r} has unknown suffix {suffix!r}; "
            f"accepted: B/K/KB/M/MB/G/GB/T/TB (case-insensitive)"
        )

    return int(magnitude * _BYTES_SUFFIX_MULTIPLIERS[suffix])


class GrowthConfig(BaseModel):
    """RFC ``rfc-corpus-growth-controls`` — bounds on corpus growth.

    Drives the ``corpus-forge prune`` admin verb, the per-source row/byte
    caps enforced inside ``ingest_once``, and the ``corpus-forge estimate
    sync`` pre-flight gate.

    All fields default to values that effectively disable enforcement
    (``sync_cap_bytes = None``, ``per_source_cap_default_rows = 0``)
    so adding the block doesn't change behaviour for existing configs.
    The user opts in by setting the cap explicitly in ``config.toml``.

    Fields:

    - ``prune_percentile_default``: integer 0-100. The default
      ``--percentile`` for ``corpus-forge prune`` when the CLI flag
      isn't given. Default ``10`` matches the RFC's "Goals" section.
    - ``sync_cap_bytes``: human-readable byte cap on the projected
      ``estimate sync`` delta (``"10G"``, ``"500M"``, etc.). When set,
      ``corpus-forge estimate sync`` exits non-zero if the predicted
      delta exceeds this. ``None`` means no cap. Parsed via
      :func:`_parse_bytes` — accepts B/K/KB/M/MB/G/GB/T/TB suffixes,
      case-insensitive. Stored internally as an ``int`` (resolved
      bytes).
    - ``per_source_cap_default_rows``: integer ``>= 0`` row cap
      applied to a ``DatasetSourceConfig`` that doesn't declare its
      own ``max_rows``. ``0`` (the default) disables the implicit
      cap. The per-source ``max_rows`` field (added by the same
      RFC's ``DatasetSourceConfig`` task) overrides this for that
      one source.
    """

    prune_percentile_default: int = Field(default=10, ge=0, le=100)
    sync_cap_bytes: int | None = None
    per_source_cap_default_rows: int = Field(default=0, ge=0)

    model_config = ConfigDict(extra="forbid")

    @field_validator("sync_cap_bytes", mode="before")
    @classmethod
    def _resolve_sync_cap_bytes(cls, value: int | str | None) -> int | None:
        """Accept ``None``, an int, or a human-readable string like ``"10G"``."""
        if value is None:
            return None
        return _parse_bytes(value)


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
    # Phase H — code-enricher backend selector. Default backend="none"
    # keeps legacy configs untouched.
    code_enricher: EnricherConfig = Field(default_factory=EnricherConfig)
    # Phase J / J1 — sync storage estimator knobs. Pure-prediction;
    # default ``compression_ratio = 1.0`` is the conservative
    # no-compression baseline so existing configs see no behaviour
    # change.
    estimate: EstimateConfig = Field(default_factory=EstimateConfig)
    # Phase L Wave 7 — Ollama daemon endpoint used by the admin verbs.
    # Defaulted so existing configs (which omit the block) keep validating.
    ollama: OllamaConfig = Field(default_factory=OllamaConfig)
    # Phase M Wave 2 — filesystem-walker knobs (extra_skip_dirs,
    # follow_symlinks, workers). Defaulted so existing configs (which omit
    # the block) keep validating.
    scan: ScanConfig = Field(default_factory=ScanConfig)
    # Phase O Wave 1 — EDA + corpus-cleaning analysis config. Defaults to
    # ``enabled=False`` so existing configs without an ``[analyze]`` block
    # continue to validate. Heavy deps (sklearn, hdbscan, etc.) are lazy-
    # imported inside ``corpus_forge.analyze`` function bodies and are
    # never pulled in by config parsing alone.
    analyze: AnalyzeConfig = Field(default_factory=AnalyzeConfig)
    # RFC ``rfc-corpus-growth-controls`` — prune percentile, sync cap, and
    # per-source row caps. Defaults to no-enforcement values
    # (``sync_cap_bytes=None``, ``per_source_cap_default_rows=0``) so
    # existing configs without a ``[growth]`` block continue to validate
    # and behave identically.
    growth: GrowthConfig = Field(default_factory=GrowthConfig)
    # RFC ``rfc-eval-framework-expansion`` — tolerance band for
    # ``corpus-forge eval regression``. Defaults to ``enabled=True`` +
    # ``default_tolerance=0.02`` so a configured baseline gates the
    # next run with a reasonable 2-percentage-point band. Existing
    # configs without an ``[eval_regression]`` block continue to
    # validate; the regression runner short-circuits if no baseline
    # JSON is passed in.
    eval_regression: EvalRegressionConfig = Field(default_factory=EvalRegressionConfig)

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

    @model_validator(mode="after")
    def _check_fast_tier_embedder(self) -> "Config":
        """Phase N Wave 3 — cross-reference fast tier embedder name.

        When ``retrieval.fast_tier_embedder_name`` is set, the name
        MUST resolve to a declared ``[[embedders]]`` entry.  Catches
        typos at config-load time rather than at search time (where
        the registry lookup would silently return ``None`` and
        ``HybridRetriever`` would raise from inside the search call).
        """
        name = self.retrieval.fast_tier_embedder_name
        if name is None:
            return self
        embedder_names = {e.name for e in self.embedders}
        if name not in embedder_names:
            raise ValueError(
                f"retrieval.fast_tier_embedder_name={name!r} does not match any "
                f"[[embedders]] entry; declared embedders: {sorted(embedder_names)!r}"
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

    def resolve_code_enricher_api_key(self) -> str | None:
        """Read the code-enricher remote API key from the configured env var.

        Returns ``None`` when the env var is unset. The remote-Ollama
        path tolerates an absent key (the Bearer header is just
        omitted); the OpenAI-shape path treats absence as fatal at
        construction time.
        """
        return os.environ.get(self.code_enricher.remote_api_key_env)

    def host_id(self) -> str:
        if self.daemon.host_id:
            return self.daemon.host_id
        host_id_path = Path.home() / ".config" / "corpus-forge" / "host_id"
        if host_id_path.exists():
            return host_id_path.read_text(encoding="utf-8").strip()
        hostname = socket.gethostname()
        host_id_path.parent.mkdir(parents=True, exist_ok=True)
        host_id_path.write_text(hostname, encoding="utf-8")
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
            # Load secrets as environment variables. Force utf-8 so Windows
            # (default cp1252) doesn't garble non-ASCII tokens.
            with secrets_path.open(encoding="utf-8") as f:
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
