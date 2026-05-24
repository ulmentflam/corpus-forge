"""Exact and near-duplicate detection for corpus chunks.

``exact_duplicates`` groups chunks by their pre-computed ``content_hash``
column (pure stdlib, no heavy deps).

``near_duplicates`` uses MinHash LSH (datasketch) to find semantically
near-identical chunks.  The datasketch import is **lazy** — it happens
inside the function body so importing this module does not load datasketch.

Cross-reference: ``.planning/tdd/phase_o_eda_cleaning.md`` § Wave O2.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any  # retained for DB-API conn (psycopg/sqlite3 duck-typed)

# Minimum number of members required to report a duplicate group / cluster.
_MIN_GROUP_SIZE: int = 2


def exact_duplicates(chunks: list[dict[str, Any]]) -> dict[str, list[int]]:
    """Group chunks by ``content_hash``, returning only groups with >= 2 members.

    Args:
        chunks: Each dict must have ``"id"`` (int) and ``"content_hash"``
            (str or None).  Rows where ``content_hash`` is None are skipped.

    Returns:
        Dict mapping ``content_hash`` to sorted list of chunk ids.  Singletons
        (groups of size 1) are excluded.  Empty input returns ``{}``.
    """
    groups: dict[str, list[int]] = {}
    for chunk in chunks:
        h = chunk.get("content_hash")
        if h is None:
            continue
        groups.setdefault(h, []).append(int(chunk["id"]))

    return {h: sorted(ids) for h, ids in groups.items() if len(ids) >= _MIN_GROUP_SIZE}


def _stable_cluster_id(chunk_ids: list[int]) -> str:
    """Derive a deterministic cluster ID from sorted chunk ids."""
    key = ",".join(str(i) for i in sorted(chunk_ids))
    return "ndc_" + hashlib.sha256(key.encode()).hexdigest()[:16]


def _word_shingles(text: str, n: int = 4) -> list[str]:
    """Return n-gram word shingles for *text*."""
    words = text.split()
    if len(words) <= n:
        return words if words else [text]
    return [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]


def near_duplicates(
    chunks: list[dict[str, Any]],
    *,
    threshold: float = 0.85,
    num_perm: int = 128,
) -> list[dict[str, object]]:
    """Cluster near-duplicate chunks using MinHash LSH.

    Datasketch is imported lazily so importing this module does not load it.

    Args:
        chunks: Each dict must have ``"id"`` (int) and ``"text"`` (str).
        threshold: Jaccard similarity threshold for LSH bucketing (default 0.85).
        num_perm: Number of MinHash permutations (default 128).

    Returns:
        List of cluster dicts, each with:
        ``{"cluster_id": str, "chunk_ids": list[int], "similarity": float,
        "method": "minhash_lsh"}``.
        ``cluster_id`` is derived deterministically from sorted ``chunk_ids``
        so re-runs yield identical IDs.
        Returns ``[]`` when fewer than 2 chunks are provided.

    Notes:
        ``similarity`` per cluster is the average pairwise Jaccard estimate
        computed from the MinHash signatures of the cluster members.
    """
    if len(chunks) < _MIN_GROUP_SIZE:
        return []

    # Lazy import — datasketch must NOT appear in sys.modules at module level.
    # PLC0415 suppressed via pyproject.toml per-file-ignores: the entire module
    # contract requires datasketch to be imported lazily; the wave gate asserts
    # this with a python -c snapshot check.
    from datasketch import MinHash, MinHashLSH  # type: ignore[import-untyped]

    try:
        lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
    except ValueError:
        # Extreme threshold values (e.g. 0.999) can produce an invalid
        # band/row configuration (b < 2) in datasketch.  In that regime
        # virtually nothing would cluster, so returning [] is correct.
        return []

    minhashes: dict[int, MinHash] = {}

    for chunk in chunks:
        chunk_id = int(chunk["id"])
        text = chunk.get("text", "") or ""
        m = MinHash(num_perm=num_perm)
        for shingle in _word_shingles(text):
            m.update(shingle.encode("utf-8"))
        minhashes[chunk_id] = m
        lsh.insert(str(chunk_id), m)

    # Collect clusters via union-find over LSH neighbour pairs.
    parent: dict[int, int] = {int(c["id"]): int(c["id"]) for c in chunks}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for chunk in chunks:
        chunk_id = int(chunk["id"])
        neighbours = lsh.query(minhashes[chunk_id])
        for nb_str in neighbours:
            nb_id = int(nb_str)  # type: ignore[arg-type]
            if nb_id != chunk_id:
                union(chunk_id, nb_id)

    # Group chunk ids by their union-find root.
    component_map: dict[int, list[int]] = {}
    for chunk in chunks:
        chunk_id = int(chunk["id"])
        component_map.setdefault(find(chunk_id), []).append(chunk_id)

    results: list[dict[str, object]] = []
    for members in component_map.values():
        if len(members) < _MIN_GROUP_SIZE:
            continue
        sorted_members = sorted(members)
        cluster_id = _stable_cluster_id(sorted_members)

        # Average pairwise Jaccard estimate from MinHash signatures.
        jaccard_sum = 0.0
        pair_count = 0
        for i in range(len(sorted_members)):
            for j in range(i + 1, len(sorted_members)):
                jaccard_sum += minhashes[sorted_members[i]].jaccard(minhashes[sorted_members[j]])
                pair_count += 1
        similarity = float(jaccard_sum / pair_count) if pair_count > 0 else 1.0

        results.append(
            {
                "cluster_id": cluster_id,
                "chunk_ids": sorted_members,
                "similarity": similarity,
                "method": "minhash_lsh",
            }
        )

    return results


def persist_clusters(
    conn: Any,
    clusters: list[dict[str, Any]],
    *,
    method: str = "minhash_lsh",
) -> int:
    """Persist near-duplicate clusters to the ``near_duplicate_clusters`` table.

    Idempotent: rows are inserted only when the ``(cluster_id, chunk_id)`` pair
    does not already exist (``WHERE NOT EXISTS`` guard — no unique constraint
    required in the schema).

    Args:
        conn: A DB-API 2.0 connection — ``sqlite3.Connection`` or a psycopg
            connection.  Detected by type; ``?`` placeholders used for SQLite,
            ``%s`` for Postgres.
        clusters: List of cluster dicts, each with keys ``"cluster_id"``
            (str) and ``"chunk_ids"`` (list[int]).  Extra keys (e.g.
            ``"similarity"``, ``"method"``) are read but never required beyond
            ``"cluster_id"`` and ``"chunk_ids"``.
        method: Label stored in the ``method`` column for every inserted row.
            Overrides any ``"method"`` field inside the cluster dict.

    Returns:
        Number of rows actually inserted (re-runs of the same clusters return
        0 because every pair already exists).

    Raises:
        KeyError: if a cluster dict is missing ``"cluster_id"`` or
            ``"chunk_ids"``.
        ValueError: if ``"chunk_ids"`` is not a list.
    """
    is_sqlite = isinstance(conn, sqlite3.Connection)

    if is_sqlite:
        insert_sql = (
            "INSERT INTO near_duplicate_clusters "
            "(cluster_id, chunk_id, similarity, method) "
            "SELECT ?, ?, ?, ? "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM near_duplicate_clusters "
            "WHERE cluster_id = ? AND chunk_id = ?"
            ")"
        )
    else:
        insert_sql = (
            "INSERT INTO corpus.near_duplicate_clusters "
            "(cluster_id, chunk_id, similarity, method) "
            "SELECT %s, %s, %s, %s "
            "WHERE NOT EXISTS ("
            "SELECT 1 FROM corpus.near_duplicate_clusters "
            "WHERE cluster_id = %s AND chunk_id = %s"
            ")"
        )

    inserted = 0

    if is_sqlite:
        cur = conn.cursor()
        try:
            for cluster in clusters:
                # Validate required keys — raise early before touching the DB.
                cluster_id: str = cluster["cluster_id"]
                chunk_ids: list[int] = cluster["chunk_ids"]
                if not isinstance(chunk_ids, list):
                    raise ValueError(f"cluster 'chunk_ids' must be a list, got {type(chunk_ids)!r}")
                similarity: float = float(cluster.get("similarity") or 0.0)

                for chunk_id in chunk_ids:
                    cur.execute(
                        insert_sql,
                        (
                            cluster_id,
                            int(chunk_id),
                            similarity,
                            method,
                            cluster_id,
                            int(chunk_id),
                        ),
                    )
                    inserted += cur.rowcount if cur.rowcount > 0 else 0
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
    else:
        with conn.cursor() as cur:
            try:
                for cluster in clusters:
                    cluster_id = cluster["cluster_id"]
                    chunk_ids = cluster["chunk_ids"]
                    if not isinstance(chunk_ids, list):
                        raise ValueError(
                            f"cluster 'chunk_ids' must be a list, got {type(chunk_ids)!r}"
                        )
                    similarity = float(cluster.get("similarity") or 0.0)

                    for chunk_id in chunk_ids:
                        cur.execute(
                            insert_sql,
                            (
                                cluster_id,
                                int(chunk_id),
                                similarity,
                                method,
                                cluster_id,
                                int(chunk_id),
                            ),
                        )
                        inserted += cur.rowcount if cur.rowcount > 0 else 0
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    return inserted
