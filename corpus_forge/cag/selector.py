"""corpus_forge.cag.selector — Hybrid CAG / RAG selector.

Public API
----------
select(query, dataset, *, retriever, root=None) -> tuple[str, dict | SearchResponse]
    Route a query to either a precomputed disk cache (cache hit) or the live
    retriever (cache miss).

HybridCagSelector(retriever, root=None)
    Stateful wrapper around ``select``.  Holds the retriever and root so
    callers do not need to pass them on every call.

Cache-key derivation
--------------------
The selector uses a **query-based** key scheme (distinct from the
content-hash-based scheme used by the cache builder in ``cache.py``).

    key = sha256(json.dumps(
        {"dataset": dataset, "template": template, "query": query},
        sort_keys=True,
    ).encode()).hexdigest()

The default template name is ``"default"``.  The resulting hex digest is
used as the filename under ``<root>/<dataset>/<key>.json``.

On hit the JSON file is parsed and returned verbatim — no ``_cf_route`` key
is injected into the payload.  The route is communicated solely by the
tuple's first element (``"cache"`` or ``"rag"``).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol

_DEFAULT_TEMPLATE = "default"
_DEFAULT_ROOT: Path | None = None


class _SearchableRetriever(Protocol):
    """Minimal duck-typed retriever shape — anything with ``.search(query)``.

    Narrower than :class:`corpus_forge.retrieval.retriever.Retriever` (which
    requires a ``SearchOptions`` argument); the selector calls only the
    single-arg form, so we accept any object that satisfies that.  Return type
    is intentionally ``object`` — the selector forwards the value verbatim to
    its caller and never inspects it.
    """

    def search(self, query: str) -> object: ...


def _derive_key(query: str, dataset: str, template: str = _DEFAULT_TEMPLATE) -> str:
    """Derive the SHA-256 hex-digest cache key for a (query, dataset, template) triple."""
    raw = json.dumps(
        {"dataset": dataset, "template": template, "query": query},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _resolve_cache_file(
    query: str,
    dataset: str,
    root: Path | None,
    template: str = _DEFAULT_TEMPLATE,
) -> Path | None:
    """Return the cache file path if it exists, else None."""
    if root is None:
        return None
    key = _derive_key(query, dataset, template)
    candidate = root / dataset / f"{key}.json"
    if candidate.is_file():
        return candidate
    return None


def select(
    query: str,
    dataset: str,
    *,
    retriever: _SearchableRetriever,
    root: Path | None = None,
) -> tuple[str, object]:
    """Route *query* to cache or retriever.

    Parameters
    ----------
    query:
        The user query string.
    dataset:
        The dataset name — used as the directory segment under *root*.
    retriever:
        An object with a ``.search(query, ...)`` method.  Called only on a
        cache miss.
    root:
        Directory that contains dataset subdirectories with cache JSON files.
        Defaults to ``None`` (no cache lookup — always misses).

    Returns
    -------
    tuple[str, dict | SearchResponse]
        ``("cache", payload_dict)`` on a cache hit — *payload_dict* is the
        parsed JSON from disk, verbatim.
        ``("rag", SearchResponse)`` on a cache miss — the retriever is called
        and its return value is passed through.
    """
    cache_file = _resolve_cache_file(query, dataset, root)
    if cache_file is not None:
        payload: dict[str, object] = json.loads(cache_file.read_text())
        return ("cache", payload)
    result = retriever.search(query)
    return ("rag", result)


class HybridCagSelector:
    """Stateful wrapper around :func:`select`.

    Holds a *retriever* and an optional *root* so that callers do not need to
    pass those on every call.

    Parameters
    ----------
    retriever:
        An object with a ``.search(query, ...)`` method.
    root:
        Cache root directory.  ``None`` means no cache lookup (always misses).
    """

    def __init__(self, retriever: _SearchableRetriever, *, root: Path | None = None) -> None:
        self._retriever = retriever
        self._root = root

    def select(self, query: str, dataset: str) -> tuple[str, object]:
        """Delegate to the module-level :func:`select`."""
        return select(query, dataset, retriever=self._retriever, root=self._root)
