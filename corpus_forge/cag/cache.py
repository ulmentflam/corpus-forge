"""corpus_forge.cag.cache — CAG cache builder and invalidation utilities.

Public API
----------
build_cache(conn, dataset, *, top_k=50, template="chatml", root=None) -> Path
    Fetch the top-k chunks for *dataset*, render them via *template*, and
    write a JSON cache file under *root/<dataset>/<key>.json*.  Returns the
    path of the written file.

cache_key(dataset_id, content_hashes, template) -> str
    Deterministic 16-char hex key derived from
    ``sha256(repr((dataset_id, sorted(content_hashes), template)))[:16]``.
    Input ordering of *content_hashes* does not affect the key.

cache_path(root, dataset, key) -> Path
    Resolve ``root / dataset / f"{key}.json"``.

list_cached_keys(root, dataset) -> list[str]
    Return the stem names of all ``.json`` files in the dataset directory.
    Returns ``[]`` if the directory does not exist.

invalidate(root, dataset, content_hash) -> int
    Delete every cache file that references *content_hash*, either because
    the file's ``content_hashes`` list contains it or because the filename
    stem equals the hash (direct-hash cache layout).  Scans both
    ``root/dataset/`` and ``root/cag/dataset/`` to cover both the builder
    and the live-written path conventions.  Returns the number of files
    deleted.  Permission errors are caught, logged as warnings, and
    reported as 0 deleted for that file.

invalidate_for_chunk(chunk_id, dataset_id, *, root, conn) -> int
    Look up the chunk's ``content_hash`` via *conn*, then delegate to
    :func:`invalidate`.  Returns 0 immediately if ``content_hash`` is NULL.

Phase P Wave 3.  See ``.planning/tdd/phase_o_eda_cleaning.md`` § Wave P3.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_DEFAULT_ROOT = Path.home() / ".cache" / "corpus-forge" / "cag"


# ---------------------------------------------------------------------------
# cache_key
# ---------------------------------------------------------------------------


def cache_key(dataset_id: int, content_hashes: list[str], template: str) -> str:
    """Return a 16-char deterministic hex cache key.

    The key is ``sha256(repr((dataset_id, sorted(content_hashes), template)))``
    truncated to 16 hex characters.  Sorting *content_hashes* internally
    means input order does not affect the result.
    """
    raw = repr((dataset_id, sorted(content_hashes), template))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# cache_path
# ---------------------------------------------------------------------------


def cache_path(root: Path, dataset: str, key: str) -> Path:
    """Return the canonical path for a cache file.

    Path: ``root / dataset / f"{key}.json"``.
    """
    return root / dataset / f"{key}.json"


# ---------------------------------------------------------------------------
# Internal helpers (module-level so tests can monkeypatch)
# ---------------------------------------------------------------------------


def _fetch_chunks(conn: Any, dataset: str, top_k: int) -> tuple[int, list[dict[str, Any]]]:
    """Fetch the top-*top_k* chunks for *dataset* from *conn*.

    Returns ``(dataset_id, list_of_chunk_dicts)``.  Each dict has at least
    ``id``, ``content_hash``, and ``text`` keys.
    """
    row = conn.execute(
        "SELECT id FROM datasets WHERE name = ?",
        (dataset,),
    ).fetchone()
    if row is None:
        raise ValueError(f"dataset {dataset!r} not found")
    dataset_id: int = int(row[0]) if not isinstance(row, dict) else int(row["id"])

    # `chunks` and `documents` both have an `id` column — qualify each
    # selected column so the JOIN resolves unambiguously.
    cursor = conn.execute(
        "SELECT chunks.id AS id, chunks.content_hash AS content_hash, "
        "       chunks.text AS text "
        "FROM chunks "
        "JOIN documents ON chunks.document_id = documents.id "
        "WHERE documents.dataset_id = ? "
        "ORDER BY chunks.id "
        "LIMIT ?",
        (dataset_id, top_k),
    )
    rows = cursor.fetchall()
    chunks: list[dict[str, Any]] = []
    for r in rows:
        if isinstance(r, dict):
            chunks.append(dict(r))
        else:
            chunks.append({"id": r[0], "content_hash": r[1], "text": r[2]})
    return dataset_id, chunks


def _render_template(chunks: list[dict[str, Any]], template: str) -> str:
    """Render *chunks* into a prompt string using *template*.

    This is a minimal implementation; downstream clients can substitute a
    richer renderer by monkeypatching this function.
    """
    if template == "chatml":
        parts = ["<|im_start|>system\n"]
        for chunk in chunks:
            parts.append(chunk.get("text", ""))
            parts.append("\n")
        parts.append("<|im_end|>")
        return "".join(parts)
    # Generic fallback: newline-joined texts.
    return "\n\n".join(chunk.get("text", "") for chunk in chunks)


# ---------------------------------------------------------------------------
# build_cache
# ---------------------------------------------------------------------------


def build_cache(
    conn: Any,
    dataset: str,
    *,
    top_k: int = 50,
    template: str = "chatml",
    root: Path | None = None,
) -> Path:
    """Build (or rebuild) the cache for *dataset* and return the file path.

    Fetches the top-*top_k* chunks via :func:`_fetch_chunks`, renders them
    via :func:`_render_template`, and writes a JSON file at
    ``root / dataset / <key>.json``.

    Parameters
    ----------
    conn:
        A database connection (duck-typed; must support ``.execute()``).
    dataset:
        The dataset name.
    top_k:
        Number of chunks to include.  Default: 50.
    template:
        Template format name.  Default: ``"chatml"``.
    root:
        Directory under which dataset subdirectories are created.  Defaults
        to ``~/.cache/corpus-forge/cag``.
    """
    if root is None:
        root = _DEFAULT_ROOT

    dataset_id, chunks = _fetch_chunks(conn, dataset, top_k)
    messages = _render_template(chunks, template)

    hashes = [c["content_hash"] for c in chunks if c.get("content_hash")]
    key = cache_key(dataset_id, hashes, template)
    path = cache_path(root, dataset, key)

    path.parent.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "dataset": dataset,
        "template": template,
        "content_hashes": hashes,
        "messages": messages,
        "built_at": datetime.now().isoformat(),
        "cache_key": key,
    }
    path.write_text(json.dumps(payload))
    return path


# ---------------------------------------------------------------------------
# list_cached_keys
# ---------------------------------------------------------------------------


def list_cached_keys(root: Path, dataset: str) -> list[str]:
    """Return the stem names of all ``.json`` cache files for *dataset*.

    Returns an empty list if the dataset directory does not exist.
    """
    ds_dir = root / dataset
    if not ds_dir.is_dir():
        return []
    return [p.stem for p in ds_dir.iterdir() if p.suffix == ".json"]


# ---------------------------------------------------------------------------
# invalidate
# ---------------------------------------------------------------------------


def invalidate(root: Path, dataset: str, content_hash: str) -> int:
    """Delete all cache files that reference *content_hash*.

    Scans two directory layouts:

    1. ``root / dataset /`` — builder layout; files matched by their
       ``content_hashes`` JSON field (list) containing *content_hash*, or by
       their filename stem equalling *content_hash*.
    2. ``root / "cag" / dataset /`` — live-written layout; files matched by
       filename stem equalling *content_hash* or by ``content_hashes`` field.

    Returns the number of files successfully deleted.  Files that cannot be
    deleted due to a :exc:`PermissionError` are skipped and a warning is
    logged; they do NOT contribute to the returned count.
    """
    candidate_dirs = [
        root / dataset,
        root / "cag" / dataset,
    ]

    deleted = 0
    for scan_dir in candidate_dirs:
        if not scan_dir.is_dir():
            continue
        for json_file in scan_dir.iterdir():
            if json_file.suffix != ".json":
                continue

            # Match by filename stem (direct-hash layout).
            stem_match = json_file.stem == content_hash

            # Match by content_hashes field (builder layout).
            field_match = False
            if not stem_match:
                try:
                    data = json.loads(json_file.read_text())
                    hashes = data.get("content_hashes")
                    if isinstance(hashes, list) and content_hash in hashes:
                        field_match = True
                except (OSError, json.JSONDecodeError):
                    pass

            if stem_match or field_match:
                try:
                    json_file.unlink()
                    deleted += 1
                except PermissionError as exc:
                    _log.warning(
                        "CAG cache invalidation skipped for %s: permission error: %s",
                        json_file,
                        exc,
                    )
    return deleted


# ---------------------------------------------------------------------------
# invalidate_for_chunk
# ---------------------------------------------------------------------------


def invalidate_for_chunk(
    chunk_id: int,
    dataset_id: int,
    *,
    root: Path,
    conn: Any,
) -> int:
    """Look up the chunk's ``content_hash`` and call :func:`invalidate`.

    Parameters
    ----------
    chunk_id:
        Primary key of the chunk to invalidate caches for.
    dataset_id:
        Primary key of the dataset (used to resolve the dataset name).
    root:
        CAG cache root directory.
    conn:
        A live database connection used for the lookup queries.

    Returns
    -------
    int
        Number of cache files deleted, or 0 if the chunk has no
        ``content_hash``.
    """
    # Resolve content_hash.
    row = conn.execute(
        "SELECT content_hash FROM chunks WHERE id = ?",
        (chunk_id,),
    ).fetchone()
    if row is None:
        return 0
    content_hash = row[0] if not isinstance(row, dict) else row.get("content_hash")
    if not content_hash:
        return 0

    # Resolve dataset name.
    ds_row = conn.execute(
        "SELECT name FROM datasets WHERE id = ?",
        (dataset_id,),
    ).fetchone()
    if ds_row is None:
        dataset_name = str(dataset_id)
    else:
        dataset_name = (
            ds_row[0] if not isinstance(ds_row, dict) else ds_row.get("name", str(dataset_id))
        )

    return invalidate(root, dataset_name, content_hash)
