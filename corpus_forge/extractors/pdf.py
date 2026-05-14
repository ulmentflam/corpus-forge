"""Digital PDF extractor.

Phase D / Wave 1 — D-07.

Wraps ``pymupdf4llm.to_markdown`` for the digital-text-layer path of
PDF extraction. P0 only — Wave 4 (E-05) layers VLM escalation on top
by upgrading this same module.

License posture: ``pymupdf4llm`` is AGPL-3.0 (user-approved for this
milestone). It is gated behind the ``[multi-format]`` optional extra so
the core install stays Apache-2.0. The backend is **lazy-imported**
inside :meth:`PdfDigitalExtractor.extract` so importing this module on
a core install does not raise — only invoking ``extract`` does.

Sparse-text-layer signal
------------------------

If the extracted markdown averages less than ``_SPARSE_CHARS_PER_PAGE``
characters per page, the extractor sets
``metadata["sparse_text_layer"] = True``. Wave 5 (E-05) uses this
signal to escalate to OCR for scanned PDFs.
"""

from __future__ import annotations

from pathlib import Path

from .base import ExtractedDocument

# Average chars-per-page threshold below which we flag the text layer
# as sparse (so the OCR escalation path in E-05 can pick it up).
_SPARSE_CHARS_PER_PAGE = 100


class PdfDigitalExtractor:
    """Digital-only PDF extractor (``.pdf`` → markdown)."""

    supported_extensions: tuple[str, ...] = (".pdf",)

    def extract(self, path: Path) -> ExtractedDocument:
        # Lazy imports — keep module-import cheap on core installs without
        # the [multi-format] extra. Mirrors the pattern in
        # ``corpus_forge/mcp/server.py``.
        import pymupdf  # noqa: PLC0415

        # pymupdf4llm 1.27+ ships a `_use_layout=True` default that routes
        # `to_markdown` through `pymupdf-layout`, which falls back to
        # Tesseract OCR when it can't recognise the page layout. That is
        # *exactly* the path Wave 5 (E-05) takes over via the VLM — for
        # the digital-only path we want the bare text-layer reader. Reach
        # past the layout switch and call the rag helper directly.
        from pymupdf4llm.helpers.pymupdf_rag import to_markdown  # noqa: PLC0415

        # Open once to get the page count; to_markdown re-opens internally
        # which is fine — pymupdf docs are cheap to construct.
        with pymupdf.open(str(path)) as doc:
            page_count = int(doc.page_count)

        text = to_markdown(str(path))

        metadata: dict = {
            "page_count": page_count,
            "extractor": "pdf_digital",
        }

        # Sparse text-layer detector — average chars/page below threshold
        # signals a scanned PDF that Wave 5 (E-05) will escalate to OCR.
        if page_count > 0:
            avg_chars = len(text) / page_count
            if avg_chars < _SPARSE_CHARS_PER_PAGE:
                metadata["sparse_text_layer"] = True

        return ExtractedDocument(
            text=text,
            chunker_hint="markdown",
            language=None,
            metadata=metadata,
            labels=[("format", "pdf"), ("extractor", "pymupdf4llm")],
        )
