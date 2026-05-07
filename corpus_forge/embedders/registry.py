"""Embedder registry for corpus-forge."""

from .base import Embedder
from .openai import OpenAIEmbedder
from .sentence_transformers import SentenceTransformersEmbedder


class EmbedderRegistry:
    """Registry for managing embedder instances."""

    def __init__(self):
        self._embedder_classes = {
            "sentence_transformers": SentenceTransformersEmbedder,
            "openai": OpenAIEmbedder,
        }
        self._instances: dict[str, Embedder] = {}

    def register(
        self, name: str, provider: str, model_id: str, dimension: int, **kwargs
    ) -> Embedder:
        """Register and create an embedder instance."""
        if provider not in self._embedder_classes:
            raise ValueError(f"Unknown embedder provider: {provider}")

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
