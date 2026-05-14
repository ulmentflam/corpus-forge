"""EPUB extractor.

Phase D / Wave 1 — D-09.

Strategy: ``ebooklib.epub.read_epub`` opens the archive, every
``ITEM_DOCUMENT`` (chapter / nav / etc.) is converted with
``markdownify`` and joined with a horizontal-rule separator so
downstream Markdown chunkers see explicit chapter boundaries.

License posture: ``ebooklib`` is AGPL-3.0 (boundary lives in
``[multi-format]``). Both backends are lazy-imported inside ``extract``.
"""

from __future__ import annotations

from pathlib import Path

from .base import ExtractedDocument

# Inter-chapter separator. Renders as a horizontal rule in Markdown so
# the chunker has an obvious boundary to break on.
_CHAPTER_SEPARATOR = "\n\n---\n\n"


def _first_dc(metadata_list: list) -> str | None:
    """Return the first ``("value", attrs)`` tuple's value, or None."""
    if not metadata_list:
        return None
    first = metadata_list[0]
    if isinstance(first, tuple) and first:
        value = first[0]
        if value:
            return str(value)
    return None


class EpubExtractor:
    """Reads ``.epub`` → joined-chapter Markdown."""

    supported_extensions: tuple[str, ...] = (".epub",)

    def extract(self, path: Path) -> ExtractedDocument:
        import ebooklib  # noqa: PLC0415
        from ebooklib import epub  # noqa: PLC0415
        from markdownify import markdownify  # noqa: PLC0415

        book = epub.read_epub(str(path))

        title = _first_dc(book.get_metadata("DC", "title")) or path.stem
        author = _first_dc(book.get_metadata("DC", "creator"))

        chapters: list[str] = []
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            raw = item.get_content()
            # ebooklib hands us bytes — decode before markdownify.
            html = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
            md = markdownify(html, heading_style="ATX", bullets="-")
            chapters.append(md)

        text = _CHAPTER_SEPARATOR.join(chapters)

        return ExtractedDocument(
            text=text,
            chunker_hint="markdown",
            language=None,
            metadata={
                "title": title,
                "author": author,
                "chapter_count": len(chapters),
                "extractor": "epub",
            },
            labels=[("format", "epub")],
        )
