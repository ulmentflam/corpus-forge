"""Chunker package re-exports.

The ``code`` submodule lazy-imports ``tree-sitter-language-pack`` so
importing this package does not require the optional ``[code]`` extra.
``CodeChunker`` is re-exported here for ergonomics but raises an
``ImportError`` only on first use when the extra is missing.
"""

from .base import Chunker, ConversationChunker, MarkdownChunker, PassthroughChunker, TextChunk
from .hard_max import enforce_chunk_hard_max

__all__ = [
    "CDCChunker",
    "Chunker",
    "CodeChunker",
    "ConversationChunker",
    "MarkdownChunker",
    "PassthroughChunker",
    "TextChunk",
    "enforce_chunk_hard_max",
]


def __getattr__(name: str):
    """Lazy exports — keep optional-extra dependencies out of import time.

    ``CodeChunker`` lazy-imports tree-sitter (Phase D ``[code]`` extra).
    ``CDCChunker`` lazy-imports the ``fastcdc`` Python package (Phase F
    ``[multi-format]`` extra). Surfacing them via ``__getattr__`` lets
    ``from corpus_forge.chunkers import CDCChunker`` resolve only when the
    caller actually needs CDC chunking.
    """
    if name == "CodeChunker":
        from .code import CodeChunker  # noqa: PLC0415

        return CodeChunker
    if name == "CDCChunker":
        from .cdc import CDCChunker  # noqa: PLC0415

        return CDCChunker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
