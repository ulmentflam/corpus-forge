"""Phase G (G-08) — live-LocalWhisper end-to-end tests.

Marker-gated by ``requires_whisper_local``. Auto-skipped at collection
time when:

- ``faster-whisper`` is not installed (the ``[whisper]`` extra missing).
- The tiny model fails to load (offline first-run download).

Fixtures are generated in-test inside ``tmp_path`` (a 1-second silent
WAV via stdlib :mod:`wave` + :mod:`numpy`) so we don't ship audio fixtures
in the repo and don't disturb the Phase D multi-format ingest test on
machines without the ``[whisper]`` extra.

Wall-clock budget per test: ~5-20 s for the tiny model on M-series CPU.
"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.requires_whisper_local]


def _synth_silent_wav(out: Path, seconds: float = 1.0, sample_rate: int = 16000) -> Path:
    """Write a mono silent WAV file. Whisper will return empty / near-empty
    transcript for it, but the pipeline still exercises the full code path
    (model load → segment iteration → format)."""
    import numpy as np

    frames = np.zeros(int(seconds * sample_rate), dtype=np.int16)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(frames.tobytes())
    return out


@pytest.mark.timeout(120)
def test_local_whisper_round_trip(tmp_path: Path) -> None:
    """Tiny model transcribes a 1-second silent WAV without errors."""
    from corpus_forge.whisper.local import LocalWhisper

    wav = _synth_silent_wav(tmp_path / "silent.wav")
    audio_bytes = wav.read_bytes()

    w = LocalWhisper(model="tiny", compute_type="int8", device="cpu")
    out = w.transcribe(audio_bytes)
    # Silence may produce empty transcript or near-empty; both are
    # acceptable as long as the call doesn't raise.
    assert isinstance(out, str)


@pytest.mark.timeout(120)
def test_audio_extractor_round_trip(tmp_path: Path) -> None:
    """AudioExtractor end-to-end against the synthetic WAV."""
    from corpus_forge.extractors import ExtractedDocument
    from corpus_forge.extractors.audio import AudioExtractor
    from corpus_forge.whisper.local import LocalWhisper

    wav = _synth_silent_wav(tmp_path / "silent.wav")
    w = LocalWhisper(model="tiny", compute_type="int8", device="cpu")
    doc = AudioExtractor(whisper=w).extract(wav)
    assert isinstance(doc, ExtractedDocument)
    assert doc.chunker_hint == "passthrough"
    labels = {(ns, val) for ns, val in doc.labels}
    assert ("format", "audio") in labels


@pytest.mark.timeout(10)
def test_extractor_returns_none_when_backend_none(tmp_path: Path) -> None:
    """``config.whisper.backend == "none"`` ⇒ extractor returns None."""
    from corpus_forge.extractors.audio import AudioExtractor
    from corpus_forge.whisper.base import NoopWhisper

    wav = _synth_silent_wav(tmp_path / "silent.wav")
    assert AudioExtractor(whisper=NoopWhisper()).extract(wav) is None
