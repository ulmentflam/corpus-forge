"""Phase D / Wave 4 (E-02) — :class:`OllamaVLM` HTTP backend.

Talks to a local Ollama daemon via the ``POST /api/generate`` endpoint
with ``stream=false`` (single JSON response per request). The
``requests`` library is lazy-imported inside each method so importing
this module with the ``[ocr]`` extra absent does NOT error — the
import happens at the first OCR call instead, surfacing a clean
``ImportError`` only if/when OCR is actually attempted.

Failure modes (mapped to custom :class:`~corpus_forge.vlm.VLMError`
subclasses):

- ``requests.ConnectionError`` → :class:`VLMUnavailableError` (daemon down).
- ``requests.Timeout`` from ``/api/generate`` → :class:`VLMTimeoutError`
  (the budget is the user's primary tuning knob).
- ``requests.Timeout`` from ``/api/tags`` (warmup) → :class:`VLMUnavailableError`
  (we treat health-check slowness as daemon-not-reachable, not as a
  retryable timeout).
- Non-2xx response → :class:`VLMResponseError` carrying the status and
  a truncated body.
- Malformed JSON / missing ``response`` key → :class:`VLMResponseError`.
- Anything else under :class:`requests.RequestException` →
  :class:`VLMUnavailableError`.
"""

from __future__ import annotations

import base64
import logging

from .base import (
    VLMResponseError,
    VLMTimeoutError,
    VLMUnavailableError,
)

logger = logging.getLogger(__name__)

_DEFAULT_DESCRIBE_PROMPT = (
    "Transcribe any text verbatim and describe the visual content "
    "faithfully. Output clean Markdown only — no preamble."
)

# Sized for the 7B/32B Qwen-VL context; long pages would otherwise
# silently truncate. Override at the request layer if a backend ever
# exposes the knob, but right now 8K is the safe floor.
_NUM_CTX = 8192


class OllamaVLM:
    """Local Ollama daemon backend for the :class:`VLMBackend` Protocol.

    The default model is ``qwen2.5vl:7b`` (Apache-2.0, ~5 GB), pulled
    via ``ollama pull qwen2.5vl:7b`` on the host. ``warmup()`` GETs
    ``/api/tags`` and confirms the configured tag is installed — raising
    :class:`VLMUnavailableError` early surfaces "you forgot to ``ollama
    pull``" at boot time, not in the middle of an ingest run.
    """

    name = "ollama"

    def __init__(
        self,
        *,
        model: str = "qwen2.5vl:7b",
        ollama_url: str = "http://localhost:11434",
        timeout_s: float = 120.0,
        temperature: float = 0.0,
    ) -> None:
        self.model = model
        # Strip trailing slash so URL composition produces exactly one.
        self.ollama_url = ollama_url.rstrip("/")
        self.timeout_s = timeout_s
        self.temperature = temperature

    # ── public API ────────────────────────────────────────────────────

    def warmup(self) -> None:
        """Health-check: GET ``/api/tags`` and verify the model is installed."""
        import requests  # noqa: PLC0415 — lazy import (see module docstring)

        url = f"{self.ollama_url}/api/tags"
        try:
            resp = requests.get(url, timeout=self.timeout_s)
        except requests.Timeout as exc:
            # Treat health-check timeout as "daemon not reachable".
            raise VLMUnavailableError(
                f"Ollama daemon at {self.ollama_url} did not respond to /api/tags within "
                f"{self.timeout_s}s — is it running?"
            ) from exc
        except requests.ConnectionError as exc:
            raise VLMUnavailableError(
                f"Cannot connect to Ollama daemon at {self.ollama_url}: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise VLMUnavailableError(
                f"Ollama health check at {self.ollama_url} failed: {exc}"
            ) from exc

        if not resp.ok:
            body = (resp.text or "")[:200]
            raise VLMUnavailableError(f"Ollama /api/tags returned HTTP {resp.status_code}: {body}")

        try:
            data = resp.json()
        except ValueError as exc:
            raise VLMUnavailableError(
                f"Ollama /api/tags returned non-JSON: {(resp.text or '')[:200]}"
            ) from exc

        models = data.get("models") or []
        installed = {m.get("name") for m in models if isinstance(m, dict)}
        if self.model not in installed:
            raise VLMUnavailableError(
                f"Ollama model {self.model!r} not found on {self.ollama_url}. "
                f"Install with: ollama pull {self.model}"
            )

    def describe_image(self, image: bytes, *, prompt: str | None = None) -> str:
        """Transcribe + describe ``image`` with an optional caller prompt."""
        return self._generate(image, prompt or _DEFAULT_DESCRIBE_PROMPT)

    def extract_page(self, image: bytes, *, page_number: int) -> str:
        """Extract ``image`` as faithful Markdown for one PDF page."""
        prompt = (
            f"You are extracting page {page_number} of a PDF as faithful Markdown. "
            "Preserve headings, lists, tables, code blocks, and math. Do not "
            "summarise. Output Markdown only — no preamble, no notes about the "
            "page itself."
        )
        return self._generate(image, prompt)

    # ── internals ─────────────────────────────────────────────────────

    def _generate(self, image: bytes, prompt: str) -> str:
        """Single ``POST /api/generate`` call. Returns ``response`` text."""
        import requests  # noqa: PLC0415

        url = f"{self.ollama_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "images": [base64.b64encode(image).decode("ascii")],
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": _NUM_CTX,
            },
        }

        try:
            resp = requests.post(url, json=payload, timeout=self.timeout_s)
        except requests.Timeout as exc:
            raise VLMTimeoutError(f"Ollama generate exceeded {self.timeout_s}s budget") from exc
        except requests.ConnectionError as exc:
            raise VLMUnavailableError(
                f"Cannot connect to Ollama daemon at {self.ollama_url}: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise VLMUnavailableError(f"Ollama generate request failed: {exc}") from exc

        if not resp.ok:
            body = (resp.text or "")[:200]
            raise VLMResponseError(f"HTTP {resp.status_code}: {body}")

        try:
            data = resp.json()
        except ValueError as exc:
            body = (resp.text or "")[:200]
            raise VLMResponseError(f"Malformed JSON from Ollama: {body}") from exc

        if "response" not in data:
            raise VLMResponseError(f"Ollama response missing 'response' key: {str(data)[:200]}")

        return data["response"]
