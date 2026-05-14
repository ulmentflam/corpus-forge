"""Unit tests for D-03 (half 1): PassthroughMarkdownExtractor.

Handles ``.md`` and ``.markdown`` files. Reads the file verbatim and
sets ``chunker_hint = "markdown"`` so the downstream
:class:`MarkdownChunker` picks it up. Title is the first ``# heading``
in the file, falling back to the file stem.
"""

from __future__ import annotations

from pathlib import Path

from corpus_forge.extractors import ExtractedDocument, Extractor
from corpus_forge.extractors.passthrough import PassthroughMarkdownExtractor


def test_extractor_protocol_conformance():
    ex: Extractor = PassthroughMarkdownExtractor()
    assert isinstance(ex.supported_extensions, tuple)


def test_supported_extensions():
    ex = PassthroughMarkdownExtractor()
    assert set(ex.supported_extensions) == {".md", ".markdown"}


def test_extract_returns_extracted_document(tmp_path: Path):
    p = tmp_path / "hello.md"
    p.write_text("# Greetings\n\nHello world\n", encoding="utf-8")
    doc = PassthroughMarkdownExtractor().extract(p)
    assert isinstance(doc, ExtractedDocument)
    assert doc.chunker_hint == "markdown"


def test_extract_preserves_text_verbatim(tmp_path: Path):
    raw = "# Foo\n\nBar baz **bold** _em_\n\n- list\n- items\n"
    p = tmp_path / "doc.md"
    p.write_text(raw, encoding="utf-8")
    doc = PassthroughMarkdownExtractor().extract(p)
    assert doc.text == raw


def test_extract_title_from_first_heading(tmp_path: Path):
    p = tmp_path / "post.md"
    p.write_text("# My Title\n\nBody.", encoding="utf-8")
    doc = PassthroughMarkdownExtractor().extract(p)
    assert doc.metadata.get("title") == "My Title"


def test_extract_title_strips_leading_hashes(tmp_path: Path):
    p = tmp_path / "post.md"
    p.write_text("## A Section\n\nBody.", encoding="utf-8")
    doc = PassthroughMarkdownExtractor().extract(p)
    # Even though H2 isn't "the title", the title-ish heading should be captured.
    assert doc.metadata.get("title") == "A Section"


def test_extract_title_falls_back_to_stem(tmp_path: Path):
    p = tmp_path / "notes-2024.md"
    p.write_text("just body text, no heading", encoding="utf-8")
    doc = PassthroughMarkdownExtractor().extract(p)
    assert doc.metadata.get("title") == "notes-2024"


def test_extract_handles_markdown_extension(tmp_path: Path):
    p = tmp_path / "doc.markdown"
    p.write_text("# Hi", encoding="utf-8")
    doc = PassthroughMarkdownExtractor().extract(p)
    assert doc.chunker_hint == "markdown"
    assert doc.text == "# Hi"


def test_extract_empty_file(tmp_path: Path):
    p = tmp_path / "empty.md"
    p.write_text("", encoding="utf-8")
    doc = PassthroughMarkdownExtractor().extract(p)
    assert doc.text == ""
    assert doc.metadata.get("title") == "empty"


def test_extract_language_is_none(tmp_path: Path):
    p = tmp_path / "x.md"
    p.write_text("# x", encoding="utf-8")
    doc = PassthroughMarkdownExtractor().extract(p)
    assert doc.language is None


def test_extract_labels_default_empty(tmp_path: Path):
    p = tmp_path / "x.md"
    p.write_text("# x", encoding="utf-8")
    doc = PassthroughMarkdownExtractor().extract(p)
    assert doc.labels == []
