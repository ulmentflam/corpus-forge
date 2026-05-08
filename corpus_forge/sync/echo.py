"""EchoSuppressor — duplicate-write suppression cache for file-sync."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path


class EchoSuppressor:
    """Suppress duplicate write events for a short TTL window.

    Keys are ``str(path.resolve())``.  Values are ``(content_hash, expires_at)``.

    Thread-safe via :class:`threading.Lock`.
    """

    def __init__(
        self,
        default_ttl_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._default_ttl_s: float = default_ttl_s
        self._clock: Callable[[], float] = clock
        self._cache: dict[str, tuple[str, float]] = {}
        self._lock: threading.Lock = threading.Lock()

    # ── public API ────────────────────────────────────────────────────────

    def register(self, path: Path, content_hash: str, ttl_s: float | None = None) -> None:
        """Register *path* with *content_hash* for *ttl_s* seconds (default from constructor)."""
        if not isinstance(path, Path):
            raise TypeError(f"path must be a Path, got {type(path).__name__}")
        if not isinstance(content_hash, str):
            raise TypeError(f"content_hash must be a str, got {type(content_hash).__name__}")

        now = self._clock()
        effective_ttl = ttl_s if ttl_s is not None else self._default_ttl_s
        key = str(path.resolve())

        with self._lock:
            self._cache[key] = (content_hash, now + effective_ttl)

    def was_just_written(self, path: Path, content_hash: str) -> bool:
        """Return True if *path*+*content_hash* is a fresh registered entry.

        The entry is **consumed** (removed) on a successful match.  A
        mismatched hash does *not* consume the entry.
        """
        key = str(path.resolve())

        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return False
            stored_hash, expires_at = entry
            if stored_hash != content_hash:
                return False
            if self._clock() >= expires_at:
                del self._cache[key]
                return False
            del self._cache[key]
            return True

    def gc(self, now: float | None = None) -> None:
        """Remove all expired entries.

        Uses *now* when provided; otherwise calls :attr:`_clock`.
        """
        current = now if now is not None else self._clock()

        with self._lock:
            expired_keys = [
                key for key, (_, expires_at) in self._cache.items() if current >= expires_at
            ]
            for key in expired_keys:
                del self._cache[key]
