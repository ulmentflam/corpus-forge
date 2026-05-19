"""Embedder registry for corpus-forge."""

from .base import Embedder
from .model2vec import Model2VecEmbedder
from .openai import OpenAIEmbedder
from .sentence_transformers import SentenceTransformersEmbedder


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
