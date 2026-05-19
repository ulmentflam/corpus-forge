"""Phase M Wave 5 — research-only ``SembleRetriever`` adapter.

Bridges `MinishLab/semble`'s ``SembleIndex`` (in-memory, chunk-oriented, file
+ line-range keyed) onto the corpus-forge ``Retriever`` protocol
(``search(query, options) -> list[Hit]``).

This file is **not** part of the corpus-forge wheel.  It lives under the
top-level ``experiments/`` directory which is excluded from the
``hatch.build`` wheel target and from the Docker image (see
``pyproject.toml`` / ``.dockerignore``).  Production code MUST NOT import
from ``experiments``.

Hard scope limits (Phase M Wave 5):

- No changes anywhere under ``corpus_forge/``.
- No new top-level deps in ``pyproject.toml``.  ``semble`` is expected to
  be installed manually into the bench venv (``uv pip install semble`` or
  pinned commit) — this module imports it lazily inside ``__init__`` so
  the file is importable without ``semble`` present.
- In-memory only.  No interaction with ``StorageBackend``, embedders, or
  the existing retrieval surface.

Mapping decisions
-----------------

``corpus_forge.retrieval.types.Hit`` carries a backend ``chunk_id: int``
and ``dataset_id: int``.  Semble has no integer chunk ids — chunks are
identified by (file_path, start_line, end_line).  This adapter:

- Generates a dense 0-based integer id by enumeration order of the index
  (the same index ordering ``SembleIndex.chunks`` uses), stored on a
  ``chunk_id`` attribute so the bench harness can join back to
  ``(file_path, start_line, end_line)`` when scoring.
- Reports ``dataset_id=0`` (the spike has no notion of multi-dataset).
- Stuffs the semble chunk's ``file_path`` / ``start_line`` / ``end_line``
  / ``language`` / ``source`` (semble's HYBRID/SEMANTIC/BM25 label) into
  the ``Hit.metadata`` dict so the bench's ground-truth scorer can match
  on byte spans without losing semble's own ranking signals.
- Maps ``SearchResult.score`` through unchanged.  Semble's hybrid score
  is roughly in [0, 1] but is RRF-fused → reranker-adjusted; it is NOT
  directly comparable to ``HybridRetriever``'s RRF score and the bench
  should treat scores as ordinal, not cardinal.

The ``Retriever`` protocol's ``SearchOptions`` carries ``k``, ``dataset``,
``fusion``, ``alpha``, ``rerank``, ``rerank_top_n``.  This adapter honors
``k`` directly and ignores everything else (with a single exception: if
``options.fusion == "alpha"`` we forward ``options.alpha`` to semble as
its ``alpha`` parameter; otherwise we pass ``None`` so semble's
auto-detect picks the per-query default).  ``options.dataset`` is
silently ignored — single-corpus spike.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from corpus_forge.retrieval.types import Hit, SearchOptions


@dataclass(frozen=True)
class SembleHit:
    """A ``Hit``-shaped dataclass for SembleRetriever output.

    Structurally compatible with ``corpus_forge.retrieval.types.Hit`` —
    the bench harness coerces this into a real ``Hit`` when it needs to.

    We do **not** import ``Hit`` at module level to keep the experiments/
    directory zero-touch on the production package — the adapter is
    importable in environments where ``corpus_forge`` is uninstalled
    (e.g. a clean spike venv).
    """

    chunk_id: int
    score: float
    text: str
    document_id: int | None
    source_uri: str | None
    title: str | None
    dataset_id: int
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str = "fused"


class SembleRetriever:
    """In-memory wrapper that adapts ``SembleIndex`` to ``Retriever``.

    Usage in the bench::

        retriever = SembleRetriever.from_path(repo_root, extensions=...)
        hits = retriever.search("...", SearchOptions(k=10))

    The ``chunk_id`` field on each returned hit indexes back into
    ``retriever.chunks`` (a snapshot of ``SembleIndex.chunks`` at
    construction time) so the bench harness can recover the
    ``(file_path, start_line, end_line)`` triplet for ground-truth
    matching.  The ``metadata`` dict carries the same triplet eagerly so
    callers do not need to dereference ``retriever.chunks``.
    """

    def __init__(self, index: Any) -> None:
        """Construct from a pre-built ``SembleIndex``.

        Args:
            index: a ``semble.SembleIndex`` instance.  Stored by
                reference; the caller owns its lifetime.
        """
        self._index = index
        # Snapshot the chunk list so ``chunk_id -> (file, lines)`` joins
        # are stable across calls even if a future semble version starts
        # re-indexing in place.
        self.chunks: list[Any] = list(index.chunks)
        # file_path -> {line: chunk_idx} for fast (file, line) -> chunk_id
        # lookups; only built on demand because most calls don't need it.
        self._line_index: dict[str, list[tuple[int, int, int]]] | None = None

    # ── construction helpers ────────────────────────────────────────────

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        extensions: list[str] | None = None,
        include_text_files: bool = True,
    ) -> SembleRetriever:
        """Build an in-memory index from a directory.

        Args:
            path: root directory to index (typically the corpus-forge repo
                checkout at a pinned commit).
            extensions: optional list of file extensions to include.  When
                ``None``, semble's default code-extension set is used.
            include_text_files: when True, semble also indexes ``.md``,
                ``.yaml``, ``.json`` etc.  Defaults to ``True`` for this
                spike because the bench corpus includes markdown docs.
        """
        from semble import SembleIndex  # noqa: PLC0415  # lazy: optional dep

        index = SembleIndex.from_path(
            Path(path),
            extensions=extensions,
            include_text_files=include_text_files,
        )
        return cls(index)

    # ── Retriever protocol ──────────────────────────────────────────────

    def search(self, query: str, options: SearchOptions) -> list[Hit]:
        """Run the search and return up to ``options.k`` hits.

        Honored options: ``k``, ``alpha`` (only when
        ``options.fusion == "alpha"``).  Everything else is silently
        ignored — see module docstring.
        """
        # Lazy semble import (kept inside the call so test_metrics.py can
        # run ungated without semble installed).
        from semble import SearchMode  # noqa: PLC0415  # lazy: optional dep

        alpha = options.alpha if getattr(options, "fusion", None) == "alpha" else None
        results = self._index.search(
            query,
            top_k=options.k,
            mode=SearchMode.HYBRID,
            alpha=alpha,
        )

        # Build (file_path, start_line, end_line) -> chunk_id (= index in
        # self.chunks) so we can stamp ``chunk_id`` deterministically.
        # This is the inverse of the snapshot built in ``__init__`` and
        # is rebuilt per-call (cheap; <2k chunks for this repo).
        key_to_id: dict[tuple[str, int, int], int] = {
            (c.file_path, c.start_line, c.end_line): i
            for i, c in enumerate(self.chunks)
        }

        hits: list[SembleHit] = []
        for r in results:
            c = r.chunk
            cid = key_to_id.get((c.file_path, c.start_line, c.end_line), -1)
            hits.append(
                SembleHit(
                    chunk_id=cid,
                    score=float(r.score),
                    text=c.content,
                    document_id=None,
                    source_uri=f"file://{c.file_path}",
                    title=None,
                    dataset_id=0,
                    metadata={
                        "file_path": c.file_path,
                        "start_line": c.start_line,
                        "end_line": c.end_line,
                        "language": c.language,
                        "semble_source": str(r.source),
                    },
                    source="fused",
                )
            )
        return hits  # type: ignore[return-value]

    # ── bench-harness helpers ───────────────────────────────────────────

    def chunk_span_for_hit(self, hit: Any) -> tuple[str, int, int] | None:
        """Return ``(file_path, start_line, end_line)`` for ``hit``.

        ``hit`` may be a ``SembleHit`` from this adapter OR a generic
        ``Hit`` whose ``metadata`` dict carries the same keys.  Returns
        ``None`` when the keys are absent (e.g. a ``HybridRetriever`` hit
        passed in by mistake).
        """
        meta = getattr(hit, "metadata", None) or {}
        fp = meta.get("file_path")
        sl = meta.get("start_line")
        el = meta.get("end_line")
        if fp is None or sl is None or el is None:
            return None
        return (str(fp), int(sl), int(el))
