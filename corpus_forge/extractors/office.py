"""Office (DOCX / PPTX / XLSX) extractor via Docling.

Phase D / Wave 1 — D-10.

Strategy: Docling's :class:`DocumentConverter` with the default
pipeline. No VLM — that's the P1 escalation (Wave 5+). One converter
construction per :meth:`OfficeExtractor.extract` call; Docling is
designed to be cheap to instantiate for office formats (no
model loading required when neither OCR nor table-structure is
enabled).

License posture: Docling itself is MIT but pulls ``torch`` and
``onnxruntime`` transitively. The whole ``[multi-format]`` extra is the
AGPL boundary anyway (pymupdf4llm, ebooklib).

``num_pages`` on :class:`DoclingDocument` is a **method**, not an
attribute — for word documents it returns 0, for slides / sheets it
returns the slide / sheet count. We surface whatever value the method
returns; the prompt's ``getattr(..., None)`` shape is preserved by
guarding the call.
"""

from __future__ import annotations

from pathlib import Path

from .base import ExtractedDocument

# Map file extension → (format-label, label-namespace-value).
_FORMAT_BY_EXT: dict[str, str] = {
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
}


def _num_pages(document: object) -> int | None:
    """Return ``document.num_pages()`` if callable, else None.

    Docling's DoclingDocument exposes ``num_pages`` as a bound method
    that returns 0 for paragraph-flow docs (DOCX) and the slide / sheet
    count for PPTX / XLSX. Older Docling builds expose it as a property
    — handle both shapes defensively.
    """
    attr = getattr(document, "num_pages", None)
    if attr is None:
        return None
    try:
        value = attr() if callable(attr) else attr
    except Exception:  # pragma: no cover — defensive
        return None
    if isinstance(value, int):
        return value
    return None


class OfficeExtractor:
    """Reads ``.docx`` / ``.pptx`` / ``.xlsx`` → Markdown via Docling."""

    supported_extensions: tuple[str, ...] = (".docx", ".pptx", ".xlsx")

    def extract(self, path: Path) -> ExtractedDocument:
        from docling.document_converter import DocumentConverter  # noqa: PLC0415

        converter = DocumentConverter()
        result = converter.convert(str(path))
        text = result.document.export_to_markdown()

        ext = path.suffix.lower()
        fmt = _FORMAT_BY_EXT.get(ext, ext.lstrip("."))

        return ExtractedDocument(
            text=text,
            chunker_hint="markdown",
            language=None,
            metadata={
                "extractor": "docling",
                "format": fmt,
                "page_count": _num_pages(result.document),
            },
            labels=[("format", fmt), ("extractor", "docling")],
        )
