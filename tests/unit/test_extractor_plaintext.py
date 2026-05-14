"""Unit tests for D-03 (half 2): PlainTextExtractor.

Handles ``.txt .log .rst .org .tex .adoc`` files. Reads the file verbatim
and sets ``chunker_hint = "passthrough"`` so the downstream
:class:`PassthroughChunker` handles segmentation. Title is the first
non-empty line stripped of any leading punctuation, falling back to the
file stem.
"""

from __future__ import annotations

from pathlib import Path

from corpus_forge.extractors import ExtractedDocument, Extractor
from corpus_forge.extractors.plaintext import PlainTextExtractor


def test_extractor_protocol_conformance():
    ex: Extractor = PlainTextExtractor()
    assert isinstance(ex.supported_extensions, tuple)


def test_supported_extensions():
    ex = PlainTextExtractor()
    assert set(ex.supported_extensions) == {
        ".txt",
        ".log",
        ".rst",
        ".org",
        ".tex",
        ".adoc",
    }


def test_extract_returns_extracted_document(tmp_path: Path):
    p = tmp_path / "notes.txt"
    p.write_text("hello", encoding="utf-8")
    doc = PlainTextExtractor().extract(p)
    assert isinstance(doc, ExtractedDocument)
    assert doc.chunker_hint == "passthrough"


def test_extract_text_verbatim(tmp_path: Path):
    raw = "Line one\nLine two\nLine three\n"
    p = tmp_path / "notes.txt"
    p.write_text(raw, encoding="utf-8")
    doc = PlainTextExtractor().extract(p)
    assert doc.text == raw


def test_extract_handles_rst(tmp_path: Path):
    p = tmp_path / "doc.rst"
    p.write_text("Title\n=====\n\nBody.", encoding="utf-8")
    doc = PlainTextExtractor().extract(p)
    assert doc.chunker_hint == "passthrough"


def test_extract_handles_log(tmp_path: Path):
    p = tmp_path / "session.log"
    p.write_text("INFO: foo\nWARN: bar\n", encoding="utf-8")
    doc = PlainTextExtractor().extract(p)
    assert "INFO" in doc.text


def test_extract_handles_org(tmp_path: Path):
    p = tmp_path / "agenda.org"
    p.write_text("* TODO Item one\n* DONE Item two\n", encoding="utf-8")
    doc = PlainTextExtractor().extract(p)
    assert doc.chunker_hint == "passthrough"


def test_extract_handles_tex(tmp_path: Path):
    p = tmp_path / "paper.tex"
    p.write_text(r"\section{Intro}" "\n", encoding="utf-8")
    doc = PlainTextExtractor().extract(p)
    assert doc.chunker_hint == "passthrough"


def test_extract_handles_adoc(tmp_path: Path):
    p = tmp_path / "guide.adoc"
    p.write_text("= Title\n\nBody.\n", encoding="utf-8")
    doc = PlainTextExtractor().extract(p)
    assert doc.chunker_hint == "passthrough"


def test_extract_title_from_first_line(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_text("First Line\nsecond\nthird", encoding="utf-8")
    doc = PlainTextExtractor().extract(p)
    assert doc.metadata.get("title") == "First Line"


def test_extract_title_falls_back_to_stem(tmp_path: Path):
    p = tmp_path / "anonymous-2024.txt"
    p.write_text("", encoding="utf-8")
    doc = PlainTextExtractor().extract(p)
    assert doc.metadata.get("title") == "anonymous-2024"


def test_extract_language_is_none(tmp_path: Path):
    p = tmp_path / "x.txt"
    p.write_text("hello", encoding="utf-8")
    doc = PlainTextExtractor().extract(p)
    assert doc.language is None
