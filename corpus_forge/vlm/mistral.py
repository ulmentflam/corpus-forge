"""Phase D / Wave 4 (E-03) — :class:`MistralOCR` HTTP backend.

Remote fallback to the Mistral OCR API (``POST /v1/ocr``). Used when
the local Ollama daemon can't be reached or when accuracy / batch
throughput matters more than locality.

The implementation mirrors :class:`corpus_forge.vlm.ollama.OllamaVLM`:

- ``requests`` is lazy-imported inside each method.
- ``warmup()`` is intentionally a no-op (no free Mistral health
  endpoint exists; a real request would cost money). The constructor
  validates that the API key is present so misconfiguration still
  fails at boot time.
- Exception mapping table mirrors Ollama; 401/403 explicitly map to
  :class:`VLMUnavailableError` with an "API key rejected" message so
  callers know exactly which lever to pull.

Mistral OCR doesn't really accept a free-form user prompt today; the
``prompt`` parameter on :meth:`describe_image` is therefore accepted
for Protocol parity but ignored (documented limitation).
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


class MistralOCR:
    """Mistral OCR HTTP backend for the :class:`VLMBackend` Protocol."""

    name = "mistral"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "mistral-ocr-2503",
        base_url: str = "https://api.mistral.ai/v1",
        timeout_s: float = 120.0,
    ) -> None:
        if not api_key:
            raise VLMUnavailableError(
                "MistralOCR requires an API key (set MISTRAL_API_KEY in secrets.env)"
            )
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    # ── public API ────────────────────────────────────────────────────

    def warmup(self) -> None:
        """No-op health check.

        Mistral has no free OCR health endpoint, so we just confirm the
        api-key is present (already done in ``__init__``) and log that
        the backend is configured.
        """
        logger.info(
            "MistralOCR configured (model=%s, base_url=%s); warmup is a no-op",
            self.model,
            self.base_url,
        )

    def describe_image(self, image: bytes, *, prompt: str | None = None) -> str:
        """OCR ``image`` and return concatenated Markdown.

        The ``prompt`` parameter is accepted for Protocol parity but
        ignored — Mistral OCR doesn't take a user prompt today. If the
        upstream API ever exposes an ``instructions`` field, wire it
        through here.
        """
        if prompt is not None:
            logger.debug(
                "MistralOCR.describe_image: prompt= %r ignored "
                "(Mistral OCR does not accept user prompts).",
                prompt,
            )
        return self._ocr(image)

    def extract_page(self, image: bytes, *, page_number: int) -> str:
        """OCR a single PDF page image. ``page_number`` is metadata-only."""
        logger.debug("MistralOCR.extract_page: page %d", page_number)
        return self._ocr(image)

    # ── internals ─────────────────────────────────────────────────────

    def _ocr(self, image: bytes) -> str:
        """Single ``POST /ocr`` call. Returns concatenated page markdown."""
        import requests  # noqa: PLC0415 — lazy import (see module docstring)

        url = f"{self.base_url}/ocr"
        b64 = base64.b64encode(image).decode("ascii")
        payload = {
            "model": self.model,
            "document": {
                "type": "image_url",
                "image_url": f"data:image/png;base64,{b64}",
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout_s)
        except requests.Timeout as exc:
            raise VLMTimeoutError(f"Mistral OCR exceeded {self.timeout_s}s budget") from exc
        except requests.ConnectionError as exc:
            raise VLMUnavailableError(
                f"Cannot connect to Mistral at {self.base_url}: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise VLMUnavailableError(f"Mistral OCR request failed: {exc}") from exc

        # 401/403 → key rejected → unavailable (point the user at
        # secrets.env). Everything else non-2xx is a response error.
        if resp.status_code in (401, 403):
            body = (resp.text or "")[:200]
            raise VLMUnavailableError(f"Mistral API key rejected (HTTP {resp.status_code}): {body}")
        if not resp.ok:
            body = (resp.text or "")[:200]
            raise VLMResponseError(f"HTTP {resp.status_code}: {body}")

        try:
            data = resp.json()
        except ValueError as exc:
            body = (resp.text or "")[:200]
            raise VLMResponseError(f"Malformed JSON from Mistral: {body}") from exc

        pages = data.get("pages")
        if not isinstance(pages, list) or not pages:
            raise VLMResponseError(
                f"Mistral response missing or empty 'pages' list: {str(data)[:200]}"
            )

        parts: list[str] = []
        for idx, page in enumerate(pages):
            if not isinstance(page, dict) or "markdown" not in page:
                raise VLMResponseError(
                    f"Mistral page {idx} missing 'markdown' key: {str(page)[:200]}"
                )
            parts.append(page["markdown"])

        return "\n\n".join(parts)
