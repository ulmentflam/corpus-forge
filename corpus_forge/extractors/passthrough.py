"""Passthrough Markdown extractor.

Phase D / Wave 0 — D-03 (half 1). Pure stdlib.

Handles ``.md`` and ``.markdown`` files. The file content is preserved
verbatim and the downstream chunker is :class:`MarkdownChunker`. Title
is taken from the first markdown heading (``# heading`` of any level) or
falls back to the file stem.
"""

from __future__ import annotations

from pathlib import Path

from .base import ExtractedDocument


def _first_heading(text: str) -> str | None:
    """Return the first markdown heading text (any level), or None."""
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip()
            if heading:
                return heading
    return None


class PassthroughMarkdownExtractor:
    """Reads ``.md`` / ``.markdown`` files unchanged."""

    supported_extensions: tuple[str, ...] = (".md", ".markdown")

    def extract(self, path: Path) -> ExtractedDocument:
        text = path.read_text(encoding="utf-8")
        title = _first_heading(text) or path.stem
        return ExtractedDocument(
            text=text,
            chunker_hint="markdown",
            language=None,
            metadata={"title": title},
            labels=[],
        )
