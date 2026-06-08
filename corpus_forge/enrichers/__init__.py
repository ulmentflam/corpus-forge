"""Code-chunk enrichment subsystem — Phase H.

Public API:

- :class:`~corpus_forge.enrichers.base.CodeChunkEnrichment` /
  :class:`~corpus_forge.enrichers.base.CodeEnricher` /
  :class:`~corpus_forge.enrichers.base.NoopEnricher` — the seam.
- :class:`~corpus_forge.enrichers.base.EnricherError` and subclasses —
  operational failure hierarchy.
- :class:`~corpus_forge.enrichers.registry.EnricherRegistry` — ordered
  registry of enricher instances.
- :func:`get_active_enricher` — boot-time factory driven by
  :class:`corpus_forge.config.EnricherConfig`.

Heavy backends (Qwen local / remote — both pull in ``requests``) are
:mod:`importlib`-loaded so importing this package with the relevant
optional dependencies absent does not error.

**Cross-cutting principle**: every model client in corpus-forge
supports a configurable local-or-remote URL. Phase H exposes that via
TWO concrete backends (``QwenCoderLocal`` for local Ollama,
``QwenCoderRemote`` for any hosted Ollama / OpenAI-compat endpoint)
plus explicit ``local_url`` and ``remote_url`` config fields.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

from .base import (
    CodeChunkEnrichment,
    CodeEnricher,
    EnricherError,
    EnricherResponseError,
    EnricherTimeoutError,
    EnricherUnavailableError,
    NoopEnricher,
    _parse_enrichment_response,
)
from .registry import EnricherRegistry

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.config import Config

logger = logging.getLogger(__name__)


# Map enricher backend tag → (submodule, class_name). Mirrors the
# ``_CLASSIFIER_REGISTRY`` dispatcher in classifiers.__init__.
_ENRICHER_REGISTRY: dict[str, tuple[str, str]] = {
    "qwen-local": ("qwen_local", "QwenCoderLocal"),
    "qwen-remote": ("qwen_remote", "QwenCoderRemote"),
}


# Module-level singleton (mirrors `vlm.registry.registry`).
registry = EnricherRegistry()


def _load_class(submodule: str, class_name: str) -> type | None:
    """Lazy-load ``corpus_forge.enrichers.<submodule>.<class_name>``.

    Returns ``None`` if the submodule isn't importable (e.g. ``requests``
    missing). The factory turns that into a clean
    :class:`EnricherUnavailableError` at the call site.
    """
    try:
        mod = importlib.import_module(f"corpus_forge.enrichers.{submodule}")
    except ImportError as exc:
        logger.debug("enrichers submodule %s not available: %s", submodule, exc)
        return None
    return getattr(mod, class_name, None)


def get_active_enricher(config: Config) -> CodeEnricher:
    """Construct + register the enricher named by ``config.code_enricher``.

    Resolution order:

    - ``backend = "none"``  → :class:`NoopEnricher` (every operational
      call raises :class:`EnricherUnavailableError`).
    - ``backend = "local"`` → :class:`QwenCoderLocal` against
      ``code_enricher.local_url`` + ``code_enricher.local_model``.
    - ``backend = "remote"`` → :class:`QwenCoderRemote` against
      ``code_enricher.remote_url`` + ``code_enricher.remote_model``,
      configurable ``remote_api_shape``, API key resolved via
      :meth:`Config.resolve_code_enricher_api_key`. Raises
      :class:`EnricherUnavailableError` if the API key env var is set
      but resolves to an empty string AND the shape requires one
      (``"openai"`` always requires; ``"ollama"`` is tolerant — Bearer
      header just omitted).

    The constructed backend is registered in the module-level
    :data:`registry` so other call-sites can look it up by name.
    """
    cfg = config.code_enricher
    backend = cfg.backend

    if backend == "none":
        noop = NoopEnricher()
        registry.register(noop)
        return noop

    if backend == "local":
        cls = _load_class("qwen_local", "QwenCoderLocal")
        if cls is None:
            raise EnricherUnavailableError(
                "Local backend selected but `corpus_forge.enrichers.qwen_local` "
                "could not be imported — ensure `requests` is installed."
            )
        from corpus_forge.net import resolve_endpoint_for  # noqa: PLC0415

        instance = cls(
            model=cfg.local_model,
            llm_url=resolve_endpoint_for(str(cfg.local_url), config, default_scheme="http").rstrip(
                "/"
            ),
            timeout_s=cfg.timeout_s,
            temperature=cfg.temperature,
        )
        registry.register(instance)
        return instance

    if backend == "remote":
        cls = _load_class("qwen_remote", "QwenCoderRemote")
        if cls is None:
            raise EnricherUnavailableError(
                "Remote backend selected but `corpus_forge.enrichers.qwen_remote` "
                "could not be imported — ensure `requests` is installed."
            )
        api_key = config.resolve_code_enricher_api_key()
        from corpus_forge.net import resolve_endpoint_for  # noqa: PLC0415

        instance = cls(
            api_shape=cfg.remote_api_shape,
            model=cfg.remote_model,
            base_url=resolve_endpoint_for(
                str(cfg.remote_url), config, default_scheme="http"
            ).rstrip("/"),
            api_key=api_key,
            timeout_s=cfg.timeout_s,
            temperature=cfg.temperature,
        )
        registry.register(instance)
        return instance

    # pyrefly: Literal["local","remote","none"] is exhaustive.
    raise EnricherUnavailableError(f"Unknown code-enricher backend: {backend!r}")


__all__ = [
    "CodeChunkEnrichment",
    "CodeEnricher",
    "EnricherError",
    "EnricherRegistry",
    "EnricherResponseError",
    "EnricherTimeoutError",
    "EnricherUnavailableError",
    "NoopEnricher",
    "_parse_enrichment_response",
    "get_active_enricher",
    "registry",
]
