"""Phase H — :class:`QwenCoderRemote` (configurable URL + API shape).

Speaks either:

- the **Ollama** API (``POST /api/generate``) at a remote URL — same
  shape as :class:`QwenCoderLocal` but with an ``Authorization: Bearer
  <api_key>`` header. Used for hosted Ollama servers (e.g. an internal
  shared box, an Ollama Cloud endpoint).
- the **OpenAI chat-completions** API
  (``POST /chat/completions``) with a ``response_format`` request for
  JSON output. Used for any OpenAI-compatible proxy (vLLM,
  text-generation-inference, llama.cpp's OpenAI shim, etc.).

The shape is selected per-instance via the ``api_shape`` constructor
kwarg. Both shapes share the inner-JSON parser
(:func:`corpus_forge.enrichers.base._parse_enrichment_response`) so the
graceful-fallback semantics are identical to the local backend.

**Local-or-remote URL is a cross-cutting requirement.** This class
plus :class:`QwenCoderLocal` are the two concrete halves the project
policy requires (separate classes, separate config fields,
``local_url`` and ``remote_url``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from .base import (
    CodeChunkEnrichment,
    EnricherResponseError,
    EnricherTimeoutError,
    EnricherUnavailableError,
    _parse_enrichment_response,
)
from .qwen_local import _NUM_CTX, _PROMPT_TEMPLATE

if TYPE_CHECKING:  # pragma: no cover — typing only
    from corpus_forge.chunkers.base import TextChunk

logger = logging.getLogger(__name__)


class QwenCoderRemote:
    """Remote Qwen-coder backend speaking Ollama OR OpenAI chat-completions.

    Constructor kwargs (all keyword-only):

    - ``api_shape``: ``"ollama"`` (default) or ``"openai"``. Selects
      the request/response envelope.
    - ``model``: provider-specific tag (Ollama: ``qwen3.6:35b-a3b-instruct``;
      OpenAI-compat: whatever model name the proxy advertises).
    - ``base_url``: base URL. For Ollama, the backend appends
      ``/api/generate``; for OpenAI, ``/chat/completions``.
    - ``api_key``: bearer token. ``"openai"`` shape requires a non-empty
      key — :class:`EnricherUnavailableError` is raised at construction
      if absent. ``"ollama"`` shape tolerates an empty key (the header
      is then omitted) since some hosted Ollama servers are open.
    - ``timeout_s``: per-request HTTP budget. Default 180 s — same as
      local.
    - ``temperature``: sampling temperature. Default 0.1.
    """

    name = "qwen-remote"

    def __init__(
        self,
        *,
        api_shape: Literal["ollama", "openai"] = "ollama",
        model: str = "qwen3.6:35b-a3b-instruct",
        base_url: str = "http://localhost:11434",
        api_key: str | None = None,
        timeout_s: float = 180.0,
        temperature: float = 0.1,
    ) -> None:
        if api_shape not in ("ollama", "openai"):
            raise EnricherUnavailableError(
                f"api_shape must be 'ollama' or 'openai'; got {api_shape!r}"
            )
        if api_shape == "openai" and not api_key:
            raise EnricherUnavailableError(
                "Remote enricher with api_shape='openai' requires a non-empty api_key — "
                "set the configured env var in secrets.env (see code_enricher.remote_api_key_env)."
            )

        self.api_shape = api_shape
        self.model = model
        # Strip trailing slash so URL composition produces exactly one.
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or None
        self.timeout_s = timeout_s
        self.temperature = temperature

    # ── public API ────────────────────────────────────────────────────

    def warmup(self) -> None:
        """Best-effort readiness check.

        For ``api_shape='ollama'`` this is a GET ``/api/tags`` probe —
        same shape as :class:`QwenCoderLocal.warmup`. For the OpenAI
        shape there is no universally-supported tag-list endpoint, so
        warmup is a no-op (the first ``enrich`` call surfaces any
        transport / auth issue).
        """
        if self.api_shape != "ollama":
            return None

        import requests  # noqa: PLC0415

        url = f"{self.base_url}/api/tags"
        headers = self._auth_headers()
        try:
            resp = requests.get(url, timeout=self.timeout_s, headers=headers)
        except requests.Timeout as exc:
            raise EnricherUnavailableError(
                f"Remote Ollama at {self.base_url} did not respond to /api/tags "
                f"within {self.timeout_s}s — is it reachable?"
            ) from exc
        except requests.ConnectionError as exc:
            raise EnricherUnavailableError(
                f"Cannot connect to remote Ollama at {self.base_url}: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise EnricherUnavailableError(
                f"Remote Ollama health check at {self.base_url} failed: {exc}"
            ) from exc

        if not resp.ok:
            body = (resp.text or "")[:200]
            raise EnricherUnavailableError(
                f"Remote Ollama /api/tags returned HTTP {resp.status_code}: {body}"
            )
        return None

    def enrich(self, chunk: TextChunk, *, language: str) -> CodeChunkEnrichment:
        """Enrich ``chunk`` via the configured API shape."""
        if self.api_shape == "openai":
            return self._enrich_openai(chunk, language)
        return self._enrich_ollama(chunk, language)

    # ── internals ─────────────────────────────────────────────────────

    def _auth_headers(self) -> dict[str, str]:
        """Bearer-auth header dict (empty when no api_key set)."""
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    def _build_prompt(self, chunk: TextChunk, language: str) -> str:
        """Build the prompt (shared template with the local backend)."""
        return _PROMPT_TEMPLATE.format(language=language or "unknown", code=chunk.text or "")

    def _enrich_ollama(self, chunk: TextChunk, language: str) -> CodeChunkEnrichment:
        """Hosted Ollama path — ``POST /api/generate`` with bearer auth."""
        import requests  # noqa: PLC0415

        url = f"{self.base_url}/api/generate"
        payload = {
            "model": self.model,
            "prompt": self._build_prompt(chunk, language),
            "stream": False,
            "format": "json",
            "options": {
                "temperature": self.temperature,
                "num_ctx": _NUM_CTX,
            },
        }
        try:
            resp = requests.post(
                url,
                json=payload,
                timeout=self.timeout_s,
                headers=self._auth_headers(),
            )
        except requests.Timeout as exc:
            raise EnricherTimeoutError(
                f"Qwen-remote (ollama) exceeded {self.timeout_s}s budget at {url}"
            ) from exc
        except requests.ConnectionError as exc:
            raise EnricherUnavailableError(
                f"Cannot connect to remote Ollama at {self.base_url}: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise EnricherUnavailableError(f"Qwen-remote (ollama) request failed: {exc}") from exc

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
                f"Qwen-remote (ollama) missing 'response' key: {str(envelope)[:200]}"
            )

        raw_inner = envelope["response"]
        if not isinstance(raw_inner, str):
            raw_inner = "" if raw_inner is None else str(raw_inner)
        return _parse_enrichment_response(raw_inner, self.model)

    def _enrich_openai(self, chunk: TextChunk, language: str) -> CodeChunkEnrichment:
        """OpenAI-compat path — ``POST /chat/completions``."""
        import requests  # noqa: PLC0415

        url = f"{self.base_url}/chat/completions"
        prompt = self._build_prompt(chunk, language)
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": self.temperature,
        }
        try:
            resp = requests.post(
                url,
                json=payload,
                timeout=self.timeout_s,
                headers=self._auth_headers(),
            )
        except requests.Timeout as exc:
            raise EnricherTimeoutError(
                f"Qwen-remote (openai) exceeded {self.timeout_s}s budget at {url}"
            ) from exc
        except requests.ConnectionError as exc:
            raise EnricherUnavailableError(
                f"Cannot connect to remote OpenAI-compat endpoint at {self.base_url}: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise EnricherUnavailableError(f"Qwen-remote (openai) request failed: {exc}") from exc

        if not resp.ok:
            body = (resp.text or "")[:200]
            raise EnricherResponseError(f"HTTP {resp.status_code}: {body}")

        try:
            envelope = resp.json()
        except ValueError as exc:
            body = (resp.text or "")[:200]
            raise EnricherResponseError(f"Malformed outer JSON: {body}") from exc

        # OpenAI shape: data["choices"][0]["message"]["content"] is the
        # raw JSON string emitted by the model.
        choices = envelope.get("choices") if isinstance(envelope, dict) else None
        if not isinstance(choices, list) or not choices:
            raise EnricherResponseError(
                f"Qwen-remote (openai) missing 'choices' list: {str(envelope)[:200]}"
            )
        first = choices[0]
        if not isinstance(first, dict):
            raise EnricherResponseError(
                f"Qwen-remote (openai) choices[0] is not an object: {str(first)[:200]}"
            )
        message = first.get("message")
        if not isinstance(message, dict):
            raise EnricherResponseError(
                f"Qwen-remote (openai) choices[0].message is not an object: {str(message)[:200]}"
            )
        raw_inner = message.get("content", "")
        if not isinstance(raw_inner, str):
            raw_inner = "" if raw_inner is None else str(raw_inner)
        return _parse_enrichment_response(raw_inner, self.model)
