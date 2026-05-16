"""Phase D / Wave 4 (E-03) — :class:`MistralOCR` HTTP backend.

Remote fallback to the Mistral OCR API (``POST /v1/ocr``). Used when
the local Ollama daemon can't be reached or when accuracy / batch
throughput matters more than locality.

Transport-level error mapping is delegated to
:mod:`corpus_forge._http`: ``requests.Timeout`` →
:class:`VLMTimeoutError`, ``ConnectionError`` and generic
``RequestException`` → :class:`VLMUnavailableError`, 401/403 →
:class:`VLMUnavailableError` (API key rejected), other non-2xx and
malformed JSON → :class:`VLMResponseError`.

Mistral OCR doesn't accept a free-form user prompt today; the
``prompt`` parameter on :meth:`describe_image` is therefore accepted
for Protocol parity but ignored (documented limitation). ``warmup`` is
a no-op — there is no free Mistral health endpoint and a real OCR
request costs money; the api-key presence check happens in
``__init__`` so misconfiguration still fails at boot.
"""

from __future__ import annotations

import base64
import logging

from corpus_forge._http import HttpErrors, request_json

from .base import (
    VLMResponseError,
    VLMTimeoutError,
    VLMUnavailableError,
)

logger = logging.getLogger(__name__)

_ERR = HttpErrors(VLMUnavailableError, VLMTimeoutError, VLMResponseError)


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
        """No-op health check. Api-key presence is validated in ``__init__``."""
        logger.info(
            "MistralOCR configured (model=%s, base_url=%s); warmup is a no-op",
            self.model,
            self.base_url,
        )

    def describe_image(self, image: bytes, *, prompt: str | None = None) -> str:
        """OCR ``image`` and return concatenated Markdown.

        The ``prompt`` parameter is accepted for Protocol parity but
        ignored — Mistral OCR doesn't take a user prompt today.
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
        b64 = base64.b64encode(image).decode("ascii")
        data = request_json(
            "POST",
            f"{self.base_url}/ocr",
            timeout_s=self.timeout_s,
            errors=_ERR,
            label="Mistral OCR",
            base_url=self.base_url,
            api_key=self.api_key,
            json_body={
                "model": self.model,
                "document": {
                    "type": "image_url",
                    "image_url": f"data:image/png;base64,{b64}",
                },
            },
        )

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
