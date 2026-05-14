"""Phase D / Wave 4 — :class:`VLMRegistry` and ``get_active_vlm``.

Shape mirrors :class:`corpus_forge.embedders.registry.EmbedderRegistry`.

The registry is a flat ``dict[str, VLMBackend]`` keyed on
``backend.name``. ``get_active_vlm(config)`` is the boot-time factory:
it constructs the concrete backend named by ``config.vlm.backend``,
registers it in the module-level singleton, and returns it. Heavy
backends (Ollama, Mistral — both pull in ``requests``) are
:mod:`importlib`-loaded so importing :mod:`corpus_forge.vlm` with no
``[ocr]`` extra installed does not error.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

from .base import NoopVLM, VLMBackend, VLMUnavailableError

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.config import Config

logger = logging.getLogger(__name__)


class VLMRegistry:
    """Registry for VLM backend instances.

    Last-write-wins on ``register`` so callers can swap out a backend
    (e.g. tests injecting a stub) by re-registering. ``get(name)``
    returns ``None`` for unknown names — the factory layer decides
    whether the miss is fatal.
    """

    def __init__(self) -> None:
        self._instances: dict[str, VLMBackend] = {}

    def register(self, backend: VLMBackend) -> None:
        """Register ``backend`` keyed on ``backend.name``."""
        if backend.name in self._instances:
            logger.debug("VLMRegistry: replacing existing backend %r", backend.name)
        self._instances[backend.name] = backend

    def get(self, name: str) -> VLMBackend | None:
        """Return the backend registered under ``name`` or ``None``."""
        return self._instances.get(name)

    def list_names(self) -> list[str]:
        """Return every registered backend name."""
        return list(self._instances.keys())

    def clear(self) -> None:
        """Drop every registered backend (test helper)."""
        self._instances.clear()


# Module-level singleton (mirrors `embedders.registry.registry`).
registry = VLMRegistry()


def _load_class(submodule: str, class_name: str) -> type | None:
    """Lazy-load ``corpus_forge.vlm.<submodule>.<class_name>``.

    Returns ``None`` if the submodule isn't installed (e.g. ``[ocr]``
    extra missing). The factory layer turns that into a clean
    :class:`VLMUnavailableError`.
    """
    try:
        mod = importlib.import_module(f"corpus_forge.vlm.{submodule}")
    except ImportError as exc:
        logger.debug("vlm submodule %s not available: %s", submodule, exc)
        return None
    return getattr(mod, class_name, None)


def get_active_vlm(config: Config) -> VLMBackend:
    """Construct + register the VLM backend named by ``config.vlm``.

    Resolution order:

    - ``backend = "none"``  → :class:`NoopVLM` (operational calls raise).
    - ``backend = "ollama"`` → :class:`corpus_forge.vlm.ollama.OllamaVLM`,
      constructed from ``vlm.ollama_model`` + ``vlm.ollama_url`` +
      ``vlm.timeout_s``.
    - ``backend = "mistral"`` → :class:`corpus_forge.vlm.mistral.MistralOCR`,
      with the API key resolved via :meth:`Config.resolve_mistral_api_key`.
      Raises :class:`VLMUnavailableError` if the key is missing — the
      misconfiguration is caught at boot time, not at OCR time.

    The constructed backend is registered in the module-level
    :data:`registry` so other call-sites can look it up by name.
    """
    vlm_cfg = config.vlm
    backend_name = vlm_cfg.backend

    if backend_name == "none":
        noop = NoopVLM()
        registry.register(noop)
        return noop

    if backend_name == "ollama":
        cls = _load_class("ollama", "OllamaVLM")
        if cls is None:
            raise VLMUnavailableError(
                "Ollama backend selected but `corpus_forge.vlm.ollama` could not "
                "be imported — install the `[ocr]` extra (`uv sync --extra ocr`)."
            )
        backend = cls(
            model=vlm_cfg.ollama_model,
            ollama_url=str(vlm_cfg.ollama_url).rstrip("/"),
            timeout_s=vlm_cfg.timeout_s,
        )
        registry.register(backend)
        return backend

    if backend_name == "mistral":
        api_key = config.resolve_mistral_api_key()
        if not api_key:
            raise VLMUnavailableError(
                "Mistral backend selected but the API key is missing — set "
                f"{vlm_cfg.mistral_api_key_env!r} in secrets.env."
            )
        cls = _load_class("mistral", "MistralOCR")
        if cls is None:
            raise VLMUnavailableError(
                "Mistral backend selected but `corpus_forge.vlm.mistral` could "
                "not be imported — install the `[ocr]` extra."
            )
        backend = cls(
            api_key=api_key,
            model=vlm_cfg.mistral_model,
            base_url=str(vlm_cfg.mistral_base_url).rstrip("/"),
            timeout_s=vlm_cfg.timeout_s,
        )
        registry.register(backend)
        return backend

    # pyrefly: Literal["ollama","mistral","none"] is exhaustive, so this
    # is unreachable in well-typed code. Belt-and-suspenders for runtime
    # misuse (e.g. attribute-assigned backend).
    raise VLMUnavailableError(f"Unknown VLM backend: {backend_name!r}")
