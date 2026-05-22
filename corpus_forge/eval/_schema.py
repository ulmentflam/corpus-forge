"""Shared output envelope for every ``corpus-forge eval *`` subcommand.

Backs RFC ``rfc-eval-framework-expansion`` (P1). The retrieval-eval
harness (``corpus_forge/eval/runner.py``) and the three new evaluators
landing in subsequent PRs (classifier accuracy, chunk quality,
regression) all emit JSON to stdout / ``--out`` so downstream
dashboards can plot the metrics on one timeline. A shared envelope
makes the dashboard side trivial: every record carries the same
top-level keys.

Why a separate module instead of extending ``runner.py``
--------------------------------------------------------

- All three new evaluators import it without touching the retrieval-eval
  code path.
- The envelope is dialect-free (no retrieval-specific fields) so it can
  serialise classifier confusion matrices, quality MAE, and regression
  diffs without any of them knowing about the others.
- The leading underscore (``_schema``) keeps it private to the ``eval``
  package — callers reach for ``corpus_forge.eval.runner`` or the future
  per-evaluator modules, not this one directly.

Forward-compatibility
---------------------

Future evaluator kinds will extend the ``eval_kind`` literal. Adding
``"distill"`` to that literal is a one-line change here; consumers
that filter on the field upgrade by widening their own match. Today
the literal already covers the four kinds the RFC scopes:
``classifier``, ``quality``, ``retrieval``, ``regression``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EvalKind = Literal["classifier", "quality", "retrieval", "regression"]


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with a ``Z`` suffix.

    Default factory for :attr:`EvalOutput.ts`. Surfaced as a module-level
    helper so tests can monkeypatch it for deterministic fixtures.
    """
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class EvalOutput(BaseModel):
    """Top-level envelope returned by every eval subcommand.

    All evaluator subcommands (``eval retrieval``, ``eval classifier``,
    ``eval quality``, ``eval regression``) marshal their results into
    this shape and emit ``EvalOutput.model_dump_json()`` to stdout or
    ``--out``. The dashboard layer (out of scope here) reads only these
    top-level keys.

    Fields:

    - ``eval_kind``: which evaluator produced this record. One of
      ``"classifier"``, ``"quality"``, ``"retrieval"``, ``"regression"``.
    - ``dataset``: the dataset name the evaluator ran against.
    - ``git_commit``: the commit SHA the evaluator was run against, or
      ``None`` when the working tree is detached / not a git checkout
      / git is unavailable. Best-effort — the evaluator runners populate
      this via ``corpus_forge.sources._git.git_context()`` (PR #34) when
      that helper lands, and pass ``None`` until then.
    - ``ts``: UTC ISO 8601 timestamp of when the evaluator finished.
      Defaults to "now."
    - ``metrics``: the per-kind metric payload. Free-form ``dict`` because
      each evaluator has a different metric shape:

      * ``retrieval``: ``{"ndcg": {5: 0.8, 10: 0.85}, "mrr": ..., ...}``
        (mirrors the existing ``RetrievalMetrics`` dataclass).
      * ``classifier``: ``{"precision": {"code": 0.92, "doc": 0.81, ...},
        "recall": {...}, "f1": {...}, "macro_f1": 0.86, "confusion": ...}``.
      * ``quality``: ``{"mae": {"clarity": 0.4, ...}, "spearman": {...},
        "n_chunks": 250}``.
      * ``regression``: ``{"deltas": {<metric>: <signed_delta>},
        "violations": [<metric_name>, ...], "tolerance": {...}}``.

      The structure is asserted-on by the per-evaluator unit tests, not
      here — this envelope is dialect-neutral on purpose.
    - ``config``: a free-form ``dict`` of caller-relevant config snapshot
      (e.g. retrieval's fusion strategy + alpha + k_values; classifier's
      gold path; regression's tolerance band). Captured so a re-run can
      reproduce the same evaluation deterministically.
    """

    eval_kind: EvalKind
    dataset: str
    git_commit: str | None = None
    ts: str = Field(default_factory=_utc_now_iso)
    metrics: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")
