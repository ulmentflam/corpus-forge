"""Classifier protocol + dataclasses.

Phase E / Wave 0 — C-01.

`Classifier` is the seam that lets corpus-forge assign a content-class
label (one of nine values — see :data:`ALLOWED_CLASS_VALUES`) to every
ingested document. A concrete classifier reads a
:class:`ClassifiableDocument` and returns a :class:`ClassLabel` or
``None`` (pass to the next classifier in the chain).

This module is dependency-free on purpose — the rule-based default is
stdlib-only and the LLM classifier (P1) lazy-imports `requests` at call
time. Keeping `base.py` import-cheap means
:class:`~corpus_forge.classifiers.registry.ClassifierRegistry` can be
constructed during config load without dragging optional deps into the
import graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Exception hierarchy — Phase E / Wave 3 (C-10/11).
# ---------------------------------------------------------------------------
#
# Transport-layer failures from the LLM classifier (and any future
# remote-model classifier) raise these exceptions; callers in
# ``corpus_forge/cli.py::classify`` already wrap the whole chain walk
# in a try/except so an unreachable LLM downgrades gracefully to the
# next classifier instead of crashing the run.
#
# Output-validation failures (model returned an invalid ``class`` value
# or unparseable inner JSON) do NOT raise — the LLM classifier falls
# back to ``ClassLabel(value="other", confidence=0.2, rationale=...)``
# so the chain can keep moving. See :class:`LLMClassifier`.


class ClassifierError(Exception):
    """Base for every classifier-layer operational failure.

    Callers can ``except ClassifierError`` to swallow all classifier
    failures uniformly. Each subclass carves out a discriminable
    failure mode so smarter callers can decide whether to retry,
    degrade, or surface a hard error.
    """


class ClassifierUnavailableError(ClassifierError):
    """The classifier backend cannot be reached or is not configured.

    Raised by:

    - :class:`~corpus_forge.classifiers.llm.LLMClassifier` when the
      Ollama daemon (or remote LLM endpoint) is down or unreachable.
    """


class ClassifierTimeoutError(ClassifierError):
    """The classifier was reachable but exceeded the configured timeout.

    Distinct from :class:`ClassifierUnavailableError` so callers can
    implement bounded retry/back-off (raising the timeout, falling back
    to a different classifier in the chain) without giving up entirely.
    """


class ClassifierResponseError(ClassifierError):
    """The classifier returned a malformed or error response.

    Covers non-2xx HTTP, missing keys in the JSON body, invalid outer
    JSON, etc. The response body (truncated to a few hundred chars) is
    preserved in the message so log lines stay useful for debugging.

    Note: invalid *inner* JSON from the model itself (the LLM's own
    output) is NOT a response error — it's a graceful fallback to
    ``class=other`` with a 0.2 confidence. Only transport / envelope
    failures land here.
    """


# ---------------------------------------------------------------------------
# Allowed class values — single source of truth.
# ---------------------------------------------------------------------------

# Order is the canonical enum order from the plan; downstream code uses
# membership only, not ordering.
ALLOWED_CLASS_VALUES: tuple[str, ...] = (
    "code",
    "chat",
    "book",
    "textbook",
    "paper",
    "article",
    "reference",
    "note",
    "other",
)


# ---------------------------------------------------------------------------
# ClassifiableDocument — what the classifier sees.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassifiableDocument:
    """A snapshot of a document handed to the classifier chain.

    Attributes:
        document_id: Primary key of the ``documents`` row.
        source_uri: Origin URI (``file:///...``, ``claude-code://...``,
            etc.). The rule classifier inspects this for path heuristics.
        title: Optional title (markdown frontmatter, EPUB metadata, …).
        text: Document body. Used by content-density heuristics; the
            classifier MUST NOT modify it.
        format_labels: Already-attached structural labels such as
            ``[("format", "pdf"), ("language", "python")]``. The
            classifier reads but does not write this list.
        metadata: Free-form metadata mirrored from
            ``documents.metadata``. Contains keys like ``page_count``
            (PDF), ``byte_count`` (code), and ``chunker_hint``.
    """

    document_id: int
    source_uri: str
    title: str | None
    text: str
    format_labels: list[tuple[str, str]] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ClassLabel — what the classifier returns.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassLabel:
    """A single classifier output.

    The dataclass is frozen so callers can use instances as cache keys
    and assert provenance with confidence.

    Attributes:
        value: One of :data:`ALLOWED_CLASS_VALUES`. Validated in
            ``__post_init__``.
        confidence: Self-reported confidence in ``[0.0, 1.0]``. Hand-
            calibrated for the rule classifier; self-reported by the LLM
            classifier (P1). Validated in ``__post_init__``.
        rationale: Short human-readable reason. Persisted into the
            ``corpus.document_labels.source`` channel today via the
            ``classifier:<name>`` source value; the rationale itself is
            kept on the in-process audit log (and emitted by the CLI's
            ``--json`` output) so the user can debug calibration drift.
    """

    value: str
    confidence: float
    rationale: str

    def __post_init__(self) -> None:
        if self.value not in ALLOWED_CLASS_VALUES:
            raise ValueError(
                f"ClassLabel.value must be one of {ALLOWED_CLASS_VALUES!r}; got {self.value!r}"
            )
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"ClassLabel.confidence must be in [0.0, 1.0]; got {self.confidence!r}"
            )


# ---------------------------------------------------------------------------
# Classifier — the seam.
# ---------------------------------------------------------------------------


@runtime_checkable
class Classifier(Protocol):
    """Content-class classifier protocol.

    Concrete classifiers expose a stable :attr:`name` (used as
    ``classifier:<name>`` in the ``document_labels.source`` column) and
    implement :meth:`classify`. Returning ``None`` from ``classify``
    means "I have no signal — pass to the next classifier in the chain"
    rather than committing to a low-confidence guess.

    Implementations must be cheap to import — heavy optional backends
    (HTTP clients, model weights) belong inside ``__init__`` or
    ``classify``, not at module top level. See
    :mod:`corpus_forge.classifiers.rule_based` for the established
    pattern.
    """

    name: str
    """Stable identifier. Used in the ``source`` field of
    ``document_labels`` (``classifier:rule`` / ``classifier:llm`` / …)."""

    def classify(self, doc: ClassifiableDocument) -> ClassLabel | None:
        """Classify ``doc`` and return a label, or ``None`` to pass."""
        ...
