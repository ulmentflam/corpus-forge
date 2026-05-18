"""Pending / skipped re-embed marker (Phase L Wave 5).

Lives at ``<platformdirs.user_cache_dir>/corpus-forge/state/pending_rerun.json``.
Atomic-write via tempfile + ``os.replace``.

JSON shape:

    {
      "<embedder_name>": {
        "state": "pending|skipped",
        "fp_was": "...",
        "fp_now": "...",
        "detected_at": "iso",
        "suppressed_until": "iso?"
      }
    }

A ``skipped`` entry suppresses the drift prompt for ``_SUPPRESSION_DAYS``
(default 7) days for the matching ``(name, fp_now)`` pair.  Re-changing
the fingerprint (``fp_now`` differs from the stored value) invalidates
the suppression so the user is re-prompted on a fresh model swap.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

import platformdirs

_SUPPRESSION_DAYS = 7


def _state_dir() -> Path:
    """Return the directory the marker file lives in (creating it on demand)."""

    base = Path(platformdirs.user_cache_dir("corpus-forge")) / "state"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _marker_path() -> Path:
    return _state_dir() / "pending_rerun.json"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _read() -> dict:
    p = _marker_path()
    if not p.exists():
        return {}
    try:
        text = p.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _atomic_write(payload: dict) -> None:
    """Write the marker JSON via tempfile + atomic rename."""

    p = _marker_path()
    fd, tmp_name = tempfile.mkstemp(prefix=".pending_rerun.", dir=str(p.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
        tmp_path.replace(p)
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def mark_pending(name: str, *, fp_was: str, fp_now: str) -> None:
    """Record that the user picked "later" for the drift of ``name``."""

    payload = _read()
    payload[name] = {
        "state": "pending",
        "fp_was": fp_was,
        "fp_now": fp_now,
        "detected_at": _now_iso(),
    }
    _atomic_write(payload)


def mark_skipped(name: str, *, fp_was: str, fp_now: str) -> None:
    """Record that the user picked "skip" — suppress prompts for ``_SUPPRESSION_DAYS``."""

    payload = _read()
    suppress_until = datetime.now(UTC) + timedelta(days=_SUPPRESSION_DAYS)
    payload[name] = {
        "state": "skipped",
        "fp_was": fp_was,
        "fp_now": fp_now,
        "detected_at": _now_iso(),
        "suppressed_until": suppress_until.replace(microsecond=0).isoformat(),
    }
    _atomic_write(payload)


def check_pending_or_skipped(name: str, fp_now: str) -> Literal["pending", "skipped", "none"]:
    """Return the marker state for ``(name, fp_now)`` or ``"none"``.

    - ``"pending"``: the user said "later"; re-prompt.
    - ``"skipped"``: the user said "skip" and the suppression hasn't
      expired; do NOT re-prompt.
    - ``"none"``: no marker, expired marker, or the user changed
      fingerprints again (the suppression no longer applies to the
      current ``fp_now``).
    """

    payload = _read()
    entry = payload.get(name)
    if not isinstance(entry, dict):
        return "none"

    # If the user re-changed fingerprints, the marker is stale.
    stored_fp_now = entry.get("fp_now")
    if stored_fp_now and stored_fp_now != fp_now:
        return "none"

    state = entry.get("state")
    if state == "skipped":
        suppressed = entry.get("suppressed_until")
        if not suppressed:
            return "none"
        try:
            until = datetime.fromisoformat(suppressed)
        except ValueError:
            return "none"
        if datetime.now(UTC) >= until:
            return "none"
        return "skipped"
    if state == "pending":
        return "pending"
    return "none"


def clear_marker(name: str) -> None:
    """Remove the marker entry for ``name`` (no-op if absent)."""

    payload = _read()
    if name in payload:
        del payload[name]
        _atomic_write(payload)


__all__ = [
    "check_pending_or_skipped",
    "clear_marker",
    "mark_pending",
    "mark_skipped",
]
