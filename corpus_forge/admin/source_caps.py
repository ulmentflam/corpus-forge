"""Per-source row / byte cap enforcement.

RFC ``rfc-corpus-growth-controls`` — fourth-item enforcement. When a
:class:`corpus_forge.config.DatasetSourceConfig` declares ``max_rows``
or ``max_bytes``, the ingest loop calls :func:`enforce_source_caps`
after each successful :func:`corpus_forge.ingest.ingest_one`. If the
source is over either cap, every chunk attributed to the source is
scored via
:func:`corpus_forge.curation.selector.score_for_pruning` (the same
rubric used by :mod:`corpus_forge.admin.prune`) and the lowest-scoring
rows are evicted until the source is back under all configured caps.

Attribution
-----------

Each plugin emits a deterministic URI prefix on every row it writes
(``source_uri=...`` on documents and conversations). The map lives in
:func:`derive_source_uri_prefix`; it mirrors what the source plugins
under :mod:`corpus_forge.sources` actually construct. Plugins whose
URI scheme is not uniquely owned by a single source instance (e.g.
``zotero`` with no ``library_id``, or an unknown plugin name) return
``None`` and cap enforcement is silently skipped with a single
WARNING line.

Scoring + eviction
------------------

The scoring side reuses the same primitives as
:mod:`corpus_forge.admin.prune` so cap eviction and admin-driven
prune agree on what "low quality / safe to drop" means. The
deletion path borrows ``_delete_chunks`` from :mod:`prune` to keep
both surfaces on a single bulk-DELETE / chunked-IN dispatch.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from corpus_forge.admin.prune import (
    _delete_chunks,
    _feedback_drag,
    _is_postgres_like,
    _load_feedback_by_chunk_id,
    _minhash_available,
)
from corpus_forge.curation.selector import (
    _Candidate,
    _compute_confidence_deficit,
    _compute_freshness,
    _compute_missing_metadata,
    _iter_curation_candidates,
    score_for_pruning,
)

logger = logging.getLogger(__name__)


# Default candidate pool when scoring for cap enforcement. Sized to be
# larger than typical caps so the rubric has the full pool to rank
# against; if a user has more than this many rows attributed to one
# source, the eviction still proceeds but the score breakdown sees a
# capped window.
_DEFAULT_SCORING_POOL: int = 10_000


@dataclass(frozen=True)
class CapEnforcementReport:
    """Outcome of one :func:`enforce_source_caps` invocation.

    ``reason`` names the terminal branch:

    - ``"no_cap"`` — neither ``max_rows`` nor ``max_bytes`` is set.
    - ``"no_prefix"`` — the plugin doesn't have a derivable URI prefix
      (unknown plugin, or e.g. ``zotero`` without a ``library_id``).
    - ``"under_cap"`` — both caps satisfied; no rows evicted.
    - ``"evicted_max_rows"`` — over the row cap; rows evicted to fit.
    - ``"evicted_max_bytes"`` — over the byte cap; rows evicted to fit.
    """

    dataset_id: int
    source_uri_prefix: str | None
    rows_before: int
    bytes_before: int
    rows_evicted: int
    bytes_evicted: int
    cap_max_rows: int | None
    cap_max_bytes: int | None
    reason: str


# ─────────────────────────────────────────────────────────────────────────
# Source-URI prefix derivation
# ─────────────────────────────────────────────────────────────────────────


def derive_source_uri_prefix(source_config: Any) -> str | None:
    """Return the URI prefix for ``source_config`` or ``None``.

    The returned prefix is what gets ``LIKE prefix || '%'``-matched
    against ``documents.source_uri`` and ``conversations.source_uri``.
    Each branch mirrors the construction used by the corresponding
    source plugin under :mod:`corpus_forge.sources`:

    - ``markdown_vault`` → ``vault://<vault_root.name>/``
    - ``claude_code``   → ``claude-code://`` (secondary scheme
      ``claude-code-history://`` is matched by
      :func:`_claude_code_extra_prefix` and summed in).
    - ``opencode``      → ``opencode://``
    - ``gemini_cli``    → ``gemini-cli://``
    - ``codex_cli``     → ``codex-cli://``
    - ``chatgpt_export``→ ``chatgpt-export://``
    - ``jsonl_chat``    → ``jsonl-chat://``
    - ``zotero``        → ``zotero://<library_id>/`` (requires
      ``library_id``; returns ``None`` when absent).
    - ``filesystem``    → ``filesystem://<root.name>/``

    Returning ``None`` causes cap enforcement to skip the source with
    a single WARNING line — preferred over the alternative of
    accidentally evicting from the wrong source.
    """

    plugin = getattr(source_config, "plugin", None)
    if not isinstance(plugin, str):
        return None

    if plugin == "markdown_vault":
        root = getattr(source_config, "vault_root", None)
        if not root:
            return None
        return f"vault://{Path(str(root)).name}/"

    if plugin == "filesystem":
        # ``filesystem`` plugin uses ``root`` (D-15). Fall back to
        # ``fs_root`` defensively in case a stale config still uses it.
        root = getattr(source_config, "root", None) or getattr(source_config, "fs_root", None)
        if not root:
            return None
        return f"filesystem://{Path(str(root)).name}/"

    if plugin == "zotero":
        # ``ZoteroSourceConfig`` exposes the library identity nested
        # under a ``zotero`` block. ``user_id`` is the canonical
        # library id in single-user mode; ``group_id`` is used in
        # group-library mode. Without either we cannot scope the
        # prefix uniquely → skip.
        nested = getattr(source_config, "zotero", None)
        if nested is not None:
            lib_id = getattr(nested, "user_id", None) or getattr(nested, "group_id", None)
            if lib_id:
                return f"zotero://{lib_id}/"
        return None

    scheme_map: dict[str, str] = {
        "claude_code": "claude-code://",
        "opencode": "opencode://",
        "gemini_cli": "gemini-cli://",
        "codex_cli": "codex-cli://",
        "chatgpt_export": "chatgpt-export://",
        "jsonl_chat": "jsonl-chat://",
    }
    return scheme_map.get(plugin)


def _claude_code_extra_prefix(plugin: str) -> str | None:
    """Return the secondary URI prefix for ``claude_code``.

    ``claude_code`` emits *two* schemes: ``claude-code://`` for the
    per-session JSONL conversations and ``claude-code-history://`` for
    the optional ``~/.claude/history.jsonl`` typed-prompt log. Both
    contribute to the same source's row + byte totals, so the cap
    enforcement loop sums them under one umbrella.
    """
    if plugin == "claude_code":
        return "claude-code-history://"
    return None


# ─────────────────────────────────────────────────────────────────────────
# Row + byte counting
# ─────────────────────────────────────────────────────────────────────────


def _schema_prefix(backend: Any) -> str:
    """Return ``"corpus."`` on Postgres-shaped backends else ``""``.

    The SQLite backend stores tables in the connection-default schema
    (so plain ``chunks`` / ``documents``). Postgres puts them under
    the ``corpus`` schema. Matches the same asymmetry handled by
    :func:`corpus_forge.admin.prune._delete_chunks`.
    """
    return "corpus." if _is_postgres_like(backend) else ""


def _placeholder(backend: Any) -> str:
    """Return the parameter placeholder for ``backend._execute``.

    Postgres (``pyformat``) → ``%s``; SQLite (``qmark``) → ``?``.
    Unknown / missing ``_paramstyle`` falls back to ``%s`` (matches
    the Postgres-friendly assumption baked into the rest of the
    admin surface).
    """
    paramstyle = getattr(backend, "_paramstyle", None)
    if isinstance(paramstyle, str) and paramstyle == "qmark":
        return "?"
    return "%s"


def count_source_rows(
    backend: Any,
    dataset_id: int,
    prefix: str,
) -> tuple[int, int]:
    """Return ``(row_count, total_bytes)`` for chunks attributed to ``prefix``.

    Counts both documents-rooted and conversations-rooted chunks
    matching ``source_uri LIKE prefix || '%'`` scoped to
    ``dataset_id``. ``total_bytes`` is the sum of ``LENGTH(chunk.text)``
    over the matched chunks. The conversations branch is guarded —
    backends that don't ship a ``conversations`` table degrade silently
    to ``(doc_count, doc_bytes)``.
    """

    execute = getattr(backend, "_execute", None)
    if not callable(execute):
        return 0, 0

    placeholder = _placeholder(backend)
    schema = _schema_prefix(backend)
    pattern = prefix + "%"

    # Documents path — always present in every supported backend.
    doc_sql = (
        f"SELECT COUNT(*) AS cnt, COALESCE(SUM(LENGTH(c.text)), 0) AS total_bytes "
        f"FROM {schema}chunks c "
        f"JOIN {schema}documents d ON d.id = c.document_id "
        f"WHERE d.dataset_id = {placeholder} AND d.source_uri LIKE {placeholder}"
    )
    try:
        doc_rows = execute(doc_sql, (dataset_id, pattern))
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("documents count probe failed: %r", exc)
        doc_rows = []
    doc_count = 0
    doc_bytes = 0
    if isinstance(doc_rows, list) and doc_rows:
        first = doc_rows[0]
        if isinstance(first, dict):
            doc_count = int(first.get("cnt", 0) or 0)
            doc_bytes = int(first.get("total_bytes", 0) or 0)

    # Conversations path — guarded for backends without the table.
    conv_count = 0
    conv_bytes = 0
    conv_sql = (
        f"SELECT COUNT(*) AS cnt, COALESCE(SUM(LENGTH(c.text)), 0) AS total_bytes "
        f"FROM {schema}chunks c "
        f"JOIN {schema}conversations cv ON cv.id = c.conversation_id "
        f"WHERE cv.dataset_id = {placeholder} AND cv.source_uri LIKE {placeholder}"
    )
    try:
        conv_rows = execute(conv_sql, (dataset_id, pattern))
    except Exception as exc:
        logger.debug("conversations count probe unavailable: %r", exc)
        conv_rows = []
    if isinstance(conv_rows, list) and conv_rows:
        first = conv_rows[0]
        if isinstance(first, dict):
            conv_count = int(first.get("cnt", 0) or 0)
            conv_bytes = int(first.get("total_bytes", 0) or 0)

    return doc_count + conv_count, doc_bytes + conv_bytes


# ─────────────────────────────────────────────────────────────────────────
# Score + evict
# ─────────────────────────────────────────────────────────────────────────


def _score_candidate(
    cand: _Candidate,
    *,
    feedback_rows_by_chunk_id: dict[int, list[dict[str, Any]]],
    minhash_module: Any | None,
) -> float:
    """Compute one candidate's prune score using the same rubric as :mod:`prune`.

    Sub-score derivation matches :func:`corpus_forge.admin.prune.prune_dataset`:

    - ``confidence_deficit`` from :func:`_compute_confidence_deficit`.
    - ``missing_metadata`` from :func:`_compute_missing_metadata`.
    - ``freshness_inverted`` is ``1.0 - _compute_freshness(...)``.
    - ``duplicate_density`` is left at ``0.0`` when MinHash is
      unavailable. Cap enforcement is a hot path on every successful
      ingest — paying for a full MinHash boot here would visibly slow
      down ingestion, so we honor the same import-driven feature flag
      :mod:`prune` uses and accept degraded scoring when the module is
      missing.
    - ``feedback_drag`` from :func:`_feedback_drag`.
    """
    deficit = _compute_confidence_deficit(cand.classifier_confidence)
    missing_score, _missing_fields = _compute_missing_metadata(cand)
    fresh = _compute_freshness(cand.modified_at)
    fresh_inv = 1.0 - fresh

    dup_density = 0.0
    if minhash_module is not None:
        try:
            distance = float(
                minhash_module.jaccard_neighbor_distance(
                    chunk_id=cand.chunk_id,
                    text=cand.text,
                )
            )
            dup_density = max(0.0, min(1.0, 1.0 - distance))
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("minhash probe raised %r — treating as 0.0", exc)
            dup_density = 0.0

    fb_drag = _feedback_drag(cand, feedback_rows_by_chunk_id)

    sub_scores: dict[str, float] = {
        "confidence_deficit": deficit,
        "missing_metadata": missing_score,
        "freshness_inverted": fresh_inv,
        "duplicate_density": dup_density,
        "feedback_drag": fb_drag,
    }
    score, _ = score_for_pruning(cand, sub_scores=sub_scores)
    return score


def _candidate_text_bytes(cand: _Candidate) -> int:
    """Return the byte cost we charge against ``max_bytes`` for ``cand``.

    ``LENGTH(chunk.text)`` is what :func:`count_source_rows` aggregates,
    so we keep the unit consistent here. ``len(str)`` is the unicode
    code-point count in Python — for ASCII corpora this matches the
    SQL ``LENGTH`` exactly; for multi-byte content it is a close
    approximation. Treating it as exact is acceptable because the cap
    is itself a soft target (we evict until under it, not until exactly
    at it) and the next ingest cycle will re-check.
    """
    return len(cand.text or "")


def _resolve_dataset_name(backend: Any, dataset_id: int) -> str | None:
    """Return the dataset name for ``dataset_id`` or ``None``.

    Cross-dataset URI-prefix collisions (e.g. two datasets each with a
    ``claude_code`` source whose prefix is ``claude-code://``) would
    otherwise let eviction in dataset A delete chunks attributed to a
    matching prefix in dataset B. We resolve the dataset name and
    forward it to :func:`_iter_curation_candidates` so the candidate
    pool is dataset-scoped at the SQL level.

    Backends MAY expose ``find_dataset_name_by_id`` directly (matches
    the existing ``find_dataset_id_by_name`` shape on
    :class:`corpus_forge.backends.base.Backend`); otherwise we fall
    back to a parametrised ``SELECT name FROM corpus.datasets WHERE
    id = ?`` using the same schema-prefix / placeholder dispatch the
    rest of this module uses.

    Returning ``None`` is defensive — the caller will fall back to a
    cross-dataset scan with a logged warning, which is no worse than
    the pre-fix behavior.
    """

    direct = getattr(backend, "find_dataset_name_by_id", None)
    if callable(direct):
        try:
            raw = direct(dataset_id)
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("find_dataset_name_by_id(%r) raised %r", dataset_id, exc)
            raw = None
        if isinstance(raw, str) and raw:
            return raw

    execute = getattr(backend, "_execute", None)
    if not callable(execute):
        return None
    placeholder = _placeholder(backend)
    schema = _schema_prefix(backend)
    sql = f"SELECT name FROM {schema}datasets WHERE id = {placeholder}"
    try:
        rows = execute(sql, (dataset_id,))
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("dataset name lookup for id=%r failed: %r", dataset_id, exc)
        return None
    if not isinstance(rows, list) or not rows:
        return None
    first = rows[0]
    name = first.get("name") if isinstance(first, dict) else None
    if isinstance(name, str) and name:
        return name
    return None


def _evict_lowest_scoring(
    backend: Any,
    prefix: str,
    *,
    dataset_id: int,
    extra_prefix: str | None,
    target_max_rows: float,
    target_max_bytes: float,
    current_rows: int,
    current_bytes: int,
) -> tuple[list[int], int]:
    """Score all chunks under ``prefix`` (+ optional ``extra_prefix``) and evict
    the lowest-scoring rows until ``current_rows`` ≤ ``target_max_rows`` AND
    ``current_bytes`` ≤ ``target_max_bytes``.

    Returns ``(deleted_chunk_ids, deleted_bytes)``.
    """

    # Pull a wide-enough candidate pool, scoped to ``dataset_id`` so a
    # cross-dataset URI-prefix collision (e.g. two datasets each with a
    # ``claude_code`` source) cannot cause eviction in dataset A to
    # delete chunks belonging to dataset B. ``_iter_curation_candidates``
    # takes a dataset *name* — we resolve id→name once here and forward
    # it. If resolution fails we fall back to the unscoped walk and log
    # a WARNING (no worse than the pre-fix behavior).
    dataset_name = _resolve_dataset_name(backend, dataset_id)
    if dataset_name is None:
        logger.warning(
            "cap enforcement: could not resolve dataset name for id=%d — "
            "candidate pool will not be dataset-scoped; cross-dataset URI "
            "collisions on prefix %r may over-evict",
            dataset_id,
            prefix,
        )

    candidates: list[_Candidate] = []
    matching_prefixes: tuple[str, ...] = (prefix, extra_prefix) if extra_prefix else (prefix,)
    for cand in _iter_curation_candidates(
        backend, dataset=dataset_name, limit=_DEFAULT_SCORING_POOL
    ):
        uri = cand.source_uri or ""
        if any(uri.startswith(p) for p in matching_prefixes):
            candidates.append(cand)

    if not candidates:
        logger.warning(
            "cap enforcement: no scorable candidates for prefix %r — "
            "skipping eviction (storage rows=%d bytes=%d)",
            prefix,
            current_rows,
            current_bytes,
        )
        return [], 0

    # Resolve optional sub-score data sources once.
    minhash_module: Any | None = None
    if _minhash_available():
        minhash_module = importlib.import_module("corpus_forge.quality.minhash")

    feedback_rows = _load_feedback_by_chunk_id(backend)

    # Score every candidate, then sort highest-score-first (most
    # prunable first). The eviction loop walks this list and deletes
    # until both targets are met.
    scored: list[tuple[float, int, int]] = []  # (score, chunk_id, byte_cost)
    for cand in candidates:
        score = _score_candidate(
            cand,
            feedback_rows_by_chunk_id=feedback_rows,
            minhash_module=minhash_module,
        )
        scored.append((score, cand.chunk_id, _candidate_text_bytes(cand)))
    scored.sort(key=lambda entry: entry[0], reverse=True)

    # Walk and accumulate eviction list until both caps are satisfied.
    rows_remaining = float(current_rows)
    bytes_remaining = float(current_bytes)
    to_delete: list[int] = []
    deleted_bytes = 0
    for _score, chunk_id, byte_cost in scored:
        if rows_remaining <= target_max_rows and bytes_remaining <= target_max_bytes:
            break
        to_delete.append(chunk_id)
        deleted_bytes += byte_cost
        rows_remaining -= 1
        bytes_remaining -= byte_cost

    if not to_delete:
        return [], 0

    try:
        _delete_chunks(backend, to_delete)
    except Exception:
        logger.exception(
            "cap enforcement: bulk delete failed for %d ids (prefix=%r)",
            len(to_delete),
            prefix,
        )
        raise
    return to_delete, deleted_bytes


# ─────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────


def enforce_source_caps(
    backend: Any,
    dataset_id: int,
    source_config: Any,
) -> CapEnforcementReport:
    """Enforce per-source row + byte caps on ``source_config``.

    No-op when neither ``max_rows`` nor ``max_bytes`` is set. When set
    and the source is over either cap, evicts the lowest-scoring
    chunks (by the
    :func:`corpus_forge.curation.selector.score_for_pruning` rubric)
    until back under all configured caps.

    Returns a :class:`CapEnforcementReport` whose ``reason`` field
    names the terminal branch — see the dataclass docstring.
    """

    max_rows = getattr(source_config, "max_rows", None)
    max_bytes = getattr(source_config, "max_bytes", None)

    if max_rows is None and max_bytes is None:
        return CapEnforcementReport(
            dataset_id=dataset_id,
            source_uri_prefix=None,
            rows_before=0,
            bytes_before=0,
            rows_evicted=0,
            bytes_evicted=0,
            cap_max_rows=None,
            cap_max_bytes=None,
            reason="no_cap",
        )

    prefix = derive_source_uri_prefix(source_config)
    if prefix is None:
        plugin = getattr(source_config, "plugin", "?")
        logger.warning(
            "cap enforcement skipped: cannot derive URI prefix for plugin %r "
            "(max_rows=%s max_bytes=%s)",
            plugin,
            max_rows,
            max_bytes,
        )
        return CapEnforcementReport(
            dataset_id=dataset_id,
            source_uri_prefix=None,
            rows_before=0,
            bytes_before=0,
            rows_evicted=0,
            bytes_evicted=0,
            cap_max_rows=max_rows,
            cap_max_bytes=max_bytes,
            reason="no_prefix",
        )

    plugin = getattr(source_config, "plugin", "")
    rows, total_bytes = count_source_rows(backend, dataset_id, prefix)
    extra_prefix = _claude_code_extra_prefix(plugin) if isinstance(plugin, str) else None
    if extra_prefix:
        extra_rows, extra_bytes = count_source_rows(backend, dataset_id, extra_prefix)
        rows += extra_rows
        total_bytes += extra_bytes

    over_rows = max_rows is not None and rows > max_rows
    over_bytes = max_bytes is not None and total_bytes > max_bytes
    if not (over_rows or over_bytes):
        return CapEnforcementReport(
            dataset_id=dataset_id,
            source_uri_prefix=prefix,
            rows_before=rows,
            bytes_before=total_bytes,
            rows_evicted=0,
            bytes_evicted=0,
            cap_max_rows=max_rows,
            cap_max_bytes=max_bytes,
            reason="under_cap",
        )

    target_max_rows: float = float(max_rows) if max_rows is not None else float("inf")
    target_max_bytes: float = float(max_bytes) if max_bytes is not None else float("inf")

    evicted_ids, evicted_bytes = _evict_lowest_scoring(
        backend,
        prefix,
        dataset_id=dataset_id,
        extra_prefix=extra_prefix,
        target_max_rows=target_max_rows,
        target_max_bytes=target_max_bytes,
        current_rows=rows,
        current_bytes=total_bytes,
    )

    reason = "evicted_max_rows" if over_rows else "evicted_max_bytes"
    return CapEnforcementReport(
        dataset_id=dataset_id,
        source_uri_prefix=prefix,
        rows_before=rows,
        bytes_before=total_bytes,
        rows_evicted=len(evicted_ids),
        bytes_evicted=evicted_bytes,
        cap_max_rows=max_rows,
        cap_max_bytes=max_bytes,
        reason=reason,
    )


__all__ = [
    "CapEnforcementReport",
    "count_source_rows",
    "derive_source_uri_prefix",
    "enforce_source_caps",
]
