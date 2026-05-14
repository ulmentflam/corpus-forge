"""Unit tests for D-06: ExtractionConfig pydantic model.

Wave 0 of the multi-format milestone. The model is purely additive — it
attaches as an optional ``extraction`` field on
:class:`DatasetSourceConfig` and existing config tests must remain green.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from corpus_forge.config import DatasetSourceConfig, ExtractionConfig

# ── ExtractionConfig defaults ────────────────────────────────────────────


def test_extraction_config_defaults_all_enabled():
    """All feature flags default to True."""
    cfg = ExtractionConfig()
    assert cfg.enable_pdf is True
    assert cfg.enable_office is True
    assert cfg.enable_code is True
    assert cfg.enable_html is True
    assert cfg.enable_epub is True
    assert cfg.enable_notebook is True
    assert cfg.enable_csv is True


def test_extraction_config_code_chunker_config_defaults():
    """code_chunker_config defaults match the CodeChunker prose: 1500/100/100."""
    cfg = ExtractionConfig()
    assert cfg.code_chunker_config == {
        "max_chars": 1500,
        "min_chars": 100,
        "overlap": 100,
    }


def test_extraction_config_disable_flags_round_trip():
    cfg = ExtractionConfig(
        enable_pdf=False,
        enable_office=False,
        enable_html=False,
        enable_code=True,
    )
    assert cfg.enable_pdf is False
    assert cfg.enable_office is False
    assert cfg.enable_html is False
    assert cfg.enable_code is True
    # Others retain defaults.
    assert cfg.enable_csv is True


def test_extraction_config_code_chunker_config_override():
    cfg = ExtractionConfig(code_chunker_config={"max_chars": 800})
    assert cfg.code_chunker_config == {"max_chars": 800}


def test_extraction_config_rejects_unknown_field():
    """Pydantic strict-ish surface — unknown fields should raise."""
    with pytest.raises(ValidationError):
        ExtractionConfig(enable_quantum_telepathy=True)  # type: ignore[call-arg]


def test_extraction_config_type_coercion_strict_on_bools():
    """enable_* flags must be real bools (or coercible)."""
    cfg = ExtractionConfig(enable_pdf=False)
    assert cfg.enable_pdf is False


# ── DatasetSourceConfig integration (additive) ───────────────────────────


def test_dataset_source_config_extraction_optional_defaults_to_none():
    """Existing DatasetSourceConfig instances continue working without
    supplying an `extraction` block."""
    src = DatasetSourceConfig(plugin="markdown_vault", chunker="markdown")
    assert src.extraction is None


def test_dataset_source_config_accepts_extraction_block():
    src = DatasetSourceConfig(
        plugin="filesystem",
        chunker="markdown",
        extraction=ExtractionConfig(enable_pdf=False),
    )
    assert src.extraction is not None
    assert src.extraction.enable_pdf is False


def test_dataset_source_config_accepts_extraction_as_dict():
    """Pydantic should coerce a dict into ExtractionConfig automatically."""
    src = DatasetSourceConfig(
        plugin="filesystem",
        chunker="markdown",
        extraction={"enable_code": False, "enable_pdf": True},
    )
    assert src.extraction is not None
    assert src.extraction.enable_code is False
    assert src.extraction.enable_pdf is True


# ── TOML round-trip (config.example.toml shape) ──────────────────────────


def test_extraction_config_from_toml_like_dict():
    """Pydantic accepts the same dict shape tomllib would emit."""
    toml_data = {
        "enable_pdf": True,
        "enable_office": False,
        "enable_code": True,
        "code_chunker_config": {
            "max_chars": 2000,
            "min_chars": 200,
            "overlap": 150,
        },
    }
    cfg = ExtractionConfig(**toml_data)
    assert cfg.enable_pdf is True
    assert cfg.enable_office is False
    assert cfg.code_chunker_config["max_chars"] == 2000
