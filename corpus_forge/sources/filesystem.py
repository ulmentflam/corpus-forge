"""Generic filesystem source — D-14 (Wave 2 of the multi-format milestone).

Walks a heterogeneous tree under ``root`` and hands every file off to an
:class:`~corpus_forge.extractors.registry.ExtractorRegistry`. Selection of
the extractor honours the registry's two-pass dispatch:

1. Extension (case-insensitive).
2. Filename (``Makefile`` / ``Dockerfile`` / ``.gitignore`` etc.).

Extractor families gated by :class:`~corpus_forge.config.ExtractionConfig`
(``enable_pdf``, ``enable_office``, ``enable_code``, ...) are silently
skipped on a per-file basis — the registry itself is constructed
respecting these gates by :func:`register_default_extractors`, but we
**also** enforce per-extractor-class gating here so a caller-supplied
registry can't smuggle disabled families through.

Files larger than :attr:`ExtractionConfig.max_bytes` are skipped with a
WARNING log entry.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from corpus_forge.config import ExtractionConfig, ScanConfig
from corpus_forge.extractors.registry import ExtractorRegistry, register_default_extractors
from corpus_forge.ignore import CorpusIgnore, IgnoreStack
from corpus_forge.macos_tcc import download_if_evicted, is_iclouddrive_managed
from corpus_forge.sources.base import RawDocument, WatchedSource

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.vlm.base import VLMBackend
    from corpus_forge.whisper.base import WhisperBackend

logger = logging.getLogger(__name__)


# Maps **extractor class name** → ``ExtractionConfig`` field that gates
# it. Class-name lookup keeps the source decoupled from the concrete
# extractor classes (no heavy imports at module load) while still letting
# us refuse to invoke a disabled family even when the registry has it.
_FAMILY_FLAGS: dict[str, str] = {
    "PdfDigitalExtractor": "enable_pdf",
    "OfficeExtractor": "enable_office",
    "CodeExtractor": "enable_code",
    "HtmlExtractor": "enable_html",
    "EpubExtractor": "enable_epub",
    "NotebookExtractor": "enable_notebook",
    "CsvExtractor": "enable_csv",
    "ImageExtractor": "enable_image",
    # Phase G — Whisper extractors. There is no per-family flag on the
    # current ``ExtractionConfig``; gating happens via the global
    # ``config.whisper.backend`` (NoopWhisper short-circuits both
    # extractors). Listing them here is forward-compatible — if a future
    # ``enable_audio`` / ``enable_video`` flag lands, gating will Just
    # Work via the same path.
}


def _ignore_from_globs(globs: list[str], *, root: Path) -> IgnoreStack | None:
    """Translate `exclude_globs` into a gitignore-style :class:`IgnoreStack`.

    Phase M Wave 2 — adapter so :class:`FilesystemSource` can drive the
    unified `scanner.walker.walk` without dual maintenance of two
    exclude-matching code paths.

    Legacy semantics (the reference behaviour we must preserve):
    :func:`fnmatch.fnmatch` is applied to BOTH the full relative path
    AND each individual path component. ``fnmatch`` does NOT special-case
    ``/`` — so a pattern like ``cache/`` matches NOTHING (no real path
    has a trailing slash; no component equals ``cache/``).

    Translation rules:

    - ``pattern`` (no slash): pass through. gitignore's unanchored
      semantics match at any depth — covers BOTH the rel-path full-match
      and component-match cases legacy handled.
    - ``dir/**``: translates to ``dir/``, a gitignore directory prune.
    - ``dir/sub/file``: pass through. gitignore's path-with-slash rule
      anchors at the gitignore root — same as `fnmatch` on the relative
      path.
    - ``foo/`` (trailing slash, no ``**``): legacy no-op (fnmatch never
      matches), so we DROP it from the stack to preserve parity.
    - Empty / whitespace lines: skipped.

    Returns ``None`` when the resulting line list is empty so the
    walker can short-circuit the ignore-stack consultation entirely.
    """
    if not globs:
        return None

    lines: list[str] = []
    for raw in globs:
        if not raw or not raw.strip():
            continue
        pat = raw
        # `dir/**` → directory prune.
        if pat.endswith("/**"):
            lines.append(pat[:-3] + "/")
            continue
        # `foo/` (trailing slash but NOT `/**`) — legacy fnmatch never
        # matches this against any real path, so drop it. Without this,
        # we'd over-prune `cache/` style patterns that legacy left alone.
        if pat.endswith("/"):
            continue
        # Anything else — pass through.
        lines.append(pat)

    if not lines:
        return None

    return IgnoreStack(sets=(CorpusIgnore.from_lines(lines, root=root),))


class FilesystemSource(WatchedSource):
    """Generic walker over a heterogeneous directory tree.

    One source plugin handles every file format the extractor registry
    knows about — no per-format Source proliferation. The chunker
    selection happens **per-document** via :class:`ChunkerDispatcher`
    (D-05) based on ``ExtractedDocument.chunker_hint``.
    """

    name = "filesystem"
    dataset_kind = "text"

    def __init__(
        self,
        root: Path | str,
        *,
        exclude_globs: list[str] | None = None,
        extraction: ExtractionConfig | None = None,
        scan_config: ScanConfig | None = None,
        vlm: VLMBackend | None = None,
        whisper: WhisperBackend | None = None,
        debounce: float = 2.0,
        logical_name: str | None = None,
    ):
        super().__init__(Path(root), debounce=debounce)
        self.exclude_globs: list[str] = list(exclude_globs) if exclude_globs else []
        self.extraction: ExtractionConfig = extraction or ExtractionConfig()
        self.scan_config: ScanConfig = scan_config or ScanConfig()
        self.vlm = vlm
        self.whisper = whisper
        # Optional shared identifier for the same logical source across
        # multiple machines (multi-machine ingest). When set, drives the
        # `filesystem://logical/<name>` URI prefix in ``_source_uri_prefix_for``
        # so different absolute paths on different hosts converge on a
        # single ingest_run_sources row. See DatasetSourceConfig.logical_name.
        self.logical_name: str | None = logical_name
        # Registry baked with the user's tunables (csv_max_rows,
        # code_chunker_config, OCR knobs, VLM injection, Whisper
        # injection) at construction time so the hot path never re-reads
        # config.
        self._registry: ExtractorRegistry = register_default_extractors(
            self.extraction, vlm=vlm, whisper=whisper
        )

    # ── Discovery ──────────────────────────────────────────────────────

    def _registry_extensions(self) -> frozenset[str]:
        # Defensive: custom registries (tests use stubs) may not implement
        # `.extensions()`. In that case we yield no pre-stat filter — the
        # registry's per-file `get_for(path)` is the authoritative decision.
        getter = getattr(self._registry, "extensions", None)
        if getter is None:
            return frozenset()
        return frozenset(ext.lower() for ext in getter())

    def _registry_filenames(self) -> frozenset[str]:
        getter = getattr(self._registry, "filenames", None)
        if getter is None:
            return frozenset()
        return frozenset(getter())

    def discover(self) -> Iterator[Path]:
        """Walk ``root`` recursively yielding every regular file.

        Phase M Wave 2 — backed by `scanner.walker.walk` so the
        baseline-skip (`.git`, `node_modules`, ...) and ignore-driven
        prunes happen DURING descent rather than after.

        The ignore stack composes (in evaluation order):

        1. Global ``~/.config/corpus-forge/ignore`` (or
           ``CF_GLOBAL_IGNORE_FILE``) — user-machine defaults.
        2. Local ``<root>/.corpusignore`` — per-tree rules, including
           the Phase M Wave 1 managed block that setup writes.
        3. ``exclude_globs`` translated via :func:`_ignore_from_globs`
           — legacy per-source toml knob; later sets win on conflict.

        This is also the set ``estimate_sync_size`` and the
        ``corpus-forge ignore`` CLI/MCP surfaces operate on, so the
        three views can't drift.
        """
        from corpus_forge.ignore import (  # noqa: PLC0415
            IgnoreStack,
            load_global_ignore,
            load_local_ignore,
        )
        from corpus_forge.scanner import walk  # noqa: PLC0415
        from corpus_forge.scanner.walker import resolve_effective_workers  # noqa: PLC0415

        # Global first, then local — gitignore semantics: later set wins.
        sets = []
        try:
            global_set = load_global_ignore()
            if global_set.patterns:
                sets.append(global_set)
        except (OSError, ValueError) as exc:  # pragma: no cover — defensive
            logger.debug("FilesystemSource: global ignore failed (%s) — skipping", exc)
        try:
            local_set = load_local_ignore(self.root)
            if local_set.patterns:
                sets.append(local_set)
        except (OSError, ValueError) as exc:  # pragma: no cover — defensive
            logger.debug("FilesystemSource: local ignore failed (%s) — skipping", exc)

        glob_stack = _ignore_from_globs(self.exclude_globs, root=self.root)
        if glob_stack is not None:
            sets.extend(glob_stack.sets)

        ignore: IgnoreStack | None = IgnoreStack(sets=tuple(sets)) if sets else None
        # Restrict to extensions the registry actually handles plus the
        # extension-less filenames it supports (Makefile/Dockerfile/...).
        # Pre-stat short-circuit cost dominates for big trees.
        include_exts = self._registry_extensions() or None
        include_filenames = self._registry_filenames() or None

        _workers = resolve_effective_workers(self.scan_config.workers)
        for entry in walk(
            self.root,
            ignore=ignore,
            include_exts=include_exts,
            include_filenames=include_filenames,
            follow_symlinks=False,
            sort=True,
            workers=_workers,
        ):
            yield entry.path

    # ── Parsing ────────────────────────────────────────────────────────

    def parse(self, path: Path) -> RawDocument | None:
        """Dispatch ``path`` through the extractor registry.

        Returns None (and logs DEBUG) when:
        - No extractor is registered for the path.
        - The extractor's family flag is disabled.
        - The file exceeds ``ExtractionConfig.max_bytes`` (logs WARNING).
        """
        # max_bytes guard first — cheap stat call, avoids reading huge
        # files into memory only to throw them away.
        try:
            size = path.stat().st_size
        except OSError as exc:
            logger.debug("Cannot stat %s: %s — skipping", path, exc)
            return None
        if size > self.extraction.max_bytes:
            logger.warning(
                "Skipping oversized file %s (%d bytes > max_bytes %d)",
                path,
                size,
                self.extraction.max_bytes,
            )
            return None

        extractor = self._registry.get_for(path)
        if extractor is None:
            logger.debug("No extractor for %s — skipping", path)
            return None

        # Per-family gate. The registry was already built honouring the
        # gates, but a custom registry / direct registration could
        # bypass that — enforce here too.
        flag_name = _FAMILY_FLAGS.get(type(extractor).__name__)
        if flag_name is not None and not getattr(self.extraction, flag_name, True):
            logger.debug(
                "Extractor family %s disabled (%s=False) — skipping %s",
                type(extractor).__name__,
                flag_name,
                path,
            )
            return None

        # Proactive iCloud download: when ``path`` lives under
        # macOS's iCloud Drive mirror and is currently a cloud-only
        # placeholder, ask the iCloud daemon to materialise it before
        # the extractor reads it. macOS auto-downloads transparently
        # on ``open()`` when TCC is granted, but the explicit ``brctl
        # download`` hand-off makes a network hiccup on a metered
        # link surface as a clean ``Could not materialise…`` WARNING
        # instead of a wedged-extractor timeout further down the
        # pipeline. Best-effort: ``download_if_evicted`` is a no-op
        # on non-macOS hosts and a safe-fallback when ``brctl`` is
        # unavailable, so it's cheap to call unconditionally.
        if is_iclouddrive_managed(path) and not download_if_evicted(path):
            logger.warning(
                "Could not materialise iCloud placeholder %s — skipping "
                "(brctl download failed or file still evicted)",
                path,
            )
            return None

        try:
            extracted = extractor.extract(path)
        except Exception as exc:
            logger.warning("Extractor %s failed on %s: %s", type(extractor).__name__, path, exc)
            return None

        # Phase G: AudioExtractor / VideoExtractor may return ``None`` to
        # signal "no transcription backend configured — skip this file
        # silently". Treat that the same as "no extractor matched".
        if extracted is None:
            logger.debug(
                "Extractor %s returned None for %s — skipping",
                type(extractor).__name__,
                path,
            )
            return None

        # Build metadata: extractor's free-form dict + reserved keys
        # (chunker_hint, language) set by the source layer.
        metadata: dict = dict(extracted.metadata)
        metadata["chunker_hint"] = extracted.chunker_hint
        if extracted.language is not None:
            metadata["language"] = extracted.language

        title = self._title_for(path, extracted.text, extracted.chunker_hint)

        try:
            modified_at = path.stat().st_mtime
        except OSError:
            modified_at = 0.0

        # ``file_content_hash`` reopens the file to read the bytes. On
        # iCloud / network mounts the file can be evicted between
        # extraction and hashing (Apple's iCloud daemon turns
        # rarely-accessed files into placeholders mid-scan); the
        # extractor already handles this gracefully so we mirror that
        # behaviour here instead of letting a vanished file crash the
        # whole ingest pass. Logged as WARNING so the operator sees
        # the same shape of message the extractor emits.
        try:
            content_hash_value = self.file_content_hash(path)
        except (FileNotFoundError, OSError) as exc:
            logger.warning(
                "Could not hash %s after extraction (likely iCloud / network "
                "eviction between read and hash): %s — skipping",
                path,
                exc,
            )
            return None

        return RawDocument(
            source_uri=f"filesystem://{self.root.name}/{path.relative_to(self.root)}",
            content_hash=content_hash_value,
            text=extracted.text,
            title=title,
            modified_at=modified_at,
            metadata=metadata,
            labels=list(extracted.labels),
        )

    # ── Helpers ────────────────────────────────────────────────────────

    def _title_for(self, path: Path, text: str, chunker_hint: str) -> str:
        """Pick a human-friendly title for a RawDocument.

        - ``chunker_hint == "markdown"`` — first ``# `` heading, else stem.
        - ``chunker_hint == "code"`` — relative path from ``root``.
        - Otherwise — stem.
        """
        if chunker_hint == "markdown":
            first_line = text.split("\n", 1)[0] if text else ""
            if first_line.startswith("# "):
                return first_line[2:].strip()
            return path.stem
        if chunker_hint == "code":
            try:
                return str(path.relative_to(self.root))
            except ValueError:
                return path.name
        return path.stem
