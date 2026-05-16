"""Phase G (G-12) — :class:`ClipRemoteEmbedder` unit tests (mocked HTTP)."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from corpus_forge.embedders.clip_remote import ClipRemoteEmbedder, _to_data_url
from corpus_forge.embedders.multimodal import (
    MultiModalEmbedder,
    MultiModalResponseError,
    MultiModalTimeoutError,
    MultiModalUnavailableError,
)


def _mk_resp(
    *,
    ok: bool = True,
    status: int = 200,
    json_data: dict | None = None,
    text: str = "",
):
    resp = MagicMock()
    resp.ok = ok
    resp.status_code = status
    resp.text = text
    resp.json = MagicMock(
        return_value=json_data
        if json_data is not None
        else {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
    )
    return resp


# ── _to_data_url helper ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("prefix", "expected"),
    [
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff\xe0", "image/jpeg"),
        (b"GIF89a", "image/gif"),
    ],
)
def test_to_data_url_detects_mime(prefix: bytes, expected: str) -> None:
    payload = prefix + b"\x00" * 10
    url = _to_data_url(payload)
    assert url.startswith(f"data:{expected};base64,")
    # Round-trip the base64 portion.
    b64 = url.split(",", 1)[1]
    assert base64.b64decode(b64) == payload


def test_to_data_url_webp_detection() -> None:
    payload = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8
    assert _to_data_url(payload).startswith("data:image/webp;base64,")


def test_to_data_url_unknown_falls_back_to_octet_stream() -> None:
    assert _to_data_url(b"random-bytes").startswith("data:application/octet-stream;base64,")


# ── Protocol surface ────────────────────────────────────────────────────


def test_satisfies_protocol() -> None:
    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    assert isinstance(e, MultiModalEmbedder)


def test_trailing_slash_stripped() -> None:
    e = ClipRemoteEmbedder(base_url="https://x/v1/", model="m", dimension=8, api_key="sk")
    assert e.base_url == "https://x/v1"


def test_warmup_is_noop() -> None:
    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    assert e.warmup() is None


# ── Happy path ──────────────────────────────────────────────────────────


def test_encode_text_returns_vectors() -> None:
    resp = _mk_resp(json_data={"data": [{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}]})
    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=2, api_key="sk")
    with patch("requests.post", return_value=resp) as mp:
        out = e.encode_text(["a", "b"])
    assert out == [[0.1, 0.2], [0.3, 0.4]]
    args, kwargs = mp.call_args
    assert args[0] == "https://x/embeddings"
    assert kwargs["headers"]["Authorization"] == "Bearer sk"
    assert kwargs["json"]["input"] == ["a", "b"]
    assert kwargs["json"]["model"] == "m"


def test_encode_image_sends_data_urls() -> None:
    resp = _mk_resp(json_data={"data": [{"embedding": [0.5, 0.6]}]})
    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=2, api_key="sk")
    with patch("requests.post", return_value=resp) as mp:
        e.encode_image([b"\x89PNG\r\n\x1a\nfake"])
    _args, kwargs = mp.call_args
    assert kwargs["json"]["input"][0].startswith("data:image/png;base64,")


def test_encode_text_empty_returns_empty() -> None:
    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    with patch("requests.post") as mp:
        assert e.encode_text([]) == []
    mp.assert_not_called()


def test_encode_image_empty_returns_empty() -> None:
    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    with patch("requests.post") as mp:
        assert e.encode_image([]) == []
    mp.assert_not_called()


# ── Failure modes ───────────────────────────────────────────────────────


def test_timeout_raises_timeout_error() -> None:
    import requests

    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    with (
        patch("requests.post", side_effect=requests.Timeout("slow")),
        pytest.raises(MultiModalTimeoutError),
    ):
        e.encode_text(["a"])


def test_connection_error_raises_unavailable() -> None:
    import requests

    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    with (
        patch("requests.post", side_effect=requests.ConnectionError("nope")),
        pytest.raises(MultiModalUnavailableError, match=r"(?i)connect"),
    ):
        e.encode_text(["a"])


def test_request_exception_raises_unavailable() -> None:
    import requests

    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    with (
        patch("requests.post", side_effect=requests.RequestException("weird")),
        pytest.raises(MultiModalUnavailableError, match=r"(?i)request"),
    ):
        e.encode_text(["a"])


@pytest.mark.parametrize("status", [401, 403])
def test_auth_failures_raise_unavailable(status: int) -> None:
    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    resp = _mk_resp(ok=False, status=status, text="bad key")
    with (
        patch("requests.post", return_value=resp),
        pytest.raises(MultiModalUnavailableError, match=r"(?i)api key|rejected"),
    ):
        e.encode_text(["a"])


def test_non_2xx_raises_response_error() -> None:
    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    resp = _mk_resp(ok=False, status=500, text="boom")
    with (
        patch("requests.post", return_value=resp),
        pytest.raises(MultiModalResponseError, match=r"500"),
    ):
        e.encode_text(["a"])


def test_malformed_json_raises_response_error() -> None:
    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    resp = _mk_resp(text="not json")
    resp.json = MagicMock(side_effect=ValueError("bad json"))
    with (
        patch("requests.post", return_value=resp),
        pytest.raises(MultiModalResponseError, match=r"(?i)malformed|json"),
    ):
        e.encode_text(["a"])


def test_missing_data_key_raises_response_error() -> None:
    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    resp = _mk_resp(json_data={"object": "list"})
    with (
        patch("requests.post", return_value=resp),
        pytest.raises(MultiModalResponseError, match=r"(?i)data"),
    ):
        e.encode_text(["a"])


def test_data_not_list_raises_response_error() -> None:
    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    resp = _mk_resp(json_data={"data": "wrong"})
    with (
        patch("requests.post", return_value=resp),
        pytest.raises(MultiModalResponseError, match=r"(?i)data.*list"),
    ):
        e.encode_text(["a"])


def test_embedding_missing_raises_response_error() -> None:
    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    resp = _mk_resp(json_data={"data": [{"object": "embedding"}]})
    with (
        patch("requests.post", return_value=resp),
        pytest.raises(MultiModalResponseError, match=r"(?i)embedding"),
    ):
        e.encode_text(["a"])


def test_embedding_not_list_raises_response_error() -> None:
    e = ClipRemoteEmbedder(base_url="https://x", model="m", dimension=8, api_key="sk")
    resp = _mk_resp(json_data={"data": [{"embedding": "wrong"}]})
    with (
        patch("requests.post", return_value=resp),
        pytest.raises(MultiModalResponseError, match=r"(?i)not a list"),
    ):
        e.encode_text(["a"])
