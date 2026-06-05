"""Phase I-11 — strictly-anonymous daily PyPI version-check ping.

Fetches the latest ``corpus-forge`` version from
``https://pypi.org/pypi/corpus-forge/json`` and prints a one-line
notice when a newer release is available. The reply is cached at
``~/.cache/corpus-forge/version-check.json`` for 24 hours so the
endpoint is hit at most once per day per host.

Opt out by setting ``CF_NO_VERSION_CHECK=1``. Silent failure on
offline / DNS / 5xx — never blocks the CLI on network conditions.

**Privacy invariants** (per the Phase I locked decisions):

- User-Agent is a literal ``corpus-forge/<version>`` — no install-id,
  no host fingerprint.
- No POST body, no headers carrying machine-identifiable info.
- Cache file holds version strings + a ``last_checked_unix`` int and
  nothing else.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from corpus_forge import __version__

logger = logging.getLogger(__name__)

PYPI_URL = "https://pypi.org/pypi/corpus-forge/json"
CACHE_TTL_S = 24 * 60 * 60  # one day
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "corpus-forge" / "version-check.json"
USER_AGENT = f"corpus-forge/{__version__}"

_OPT_OUT_ENV = "CF_NO_VERSION_CHECK"


@dataclass(frozen=True)
class VersionCheckResult:
    """Outcome of a single :func:`check_for_update` call."""

    installed: str
    latest: str | None
    is_newer_available: bool
    served_from_cache: bool
    cache_path: Path

    def notice(self) -> str | None:
        """Human-readable "newer version available" line, or ``None``."""
        if not self.is_newer_available or self.latest is None:
            return None
        return (
            f"note: corpus-forge v{self.latest} is available "
            f"(you have v{self.installed}). Run `corpus-forge update`."
        )


def check_for_update(
    *,
    cache_path: Path | None = None,
    timeout_s: float = 2.0,
    now: float | None = None,
    env: dict[str, str] | None = None,
    installed: str | None = None,
    force_refresh: bool = False,
) -> VersionCheckResult | None:
    """Return a :class:`VersionCheckResult` or ``None`` when opted-out.

    The default ``timeout_s=2`` is intentionally tight — this is a
    fire-and-forget probe on the CLI's hot path; we never want to
    block a user staring at ``corpus-forge --version``.

    Args:
        cache_path: Override the default cache location (test hook).
        timeout_s: Per-request HTTP budget. Tight by design.
        now: Override ``time.time()`` for cache-TTL tests.
        env: ``os.environ``-equivalent override (test hook).
        installed: Override the installed-version string (test hook).
        force_refresh: Skip the 24h-cache fast-path and ping PyPI now
            (the fresh answer is still written back to the cache).
            Added for the MCP ``check_update`` tool's explicit
            "check now" gesture; the opt-out env var still wins.
    """
    e = env if env is not None else os.environ
    if e.get(_OPT_OUT_ENV):
        return None

    installed_version = installed or __version__
    cache = cache_path or DEFAULT_CACHE_PATH
    current = now if now is not None else time.time()

    # Cache hit fast-path.
    cached = _load_cache(cache)
    if (
        not force_refresh
        and cached is not None
        and (current - cached.get("last_checked_unix", 0)) < CACHE_TTL_S
    ):
        latest = cached.get("latest")
        return VersionCheckResult(
            installed=installed_version,
            latest=latest,
            is_newer_available=_is_newer(latest, installed_version),
            served_from_cache=True,
            cache_path=cache,
        )

    # Cache miss → ping PyPI. Silent failure on any error.
    latest = _fetch_latest(timeout_s=timeout_s)
    if latest is None:
        return VersionCheckResult(
            installed=installed_version,
            latest=cached.get("latest") if cached else None,
            is_newer_available=False,
            served_from_cache=False,
            cache_path=cache,
        )

    _save_cache(cache, latest=latest, when=current)
    return VersionCheckResult(
        installed=installed_version,
        latest=latest,
        is_newer_available=_is_newer(latest, installed_version),
        served_from_cache=False,
        cache_path=cache,
    )


def cached_check_result(
    *,
    cache_path: Path | None = None,
    env: dict[str, str] | None = None,
    installed: str | None = None,
) -> VersionCheckResult | None:
    """Cache-only variant of :func:`check_for_update` — never networks.

    Reads whatever the last successful check wrote (even past the 24h
    TTL — a stale "newer available" is still true or harmlessly
    conservative) and returns ``None`` when opted out or when no cache
    exists yet. Built for surfaces that must stay off the network
    unconditionally, e.g. the MCP server's ``instructions=`` advisory
    constructed at startup.
    """
    e = env if env is not None else os.environ
    if e.get(_OPT_OUT_ENV):
        return None
    cache = cache_path or DEFAULT_CACHE_PATH
    cached = _load_cache(cache)
    if cached is None:
        return None
    latest = cached.get("latest")
    if not isinstance(latest, str):
        return None
    installed_version = installed or __version__
    return VersionCheckResult(
        installed=installed_version,
        latest=latest,
        is_newer_available=_is_newer(latest, installed_version),
        served_from_cache=True,
        cache_path=cache,
    )


# ── helpers ────────────────────────────────────────────────────────────


def _is_newer(latest: str | None, installed: str) -> bool:
    """Naive lexicographic-then-component comparison.

    PEP-440 is complicated; we don't need to nail every nuance — a
    false positive just prints a ``newer available`` notice the user
    can ignore. False negative would be the bad case (we miss an
    upgrade) so we lean toward calling things newer.
    """
    if not latest:
        return False
    if latest == installed:
        return False
    try:
        from packaging.version import InvalidVersion, Version  # noqa: PLC0415

        try:
            return Version(latest) > Version(installed)
        except InvalidVersion:
            return latest > installed
    except ImportError:
        # ``packaging`` is a transitive of pydantic + setuptools, so it
        # should always be present. Defensive fallback for the truly
        # minimal install: lexicographic compare.
        return latest > installed


def _load_cache(path: Path) -> dict | None:
    """Read the cache file. Returns ``None`` on any error."""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _save_cache(path: Path, *, latest: str, when: float) -> None:
    """Best-effort cache write. Silent on disk-full / permission errors."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump({"latest": latest, "last_checked_unix": int(when)}, f)
    except OSError as exc:
        logger.debug("version-check cache write failed: %s", exc)


def _fetch_latest(*, timeout_s: float) -> str | None:
    """Fetch the latest version string from PyPI.

    Lazy-imports ``urllib.request`` to keep the CLI's hot path cheap
    when the user has opted out. Silent on any error.
    """
    try:
        import urllib.error  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415
    except ImportError:
        return None

    req = urllib.request.Request(
        PYPI_URL,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (TimeoutError, urllib.error.URLError, OSError, ValueError) as exc:
        logger.debug("version-check fetch failed: %s", exc)
        return None

    info = data.get("info") if isinstance(data, dict) else None
    if not isinstance(info, dict):
        return None
    latest = info.get("version")
    return latest if isinstance(latest, str) else None
