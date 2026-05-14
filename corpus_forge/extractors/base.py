"""Extractor protocol + the dataclass it returns.

Phase D / Wave 0 — D-01.

`Extractor` is the seam that lets corpus-forge ingest arbitrary file
formats. A concrete extractor reads a file from disk and returns an
:class:`ExtractedDocument` whose ``chunker_hint`` selects the per-document
chunker downstream (see ``corpus_forge.ingest.ChunkerDispatcher``).

This module is dependency-free on purpose: importing it must never pull
in heavy optional deps (Docling, pymupdf4llm, etc.). Concrete extractors
live in sibling modules and lazy-import their backends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

ChunkerHint = Literal["markdown", "code", "passthrough", "conversation"]


@dataclass
class ExtractedDocument:
    """A normalised document handed off to the chunker layer.

    Attributes:
        text: Document body in the form the downstream chunker expects.
            ``markdown`` / ``passthrough`` chunkers expect Markdown or
            plain text; ``code`` expects raw source.
        chunker_hint: Selects the chunker. One of ``"markdown"``,
            ``"code"``, ``"passthrough"``, ``"conversation"``.
        language: Optional language tag — used by ``CodeChunker`` to
            pick the tree-sitter grammar. ``None`` for prose extractors.
        metadata: Free-form dict copied onto ``RawDocument.metadata`` by
            the calling source. Reserved keys: ``chunker_hint``,
            ``language`` (set by the dispatcher, not the extractor).
        labels: ``[(namespace, value), ...]`` — propagated onto
            ``RawDocument.labels``.
    """

    text: str
    chunker_hint: ChunkerHint
    language: str | None = None
    metadata: dict = field(default_factory=dict)
    labels: list[tuple[str, str]] = field(default_factory=list)


@runtime_checkable
class Extractor(Protocol):
    """File-format extractor protocol.

    Concrete implementations declare which extensions they handle via
    :attr:`supported_extensions` and implement :meth:`extract`. Extensions
    are matched case-insensitively by :class:`ExtractorRegistry`.

    Extensionless files (``Makefile``, ``Dockerfile``, ``.gitignore``,
    ``.editorconfig``) are routed via the second-pass
    :attr:`supported_filenames` lookup performed by
    :class:`ExtractorRegistry` when the extension table misses (Wave 2).

    Implementations must be *cheap to import* — heavy backends
    (Docling, pymupdf4llm, tree-sitter) belong inside ``__init__`` or
    ``extract``, not at module top level. See
    ``corpus_forge/mcp/server.py`` for the established lazy-import
    pattern.
    """

    supported_extensions: tuple[str, ...]
    """Extensions handled by this extractor, including the leading dot."""

    supported_filenames: tuple[str, ...] = ()
    """Exact filenames (no extension) handled by this extractor.

    Consulted as a **second pass** by :class:`ExtractorRegistry` when the
    extension table misses — keeps the hot path extension-keyed for
    common cases while still routing ``Makefile`` / ``Dockerfile`` /
    dotfiles into ``CodeExtractor``. Defaults to ``()`` so existing
    extension-only extractors don't need to change.

    Both ``"Makefile"`` and ``"makefile"`` can be declared simultaneously
    — the registry stores filename declarations verbatim (case-sensitive
    on POSIX semantics).
    """

    def extract(self, path: Path) -> ExtractedDocument:
        """Read ``path`` and return a normalised :class:`ExtractedDocument`."""
        ...
