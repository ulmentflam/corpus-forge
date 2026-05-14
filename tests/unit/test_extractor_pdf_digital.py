"""Unit tests for D-07: PdfDigitalExtractor.

Wraps ``pymupdf4llm.to_markdown`` for the digital-text-layer path of
PDF extraction. P0 only — Wave 4 (E-05) layers VLM escalation on top
by upgrading this same module.

Synthetic PDFs are built inline with PyMuPDF in ``tmp_path`` so the
test file is self-contained.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from corpus_forge.extractors import ExtractedDocument, Extractor
from corpus_forge.extractors.pdf import PdfDigitalExtractor

# ── Fixture helpers ──────────────────────────────────────────────────


def _build_pdf(tmp_path: Path, pages: list[str], name: str = "doc.pdf") -> Path:
    """Build a tiny PDF in ``tmp_path`` with one text block per page.

    Each entry of ``pages`` becomes a single page; the text is inserted
    near the top-left so pymupdf4llm's text extraction can find it.
    """
    import pymupdf  # type: ignore[import-not-found]

    doc = pymupdf.open()
    for body in pages:
        page = doc.new_page()
        page.insert_text((72, 72), body)
    target = tmp_path / name
    doc.save(str(target))
    doc.close()
    return target


def _build_empty_pdf(tmp_path: Path, n_pages: int = 1, name: str = "blank.pdf") -> Path:
    """Build a PDF with ``n_pages`` blank pages — no text layer at all."""
    import pymupdf

    doc = pymupdf.open()
    for _ in range(n_pages):
        doc.new_page()
    target = tmp_path / name
    doc.save(str(target))
    doc.close()
    return target


# ── Tests ────────────────────────────────────────────────────────────


def test_extractor_protocol_conformance():
    ex: Extractor = PdfDigitalExtractor()
    assert isinstance(ex.supported_extensions, tuple)


def test_supported_extensions():
    ex = PdfDigitalExtractor()
    assert set(ex.supported_extensions) == {".pdf"}


def test_extract_returns_extracted_document(tmp_path: Path):
    p = _build_pdf(tmp_path, ["Hello world. " * 20])
    doc = PdfDigitalExtractor().extract(p)
    assert isinstance(doc, ExtractedDocument)
    assert doc.chunker_hint == "markdown"


def test_extract_text_is_non_empty(tmp_path: Path):
    p = _build_pdf(tmp_path, ["This is the body of the only page. " * 10])
    doc = PdfDigitalExtractor().extract(p)
    assert doc.text.strip()
    # Some content from the text layer must round-trip into the markdown.
    assert "body" in doc.text


def test_extract_metadata_page_count(tmp_path: Path):
    pages = ["First page " * 30, "Second page " * 30, "Third page " * 30]
    p = _build_pdf(tmp_path, pages)
    doc = PdfDigitalExtractor().extract(p)
    assert doc.metadata.get("page_count") == 3


def test_extract_metadata_extractor_tag(tmp_path: Path):
    p = _build_pdf(tmp_path, ["body " * 30])
    doc = PdfDigitalExtractor().extract(p)
    assert doc.metadata.get("extractor") == "pdf_digital"


def test_extract_labels(tmp_path: Path):
    p = _build_pdf(tmp_path, ["body " * 30])
    doc = PdfDigitalExtractor().extract(p)
    label_set = {(ns, val) for ns, val in doc.labels}
    assert ("format", "pdf") in label_set
    assert ("extractor", "pymupdf4llm") in label_set


def test_extract_sparse_text_layer_flag_for_blank_pdf(tmp_path: Path):
    """A blank PDF has < 100 chars/page on average → ``sparse_text_layer``."""
    p = _build_empty_pdf(tmp_path, n_pages=2)
    doc = PdfDigitalExtractor().extract(p)
    assert doc.metadata.get("sparse_text_layer") is True


def test_extract_dense_pdf_omits_sparse_flag(tmp_path: Path):
    """A dense PDF should NOT mark itself as sparse."""
    # 250+ chars per page on each of 2 pages.
    body = "lorem ipsum dolor sit amet consectetur adipiscing elit " * 8
    p = _build_pdf(tmp_path, [body, body])
    doc = PdfDigitalExtractor().extract(p)
    # Either absent or explicitly False — both are acceptable shapes.
    assert not doc.metadata.get("sparse_text_layer", False)


def test_extract_language_is_none(tmp_path: Path):
    p = _build_pdf(tmp_path, ["body " * 30])
    doc = PdfDigitalExtractor().extract(p)
    assert doc.language is None


def test_extract_lazy_import_does_not_load_on_module_import():
    """Importing the extractor module must NOT import pymupdf4llm.

    Run in an isolated subprocess so we don't poison ``sys.modules`` for
    later tests (the registry tests assert class-identity equality with
    the already-imported ``PdfDigitalExtractor``).
    """
    import subprocess
    import sys

    script = (
        "import sys; "
        "import corpus_forge.extractors.pdf as m; "
        "assert 'pymupdf4llm' not in sys.modules, sorted(sys.modules); "
        "assert 'pymupdf' not in sys.modules, sorted(sys.modules); "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_registry_wires_pdf_extractor(tmp_path: Path):
    """``register_default_extractors`` must pick up the new module."""
    from corpus_forge.extractors import register_default_extractors

    reg = register_default_extractors(config=None)
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4\n%EOF\n")
    extractor = reg.get_for(p)
    assert extractor is not None
    assert isinstance(extractor, PdfDigitalExtractor)


def test_extract_handles_path_with_spaces(tmp_path: Path):
    p = _build_pdf(tmp_path, ["body " * 30], name="my doc.pdf")
    doc = PdfDigitalExtractor().extract(p)
    assert doc.text.strip()


def test_extract_metadata_page_count_int(tmp_path: Path):
    """``page_count`` must be an int (not numpy int / str)."""
    p = _build_pdf(tmp_path, ["body " * 30, "more " * 30])
    doc = PdfDigitalExtractor().extract(p)
    pc = doc.metadata.get("page_count")
    assert isinstance(pc, int)
    assert pc == 2


@pytest.mark.parametrize(
    ("body", "expect_sparse"),
    [
        # ~10 chars/page — clearly below the 100-char threshold.
        ("short", True),
        # Multi-line dense body so each page renders well over 100 chars
        # of extracted text. Use insert_text's newline support.
        (
            "lorem ipsum dolor sit amet\n"
            "consectetur adipiscing elit\n"
            "sed do eiusmod tempor incididunt\n"
            "ut labore et dolore magna aliqua\n"
            "ut enim ad minim veniam\n",
            False,
        ),
    ],
)
def test_sparse_flag_threshold(tmp_path: Path, body: str, expect_sparse: bool):
    """Threshold = 100 chars/page average; below → sparse, above → not."""
    p = _build_pdf(tmp_path, [body, body])
    doc = PdfDigitalExtractor().extract(p)
    if expect_sparse:
        assert doc.metadata.get("sparse_text_layer") is True
    else:
        assert not doc.metadata.get("sparse_text_layer", False)
