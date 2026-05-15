"""Remote-Mistral OCR end-to-end tests — Phase D / Wave 6 (E-08).

Mirror of ``test_ocr_local_e2e.py`` but driving the
:class:`~corpus_forge.vlm.mistral.MistralOCR` backend instead of the
local Ollama daemon. The test bodies are intentionally short and
contract-shaped — the goal is to confirm the live API surface still
matches our wire-level expectations (response JSON shape, error
mapping, output Markdown is non-empty). Quality-of-extraction is not
asserted; the unit suite covers the exception map with mocked HTTP.

Gating: every test carries the ``requires_mistral_api`` pytest marker.
``tests/integration/conftest.py`` auto-skips the entire file at
collection time when ``MISTRAL_API_KEY`` is unset (the common case on
contributor machines that haven't requested an API key). When the key
*is* set, the suite makes live API calls — each costs roughly
$0.001 / 1000 pages per the Mistral OCR pricing page, so running the
whole file is on the order of a few US cents per CI run.

The 401-/wrong-key path is intentionally NOT covered here:
``MistralOCR.warmup()`` validates the key shape at construction time
and the Wave 4 unit suite (``tests/unit/test_vlm_mistral.py``) covers
the HTTP exception map with mocked responses. Live e2e is for
"contract still matches reality", not for re-testing exception paths.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from corpus_forge.extractors.image import ImageExtractor
from corpus_forge.extractors.pdf import PdfDigitalExtractor
from corpus_forge.vlm.mistral import MistralOCR

pytestmark = [pytest.mark.integration, pytest.mark.requires_mistral_api]

_FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "multi_format_corpus"
_SCANNED_PDF = _FIXTURE_ROOT / "pdf" / "scanned-paper.pdf"
_SCREENSHOT = _FIXTURE_ROOT / "images" / "screenshot.png"
_RECEIPT = _FIXTURE_ROOT / "images" / "photo-of-receipt.jpg"
_DIAGRAM = _FIXTURE_ROOT / "images" / "diagram.webp"

_MIN_TEXT_LEN = 50


def _make_vlm(*, timeout_s: float = 120.0) -> MistralOCR:
    """Build a :class:`MistralOCR` against the live API.

    The conftest skip guarantees ``MISTRAL_API_KEY`` is set before any
    of these tests run; we read it from the environment here without
    a fallback so a regression in the skip plumbing surfaces as a
    ``VLMUnavailableError`` from the constructor (loud, not silent).
    """
    api_key = os.environ["MISTRAL_API_KEY"]
    return MistralOCR(api_key=api_key, timeout_s=timeout_s)


@pytest.mark.timeout(180)
def test_pdf_escalation_happy_path() -> None:
    """Scanned PDF → Mistral OCR escalation produces non-empty Markdown."""
    assert _SCANNED_PDF.is_file(), f"Fixture missing: {_SCANNED_PDF}"
    vlm = _make_vlm()
    extractor = PdfDigitalExtractor(vlm=vlm, ocr_enabled=True, min_chars_per_page=100)

    doc = extractor.extract(_SCANNED_PDF)

    assert len(doc.text) > _MIN_TEXT_LEN, (
        f"Mistral OCR returned only {len(doc.text)} chars. Response preview: {doc.text[:200]!r}"
    )
    assert doc.metadata.get("tier") == "ocr_escalated"
    assert doc.metadata.get("pages_ocr_count", 0) >= 1
    assert doc.metadata.get("ocr_backend") == "mistral"
    assert ("ocr", "mistral") in doc.labels
    assert ("format", "pdf") in doc.labels


@pytest.mark.timeout(120)
def test_image_extractor_happy_path() -> None:
    """ImageExtractor + Mistral OCR produces non-empty Markdown for a screenshot."""
    assert _SCREENSHOT.is_file(), f"Fixture missing: {_SCREENSHOT}"
    vlm = _make_vlm()
    extractor = ImageExtractor(vlm=vlm)

    doc = extractor.extract(_SCREENSHOT)

    assert len(doc.text) > _MIN_TEXT_LEN
    assert doc.chunker_hint == "markdown"
    assert ("format", "image") in doc.labels
    assert ("ocr", "mistral") in doc.labels


@pytest.mark.timeout(240)
def test_all_three_image_fixtures_round_trip() -> None:
    """Every P1 image fixture round-trips through Mistral OCR."""
    vlm = _make_vlm()
    extractor = ImageExtractor(vlm=vlm)
    for fixture in (_SCREENSHOT, _RECEIPT, _DIAGRAM):
        assert fixture.is_file(), f"Fixture missing: {fixture}"
        doc = extractor.extract(fixture)
        assert doc.text, f"Mistral OCR returned empty text for {fixture.name}"
        assert isinstance(doc.text, str)
