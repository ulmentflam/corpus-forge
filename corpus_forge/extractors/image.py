"""VLM-backed image extractor.

Phase D — Wave 5 (E-06).

A thin shim over the active :class:`~corpus_forge.vlm.base.VLMBackend`:
read the file bytes, hand them to :meth:`VLMBackend.describe_image`,
return the resulting Markdown inside an
:class:`~corpus_forge.extractors.base.ExtractedDocument`.

The extractor doesn't try to decode or transform the image itself —
that's the VLM's responsibility. The two practical consequences:

- ``.heic`` files require a VLM that can decode HEIC raw bytes. Ollama
  + Qwen2.5-VL handle HEIC natively; Mistral OCR's API surface accepts
  data URLs and decodes server-side. If a user's VLM cannot decode
  HEIC bytes, the failure surfaces as a ``VLMResponseError`` from the
  backend — install ``pillow-heif`` (an opt-in extra) to convert HEIC
  to PNG upstream if that becomes a problem.
- Multi-page TIFFs are treated as a single image. Per-page extraction
  for multi-page TIFF is out of scope for Wave 5 (would require a
  rasterisation step similar to the PDF Tier 2 path).

Registry registration is handled by
:func:`~corpus_forge.extractors.registry.register_default_extractors`,
which conditionally instantiates this class when:

1. a real VLM (not :class:`~corpus_forge.vlm.base.NoopVLM`) is wired in,
2. :attr:`~corpus_forge.config.ExtractionConfig.ocr_enabled` is ``True``, AND
3. :attr:`~corpus_forge.config.ExtractionConfig.enable_image` is ``True``.

Otherwise the extractor is silently skipped — users who installed
``[multi-format]`` but didn't configure a VLM get the same "no image
support" experience they had before Wave 5.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .base import ExtractedDocument

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.vlm.base import VLMBackend


_DEFAULT_PROMPT = (
    "Transcribe any text verbatim and describe the visual content "
    "faithfully. Output clean Markdown only — no preamble."
)


class ImageExtractor:
    """Image-to-Markdown extractor backed by a VLM.

    Args:
        vlm: A :class:`~corpus_forge.vlm.base.VLMBackend` (keyword-only).
            Required — the extractor delegates to
            ``vlm.describe_image(image_bytes, prompt=...)``.
        prompt: Override the default transcribe-and-describe prompt
            (keyword-only). ``None`` uses the module-level default.
    """

    name = "image"
    supported_extensions: tuple[str, ...] = (
        ".png",
        ".jpg",
        ".jpeg",
        ".tif",
        ".tiff",
        ".bmp",
        ".webp",
        ".heic",
    )
    supported_filenames: tuple[str, ...] = ()

    def __init__(self, *, vlm: VLMBackend, prompt: str | None = None) -> None:
        self.vlm = vlm
        self.prompt = prompt if prompt is not None else _DEFAULT_PROMPT

    def extract(self, path: Path) -> ExtractedDocument:
        image_bytes = path.read_bytes()
        markdown = self.vlm.describe_image(image_bytes, prompt=self.prompt)
        return ExtractedDocument(
            text=markdown,
            chunker_hint="markdown",
            language=None,
            metadata={
                "extractor": "image",
                "ocr_backend": self.vlm.name,
                "byte_count": len(image_bytes),
                # Phase G P1 (G-15): persist the source path so the
                # image-embed backfill can re-read the bytes without
                # parsing the source URI.
                "image_path": str(path.resolve()),
            },
            labels=[("format", "image"), ("ocr", self.vlm.name)],
        )
