"""Wall-clock time estimator for the corpus-forge ingest pipeline.

Sibling to :mod:`corpus_forge.estimate` (which predicts *storage*). Given
a :class:`~corpus_forge.estimate.SyncEstimate` and the active
:class:`~corpus_forge.config.Config`, this module predicts how long the
full ingest will take, broken down per phase: ``scan`` ➜ ``extract`` ➜
``chunk`` ➜ ``embed`` ➜ ``db_write``.

Two data sources feed the estimate, blended in this order:

1. **Calibration profile** (``corpus_forge.runtime_profile``) — rolling
   EWMA of *measured* per-phase throughput from past ``ingest``/``embed``
   runs on this host. When a rate is present in the profile, it wins.
2. **Heuristic fallback** — a static per-extractor / per-embedder
   constants table tuned from a modern dev machine. Always available;
   ships ~±50% accurate on a cold install and is gradually displaced by
   profile data as real runs accumulate.

The estimator is a PURE FUNCTION over ``(SyncEstimate, Config, profile)`` —
no backend access, no model calls. The returned :class:`TimeEstimate` is
JSON-serialisable via :func:`dataclasses.asdict` under a stable
``schema_version = 1`` contract.

Surfaces:

- ``corpus-forge estimate`` CLI: human "Estimated wall-clock" table and
  ``--json`` ``time:`` key.
- MCP ``estimate_sync_size`` tool: ``time:`` block alongside ``estimate:``.
- ``corpus-forge ingest --once``: one-line ETA log at startup.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.config import Config
    from corpus_forge.estimate import SyncEstimate
    from corpus_forge.runtime_profile import RuntimeProfile

#: Stable on-the-wire schema version for :class:`TimeEstimate`.
SCHEMA_VERSION = 1

# ─────────────────────────────────────────────────────────────────────────
# Heuristic constants
# ─────────────────────────────────────────────────────────────────────────
#
# These are first-install fallbacks. The profile is preferred whenever a
# matching key is present. Sources: rough measurements on a 2025 desktop
# (modern NVMe SSD, RTX-class GPU for embeddings). Tune via the profile,
# not by editing this table — calibration is the supported knob.

#: Filesystem scan rate — files per second. ``walker.walk`` is
#: ``stat``-bound on a warm cache; on a cold cache it drops by ~10x but
#: that's a one-time hit we don't bother modelling.
_DEFAULT_SCAN_SEC_PER_FILE = 1.0e-4  # ~10 k files/sec

#: Per-extractor-class extract throughput, in seconds-per-byte of raw
#: input. PDF and audio/video dominate; markdown and code are effectively
#: free relative to embedding.
_DEFAULT_EXTRACT_SEC_PER_BYTE: dict[str, float] = {
    "markdown": 2.0e-8,   # ~50 MB/s
    "pdf": 1.0e-6,        # ~1 MB/s (digital PDFs; OCR escalation is slower)
    "code": 5.0e-8,       # ~20 MB/s (tree-sitter is moderately fast)
    "notebook": 1.0e-7,   # ~10 MB/s (JSON parse + cell unwrap)
    "csv": 3.3e-8,        # ~30 MB/s
    "structured": 3.3e-8, # ~30 MB/s
    "subtitle": 5.0e-8,   # ~20 MB/s
    "image": 5.0e-7,      # ~2 MB/s (decode + optional OCR fast-path)
    "audio_video": 1.0e-5, # ~100 KB/s (whisper dominates — VERY rough)
    "unknown": 0.0,
}

#: Per-extractor-class chunking cost, in seconds-per-chunk. Tree-sitter
#: code chunking is the slow one; everything else is comparable.
_DEFAULT_CHUNK_SEC_PER_CHUNK: dict[str, float] = {
    "markdown": 2.0e-4,
    "pdf": 2.0e-4,
    "code": 5.0e-4,       # tree-sitter AST descent
    "notebook": 2.0e-4,
    "csv": 2.0e-4,
    "structured": 2.0e-4,
    "subtitle": 2.0e-4,
    "image": 0.0,         # image lane doesn't chunk text
    "audio_video": 2.0e-4,
    "unknown": 0.0,
}

#: Default per-embedder seconds-per-chunk. Scaled by dimension because
#: bigger vectors mean more flops per encode call (rough linear proxy).
#: ``qwen3_8b`` at 4096-dim batches at ~25 chunks/sec on consumer GPUs;
#: ``bge-small`` at 384-dim batches at ~200/sec. Scaled from a 768-dim
#: anchor of 25 ms/chunk.
_EMBED_ANCHOR_DIM = 768
_EMBED_ANCHOR_SEC_PER_CHUNK = 0.025


def _default_embed_sec_per_chunk(dim: int) -> float:
    """Heuristic per-embedder seconds-per-chunk, scaled by dimension."""
    if dim <= 0:
        return _EMBED_ANCHOR_SEC_PER_CHUNK
    # Sub-linear scaling: doubling dim doesn't quite double the cost
    # (batching amortises some of it), so use a 0.8 exponent.
    ratio = (dim / _EMBED_ANCHOR_DIM) ** 0.8
    return _EMBED_ANCHOR_SEC_PER_CHUNK * ratio


#: Postgres write throughput per chunk — measured against a local Postgres
#: on the same host. Network-attached / remote Postgres will be slower; the
#: profile picks that up automatically after a real run.
_DEFAULT_DB_WRITE_SEC_PER_CHUNK = 2.5e-4  # ~4 k chunks/sec
#: Per-document fixed cost on top of per-chunk writes.
_DOC_WRITE_SEC_PER_DOC = 5.0e-4


# ─────────────────────────────────────────────────────────────────────────
# Public dataclasses
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PhaseTime:
    """Per-phase wall-clock prediction.

    ``source`` records where each per-phase rate came from so the CLI /
    MCP caller can distinguish "calibrated from N runs" from
    "heuristic-only, still uncalibrated."
    """

    name: str
    seconds: float
    source: str  # "profile" | "heuristic" | "mixed"
    units: int = 0
    notes: str = ""


@dataclass(frozen=True)
class TimeEstimate:
    """Full wall-clock prediction. JSON-serialisable; ``schema_version=1``.

    Fields:
        schema_version: Stable version marker.
        total_seconds: Sum across all phases.
        calibration: ``"heuristic"`` when no profile data was used,
            ``"calibrated"`` when every relevant rate came from the
            profile, ``"hybrid"`` when both contributed.
        profile_samples: Total sample count folded into the profile so
            far (zero when no profile exists yet). The CLI uses this to
            render a "calibrated from N samples" footer.
        phases: Per-phase breakdown in the order they execute.
    """

    schema_version: int
    total_seconds: float
    calibration: str
    profile_samples: int
    phases: list[PhaseTime] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────
# Estimation
# ─────────────────────────────────────────────────────────────────────────


def _resolve_rate(
    profile: RuntimeProfile | None,
    phase: str,
    key: str | None,
    default: float,
) -> tuple[float, str]:
    """Return ``(rate, source)`` — profile if available, else heuristic."""
    if profile is not None:
        try:
            rate = profile.get_rate(phase, key)  # type: ignore[arg-type]
        except ValueError:
            rate = None
        if rate is not None and rate > 0:
            return rate, "profile"
    return default, "heuristic"


def estimate_time(
    sync: SyncEstimate,
    config: Config,
    *,
    profile: RuntimeProfile | None = None,
) -> TimeEstimate:
    """Predict wall-clock time for the ingest described by ``sync``.

    Args:
        sync: A :class:`~corpus_forge.estimate.SyncEstimate` produced by
            ``estimate_sync(path, config)``. The estimator does *not*
            re-walk the filesystem.
        config: The same :class:`Config` used to build ``sync``. Drives
            embedder selection (each active embedder contributes its own
            ``embed`` cost — they run sequentially in the current
            pipeline, so the cost is additive across embedders).
        profile: Optional pre-loaded
            :class:`~corpus_forge.runtime_profile.RuntimeProfile`. When
            omitted, the on-disk default is loaded lazily.

    Returns:
        :class:`TimeEstimate` — a frozen, JSON-serialisable dataclass.
    """
    from corpus_forge import runtime_profile as _rp  # noqa: PLC0415

    if profile is None:
        profile = _rp.load()

    sources_used: set[str] = set()

    # ── scan ────────────────────────────────────────────────────────────
    scan_rate, scan_src = _resolve_rate(profile, "scan", None, _DEFAULT_SCAN_SEC_PER_FILE)
    sources_used.add(scan_src)
    scan_seconds = sync.file_count * scan_rate
    scan_phase = PhaseTime(
        name="scan",
        seconds=scan_seconds,
        source=scan_src,
        units=sync.file_count,
        notes=f"{sync.file_count} files",
    )

    # ── extract + chunk (per extractor class) ──────────────────────────
    extract_seconds = 0.0
    chunk_seconds = 0.0
    extract_sources: set[str] = set()
    chunk_sources: set[str] = set()
    total_chunks = 0
    for summary in sync.by_extractor:
        cls = summary.extractor_class
        ext_rate, ext_src = _resolve_rate(
            profile,
            "extract",
            cls,
            _DEFAULT_EXTRACT_SEC_PER_BYTE.get(cls, 0.0),
        )
        ch_rate, ch_src = _resolve_rate(
            profile,
            "chunk",
            cls,
            _DEFAULT_CHUNK_SEC_PER_CHUNK.get(cls, 0.0),
        )
        extract_seconds += summary.raw_bytes * ext_rate
        chunk_seconds += summary.est_chunks * ch_rate
        extract_sources.add(ext_src)
        chunk_sources.add(ch_src)
        total_chunks += summary.est_chunks

    sources_used.update(extract_sources)
    sources_used.update(chunk_sources)
    extract_phase = PhaseTime(
        name="extract",
        seconds=extract_seconds,
        source=_collapse_sources(extract_sources),
        units=sync.total_raw_bytes,
        notes=f"{len(sync.by_extractor)} extractor class(es)",
    )
    chunk_phase = PhaseTime(
        name="chunk",
        seconds=chunk_seconds,
        source=_collapse_sources(chunk_sources),
        units=total_chunks,
        notes=f"{total_chunks} chunks",
    )

    # ── embed (per embedder; serial in the current pipeline) ───────────
    embed_seconds = 0.0
    embed_sources: set[str] = set()
    by_name = {e.name: e for e in config.embedders}
    for name in sync.embedders_active:
        e_cfg = by_name.get(name)
        dim = int(getattr(e_cfg, "dimension", 0)) if e_cfg is not None else 0
        heuristic = _default_embed_sec_per_chunk(dim)
        rate, src = _resolve_rate(profile, "embed", name, heuristic)
        embed_seconds += total_chunks * rate
        embed_sources.add(src)
    sources_used.update(embed_sources)
    embed_phase = PhaseTime(
        name="embed",
        seconds=embed_seconds,
        source=_collapse_sources(embed_sources) if embed_sources else "heuristic",
        units=total_chunks * max(len(sync.embedders_active), 1),
        notes=f"{len(sync.embedders_active)} embedder(s) × {total_chunks} chunks",
    )

    # ── db_write (single flat rate × chunks, plus per-doc fixed cost) ──
    write_rate, write_src = _resolve_rate(
        profile,
        "db_write",
        None,
        _DEFAULT_DB_WRITE_SEC_PER_CHUNK,
    )
    sources_used.add(write_src)
    write_seconds = total_chunks * write_rate + sync.file_count * _DOC_WRITE_SEC_PER_DOC
    write_phase = PhaseTime(
        name="db_write",
        seconds=write_seconds,
        source=write_src,
        units=total_chunks + sync.file_count,
        notes=f"{total_chunks} chunks + {sync.file_count} docs",
    )

    phases = [scan_phase, extract_phase, chunk_phase, embed_phase, write_phase]
    total = sum(p.seconds for p in phases)

    return TimeEstimate(
        schema_version=SCHEMA_VERSION,
        total_seconds=total,
        calibration=_collapse_sources(sources_used),
        profile_samples=profile.total_samples() if profile is not None else 0,
        phases=phases,
    )


def _collapse_sources(sources: set[str]) -> str:
    """Reduce a set of per-rate sources to a single calibration label."""
    sources = {s for s in sources if s}
    if not sources:
        return "heuristic"
    if sources == {"profile"}:
        return "calibrated"
    if sources == {"heuristic"}:
        return "heuristic"
    return "hybrid"


# ─────────────────────────────────────────────────────────────────────────
# Formatting helper (used by CLI + ingest startup log)
# ─────────────────────────────────────────────────────────────────────────


def format_duration(seconds: float) -> str:
    """Render ``seconds`` as a short ``Xh Ym Zs`` / ``Ym Zs`` / ``Zs`` string.

    Always rounds to whole seconds — sub-second resolution is noise at
    estimator-level accuracy.
    """
    if seconds < 0 or seconds != seconds:  # NaN guard
        return "—"
    total = int(round(seconds))
    if total < 60:
        return f"{total}s"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


__all__ = [
    "PhaseTime",
    "SCHEMA_VERSION",
    "TimeEstimate",
    "estimate_time",
    "format_duration",
]
