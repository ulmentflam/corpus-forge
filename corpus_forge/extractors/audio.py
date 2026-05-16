"""Phase G — :class:`AudioExtractor`.

Reads raw audio bytes (``.mp3 .wav .m4a .ogg .flac``), pipes them
through the active :class:`~corpus_forge.whisper.base.WhisperBackend`,
and returns a Markdown transcript inside an
:class:`~corpus_forge.extractors.base.ExtractedDocument`.

When the configured backend is :class:`NoopWhisper`
(``config.whisper.backend == "none"``), :meth:`AudioExtractor.extract`
returns ``None`` so the audio file is silently skipped on ingest —
mirrors the Phase D NoopVLM / ImageExtractor pattern.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .base import ExtractedDocument

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.whisper.base import WhisperBackend

logger = logging.getLogger(__name__)


class AudioExtractor:
    """Audio-to-Markdown extractor backed by a Whisper backend.

    Args:
        whisper: A :class:`~corpus_forge.whisper.base.WhisperBackend`
            (keyword-only). Required — the extractor delegates to
            ``whisper.transcribe(audio_bytes, language=...)``.
        language: Optional ISO-639-1 hint forwarded to the backend.
            ``None`` / empty string = auto-detect.
    """

    name = "audio"
    supported_extensions: tuple[str, ...] = (
        ".mp3",
        ".wav",
        ".m4a",
        ".ogg",
        ".flac",
    )
    supported_filenames: tuple[str, ...] = ()

    def __init__(
        self,
        *,
        whisper: WhisperBackend,
        language: str | None = None,
    ) -> None:
        self.whisper = whisper
        # Treat empty string as "auto-detect" so config defaults pass
        # through cleanly.
        self.language: str | None = language if language else None

    def extract(self, path: Path) -> ExtractedDocument | None:
        """Transcribe ``path`` and return the result, or ``None`` to skip."""
        # NoopWhisper signals "no transcription configured" — skip the
        # file silently rather than raising, so audio/video sit dormant
        # on disk until the user opts in. Import is lazy so the
        # extractor module stays import-cheap.
        from corpus_forge.whisper.base import NoopWhisper  # noqa: PLC0415

        if isinstance(self.whisper, NoopWhisper):
            logger.debug("AudioExtractor: no whisper backend configured — skipping %s", path)
            return None

        audio_bytes = path.read_bytes()
        markdown = self.whisper.transcribe(audio_bytes, language=self.language)
        backend_name = self.whisper.name
        model = getattr(self.whisper, "model", backend_name)
        return ExtractedDocument(
            text=markdown,
            chunker_hint="passthrough",
            language=self.language,
            metadata={
                "extractor": "whisper",
                "whisper_backend": backend_name,
                "byte_count": len(audio_bytes),
            },
            labels=[("format", "audio"), ("transcription", str(model))],
        )
