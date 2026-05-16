"""Phase G — :class:`VideoExtractor`.

Demuxes the audio track from ``.mp4 .mov .webm .mkv .avi`` via
``imageio-ffmpeg`` (a Python wheel that bundles a static ``ffmpeg``
binary), then routes the audio through the active Whisper backend.

Graceful degradation:

- ``imageio-ffmpeg`` missing / no ffmpeg binary → log ERROR + return
  ``None`` (file skipped, ingest continues).
- ``ffmpeg`` invocation fails (corrupted video, no audio track) →
  log ERROR + return ``None``.
- :class:`NoopWhisper` configured → return ``None`` silently (mirrors
  :class:`AudioExtractor`).
"""

from __future__ import annotations

import contextlib
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from .base import ExtractedDocument

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.whisper.base import WhisperBackend

logger = logging.getLogger(__name__)


def _find_ffmpeg() -> str | None:
    """Return the path to the ``ffmpeg`` binary or ``None``.

    Lazy-imports ``imageio_ffmpeg`` so the extractor module stays
    import-cheap when the ``[whisper]`` extra isn't installed.
    """
    try:
        # pyrefly: ignore[missing-import]  # optional dep, install via [whisper] extra
        import imageio_ffmpeg  # noqa: PLC0415
    except ImportError:
        return None
    try:
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # pragma: no cover — extremely defensive
        return None


class VideoExtractor:
    """Video-to-Markdown extractor (audio-track → Whisper transcript).

    Args:
        whisper: A :class:`~corpus_forge.whisper.base.WhisperBackend`
            (keyword-only). Required.
        language: Optional ISO-639-1 hint. Empty / ``None`` = auto.
    """

    name = "video"
    supported_extensions: tuple[str, ...] = (
        ".mp4",
        ".mov",
        ".webm",
        ".mkv",
        ".avi",
    )
    supported_filenames: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        whisper: WhisperBackend,
        language: str | None = None,
    ) -> None:
        self.whisper = whisper
        self.language: str | None = language if language else None

    def extract(self, path: Path) -> ExtractedDocument | None:
        """Demux audio + transcribe, or return ``None`` to skip."""
        from corpus_forge.whisper.base import NoopWhisper  # noqa: PLC0415

        if isinstance(self.whisper, NoopWhisper):
            logger.debug("VideoExtractor: no whisper backend configured — skipping %s", path)
            return None

        ffmpeg = _find_ffmpeg()
        if ffmpeg is None:
            logger.error(
                "VideoExtractor: ffmpeg not available (install the [whisper] "
                "extra: `uv sync --extra whisper`) — skipping %s",
                path,
            )
            return None

        # Demux audio to a temporary WAV. ``-vn`` strips video; ``-acodec
        # pcm_s16le`` produces an uncompressed mono 16 kHz WAV which is
        # the format Whisper-family models prefer. ``-y`` overwrites the
        # tempfile placeholder NamedTemporaryFile created.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            wav_path = Path(tmp.name)
        try:
            cmd = [
                ffmpeg,
                "-y",
                "-i",
                str(path),
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(wav_path),
            ]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    check=False,
                )
            except OSError as exc:
                logger.error("VideoExtractor: failed to invoke ffmpeg for %s: %s", path, exc)
                return None
            if proc.returncode != 0:
                stderr = (proc.stderr or b"").decode("utf-8", errors="replace")[-400:]
                logger.error(
                    "VideoExtractor: ffmpeg failed (rc=%d) for %s: %s",
                    proc.returncode,
                    path,
                    stderr,
                )
                return None

            audio_bytes = wav_path.read_bytes()
            if not audio_bytes:
                logger.error("VideoExtractor: empty audio track for %s — skipping", path)
                return None

            markdown = self.whisper.transcribe(audio_bytes, language=self.language)
            backend_name = self.whisper.name
            model = getattr(self.whisper, "model", backend_name)
            return ExtractedDocument(
                text=markdown,
                chunker_hint="passthrough",
                language=self.language,
                metadata={
                    "extractor": "whisper-video",
                    "whisper_backend": backend_name,
                    "byte_count": path.stat().st_size,
                },
                labels=[("format", "video"), ("transcription", str(model))],
            )
        finally:
            with contextlib.suppress(OSError):
                wav_path.unlink(missing_ok=True)
