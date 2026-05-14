"""Unit tests for D-04 (half 1): StructuredDataExtractor.

Handles ``.json .yaml .yml .toml`` files. Pretty-prints and wraps in a
fenced code block tagged with the format. ``chunker_hint = "passthrough"``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_forge.extractors import ExtractedDocument
from corpus_forge.extractors.structured import StructuredDataExtractor


def test_supported_extensions():
    ex = StructuredDataExtractor()
    assert set(ex.supported_extensions) == {".json", ".yaml", ".yml", ".toml"}


def test_extract_json_is_fenced(tmp_path: Path):
    p = tmp_path / "data.json"
    p.write_text('{"a": 1, "b": [2, 3]}', encoding="utf-8")
    doc = StructuredDataExtractor().extract(p)
    assert isinstance(doc, ExtractedDocument)
    assert doc.chunker_hint == "passthrough"
    assert doc.text.startswith("```json")
    assert doc.text.rstrip().endswith("```")
    # Pretty-printed → indented
    assert '"a": 1' in doc.text


def test_extract_json_pretty_prints(tmp_path: Path):
    p = tmp_path / "data.json"
    p.write_text('{"a":1,"b":2}', encoding="utf-8")
    doc = StructuredDataExtractor().extract(p)
    # Should be re-indented with 2 spaces.
    assert "  " in doc.text  # indentation present
    assert "\n" in doc.text  # multi-line output


def test_extract_toml(tmp_path: Path):
    p = tmp_path / "config.toml"
    p.write_text('[section]\nkey = "value"\nnumber = 42\n', encoding="utf-8")
    doc = StructuredDataExtractor().extract(p)
    assert doc.chunker_hint == "passthrough"
    assert doc.text.startswith("```toml")
    assert doc.text.rstrip().endswith("```")
    # Body content survives the pretty-print.
    assert "section" in doc.text
    assert "key" in doc.text


def test_extract_yaml(tmp_path: Path):
    """YAML may be available (PyYAML) or fall back to a tiny regex
    pretty-printer; either way, fenced output must round-trip."""
    p = tmp_path / "data.yaml"
    p.write_text("key: value\nlist:\n  - a\n  - b\n", encoding="utf-8")
    doc = StructuredDataExtractor().extract(p)
    assert doc.chunker_hint == "passthrough"
    assert doc.text.startswith("```yaml")
    assert "key" in doc.text


def test_extract_yml(tmp_path: Path):
    p = tmp_path / "data.yml"
    p.write_text("foo: bar\n", encoding="utf-8")
    doc = StructuredDataExtractor().extract(p)
    assert doc.text.startswith("```yaml")  # .yml normalised to yaml fence


def test_extract_invalid_json_falls_back_to_raw(tmp_path: Path):
    """Malformed structured data shouldn't crash — wrap raw text in the
    fence and let downstream chunkers handle it."""
    p = tmp_path / "broken.json"
    p.write_text("{not valid json", encoding="utf-8")
    doc = StructuredDataExtractor().extract(p)
    assert doc.text.startswith("```json")
    assert "{not valid json" in doc.text


def test_extract_invalid_toml_falls_back_to_raw(tmp_path: Path):
    p = tmp_path / "broken.toml"
    p.write_text("this = is = not = toml", encoding="utf-8")
    doc = StructuredDataExtractor().extract(p)
    assert doc.text.startswith("```toml")
    assert "this = is = not = toml" in doc.text


def test_extract_title_from_stem(tmp_path: Path):
    p = tmp_path / "manifest.json"
    p.write_text('{"x": 1}', encoding="utf-8")
    doc = StructuredDataExtractor().extract(p)
    assert doc.metadata.get("title") == "manifest"


def test_extract_format_label_in_metadata(tmp_path: Path):
    p = tmp_path / "x.toml"
    p.write_text("k = 1\n", encoding="utf-8")
    doc = StructuredDataExtractor().extract(p)
    assert doc.metadata.get("format") == "toml"


def test_extract_empty_file(tmp_path: Path):
    p = tmp_path / "x.json"
    p.write_text("", encoding="utf-8")
    doc = StructuredDataExtractor().extract(p)
    assert doc.text.startswith("```json")


@pytest.mark.parametrize(
    ("filename", "fence"),
    [
        ("a.json", "```json"),
        ("a.yaml", "```yaml"),
        ("a.yml", "```yaml"),
        ("a.toml", "```toml"),
    ],
)
def test_extract_fence_matches_extension(tmp_path: Path, filename: str, fence: str):
    p = tmp_path / filename
    p.write_text("k: v\n" if "yaml" in filename or "yml" in filename else "{}", encoding="utf-8")
    doc = StructuredDataExtractor().extract(p)
    assert doc.text.startswith(fence)
