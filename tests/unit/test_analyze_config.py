"""Phase O Wave 1 — O1-T1: AnalyzeConfig pydantic block + TOML round-trip.

Pins the shape of ``AnalyzeConfig`` and its attachment to the top-level
``Config`` model.  Contract source: `.planning/tdd/phase_o_eda_cleaning.md`
§ Wave O1 RED (that doc is the canonical spec; it takes precedence over
the task-level description on any discrepancy).

RED state: ``from corpus_forge.config import AnalyzeConfig`` fails with
``ImportError`` because ``AnalyzeConfig`` does not yet exist.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_config(toml_text: str, tmp_path: Path):
    """Write TOML to a temp file and load it via ``Config.load``."""
    from corpus_forge.config import Config

    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(textwrap.dedent(toml_text), encoding="utf-8")
    return Config.load(config_path=cfg_path)


# Minimal valid TOML floor that Config requires (backend / daemon / datasets /
# embedders).  Mirrors the ``_BASE_TOML`` used in test_config_classifier.py.
_BASE_TOML = """
[backend]
kind = "postgres"
dsn = "postgresql://localhost/forge"

[daemon]

[[datasets]]
name = "x"
kind = "text"
sources = [{plugin = "markdown_vault", vault_root = "/tmp", chunker = "markdown"}]

[[embedders]]
name = "e"
provider = "sentence_transformers"
model_id = "m"
dimension = 1
"""

# Minimal dict form used for in-process Config(**...) construction.
_MINIMAL_KWARGS = {
    "backend": {"kind": "sqlite", "dsn": "/tmp/test.db"},
    "daemon": {},
    "datasets": [
        {
            "name": "ds",
            "kind": "text",
            "sources": [{"plugin": "markdown_vault", "vault_root": "/tmp", "chunker": "markdown"}],
        }
    ],
    "embedders": [
        {
            "name": "e1",
            "provider": "sentence_transformers",
            "model_id": "test/m",
            "dimension": 8,
        }
    ],
}


# ---------------------------------------------------------------------------
# 1. Import smoke
# ---------------------------------------------------------------------------


class TestAnalyzeConfigImport:
    """AnalyzeConfig must be importable from corpus_forge.config."""

    def test_analyze_config_importable(self) -> None:
        """``from corpus_forge.config import AnalyzeConfig`` must succeed."""
        from corpus_forge.config import AnalyzeConfig  # noqa: F401

    def test_config_importable_with_analyze_config(self) -> None:
        """Both ``Config`` and ``AnalyzeConfig`` live in the same module."""
        from corpus_forge.config import AnalyzeConfig, Config  # noqa: F401


# ---------------------------------------------------------------------------
# 2. Default field values
# ---------------------------------------------------------------------------


class TestAnalyzeConfigDefaults:
    """Every field default matches the spec in phase_o_eda_cleaning.md § Wave O1 RED."""

    def _make(self):
        from corpus_forge.config import AnalyzeConfig

        return AnalyzeConfig()

    def test_enabled_defaults_to_false(self) -> None:
        assert self._make().enabled is False

    def test_dedup_threshold_defaults_to_0_85(self) -> None:
        assert self._make().dedup_threshold == pytest.approx(0.85)

    def test_topic_min_cluster_size_defaults_to_10(self) -> None:
        assert self._make().topic_min_cluster_size == 10

    def test_language_detector_defaults_to_langdetect(self) -> None:
        assert self._make().language_detector == "langdetect"

    def test_judge_endpoint_defaults_to_localhost_11434(self) -> None:
        endpoint = str(self._make().judge_endpoint)
        assert "localhost" in endpoint
        assert "11434" in endpoint

    def test_judge_model_defaults_to_qwen2_5(self) -> None:
        assert self._make().judge_model == "qwen2.5:7b-instruct"

    def test_judge_api_key_env_defaults_to_empty_string(self) -> None:
        assert self._make().judge_api_key_env == ""

    def test_judge_timeout_s_defaults_to_60(self) -> None:
        assert self._make().judge_timeout_s == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# 3. Field validation
# ---------------------------------------------------------------------------


class TestAnalyzeConfigValidation:
    """Pydantic field constraints from the spec."""

    def _cls(self):
        from corpus_forge.config import AnalyzeConfig

        return AnalyzeConfig

    # dedup_threshold: ge=0.0, le=1.0
    def test_dedup_threshold_accepts_zero(self) -> None:
        c = self._cls()(dedup_threshold=0.0)
        assert c.dedup_threshold == pytest.approx(0.0)

    def test_dedup_threshold_accepts_one(self) -> None:
        c = self._cls()(dedup_threshold=1.0)
        assert c.dedup_threshold == pytest.approx(1.0)

    def test_dedup_threshold_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._cls()(dedup_threshold=-0.01)

    def test_dedup_threshold_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._cls()(dedup_threshold=1.01)

    # topic_min_cluster_size: ge=2
    def test_topic_min_cluster_size_accepts_2(self) -> None:
        c = self._cls()(topic_min_cluster_size=2)
        assert c.topic_min_cluster_size == 2

    def test_topic_min_cluster_size_below_2_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._cls()(topic_min_cluster_size=1)

    def test_topic_min_cluster_size_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._cls()(topic_min_cluster_size=0)

    # language_detector: Literal["fasttext", "langdetect"]
    def test_language_detector_accepts_langdetect(self) -> None:
        c = self._cls()(language_detector="langdetect")
        assert c.language_detector == "langdetect"

    def test_language_detector_accepts_fasttext(self) -> None:
        c = self._cls()(language_detector="fasttext")
        assert c.language_detector == "fasttext"

    def test_language_detector_rejects_invalid(self) -> None:
        """'spacy' is not a valid detector — must be rejected via ValidationError."""
        with pytest.raises(ValidationError):
            self._cls()(language_detector="spacy")  # type: ignore[arg-type]

    def test_language_detector_rejects_empty_string(self) -> None:
        with pytest.raises(ValidationError):
            self._cls()(language_detector="")  # type: ignore[arg-type]

    def test_language_detector_rejects_arbitrary_string(self) -> None:
        with pytest.raises(ValidationError):
            self._cls()(language_detector="nltk")  # type: ignore[arg-type]

    # judge_endpoint: AnyHttpUrl
    def test_judge_endpoint_accepts_remote_url(self) -> None:
        c = self._cls()(judge_endpoint="https://api.openai.com")  # type: ignore[arg-type]
        assert "openai.com" in str(c.judge_endpoint)

    def test_judge_endpoint_accepts_http_local(self) -> None:
        c = self._cls()(judge_endpoint="http://localhost:11434")  # type: ignore[arg-type]
        assert "localhost" in str(c.judge_endpoint)

    def test_judge_endpoint_rejects_ftp(self) -> None:
        with pytest.raises(ValidationError):
            self._cls()(judge_endpoint="ftp://nope.example.com")  # type: ignore[arg-type]

    def test_judge_endpoint_rejects_bare_string(self) -> None:
        with pytest.raises(ValidationError):
            self._cls()(judge_endpoint="not-a-url")  # type: ignore[arg-type]

    # judge_timeout_s: gt=0
    def test_judge_timeout_s_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            self._cls()(judge_timeout_s=0.0)

    def test_judge_timeout_s_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._cls()(judge_timeout_s=-1.0)

    def test_judge_timeout_s_accepts_positive(self) -> None:
        c = self._cls()(judge_timeout_s=30.0)
        assert c.judge_timeout_s == pytest.approx(30.0)

    # judge_api_key_env: allow-empty; when non-empty must be a valid POSIX identifier
    def test_judge_api_key_env_empty_string_accepted(self) -> None:
        """Empty string is the "no auth" default — must not raise."""
        c = self._cls()(judge_api_key_env="")
        assert c.judge_api_key_env == ""

    def test_judge_api_key_env_valid_posix_accepted(self) -> None:
        c = self._cls()(judge_api_key_env="OPENAI_API_KEY")
        assert c.judge_api_key_env == "OPENAI_API_KEY"

    def test_judge_api_key_env_underscore_prefix_accepted(self) -> None:
        c = self._cls()(judge_api_key_env="_MY_KEY")
        assert c.judge_api_key_env == "_MY_KEY"

    def test_judge_api_key_env_spaces_rejected(self) -> None:
        """A value with spaces is not a valid POSIX env var name."""
        with pytest.raises(ValidationError):
            self._cls()(judge_api_key_env="MY KEY")

    def test_judge_api_key_env_hyphens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._cls()(judge_api_key_env="MY-KEY")

    def test_judge_api_key_env_digit_prefix_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._cls()(judge_api_key_env="1BAD_KEY")

    # extra="forbid"
    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            self._cls()(unknown_extra=True)  # type: ignore[call-arg]

    def test_model_config_extra_is_forbid(self) -> None:
        """Confirm ConfigDict(extra='forbid') is set on the model."""
        from corpus_forge.config import AnalyzeConfig

        assert AnalyzeConfig.model_config.get("extra") == "forbid"


# ---------------------------------------------------------------------------
# 4. Config.analyze wiring (in-process construction)
# ---------------------------------------------------------------------------


class TestConfigAnalyzeWiring:
    """Top-level Config carries analyze: AnalyzeConfig = Field(default_factory=AnalyzeConfig)."""

    def test_config_has_analyze_attribute(self) -> None:
        from corpus_forge.config import AnalyzeConfig, Config

        cfg = Config(**_MINIMAL_KWARGS)
        assert isinstance(cfg.analyze, AnalyzeConfig)

    def test_config_analyze_defaults_when_omitted(self) -> None:
        """Omitting analyze from kwargs yields the default AnalyzeConfig."""
        from corpus_forge.config import Config

        cfg = Config(**_MINIMAL_KWARGS)
        assert cfg.analyze.enabled is False
        assert cfg.analyze.dedup_threshold == pytest.approx(0.85)
        assert cfg.analyze.topic_min_cluster_size == 10
        assert cfg.analyze.language_detector == "langdetect"
        assert "11434" in str(cfg.analyze.judge_endpoint)
        assert cfg.analyze.judge_model == "qwen2.5:7b-instruct"
        assert cfg.analyze.judge_api_key_env == ""
        assert cfg.analyze.judge_timeout_s == pytest.approx(60.0)

    def test_config_analyze_overridable_via_dict(self) -> None:
        """analyze= dict is accepted and parsed into AnalyzeConfig."""
        from corpus_forge.config import Config

        kwargs = dict(_MINIMAL_KWARGS)
        kwargs["analyze"] = {  # type: ignore[assignment]
            "enabled": True,
            "dedup_threshold": 0.9,
            "topic_min_cluster_size": 5,
            "language_detector": "fasttext",
            "judge_timeout_s": 120.0,
        }
        cfg = Config(**kwargs)
        assert cfg.analyze.enabled is True
        assert cfg.analyze.dedup_threshold == pytest.approx(0.9)
        assert cfg.analyze.topic_min_cluster_size == 5
        assert cfg.analyze.language_detector == "fasttext"
        assert cfg.analyze.judge_timeout_s == pytest.approx(120.0)


# ---------------------------------------------------------------------------
# 5. TOML round-trip (via Config.load)
# ---------------------------------------------------------------------------


class TestAnalyzeConfigTomlRoundTrip:
    """Config.load() correctly populates AnalyzeConfig from a [analyze] TOML block."""

    def test_toml_with_analyze_block_round_trips(self, tmp_path: Path) -> None:
        """All [analyze] fields survive a write→load cycle."""
        toml_body = _BASE_TOML + textwrap.dedent("""
            [analyze]
            enabled = true
            dedup_threshold = 0.75
            topic_min_cluster_size = 15
            language_detector = "fasttext"
            judge_endpoint = "https://api.openai.com"
            judge_model = "gpt-4o-mini"
            judge_api_key_env = "OPENAI_API_KEY"
            judge_timeout_s = 45.0
        """)
        cfg = _load_config(toml_body, tmp_path)

        assert cfg.analyze.enabled is True
        assert cfg.analyze.dedup_threshold == pytest.approx(0.75)
        assert cfg.analyze.topic_min_cluster_size == 15
        assert cfg.analyze.language_detector == "fasttext"
        assert "openai.com" in str(cfg.analyze.judge_endpoint)
        assert cfg.analyze.judge_model == "gpt-4o-mini"
        assert cfg.analyze.judge_api_key_env == "OPENAI_API_KEY"
        assert cfg.analyze.judge_timeout_s == pytest.approx(45.0)

    def test_toml_omitting_analyze_block_uses_defaults(self, tmp_path: Path) -> None:
        """Existing user configs that omit [analyze] continue to validate.

        This is the backwards-compat invariant: default_factory=AnalyzeConfig
        means the block is optional.
        """
        cfg = _load_config(_BASE_TOML, tmp_path)

        from corpus_forge.config import AnalyzeConfig

        assert isinstance(cfg.analyze, AnalyzeConfig)
        assert cfg.analyze.enabled is False
        assert cfg.analyze.dedup_threshold == pytest.approx(0.85)
        assert cfg.analyze.language_detector == "langdetect"

    def test_toml_with_empty_analyze_block_uses_defaults(self, tmp_path: Path) -> None:
        """An empty [analyze] section is equivalent to the default."""
        cfg = _load_config(_BASE_TOML + "\n[analyze]\n", tmp_path)
        assert cfg.analyze.enabled is False
        assert cfg.analyze.topic_min_cluster_size == 10

    def test_toml_analyze_local_endpoint_round_trips(self, tmp_path: Path) -> None:
        """Local Ollama endpoint (default example) survives the round-trip."""
        toml_body = _BASE_TOML + textwrap.dedent("""
            [analyze]
            judge_endpoint = "http://localhost:11434"
        """)
        cfg = _load_config(toml_body, tmp_path)
        assert "localhost" in str(cfg.analyze.judge_endpoint)
        assert "11434" in str(cfg.analyze.judge_endpoint)

    def test_toml_analyze_invalid_language_detector_rejected(self, tmp_path: Path) -> None:
        """A [analyze] block with an invalid language_detector is rejected at load time."""
        toml_body = _BASE_TOML + textwrap.dedent("""
            [analyze]
            language_detector = "spacy"
        """)
        with pytest.raises((ValidationError, Exception)):
            _load_config(toml_body, tmp_path)

    def test_toml_analyze_extra_key_rejected(self, tmp_path: Path) -> None:
        """extra='forbid' propagates through TOML loading — unknown keys raise."""
        toml_body = _BASE_TOML + textwrap.dedent("""
            [analyze]
            totally_unknown_key = true
        """)
        with pytest.raises((ValidationError, Exception)):
            _load_config(toml_body, tmp_path)
