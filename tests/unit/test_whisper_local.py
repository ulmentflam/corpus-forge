"""Phase G (G-02) — :class:`LocalWhisper` unit tests.

faster-whisper is patched at the module level so these tests run on a
machine without the ``[whisper]`` extra installed.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.whisper.base import WhisperBackend, WhisperUnavailableError
from corpus_forge.whisper.local import LocalWhisper, _seconds_to_mmss

# ── Helpers ─────────────────────────────────────────────────────────────


def _make_segment(start: float, text: str) -> MagicMock:
    seg = MagicMock()
    seg.start = start
    seg.text = text
    return seg


def _install_fake_faster_whisper(model_mock: MagicMock) -> None:
    """Inject a fake ``faster_whisper`` module so the lazy import works."""
    mod = types.ModuleType("faster_whisper")
    mod.WhisperModel = MagicMock(return_value=model_mock)  # type: ignore[attr-defined]
    sys.modules["faster_whisper"] = mod


# ── Protocol surface ───────────────────────────────────────────────────


def test_satisfies_whisper_protocol() -> None:
    assert isinstance(LocalWhisper(), WhisperBackend)


def test_name_is_local() -> None:
    assert LocalWhisper().name == "local"


def test_defaults() -> None:
    w = LocalWhisper()
    assert w.model == "small"
    assert w.compute_type == "auto"
    assert w.device == "auto"


# ── _seconds_to_mmss ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0.0, "00:00"),
        (5.4, "00:05"),
        (61.0, "01:01"),
        (3599.9, "59:59"),
        (3600.0, "01:00:00"),
        (7322.5, "02:02:02"),
        (-12.3, "00:00"),
    ],
)
def test_seconds_to_mmss(seconds: float, expected: str) -> None:
    assert _seconds_to_mmss(seconds) == expected


# ── Lazy-import failure → WhisperUnavailableError ───────────────────────


def test_warmup_without_faster_whisper_raises() -> None:
    """If ``faster_whisper`` isn't importable, ``warmup`` raises a clean error."""
    saved = sys.modules.pop("faster_whisper", None)
    try:
        with patch.dict(sys.modules, {"faster_whisper": None}):
            w = LocalWhisper()
            with pytest.raises(WhisperUnavailableError, match=r"(?i)faster-whisper|install"):
                w.warmup()
    finally:
        if saved is not None:
            sys.modules["faster_whisper"] = saved


def test_transcribe_without_faster_whisper_raises(tmp_path) -> None:
    saved = sys.modules.pop("faster_whisper", None)
    try:
        with patch.dict(sys.modules, {"faster_whisper": None}):
            w = LocalWhisper()
            with pytest.raises(WhisperUnavailableError):
                w.transcribe(b"\x00\x01\x02")
    finally:
        if saved is not None:
            sys.modules["faster_whisper"] = saved


# ── Model-load failure path ─────────────────────────────────────────────


def test_warmup_propagates_model_load_failure() -> None:
    mod = types.ModuleType("faster_whisper")

    def _broken_init(*_a, **_kw):
        raise RuntimeError("corrupt weights")

    mod.WhisperModel = _broken_init  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"faster_whisper": mod}):
        w = LocalWhisper(device="cpu", compute_type="int8")
        with pytest.raises(WhisperUnavailableError, match=r"(?i)load|model"):
            w.warmup()


# ── Happy path: segments → Markdown ─────────────────────────────────────


def test_transcribe_formats_segments_with_timestamps() -> None:
    fake_model = MagicMock()
    segments = [
        _make_segment(0.0, " Hello world."),
        _make_segment(2.7, " Second segment."),
        _make_segment(65.0, " Crossed-minute segment."),
    ]
    fake_model.transcribe.return_value = (iter(segments), MagicMock())
    _install_fake_faster_whisper(fake_model)

    w = LocalWhisper(model="tiny", compute_type="int8", device="cpu")
    result = w.transcribe(b"\x00\x01\x02\x03")

    assert "**[00:00]** Hello world." in result
    assert "**[00:02]** Second segment." in result
    assert "**[01:05]** Crossed-minute segment." in result


def test_transcribe_drops_empty_segments() -> None:
    fake_model = MagicMock()
    segments = [
        _make_segment(0.0, "real text"),
        _make_segment(1.5, "   "),
        _make_segment(3.0, ""),
        _make_segment(4.0, "more"),
    ]
    fake_model.transcribe.return_value = (iter(segments), MagicMock())
    _install_fake_faster_whisper(fake_model)

    w = LocalWhisper(device="cpu", compute_type="int8")
    out = w.transcribe(b"x")
    assert "real text" in out
    assert "more" in out
    # Empty/whitespace segments are dropped.
    assert out.count("**[") == 2


def test_transcribe_forwards_language_kwarg() -> None:
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter([_make_segment(0.0, "hi")]), MagicMock())
    _install_fake_faster_whisper(fake_model)

    LocalWhisper(device="cpu", compute_type="int8").transcribe(b"x", language="en")
    _args, kwargs = fake_model.transcribe.call_args
    assert kwargs.get("language") == "en"


def test_transcribe_no_language_kwarg_when_none() -> None:
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter([_make_segment(0.0, "hi")]), MagicMock())
    _install_fake_faster_whisper(fake_model)

    LocalWhisper(device="cpu", compute_type="int8").transcribe(b"x", language=None)
    _args, kwargs = fake_model.transcribe.call_args
    assert "language" not in kwargs


def test_warmup_loads_model_once() -> None:
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter([]), MagicMock())
    mod = types.ModuleType("faster_whisper")
    ctor = MagicMock(return_value=fake_model)
    mod.WhisperModel = ctor  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"faster_whisper": mod}):
        w = LocalWhisper(device="cpu", compute_type="int8")
        w.warmup()
        w.warmup()
        # Second call must not re-instantiate the model.
        assert ctor.call_count == 1


def test_transcribe_loads_model_lazily() -> None:
    fake_model = MagicMock()
    fake_model.transcribe.return_value = (iter([_make_segment(0.0, "hi")]), MagicMock())
    mod = types.ModuleType("faster_whisper")
    ctor = MagicMock(return_value=fake_model)
    mod.WhisperModel = ctor  # type: ignore[attr-defined]
    with patch.dict(sys.modules, {"faster_whisper": mod}):
        w = LocalWhisper(device="cpu", compute_type="int8")
        # Not loaded yet:
        assert w._model is None
        w.transcribe(b"x")
        assert w._model is fake_model
        ctor.assert_called_once()
