"""Chunker package re-exports.

The ``code`` submodule lazy-imports ``tree-sitter-language-pack`` so
importing this package does not require the optional ``[code]`` extra.
``CodeChunker`` is re-exported here for ergonomics but raises an
``ImportError`` only on first use when the extra is missing.
"""

from .base import Chunker, ConversationChunker, MarkdownChunker, PassthroughChunker, TextChunk

__all__ = [
    "Chunker",
    "CodeChunker",
    "ConversationChunker",
    "MarkdownChunker",
    "PassthroughChunker",
    "TextChunk",
]


def __getattr__(name: str):
    """Lazy export of CodeChunker — avoids importing tree-sitter at package import time."""
    if name == "CodeChunker":
        from .code import CodeChunker  # noqa: PLC0415

        return CodeChunker
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
