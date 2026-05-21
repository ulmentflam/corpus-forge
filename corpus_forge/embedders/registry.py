"""Embedder registry for corpus-forge."""

from typing import Any

from .base import Embedder
from .model2vec import Model2VecEmbedder
from .openai import OpenAIEmbedder
from .sentence_transformers import SentenceTransformersEmbedder


def _per_provider_extras(embedder_config) -> dict[str, Any]:
    """Build the provider-specific kwargs dict for ``EmbedderRegistry.register``.

    Different providers accept different optional kwargs:

    - ``sentence_transformers``: ``device`` (auto-resolves
      ``"auto"`` to mps / cuda / cpu via ``resolve_device``); rejects
      ``api_key_env`` and ``base_url`` (the OpenAI SDK args).
    - ``openai``: ``api_key_env`` + optional ``base_url``; rejects
      ``device`` — the HTTP transport has no local-accelerator
      concept and passing it raised ``TypeError`` on every first-run
      ingest against Ollama's OpenAI-compatible endpoint.
    - ``model2vec``: CPU-only static embeddings; same "no device"
      story as openai.

    Pulled into a single helper so the three call sites
    (``corpus_forge.ingest.get_active_embedders``,
    ``corpus_forge.cli._build_retriever_for_eval``,
    ``corpus_forge.admin.embedder.run_embedder_smoke``) can't drift
    apart again — they were all reimplementing this in subtly
    different ways and ``_build_retriever_for_eval`` in particular
    was forgetting to forward ``base_url`` + ``api_key_env``, which
    broke every dense ``corpus-forge search`` against a local
    Ollama OpenAI-compat endpoint.
    """
    extras: dict[str, Any] = {
        "normalized": getattr(embedder_config, "normalize", True),
        "distance": getattr(embedder_config, "distance", "cosine"),
        "batch_size": getattr(embedder_config, "batch_size", 32),
    }
    provider = getattr(embedder_config, "provider", "")
    if provider == "sentence_transformers":
        extras["device"] = getattr(embedder_config, "device", "auto")
    elif provider == "openai":
        extras["api_key_env"] = getattr(embedder_config, "api_key_env", "OPENAI_API_KEY")
        base_url = getattr(embedder_config, "base_url", None)
        if base_url is not None:
            # ``base_url`` may be a pydantic AnyHttpUrl — cast to str
            # and strip the trailing slash so the OpenAI SDK accepts
            # it unchanged.
            extras["base_url"] = str(base_url).rstrip("/")
    # ``model2vec`` and any future CPU-only / static providers fall
    # through with just the common kwargs.
    return extras


def register_from_config(registry: "EmbedderRegistry", embedder_config) -> Embedder:
    """Register an embedder using the per-provider kwarg policy.

    Every call site that builds an :class:`Embedder` from a config
    object should route through here so the provider-specific
    kwargs don't drift between ingest, search, and admin paths.
    """
    return registry.register(
        name=embedder_config.name,
        provider=embedder_config.provider,
        model_id=embedder_config.model_id,
        dimension=embedder_config.dimension,
        **_per_provider_extras(embedder_config),
    )


class EmbedderRegistry:
    """Registry for managing embedder instances."""

    def __init__(self):
        self._embedder_classes = {
            "sentence_transformers": SentenceTransformersEmbedder,
            "openai": OpenAIEmbedder,
            # Phase N Wave 3 — static-tier fast embedder (model2vec /
            # potion-code-16M).  Optional ``[fast-tier]`` extra; the
            # provider module is importable without the extra (encode
            # raises ImportError lazily).
            "model2vec": Model2VecEmbedder,
        }
        self._instances: dict[str, Embedder] = {}

    def register(
        self, name: str, provider: str, model_id: str, dimension: int, **kwargs
    ) -> Embedder:
        """Register and create an embedder instance.

        If an embedder with the same name already exists, its attributes are
        updated in-place and the same object reference is returned.
        """
        if provider not in self._embedder_classes:
            raise ValueError(f"Unknown embedder provider: {provider}")

        if name in self._instances:
            existing = self._instances[name]
            existing.provider = provider
            existing.model_id = model_id
            existing.dimension = dimension
            for key, value in kwargs.items():
                setattr(existing, key, value)
            return existing

        embedder_class = self._embedder_classes[provider]
        embedder = embedder_class(name=name, model_id=model_id, dimension=dimension, **kwargs)

        self._instances[name] = embedder
        return embedder

    def get(self, name: str) -> Embedder | None:
        """Get an embedder instance by name."""
        return self._instances.get(name)

    def list_names(self) -> list[str]:
        """List all registered embedder names."""
        return list(self._instances.keys())

    def clear(self):
        """Clear all registered embedders."""
        self._instances.clear()


# Global registry instance
registry = EmbedderRegistry()
