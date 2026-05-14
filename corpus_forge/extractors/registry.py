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
    """Dispatch table from file extension to :class:`Extractor`."""

    def __init__(self) -> None:
        self._by_ext: dict[str, Extractor] = {}

    def register(self, extractor: Extractor) -> None:
        """Register ``extractor`` for each of its supported extensions.

        Last-write-wins: registering for an already-bound extension
        replaces the previous entry. Extensions are normalised to
        lowercase so callers do not need to be careful about case.

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

    def get_for(self, path: Path) -> Extractor | None:
        """Return the extractor for ``path`` or ``None`` if unsupported."""
        return self._by_ext.get(path.suffix.lower())

    def extensions(self) -> Iterable[str]:
        """Return every registered (lowercase) extension."""
        return list(self._by_ext.keys())


def register_default_extractors(config: object | None) -> ExtractorRegistry:
    """Construct a fully-wired :class:`ExtractorRegistry` from ``config``.

    ``config`` is duck-typed against the ``enable_*`` flags exposed by
    :class:`corpus_forge.config.ExtractionConfig` (D-06). When ``config``
    is ``None`` every flag defaults to ``True``. Importantly, when a
    family is disabled, its concrete extractor module is **never
    imported** — heavy optional deps stay out of the import graph.

    The default registry is populated incrementally as later Wave 0/1
    tasks land:

    - Wave 0 (D-03): passthrough / plaintext.
    - Wave 0 (D-04): structured / subtitle.
    - Wave 1 (D-07..D-13): pdf / html / epub / office / notebook / csv /
      code.

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
            reg.register(cls())

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
            reg.register(cls())

    if _flag("enable_code"):
        cls = _try_load("code", "CodeExtractor")
        if cls is not None:
            reg.register(cls())

    return reg
