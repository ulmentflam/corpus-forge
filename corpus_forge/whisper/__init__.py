"""Phase G / P0 — Whisper transcription plug-in surface.

Public API:

- :class:`WhisperBackend` — :class:`~typing.Protocol` for audio-to-text
  transcription backends.
- :class:`WhisperRegistry`, :data:`registry` — flat registry of backend
  instances keyed on ``backend.name`` (mirrors
  :class:`corpus_forge.vlm.registry.VLMRegistry`).
- :func:`get_active_whisper` — boot-time factory driven by
  :class:`corpus_forge.config.WhisperConfig`.
- :class:`NoopWhisper` — used when ``config.whisper.backend == "none"``;
  every operational call raises :class:`WhisperUnavailableError` so
  callers fail loud instead of silently producing empty transcripts.
- :class:`WhisperError` + subclasses — operational failure hierarchy.

Importing this package must NOT pull in ``faster_whisper`` or ``requests``
(the local + remote backends lazy-import inside their methods). Phase G
audio/video extractors depend on a clean import even when the
``[whisper]`` extra is not installed.
"""

from __future__ import annotations

from .base import (
    NoopWhisper,
    WhisperBackend,
    WhisperError,
    WhisperResponseError,
    WhisperTimeoutError,
    WhisperUnavailableError,
)
from .registry import WhisperRegistry, get_active_whisper, registry

__all__ = [
    "NoopWhisper",
    "WhisperBackend",
    "WhisperError",
    "WhisperRegistry",
    "WhisperResponseError",
    "WhisperTimeoutError",
    "WhisperUnavailableError",
    "get_active_whisper",
    "registry",
]
