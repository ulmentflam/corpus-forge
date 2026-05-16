"""Phase G (G-06) — :class:`VideoExtractor` unit tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.extractors import ExtractedDocument, Extractor
from corpus_forge.extractors.video import VideoExtractor
from corpus_forge.whisper.base import NoopWhisper, WhisperBackend


def _make_whisper(name: str = "local", returns: str = "**[00:00]** body") -> MagicMock:
    w = MagicMock(spec=WhisperBackend)
    w.name = name
    w.model = "small"
    w.transcribe.return_value = returns
    return w


_VID_BYTES = b"\x00\x00\x00\x18ftypmp42"


def _write_video(tmp_path: Path, name: str = "v.mp4", body: bytes = _VID_BYTES) -> Path:
    p = tmp_path / name
    p.write_bytes(body)
    return p


def _mock_ffmpeg_run(*, returncode: int = 0, wav_body: bytes = b"RIFF\x00\x00\x00\x00WAVE"):
    """Patch subprocess.run AND make the WAV tempfile materialise with content."""

    def _side_effect(cmd, capture_output=True, check=False):
        # The output WAV path is the last argument.
        out_path = Path(cmd[-1])
        out_path.write_bytes(wav_body)
        result = MagicMock()
        result.returncode = returncode
        result.stderr = b""
        return result

    return _side_effect


# ── Protocol conformance ────────────────────────────────────────────────


def test_extractor_protocol_conformance() -> None:
    ex: Extractor = VideoExtractor(whisper=_make_whisper())
    assert isinstance(ex.supported_extensions, tuple)


def test_supported_extensions() -> None:
    ex = VideoExtractor(whisper=_make_whisper())
    assert set(ex.supported_extensions) == {".mp4", ".mov", ".webm", ".mkv", ".avi"}


def test_constructor_requires_keyword_whisper() -> None:
    with pytest.raises(TypeError):
        VideoExtractor(_make_whisper())  # type: ignore[misc]


# ── Happy path ──────────────────────────────────────────────────────────


def test_extract_returns_extracted_document(tmp_path: Path) -> None:
    p = _write_video(tmp_path)
    w = _make_whisper(returns="**[00:00]** spoken")
    with (
        patch("corpus_forge.extractors.video._find_ffmpeg", return_value="/fake/ffmpeg"),
        patch("subprocess.run", side_effect=_mock_ffmpeg_run()),
    ):
        doc = VideoExtractor(whisper=w).extract(p)
    assert isinstance(doc, ExtractedDocument)
    assert doc.text == "**[00:00]** spoken"
    assert doc.chunker_hint == "passthrough"


def test_extract_metadata_shape(tmp_path: Path) -> None:
    p = _write_video(tmp_path)
    w = _make_whisper(name="remote")
    with (
        patch("corpus_forge.extractors.video._find_ffmpeg", return_value="/fake/ffmpeg"),
        patch("subprocess.run", side_effect=_mock_ffmpeg_run()),
    ):
        doc = VideoExtractor(whisper=w).extract(p)
    assert doc is not None
    assert doc.metadata["extractor"] == "whisper-video"
    assert doc.metadata["whisper_backend"] == "remote"
    assert doc.metadata["byte_count"] > 0


def test_extract_labels(tmp_path: Path) -> None:
    p = _write_video(tmp_path)
    w = _make_whisper()
    with (
        patch("corpus_forge.extractors.video._find_ffmpeg", return_value="/fake/ffmpeg"),
        patch("subprocess.run", side_effect=_mock_ffmpeg_run()),
    ):
        doc = VideoExtractor(whisper=w).extract(p)
    assert doc is not None
    labels = {(ns, val) for ns, val in doc.labels}
    assert ("format", "video") in labels
    assert ("transcription", "small") in labels


def test_extract_demuxes_audio_via_ffmpeg(tmp_path: Path) -> None:
    p = _write_video(tmp_path)
    w = _make_whisper()
    side = _mock_ffmpeg_run(wav_body=b"some-wav-bytes")
    with (
        patch("corpus_forge.extractors.video._find_ffmpeg", return_value="/fake/ffmpeg"),
        patch("subprocess.run", side_effect=side) as mp,
    ):
        VideoExtractor(whisper=w).extract(p)
    # ffmpeg invoked with -i <video>
    args, _kwargs = mp.call_args
    cmd = args[0]
    assert "/fake/ffmpeg" in cmd[0]
    assert "-i" in cmd
    assert str(p) in cmd
    # Whisper got the WAV bytes that ffmpeg wrote.
    sent_bytes, _ = w.transcribe.call_args[0], w.transcribe.call_args[1]
    assert sent_bytes[0] == b"some-wav-bytes"


# ── Graceful degradation ────────────────────────────────────────────────


def test_noop_whisper_returns_none(tmp_path: Path) -> None:
    p = _write_video(tmp_path)
    ex = VideoExtractor(whisper=NoopWhisper())
    assert ex.extract(p) is None


def test_missing_ffmpeg_logs_error_and_skips(tmp_path: Path, caplog) -> None:
    import logging

    p = _write_video(tmp_path)
    w = _make_whisper()
    with (
        patch("corpus_forge.extractors.video._find_ffmpeg", return_value=None),
        caplog.at_level(logging.ERROR, logger="corpus_forge.extractors.video"),
    ):
        out = VideoExtractor(whisper=w).extract(p)
    assert out is None
    assert any("ffmpeg" in rec.message.lower() for rec in caplog.records)


def test_ffmpeg_failure_logs_error_and_skips(tmp_path: Path, caplog) -> None:
    import logging

    p = _write_video(tmp_path)
    w = _make_whisper()

    def _side_effect(cmd, capture_output=True, check=False):
        result = MagicMock()
        result.returncode = 1
        result.stderr = b"no audio stream"
        return result

    with (
        patch("corpus_forge.extractors.video._find_ffmpeg", return_value="/fake/ffmpeg"),
        patch("subprocess.run", side_effect=_side_effect),
        caplog.at_level(logging.ERROR, logger="corpus_forge.extractors.video"),
    ):
        out = VideoExtractor(whisper=w).extract(p)
    assert out is None
    assert any("ffmpeg failed" in rec.message.lower() for rec in caplog.records)


def test_empty_audio_logs_error_and_skips(tmp_path: Path, caplog) -> None:
    import logging

    p = _write_video(tmp_path)
    w = _make_whisper()
    with (
        patch("corpus_forge.extractors.video._find_ffmpeg", return_value="/fake/ffmpeg"),
        patch("subprocess.run", side_effect=_mock_ffmpeg_run(wav_body=b"")),
        caplog.at_level(logging.ERROR, logger="corpus_forge.extractors.video"),
    ):
        out = VideoExtractor(whisper=w).extract(p)
    assert out is None
    assert any("empty audio" in rec.message.lower() for rec in caplog.records)


# ── Registry integration ────────────────────────────────────────────────


def test_registry_registers_video_extractor_when_whisper_present() -> None:
    from corpus_forge.config import ExtractionConfig
    from corpus_forge.extractors.registry import register_default_extractors

    reg = register_default_extractors(ExtractionConfig(), whisper=_make_whisper())
    for ext in VideoExtractor.supported_extensions:
        ex = reg.get_for(Path(f"/tmp/v{ext}"))
        assert isinstance(ex, VideoExtractor), f"missing video extractor for {ext}"


def test_registry_skips_video_extractor_when_whisper_none() -> None:
    from corpus_forge.config import ExtractionConfig
    from corpus_forge.extractors.registry import register_default_extractors

    reg = register_default_extractors(ExtractionConfig(), whisper=None)
    assert reg.get_for(Path("/tmp/v.mp4")) is None


def test_registry_skips_video_extractor_with_noop_whisper() -> None:
    from corpus_forge.config import ExtractionConfig
    from corpus_forge.extractors.registry import register_default_extractors

    reg = register_default_extractors(ExtractionConfig(), whisper=NoopWhisper())
    assert reg.get_for(Path("/tmp/v.mp4")) is None
