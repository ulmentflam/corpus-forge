"""Phase E / Wave 3 (C-10/11) — :class:`LLMClassifier`.

The LLM classifier is the "escalation" half of the default Phase E
classification chain (``[rule, llm]``). It talks to a local Ollama
daemon via ``POST /api/generate`` with ``stream=false`` and
``format=json`` so the model is constrained to emit a parseable JSON
object. Transport-level error mapping is delegated to
:mod:`corpus_forge._http` and shared with every other remote model
backend in the repo.

**Local-or-remote URL is a cross-cutting requirement.** Every model
client in corpus-forge accepts an arbitrary HTTP URL: the default is
``http://localhost:11434`` (local Ollama), but the same backend works
against any Ollama-compatible endpoint by swapping ``llm_url`` and
supplying ``api_key`` for hosted services.

Failure modes:

- Transport-layer failures → raise a typed exception from
  :mod:`corpus_forge.classifiers.base`. The caller's chain walker
  surfaces this as "this classifier had no signal" and continues.
- Output-validation (model returned a ``class`` not in the 9-value
  enum, or its inner JSON is unparseable) → log a WARNING and return
  ``ClassLabel(value="other", confidence=0.2, rationale=...)``. A
  hallucinating LLM must not block the whole classify run.
"""

from __future__ import annotations

import logging

from corpus_forge._http import HttpErrors, request_json

from .base import (
    ALLOWED_CLASS_VALUES,
    ClassifiableDocument,
    ClassifierResponseError,
    ClassifierTimeoutError,
    ClassifierUnavailableError,
    ClassLabel,
)

logger = logging.getLogger(__name__)

# Sized to match the Ollama context budget for qwen2.5:7b-instruct.
# 8K covers the prompt + a short JSON response with plenty of headroom
# for a 2 KB head+tail excerpt; raising this would cost RAM without
# improving accuracy for the 9-way classification task.
_NUM_CTX = 8192

# Short rationale prefix used by the graceful-fallback path so callers
# can grep audit logs for ``invalid LLM output`` and recover the raw
# response snippet that tripped validation.
_INVALID_OUTPUT_PREFIX = "invalid LLM output: "

# Truncate the raw model output snippet attached to the fallback's
# rationale so we don't bloat ``document_labels.rationale`` with the
# entire payload when the model dumps a long error string.
_INVALID_SNIPPET_CHARS = 120

_ERR = HttpErrors(ClassifierUnavailableError, ClassifierTimeoutError, ClassifierResponseError)


class LLMClassifier:
    """Ollama-backed document classifier.

    Constructor kwargs (all keyword-only — keeps call sites explicit):

    - ``model``: Ollama tag. Default ``"qwen2.5:7b-instruct"`` —
      strong on the 9-way classification, fast on M-series.
    - ``llm_url``: base URL of the Ollama-compatible endpoint. Default
      ``"http://localhost:11434"`` (local). Swap to a remote URL to
      point at a hosted Ollama / vLLM / OpenAI-shape proxy that speaks
      the ``/api/generate`` shape.
    - ``api_key``: optional bearer token. Empty / ``None`` omits the
      ``Authorization`` header (matches open local Ollama daemons);
      set it to use the same classifier against a hosted, authenticated
      endpoint without changing any other config.
    - ``timeout_s``: per-request HTTP budget. The qwen2.5:7b-instruct
      first-token floor on M-series is ~1-3 s; warm calls run 5-10 s
      for a 2 KB excerpt. Default 60 s leaves slack for the first call.
    - ``temperature``: sampling temperature. Default 0.0 (deterministic).
    - ``excerpt_chars``: total head+tail budget passed to the model
      (head and tail each get ``excerpt_chars // 2``).
    """

    name = "llm"

    def __init__(
        self,
        *,
        model: str = "qwen2.5:7b-instruct",
        llm_url: str = "http://localhost:11434",
        api_key: str | None = None,
        timeout_s: float = 60.0,
        temperature: float = 0.0,
        excerpt_chars: int = 2000,
    ) -> None:
        self.model = model
        # Strip trailing slash so URL composition produces exactly one.
        self.llm_url = llm_url.rstrip("/")
        self.api_key = api_key or None
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.excerpt_chars = excerpt_chars

    # ── public API ────────────────────────────────────────────────────

    def classify(self, doc: ClassifiableDocument) -> ClassLabel | None:
        """Classify ``doc`` and return a :class:`ClassLabel`.

        Returns ``None`` only on the explicit "I have no signal" branch
        (currently unused — the LLM always opines, even if the opinion
        is a fallback ``other``). Transport failures raise; output
        validation gracefully falls back to ``class=other``.
        """
        envelope = request_json(
            "POST",
            f"{self.llm_url}/api/generate",
            timeout_s=self.timeout_s,
            errors=_ERR,
            label="LLM classifier",
            base_url=self.llm_url,
            api_key=self.api_key,
            json_body={
                "model": self.model,
                "prompt": self._build_prompt(doc),
                "stream": False,
                "format": "json",
                "options": {"temperature": self.temperature, "num_ctx": _NUM_CTX},
            },
            required_keys=("response",),
            auth_to_unavailable=bool(self.api_key),
        )
        return self._parse_inner(envelope["response"])

    # ── internals ─────────────────────────────────────────────────────

    def _build_prompt(self, doc: ClassifiableDocument) -> str:
        """Assemble the head+tail excerpt + format labels + 9-enum prompt."""
        excerpt = _excerpt(doc.text or "", self.excerpt_chars)
        labels_str = "\n".join(f"- {k}={v}" for k, v in (doc.format_labels or [])) or "- (none)"
        enum_list = ", ".join(ALLOWED_CLASS_VALUES)
        title = doc.title or "(no title)"

        return (
            "You are a document classifier. Read the excerpt below and emit a "
            "single JSON object with the keys `class`, `confidence`, and "
            "`rationale`.\n"
            "\n"
            f"The `class` field MUST be exactly one of: {enum_list}.\n"
            "The `confidence` field is a float in [0.0, 1.0].\n"
            "The `rationale` field is a short human-readable string "
            "(<= 200 chars) explaining the choice.\n"
            "\n"
            "Class meanings:\n"
            "- code: source code, scripts, build files (Makefile, Dockerfile, "
            "config-as-code).\n"
            "- chat: conversation transcripts (Claude Code, OpenCode, generic "
            "dialogue).\n"
            "- book: long-form non-pedagogical (fiction, memoir, popular "
            "non-fiction, biography).\n"
            "- textbook: long-form pedagogical (academic textbook, course "
            "notes, exercises).\n"
            "- paper: research/academic paper (PDF with abstract + citations).\n"
            "- article: blog post, magazine, news, opinion writing.\n"
            "- reference: API docs, schema spec, manifest, machine-readable "
            "data (JSON/YAML/TOML/CSV).\n"
            "- note: personal notes, vault markdown jottings, journal.\n"
            "- other: fallback when no signal is strong enough.\n"
            "\n"
            f"Title: {title}\n"
            f"Source URI: {doc.source_uri}\n"
            "Format labels:\n"
            f"{labels_str}\n"
            "\n"
            "Document excerpt (head + tail; middle may be truncated):\n"
            "---\n"
            f"{excerpt}\n"
            "---\n"
            "\n"
            "Respond with ONLY the JSON object — no preamble, no markdown "
            "fence, no commentary."
        )

    def _parse_inner(self, raw_inner: object) -> ClassLabel:
        """Parse the model's inner JSON output and validate it.

        Falls back to ``ClassLabel(value="other", confidence=0.2, ...)``
        for any output-validation failure (logged as WARNING). Transport
        failures never reach this method.
        """
        # The shared HTTP helper preserves the envelope value untouched,
        # so the model's inner JSON may legitimately come back as a
        # non-string (some Ollama versions emit empty list / dict on
        # error). Coerce to str like the enricher does for parity.
        import json  # noqa: PLC0415

        if not isinstance(raw_inner, str):
            raw_inner = "" if raw_inner is None else str(raw_inner)

        try:
            parsed = json.loads(raw_inner)
        except (ValueError, TypeError):
            return self._fallback(raw_inner, "inner JSON unparseable")

        if not isinstance(parsed, dict):
            return self._fallback(raw_inner, "inner JSON is not an object")

        cls_value = parsed.get("class")
        if not isinstance(cls_value, str) or cls_value not in ALLOWED_CLASS_VALUES:
            return self._fallback(raw_inner, f"class={cls_value!r} not in enum")

        raw_confidence = parsed.get("confidence", 0.5)
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = 0.5
        # Clamp into the dataclass-accepted range.
        confidence = max(0.0, min(1.0, confidence))

        rationale = parsed.get("rationale", "")
        if not isinstance(rationale, str):
            rationale = str(rationale)

        return ClassLabel(value=cls_value, confidence=confidence, rationale=rationale)

    def _fallback(self, raw_inner: str, reason: str) -> ClassLabel:
        """Build the ``other`` 0.2 fallback label and log a WARNING."""
        snippet = (raw_inner or "")[:_INVALID_SNIPPET_CHARS]
        logger.warning("LLMClassifier: invalid LLM output (%s); snippet=%r", reason, snippet)
        return ClassLabel(
            value="other",
            confidence=0.2,
            rationale=f"{_INVALID_OUTPUT_PREFIX}{snippet}",
        )


def _excerpt(text: str, budget: int) -> str:
    """Return head + tail of ``text`` totalling at most ``budget`` chars.

    When ``len(text) <= budget`` the whole text is returned. Otherwise
    the head is ``budget // 2`` chars, the tail is ``budget // 2``
    chars, and a ``\\n...\\n`` separator marks the elision so the model
    knows there's a gap.
    """
    if not text:
        return ""
    if len(text) <= budget:
        return text
    half = max(1, budget // 2)
    head = text[:half]
    tail = text[-half:]
    return f"{head}\n...\n{tail}"
