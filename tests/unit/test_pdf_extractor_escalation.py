"""Unit tests for E-05: PdfDigitalExtractor OCR escalation.

Wave 5 (P1 OCR integration). Builds on the Wave 1 D-07 digital-only
PdfDigitalExtractor by layering a VLM-backed Tier 2 OCR fallback on top.

Test strategy
-------------
- All HTTP is mocked. No live Ollama / Mistral calls.
- ``pdf2image.convert_from_path`` is patched at the *call site* via
  ``mocker.patch("corpus_forge.extractors.pdf.pdf2image", new=...)`` so
  the lazy-import inside ``extract`` resolves to a stub.
- The VLM is a ``unittest.mock.Mock(spec=VLMBackend)`` so signature
  drift in the Protocol fails the test loudly.
- Synthetic PDFs are built inline with PyMuPDF in ``tmp_path`` (same
  helper pattern as ``test_extractor_pdf_digital.py``).
"""

from __future__ import annotations

import logging
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

from corpus_forge.extractors import ExtractedDocument
from corpus_forge.extractors.pdf import PdfDigitalExtractor
from corpus_forge.vlm.base import (
    NoopVLM,
    VLMBackend,
    VLMResponseError,
    VLMTimeoutError,
    VLMUnavailableError,
)

# ── Fixture helpers ──────────────────────────────────────────────────


def _build_pdf(tmp_path: Path, pages: list[str], name: str = "doc.pdf") -> Path:
    """Build a tiny PDF with one text block per page."""
    import pymupdf  # type: ignore[import-not-found]

    doc = pymupdf.open()
    for body in pages:
        page = doc.new_page()
        page.insert_text((72, 72), body)
    target = tmp_path / name
    doc.save(str(target))
    doc.close()
    return target


def _build_blank_pdf(tmp_path: Path, n_pages: int = 1, name: str = "blank.pdf") -> Path:
    """Build a PDF with ``n_pages`` blank pages — no text layer."""
    import pymupdf

    doc = pymupdf.open()
    for _ in range(n_pages):
        doc.new_page()
    target = tmp_path / name
    doc.save(str(target))
    doc.close()
    return target


def _stub_pdf2image_module(images: list[Image.Image]) -> types.ModuleType:
    """Return a stub `pdf2image` module exposing the API surface we need.

    Includes:
    - ``convert_from_path(path, dpi=..., fmt=...)`` returning the given
      list of PIL.Image instances.
    - ``PDFInfoNotInstalledError`` exception class (for the poppler-missing
      test which substitutes a side_effect).
    """
    mod = types.ModuleType("pdf2image")
    mod.convert_from_path = MagicMock(return_value=images)  # type: ignore[attr-defined]

    # Real pdf2image ships an `exceptions` submodule with PDFInfoNotInstalledError.
    # Mirror the public surface; the extractor imports it via
    # `pdf2image.exceptions.PDFInfoNotInstalledError` for clean error mapping.
    class _PDFInfoNotInstalledError(Exception):
        pass

    exc_mod = types.ModuleType("pdf2image.exceptions")
    exc_mod.PDFInfoNotInstalledError = _PDFInfoNotInstalledError  # type: ignore[attr-defined]
    mod.exceptions = exc_mod  # type: ignore[attr-defined]
    return mod


def _make_vlm(
    name: str = "ollama",
    *,
    extract_returns: list[str] | None = None,
    extract_raises: list[BaseException | None] | None = None,
    model: str = "qwen2.5vl:7b",
) -> MagicMock:
    """Build a `Mock(spec=VLMBackend)` with canned per-page responses.

    Either ``extract_returns`` (list of per-page markdown strings) or
    ``extract_raises`` (list of exceptions / Nones — None means "use the
    canned return at the same index") is honoured.
    """
    vlm = MagicMock(spec=VLMBackend)
    vlm.name = name
    vlm.model = model

    if extract_raises is not None:
        # Side-effect with per-call exception or per-call value.
        # ``extract_returns`` may be longer than ``extract_raises``; we
        # walk both with a counter.
        returns = extract_returns or []

        def _side_effect(image: bytes, *, page_number: int) -> str:
            i = page_number - 1
            exc = extract_raises[i] if i < len(extract_raises) else None
            if exc is not None:
                raise exc
            return returns[i] if i < len(returns) else f"page {page_number} markdown"

        vlm.extract_page.side_effect = _side_effect
    elif extract_returns is not None:
        vlm.extract_page.side_effect = list(extract_returns)
    else:
        vlm.extract_page.return_value = "# OCR page\n\nfallback markdown"

    return vlm


# ── Constructor injection ────────────────────────────────────────────


def test_constructor_accepts_optional_vlm_arg() -> None:
    """Constructor accepts ``vlm=None`` (default) and a VLMBackend instance."""
    ex_default = PdfDigitalExtractor()
    assert ex_default.vlm is None

    vlm = _make_vlm()
    ex = PdfDigitalExtractor(vlm=vlm)
    assert ex.vlm is vlm


def test_constructor_accepts_ocr_config_kwargs() -> None:
    """ocr_enabled / min_chars_per_page / dpi knobs land on the instance."""
    vlm = _make_vlm()
    ex = PdfDigitalExtractor(
        vlm=vlm,
        ocr_enabled=False,
        min_chars_per_page=42,
        ocr_dpi=300,
    )
    assert ex.ocr_enabled is False
    assert ex.min_chars_per_page == 42
    assert ex.ocr_dpi == 300


# ── Tier 1 (no escalation) ───────────────────────────────────────────


def test_dense_pdf_no_escalation_when_vlm_present(tmp_path: Path) -> None:
    """Dense text layer ⇒ Tier 1 only, even when a VLM is wired in."""
    body = "lorem ipsum dolor sit amet consectetur adipiscing elit " * 8
    pdf = _build_pdf(tmp_path, [body, body])

    vlm = _make_vlm()
    doc = PdfDigitalExtractor(vlm=vlm).extract(pdf)

    assert isinstance(doc, ExtractedDocument)
    assert doc.metadata.get("tier") == "digital"
    assert doc.metadata.get("sparse_text_layer") is not True
    assert vlm.extract_page.called is False
    label_set = {(ns, val) for ns, val in doc.labels}
    assert not any(ns == "ocr" for ns, _val in label_set)


def test_tier1_no_vlm_arg(tmp_path: Path) -> None:
    """vlm=None ⇒ no escalation regardless of sparse-text signal."""
    pdf = _build_blank_pdf(tmp_path, n_pages=2)
    doc = PdfDigitalExtractor().extract(pdf)
    assert doc.metadata.get("tier") == "digital"
    assert doc.metadata.get("sparse_text_layer") is True
    assert not any(ns == "ocr" for ns, _val in doc.labels)


def test_tier1_with_noop_vlm(tmp_path: Path) -> None:
    """NoopVLM short-circuits silently (Option 1 from the open question)."""
    pdf = _build_blank_pdf(tmp_path, n_pages=2)
    ex = PdfDigitalExtractor(vlm=NoopVLM())
    doc = ex.extract(pdf)
    assert doc.metadata.get("tier") == "digital"
    # No escalation-attempt marker because the short-circuit is *silent*.
    assert "ocr_escalation_attempted" not in doc.metadata
    assert "ocr_escalation_failed_reason" not in doc.metadata


def test_ocr_enabled_false_short_circuits(tmp_path: Path) -> None:
    """ocr_enabled=False kills escalation even with sparse text + real VLM."""
    pdf = _build_blank_pdf(tmp_path, n_pages=2)
    vlm = _make_vlm()
    ex = PdfDigitalExtractor(vlm=vlm, ocr_enabled=False)
    doc = ex.extract(pdf)
    assert doc.metadata.get("tier") == "digital"
    assert vlm.extract_page.called is False


# ── Tier 2 (escalation) ──────────────────────────────────────────────


def test_tier2_escalation_happy_path(tmp_path: Path, monkeypatch) -> None:
    """Sparse PDF + real VLM ⇒ rasterize, call VLM per page, concatenate."""
    pdf = _build_blank_pdf(tmp_path, n_pages=3)

    images = [Image.new("RGB", (10, 10), "white") for _ in range(3)]
    stub = _stub_pdf2image_module(images)
    monkeypatch.setattr("corpus_forge.extractors.pdf.pdf2image", stub, raising=False)

    vlm = _make_vlm(
        name="ollama",
        extract_returns=["# Page 1\n\nalpha", "# Page 2\n\nbeta", "# Page 3\n\ngamma"],
        model="qwen2.5vl:7b",
    )
    ex = PdfDigitalExtractor(vlm=vlm)
    doc = ex.extract(pdf)

    # Body is concatenated with the separator.
    assert "# Page 1" in doc.text
    assert "# Page 2" in doc.text
    assert "# Page 3" in doc.text
    assert "\n\n---\n\n" in doc.text

    assert doc.metadata.get("tier") == "ocr_escalated"
    assert doc.metadata.get("pages_ocr_count") == 3
    assert doc.metadata.get("ocr_backend") == "ollama"
    assert doc.metadata.get("ocr_model") == "qwen2.5vl:7b"

    label_set = {(ns, val) for ns, val in doc.labels}
    assert ("ocr", "ollama") in label_set
    assert ("ocr_model", "qwen2.5vl:7b") in label_set
    # The original format/extractor labels remain.
    assert ("format", "pdf") in label_set
    assert ("extractor", "pymupdf4llm") in label_set

    # VLM called once per page with page_number=1..N.
    assert vlm.extract_page.call_count == 3
    seen_pages = sorted(call.kwargs.get("page_number") for call in vlm.extract_page.call_args_list)
    assert seen_pages == [1, 2, 3]

    # pdf2image was called with the expected default DPI.
    stub.convert_from_path.assert_called_once()
    call = stub.convert_from_path.call_args
    assert call.kwargs.get("dpi") == 200
    assert call.kwargs.get("fmt") in {"png", "PNG"}


def test_tier2_dpi_knob_honoured(tmp_path: Path, monkeypatch) -> None:
    """ocr_dpi=300 ⇒ pdf2image.convert_from_path(dpi=300)."""
    pdf = _build_blank_pdf(tmp_path, n_pages=1)
    images = [Image.new("RGB", (10, 10), "white")]
    stub = _stub_pdf2image_module(images)
    monkeypatch.setattr("corpus_forge.extractors.pdf.pdf2image", stub, raising=False)

    vlm = _make_vlm(extract_returns=["ok"])
    PdfDigitalExtractor(vlm=vlm, ocr_dpi=300).extract(pdf)

    call = stub.convert_from_path.call_args
    assert call.kwargs.get("dpi") == 300


def test_tier2_min_chars_per_page_threshold(tmp_path: Path, monkeypatch) -> None:
    """min_chars_per_page=10 ⇒ a tiny-text PDF doesn't escalate."""
    # ~6 chars/page, but with min_chars_per_page=5 it shouldn't be sparse.
    pdf = _build_pdf(tmp_path, ["hi", "hi"])
    vlm = _make_vlm()
    # Even though the text is sparse by the default threshold (100),
    # we set it below the per-page char count so escalation does NOT fire.
    doc = PdfDigitalExtractor(vlm=vlm, min_chars_per_page=1).extract(pdf)
    assert doc.metadata.get("tier") == "digital"
    assert vlm.extract_page.called is False


def test_tier2_min_chars_per_page_above_actual_escalates(tmp_path: Path, monkeypatch) -> None:
    """min_chars_per_page well above actual ⇒ escalate even on a dense-ish PDF."""
    body = "small body"  # short
    pdf = _build_pdf(tmp_path, [body, body])

    images = [Image.new("RGB", (10, 10), "white") for _ in range(2)]
    stub = _stub_pdf2image_module(images)
    monkeypatch.setattr("corpus_forge.extractors.pdf.pdf2image", stub, raising=False)

    vlm = _make_vlm(extract_returns=["ocr1", "ocr2"])
    ex = PdfDigitalExtractor(vlm=vlm, min_chars_per_page=10_000)
    doc = ex.extract(pdf)

    assert doc.metadata.get("tier") == "ocr_escalated"
    assert vlm.extract_page.call_count == 2


# ── Failure handling ────────────────────────────────────────────────


def test_vlm_unavailable_graceful_fallback(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """VLMUnavailableError on first page ⇒ graceful Tier 1 + reason captured."""
    pdf = _build_blank_pdf(tmp_path, n_pages=3)

    images = [Image.new("RGB", (10, 10), "white") for _ in range(3)]
    stub = _stub_pdf2image_module(images)
    monkeypatch.setattr("corpus_forge.extractors.pdf.pdf2image", stub, raising=False)

    vlm = _make_vlm(
        extract_raises=[VLMUnavailableError("daemon down"), None, None],
        extract_returns=["x", "y", "z"],
    )
    ex = PdfDigitalExtractor(vlm=vlm)

    with caplog.at_level(logging.WARNING, logger="corpus_forge.extractors.pdf"):
        doc = ex.extract(pdf)

    # Tier 1 markdown was returned. metadata.tier == "digital" because the
    # escalation attempt was aborted; the bookkeeping flags record the attempt.
    assert doc.metadata.get("tier") == "digital"
    assert doc.metadata.get("ocr_escalation_attempted") is True
    assert "daemon down" in str(doc.metadata.get("ocr_escalation_failed_reason", ""))
    # No `ocr` label survives a failed escalation.
    assert not any(ns == "ocr" for ns, _val in doc.labels)


def test_vlm_timeout_per_page_placeholder(tmp_path: Path, monkeypatch) -> None:
    """VLMTimeoutError on a middle page ⇒ placeholder; other pages OCR'd."""
    pdf = _build_blank_pdf(tmp_path, n_pages=3)

    images = [Image.new("RGB", (10, 10), "white") for _ in range(3)]
    stub = _stub_pdf2image_module(images)
    monkeypatch.setattr("corpus_forge.extractors.pdf.pdf2image", stub, raising=False)

    vlm = _make_vlm(
        extract_raises=[None, VLMTimeoutError("page 2 took too long"), None],
        extract_returns=["alpha", "skipped", "gamma"],
    )
    ex = PdfDigitalExtractor(vlm=vlm)
    doc = ex.extract(pdf)

    # Escalation completed (one timeout != abort).
    assert doc.metadata.get("tier") == "ocr_escalated"
    assert doc.metadata.get("pages_ocr_count") == 3
    assert "alpha" in doc.text
    assert "gamma" in doc.text
    assert "<!-- VLM timeout on page 2 -->" in doc.text


def test_poppler_missing_graceful_fallback(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """PDFInfoNotInstalledError ⇒ Tier 1 + reason=poppler-not-installed."""
    pdf = _build_blank_pdf(tmp_path, n_pages=2)

    images: list[Image.Image] = []
    stub = _stub_pdf2image_module(images)
    poppler_exc = stub.exceptions.PDFInfoNotInstalledError
    stub.convert_from_path.side_effect = poppler_exc("poppler not found")
    monkeypatch.setattr("corpus_forge.extractors.pdf.pdf2image", stub, raising=False)

    vlm = _make_vlm()
    ex = PdfDigitalExtractor(vlm=vlm)

    with caplog.at_level(logging.ERROR, logger="corpus_forge.extractors.pdf"):
        doc = ex.extract(pdf)

    assert doc.metadata.get("tier") == "digital"
    assert doc.metadata.get("ocr_escalation_attempted") is True
    assert doc.metadata.get("ocr_escalation_failed_reason") == "poppler-not-installed"
    # The VLM was never called.
    assert vlm.extract_page.called is False
    # The error log mentions poppler so users know how to fix it.
    assert any("poppler" in rec.message.lower() for rec in caplog.records)


def test_vlm_response_error_aborts_escalation(tmp_path: Path, monkeypatch) -> None:
    """VLMResponseError mid-escalation is treated like VLMUnavailableError —
    graceful Tier 1 fallback so a broken HTTP response doesn't poison the ingest."""
    pdf = _build_blank_pdf(tmp_path, n_pages=2)

    images = [Image.new("RGB", (10, 10), "white") for _ in range(2)]
    stub = _stub_pdf2image_module(images)
    monkeypatch.setattr("corpus_forge.extractors.pdf.pdf2image", stub, raising=False)

    vlm = _make_vlm(extract_raises=[VLMResponseError("bad JSON"), None])
    ex = PdfDigitalExtractor(vlm=vlm)
    doc = ex.extract(pdf)

    assert doc.metadata.get("tier") == "digital"
    assert doc.metadata.get("ocr_escalation_attempted") is True
    assert "bad JSON" in str(doc.metadata.get("ocr_escalation_failed_reason", ""))


# ── Regression guards ───────────────────────────────────────────────


def test_rag_helper_import_path_preserved() -> None:
    """Regression guard: must use `pymupdf4llm.helpers.pymupdf_rag.to_markdown`.

    The top-level `pymupdf4llm.to_markdown` silently falls back to Tesseract
    OCR which conflicts with our VLM-based escalation (memory:
    `project_phase_d_pymupdf4llm_rag_helper`).
    """
    src = Path("corpus_forge/extractors/pdf.py").read_text(encoding="utf-8")
    assert "from pymupdf4llm.helpers.pymupdf_rag import to_markdown" in src, (
        "PdfDigitalExtractor must keep using the rag-helper import path; "
        "top-level pymupdf4llm.to_markdown auto-falls-back to Tesseract."
    )


def test_lazy_import_pdf2image_not_loaded_at_module_import() -> None:
    """Importing `corpus_forge.extractors.pdf` must NOT pull in pdf2image."""
    import subprocess
    import sys

    script = (
        "import sys; "
        "import corpus_forge.extractors.pdf as m; "
        "assert 'pdf2image' not in sys.modules, sorted(k for k in sys.modules if 'pdf' in k); "
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


def test_is_sparse_returns_false_for_zero_pages() -> None:
    """Defensive: the sparse-text heuristic never divides by zero."""
    ex = PdfDigitalExtractor()
    assert ex._is_sparse("anything", 0) is False
    assert ex._is_sparse("", 0) is False
