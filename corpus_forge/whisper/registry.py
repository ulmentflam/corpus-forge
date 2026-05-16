"""Phase G — :class:`WhisperRegistry` and ``get_active_whisper``.

Shape mirrors :class:`corpus_forge.vlm.registry.VLMRegistry`.

The registry is a flat ``dict[str, WhisperBackend]`` keyed on
``backend.name``. ``get_active_whisper(config)`` is the boot-time
factory: it constructs the concrete backend named by
``config.whisper.backend``, registers it in the module-level singleton,
and returns it. Heavy backends (``faster-whisper`` weights, ``requests``)
are :mod:`importlib`-loaded so importing :mod:`corpus_forge.whisper`
with no ``[whisper]`` extra installed does not error.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

from .base import NoopWhisper, WhisperBackend, WhisperUnavailableError

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.config import Config

logger = logging.getLogger(__name__)


class WhisperRegistry:
    """Registry for Whisper backend instances.

    Last-write-wins on ``register`` so callers can swap out a backend
    (e.g. tests injecting a stub) by re-registering. ``get(name)``
    returns ``None`` for unknown names — the factory layer decides
    whether the miss is fatal.
    """

    def __init__(self) -> None:
        self._instances: dict[str, WhisperBackend] = {}

    def register(self, backend: WhisperBackend) -> None:
        """Register ``backend`` keyed on ``backend.name``."""
        if backend.name in self._instances:
            logger.debug("WhisperRegistry: replacing existing backend %r", backend.name)
        self._instances[backend.name] = backend

    def get(self, name: str) -> WhisperBackend | None:
        """Return the backend registered under ``name`` or ``None``."""
        return self._instances.get(name)

    def list_names(self) -> list[str]:
        """Return every registered backend name."""
        return list(self._instances.keys())

    def clear(self) -> None:
        """Drop every registered backend (test helper)."""
        self._instances.clear()


# Module-level singleton (mirrors `vlm.registry.registry`).
registry = WhisperRegistry()


def _load_class(submodule: str, class_name: str) -> type | None:
    """Lazy-load ``corpus_forge.whisper.<submodule>.<class_name>``.

    Returns ``None`` if the submodule isn't installed (e.g. ``[whisper]``
    extra missing). The factory layer turns that into a clean
    :class:`WhisperUnavailableError`.
    """
    try:
        mod = importlib.import_module(f"corpus_forge.whisper.{submodule}")
    except ImportError as exc:
        logger.debug("whisper submodule %s not available: %s", submodule, exc)
        return None
    return getattr(mod, class_name, None)


def get_active_whisper(config: Config) -> WhisperBackend:
    """Construct + register the Whisper backend named by ``config.whisper``.

    Resolution order:

    - ``backend = "none"``   → :class:`NoopWhisper` (operational calls raise).
    - ``backend = "local"``  → :class:`corpus_forge.whisper.local.LocalWhisper`,
      constructed from ``whisper.model`` + ``whisper.local_compute_type``.
    - ``backend = "remote"`` → :class:`corpus_forge.whisper.remote.RemoteWhisper`,
      with the API key resolved via the configured env var.
      Raises :class:`WhisperUnavailableError` if the key is missing — the
      misconfiguration is caught at boot time, not at transcribe time.

    The constructed backend is registered in the module-level
    :data:`registry` so other call-sites can look it up by name.
    """
    cfg = config.whisper
    backend_name = cfg.backend

    if backend_name == "none":
        noop = NoopWhisper()
        registry.register(noop)
        return noop

    if backend_name == "local":
        cls = _load_class("local", "LocalWhisper")
        if cls is None:
            raise WhisperUnavailableError(
                "Local Whisper backend selected but `corpus_forge.whisper.local` "
                "could not be imported — install the `[whisper]` extra "
                "(`uv sync --extra whisper`)."
            )
        backend = cls(
            model=cfg.model,
            compute_type=cfg.local_compute_type,
            device="auto",
        )
        registry.register(backend)
        return backend

    if backend_name == "remote":
        import os  # noqa: PLC0415 — local; mirrors VLM pattern

        api_key = os.environ.get(cfg.remote_api_key_env)
        if not api_key:
            raise WhisperUnavailableError(
                "Remote Whisper backend selected but the API key is missing — "
                f"set {cfg.remote_api_key_env!r} in secrets.env."
            )
        cls = _load_class("remote", "RemoteWhisper")
        if cls is None:
            raise WhisperUnavailableError(
                "Remote Whisper backend selected but `corpus_forge.whisper.remote` "
                "could not be imported — install the `[whisper]` extra."
            )
        backend = cls(
            base_url=str(cfg.remote_base_url).rstrip("/"),
            model=cfg.model,
            api_key=api_key,
            timeout_s=cfg.timeout_s,
        )
        registry.register(backend)
        return backend

    # pyrefly: Literal["local","remote","none"] is exhaustive, so this is
    # unreachable in well-typed code. Belt-and-suspenders for runtime
    # misuse (e.g. attribute-assigned backend).
    raise WhisperUnavailableError(f"Unknown Whisper backend: {backend_name!r}")
