"""Phase G — :class:`RemoteWhisper` HTTP backend (OpenAI-compat).

Talks to any OpenAI-compatible Whisper endpoint
(``POST {base_url}/audio/transcriptions``) — OpenAI itself, Groq (free
tier, very fast), Replicate, self-hosted whisper.cpp via HTTP.
Transport-level error mapping is delegated to
:mod:`corpus_forge._http` and shared with every other remote model
backend in the codebase (VLM, code enricher, LLM classifier, CLIP
embedder).

Failure modes:

- ``ConnectionError`` / generic ``RequestException`` →
  :class:`WhisperUnavailableError` (endpoint down).
- ``Timeout`` → :class:`WhisperTimeoutError`.
- 401 / 403 → :class:`WhisperUnavailableError` ("API key rejected").
- Non-2xx (other) / malformed JSON / missing ``text`` →
  :class:`WhisperResponseError`.
"""

from __future__ import annotations

import io
import logging

from corpus_forge._http import HttpErrors, request_json

from .base import (
    WhisperResponseError,
    WhisperTimeoutError,
    WhisperUnavailableError,
)

logger = logging.getLogger(__name__)

_ERR = HttpErrors(WhisperUnavailableError, WhisperTimeoutError, WhisperResponseError)


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

        Returns the transcribed text from the JSON ``text`` field. The
        endpoint shape matches OpenAI's documented Whisper API.
        """
        form: dict[str, str] = {"model": self.model, "response_format": "json"}
        if language:
            form["language"] = language

        payload = request_json(
            "POST",
            f"{self.base_url}/audio/transcriptions",
            timeout_s=self.timeout_s,
            errors=_ERR,
            label="Remote Whisper",
            base_url=self.base_url,
            api_key=self.api_key,
            files={"file": ("audio.bin", io.BytesIO(audio), "application/octet-stream")},
            data=form,
            required_keys=("text",),
        )

        text = payload["text"]
        if not isinstance(text, str):
            raise WhisperResponseError(
                f"Whisper response 'text' is not a string: {type(text).__name__}"
            )
        return text
