"""Phase P Wave 2 — ``LearnedReranker`` + ``train_reranker``.

Trains a lightweight ``sklearn.linear_model.LogisticRegression`` model on
``search_result_events`` feedback signals and uses it as a second-stage
reranker on top of fused retrieval results.

Lazy-import discipline
----------------------
``sklearn`` and ``joblib`` are imported **inside function bodies** so that
``import corpus_forge.retrieval.rerank.learned`` is cheap — no heavy ML deps
are loaded at module import time.  The wave gate enforces this with a
``python -c`` snapshot assertion.

Feature specification (pinned)
-------------------------------
Three features per training row:

``chunk_score``
    The numeric ``value`` field from ``search_result_events``; defaults to
    ``0.5`` when ``NULL``.

``lexical_score``
    ``1.0`` if the event's ``source == "lexical"``, otherwise ``0.0``.

``query_len``
    ``len(query)`` from the joined ``search_sessions`` row.

Label derivation (pinned)
--------------------------
* Positive (1): ``signal == "thumbs_up"`` **or** ``value > 0.5``.
* Negative (0): ``signal == "thumbs_down"`` **or** ``value < 0.5``.
* Neutral (skip): ``value == 0.5`` with no explicit thumbs signal.

Artifact format
---------------
The persisted joblib file contains a ``dict``::

    {"model": LogisticRegression, "feature_spec": list[str]}

so future loaders can detect feature drift.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from corpus_forge.retrieval.types import Hit

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

_FEATURE_SPEC = ["chunk_score", "lexical_score", "query_len"]

_DEFAULT_NAME = "learned-reranker"


# ---------------------------------------------------------------------------
# train_reranker
# ---------------------------------------------------------------------------


def train_reranker(
    conn: Any,
    out_path: Path,
    *,
    source_filter: list[str] | None = None,
) -> dict[str, Any]:
    """Train a LogisticRegression reranker on search-feedback events.

    Pulls rows from ``search_result_events`` joined to ``search_sessions``
    (via ``session_id``), derives feature vectors and binary labels, trains
    an ``sklearn.linear_model.LogisticRegression``, and persists the model
    artifact at ``out_path`` via ``joblib.dump``.

    Args:
        conn:
            A DB-API 2.0 connection (sqlite3 or psycopg2-compatible) with
            ``search_sessions`` and ``search_result_events`` tables present.
        out_path:
            Filesystem path where the serialised model artifact will be
            written (e.g. ``Path("~/.config/corpus-forge/reranker.joblib")``).
        source_filter:
            When provided, only rows whose ``search_result_events.source``
            is in this list are used for training.  ``None`` = all sources.

    Returns:
        A dict with keys:

        ``n_train`` (int)
            Total labelled rows used for training (pos + neg).
        ``n_pos`` (int)
            Number of positive labels.
        ``n_neg`` (int)
            Number of negative labels.
        ``auc`` (float | None)
            ROC-AUC on the training set.  ``None`` when only a single class
            is present (single-class LogisticRegression scenario).
        ``model_path`` (str)
            String representation of ``out_path``.

    Raises:
        ValueError:
            When the events table is empty or yields no labelled rows after
            neutral filtering (so callers get a clear error instead of a
            phantom zero-sample model).
    """
    import joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    # ------------------------------------------------------------------
    # Fetch rows
    # ------------------------------------------------------------------
    if source_filter is not None:
        placeholders = ",".join("?" * len(source_filter))
        sql = f"""
            SELECT
                sre.signal,
                sre.value,
                sre.source,
                ss.query
            FROM search_result_events AS sre
            JOIN search_sessions AS ss ON ss.id = sre.session_id
            WHERE sre.source IN ({placeholders})
        """
        cursor = conn.execute(sql, source_filter)
    else:
        sql = """
            SELECT
                sre.signal,
                sre.value,
                sre.source,
                ss.query
            FROM search_result_events AS sre
            JOIN search_sessions AS ss ON ss.id = sre.session_id
        """
        cursor = conn.execute(sql)

    rows = cursor.fetchall()

    # ------------------------------------------------------------------
    # Build feature matrix + labels (skip neutral rows)
    # ------------------------------------------------------------------
    X: list[list[float]] = []
    y: list[int] = []

    for row in rows:
        # Support both sqlite3.Row (subscript by name) and plain tuples.
        if hasattr(row, "keys"):
            signal = row["signal"]
            raw_value = row["value"]
            source = row["source"]
            query = row["query"]
        else:
            signal, raw_value, source, query = row

        chunk_score: float = float(raw_value) if raw_value is not None else 0.5
        lexical_score: float = 1.0 if source == "lexical" else 0.0
        query_len: float = float(len(query))

        # Label derivation
        if signal == "thumbs_up" or (raw_value is not None and chunk_score > 0.5):
            label = 1
        elif signal == "thumbs_down" or (raw_value is not None and chunk_score < 0.5):
            label = 0
        else:
            # Neutral (value == 0.5 without explicit thumbs) — skip.
            continue

        X.append([chunk_score, lexical_score, query_len])
        y.append(label)

    if not X:
        raise ValueError(
            "No labeled training rows found in search_result_events. "
            "Collect thumbs_up / thumbs_down feedback before training."
        )

    n_pos = sum(1 for lbl in y if lbl == 1)
    n_neg = sum(1 for lbl in y if lbl == 0)
    n_train = len(y)

    # ------------------------------------------------------------------
    # Train
    # ------------------------------------------------------------------
    clf = LogisticRegression(random_state=42, max_iter=500)
    clf.fit(X, y)

    # ------------------------------------------------------------------
    # AUC — None when single class present
    # ------------------------------------------------------------------
    auc: float | None
    unique_labels = set(y)
    if len(unique_labels) < 2:
        auc = None
    else:
        probs = clf.predict_proba(X)[:, 1]
        auc = float(roc_auc_score(y, probs))

    # ------------------------------------------------------------------
    # Persist
    # ------------------------------------------------------------------
    artifact = {
        "model": clf,
        "feature_spec": _FEATURE_SPEC,
    }
    joblib.dump(artifact, out_path)

    return {
        "n_train": n_train,
        "n_pos": n_pos,
        "n_neg": n_neg,
        "auc": auc,
        "model_path": str(out_path),
    }


# ---------------------------------------------------------------------------
# LearnedReranker
# ---------------------------------------------------------------------------


class LearnedReranker:
    """Second-stage reranker backed by a persisted LogisticRegression model.

    Satisfies the :class:`~corpus_forge.retrieval.rerank.base.Reranker`
    Protocol (runtime-checkable).

    Construction is deliberately cheap — the joblib artifact is **not**
    loaded in ``__init__``; it is loaded on the first call to
    :meth:`rerank` (or :meth:`warmup`).

    Args:
        model_path:
            Path to the joblib artifact produced by :func:`train_reranker`.
    """

    name: str = _DEFAULT_NAME
    model_id: str

    def __init__(self, model_path: Path) -> None:
        self.model_path = model_path
        self.model_id = str(model_path)
        # Memoised model artifact.  Populated by :meth:`_load_model`.
        self._artifact: dict[str, Any] | None = None

    # ── lazy model accessor ────────────────────────────────────────────────

    def _load_model(self) -> None:
        """Load the joblib artifact into ``self._artifact`` (idempotent)."""
        import joblib

        self._artifact = joblib.load(self.model_path)

    def _ensure_loaded(self) -> None:
        """Ensure the model is loaded; call ``_load_model`` if not yet done."""
        if self._artifact is None:
            self._load_model()

    # ── public API ─────────────────────────────────────────────────────────

    def warmup(self) -> None:
        """Eagerly load the model artifact from disk."""
        self._ensure_loaded()

    def rerank(
        self,
        query: str,
        hits: list[Hit],
        *,
        top_n: int | None = None,
    ) -> list[Hit]:
        """Re-score and re-sort ``hits`` using the trained logistic model.

        Empty ``hits`` returns ``[]`` WITHOUT loading the model artifact.

        Args:
            query: The raw query string.
            hits: Fused hit list to rerank (assumed pre-sorted by fused score).
            top_n:
                When not ``None``, return at most ``top_n`` hits.
                ``None`` returns all hits (re-ordered).

        Returns:
            A new ``list[Hit]`` sorted descending by the predicted positive-
            class probability, with ``source`` set to ``"reranked"`` on each
            hit.  All other ``Hit`` fields are preserved unchanged.
        """
        if not hits:
            return []

        self._ensure_loaded()

        assert self._artifact is not None  # narrowing for type checkers
        clf = self._artifact["model"]

        # Build feature matrix
        query_len = float(len(query))
        features: list[list[float]] = []
        for hit in hits:
            chunk_score = float(hit.score)
            lexical_score = 1.0 if hit.source == "lexical" else 0.0
            features.append([chunk_score, lexical_score, query_len])

        # Predict positive-class probabilities
        probs: list[float] = [float(p) for p in clf.predict_proba(features)[:, 1]]

        # Sort descending by predicted score
        indexed = list(zip(hits, probs, strict=True))
        indexed.sort(key=lambda pair: (-pair[1], -pair[0].score, pair[0].chunk_id))

        # Build output with source="reranked" and the predicted score
        out: list[Hit] = []
        for hit, prob in indexed:
            out.append(
                Hit(
                    chunk_id=hit.chunk_id,
                    score=prob,
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
