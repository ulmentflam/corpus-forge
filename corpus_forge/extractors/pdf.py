"""Digital PDF extractor with optional VLM-backed OCR escalation.

Phase D — Wave 1 (D-07) introduced the digital-only path; Wave 5 (E-05)
layered the VLM-backed Tier 2 OCR escalation on top of it.

Tier 1 — `pymupdf4llm` text-layer pass
---------------------------------------

The :func:`pymupdf4llm.helpers.pymupdf_rag.to_markdown` rag-helper reads
the PDF's embedded text layer and emits Markdown. We deliberately do
NOT use the top-level :func:`pymupdf4llm.to_markdown` entry point — it
ships with ``_use_layout=True`` in pymupdf4llm 1.27+, which routes the
call through :mod:`pymupdf-layout` and silently falls back to Tesseract
OCR on pages it can't lay out. That bypass conflicts with our explicit
VLM-driven escalation (and would mix two OCR engines on the same
document), so we reach past the layout switch and call the rag helper
directly. See ``project_phase_d_pymupdf4llm_rag_helper`` for the full
backstory.

Tier 2 — VLM OCR escalation
---------------------------

When the average text-layer chars/page falls below
``min_chars_per_page`` (default 100, configurable via
``ExtractionConfig.ocr_min_chars_per_page``), the extractor:

1. Rasterises every page to PNG via :func:`pdf2image.convert_from_path`
   at ``ocr_dpi`` (default 200 DPI). ``pdf2image`` is a thin wrapper
   over the system ``poppler-utils`` binaries — Tier 2 ships with a
   non-Python system requirement (see README's "Distribution /
   licensing" section).
2. Sends each PNG to the configured :class:`VLMBackend` via
   :meth:`extract_page`. Per-page Markdown is concatenated with
   ``\\n\\n---\\n\\n`` to preserve page boundaries.
3. Stamps :attr:`ExtractedDocument.metadata` with ``tier="ocr_escalated"``,
   ``pages_ocr_count``, ``ocr_backend``, ``ocr_model`` and adds
   ``(\"ocr\", vlm.name)`` / ``(\"ocr_model\", model_tag)`` labels so
   downstream consumers can filter on OCR provenance.

Failure handling is "robust functionality, all green tests, stable
behavior" (per user directive). Every failure mode degrades gracefully:

- VLM aborts on the first page (``VLMUnavailableError`` /
  ``VLMResponseError``) ⇒ Tier 1 markdown is returned with
  ``metadata.ocr_escalation_attempted = True`` and
  ``metadata.ocr_escalation_failed_reason``. No ``ocr`` label is added.
- VLM times out on a single page in the middle of the run
  (``VLMTimeoutError``) ⇒ a ``<!-- VLM timeout on page N -->`` placeholder
  takes that page's slot in the concatenated Markdown; remaining pages
  continue. Escalation metadata still reports the page count.
- ``pdf2image.exceptions.PDFInfoNotInstalledError`` (poppler missing) ⇒
  ERROR log + Tier 1 fallback with
  ``ocr_escalation_failed_reason = "poppler-not-installed"``.

The extractor never raises out of :meth:`extract` because of an OCR
failure — the caller (typically
:class:`~corpus_forge.sources.filesystem.FilesystemSource`) sees a
populated :class:`ExtractedDocument` either way.

Constructor injection
---------------------

``vlm`` is constructor-injected so unit tests can stub it without
dragging the global VLM registry into every fixture. ``vlm=None``
(the default) disables escalation entirely — the legacy D-07 contract
is preserved.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import ExtractedDocument

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.vlm.base import VLMBackend

logger = logging.getLogger(__name__)

# Default average chars-per-page threshold below which the text layer is
# flagged as sparse (and Tier 2 escalation is considered). Overridable
# via the ``min_chars_per_page`` constructor argument or
# ``ExtractionConfig.ocr_min_chars_per_page``.
_DEFAULT_MIN_CHARS_PER_PAGE = 100
_DEFAULT_OCR_DPI = 200
_PAGE_SEPARATOR = "\n\n---\n\n"

# Imported lazily by :meth:`extract` so module import doesn't pull in
# pdf2image / pillow on a no-[ocr] install. The module-level binding
# is `None` until the first escalation call hydrates it.
pdf2image = None  # type: ignore[assignment]


class PdfDigitalExtractor:
    """PDF extractor with optional VLM-backed OCR escalation.

    Args:
        vlm: A :class:`~corpus_forge.vlm.base.VLMBackend` instance, or
            ``None`` to disable escalation. A :class:`NoopVLM` is
            treated identically to ``None`` (silent short-circuit so
            users who installed ``[multi-format]`` but didn't configure
            a VLM still get the D-07 digital-only behaviour).
        ocr_enabled: Master switch (default ``True``). Setting ``False``
            disables escalation even when a real VLM is wired in and
            the text layer is sparse.
        min_chars_per_page: Average chars-per-page threshold below
            which Tier 2 fires (default ``100``).
        ocr_dpi: Rasterisation DPI for ``pdf2image.convert_from_path``
            (default ``200``).
    """

    supported_extensions: tuple[str, ...] = (".pdf",)

    def __init__(
        self,
        *,
        vlm: VLMBackend | None = None,
        ocr_enabled: bool = True,
        min_chars_per_page: int = _DEFAULT_MIN_CHARS_PER_PAGE,
        ocr_dpi: int = _DEFAULT_OCR_DPI,
    ) -> None:
        self.vlm = vlm
        self.ocr_enabled = ocr_enabled
        self.min_chars_per_page = min_chars_per_page
        self.ocr_dpi = ocr_dpi

    # ── public API ─────────────────────────────────────────────────────

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

        tier1_text = to_markdown(str(path))

        metadata: dict = {
            "page_count": page_count,
            "extractor": "pdf_digital",
            "tier": "digital",
        }
        labels: list[tuple[str, str]] = [
            ("format", "pdf"),
            ("extractor", "pymupdf4llm"),
        ]

        # Sparse text-layer detector — average chars/page below threshold
        # signals a scanned PDF that Tier 2 (below) will escalate.
        sparse = self._is_sparse(tier1_text, page_count)
        if sparse:
            metadata["sparse_text_layer"] = True

        if not self._should_escalate(sparse):
            return ExtractedDocument(
                text=tier1_text,
                chunker_hint="markdown",
                language=None,
                metadata=metadata,
                labels=labels,
            )

        # ── Tier 2 — VLM escalation ──────────────────────────────────
        return self._escalate(
            path=path,
            tier1_text=tier1_text,
            base_metadata=metadata,
            base_labels=labels,
        )

    # ── private helpers ────────────────────────────────────────────────

    def _is_sparse(self, text: str, page_count: int) -> bool:
        """Return True if average chars/page is below the threshold."""
        if page_count <= 0:
            return False
        avg = len(text) / page_count
        return avg < self.min_chars_per_page

    def _should_escalate(self, sparse: bool) -> bool:
        """Return True if Tier 2 should fire for this document.

        Short-circuits on:
        - no sparse-text-layer signal,
        - ``ocr_enabled=False``,
        - ``vlm is None`` (extractor constructed without a backend),
        - ``isinstance(vlm, NoopVLM)`` (Option 1 silent short-circuit:
          user installed ``[multi-format]`` but didn't configure a VLM ⇒
          fall through to Tier 1 unchanged).
        """
        if not sparse:
            return False
        if not self.ocr_enabled:
            return False
        if self.vlm is None:
            return False
        # Lazy import to avoid pulling the vlm package into our module
        # graph until it's actually needed.
        from corpus_forge.vlm.base import NoopVLM  # noqa: PLC0415

        return not isinstance(self.vlm, NoopVLM)

    def _escalate(
        self,
        *,
        path: Path,
        tier1_text: str,
        base_metadata: dict,
        base_labels: list[tuple[str, str]],
    ) -> ExtractedDocument:
        """Tier 2 — rasterise pages and call the VLM per page."""
        # Resolve pdf2image at call time so unit tests can substitute
        # the module via ``monkeypatch.setattr("corpus_forge.extractors.pdf.pdf2image", ...)``
        # before invoking ``.extract()``. The first real invocation
        # populates the module-level alias from the installed package.
        _pdf2image = _resolve_pdf2image()
        if _pdf2image is None:
            logger.error(
                "pdf2image is not installed — install the [ocr] extra to enable "
                "PDF OCR escalation (uv sync --extra ocr). Falling back to Tier 1 "
                "markdown for %s.",
                path,
            )
            return _tier1_with_failure(
                tier1_text=tier1_text,
                base_metadata=base_metadata,
                base_labels=base_labels,
                reason="pdf2image-not-installed",
            )

        try:
            images = _pdf2image.convert_from_path(
                str(path),
                dpi=self.ocr_dpi,
                fmt="png",
            )
        except _pdf2image.exceptions.PDFInfoNotInstalledError as exc:
            logger.error(
                "poppler-utils is not installed (PDFInfoNotInstalledError on %s): %s. "
                "Install poppler via Homebrew (`brew install poppler`) on macOS or "
                "your distro's package manager on Linux. Falling back to Tier 1 "
                "markdown for this document.",
                path,
                exc,
            )
            return _tier1_with_failure(
                tier1_text=tier1_text,
                base_metadata=base_metadata,
                base_labels=base_labels,
                reason="poppler-not-installed",
            )
        except Exception as exc:
            logger.warning(
                "pdf2image rasterisation failed on %s: %s. Falling back to Tier 1 markdown.",
                path,
                exc,
            )
            return _tier1_with_failure(
                tier1_text=tier1_text,
                base_metadata=base_metadata,
                base_labels=base_labels,
                reason=f"pdf2image-error: {exc}",
            )

        # Call the VLM per page. VLMUnavailableError / VLMResponseError
        # on the first page aborts escalation (graceful fallback);
        # VLMTimeoutError on a single page leaves a placeholder and
        # continues.
        from corpus_forge.vlm.base import (  # noqa: PLC0415
            VLMResponseError,
            VLMTimeoutError,
            VLMUnavailableError,
        )

        vlm = self.vlm
        # ``vlm is None`` was filtered by ``_should_escalate`` — narrow
        # the type for pyrefly.
        assert vlm is not None

        per_page: list[str] = []
        for idx, image in enumerate(images, start=1):
            page_bytes = _to_png_bytes(image)
            try:
                md = vlm.extract_page(page_bytes, page_number=idx)
                per_page.append(md)
            except VLMTimeoutError as exc:
                logger.warning(
                    "VLM timeout on page %d of %s: %s — inserting placeholder.",
                    idx,
                    path,
                    exc,
                )
                per_page.append(f"<!-- VLM timeout on page {idx} -->")
            except (VLMUnavailableError, VLMResponseError) as exc:
                logger.warning(
                    "VLM error on page %d of %s: %s. Aborting escalation; "
                    "falling back to Tier 1 markdown.",
                    idx,
                    path,
                    exc,
                )
                return _tier1_with_failure(
                    tier1_text=tier1_text,
                    base_metadata=base_metadata,
                    base_labels=base_labels,
                    reason=str(exc),
                )

        text = _PAGE_SEPARATOR.join(per_page)

        meta = dict(base_metadata)
        meta["tier"] = "ocr_escalated"
        meta["pages_ocr_count"] = len(images)
        meta["ocr_backend"] = vlm.name
        model_tag = _get_model_tag(vlm)
        if model_tag is not None:
            meta["ocr_model"] = model_tag

        labels = list(base_labels)
        labels.append(("ocr", vlm.name))
        if model_tag is not None:
            labels.append(("ocr_model", model_tag))

        return ExtractedDocument(
            text=text,
            chunker_hint="markdown",
            language=None,
            metadata=meta,
            labels=labels,
        )


def _resolve_pdf2image() -> Any:
    """Resolve the ``pdf2image`` module, honouring test-time monkeypatches.

    Three precedence rules apply:

    1. If a test has set ``corpus_forge.extractors.pdf.pdf2image`` to a
       non-``None`` value via :meth:`pytest.MonkeyPatch.setattr`, use it.
    2. Otherwise, try to import the real package. On success the module
       binding is cached so subsequent calls are cheap.
    3. On ``ImportError`` (no ``[ocr]`` extra installed), return
       ``None`` — :meth:`PdfDigitalExtractor._escalate` handles the
       missing-dep case by returning Tier 1 markdown.

    Returns:
        The ``pdf2image`` module (or a test stub mirroring its surface)
        when escalation is possible, ``None`` when the dependency is
        missing. The return type is annotated as :data:`typing.Any` so
        the caller can dot-access ``convert_from_path`` and
        ``exceptions.PDFInfoNotInstalledError`` without pyrefly
        complaining about a typed module reference.
    """
    global pdf2image  # noqa: PLW0603 — module-level cache by design
    if pdf2image is not None:
        return pdf2image
    try:
        import pdf2image as _pdf2image_real  # noqa: PLC0415
    except ImportError:
        return None
    pdf2image = _pdf2image_real
    return pdf2image


def _tier1_with_failure(
    *,
    tier1_text: str,
    base_metadata: dict,
    base_labels: list[tuple[str, str]],
    reason: str,
) -> ExtractedDocument:
    """Build the Tier 1 fallback :class:`ExtractedDocument` with failure metadata.

    Centralised so every Tier-2 failure path produces an identical
    shape: ``metadata.tier`` stays at ``"digital"``, the escalation
    bookkeeping flags record the attempt + reason, and no ``ocr``
    label is added.
    """
    meta = dict(base_metadata)
    meta["ocr_escalation_attempted"] = True
    meta["ocr_escalation_failed_reason"] = reason
    return ExtractedDocument(
        text=tier1_text,
        chunker_hint="markdown",
        language=None,
        metadata=meta,
        labels=base_labels,
    )


def _to_png_bytes(image: object) -> bytes:
    """Encode a PIL.Image to in-memory PNG bytes.

    Kept at module level so the per-page hot loop doesn't re-import
    BytesIO on every call.
    """
    from io import BytesIO  # noqa: PLC0415

    buf = BytesIO()
    # Duck-typed: any object with a Pillow-style ``save(buf, format=...)``
    # works. The unit tests pass real ``PIL.Image`` instances.
    image.save(buf, format="PNG")  # type: ignore[attr-defined]
    return buf.getvalue()


def _get_model_tag(vlm: object) -> str | None:
    """Best-effort lookup for the VLM's model tag (Ollama) / id (Mistral).

    Both concrete backends in Wave 4 expose a ``.model`` attribute; the
    Protocol does not require it, so we duck-type the lookup and return
    ``None`` for backends that don't expose one.
    """
    return getattr(vlm, "model", None)
