"""Code-enrichment protocol + dataclass + exception hierarchy.

Phase H / Wave 0 — H-01.

The :class:`CodeEnricher` seam wires an LLM-backed enrichment pass onto
chunks of ``class=code`` documents. Each enrichment carries:

- a *synthesized docstring* when the construct lacks one;
- a *semantic summary* in domain language;
- a *symbol references* list (functions/types this chunk depends on);
- the *model tag* that produced it (used for idempotency on re-runs);
- a self-reported *confidence* in ``[0.0, 1.0]``.

Mirrors :mod:`corpus_forge.classifiers.base` exception shape and
:mod:`corpus_forge.vlm.base` "noop fail-loud" policy. Concrete backends
(``QwenCoderLocal`` / ``QwenCoderRemote``) ship in Wave 1.

A single shared response parser, :func:`_parse_enrichment_response`,
turns a raw inner JSON string into a :class:`CodeChunkEnrichment` and
gracefully falls back when the model emits malformed JSON or the wrong
shape — exactly mirroring the ``class=other`` 0.2 fallback in
:class:`corpus_forge.classifiers.llm.LLMClassifier`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from corpus_forge.chunkers.base import TextChunk

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exception hierarchy.
# ---------------------------------------------------------------------------


class EnricherError(Exception):
    """Base for every enricher-layer operational failure.

    Callers can ``except EnricherError`` to swallow every enricher-side
    transport / configuration failure uniformly; subclasses carve out
    discriminable modes for smarter callers (retry, degrade, downgrade
    to the next enricher in the chain).
    """


class EnricherUnavailableError(EnricherError):
    """The enricher backend cannot be reached or is not configured.

    Raised by:

    - :class:`NoopEnricher` on every call (the explicit "disabled"
      signal — see :func:`corpus_forge.enrichers.get_active_enricher`).
    - Concrete HTTP backends when the daemon is down (``ConnectionError``)
      or when constructor inputs are missing (e.g. remote backend with
      empty API key).
    """


class EnricherTimeoutError(EnricherError):
    """The enricher was reachable but exceeded the configured timeout.

    Distinct from :class:`EnricherUnavailableError` so the CLI can
    implement bounded retry/backoff (raise the timeout, fall back to a
    different backend) without giving up entirely on the run.
    """


class EnricherResponseError(EnricherError):
    """The enricher returned a malformed or error envelope.

    Covers transport-envelope problems: non-2xx HTTP, missing keys in
    the outer JSON, invalid outer JSON. The response body (truncated to
    a few hundred chars) is preserved in the message so log lines stay
    useful for debugging.

    Note: malformed *inner* JSON from the model itself (the LLM's own
    output) is NOT a response error — it falls back gracefully to a
    sentinel :class:`CodeChunkEnrichment` with ``summary="invalid LLM
    output"`` and ``confidence=0.0``. Only transport / envelope failures
    raise this exception.
    """


# ---------------------------------------------------------------------------
# Dataclass.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CodeChunkEnrichment:
    """LLM-generated enrichment for a single code chunk.

    Attributes:
        docstring: Synthesized docstring text or ``None`` when the
            construct already has one (the LLM may leave ``docstring``
            as JSON ``null`` to signal "no synthesis needed").
        summary: One- or two-sentence semantic summary in domain
            language. Always a string; falls back to
            ``"invalid LLM output"`` on parse failure (with
            ``confidence == 0.0``).
        symbols: Flat list of referenced symbol names — functions /
            types / module-level identifiers this chunk depends on.
            Useful for future cross-chunk linking; currently flat so
            P2 graph storage can be added without a schema change.
        model: The model tag that produced the enrichment. Used by
            :meth:`StorageBackend.iter_code_chunks_for_enrichment` for
            idempotency — chunks already enriched with the *current*
            model tag are skipped on re-run.
        confidence: Self-reported confidence in ``[0.0, 1.0]``. The
            parser clamps any model output into the valid range so a
            hallucinated ``1.5`` cannot trip downstream invariants.
    """

    docstring: str | None
    summary: str
    symbols: list[str] = field(default_factory=list)
    model: str = ""
    confidence: float = 0.0

    def __post_init__(self) -> None:
        # Soft-validate the confidence range. We *don't* raise here —
        # the parser clamps before construction so this guard exists
        # only for hand-constructed instances (test fixtures, future
        # rule-based enrichers).
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(
                f"CodeChunkEnrichment.confidence must be in [0.0, 1.0]; got {self.confidence!r}"
            )

    def to_metadata(self) -> dict:
        """Serialise to the ``chunks.metadata.enrichment`` dict shape.

        Mirrors the JSON shape downstream consumers expect — flat keys,
        ``docstring`` may be ``None``. Round-trips through JSON without
        loss.
        """
        return {
            "docstring": self.docstring,
            "summary": self.summary,
            "symbols": list(self.symbols),
            "model": self.model,
            "confidence": float(self.confidence),
        }


# ---------------------------------------------------------------------------
# Protocol.
# ---------------------------------------------------------------------------


@runtime_checkable
class CodeEnricher(Protocol):
    """Code-chunk enrichment protocol.

    Concrete enrichers expose a stable :attr:`name` (used as
    ``enricher:<name>`` in audit logs and CLI output) and implement
    :meth:`enrich` + :meth:`warmup`. ``enrich`` returns a
    :class:`CodeChunkEnrichment` populated with whatever the backend
    could extract — graceful fallback semantics live inside each
    concrete enricher.

    Implementations must be cheap to import — heavy optional backends
    (``requests`` HTTP clients, tokenizer libraries) belong inside
    ``__init__`` or ``enrich``, not at module top level.
    """

    name: str
    """Stable identifier. Used in CLI / log output as
    ``enricher:<name>`` (``enricher:qwen-local`` / ``enricher:qwen-remote``)."""

    def enrich(self, chunk: TextChunk, *, language: str) -> CodeChunkEnrichment:
        """Enrich ``chunk`` and return a :class:`CodeChunkEnrichment`.

        ``language`` is the chunk's language tag (``python`` / ``rust``
        / ``go`` / ``javascript`` / …) — taken from the parent
        document's ``format=*`` / ``language=*`` labels.
        """
        ...

    def warmup(self) -> None:
        """Best-effort readiness check.

        Concrete HTTP backends typically GET ``/api/tags`` to confirm
        the model is pulled; :class:`NoopEnricher` is a no-op.
        """
        ...


# ---------------------------------------------------------------------------
# Noop / disabled backend.
# ---------------------------------------------------------------------------


class NoopEnricher:
    """Fail-loud backend used when ``config.code_enricher.backend == "none"``.

    Every operational call raises :class:`EnricherUnavailableError` so
    callers know the enricher is disabled at boot time rather than
    silently writing empty enrichment metadata. Mirrors
    :class:`corpus_forge.vlm.NoopVLM`'s "raise on every call" policy.
    """

    name = "noop"

    def enrich(
        self,
        chunk: TextChunk,  # noqa: ARG002 — Protocol shape; not used here
        *,
        language: str,  # noqa: ARG002 — Protocol shape; not used here
    ) -> CodeChunkEnrichment:
        """Always raise :class:`EnricherUnavailableError`."""
        raise EnricherUnavailableError("CodeEnricher is disabled (backend='none')")

    def warmup(self) -> None:
        """No-op: Noop has nothing to warm up."""
        return None


# ---------------------------------------------------------------------------
# Shared inner-JSON parser.
# ---------------------------------------------------------------------------


# Short prefix used in the graceful-fallback summary so callers can
# grep audit output for ``invalid LLM output`` and recover the raw
# response snippet that tripped validation.
_INVALID_SUMMARY = "invalid LLM output"

# Truncate the raw model output snippet attached to fallback summaries
# so we don't bloat log lines with the entire payload.
_INVALID_SNIPPET_CHARS = 200


def _parse_enrichment_response(raw_json_str: str, model_tag: str) -> CodeChunkEnrichment:
    """Parse the LLM's inner JSON string into a :class:`CodeChunkEnrichment`.

    Shared by :class:`QwenCoderLocal` and :class:`QwenCoderRemote` (both
    Ollama and OpenAI shapes). DRY parser ensures the three backends
    apply identical validation + fallback semantics.

    Validation rules (each failure → graceful fallback, never raise):

    - inner JSON unparseable → fallback with ``summary='invalid LLM output'``.
    - inner JSON is not an object → fallback.
    - ``summary`` missing or non-string → coerced to ``str(...)`` and
      defaulted to ``''`` when absent.
    - ``docstring`` non-string and non-null → coerced to ``str(...)``.
    - ``symbols`` missing → ``[]``; non-list → coerced via ``list(...)``;
      each entry coerced to ``str``.
    - ``confidence`` non-float / out-of-range → clamped to ``[0.0, 1.0]``.

    The graceful-fallback path logs a WARNING (under the
    ``corpus_forge.enrichers.base`` logger) so audit logs surface the
    bad payload without crashing the run.
    """
    try:
        parsed = json.loads(raw_json_str) if raw_json_str else None
    except (TypeError, ValueError):
        return _fallback(raw_json_str, model_tag, "inner JSON unparseable")

    if not isinstance(parsed, dict):
        return _fallback(raw_json_str, model_tag, "inner JSON is not an object")

    # docstring may legitimately be null — preserve that signal.
    raw_docstring = parsed.get("docstring")
    docstring: str | None
    if raw_docstring is None:
        docstring = None
    elif isinstance(raw_docstring, str):
        docstring = raw_docstring
    else:
        docstring = str(raw_docstring)

    raw_summary = parsed.get("summary", "")
    summary = raw_summary if isinstance(raw_summary, str) else str(raw_summary)

    raw_symbols = parsed.get("symbols", [])
    if isinstance(raw_symbols, list):
        symbols = [s if isinstance(s, str) else str(s) for s in raw_symbols]
    else:
        # Coerce non-list to a single-element list rather than dropping —
        # avoids silent data loss for an off-shape but informative model
        # output.
        symbols = [str(raw_symbols)]

    raw_confidence = parsed.get("confidence", 0.5)
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    return CodeChunkEnrichment(
        docstring=docstring,
        summary=summary,
        symbols=symbols,
        model=model_tag,
        confidence=confidence,
    )


def _fallback(raw_inner: str, model_tag: str, reason: str) -> CodeChunkEnrichment:
    """Build the graceful-fallback enrichment and log a WARNING."""
    snippet = (raw_inner or "")[:_INVALID_SNIPPET_CHARS]
    logger.warning("CodeEnricher: invalid LLM output (%s); snippet=%r", reason, snippet)
    return CodeChunkEnrichment(
        docstring=None,
        summary=_INVALID_SUMMARY,
        symbols=[],
        model=model_tag,
        confidence=0.0,
    )
