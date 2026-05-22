"""Per-host runtime calibration profile for wall-clock estimation.

Companion to :mod:`corpus_forge.time_estimate`. Persists measured
throughput from real ``ingest`` / ``embed`` runs in a small JSON file so
the *next* ``corpus-forge estimate`` invocation can blend live numbers
in instead of relying solely on the heuristic table.

File layout (``schema_version = 1``)::

    {
      "schema_version": 1,
      "updated_at": "2026-05-22T10:45:34Z",
      "host_id": "evan-workstation",
      "scan":     {"sec_per_file": 1.2e-4, "samples": 3},
      "extract":  {"markdown": {"sec_per_byte": 2.1e-8, "samples": 4}, ...},
      "chunk":    {"markdown": {"sec_per_chunk": 1.9e-4, "samples": 4}, ...},
      "embed":    {"qwen3_8b": {"sec_per_chunk": 0.041,  "samples": 6}, ...},
      "db_write": {"sec_per_chunk": 2.3e-4, "samples": 5}
    }

Location:

- macOS / Linux: ``~/.config/corpus-forge/runtime_profile.json``
- Windows:       ``%APPDATA%\\corpus-forge\\runtime_profile.json``

Override via ``CF_RUNTIME_PROFILE`` (used by tests + multi-host setups).

All public functions are best-effort — IO failures log at DEBUG and
return ``None`` / continue so a read-only HOME never breaks ingest.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

#: EWMA weight applied to each new sample. ``new = alpha*x + (1-alpha)*old``.
#: 0.3 means a single anomalous run cannot dominate; ~3 runs are enough
#: for a stable signal.
DEFAULT_ALPHA = 0.3

#: Schema version of the on-disk profile. Bump on any breaking change.
SCHEMA_VERSION = 1

Phase = Literal["scan", "extract", "chunk", "embed", "db_write"]

# Phases that are keyed by a string (extractor class or embedder name).
_KEYED_PHASES: frozenset[str] = frozenset({"extract", "chunk", "embed"})
# Phases that are flat (single rate, no inner key).
_FLAT_PHASES: frozenset[str] = frozenset({"scan", "db_write"})

# Process-wide lock guarding the read-modify-write cycle. The atomic
# rename guarantees on-disk consistency across processes; the lock just
# avoids losing intra-process concurrent updates.
_LOCK = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────


def default_profile_path() -> Path:
    """Resolve the profile file path with env override + per-OS default."""
    env = os.environ.get("CF_RUNTIME_PROFILE")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "corpus-forge" / "runtime_profile.json"
    return Path.home() / ".config" / "corpus-forge" / "runtime_profile.json"


# ─────────────────────────────────────────────────────────────────────────
# Dataclasses
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class Rate:
    """A single rolling EWMA rate plus the count of samples that built it."""

    sec_per_unit: float
    samples: int = 0


@dataclass
class RuntimeProfile:
    """In-memory view of the on-disk JSON profile.

    The dict-valued fields use bare ``dict[str, Rate]`` so callers iterate
    with ordinary mapping semantics. The class is intentionally mutable —
    every public update path round-trips through :func:`save`.
    """

    schema_version: int = SCHEMA_VERSION
    updated_at: str = ""
    host_id: str = ""
    scan: Rate | None = None
    extract: dict[str, Rate] = field(default_factory=dict)
    chunk: dict[str, Rate] = field(default_factory=dict)
    embed: dict[str, Rate] = field(default_factory=dict)
    db_write: Rate | None = None

    def total_samples(self) -> int:
        """Total number of samples folded into this profile, across phases.

        Used by the CLI to render "calibrated from N past samples"
        footers.
        """
        n = 0
        if self.scan is not None:
            n += self.scan.samples
        if self.db_write is not None:
            n += self.db_write.samples
        for d in (self.extract, self.chunk, self.embed):
            n += sum(r.samples for r in d.values())
        return n

    def is_empty(self) -> bool:
        """``True`` when no phase has ever recorded a sample."""
        return self.total_samples() == 0

    def get_rate(self, phase: Phase, key: str | None = None) -> float | None:
        """Return ``sec_per_unit`` for ``phase`` / ``key``, or ``None``.

        For flat phases (``scan``, ``db_write``) ``key`` is ignored.
        """
        if phase in _FLAT_PHASES:
            r = self.scan if phase == "scan" else self.db_write
            return r.sec_per_unit if r is not None else None
        if phase not in _KEYED_PHASES:
            raise ValueError(f"unknown phase: {phase!r}")
        if key is None:
            raise ValueError(f"phase {phase!r} requires a key")
        bucket = getattr(self, phase)
        r = bucket.get(key)
        return r.sec_per_unit if r is not None else None

    def to_dict(self) -> dict[str, object]:
        """JSON-serialisable view of the profile."""
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "updated_at": self.updated_at,
            "host_id": self.host_id,
        }
        if self.scan is not None:
            payload["scan"] = asdict(self.scan)
        payload["extract"] = {k: asdict(r) for k, r in self.extract.items()}
        payload["chunk"] = {k: asdict(r) for k, r in self.chunk.items()}
        payload["embed"] = {k: asdict(r) for k, r in self.embed.items()}
        if self.db_write is not None:
            payload["db_write"] = asdict(self.db_write)
        return payload


# ─────────────────────────────────────────────────────────────────────────
# Load / save
# ─────────────────────────────────────────────────────────────────────────


def _from_dict(raw: dict[str, object]) -> RuntimeProfile:
    """Hydrate :class:`RuntimeProfile` from a parsed-JSON dict.

    Tolerant of partial / forward-compatible payloads: unknown keys are
    ignored, missing keys default to empty.
    """
    # ``raw`` is ``dict[str, object]`` so ``.get`` widens to ``object``,
    # which the typechecker won't pass straight into ``int()``. Coerce
    # via a try/except so a corrupt schema_version field (e.g. a string)
    # silently falls back to the current version rather than blowing up.
    sv_raw = raw.get("schema_version")
    try:
        schema_version = int(sv_raw) if isinstance(sv_raw, (int, str, float)) else SCHEMA_VERSION
    except (TypeError, ValueError):
        schema_version = SCHEMA_VERSION
    profile = RuntimeProfile(
        schema_version=schema_version,
        updated_at=str(raw.get("updated_at", "")),
        host_id=str(raw.get("host_id", "")),
    )

    def _hydrate_rate(obj: object) -> Rate | None:
        if not isinstance(obj, dict):
            return None
        try:
            return Rate(
                sec_per_unit=float(obj["sec_per_unit"]),
                samples=int(obj.get("samples", 0) or 0),
            )
        except (KeyError, TypeError, ValueError):
            return None

    profile.scan = _hydrate_rate(raw.get("scan"))
    profile.db_write = _hydrate_rate(raw.get("db_write"))
    for phase_name in ("extract", "chunk", "embed"):
        bucket = raw.get(phase_name) or {}
        if not isinstance(bucket, dict):
            continue
        hydrated: dict[str, Rate] = {}
        for key, val in bucket.items():
            r = _hydrate_rate(val)
            if r is not None:
                hydrated[str(key)] = r
        setattr(profile, phase_name, hydrated)
    return profile


def load(path: Path | None = None) -> RuntimeProfile:
    """Load the profile from disk, or return an empty profile.

    Never raises — a missing / unreadable / malformed file yields an
    empty :class:`RuntimeProfile`. Callers treat "empty" the same as
    "no calibration available yet."
    """
    target = path if path is not None else default_profile_path()
    try:
        with target.open(encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return RuntimeProfile()
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("runtime_profile: failed to read %s: %s", target, exc)
        return RuntimeProfile()
    if not isinstance(raw, dict):
        logger.debug("runtime_profile: %s is not a JSON object — ignoring", target)
        return RuntimeProfile()
    return _from_dict(raw)


def save(profile: RuntimeProfile, path: Path | None = None) -> bool:
    """Atomically write ``profile`` to disk.

    Best-effort: returns ``False`` (and logs at DEBUG) on any IO failure
    so callers can chain ``if not save(...): ...`` without try/except.
    """
    target = path if path is not None else default_profile_path()
    profile.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write to a sibling temp file then rename — that gives us an
        # atomic update even across power loss / concurrent readers.
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            json.dump(profile.to_dict(), tmp, ensure_ascii=False, indent=2)
            tmp.flush()
            # fsync isn't available on every filesystem (notably tmpfs);
            # the rename still gives us atomicity, just without the
            # durability guarantee.
            with contextlib.suppress(OSError):
                os.fsync(tmp.fileno())
            tmp_path = Path(tmp.name)
        tmp_path.replace(target)
    except OSError as exc:
        logger.debug("runtime_profile: failed to write %s: %s", target, exc)
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────
# Update
# ─────────────────────────────────────────────────────────────────────────


def _apply_ewma(current: Rate | None, sample: float, alpha: float) -> Rate:
    """Fold ``sample`` into ``current`` via EWMA, bumping the sample count."""
    if current is None or current.samples <= 0:
        return Rate(sec_per_unit=sample, samples=1)
    next_value = alpha * sample + (1.0 - alpha) * current.sec_per_unit
    return Rate(sec_per_unit=next_value, samples=current.samples + 1)


def record(
    phase: Phase,
    *,
    units: float,
    seconds: float,
    key: str | None = None,
    alpha: float = DEFAULT_ALPHA,
    path: Path | None = None,
) -> bool:
    """Fold one observation into the on-disk profile.

    Args:
        phase: One of ``"scan"`` / ``"extract"`` / ``"chunk"`` /
            ``"embed"`` / ``"db_write"``.
        units: Work performed (files for ``scan``; bytes for
            ``extract``; chunks for ``chunk`` / ``embed`` / ``db_write``).
        seconds: Wall-clock duration of the phase, in seconds.
        key: Extractor class (``extract``, ``chunk``) or embedder name
            (``embed``). Required for keyed phases, ignored otherwise.
        alpha: EWMA weight for the new sample. Defaults to
            :data:`DEFAULT_ALPHA`. Tests pass 1.0 for deterministic
            single-shot updates.
        path: Optional override for the profile path (tests use this).

    Returns:
        ``True`` on a successful write, ``False`` otherwise. Never
        raises — calibration is best-effort and must not break a real
        ingest run.
    """
    if units <= 0 or seconds <= 0:
        return False
    # EWMA semantics break outside (0, 1]: alpha=0 freezes the rate, > 1
    # over-shoots, < 0 flips the sign. Reject up front so a buggy caller
    # can't corrupt the on-disk profile.
    if not (0.0 < alpha <= 1.0):
        logger.debug("runtime_profile: alpha %r out of (0, 1] — skipping", alpha)
        return False
    if phase in _KEYED_PHASES and key is None:
        logger.debug("runtime_profile: phase %r requires a key — skipping", phase)
        return False
    sample = seconds / units
    # Reject inf/-inf/NaN as well as non-positive samples. ``isfinite``
    # covers all three non-finite cases that would otherwise poison the
    # EWMA on the next read.
    if not math.isfinite(sample) or sample <= 0:
        return False
    with _LOCK:
        profile = load(path=path)
        if phase == "scan":
            profile.scan = _apply_ewma(profile.scan, sample, alpha)
        elif phase == "db_write":
            profile.db_write = _apply_ewma(profile.db_write, sample, alpha)
        elif phase in _KEYED_PHASES:
            # ``key`` is narrowed non-None by the up-front check at the
            # top of the function, but pyrefly can't track that across
            # the early-return; re-assert for the typechecker.
            if key is None:  # pragma: no cover — guarded above
                return False
            bucket: dict[str, Rate] = getattr(profile, phase)
            bucket[key] = _apply_ewma(bucket.get(key), sample, alpha)
        else:  # pragma: no cover — guarded above
            return False
        return save(profile, path=path)


__all__ = [
    "DEFAULT_ALPHA",
    "SCHEMA_VERSION",
    "Phase",
    "Rate",
    "RuntimeProfile",
    "default_profile_path",
    "load",
    "record",
    "save",
]
