"""Extension → :class:`Extractor` registry.

Phase D / Wave 0 — D-01.

The registry is a flat ``dict[str, Extractor]`` keyed on the lowercase
file extension (including the leading dot). It is intentionally simple:

- ``register(ext)`` overwrites any prior entry for the same extension —
  last-write-wins so users can swap in a custom extractor by registering
  it after the defaults.
- ``get_for(path)`` returns ``None`` for unsupported extensions; callers
  decide what to do (skip, log, raise).
- :func:`register_default_extractors` is the boot hook that wires every
  P0 extractor in respecting feature flags so unused heavy deps stay
  unimported.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from .base import Extractor

if TYPE_CHECKING:  # pragma: no cover — typing only
    from collections.abc import Iterable

    from corpus_forge.vlm.base import VLMBackend
    from corpus_forge.whisper.base import WhisperBackend

logger = logging.getLogger(__name__)


def _try_load(submodule: str, class_name: str) -> type | None:
    """Lazy-load ``corpus_forge.extractors.<submodule>.<class_name>``.

    Returns ``None`` if the submodule isn't available (Wave 1+ extractors
    not yet landed, or an optional extra missing). The dynamic
    ``importlib`` call keeps static-analysis tools from flagging the
    not-yet-landed siblings as missing imports.
    """
    try:
        mod = importlib.import_module(f"corpus_forge.extractors.{submodule}")
    except ImportError as exc:
        logger.debug("extractor submodule %s not available: %s", submodule, exc)
        return None
    return getattr(mod, class_name, None)


class ExtractorRegistry:
    """Dispatch table from file extension (or filename) to :class:`Extractor`.

    Dispatch is two-pass:

    1. **Extension** — ``path.suffix.lower()`` against the extension table
       (case-insensitive).
    2. **Filename** — exact ``path.name`` against the filename table
       (case-sensitive, but both ``"Makefile"`` and ``"makefile"`` can be
       declared simultaneously — Wave 2 D-14).

    Extension lookups are the hot path; filename fallback only fires when
    the extension table misses. Extractors that don't declare
    :attr:`Extractor.supported_filenames` keep working unchanged.
    """

    def __init__(self) -> None:
        self._by_ext: dict[str, Extractor] = {}
        self._by_filename: dict[str, Extractor] = {}

    def register(self, extractor: Extractor) -> None:
        """Register ``extractor`` for each of its supported extensions and filenames.

        Last-write-wins: registering for an already-bound key replaces
        the previous entry. Extension keys are normalised to lowercase so
        callers do not need to be careful about case. Filename keys are
        stored verbatim — declare both ``"Makefile"`` and ``"makefile"``
        if you want cross-platform matching.

        Raises:
            ValueError: if any declared extension does not begin with ``.``.
        """
        for ext in extractor.supported_extensions:
            if not ext.startswith("."):
                raise ValueError(f"Extractor extension must begin with '.', got {ext!r}")
            key = ext.lower()
            if key in self._by_ext:
                logger.debug("ExtractorRegistry: replacing existing extractor for %s", key)
            self._by_ext[key] = extractor

        # Filename fallback (Wave 2 — D-14). Tolerate extractors that
        # don't declare the attribute by treating it as ``()``.
        for filename in getattr(extractor, "supported_filenames", ()) or ():
            if filename in self._by_filename:
                logger.debug(
                    "ExtractorRegistry: replacing existing extractor for filename %s",
                    filename,
                )
            self._by_filename[filename] = extractor

    def get_for(self, path: Path) -> Extractor | None:
        """Return the extractor for ``path`` or ``None`` if unsupported.

        Resolution order: extension first (case-insensitive), then
        filename second-pass (verbatim). Both ``Makefile`` and
        ``makefile`` resolve when both have been declared.
        """
        ext_match = self._by_ext.get(path.suffix.lower())
        if ext_match is not None:
            return ext_match
        return self._by_filename.get(path.name)

    def extensions(self) -> Iterable[str]:
        """Return every registered (lowercase) extension."""
        return list(self._by_ext.keys())

    def filenames(self) -> Iterable[str]:
        """Return every registered filename (verbatim, case-sensitive)."""
        return list(self._by_filename.keys())


def register_default_extractors(
    config: object | None,
    vlm: VLMBackend | None = None,
    whisper: WhisperBackend | None = None,
) -> ExtractorRegistry:
    """Construct a fully-wired :class:`ExtractorRegistry` from ``config``.

    ``config`` is duck-typed against the ``enable_*`` flags exposed by
    :class:`corpus_forge.config.ExtractionConfig` (D-06). When ``config``
    is ``None`` every flag defaults to ``True``. Importantly, when a
    family is disabled, its concrete extractor module is **never
    imported** — heavy optional deps stay out of the import graph.

    ``vlm`` is Wave 5's optional second argument (E-05). When supplied:

    - :class:`~corpus_forge.extractors.pdf.PdfDigitalExtractor` is
      constructed with the VLM injected so its Tier 2 escalation path
      is active.
    - :class:`~corpus_forge.extractors.image.ImageExtractor` is
      registered for ``.png`` / ``.jpg`` / ... files, but ONLY when the
      backend is a real one (i.e. not :class:`NoopVLM`) AND
      ``ocr_enabled`` is ``True`` AND ``enable_image`` is ``True``.

    The default registry is populated incrementally as later Wave 0/1
    tasks land:

    - Wave 0 (D-03): passthrough / plaintext.
    - Wave 0 (D-04): structured / subtitle.
    - Wave 1 (D-07..D-13): pdf / html / epub / office / notebook / csv /
      code.
    - Wave 5 (E-06): image (when a real VLM is wired in).

    For tasks that have not landed yet, the corresponding ``enable_*``
    flag is honoured by silently skipping registration — callers should
    only see "extractor missing" via ``get_for() is None``, never an
    :class:`ImportError`.
    """

    def _flag(name: str, default: bool = True) -> bool:
        if config is None:
            return default
        return bool(getattr(config, name, default))

    reg = ExtractorRegistry()

    # ── Wave 0 stdlib leaves (D-03, D-04) — landed in this milestone ──
    if _flag("enable_markdown"):
        cls = _try_load("passthrough", "PassthroughMarkdownExtractor")
        if cls is not None:
            reg.register(cls())

    if _flag("enable_plaintext"):
        cls = _try_load("plaintext", "PlainTextExtractor")
        if cls is not None:
            reg.register(cls())

    if _flag("enable_structured"):
        cls = _try_load("structured", "StructuredDataExtractor")
        if cls is not None:
            reg.register(cls())

    if _flag("enable_subtitle"):
        cls = _try_load("subtitle", "SubtitleExtractor")
        if cls is not None:
            reg.register(cls())

    # ── Wave 1 heavy extractors (gated by feature flags) ──
    # When disabled, NEVER import the module — that's the whole point of
    # the hook. When enabled but the module isn't installed (Wave 1 not
    # landed yet, or extra not present), swallow the ImportError so the
    # registry remains usable. ``_try_load`` returns ``None`` on
    # ``ImportError`` so missing submodules are silently skipped.
    if _flag("enable_pdf"):
        cls = _try_load("pdf", "PdfDigitalExtractor")
        if cls is not None:
            # E-05 (Wave 5): inject the VLM + OCR knobs so the Tier 2
            # escalation path is available when configured. When
            # ``vlm is None`` the extractor still works (D-07 contract
            # preserved — Tier 1 only).
            ocr_kwargs: dict = {}
            if config is not None:
                ocr_enabled_cfg = getattr(config, "ocr_enabled", None)
                if ocr_enabled_cfg is not None:
                    ocr_kwargs["ocr_enabled"] = bool(ocr_enabled_cfg)
                min_chars = getattr(config, "ocr_min_chars_per_page", None)
                if min_chars is not None:
                    ocr_kwargs["min_chars_per_page"] = int(min_chars)
                dpi = getattr(config, "ocr_dpi", None)
                if dpi is not None:
                    ocr_kwargs["ocr_dpi"] = int(dpi)
            reg.register(cls(vlm=vlm, **ocr_kwargs))

    if _flag("enable_html"):
        cls = _try_load("html", "HtmlExtractor")
        if cls is not None:
            reg.register(cls())

    if _flag("enable_epub"):
        cls = _try_load("epub", "EpubExtractor")
        if cls is not None:
            reg.register(cls())

    if _flag("enable_office"):
        cls = _try_load("office", "OfficeExtractor")
        if cls is not None:
            reg.register(cls())

    if _flag("enable_notebook"):
        cls = _try_load("notebook", "NotebookExtractor")
        if cls is not None:
            reg.register(cls())

    if _flag("enable_csv"):
        cls = _try_load("csv", "CsvExtractor")
        if cls is not None:
            csv_max_rows = getattr(config, "csv_max_rows", None) if config is not None else None
            if csv_max_rows is not None:
                reg.register(cls(max_rows=csv_max_rows))
            else:
                reg.register(cls())

    if _flag("enable_code"):
        cls = _try_load("code", "CodeExtractor")
        if cls is not None:
            code_chunker_config = (
                getattr(config, "code_chunker_config", None) if config is not None else None
            )
            if code_chunker_config is not None:
                reg.register(cls(code_chunker_config=code_chunker_config))
            else:
                reg.register(cls())

    # ── Wave 5 (E-06) — VLM-backed image extractor (gated) ──
    # Registered only when (a) a real VLM is wired in (NoopVLM is treated
    # as "no VLM configured" so users who installed [multi-format] but
    # didn't configure a VLM aren't surprised by image files becoming
    # ingest-eligible), (b) ``ocr_enabled`` is True, and (c)
    # ``enable_image`` is True.
    if vlm is not None and _flag("enable_image") and _flag("ocr_enabled") and not _is_noop_vlm(vlm):
        cls = _try_load("image", "ImageExtractor")
        if cls is not None:
            reg.register(cls(vlm=vlm))

    # ── Phase G (G-05/G-06) — Whisper-backed audio + video extractors ──
    # Registered only when a real (non-Noop) Whisper backend is wired
    # in. NoopWhisper is treated as "no transcription configured" so
    # users who haven't opted in to the ``[whisper]`` extra aren't
    # surprised by audio/video files becoming ingest-eligible.
    if whisper is not None and not _is_noop_whisper(whisper):
        audio_cls = _try_load("audio", "AudioExtractor")
        if audio_cls is not None:
            reg.register(audio_cls(whisper=whisper))
        video_cls = _try_load("video", "VideoExtractor")
        if video_cls is not None:
            reg.register(video_cls(whisper=whisper))

    return reg


def _is_noop_vlm(vlm: object) -> bool:
    """Return True if ``vlm`` is a :class:`NoopVLM` instance.

    ``corpus_forge.vlm.base`` is dependency-free (the heavy backends
    lazy-import ``requests`` inside their methods), so importing
    :class:`NoopVLM` here is safe even on a no-``[ocr]`` install.
    """
    # Defer the import to call-time so a circular-import accident in
    # downstream code surfaces at the function boundary rather than at
    # module load.
    from corpus_forge.vlm.base import NoopVLM  # noqa: PLC0415

    return isinstance(vlm, NoopVLM)


def _is_noop_whisper(whisper: object) -> bool:
    """Return True if ``whisper`` is a :class:`NoopWhisper` instance.

    ``corpus_forge.whisper.base`` is dependency-free (the heavy backends
    lazy-import ``faster_whisper`` / ``requests`` inside their methods),
    so importing :class:`NoopWhisper` here is safe even on a no-``[whisper]``
    install.
    """
    from corpus_forge.whisper.base import NoopWhisper  # noqa: PLC0415

    return isinstance(whisper, NoopWhisper)
