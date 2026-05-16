"""Phase H — :class:`QwenCoderLocal` (Ollama HTTP backend).

Talks to a local Ollama daemon via ``POST /api/generate`` with
``stream=false`` and ``format=json`` so the model is constrained to
emit a parseable JSON object. Transport layout, lazy ``requests``
import, and exception mapping mirror
:class:`corpus_forge.classifiers.llm.LLMClassifier` —
the endpoint and prompt differ; the transport doesn't.

**Local-or-remote URL is a cross-cutting requirement.** Every model
client in corpus-forge accepts an arbitrary HTTP URL: the default is
``http://localhost:11434`` (local Ollama). The remote sibling is
:class:`corpus_forge.enrichers.qwen_remote.QwenCoderRemote`; together
they provide the explicit local-vs-remote pair the project policy
calls for.

Failure modes:

- Transport-layer (``requests.Timeout``, ``ConnectionError``, non-2xx
  HTTP, malformed outer JSON) → raise a typed exception from
  :mod:`corpus_forge.enrichers.base`.
- Output-validation (model emits unparseable inner JSON or the wrong
  shape) → graceful fallback :class:`CodeChunkEnrichment` with
  ``summary='invalid LLM output'`` and ``confidence=0.0``. The shared
  parser :func:`corpus_forge.enrichers.base._parse_enrichment_response`
  handles this — same code path as the remote backend.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

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
        import requests  # noqa: PLC0415 — lazy import (see module docstring)

        url = f"{self.llm_url}/api/tags"
        try:
            resp = requests.get(url, timeout=self.timeout_s)
        except requests.Timeout as exc:
            raise EnricherUnavailableError(
                f"Ollama daemon at {self.llm_url} did not respond to /api/tags within "
                f"{self.timeout_s}s — is it running?"
            ) from exc
        except requests.ConnectionError as exc:
            raise EnricherUnavailableError(
                f"Cannot connect to Ollama daemon at {self.llm_url}: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise EnricherUnavailableError(
                f"Ollama health check at {self.llm_url} failed: {exc}"
            ) from exc

        if not resp.ok:
            body = (resp.text or "")[:200]
            raise EnricherUnavailableError(
                f"Ollama /api/tags returned HTTP {resp.status_code}: {body}"
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise EnricherUnavailableError(
                f"Ollama /api/tags returned non-JSON: {(resp.text or '')[:200]}"
            ) from exc

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
        import requests  # noqa: PLC0415 — lazy (see module docstring)

        url = f"{self.llm_url}/api/generate"
        prompt = self._build_prompt(chunk, language)
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": self.temperature,
                "num_ctx": _NUM_CTX,
            },
        }

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout_s)
        except requests.Timeout as exc:
            raise EnricherTimeoutError(
                f"Qwen-local enricher exceeded {self.timeout_s}s budget at {url}"
            ) from exc
        except requests.ConnectionError as exc:
            raise EnricherUnavailableError(
                f"Cannot connect to enricher endpoint at {self.llm_url}: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise EnricherUnavailableError(f"Qwen-local enricher request failed: {exc}") from exc

        if not resp.ok:
            body = (resp.text or "")[:200]
            raise EnricherResponseError(f"HTTP {resp.status_code}: {body}")

        try:
            envelope = resp.json()
        except ValueError as exc:
            body = (resp.text or "")[:200]
            raise EnricherResponseError(f"Malformed outer JSON: {body}") from exc

        if "response" not in envelope:
            raise EnricherResponseError(
                f"Qwen-local response missing 'response' key: {str(envelope)[:200]}"
            )

        raw_inner = envelope["response"]
        if not isinstance(raw_inner, str):
            raw_inner = "" if raw_inner is None else str(raw_inner)
        return _parse_enrichment_response(raw_inner, self.model)

    # ── internals ─────────────────────────────────────────────────────

    def _build_prompt(self, chunk: TextChunk, language: str) -> str:
        """Build the user prompt: schema + language hint + chunk text."""
        return _PROMPT_TEMPLATE.format(language=language or "unknown", code=chunk.text or "")
