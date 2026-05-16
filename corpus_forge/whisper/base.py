"""Phase G — Whisper (audio transcription) Protocol + exceptions.

The Whisper layer is the audio-to-text plug-in surface used by the Phase
G ``AudioExtractor`` and ``VideoExtractor``. Every backend implements
the same two-method Protocol; audio bytes (already extracted from the
file or video container by the caller) are handed in and a Markdown
transcript is returned.

The Protocol mirrors :class:`corpus_forge.vlm.base.VLMBackend` on
purpose: a flat :class:`~typing.Protocol`, a registry keyed on
``backend.name``, and a ``get_active_whisper`` factory driven by
:class:`corpus_forge.config.WhisperConfig`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# ── Exceptions ──────────────────────────────────────────────────────────


class WhisperError(Exception):
    """Base for every Whisper-layer operational failure.

    Callers can ``except WhisperError`` to swallow all backend failures
    uniformly. Each subclass below carves out a discriminable failure
    mode so smarter callers can decide whether to retry, degrade, or
    surface a hard error.
    """


class WhisperUnavailableError(WhisperError):
    """The backend cannot be reached or is not configured.

    Raised by:

    - :class:`NoopWhisper` for every operational call (``backend="none"``).
    - :class:`~corpus_forge.whisper.local.LocalWhisper` when the
      ``faster-whisper`` package isn't installed or the model fails to
      load.
    - :class:`~corpus_forge.whisper.remote.RemoteWhisper` when the
      endpoint is unreachable or the API key is missing / rejected
      (401 / 403).
    """


class WhisperTimeoutError(WhisperError):
    """The backend was reachable but exceeded the configured timeout.

    Distinct from :class:`WhisperUnavailableError` so callers can
    implement bounded retry/back-off without giving up entirely.
    """


class WhisperResponseError(WhisperError):
    """The backend returned a malformed or error response.

    Covers non-2xx HTTP, missing keys in the JSON body, invalid JSON,
    and unexpected/empty payloads. The response body (truncated to a
    few hundred chars) is preserved in the message so log lines stay
    useful for debugging.
    """


# ── Protocol ────────────────────────────────────────────────────────────


@runtime_checkable
class WhisperBackend(Protocol):
    """The plug-in surface for audio transcription backends.

    Implementations live behind this Protocol. ``audio`` is raw audio
    bytes (the caller owns the demux / re-encode step for video files);
    the backend returns a Markdown transcript that downstream chunkers
    will process unchanged.
    """

    name: str

    def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        """Transcribe ``audio`` to Markdown.

        Args:
            audio: Raw audio bytes (e.g. WAV / MP3 / M4A / FLAC). The
                exact accepted container depends on the backend; both
                :class:`LocalWhisper` (via ``faster-whisper``) and
                :class:`RemoteWhisper` (via the OpenAI Whisper API)
                accept the common audio formats.
            language: Optional ISO-639-1 hint (``en`` / ``fr`` / ...).
                ``None`` means auto-detect.

        Returns:
            A Markdown transcript. Segment-level backends include
            ``**[mm:ss]**`` timestamp prefixes so timestamps survive
            downstream chunking.
        """
        ...

    def warmup(self) -> None:
        """Cheap health-check / model preload.

        Implementations should fail fast (raise
        :class:`WhisperUnavailableError`) if the backend cannot be
        reached at construction-time rather than at first-call time.
        """
        ...


# ── Noop implementation ────────────────────────────────────────────────


class NoopWhisper:
    """Default backend when ``config.whisper.backend == "none"``.

    Every operational call raises :class:`WhisperUnavailableError`.
    Callers in the audio/video extractor paths typically check
    ``isinstance(backend, NoopWhisper)`` (or call the factory and let
    the extractor return ``None`` to skip the file) so audio/video
    files are silently skipped on ingest unless the user opts in.
    """

    name = "none"

    def transcribe(
        self,
        audio: bytes,  # noqa: ARG002 — Protocol signature parity
        *,
        language: str | None = None,  # noqa: ARG002 — Protocol signature parity
    ) -> str:
        raise WhisperUnavailableError(
            "No Whisper backend configured (config.whisper.backend = 'none'). "
            "Set backend = 'local' or 'remote' to enable transcription."
        )

    def warmup(self) -> None:
        raise WhisperUnavailableError(
            "No Whisper backend configured (config.whisper.backend = 'none')."
        )
