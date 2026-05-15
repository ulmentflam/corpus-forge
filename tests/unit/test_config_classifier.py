"""Unit tests for `ClassifierConfig` and its attachment to `Config`.

Phase E / Wave 0 — C-03.

Covers:
- Default-constructed `ClassifierConfig` matches plan defaults.
- `Config(...)` with no `[classifier]` block synthesises the defaults
  (backwards-compatible).
- Field validation: `chain` must be a list of strings; threshold bounds
  (0.0..1.0); extra fields rejected.
- LLM fields declared but unused at P0 — their presence in the schema
  ensures the P1 dispatch can land without breaking existing configs.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from pydantic import ValidationError

from corpus_forge.config import ClassifierConfig, Config


def _load_config(toml_text: str, tmp_path: Path) -> Config:
    """Write a TOML body to a temp file and load it via `Config.load`."""
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(textwrap.dedent(toml_text), encoding="utf-8")
    return Config.load(config_path=cfg_path)


# Minimal `[backend] + [daemon] + [[datasets]] + [[embedders]]` floor
# (matches what `Config(...)` requires).
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


class TestClassifierConfigDefaults:
    def test_default_chain_is_rule_only(self) -> None:
        c = ClassifierConfig()
        assert c.chain == ["rule"]

    def test_default_threshold_is_0_4(self) -> None:
        c = ClassifierConfig()
        assert c.escalation_threshold == pytest.approx(0.4)

    def test_default_llm_fields_declared(self) -> None:
        """P0 declares the LLM fields with defaults so P1 lands cleanly."""
        c = ClassifierConfig()
        assert c.llm_model == "qwen2.5:7b-instruct"
        # We accept either the bare host string or any string — keep the
        # assertion loose on shape so pydantic AnyUrl coercion can change
        # later without breaking tests.
        assert "11434" in str(c.llm_url)
        assert c.llm_timeout_s == pytest.approx(60.0)
        assert c.llm_excerpt_chars == 2000


class TestClassifierConfigValidation:
    def test_threshold_bounds_accepted(self) -> None:
        ClassifierConfig(escalation_threshold=0.0)
        ClassifierConfig(escalation_threshold=1.0)

    def test_threshold_below_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ClassifierConfig(escalation_threshold=-0.1)

    def test_threshold_above_one_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ClassifierConfig(escalation_threshold=1.1)

    def test_chain_must_be_list_of_strings(self) -> None:
        ClassifierConfig(chain=["rule", "llm"])
        with pytest.raises(ValidationError):
            ClassifierConfig(chain="rule")  # type: ignore[arg-type]

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ClassifierConfig(unknown_field="x")  # type: ignore[call-arg]

    def test_llm_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ClassifierConfig(llm_timeout_s=0)

    def test_llm_excerpt_chars_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ClassifierConfig(llm_excerpt_chars=0)


class TestConfigAttachment:
    def test_config_without_classifier_block_uses_defaults(self, tmp_path: Path) -> None:
        cfg = _load_config(_BASE_TOML, tmp_path)
        assert isinstance(cfg.classifier, ClassifierConfig)
        assert cfg.classifier.chain == ["rule"]
        assert cfg.classifier.escalation_threshold == pytest.approx(0.4)

    def test_config_with_empty_classifier_block_uses_defaults(self, tmp_path: Path) -> None:
        cfg = _load_config(_BASE_TOML + "\n[classifier]\n", tmp_path)
        assert cfg.classifier.chain == ["rule"]

    def test_config_with_explicit_chain(self, tmp_path: Path) -> None:
        body = _BASE_TOML + textwrap.dedent(
            """
                [classifier]
                chain = ["rule", "llm"]
                escalation_threshold = 0.6
                """
        )
        cfg = _load_config(body, tmp_path)
        assert cfg.classifier.chain == ["rule", "llm"]
        assert cfg.classifier.escalation_threshold == pytest.approx(0.6)

    def test_config_extra_classifier_field_rejected(self, tmp_path: Path) -> None:
        body = _BASE_TOML + textwrap.dedent(
            """
                [classifier]
                chain = ["rule"]
                unknown_extra = true
                """
        )
        with pytest.raises(ValidationError):
            _load_config(body, tmp_path)
