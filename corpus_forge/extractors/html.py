"""HTML extractor.

Phase D / Wave 1 — D-08.

Pipeline:

1. ``readability.Document(raw_html)`` extracts the main article body
   (strips ``<script>``, ``<style>``, nav/footer boilerplate, ads, …).
2. ``markdownify.markdownify(summary, heading_style="ATX",
   bullets="-")`` converts the cleaned HTML to Markdown.

Both backends are lazy-imported inside :meth:`HtmlExtractor.extract`
so the core install (no ``[multi-format]`` extra) stays light.
"""

from __future__ import annotations

from pathlib import Path

from .base import ExtractedDocument


class HtmlExtractor:
    """Reads ``.html`` / ``.htm`` / ``.xhtml`` → Markdown."""

    supported_extensions: tuple[str, ...] = (".html", ".htm", ".xhtml")

    def extract(self, path: Path) -> ExtractedDocument:
        from markdownify import markdownify  # noqa: PLC0415
        from readability import Document  # noqa: PLC0415

        raw = path.read_text(encoding="utf-8")
        readable = Document(raw)
        # ``summary()`` returns boilerplate-stripped HTML; ``short_title``
        # is readability's best guess at the article title.
        cleaned_html = readable.summary()
        title = readable.short_title() or path.stem

        text = markdownify(cleaned_html, heading_style="ATX", bullets="-")

        return ExtractedDocument(
            text=text,
            chunker_hint="markdown",
            language=None,
            metadata={"title": title, "extractor": "html"},
            labels=[("format", "html")],
        )
