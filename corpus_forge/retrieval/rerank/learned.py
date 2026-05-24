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
    conn: Any,  # DB-API 2.0 connection (sqlite3 or psycopg) — duck-typed
    out_path: Path,
    *,
    source_filter: list[str] | None = None,
) -> dict[str, object]:
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
    # Detect placeholder style from the connection — psycopg uses "%s",
    # sqlite3 uses "?". Both backends expose `paramstyle` via class module.
    is_postgres = "psycopg" in type(conn).__module__
    ph = "%s" if is_postgres else "?"

    base_sql = (
        "SELECT sre.signal, sre.value, sre.source, ss.query "
        "FROM search_result_events AS sre "
        "JOIN search_sessions AS ss ON ss.id = sre.session_id"
    )

    if source_filter:  # non-None AND non-empty — empty list would produce IN ().
        placeholders = ",".join([ph] * len(source_filter))
        sql = f"{base_sql} WHERE sre.source IN ({placeholders})"
        cursor = conn.execute(sql, source_filter)
    else:
        # Empty source_filter is treated the same as None — fetch everything.
        cursor = conn.execute(base_sql)

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
    # Single-class guard — moved BEFORE clf.fit so sklearn never sees a
    # one-class y (which raises its own less-helpful ValueError).
    # ------------------------------------------------------------------
    unique_labels = set(y)
    auc: float | None = None

    clf = LogisticRegression(random_state=42, max_iter=500)
    if len(unique_labels) < 2:
        # sklearn can't fit on a single class. Persist a degenerate model
        # that always predicts the lone observed label so the caller still
        # gets a usable artifact with auc=None recorded.
        from sklearn.dummy import DummyClassifier

        # sklearn estimator base type is too narrow for ``fit(X=list[...])``
        # — sklearn's stubs accept only ``ndarray | DataFrame | spmatrix``
        # but runtime accepts a plain list-of-lists.  Keeping ``Any`` here
        # bypasses sklearn's overly-strict stub for the duck-typed code
        # path; the persisted artifact's typing is tightened elsewhere.
        clf_to_persist: Any = DummyClassifier(strategy="most_frequent")
        clf_to_persist.fit(X, y)
        clf = clf_to_persist
    else:
        clf.fit(X, y)

        import numpy as np

        probs = clf.predict_proba(np.asarray(X))[:, 1]
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
        self._artifact: dict[str, object] | None = None

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

        # Predict positive-class probabilities. Resolve the column index
        # for the positive label (1) instead of assuming column 1 — that
        # breaks on single-class artifacts produced by train_reranker's
        # DummyClassifier fallback. Guard for estimators without
        # predict_proba (fall back to predict / uniform scores).
        positive_label = 1
        if hasattr(clf, "predict_proba"):
            raw_probs = clf.predict_proba(features)
            classes = list(getattr(clf, "classes_", [positive_label]))
            if positive_label in classes:
                col = classes.index(positive_label)
            elif raw_probs.shape[1] == 1:
                col = 0
            else:
                # Multi-class but no positive label seen — pick the last
                # column as a deterministic fallback.
                col = raw_probs.shape[1] - 1
            probs: list[float] = [float(p) for p in raw_probs[:, col]]
        elif hasattr(clf, "predict"):
            preds = clf.predict(features)
            probs = [1.0 if int(p) == positive_label else 0.0 for p in preds]
        else:
            probs = [0.5] * len(features)

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
