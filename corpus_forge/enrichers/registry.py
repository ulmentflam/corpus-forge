"""Code-enricher registry — Phase H / Wave 0 — H-01.

Mirrors :class:`corpus_forge.classifiers.registry.ClassifierRegistry`
shape: an ordered list of enrichers with last-write-wins on name
collisions. Phase H ships with exactly one "active" enricher at a time
(the chain pattern is reserved for future fallback enrichers — e.g.
local Ollama → remote when local is down), so :meth:`get` is the main
read path.
"""

from __future__ import annotations

import logging

from .base import CodeEnricher

logger = logging.getLogger(__name__)


class EnricherRegistry:
    """Ordered :class:`CodeEnricher` registry.

    A flat ordered list rather than a dict so future fallback chains
    (``[qwen-local, qwen-remote]``) compose cleanly without changing
    the data structure. Today only one backend is active per run; the
    factory in :mod:`corpus_forge.enrichers` constructs that single
    backend (or :class:`~corpus_forge.enrichers.base.NoopEnricher`) and
    registers it here so other call-sites can look it up by name.
    """

    def __init__(self) -> None:
        self._enrichers: list[CodeEnricher] = []

    def register(self, enricher: CodeEnricher) -> None:
        """Append ``enricher`` to the chain.

        Last-write-wins on ``name`` collisions: any prior enricher with
        the same ``name`` is removed before append so registering a
        custom version after the default cleanly overrides it.
        """
        name = enricher.name
        self._enrichers = [e for e in self._enrichers if e.name != name]
        self._enrichers.append(enricher)

    def get(self, name: str) -> CodeEnricher | None:
        """Return the enricher with this ``name``, or ``None``."""
        for e in self._enrichers:
            if e.name == name:
                return e
        return None

    def names(self) -> list[str]:
        """Return enricher names in registration order."""
        return [e.name for e in self._enrichers]

    def clear(self) -> None:
        """Drop every registered enricher (test helper)."""
        self._enrichers.clear()

    def __len__(self) -> int:
        return len(self._enrichers)
