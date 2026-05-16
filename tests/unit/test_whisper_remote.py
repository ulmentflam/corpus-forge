"""Phase G (G-03) — :class:`RemoteWhisper` unit tests with mocked HTTP."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.whisper.base import (
    WhisperBackend,
    WhisperResponseError,
    WhisperTimeoutError,
    WhisperUnavailableError,
)
from corpus_forge.whisper.remote import RemoteWhisper


def _mk_resp(*, ok: bool = True, status: int = 200, json_data: dict | None = None, text: str = ""):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status
    resp.text = text
    resp.json = MagicMock(
        return_value=json_data if json_data is not None else {"text": "transcribed"}
    )
    return resp


# ── Protocol surface ────────────────────────────────────────────────────


def test_satisfies_whisper_protocol() -> None:
    w = RemoteWhisper(base_url="https://x", model="whisper-1", api_key="sk")
    assert isinstance(w, WhisperBackend)


def test_name_is_remote() -> None:
    w = RemoteWhisper(base_url="https://x", model="whisper-1", api_key="sk")
    assert w.name == "remote"


def test_trailing_slash_stripped() -> None:
    w = RemoteWhisper(base_url="https://api.openai.com/v1/", model="whisper-1", api_key="sk")
    assert w.base_url == "https://api.openai.com/v1"


def test_warmup_is_noop() -> None:
    w = RemoteWhisper(base_url="https://x", model="whisper-1", api_key="sk")
    # Must not raise, must not make a network call.
    assert w.warmup() is None


# ── Happy path ──────────────────────────────────────────────────────────


def test_transcribe_returns_text_field() -> None:
    w = RemoteWhisper(base_url="https://api.openai.com/v1", model="whisper-1", api_key="sk")
    with patch("requests.post", return_value=_mk_resp(json_data={"text": "hello world"})) as mp:
        out = w.transcribe(b"audio-bytes")
    assert out == "hello world"
    args, kwargs = mp.call_args
    assert args[0] == "https://api.openai.com/v1/audio/transcriptions"
    assert kwargs["headers"]["Authorization"] == "Bearer sk"
    # multipart/form-data — file in `files`, model in `data`
    assert "file" in kwargs["files"]
    assert kwargs["data"]["model"] == "whisper-1"
    assert kwargs["data"]["response_format"] == "json"


def test_transcribe_includes_language_when_set() -> None:
    w = RemoteWhisper(base_url="https://x", model="whisper-1", api_key="sk")
    with patch("requests.post", return_value=_mk_resp()) as mp:
        w.transcribe(b"a", language="en")
    _args, kwargs = mp.call_args
    assert kwargs["data"]["language"] == "en"


def test_transcribe_omits_language_when_none() -> None:
    w = RemoteWhisper(base_url="https://x", model="whisper-1", api_key="sk")
    with patch("requests.post", return_value=_mk_resp()) as mp:
        w.transcribe(b"a")
    _args, kwargs = mp.call_args
    assert "language" not in kwargs["data"]


def test_transcribe_uses_configured_timeout() -> None:
    w = RemoteWhisper(base_url="https://x", model="m", api_key="sk", timeout_s=45.0)
    with patch("requests.post", return_value=_mk_resp()) as mp:
        w.transcribe(b"a")
    _args, kwargs = mp.call_args
    assert kwargs["timeout"] == 45.0


# ── Failure modes ───────────────────────────────────────────────────────


def test_transcribe_timeout_raises_whisper_timeout() -> None:
    import requests

    w = RemoteWhisper(base_url="https://x", model="m", api_key="sk", timeout_s=1.0)
    with (
        patch("requests.post", side_effect=requests.Timeout("slow")),
        pytest.raises(WhisperTimeoutError, match=r"1\.0s"),
    ):
        w.transcribe(b"a")


def test_transcribe_connection_error_raises_unavailable() -> None:
    import requests

    w = RemoteWhisper(base_url="https://x", model="m", api_key="sk")
    with (
        patch("requests.post", side_effect=requests.ConnectionError("nope")),
        pytest.raises(WhisperUnavailableError, match=r"(?i)connect"),
    ):
        w.transcribe(b"a")


def test_transcribe_request_exception_raises_unavailable() -> None:
    import requests

    w = RemoteWhisper(base_url="https://x", model="m", api_key="sk")
    with (
        patch("requests.post", side_effect=requests.RequestException("weird")),
        pytest.raises(WhisperUnavailableError, match=r"(?i)request failed"),
    ):
        w.transcribe(b"a")


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_raise_unavailable(status: int) -> None:
    w = RemoteWhisper(base_url="https://x", model="m", api_key="sk")
    resp = _mk_resp(ok=False, status=status, text="invalid api key")
    with (
        patch("requests.post", return_value=resp),
        pytest.raises(WhisperUnavailableError, match=r"(?i)api key|rejected"),
    ):
        w.transcribe(b"a")


def test_non_2xx_raises_response_error() -> None:
    w = RemoteWhisper(base_url="https://x", model="m", api_key="sk")
    resp = _mk_resp(ok=False, status=500, text="boom")
    with (
        patch("requests.post", return_value=resp),
        pytest.raises(WhisperResponseError, match=r"500"),
    ):
        w.transcribe(b"a")


def test_malformed_json_raises_response_error() -> None:
    w = RemoteWhisper(base_url="https://x", model="m", api_key="sk")
    resp = _mk_resp(text="not json")
    resp.json = MagicMock(side_effect=ValueError("bad json"))
    with (
        patch("requests.post", return_value=resp),
        pytest.raises(WhisperResponseError, match=r"(?i)malformed|json"),
    ):
        w.transcribe(b"a")


def test_missing_text_key_raises_response_error() -> None:
    w = RemoteWhisper(base_url="https://x", model="m", api_key="sk")
    resp = _mk_resp(json_data={"data": {"some": "thing"}})
    with (
        patch("requests.post", return_value=resp),
        pytest.raises(WhisperResponseError, match=r"(?i)text"),
    ):
        w.transcribe(b"a")


def test_text_not_string_raises_response_error() -> None:
    w = RemoteWhisper(base_url="https://x", model="m", api_key="sk")
    resp = _mk_resp(json_data={"text": 123})
    with (
        patch("requests.post", return_value=resp),
        pytest.raises(WhisperResponseError, match=r"(?i)not a string"),
    ):
        w.transcribe(b"a")


def test_payload_not_dict_raises_response_error() -> None:
    w = RemoteWhisper(base_url="https://x", model="m", api_key="sk")
    resp = _mk_resp(json_data=None)
    resp.json = MagicMock(return_value=["unexpected"])
    with (
        patch("requests.post", return_value=resp),
        # Either wording — "non-object JSON" or "missing 'text' key" — is
        # a faithful description of the failure mode.
        pytest.raises(WhisperResponseError, match=r"(?i)text|non-object"),
    ):
        w.transcribe(b"a")
