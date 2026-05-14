"""Phase D / Wave 4 — VLM (vision-language model) Protocol + exceptions.

The VLM layer is the OCR / image-description plug-in surface used by
the Wave 5 PDF-escalation path and the Wave 5/6 image extractor. Every
backend implements the same three-method Protocol; the file/image bytes
are produced by the caller (rasterisation lives upstream) and the
backend returns Markdown.

The Protocol mirrors the embedder layer's shape on purpose: a flat
:class:`~typing.Protocol`, a registry keyed on ``backend.name``, and a
``get_active_vlm`` factory driven by :class:`corpus_forge.config.VLMConfig`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# ── Exceptions ──────────────────────────────────────────────────────────


class VLMError(Exception):
    """Base for every VLM-layer operational failure.

    Callers can ``except VLMError`` to swallow all backend failures
    uniformly. Each subclass below carves out a discriminable failure
    mode so smarter callers (e.g. the Wave 5 PDF escalation path) can
    decide whether to retry, degrade, or surface a hard error.
    """


class VLMUnavailableError(VLMError):
    """The backend cannot be reached or is not configured.

    Raised by:

    - :class:`NoopVLM` for every operational call (``backend="none"``).
    - Ollama backends when the daemon is down or the model isn't
      installed.
    - Mistral when the API key is missing or rejected.
    """


class VLMTimeoutError(VLMError):
    """The backend was reachable but exceeded the configured timeout.

    Distinct from :class:`VLMUnavailableError` so callers can implement
    bounded retry/back-off (raising the timeout, halving the page batch
    size, etc.) without giving up entirely.
    """


class VLMResponseError(VLMError):
    """The backend returned a malformed or error response.

    Covers non-2xx HTTP, missing keys in the JSON body, invalid JSON,
    and ``pages: []`` empty-list cases. The response body (truncated to
    a few hundred chars) is preserved in the message so log lines stay
    useful for debugging.
    """


# ── Protocol ────────────────────────────────────────────────────────────


@runtime_checkable
class VLMBackend(Protocol):
    """The plug-in surface for OCR / image-description backends.

    Implementations live behind this Protocol. ``image`` is rasterised
    PNG / JPEG bytes (the caller owns the rasterisation step); both
    operational methods return Markdown that downstream chunkers will
    process unchanged.
    """

    name: str

    def describe_image(self, image: bytes, *, prompt: str | None = None) -> str:
        """Transcribe text + describe the visual content of ``image``.

        Used by the Wave 5/6 image extractor. The caller may override
        the default prompt; backends without a notion of user-supplied
        prompts (Mistral OCR, for instance) document the limitation and
        ignore the override silently.
        """
        ...

    def extract_page(self, image: bytes, *, page_number: int) -> str:
        """Extract ``image`` as a faithful Markdown reproduction of one
        PDF page.

        Used by the Wave 5 PDF escalation path. The prompt biases
        toward verbatim reproduction (preserve headings, lists, tables,
        code blocks, math; do not summarise).
        """
        ...

    def warmup(self) -> None:
        """Cheap health-check.

        Implementations should hit a status endpoint (Ollama:
        ``/api/tags``) and raise :class:`VLMUnavailableError` if the
        backend isn't reachable. Mistral has no free health endpoint
        and warms up as a no-op (just validates that the api-key is
        present at construction time).
        """
        ...


# ── Noop implementation ────────────────────────────────────────────────


class NoopVLM:
    """Default backend when ``config.vlm.backend == "none"``.

    Every operational call raises :class:`VLMUnavailableError`. This
    is explicit "no VLM configured" — callers in the PDF/image
    extractor paths fail loud at the point of attempted OCR rather
    than silently emitting empty Markdown.
    """

    name = "none"

    def describe_image(
        self,
        image: bytes,  # noqa: ARG002 — Protocol signature parity
        *,
        prompt: str | None = None,  # noqa: ARG002 — Protocol signature parity
    ) -> str:
        raise VLMUnavailableError(
            "No VLM backend configured (config.vlm.backend = 'none'). "
            "Set backend = 'ollama' or 'mistral' to enable OCR."
        )

    def extract_page(
        self,
        image: bytes,  # noqa: ARG002 — Protocol signature parity
        *,
        page_number: int,  # noqa: ARG002 — Protocol signature parity
    ) -> str:
        raise VLMUnavailableError(
            "No VLM backend configured (config.vlm.backend = 'none'). "
            "Set backend = 'ollama' or 'mistral' to enable PDF OCR."
        )

    def warmup(self) -> None:
        raise VLMUnavailableError("No VLM backend configured (config.vlm.backend = 'none').")
