"""Phase H — :class:`QwenCoderLocal` (Ollama HTTP backend).

Talks to a local Ollama daemon via ``POST /api/generate`` with
``stream=false`` and ``format=json`` so the model is constrained to
emit a parseable JSON object. Transport-level error mapping is
delegated to :mod:`corpus_forge._http` and shared with every other
remote model backend in the repo.

**Local-or-remote URL is a cross-cutting requirement.** Every model
client in corpus-forge accepts an arbitrary HTTP URL: the default is
``http://localhost:11434`` (local Ollama). The remote sibling is
:class:`corpus_forge.enrichers.qwen_remote.QwenCoderRemote`.

Failure modes:

- Transport-layer failures → raise a typed exception from
  :mod:`corpus_forge.enrichers.base` (via :mod:`corpus_forge._http`).
- Output-validation (model emits unparseable inner JSON or the wrong
  shape) → graceful fallback :class:`CodeChunkEnrichment` with
  ``summary='invalid LLM output'`` and ``confidence=0.0``. The shared
  parser :func:`corpus_forge.enrichers.base._parse_enrichment_response`
  handles this — same code path as the remote backend.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from corpus_forge._http import HttpErrors, request_json

from .base import (
    CodeChunkEnrichment,
    EnricherResponseError,
    EnricherTimeoutError,
    EnricherUnavailableError,
    _parse_enrichment_response,
)

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.chunkers.base import TextChunk

logger = logging.getLogger(__name__)

# qwen3.6:35b-a3b-instruct is an MoE — 35B total / ~3B active. A 16K
# context budget is enough to hold a typical code chunk (~1500 chars)
# plus the prompt scaffolding with comfortable headroom. Bigger ctx
# costs RAM; smaller misses long files.
_NUM_CTX = 16384


_PROMPT_TEMPLATE = """\
You are a code analysis assistant. Read the {language} code chunk below \
and emit a single JSON object describing it.

Output schema — exactly these keys, no others:
- "docstring": a synthesized docstring for the construct (a Python-style \
docstring for Python; a JSDoc/TSDoc comment for JS/TS; a /// rustdoc for \
Rust; a // godoc for Go; or a language-appropriate equivalent). Set to \
JSON null if the construct already has a docstring AND the existing one \
is adequate.
- "summary": one or two sentences, in domain language, describing what \
the chunk does. Always a non-empty string.
- "symbols": flat JSON array of referenced symbol names (function names, \
type names, module-level identifiers this chunk depends on or calls). \
Empty array when the chunk has no external references.
- "confidence": a float in [0.0, 1.0] reflecting how confident you are \
in the docstring + summary. Below 0.5 means the chunk is too small or \
opaque to summarise well.

Language: {language}

Code chunk:
---
{code}
---

Respond with ONLY the JSON object — no preamble, no markdown fence, no \
commentary.
"""

_ERR = HttpErrors(EnricherUnavailableError, EnricherTimeoutError, EnricherResponseError)


def build_prompt(chunk_text: str, language: str) -> str:
    """Format :data:`_PROMPT_TEMPLATE` with the chunk's language + text.

    Shared by :class:`QwenCoderLocal` and
    :class:`corpus_forge.enrichers.qwen_remote.QwenCoderRemote` so both
    backends present the model with the exact same prompt — required
    for behavioural parity in the inner-JSON parser.
    """
    return _PROMPT_TEMPLATE.format(language=language or "unknown", code=chunk_text or "")


class QwenCoderLocal:
    """Local Ollama backend for the :class:`CodeEnricher` protocol.

    Constructor kwargs (all keyword-only):

    - ``model``: Ollama tag. Default ``"qwen3.6:35b-a3b-instruct"`` —
      MoE 35B / ~3B active, ~22 GB on disk, fast on M-series.
    - ``llm_url``: base URL of the Ollama-compatible endpoint. Default
      ``"http://localhost:11434"`` (local). Swap to a hosted URL to
      reach a remote daemon speaking the same ``/api/generate`` shape.
    - ``timeout_s``: per-request HTTP budget. qwen3.6:35b-a3b first
      tokens land in ~3-8 s on M-series for a typical code chunk; 180 s
      leaves comfortable slack for cold starts and long chunks.
    - ``temperature``: sampling temperature. Default 0.1 — almost
      deterministic but allows minor phrasing variation in summaries.
    """

    name = "qwen-local"

    def __init__(
        self,
        *,
        model: str = "qwen3.6:35b-a3b-instruct",
        llm_url: str = "http://localhost:11434",
        timeout_s: float = 180.0,
        temperature: float = 0.1,
    ) -> None:
        self.model = model
        # Strip trailing slash so URL composition produces exactly one.
        self.llm_url = llm_url.rstrip("/")
        self.timeout_s = timeout_s
        self.temperature = temperature

    # ── public API ────────────────────────────────────────────────────

    def warmup(self) -> None:
        """Health-check: GET ``/api/tags`` and verify the model is installed."""
        data = request_json(
            "GET",
            f"{self.llm_url}/api/tags",
            timeout_s=self.timeout_s,
            errors=_ERR,
            label="Ollama daemon",
            base_url=self.llm_url,
            auth_to_unavailable=False,
            health_check=True,
        )

        models = data.get("models") or []
        installed = {m.get("name") for m in models if isinstance(m, dict)}
        if self.model not in installed:
            raise EnricherUnavailableError(
                f"Ollama model {self.model!r} not found on {self.llm_url}. "
                f"Install with: ollama pull {self.model}"
            )

    def enrich(self, chunk: TextChunk, *, language: str) -> CodeChunkEnrichment:
        """Enrich ``chunk`` and return a :class:`CodeChunkEnrichment`.

        Transport failures raise; inner-JSON failures fall back to a
        sentinel enrichment with ``summary='invalid LLM output'`` so a
        flaky model doesn't block the whole run.
        """
        envelope = request_json(
            "POST",
            f"{self.llm_url}/api/generate",
            timeout_s=self.timeout_s,
            errors=_ERR,
            label="Qwen-local enricher",
            base_url=self.llm_url,
            json_body={
                "model": self.model,
                "prompt": build_prompt(chunk.text or "", language),
                "stream": False,
                "format": "json",
                "options": {"temperature": self.temperature, "num_ctx": _NUM_CTX},
            },
            required_keys=("response",),
            auth_to_unavailable=False,
        )

        raw_inner = envelope["response"]
        if not isinstance(raw_inner, str):
            raw_inner = "" if raw_inner is None else str(raw_inner)
        return _parse_enrichment_response(raw_inner, self.model)
