"""Unit tests for D-04 (half 2): SubtitleExtractor.

Handles ``.srt`` and ``.vtt`` files. Strips timestamp/cue lines and
keeps only dialogue text. ``chunker_hint = "passthrough"``.
"""

from __future__ import annotations

from pathlib import Path

from corpus_forge.extractors import ExtractedDocument
from corpus_forge.extractors.subtitle import SubtitleExtractor

_SRT_SAMPLE = """\
1
00:00:01,000 --> 00:00:04,000
Hello world.

2
00:00:05,500 --> 00:00:09,200
This is the second subtitle line.
It spans two rows.

3
00:00:10,000 --> 00:00:12,000
Goodbye.
"""

_VTT_SAMPLE = """\
WEBVTT

00:00:01.000 --> 00:00:04.000
Hello WebVTT.

00:00:05.000 --> 00:00:07.000 line:0%
Cue with positioning.
"""


def test_supported_extensions():
    ex = SubtitleExtractor()
    assert set(ex.supported_extensions) == {".srt", ".vtt"}


def test_extract_returns_extracted_document(tmp_path: Path):
    p = tmp_path / "movie.srt"
    p.write_text(_SRT_SAMPLE, encoding="utf-8")
    doc = SubtitleExtractor().extract(p)
    assert isinstance(doc, ExtractedDocument)
    assert doc.chunker_hint == "passthrough"


def test_extract_srt_strips_indices_and_timestamps(tmp_path: Path):
    p = tmp_path / "movie.srt"
    p.write_text(_SRT_SAMPLE, encoding="utf-8")
    doc = SubtitleExtractor().extract(p)
    text = doc.text
    # Timestamps removed
    assert "00:00:01,000" not in text
    assert "-->" not in text
    # Cue indices removed (1, 2, 3 on their own lines)
    for line in text.splitlines():
        assert line.strip() not in ("1", "2", "3")


def test_extract_srt_preserves_dialogue(tmp_path: Path):
    p = tmp_path / "movie.srt"
    p.write_text(_SRT_SAMPLE, encoding="utf-8")
    doc = SubtitleExtractor().extract(p)
    assert "Hello world." in doc.text
    assert "This is the second subtitle line." in doc.text
    assert "It spans two rows." in doc.text
    assert "Goodbye." in doc.text


def test_extract_vtt_strips_header_and_timestamps(tmp_path: Path):
    p = tmp_path / "movie.vtt"
    p.write_text(_VTT_SAMPLE, encoding="utf-8")
    doc = SubtitleExtractor().extract(p)
    text = doc.text
    assert "WEBVTT" not in text
    assert "-->" not in text
    assert "00:00:01.000" not in text


def test_extract_vtt_preserves_dialogue(tmp_path: Path):
    p = tmp_path / "movie.vtt"
    p.write_text(_VTT_SAMPLE, encoding="utf-8")
    doc = SubtitleExtractor().extract(p)
    assert "Hello WebVTT." in doc.text
    assert "Cue with positioning." in doc.text


def test_extract_empty_file(tmp_path: Path):
    p = tmp_path / "empty.srt"
    p.write_text("", encoding="utf-8")
    doc = SubtitleExtractor().extract(p)
    assert doc.text == ""
    assert doc.chunker_hint == "passthrough"


def test_extract_title_falls_back_to_stem(tmp_path: Path):
    p = tmp_path / "my-movie-2024.srt"
    p.write_text(_SRT_SAMPLE, encoding="utf-8")
    doc = SubtitleExtractor().extract(p)
    assert doc.metadata.get("title") == "my-movie-2024"


def test_extract_language_is_none(tmp_path: Path):
    p = tmp_path / "x.srt"
    p.write_text("1\n00:00:01,000 --> 00:00:02,000\nhi\n", encoding="utf-8")
    doc = SubtitleExtractor().extract(p)
    assert doc.language is None


def test_extract_handles_multiple_blank_lines(tmp_path: Path):
    p = tmp_path / "noisy.srt"
    p.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nhello\n\n\n\n2\n00:00:03,000 --> 00:00:04,000\nworld\n",
        encoding="utf-8",
    )
    doc = SubtitleExtractor().extract(p)
    assert "hello" in doc.text
    assert "world" in doc.text
