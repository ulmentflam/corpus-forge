"""Plain text extractor.

Phase D / Wave 0 — D-03 (half 2). Pure stdlib.

Handles ``.txt .log .rst .org .tex .adoc`` files. Reads verbatim and
sets ``chunker_hint = "passthrough"`` so the downstream
:class:`PassthroughChunker` handles segmentation. Title is the first
non-empty line stripped of leading markup chars (``# = * \\``) or falls
back to the file stem.
"""

from __future__ import annotations

from pathlib import Path

from .base import ExtractedDocument

# Leading characters commonly used by lightweight markup languages to
# decorate headings — strip them when synthesizing a title.
_LEADING_MARKUP = "#=*\\ \t"


def _first_titleish_line(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        cleaned = stripped.lstrip(_LEADING_MARKUP).strip()
        return cleaned or stripped
    return None


class PlainTextExtractor:
    """Reads plaintext / lightweight-markup files unchanged."""

    supported_extensions: tuple[str, ...] = (
        ".txt",
        ".log",
        ".rst",
        ".org",
        ".tex",
        ".adoc",
    )

    def extract(self, path: Path) -> ExtractedDocument:
        text = path.read_text(encoding="utf-8")
        title = _first_titleish_line(text) or path.stem
        return ExtractedDocument(
            text=text,
            chunker_hint="passthrough",
            language=None,
            metadata={"title": title},
            labels=[],
        )
