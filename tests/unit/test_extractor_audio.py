"""Phase G (G-05) — :class:`AudioExtractor` unit tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from corpus_forge.extractors import ExtractedDocument, Extractor
from corpus_forge.extractors.audio import AudioExtractor
from corpus_forge.whisper.base import NoopWhisper, WhisperBackend, WhisperResponseError


def _make_whisper(name: str = "local", returns: str = "**[00:00]** hello") -> MagicMock:
    w = MagicMock(spec=WhisperBackend)
    w.name = name
    w.model = "small"
    w.transcribe.return_value = returns
    return w


def _write_audio(tmp_path: Path, name: str = "x.mp3", body: bytes = b"\xff\xfb") -> Path:
    p = tmp_path / name
    p.write_bytes(body)
    return p


# ── Protocol conformance ────────────────────────────────────────────────


def test_extractor_protocol_conformance() -> None:
    ex: Extractor = AudioExtractor(whisper=_make_whisper())
    assert isinstance(ex.supported_extensions, tuple)


def test_supported_extensions() -> None:
    ex = AudioExtractor(whisper=_make_whisper())
    assert set(ex.supported_extensions) == {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


def test_supported_filenames_is_empty() -> None:
    ex = AudioExtractor(whisper=_make_whisper())
    assert ex.supported_filenames == ()


def test_constructor_requires_keyword_whisper() -> None:
    with pytest.raises(TypeError):
        AudioExtractor(_make_whisper())  # type: ignore[misc]


# ── Extraction ──────────────────────────────────────────────────────────


def test_extract_returns_extracted_document(tmp_path: Path) -> None:
    p = _write_audio(tmp_path)
    w = _make_whisper(returns="**[00:00]** body")
    doc = AudioExtractor(whisper=w).extract(p)
    assert isinstance(doc, ExtractedDocument)
    assert doc.text == "**[00:00]** body"
    assert doc.chunker_hint == "passthrough"


def test_extract_metadata_shape(tmp_path: Path) -> None:
    p = _write_audio(tmp_path, body=b"\xff\xfb\x00\x01\x02")
    w = _make_whisper(name="remote")
    doc = AudioExtractor(whisper=w).extract(p)
    assert doc is not None
    assert doc.metadata["extractor"] == "whisper"
    assert doc.metadata["whisper_backend"] == "remote"
    assert doc.metadata["byte_count"] == len(p.read_bytes())
    assert doc.metadata["byte_count"] > 0


def test_extract_labels(tmp_path: Path) -> None:
    p = _write_audio(tmp_path)
    w = _make_whisper(name="local")
    w.model = "small"
    doc = AudioExtractor(whisper=w).extract(p)
    assert doc is not None
    labels = {(ns, val) for ns, val in doc.labels}
    assert ("format", "audio") in labels
    assert ("transcription", "small") in labels


def test_extract_passes_bytes_to_whisper(tmp_path: Path) -> None:
    p = _write_audio(tmp_path)
    w = _make_whisper()
    AudioExtractor(whisper=w).extract(p)
    w.transcribe.assert_called_once()
    args, _kwargs = w.transcribe.call_args
    assert args[0] == p.read_bytes()


def test_extract_forwards_language(tmp_path: Path) -> None:
    p = _write_audio(tmp_path)
    w = _make_whisper()
    AudioExtractor(whisper=w, language="en").extract(p)
    _args, kwargs = w.transcribe.call_args
    assert kwargs.get("language") == "en"


def test_extract_empty_string_language_treated_as_auto(tmp_path: Path) -> None:
    p = _write_audio(tmp_path)
    w = _make_whisper()
    AudioExtractor(whisper=w, language="").extract(p)
    _args, kwargs = w.transcribe.call_args
    assert kwargs.get("language") is None


# ── Noop short-circuit ──────────────────────────────────────────────────


def test_noop_whisper_returns_none(tmp_path: Path) -> None:
    p = _write_audio(tmp_path)
    ex = AudioExtractor(whisper=NoopWhisper())
    assert ex.extract(p) is None


# ── All extensions go through the same code path ────────────────────────


def test_extract_handles_all_supported_extensions(tmp_path: Path) -> None:
    w = _make_whisper()
    ex = AudioExtractor(whisper=w)
    for i, ext in enumerate(ex.supported_extensions):
        p = tmp_path / f"sample{i}{ext}"
        p.write_bytes(b"\xff\xfb")
        doc = ex.extract(p)
        assert isinstance(doc, ExtractedDocument)
        w.transcribe.reset_mock()


# ── Error propagation ──────────────────────────────────────────────────


def test_extract_propagates_whisper_response_error(tmp_path: Path) -> None:
    p = _write_audio(tmp_path)
    w = MagicMock(spec=WhisperBackend)
    w.name = "remote"
    w.model = "whisper-1"
    w.transcribe.side_effect = WhisperResponseError("malformed")
    with pytest.raises(WhisperResponseError):
        AudioExtractor(whisper=w).extract(p)


# ── Registry integration ────────────────────────────────────────────────


def test_registry_registers_audio_extractor_when_whisper_present() -> None:
    from corpus_forge.config import ExtractionConfig
    from corpus_forge.extractors.registry import register_default_extractors

    w = _make_whisper()
    reg = register_default_extractors(ExtractionConfig(), whisper=w)
    for ext in AudioExtractor.supported_extensions:
        ex = reg.get_for(Path(f"/tmp/sample{ext}"))
        assert isinstance(ex, AudioExtractor), f"missing audio extractor for {ext}"


def test_registry_skips_audio_extractor_when_whisper_none() -> None:
    from corpus_forge.config import ExtractionConfig
    from corpus_forge.extractors.registry import register_default_extractors

    reg = register_default_extractors(ExtractionConfig(), whisper=None)
    assert reg.get_for(Path("/tmp/x.mp3")) is None


def test_registry_skips_audio_extractor_with_noop_whisper() -> None:
    from corpus_forge.config import ExtractionConfig
    from corpus_forge.extractors.registry import register_default_extractors

    reg = register_default_extractors(ExtractionConfig(), whisper=NoopWhisper())
    assert reg.get_for(Path("/tmp/x.mp3")) is None
