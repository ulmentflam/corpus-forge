"""Phase R4 — `CrossEncoderReranker`.

Wraps ``sentence_transformers.CrossEncoder``.  **Lazy-loaded**: the
constructor stores config; the model is downloaded + initialised on the
first call to :meth:`warmup` or :meth:`rerank` (whichever comes first).

Default model: ``BAAI/bge-reranker-v2-m3`` (multilingual, ~600 MB).
Override via the ``model_id`` constructor kwarg.  The lighter English-only
alternate is ``cross-encoder/ms-marco-MiniLM-L-12-v2``.

Pairing convention
------------------

``rerank(query, hits, *, top_n)`` builds ``(query, hit.text)`` tuples and
passes them to ``CrossEncoder.predict(...)``.  The returned scores
replace the upstream fused score; ``source`` is set to ``"reranked"``.

Tie-breaking
------------

Sort key is descending by the new cross-encoder score.  Ties are broken
by descending FUSED score (the upstream score stored in the input hit),
then ascending ``chunk_id``.  This keeps the order stable across runs
even when the cross-encoder returns identical scores for distinct hits.

``top_n`` semantics
-------------------

* ``top_n is None``: rerank every input hit; output preserves the full
  length of the input.
* ``top_n: int``: take the top ``top_n`` of the input by FUSED score (the
  order in which the caller passed them — assumed already sorted by
  fused score), rerank just those, return them.

Empty input
-----------

``rerank(query, [])`` returns ``[]`` WITHOUT calling :meth:`_get_model`.
This keeps reranker construction + an unused search path strictly
zero-cost (no model download triggered on warm paths that happen to
return zero fused hits).

Missing ``[rerank]`` extra
--------------------------

``sentence-transformers`` is currently a hard dep of ``corpus-forge``
(see ``pyproject.toml``), so the "without the extra" path is unreachable
in practice.  The ImportError defence in :meth:`_get_model` is kept for
the day the dep moves behind the extra.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from corpus_forge.retrieval.types import Hit

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


# Default model id — the locked Phase R4 decision (see master plan).
DEFAULT_MODEL_ID = "BAAI/bge-reranker-v2-m3"
DEFAULT_NAME = "bge-reranker-v2-m3"

# Default device sentinel.  Resolved at first model-load.
_AUTO_DEVICE = "auto"


def _resolve_device(device: str) -> str:
    """Translate the ``"auto"`` sentinel into the best concrete device.

    Thin wrapper over :func:`corpus_forge._ml_device.resolve_device`
    kept as a private alias so callers in this module don't have to
    import it; the heuristic itself (MPS > CUDA > CPU) lives in one
    place now.
    """
    from corpus_forge._ml_device import resolve_device  # noqa: PLC0415

    return resolve_device(device)


class CrossEncoderReranker:
    """Cross-encoder reranker over an existing fused hit list.

    Construction is cheap; the heavy model load happens lazily on the
    first call to :meth:`warmup` or :meth:`rerank`.

    Args:
        model_id: HuggingFace model id (default: ``BAAI/bge-reranker-v2-m3``).
        device: ``"auto"`` (default) / ``"cpu"`` / ``"cuda"`` / ``"mps"``.
        batch_size: Forwarded to ``CrossEncoder.predict``.
        max_length: Forwarded to the underlying ``CrossEncoder``.
        name: Short human-readable label.
    """

    name: str
    model_id: str

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        *,
        device: str = _AUTO_DEVICE,
        batch_size: int = 32,
        max_length: int = 512,
        name: str = DEFAULT_NAME,
    ) -> None:
        self.model_id = model_id
        self.device = device
        self.batch_size = batch_size
        self.max_length = max_length
        self.name = name
        # Memoised model handle.  Populated by :meth:`_get_model`.
        self._model: Any | None = None

    # ── lazy model accessor ────────────────────────────────────────────────

    def _get_model(self) -> Any:
        """Load ``CrossEncoder`` on first call; return the cached handle.

        Raises:
            ImportError: when ``sentence_transformers`` cannot be imported.
                The error message points the user at ``pip install
                corpus-forge[rerank]``.
        """
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import CrossEncoder  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - ST is currently a hard dep
            raise ImportError(
                "CrossEncoderReranker requires `sentence-transformers`. "
                "Install with: pip install 'corpus-forge[rerank]' "
                "(or pip install sentence-transformers>=3.0)."
            ) from exc

        device = _resolve_device(self.device)
        self._model = CrossEncoder(
            self.model_id,
            max_length=self.max_length,
            device=device,
        )
        return self._model

    # ── public API ─────────────────────────────────────────────────────────

    def warmup(self) -> None:
        """Eagerly load the model and run a single tiny inference."""
        model = self._get_model()
        model.predict([("warmup", "warmup")], batch_size=self.batch_size)

    def rerank(
        self,
        query: str,
        hits: list[Hit],
        *,
        top_n: int | None = None,
    ) -> list[Hit]:
        """Re-score and re-sort ``hits``.

        See module docstring for ``top_n`` / tie-break semantics.
        """
        if not hits:
            # Short-circuit BEFORE model load: empty input is free.
            return []

        # Take the candidate window.  Input is assumed already sorted by
        # fused score descending (R2 HybridRetriever guarantees this).
        candidates = hits[:top_n] if top_n is not None else list(hits)

        model = self._get_model()
        pairs = [(query, h.text) for h in candidates]
        raw_scores = model.predict(pairs, batch_size=self.batch_size)
        # `predict` may return list-like, np.ndarray, or torch.Tensor; coerce
        # via float() on each element.
        scores: list[float] = [float(s) for s in raw_scores]

        # Sort key: (-new_score, -fused_score, chunk_id) — stable, deterministic.
        indexed = list(zip(candidates, scores, strict=True))
        indexed.sort(key=lambda pair: (-pair[1], -pair[0].score, pair[0].chunk_id))

        out: list[Hit] = []
        for hit, new_score in indexed:
            out.append(
                Hit(
                    chunk_id=hit.chunk_id,
                    score=new_score,
                    text=hit.text,
                    document_id=hit.document_id,
                    source_uri=hit.source_uri,
                    title=hit.title,
                    dataset_id=hit.dataset_id,
                    metadata=hit.metadata,
                    source="reranked",
                )
            )

        if top_n is not None:
            return out[:top_n]
        return out
