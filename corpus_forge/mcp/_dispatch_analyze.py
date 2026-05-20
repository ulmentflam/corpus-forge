"""Phase O Wave 4 — MCP dispatch for the four analyze read-only tools.

Functions in this module are called by ``corpus_forge.mcp.server._call_tool``
for the following tools:

- ``analyze_corpus``   — token / length stats sweep over a dataset.
- ``find_duplicates``  — exact + near-duplicate detection.
- ``cluster_topics``   — topic clustering via HDBSCAN / BERTopic.
- ``score_quality``    — per-chunk heuristic quality scoring (persist opt-in).

Lazy-import contract
--------------------
Heavy analyze deps (scikit-learn, hdbscan, datasketch, etc.) are imported
*inside* each dispatch function body, not at module top level.  Importing
this module does not load those packages.

Module-level helpers (``_fetch_chunks_for_dataset``,
``_fetch_chunks_by_ids``, ``_persist_quality_signals``) are defined at
module level so test suites can monkeypatch them without patching
internals of the dispatch closures.

Cross-reference: ``.planning/tdd/phase_o_eda_cleaning.md`` § Wave O4.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Module-level helpers — must be at module level for monkeypatching.
# ---------------------------------------------------------------------------


def _fetch_chunks_for_dataset(backend: Any, dataset: str) -> list[dict[str, Any]]:
    """Fetch all chunks for *dataset* from *backend*.

    The backend is expected to expose a ``list_chunks`` or similar method.
    In test fixtures the backend is a MagicMock and this function is
    monkeypatched; in production ``backend.list_chunks(dataset=dataset)``
    is called.

    Raises:
        ValueError: if the dataset does not exist or the backend call fails.
    """
    list_chunks_fn = getattr(backend, "list_chunks", None)
    if list_chunks_fn is None:
        raise ValueError(
            f"backend {type(backend).__name__!r} does not expose list_chunks; "
            f"cannot fetch dataset {dataset!r}"
        )
    result = list_chunks_fn(dataset=dataset)
    if result is None:
        raise ValueError(f"Dataset {dataset!r} not found")
    return list(result)


def _fetch_chunks_by_ids(backend: Any, chunk_ids: list[int]) -> list[dict[str, Any]]:
    """Fetch a list of chunks by their primary-key IDs.

    In test fixtures the backend is a MagicMock and this function is
    monkeypatched; in production ``backend.get_chunk`` is called for each id.
    """
    if not chunk_ids:
        return []
    get_fn = getattr(backend, "get_chunk", None)
    if get_fn is None:
        return []
    chunks = []
    for cid in chunk_ids:
        chunk = get_fn(cid)
        if chunk is not None:
            chunks.append(dict(chunk))
    return chunks


def _persist_quality_signals(
    backend: Any,
    chunk_ids: list[int],
    scores: list[float],
    source: str = "heuristic_v1",
) -> int:
    """Write quality scores to the backend's ``chunk_quality_signals`` table.

    Delegates to ``corpus_forge.analyze.quality.persist_quality_signals``.
    The function is at module level so tests can monkeypatch it.
    """
    from corpus_forge.analyze.quality import persist_quality_signals

    conn = getattr(backend, "conn", None) or getattr(backend, "connection", None)
    if conn is None:
        return 0
    return persist_quality_signals(conn, chunk_ids, scores, source=source)


# ---------------------------------------------------------------------------
# Dispatch: analyze_corpus
# ---------------------------------------------------------------------------


async def _dispatch_analyze_corpus(
    arguments: dict[str, Any],
    *,
    backend: Any,
    writes_enabled: bool,
) -> Any:
    """analyze_corpus — token/length stats sweep over a dataset.

    Returns:
        dict with keys ``n_chunks`` (int), ``token_stats`` (dict),
        ``n_documents`` (int).  All values are JSON-serializable.

    Error:
        Returns a ``CallToolResult(isError=True)`` on any exception.
    """
    from corpus_forge.analyze.stats import compute_token_stats
    from corpus_forge.mcp.server import _error_result

    dataset = arguments.get("dataset", "")
    try:
        chunks = _fetch_chunks_for_dataset(backend, dataset)
    except Exception as exc:
        return _error_result(f"analyze_corpus: {exc}")

    token_stats = compute_token_stats(chunks)

    # Count distinct document_ids (None-safe).
    doc_ids: set[int] = set()
    for c in chunks:
        doc_id = c.get("document_id")
        if doc_id is not None:
            doc_ids.add(int(doc_id))

    return {
        "n_chunks": len(chunks),
        "token_stats": {k: _json_safe(v) for k, v in token_stats.items()},
        "n_documents": len(doc_ids),
    }


# ---------------------------------------------------------------------------
# Dispatch: find_duplicates
# ---------------------------------------------------------------------------


async def _dispatch_find_duplicates(
    arguments: dict[str, Any],
    *,
    backend: Any,
    writes_enabled: bool,
) -> Any:
    """find_duplicates — exact + near-duplicate detection over a dataset.

    Returns:
        dict with keys ``exact_duplicates`` (dict mapping hash → list[int])
        and ``near_duplicates`` (list of cluster dicts).

    Error:
        Returns a ``CallToolResult(isError=True)`` on any exception.
    """
    from corpus_forge.analyze.dedup import exact_duplicates, near_duplicates
    from corpus_forge.mcp.server import _error_result

    dataset = arguments.get("dataset", "")
    threshold = float(arguments.get("threshold", 0.85))

    try:
        chunks = _fetch_chunks_for_dataset(backend, dataset)
    except Exception as exc:
        return _error_result(f"find_duplicates: {exc}")

    exact = exact_duplicates(chunks)
    near = near_duplicates(chunks, threshold=threshold)

    return {
        "exact_duplicates": exact,
        "near_duplicates": [_json_safe_dict(c) for c in near],
    }


# ---------------------------------------------------------------------------
# Dispatch: cluster_topics
# ---------------------------------------------------------------------------


async def _dispatch_cluster_topics(
    arguments: dict[str, Any],
    *,
    backend: Any,
    writes_enabled: bool,
) -> Any:
    """cluster_topics — topic clustering over a dataset.

    Returns:
        dict with key ``clusters``: list of dicts each containing
        ``cluster_id`` (str), ``chunk_ids`` (list[int]), ``top_terms``
        (list[str]).

    Error:
        Returns a ``CallToolResult(isError=True)`` on any exception.
    """
    from corpus_forge.analyze.topics import (
        cluster_topics,
        top_terms_per_cluster,
    )
    from corpus_forge.mcp.server import _error_result

    dataset = arguments.get("dataset", "")
    min_cluster_size = int(arguments.get("min_cluster_size", 10))

    try:
        chunks = _fetch_chunks_for_dataset(backend, dataset)
    except Exception as exc:
        return _error_result(f"cluster_topics: {exc}")

    if not chunks:
        return {"clusters": []}

    # Build embedding lists — fall back to empty float list when absent.
    embeddings: list[list[float]] = []
    for c in chunks:
        emb = c.get("embedding")
        if emb is not None:
            embeddings.append(list(map(float, emb)))
        else:
            embeddings.append([])

    # Filter to chunks that have a real embedding.
    indexed: list[tuple[int, list[float]]] = [(i, e) for i, e in enumerate(embeddings) if e]

    if len(indexed) < 2:
        # Not enough embedded chunks to cluster meaningfully.
        return {"clusters": []}

    idx_list = [i for i, _ in indexed]
    emb_list = [e for _, e in indexed]
    chunk_subset = [chunks[i] for i in idx_list]
    texts = [c.get("text", "") or "" for c in chunk_subset]

    result = cluster_topics(emb_list, min_cluster_size=min_cluster_size)
    assignments: list[int] = result["cluster_assignments"]
    # top_terms_per_cluster returns dict[int, list[tuple[str, float]]].
    raw_terms_map: dict[int, list[tuple[str, float]]] = top_terms_per_cluster(texts, assignments)
    # Extract just the term strings for the MCP wire format.
    terms_map: dict[int, list[str]] = {k: [t for t, _ in v] for k, v in raw_terms_map.items()}

    # Group chunk_ids by cluster label (skip noise == -1).
    from collections import defaultdict

    cluster_groups: dict[int, list[int]] = defaultdict(list)
    for pos, label in enumerate(assignments):
        if label != -1:
            cluster_groups[label].append(int(chunk_subset[pos]["id"]))

    clusters = []
    for label, cids in sorted(cluster_groups.items()):
        clusters.append(
            {
                "cluster_id": str(label),
                "chunk_ids": cids,
                "top_terms": terms_map.get(label, []),
            }
        )

    return {"clusters": clusters}


# ---------------------------------------------------------------------------
# Dispatch: score_quality
# ---------------------------------------------------------------------------


async def _dispatch_score_quality(
    arguments: dict[str, Any],
    *,
    backend: Any,
    writes_enabled: bool,
) -> Any:
    """score_quality — per-chunk heuristic quality scoring.

    Accepts ``chunk_ids`` (list[int]) OR ``dataset`` (str) to define the
    scope.  When ``persist=True``, requires ``writes_enabled=True``.

    Returns:
        dict with key ``scores`` mapping chunk_id (str) → float in [0, 1].

    Error:
        Returns a ``CallToolResult(isError=True)`` on any exception or when
        ``persist=True`` but ``writes_enabled=False``.
    """
    from corpus_forge.analyze.quality import score_chunks_batch
    from corpus_forge.mcp.server import _error_result

    persist = bool(arguments.get("persist", False))
    chunk_ids_arg: list[int] | None = arguments.get("chunk_ids")
    dataset_arg: str | None = arguments.get("dataset")

    # Persist gate.
    if persist and not writes_enabled:
        return _error_result(
            "score_quality: persist=True requires writes_enabled=True on this server"
        )

    # Resolve chunks.
    if chunk_ids_arg is not None:
        try:
            chunks = _fetch_chunks_by_ids(backend, [int(c) for c in chunk_ids_arg])
        except Exception as exc:
            return _error_result(f"score_quality: {exc}")
    elif dataset_arg is not None:
        try:
            chunks = _fetch_chunks_for_dataset(backend, dataset_arg)
        except Exception as exc:
            return _error_result(f"score_quality: {exc}")
    else:
        return _error_result("score_quality: provide either chunk_ids or dataset")

    if not chunks:
        return {"scores": {}}

    scores = score_chunks_batch(chunks)

    # Derive ids from the resolved chunk set (some inputs may be missing in
    # the backend); this keeps chunk_ids, scores and scores_map all aligned.
    resolved_ids = [int(c["id"]) for c in chunks]

    scores_map: dict[str, float] = {
        str(int(c["id"])): float(s) for c, s in zip(chunks, scores, strict=True)
    }

    if persist:
        _persist_quality_signals(backend, resolved_ids, scores)

    return {"scores": scores_map}


# ---------------------------------------------------------------------------
# JSON-safety helpers
# ---------------------------------------------------------------------------


def _json_safe(value: Any) -> Any:
    """Cast numpy scalars / Python numbers to plain Python types."""
    # Avoid importing numpy unless already present.
    import sys

    np = sys.modules.get("numpy")
    if np is not None and isinstance(value, np.generic):
        return value.item()
    if isinstance(value, float):
        return float(value)
    if isinstance(value, int):
        return int(value)
    return value


def _json_safe_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively apply ``_json_safe`` to dict values."""
    return {k: _json_safe(v) for k, v in d.items()}
