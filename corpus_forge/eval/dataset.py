"""Gold-set loader for the retrieval-eval harness — Phase R3.

JSONL schema (one row per query):

    {"query_id": "q01",
     "query": "How does the SQLite lock_source mutex work?",
     "relevant_chunk_ids": [123, 124, 489],
     "graded": {"123": 3, "124": 2, "489": 1},      # optional
     "content_hashes": ["abc...", "def...", "ghi..."] # optional, parallel to ids
    }

Required fields: ``query_id``, ``query``, ``relevant_chunk_ids``.

Optional fields:

- ``graded``: dict[str|int, int].  Used for graded-relevance NDCG.  Keys
  are normalised to int internally (JSON forces str on the wire).
- ``content_hashes``: list[str].  Must have the same length as
  ``relevant_chunk_ids``.  Used by the runner as a drift-tolerant
  fallback when a configured chunk_id has been rotated out of the
  corpus (re-ingest with different chunk boundaries).

Loader behaviour:

- Blank lines and lines starting with ``# `` are skipped.
- Bad JSON, missing required fields, or shape violations raise
  ``ValueError`` with a message including the file path AND line number.
- Missing file raises ``FileNotFoundError``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GoldQuery:
    """One gold-labelled query.

    Attributes:
        query_id: Stable short id (e.g. ``"q01"``).
        query: The natural-language query string.
        relevant_chunk_ids: Ground-truth list of relevant chunk ids
            (non-empty, ints).
        graded: Optional dict mapping chunk_id (int) → grade (int).  When
            present, used for NDCG graded-relevance scoring.  A relevant
            id present in ``relevant_chunk_ids`` but absent here is
            treated as grade 1 by the metric functions.
        content_hashes: Optional list of sha256 strings parallel to
            ``relevant_chunk_ids``.  When the corresponding chunk_id is
            missing from the corpus (re-chunking drift), the runner can
            fall back to a content-hash lookup.
    """

    query_id: str
    query: str
    relevant_chunk_ids: list[int]
    graded: dict[int, int] | None = None
    content_hashes: list[str] | None = None


def _err(path: Path, lineno: int, msg: str) -> ValueError:
    return ValueError(f"{path}:{lineno}: {msg}")


def _parse_row(path: Path, lineno: int, raw: str) -> GoldQuery:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise _err(path, lineno, f"bad JSON: {exc.msg}") from exc

    if not isinstance(obj, dict):
        raise _err(path, lineno, "row must be a JSON object")

    # ── required fields ─────────────────────────────────────────────────
    query_id = obj.get("query_id")
    if not isinstance(query_id, str) or not query_id:
        raise _err(path, lineno, "missing or empty `query_id`")

    query = obj.get("query")
    if not isinstance(query, str) or not query:
        raise _err(path, lineno, "missing or empty `query`")

    rel = obj.get("relevant_chunk_ids")
    if not isinstance(rel, list):
        raise _err(path, lineno, "`relevant_chunk_ids` must be a JSON array")
    if not rel:
        raise _err(path, lineno, "`relevant_chunk_ids` must be non-empty")
    rel_ids: list[int] = []
    for x in rel:
        if isinstance(x, bool) or not isinstance(x, int):
            raise _err(path, lineno, f"`relevant_chunk_ids` entries must be int, got {x!r}")
        rel_ids.append(int(x))

    # ── optional: graded ────────────────────────────────────────────────
    graded: dict[int, int] | None = None
    raw_graded = obj.get("graded")
    if raw_graded is not None:
        if not isinstance(raw_graded, dict):
            raise _err(path, lineno, "`graded` must be a JSON object")
        graded = {}
        for k, v in raw_graded.items():
            try:
                key = int(k)
            except (TypeError, ValueError) as exc:
                raise _err(
                    path,
                    lineno,
                    f"`graded` keys must be int-coercible, got {k!r}",
                ) from exc
            if isinstance(v, bool) or not isinstance(v, int):
                raise _err(path, lineno, f"`graded[{k!r}]` must be int, got {v!r}")
            graded[key] = int(v)

    # ── optional: content_hashes ────────────────────────────────────────
    content_hashes: list[str] | None = None
    raw_hashes = obj.get("content_hashes")
    if raw_hashes is not None:
        if not isinstance(raw_hashes, list):
            raise _err(path, lineno, "`content_hashes` must be a JSON array")
        if len(raw_hashes) != len(rel_ids):
            raise _err(
                path,
                lineno,
                f"`content_hashes` length ({len(raw_hashes)}) must match "
                f"`relevant_chunk_ids` length ({len(rel_ids)})",
            )
        for h in raw_hashes:
            if not isinstance(h, str):
                raise _err(path, lineno, f"`content_hashes` entries must be str, got {h!r}")
        content_hashes = list(raw_hashes)

    return GoldQuery(
        query_id=query_id,
        query=query,
        relevant_chunk_ids=rel_ids,
        graded=graded,
        content_hashes=content_hashes,
    )


def load_gold(path: Path | str) -> list[GoldQuery]:
    """Load a JSONL gold set from ``path``.

    Raises:
        FileNotFoundError: ``path`` doesn't exist.
        ValueError: any schema or JSON violation.  Message includes the
            file path and line number.

    Skips blank lines and lines starting with ``# ``.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"gold set not found: {p}")

    out: list[GoldQuery] = []
    with p.open("r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("#"):
                continue
            out.append(_parse_row(p, lineno, line))
    return out
