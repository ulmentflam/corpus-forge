"""Phase G — :class:`LocalWhisper` backend via ``faster-whisper``.

Loads a Whisper model in-process and transcribes raw audio bytes to a
Markdown transcript with ``**[mm:ss]** segment text`` formatting. The
``faster-whisper`` package is lazy-imported per the project pattern —
importing :mod:`corpus_forge.whisper.local` with the ``[whisper]`` extra
absent does NOT error; the ``ImportError`` only surfaces at first
:meth:`LocalWhisper.transcribe` call.

Device detection mirrors
:class:`corpus_forge.embedders.sentence_transformers.SentenceTransformersEmbedder`:
``device="auto"`` picks MPS on Apple Silicon, CUDA on NVIDIA, CPU
otherwise.

faster-whisper only supports ``cpu`` and ``cuda`` execution providers
natively. MPS is not yet supported upstream as of the project's
calibration date, so ``auto`` on Apple Silicon falls back to ``cpu``
(int8 quantisation keeps throughput acceptable — the ``small`` model
runs at ~5x real-time on M-series CPU).
"""

from __future__ import annotations

import contextlib
import logging
import tempfile
from pathlib import Path

from .base import WhisperResponseError, WhisperUnavailableError

logger = logging.getLogger(__name__)


def _detect_device() -> str:
    """Pick a device using the SentenceTransformers heuristic.

    Returns the string ``faster_whisper`` wants:

    - ``"cuda"`` if CUDA is available.
    - ``"cpu"`` otherwise. (MPS is not yet supported by faster-whisper
      upstream; on Apple Silicon we fall back to CPU + int8 quantisation
      which keeps the ``small`` model at ~5x real-time.)

    The probe imports ``torch`` lazily so importing this module without
    the ML stack installed still works.
    """
    try:
        import torch  # noqa: PLC0415
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


def _seconds_to_mmss(seconds: float) -> str:
    """Format a float-seconds offset as ``mm:ss``.

    Long-form audio occasionally crosses the hour boundary; we widen
    to ``hh:mm:ss`` automatically when ``seconds >= 3600``.
    """
    if seconds < 0:
        seconds = 0.0
    total = int(seconds)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class LocalWhisper:
    """In-process Whisper transcription backend.

    Args:
        model: ``faster-whisper`` model name (``tiny`` / ``base`` /
            ``small`` / ``medium`` / ``large``). Default ``small``.
        compute_type: ``"auto"``, ``"float16"``, ``"int8"``, or
            ``"int8_float16"``. ``"auto"`` picks ``int8`` on CPU and
            ``float16`` on CUDA.
        device: ``"auto"``, ``"cpu"``, or ``"cuda"``. ``"auto"`` uses
            :func:`_detect_device`.
    """

    name = "local"

    def __init__(
        self,
        *,
        model: str = "small",
        compute_type: str = "auto",
        device: str = "auto",
    ) -> None:
        self.model = model
        self.compute_type = compute_type
        self.device = device
        # Lazy-loaded faster_whisper.WhisperModel handle.
        self._model: object | None = None

    # ── public API ────────────────────────────────────────────────────

    def warmup(self) -> None:
        """Force-load the model so first transcribe call doesn't pay the cost.

        Raises :class:`WhisperUnavailableError` if ``faster-whisper`` is
        not installed or the model fails to load.
        """
        self._load_model()

    def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        """Transcribe ``audio`` bytes to a Markdown transcript.

        The audio bytes are written to a temporary file (``faster-whisper``'s
        ``transcribe()`` accepts a path or an in-memory file-like, but
        the path API is the well-tested one across audio container formats)
        and decoded via ffmpeg internally by the library.

        Args:
            audio: Raw audio bytes (any format ``faster-whisper`` can
                decode — MP3, WAV, M4A, OGG, FLAC, etc.).
            language: ISO-639-1 hint. ``None`` = auto-detect.

        Returns:
            Markdown transcript. Each segment is rendered on its own
            paragraph with a ``**[mm:ss]**`` timestamp prefix so
            timestamps survive downstream chunking.

        Raises:
            WhisperUnavailableError: ``faster-whisper`` not installed
                or the model fails to load.
            WhisperResponseError: ``faster-whisper`` raised an
                unexpected exception (corrupt audio, etc.).
        """
        self._load_model()
        if self._model is None:  # pragma: no cover — _load_model raises first
            raise WhisperUnavailableError("LocalWhisper model failed to load")

        with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as tmp:
            tmp.write(audio)
            tmp_path = Path(tmp.name)

        try:
            # faster-whisper returns (segments-iterator, info)
            kwargs: dict = {}
            if language:
                kwargs["language"] = language
            try:
                segments_iter, _info = self._model.transcribe(str(tmp_path), **kwargs)  # type: ignore[attr-defined]
            except Exception as exc:  # pragma: no cover — guard
                raise WhisperResponseError(f"faster-whisper transcribe failed: {exc!s}") from exc
            return self._format_segments(segments_iter)
        finally:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)

    # ── internals ─────────────────────────────────────────────────────

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            # pyrefly: ignore[missing-import]  # optional dep, install via [whisper] extra
            from faster_whisper import WhisperModel  # noqa: PLC0415
        except ImportError as exc:
            raise WhisperUnavailableError(
                "faster-whisper is not installed — `uv sync --extra whisper`."
            ) from exc

        device = self.device if self.device != "auto" else _detect_device()
        compute_type = self.compute_type
        if compute_type == "auto":
            compute_type = "float16" if device == "cuda" else "int8"
        try:
            self._model = WhisperModel(
                self.model,
                device=device,
                compute_type=compute_type,
            )
        except Exception as exc:
            raise WhisperUnavailableError(
                f"Failed to load faster-whisper model {self.model!r} "
                f"(device={device}, compute_type={compute_type}): {exc!s}"
            ) from exc

    def _format_segments(self, segments_iter: object) -> str:
        """Render a faster-whisper segments iterator as Markdown."""
        lines: list[str] = []
        for seg in segments_iter:  # type: ignore[attr-defined]
            start = getattr(seg, "start", 0.0) or 0.0
            text = (getattr(seg, "text", "") or "").strip()
            if not text:
                continue
            ts = _seconds_to_mmss(float(start))
            lines.append(f"**[{ts}]** {text}")
        return "\n\n".join(lines)
