"""Phase J / J1 — sync storage estimator.

Answers "what will it cost in Postgres to sync this folder?" *without*
actually syncing. Pure prediction; ignores existing rows.

Sizing model (verbatim from the J1 brief — see
``.planning/tdd/phase_j_living_corpus.md`` § "Sizing model"):

1. **Document row** — heap overhead + ``content_hash`` (64 B) +
   ``source_uri`` length + a text-proxy attributed to ``documents.text``
   for the head/preview Postgres stores inline.
2. **Chunk rows** — per chunk, heap overhead + ``content_hash`` (64 B) +
   ``mean_chunk_text_bytes`` (per-extractor heuristic) + small allowance
   for ``metadata JSON`` / ``heading``.
3. **Embedding rows** -- per active embedder,
   ``n_chunks * (dim * 4 + 32 B row overhead)``.
4. **HNSW overhead** -- empirical multiplier
   ``n_chunks * dim * 4 * 0.35`` per embedder (pgvector HNSW averages
   25-40 % over raw vector size for ``m=16``).
5. **Btree indexes** -- ``documents_hash_idx``,
   ``chunks_content_hash_idx`` roughly ``n_rows * 80 B`` each.

The estimator is a PURE FUNCTION over ``(path, Config)`` — no backend
opens, no extractor instantiation, no HTTP, no model calls. It consults
the extractor registry only as a constants lookup (the
``_LANG_BY_EXT`` / ``_SUPPORTED_FILENAMES`` tables in
``corpus_forge.extractors.code``) so heavy optional backends stay
unimported.

The dataclass shape is JSON-serialisable via ``dataclasses.asdict`` —
both the CLI ``--json`` mode and the MCP ``estimate_sync_size`` tool
return it verbatim under a stable ``schema_version = 1`` contract.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.config import Config
    from corpus_forge.ignore import IgnoreStack

logger = logging.getLogger(__name__)

# Phase L Wave 4 — taxonomy logger documented in
# ``.planning/tdd/phase_l_cli_ux.md`` §2. Carries the scan progress
# bookends emitted by the shared ``make_progress`` factory.
scan_logger = logging.getLogger("corpus_forge.estimate.scan")


# Module-level cache so callers that ran ``estimate_sync`` can retrieve
# the most recent :class:`ScanStats` without re-walking the tree. The
# CLI uses this; tests reset it via :func:`get_last_scan_stats`.
_LAST_SCAN_STATS: ScanStats | None = None


def get_last_scan_stats() -> ScanStats | None:
    """Return the most recent :class:`ScanStats` captured by ``_walk``.

    Phase L Wave 4 — lets ``corpus-forge estimate`` render the new
    "Scan stats" table without paying for a second walk. Returns
    ``None`` if no walk has run since import.
    """
    return _LAST_SCAN_STATS


# ─────────────────────────────────────────────────────────────────────────
# Sizing constants
# ─────────────────────────────────────────────────────────────────────────

# Per-row Postgres heap-tuple header (approximate; ignores per-column null
# bitmap, which is negligible for our row widths). Used for both document
# and chunk row sizing.
_HEAP_ROW_OVERHEAD = 28

# Content-hash column width (64 hex chars stored as TEXT, plus the
# variable-length header — round to 64 for the sizing estimate).
_CONTENT_HASH_BYTES = 64

# Small allowance for the metadata-JSON / heading columns on each chunk
# row. Most prose chunks carry an empty metadata dict and no heading; the
# 64-byte allowance covers the ``{"chunker_hint": ...}`` minimum we
# observe in practice.
_CHUNK_META_ALLOWANCE = 64

# Per-embedding row overhead (above the raw vector bytes). pgvector stores
# vectors in a `vector(dim)` column; the row header + per-column null
# bitmap + dataset_id / chunk_id FK columns amortise to ~32 B in our
# production schema.
_EMBEDDING_ROW_OVERHEAD = 32

# pgvector HNSW overhead, empirically observed at ``m=16`` to range
# 25-40 % above raw vector size. We use the midpoint 35 % as the sizing
# multiplier.
_HNSW_MULTIPLIER = 0.35

# Approximate per-row width of a btree index entry. 80 B is the
# rule-of-thumb for fixed-width keys + the heap-tid pointer + page
# overhead amortisation.
_BTREE_ROW_BYTES = 80


# ─────────────────────────────────────────────────────────────────────────
# Per-extractor heuristic table
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExtractorHeuristic:
    """Per-extractor sizing heuristic.

    ``extensions`` is the lowercase, dotted list of file extensions
    routed to this class. ``mean_chunk_text_bytes`` is the
    post-extraction average size that lands in ``chunks.text``.

    These constants ship as starting points; the CLI ``--compression-ratio``
    flag and the ``[estimate]`` config block let users tune per-deployment
    without code edits.
    """

    extractor_class: str
    extensions: tuple[str, ...]
    mean_chunk_text_bytes: int


def _collect_code_extensions() -> tuple[str, ...]:
    """Pull code-extractor extensions from
    :data:`corpus_forge.extractors.code._LANG_BY_EXT`.

    Single source of truth: when a new language ships its grammar in the
    code extractor, the estimator picks it up automatically.
    """
    from corpus_forge.extractors.code import _LANG_BY_EXT  # noqa: PLC0415

    return tuple(sorted(_LANG_BY_EXT.keys()))


def _code_filenames() -> tuple[str, ...]:
    """Filename second-pass fallback for the code extractor.

    Mirrors :data:`corpus_forge.extractors.code._SUPPORTED_FILENAMES`
    so the estimator routes ``Makefile`` / ``Dockerfile`` / dotfiles
    into the ``code`` class even when they have no extension.
    """
    from corpus_forge.extractors.code import _SUPPORTED_FILENAMES  # noqa: PLC0415

    return _SUPPORTED_FILENAMES


def _build_heuristics() -> tuple[ExtractorHeuristic, ...]:
    """Construct the static heuristic table.

    Built as a function (rather than a module-level literal) so the lazy
    import of :data:`corpus_forge.extractors.code._LANG_BY_EXT` happens
    on first use, not at module import.
    """
    return (
        ExtractorHeuristic(
            "markdown",
            (
                ".md",
                ".markdown",
                ".txt",
                ".rst",
                ".log",
                ".tex",
                ".html",
                ".htm",
                ".xhtml",
                ".epub",
                ".docx",
                ".pptx",
                ".xlsx",
            ),
            4096,
        ),
        ExtractorHeuristic("pdf", (".pdf",), 4096),
        ExtractorHeuristic("code", _collect_code_extensions(), 1920),
        ExtractorHeuristic("notebook", (".ipynb",), 4096),
        ExtractorHeuristic("csv", (".csv", ".tsv"), 4096),
        ExtractorHeuristic("structured", (".json", ".yaml", ".yml", ".toml"), 4096),
        ExtractorHeuristic("subtitle", (".srt", ".vtt"), 6144),
        ExtractorHeuristic(
            "image",
            (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic"),
            0,
        ),
        ExtractorHeuristic(
            "audio_video",
            (
                ".mp3",
                ".wav",
                ".m4a",
                ".ogg",
                ".flac",
                ".mp4",
                ".mov",
                ".webm",
                ".mkv",
                ".avi",
            ),
            6144,
        ),
    )


# Module-level cached heuristic table — built once on first use.
_HEURISTICS: tuple[ExtractorHeuristic, ...] | None = None


def _heuristics() -> tuple[ExtractorHeuristic, ...]:
    global _HEURISTICS  # noqa: PLW0603 — module-level cache
    if _HEURISTICS is None:
        _HEURISTICS = _build_heuristics()
    return _HEURISTICS


# Module-level reverse-index caches. Built lazily on first use from
# `_heuristics()`. Replaces the linear scan in the old `_classify_extension`
# (Phase M Wave 2 — every file pays the lookup cost on the hot path).
_EXT_TO_CLASS: dict[str, str] | None = None
_FILENAME_TO_CLASS: dict[str, str] | None = None


def _ext_to_class() -> dict[str, str]:
    """Lazy reverse-index: lowercase extension → extractor class name."""
    global _EXT_TO_CLASS  # noqa: PLW0603 — module-level cache
    if _EXT_TO_CLASS is None:
        idx: dict[str, str] = {}
        for h in _heuristics():
            for ext in h.extensions:
                idx[ext] = h.extractor_class
        _EXT_TO_CLASS = idx
    return _EXT_TO_CLASS


def _filename_to_class() -> dict[str, str]:
    """Lazy reverse-index: extension-less filename → extractor class.

    Currently only the `code` extractor declares filename support
    (``Makefile`` / ``Dockerfile`` / ``.gitignore`` etc.). The map is
    built once and reused.
    """
    global _FILENAME_TO_CLASS  # noqa: PLW0603 — module-level cache
    if _FILENAME_TO_CLASS is None:
        idx: dict[str, str] = {}
        for name in _code_filenames():
            idx[name] = "code"
        _FILENAME_TO_CLASS = idx
    return _FILENAME_TO_CLASS


def _classify_extension(suffix_lower: str) -> str | None:
    """Return the extractor-class for ``suffix_lower`` or ``None``."""
    return _ext_to_class().get(suffix_lower)


def _heuristic_for(extractor_class: str) -> ExtractorHeuristic | None:
    for h in _heuristics():
        if h.extractor_class == extractor_class:
            return h
    return None


# Cached union of every extractor's `supported_extensions` plus every
# heuristic-table extension. Used by the walker as `include_exts` to
# short-circuit non-text files BEFORE `entry.stat()`.
_FULL_EXT_INDEX: frozenset[str] | None = None


def _full_ext_index() -> frozenset[str]:
    """Lazy union of registry + heuristic-table extensions.

    Phase M Wave 2 — seeds `scanner.walker.walk(include_exts=...)`. The
    set is intentionally a SUPERSET of what `_ext_to_class` resolves to:
    extractors that ship without a heuristic entry (e.g. some Wave-1
    additions) still want their files counted toward `unknown` even
    though they never reach the per-class accountant.
    """
    global _FULL_EXT_INDEX  # noqa: PLW0603 — module-level cache
    if _FULL_EXT_INDEX is None:
        merged: set[str] = set()
        # Heuristic-table extensions.
        for h in _heuristics():
            for ext in h.extensions:
                merged.add(ext)
        # Registry extensions (caps any gap the heuristic table missed).
        try:
            from corpus_forge.extractors.registry import (  # noqa: PLC0415
                register_default_extractors,
            )

            reg = register_default_extractors(None)
            for ext in reg.extensions():
                merged.add(ext.lower())
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("estimate: registry extension scan failed: %s", exc)
        _FULL_EXT_INDEX = frozenset(merged)
    return _FULL_EXT_INDEX


# Directory names skipped wholesale during the walk. Tool / cache / VCS
# noise nobody ingests intentionally — kept tight on purpose.
#
# ``build`` and ``dist`` are intentionally NOT in this set: in many
# ecosystems (CMake / Bazel / Make / Java projects) they hold
# hand-authored source (Makefile, Dockerfile, BUILD.bazel,
# .editorconfig) that users do want indexed. Compiled outputs that DO
# live under those names (``*.o``, ``*.so``, ``*.dll``, ``*.class``,
# ``*.exe``, ``*.jar``, …) get filtered automatically by the walker's
# extension short-circuit because no extractor registers those
# extensions. Users who want the entire ``build/`` / ``dist/`` tree
# skipped get it from Phase M Wave 1's managed ``.corpusignore``
# ``_ALWAYS_ON`` block (``build/``, ``dist/``, ``out/``, ``target/`` …).
_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".direnv",
        ".cache",
        ".idea",
        ".vscode",
    }
)


# Per-file names skipped wholesale (macOS noise + finder metadata).
_SKIP_FILE_NAMES = frozenset({".DS_Store"})


def _should_skip_dir(name: str) -> bool:
    return name in _SKIP_DIR_NAMES


def _should_skip_file(name: str) -> bool:
    if name in _SKIP_FILE_NAMES:
        return True
    # macOS ._-prefixed AppleDouble metadata files.
    return bool(name.startswith("._"))


# ─────────────────────────────────────────────────────────────────────────
# Public dataclasses
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExtractorClassSummary:
    """Per-extractor-class roll-up surfaced by :class:`SyncEstimate`."""

    extractor_class: str
    file_count: int
    raw_bytes: int
    est_chunks: int


@dataclass(frozen=True)
class EmbedderSizing:
    """Per-embedder embedding-table sizing breakdown."""

    name: str
    dim: int
    n_chunks: int
    raw_vector_bytes: int
    hnsw_overhead_bytes: int
    row_overhead_bytes: int
    total_bytes: int


@dataclass(frozen=True)
class ScanStats:
    """Scan timing + throughput surfaced alongside :class:`SyncEstimate`.

    Phase L Wave 4 — kept as a SIBLING dataclass (not nested into the
    wire-stable :class:`SyncEstimate`) so the MCP ``estimate_sync_size``
    tool's JSON shape stays unchanged. The CLI ``estimate`` command
    renders this in a new "Scan stats" table; ``--json`` mode emits the
    payload under a top-level ``"scan"`` sibling key.
    """

    elapsed_s: float
    scan_rate: float
    file_count: int
    dir_count: int


@dataclass(frozen=True)
class SyncEstimate:
    """Result of :func:`estimate_sync`.

    JSON-serialisable via :func:`dataclasses.asdict`. ``schema_version``
    is the stable contract version exposed to MCP callers + ``--json``
    consumers; bump it on any breaking shape change.
    """

    schema_version: int
    scanned_path: str
    file_count: int
    dir_count: int
    total_raw_bytes: int
    by_extractor: list[ExtractorClassSummary] = field(default_factory=list)
    documents_bytes: int = 0
    chunks_bytes: int = 0
    embeddings: list[EmbedderSizing] = field(default_factory=list)
    btree_index_bytes: int = 0
    total_bytes: int = 0
    compression_ratio: float = 1.0
    embedders_active: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# Chunk-count heuristics
# ─────────────────────────────────────────────────────────────────────────


def _est_chunks(extractor_class: str, file_bytes: int) -> int:
    """Pure-function chunk-count estimate for a single file.

    Formulas mirror the J1 brief's "Per-extractor heuristics (initial
    table)". Minimum of 1 chunk applied to all "produces a chunk" classes
    so tiny files don't round to zero.
    """
    if extractor_class == "markdown":
        return max(0, math.ceil(file_bytes / 4096))
    if extractor_class == "pdf":
        # ceil(text_bytes / 4096) * 1.05 -- page-break overhead, then
        # minimum 1 chunk per non-empty PDF.
        return max(1, math.ceil((file_bytes / 4096) * 1.05))
    if extractor_class == "code":
        # LOC ~= file_bytes / 32; 1 chunk per ~60 LOC.
        return max(1, math.ceil((file_bytes / 32) / 60))
    if extractor_class == "notebook":
        return max(1, math.ceil(file_bytes / 8192))
    if extractor_class == "csv":
        return 1
    if extractor_class == "structured":
        return 1
    if extractor_class == "subtitle":
        return max(1, math.ceil(file_bytes / 6144))
    if extractor_class == "image":
        # CLIP image lane only -- no text chunk in J1's scope.
        return 0
    if extractor_class == "audio_video":
        # ~ 30 s cues * 60 s/MiB heuristic; minimum 1 chunk per non-empty
        # file so a 100 KiB clip doesn't round to zero.
        mb = file_bytes / (1024 * 1024)
        return max(1, math.ceil(mb * 60 / 30))
    # "unknown" -> 0 chunks contributed.
    return 0


# ─────────────────────────────────────────────────────────────────────────
# Filesystem walk
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class _ClassBucket:
    """Mutable accumulator used during the walk; frozen into
    :class:`ExtractorClassSummary` at the end."""

    file_count: int = 0
    raw_bytes: int = 0
    est_chunks: int = 0


def _walk(
    root: Path,
    *,
    ignore: IgnoreStack | None = None,
    workers: int = 1,
) -> tuple[dict[str, _ClassBucket], int, int, int, ScanStats]:
    """Walk ``root`` and bucket every file into an extractor class.

    Phase M Wave 2 — the body now delegates to
    :func:`corpus_forge.scanner.walker.walk`, which prunes baseline +
    ignore-driven subtrees DURING descent (no `is_dir`/`is_symlink` cost
    on pruned subtrees) and short-circuits non-corpus extensions BEFORE
    `entry.stat()`. The hard-coded ``_SKIP_DIR_NAMES`` /
    ``_SKIP_FILE_NAMES`` baseline still applies; the ``IgnoreStack`` is
    consulted *after* the baseline so a ``!`` negation in any
    ``.corpusignore`` cannot un-skip a baseline entry.

    Returns ``(buckets_by_class, file_count, dir_count, total_raw_bytes, scan_stats)``.

    Phase L Wave 4 — the inner loop is wrapped in
    :func:`corpus_forge.ui.progress.make_progress` (unbounded mode) with
    the ``corpus_forge.estimate.scan`` logger so users see live motion
    on a TTY and the rotating log captures the start/complete bookends
    even for long walks.
    """
    from corpus_forge.scanner import WalkStats, walk  # noqa: PLC0415
    from corpus_forge.ui.progress import make_progress  # noqa: PLC0415

    buckets: dict[str, _ClassBucket] = {}
    file_count = 0
    total_raw_bytes = 0

    walk_stats = WalkStats()
    started = time.perf_counter()

    include_exts = _full_ext_index()
    include_filenames = frozenset(_filename_to_class().keys())
    ext_index = _ext_to_class()
    name_index = _filename_to_class()

    with make_progress("Scanning", total=None, logger=scan_logger) as progress:
        task = progress.add_task("Scanning", total=None)
        for entry in walk(
            root,
            ignore=ignore,
            include_exts=include_exts,
            include_filenames=include_filenames,
            follow_symlinks=False,
            sort=True,
            stats=walk_stats,
            workers=workers,
        ):
            name = entry.path.name
            # Suffix the way `pathlib.Path.suffix` would. `entry.path`
            # is a real `Path`, but we already paid for the name above
            # so re-do the cheap rfind.
            last_dot = name.rfind(".")
            suffix = name[last_dot:].lower() if last_dot > 0 else ""
            extractor_class = ext_index.get(suffix)
            if extractor_class is None:
                extractor_class = name_index.get(name, "unknown")

            size = entry.stat.st_size
            if size > 2 * 1024 * 1024 * 1024:  # >2 GiB
                logger.debug(
                    "estimator: large file %s (%d bytes) — still counted",
                    entry.path,
                    size,
                )

            bucket = buckets.setdefault(extractor_class, _ClassBucket())
            bucket.file_count += 1
            bucket.raw_bytes += size
            bucket.est_chunks += _est_chunks(extractor_class, size)
            file_count += 1
            total_raw_bytes += size
            progress.update(task, advance=1)

    dir_count = walk_stats.dirs_descended
    elapsed_s = max(time.perf_counter() - started, 0.0)
    scan_rate = (file_count / elapsed_s) if elapsed_s > 0 else 0.0
    stats = ScanStats(
        elapsed_s=elapsed_s,
        scan_rate=scan_rate,
        file_count=file_count,
        dir_count=dir_count,
    )

    # Stash for the CLI's "Scan stats" panel — avoids a second walk.
    global _LAST_SCAN_STATS  # noqa: PLW0603 — module-level cache, intentional
    _LAST_SCAN_STATS = stats

    # Wall-clock calibration — fold the scan rate into the on-disk
    # runtime profile so future ``corpus-forge estimate`` invocations
    # blend the live filesystem speed in instead of the heuristic
    # constant. Best-effort; profile-write failures are swallowed inside
    # ``record``.
    if file_count > 0 and elapsed_s > 0:
        try:
            from corpus_forge.runtime_profile import record as _record  # noqa: PLC0415

            _record("scan", units=file_count, seconds=elapsed_s)
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("estimate: scan calibration write failed: %s", exc)

    return buckets, file_count, dir_count, total_raw_bytes, stats


def walk_with_stats(
    root: Path,
    *,
    ignore: IgnoreStack | None = None,
    workers: int = 1,
) -> tuple[dict[str, _ClassBucket], int, int, int, ScanStats]:
    """Public 5-tuple-returning variant of :func:`_walk`.

    Phase L Wave 4 — gives CLI and test sites access to the
    :class:`ScanStats` without having to re-walk. Same contract as
    :func:`_walk`; exported in ``__all__`` for stability.
    """
    return _walk(root, ignore=ignore, workers=workers)


# ─────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────


def estimate_sync(
    path: str | Path,
    config: Config,
    *,
    embedders: list[str] | None = None,
    compression_ratio: float | None = None,
    ignore: IgnoreStack | None = None,
) -> SyncEstimate:
    """Estimate the Postgres storage footprint of syncing ``path``.

    Pure-prediction — no backend access, no extractor instantiation, no
    HTTP / model calls. Consults the extractor registry as a constants
    lookup only.

    Args:
        path: Filesystem root to scan (recursive). Resolved to an
            absolute path; relative inputs are accepted.
        config: A loaded :class:`corpus_forge.config.Config`. Drives the
            embedder list, default compression ratio (from
            ``config.estimate.compression_ratio``), and (later) any
            per-deployment heuristic overrides.
        embedders: Optional explicit list of embedder names to count. By
            default every embedder with ``active=True`` is summed.
            Passing an unknown name raises :class:`ValueError`.
        compression_ratio: Optional override for
            ``config.estimate.compression_ratio``. Applied to the
            text-heavy column estimates only — embeddings + indexes are
            unaffected.

    Returns:
        :class:`SyncEstimate` — JSON-serialisable, frozen dataclass.

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        NotADirectoryError: if ``path`` is a file (estimator is
            directory-only).
        ValueError: if ``embedders`` names an unknown embedder.
    """
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"estimate path does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"estimate path is not a directory: {root}")

    # Compression ratio resolution: explicit arg wins over
    # ``config.estimate.compression_ratio``; the config field defaults
    # to 1.0 so a Config without an explicit ``[estimate]`` block sees
    # the no-compression baseline.
    effective_ratio = (
        compression_ratio
        if compression_ratio is not None
        else getattr(getattr(config, "estimate", None), "compression_ratio", 1.0)
    )

    # Embedder selection. Default = every active embedder; explicit
    # filter validates names against the config.
    configured_names = [e.name for e in config.embedders]
    if embedders is None:
        chosen = [e for e in config.embedders if getattr(e, "active", True)]
    else:
        wanted = list(embedders)
        unknown = [name for name in wanted if name not in configured_names]
        if unknown:
            raise ValueError(f"unknown embedder: {unknown!r}; configured: {configured_names!r}")
        by_name = {e.name: e for e in config.embedders}
        chosen = [by_name[name] for name in wanted]

    from corpus_forge.scanner.walker import resolve_effective_workers  # noqa: PLC0415

    _scan_workers = resolve_effective_workers(
        getattr(getattr(config, "scan", None), "workers", None)
    )
    buckets, file_count, dir_count, total_raw_bytes, _scan_stats = _walk(
        root, ignore=ignore, workers=_scan_workers
    )

    # Stable ordering for the per-class roll-up — match the heuristic
    # table order so tests + CLI tables are deterministic.
    by_extractor: list[ExtractorClassSummary] = []
    ordering = [h.extractor_class for h in _heuristics()] + ["unknown"]
    for cls in ordering:
        b = buckets.get(cls)
        if b is None:
            continue
        by_extractor.append(
            ExtractorClassSummary(
                extractor_class=cls,
                file_count=b.file_count,
                raw_bytes=b.raw_bytes,
                est_chunks=b.est_chunks,
            )
        )

    # ── per-row text-byte attribution ────────────────────────────────
    documents_bytes = 0
    chunks_bytes = 0
    total_chunks = 0
    for summary in by_extractor:
        h = _heuristic_for(summary.extractor_class)
        mean_chunk_text_bytes = h.mean_chunk_text_bytes if h is not None else 0
        # Document text proxy: best estimate of the bytes Postgres
        # actually stores on ``documents.text`` is the same as the
        # extracted-text body (which is ``est_chunks * mean_chunk_text``
        # before chunking). For image / unknown classes the proxy is 0.
        doc_text_bytes = summary.est_chunks * mean_chunk_text_bytes
        # Per-document row overhead, applied to every file (one
        # documents row per file).
        per_doc_overhead = (
            _HEAP_ROW_OVERHEAD
            + _CONTENT_HASH_BYTES
            + 0  # source_uri attributed at chunk granularity — keep doc estimate tight
        )
        documents_bytes += summary.file_count * per_doc_overhead + int(
            doc_text_bytes * effective_ratio
        )

        # Chunk-row sizing: overhead * n_chunks + compressed text bytes.
        per_chunk_overhead = _HEAP_ROW_OVERHEAD + _CONTENT_HASH_BYTES + _CHUNK_META_ALLOWANCE
        chunks_bytes += summary.est_chunks * per_chunk_overhead + int(
            summary.est_chunks * mean_chunk_text_bytes * effective_ratio
        )
        total_chunks += summary.est_chunks

    # ── embedding-row sizing per embedder ────────────────────────────
    embeddings: list[EmbedderSizing] = []
    for e_cfg in chosen:
        dim = int(getattr(e_cfg, "dimension", 0))
        raw_vector_bytes = total_chunks * dim * 4
        row_overhead_bytes = total_chunks * _EMBEDDING_ROW_OVERHEAD
        hnsw_overhead_bytes = round(raw_vector_bytes * _HNSW_MULTIPLIER)
        total = raw_vector_bytes + row_overhead_bytes + hnsw_overhead_bytes
        embeddings.append(
            EmbedderSizing(
                name=e_cfg.name,
                dim=dim,
                n_chunks=total_chunks,
                raw_vector_bytes=raw_vector_bytes,
                hnsw_overhead_bytes=hnsw_overhead_bytes,
                row_overhead_bytes=row_overhead_bytes,
                total_bytes=total,
            )
        )

    # ── btree-index sizing (documents + chunks) ──────────────────────
    btree_index_bytes = (file_count + total_chunks) * _BTREE_ROW_BYTES

    total_bytes = (
        documents_bytes + chunks_bytes + sum(e.total_bytes for e in embeddings) + btree_index_bytes
    )

    return SyncEstimate(
        schema_version=1,
        scanned_path=str(root),
        file_count=file_count,
        dir_count=dir_count,
        total_raw_bytes=total_raw_bytes,
        by_extractor=by_extractor,
        documents_bytes=documents_bytes,
        chunks_bytes=chunks_bytes,
        embeddings=embeddings,
        btree_index_bytes=btree_index_bytes,
        total_bytes=total_bytes,
        compression_ratio=effective_ratio,
        embedders_active=[e.name for e in chosen],
    )


__all__ = [
    "EmbedderSizing",
    "ExtractorClassSummary",
    "ExtractorHeuristic",
    "ScanStats",
    "SyncEstimate",
    "estimate_sync",
    "get_last_scan_stats",
    "walk_with_stats",
]
