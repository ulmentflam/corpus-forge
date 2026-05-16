"""Phase G — :class:`RemoteWhisper` HTTP backend (OpenAI-compat).

Talks to any OpenAI-compatible Whisper endpoint
(``POST {base_url}/audio/transcriptions``) — OpenAI itself, Groq (free
tier, very fast), Replicate, self-hosted whisper.cpp via HTTP. Same
transport layout, lazy ``requests`` import, and exception mapping as
:class:`corpus_forge.classifiers.llm.LLMClassifier` /
:class:`corpus_forge.vlm.ollama.OllamaVLM`.

Failure modes (mapped to custom :class:`WhisperError` subclasses):

- ``requests.ConnectionError`` → :class:`WhisperUnavailableError` (endpoint down).
- ``requests.Timeout`` → :class:`WhisperTimeoutError`.
- 401 / 403 → :class:`WhisperUnavailableError` ("API key rejected").
- Non-2xx response (other) → :class:`WhisperResponseError` carrying the
  status code and a truncated body.
- Malformed JSON / missing ``text`` key → :class:`WhisperResponseError`.
- Anything else under :class:`requests.RequestException` →
  :class:`WhisperUnavailableError`.
"""

from __future__ import annotations

import io
import logging

from .base import (
    WhisperResponseError,
    WhisperTimeoutError,
    WhisperUnavailableError,
)

logger = logging.getLogger(__name__)


class RemoteWhisper:
    """OpenAI-compatible Whisper API backend.

    Args:
        base_url: API base. ``"/audio/transcriptions"`` is appended.
            Trailing slashes are tolerated.
        model: Provider-specific model id. OpenAI uses ``whisper-1``;
            Groq has ``whisper-large-v3``; whisper.cpp self-hosted uses
            whatever name was configured.
        api_key: Bearer token (resolved by the caller from
            :attr:`WhisperConfig.remote_api_key_env`).
        timeout_s: Per-request HTTP budget. Whisper transcriptions on
            real audio commonly take 10-60 s; 300 s is a comfortable
            default ceiling.
    """

    name = "remote"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_s: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s

    # ── public API ────────────────────────────────────────────────────

    def warmup(self) -> None:
        """No-op for the remote backend.

        Live API roundtrips cost real money; we don't ping the endpoint
        at construction time. Authentication failures surface on the
        first transcribe call as :class:`WhisperUnavailableError`.
        """
        return None

    def transcribe(self, audio: bytes, *, language: str | None = None) -> str:
        """Transcribe ``audio`` via HTTP POST to ``/audio/transcriptions``.

        Returns the transcribed text from the JSON ``text`` field.
        The endpoint shape matches OpenAI's documented Whisper API
        and is implemented by most "OpenAI-compatible" providers.
        """
        import requests  # noqa: PLC0415 — lazy import (module docstring)

        url = f"{self.base_url}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        # multipart/form-data — the audio bytes are sent as a "file" field.
        files = {
            "file": ("audio.bin", io.BytesIO(audio), "application/octet-stream"),
        }
        data: dict[str, str] = {
            "model": self.model,
            "response_format": "json",
        }
        if language:
            data["language"] = language

        try:
            resp = requests.post(
                url,
                headers=headers,
                files=files,
                data=data,
                timeout=self.timeout_s,
            )
        except requests.Timeout as exc:
            raise WhisperTimeoutError(
                f"Remote Whisper exceeded {self.timeout_s}s budget at {url}"
            ) from exc
        except requests.ConnectionError as exc:
            raise WhisperUnavailableError(
                f"Cannot connect to Whisper endpoint at {self.base_url}: {exc}"
            ) from exc
        except requests.RequestException as exc:
            raise WhisperUnavailableError(f"Remote Whisper request failed: {exc}") from exc

        # Auth errors get their own bucket so callers can decide whether
        # to retry (rate-limit) vs surface a config error (401 / 403).
        if resp.status_code in (401, 403):
            raise WhisperUnavailableError(
                f"Whisper API key rejected (HTTP {resp.status_code}): {(resp.text or '')[:200]}"
            )

        if not resp.ok:
            body = (resp.text or "")[:200]
            raise WhisperResponseError(f"HTTP {resp.status_code}: {body}")

        try:
            payload = resp.json()
        except ValueError as exc:
            body = (resp.text or "")[:200]
            raise WhisperResponseError(f"Malformed JSON from Whisper endpoint: {body}") from exc

        if not isinstance(payload, dict) or "text" not in payload:
            raise WhisperResponseError(f"Whisper response missing 'text' key: {str(payload)[:200]}")

        text = payload["text"]
        if not isinstance(text, str):
            raise WhisperResponseError(
                f"Whisper response 'text' is not a string: {type(text).__name__}"
            )
        return text
