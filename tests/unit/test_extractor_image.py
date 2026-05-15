"""Unit tests for E-06: ImageExtractor.

Wave 5 (P1 OCR integration). A thin shim over the active
:class:`~corpus_forge.vlm.base.VLMBackend` — reads the file bytes,
hands them to ``vlm.describe_image``, returns the resulting markdown
inside an :class:`ExtractedDocument`.

Test strategy
-------------
- VLM is a ``unittest.mock.Mock(spec=VLMBackend)``.
- No live network; the extractor never instantiates a real backend.
- Registry registration is exercised by piggy-backing on
  :func:`register_default_extractors`, which Wave 5 (E-05) wires to take
  an optional ``vlm`` argument.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from corpus_forge.extractors import ExtractedDocument, Extractor
from corpus_forge.extractors.image import ImageExtractor
from corpus_forge.vlm.base import NoopVLM, VLMBackend, VLMResponseError

# ── Fixture helpers ──────────────────────────────────────────────────


def _png_bytes() -> bytes:
    """Return a tiny valid PNG byte-string (1x1 white pixel)."""
    from io import BytesIO

    from PIL import Image

    img = Image.new("RGB", (1, 1), "white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _write_png(tmp_path: Path, name: str = "x.png") -> Path:
    p = tmp_path / name
    p.write_bytes(_png_bytes())
    return p


def _make_vlm(name: str = "ollama", returns: str = "# Image\n\nbody") -> MagicMock:
    vlm = MagicMock(spec=VLMBackend)
    vlm.name = name
    vlm.describe_image.return_value = returns
    return vlm


# ── Protocol conformance ────────────────────────────────────────────


def test_extractor_protocol_conformance() -> None:
    ex: Extractor = ImageExtractor(vlm=_make_vlm())
    assert isinstance(ex.supported_extensions, tuple)


def test_supported_extensions() -> None:
    ex = ImageExtractor(vlm=_make_vlm())
    assert set(ex.supported_extensions) == {
        ".png",
        ".jpg",
        ".jpeg",
        ".tif",
        ".tiff",
        ".bmp",
        ".webp",
        ".heic",
    }


def test_supported_filenames_is_empty_tuple() -> None:
    """Images are extension-keyed; no filename-fallback entries."""
    ex = ImageExtractor(vlm=_make_vlm())
    assert ex.supported_filenames == ()


def test_constructor_requires_vlm_keyword() -> None:
    """`vlm` must be keyword-only — guards against positional misuse."""
    with pytest.raises(TypeError):
        ImageExtractor(MagicMock(spec=VLMBackend))  # type: ignore[misc]


def test_constructor_default_prompt_documents_intent() -> None:
    """Default prompt instructs the VLM to transcribe + describe."""
    ex = ImageExtractor(vlm=_make_vlm())
    assert isinstance(ex.prompt, str)
    assert ex.prompt.strip()
    # Common transcribe-and-describe phrasing.
    lower = ex.prompt.lower()
    assert "transcribe" in lower or "describe" in lower


# ── Extraction ──────────────────────────────────────────────────────


def test_extract_returns_extracted_document(tmp_path: Path) -> None:
    p = _write_png(tmp_path)
    vlm = _make_vlm(returns="# Picture\n\nA white square.")
    doc = ImageExtractor(vlm=vlm).extract(p)

    assert isinstance(doc, ExtractedDocument)
    assert doc.text == "# Picture\n\nA white square."
    assert doc.chunker_hint == "markdown"
    assert doc.language is None


def test_extract_metadata_shape(tmp_path: Path) -> None:
    p = _write_png(tmp_path)
    vlm = _make_vlm(name="mistral")
    doc = ImageExtractor(vlm=vlm).extract(p)
    assert doc.metadata["extractor"] == "image"
    assert doc.metadata["ocr_backend"] == "mistral"
    assert doc.metadata["byte_count"] == len(p.read_bytes())
    assert doc.metadata["byte_count"] > 0


def test_extract_labels(tmp_path: Path) -> None:
    p = _write_png(tmp_path)
    vlm = _make_vlm(name="ollama")
    doc = ImageExtractor(vlm=vlm).extract(p)
    label_set = {(ns, val) for ns, val in doc.labels}
    assert ("format", "image") in label_set
    assert ("ocr", "ollama") in label_set


def test_extract_passes_image_bytes_to_vlm(tmp_path: Path) -> None:
    p = _write_png(tmp_path)
    vlm = _make_vlm()
    ImageExtractor(vlm=vlm).extract(p)
    vlm.describe_image.assert_called_once()
    args, kwargs = vlm.describe_image.call_args
    image_arg = args[0] if args else kwargs.get("image")
    assert isinstance(image_arg, bytes)
    assert image_arg == p.read_bytes()


def test_extract_default_prompt_passed_through(tmp_path: Path) -> None:
    p = _write_png(tmp_path)
    vlm = _make_vlm()
    ex = ImageExtractor(vlm=vlm)
    ex.extract(p)
    _args, kwargs = vlm.describe_image.call_args
    assert kwargs.get("prompt") == ex.prompt


def test_extract_custom_prompt_passed_through(tmp_path: Path) -> None:
    p = _write_png(tmp_path)
    vlm = _make_vlm()
    custom = "OCR only — output plain text, no preamble."
    ex = ImageExtractor(vlm=vlm, prompt=custom)
    ex.extract(p)
    _args, kwargs = vlm.describe_image.call_args
    assert kwargs.get("prompt") == custom


def test_extract_propagates_vlm_response_error(tmp_path: Path) -> None:
    p = _write_png(tmp_path)
    vlm = MagicMock(spec=VLMBackend)
    vlm.name = "ollama"
    vlm.describe_image.side_effect = VLMResponseError("malformed body")
    with pytest.raises(VLMResponseError):
        ImageExtractor(vlm=vlm).extract(p)


def test_extract_handles_all_supported_extensions(tmp_path: Path) -> None:
    """Every declared extension is routed through the same code path."""
    vlm = _make_vlm()
    ex = ImageExtractor(vlm=vlm)
    body = _png_bytes()
    for ext in ex.supported_extensions:
        p = tmp_path / f"sample{ext}"
        p.write_bytes(body)
        doc = ex.extract(p)
        assert isinstance(doc, ExtractedDocument)
        # Reset describe_image so we can re-introspect per iteration.
        vlm.describe_image.reset_mock()


# ── Registry integration (E-05 wires the lazy registration) ─────────


def test_registry_includes_image_extractor_when_vlm_and_ocr_enabled() -> None:
    """register_default_extractors registers ImageExtractor when vlm + ocr."""
    from corpus_forge.config import ExtractionConfig
    from corpus_forge.extractors.registry import register_default_extractors

    vlm = _make_vlm()
    cfg = ExtractionConfig()  # ocr_enabled=True by default
    reg = register_default_extractors(cfg, vlm=vlm)

    # All eight image extensions resolve to an ImageExtractor instance.
    for ext in ImageExtractor.supported_extensions:
        p = Path(f"/tmp/example{ext}")
        ex = reg.get_for(p)
        assert isinstance(ex, ImageExtractor), f"missing image extractor for {ext}"


def test_registry_skips_image_extractor_when_vlm_is_none() -> None:
    """No vlm ⇒ ImageExtractor is not registered (silent skip)."""
    from corpus_forge.config import ExtractionConfig
    from corpus_forge.extractors.registry import register_default_extractors

    cfg = ExtractionConfig()
    reg = register_default_extractors(cfg, vlm=None)
    assert reg.get_for(Path("/tmp/x.png")) is None


def test_registry_skips_image_extractor_with_noop_vlm() -> None:
    """NoopVLM is treated as 'no vlm configured' ⇒ silent skip."""
    from corpus_forge.config import ExtractionConfig
    from corpus_forge.extractors.registry import register_default_extractors

    cfg = ExtractionConfig()
    reg = register_default_extractors(cfg, vlm=NoopVLM())
    assert reg.get_for(Path("/tmp/x.png")) is None


def test_registry_skips_image_extractor_when_ocr_disabled() -> None:
    """ocr_enabled=False ⇒ ImageExtractor is not registered."""
    from corpus_forge.config import ExtractionConfig
    from corpus_forge.extractors.registry import register_default_extractors

    cfg = ExtractionConfig(ocr_enabled=False)
    reg = register_default_extractors(cfg, vlm=_make_vlm())
    assert reg.get_for(Path("/tmp/x.png")) is None
