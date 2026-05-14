"""Phase D / Wave 4 — VLM (vision-language model) plug-in surface.

Public API:

- :class:`VLMBackend` — :class:`~typing.Protocol` for OCR /
  image-description backends.
- :class:`VLMRegistry`, :data:`registry` — flat registry of backend
  instances keyed on ``backend.name`` (mirrors
  :class:`corpus_forge.embedders.registry.EmbedderRegistry`).
- :func:`get_active_vlm` — boot-time factory driven by
  :class:`corpus_forge.config.VLMConfig`.
- :class:`NoopVLM` — used when ``config.vlm.backend == "none"``; every
  operational call raises :class:`VLMUnavailableError` so callers fail
  loud instead of silently producing empty Markdown.
- :class:`VLMError` + subclasses — operational failure hierarchy.

Importing this package must NOT pull in ``requests`` (the Ollama /
Mistral backends lazy-import inside their methods). Wave 5/6 OCR
integration depends on a clean import even when the ``[ocr]`` extra is
not installed.
"""

from __future__ import annotations

from .base import (
    NoopVLM,
    VLMBackend,
    VLMError,
    VLMResponseError,
    VLMTimeoutError,
    VLMUnavailableError,
)
from .registry import VLMRegistry, get_active_vlm, registry

__all__ = [
    "NoopVLM",
    "VLMBackend",
    "VLMError",
    "VLMRegistry",
    "VLMResponseError",
    "VLMTimeoutError",
    "VLMUnavailableError",
    "get_active_vlm",
    "registry",
]
