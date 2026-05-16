"""Phase G — Multi-modal embedder protocol + exceptions.

The :class:`MultiModalEmbedder` is a *new* plug-in surface alongside
the existing text :class:`~corpus_forge.embedders.base.Embedder`. It
shares the project's storage pattern (per-embedder dynamic table named
``image_embeddings_<name>`` mirroring ``embeddings_<name>``) but
distinct ingest / retrieval paths.

Why a new protocol rather than retrofitting :class:`Embedder`:

- The text embedder takes ``Sequence[str]`` and returns ``np.ndarray``;
  multi-modal backends accept either ``list[str]`` OR ``list[bytes]``.
- The dual-write embedding tables (text vs. image) make this a clean
  separation — the existing text embed pipeline keeps working untouched.

Implementations:

- :class:`~corpus_forge.embedders.clip_local.ClipLocalEmbedder` —
  ``sentence-transformers`` with ``clip-ViT-B-32`` (default, 512 d) or
  ``jina-clip-v2`` (1024 d, multilingual).
- :class:`~corpus_forge.embedders.clip_remote.ClipRemoteEmbedder` —
  OpenAI-compatible ``/v1/embeddings`` endpoint.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# ── Exceptions ──────────────────────────────────────────────────────────


class MultiModalEmbedderError(Exception):
    """Base for every multi-modal embedder operational failure."""


class MultiModalUnavailableError(MultiModalEmbedderError):
    """The backend cannot be reached or is not configured."""


class MultiModalTimeoutError(MultiModalEmbedderError):
    """The backend was reachable but exceeded the configured timeout."""


class MultiModalResponseError(MultiModalEmbedderError):
    """The backend returned a malformed or error response."""


# ── Protocol ────────────────────────────────────────────────────────────


@runtime_checkable
class MultiModalEmbedder(Protocol):
    """Shared text+image embedding space.

    Concrete backends produce vectors of the same dimensionality from
    both modalities so cosine similarity is meaningful across them.
    The default model (``clip-ViT-B-32``) emits 512-d vectors; swap to
    ``jina-clip-v2`` for 1024-d multilingual.

    Implementations must be cheap to import — heavy backends
    (sentence-transformers / requests) belong inside ``__init__`` or
    inside the encode methods, not at module top level.
    """

    name: str
    """Stable identifier. Used as the suffix of the dynamic
    ``image_embeddings_<name>`` table."""

    dimension: int
    """Vector dimensionality. Text and image vectors share the same
    dimension — that's the whole point of multi-modal embedding."""

    def encode_text(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of text strings into shared-space vectors."""
        ...

    def encode_image(self, images: list[bytes]) -> list[list[float]]:
        """Encode a batch of image bytes into shared-space vectors."""
        ...

    def warmup(self) -> None:
        """Cheap health-check / model preload."""
        ...
