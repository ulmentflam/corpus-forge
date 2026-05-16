"""Unit tests for the shared HTTP transport helper.

Targets :mod:`corpus_forge._http` — the consolidated ``requests``
exception ladder + JSON-payload validator that backs every remote model
client (VLM, Whisper, code enricher, LLM classifier, multi-modal
embedder). The model-specific test suites cover the full happy path
end-to-end; this module covers the helper's own branches.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from corpus_forge._http import HttpErrors, bearer_headers, request_json


# Family-agnostic error triad for the tests below — we don't care which
# exception type is raised, only that the helper routes failures into
# the right slot in the triad.
class _UnavailableError(Exception): ...


class _TimeoutError(Exception): ...


class _ResponseError(Exception): ...


_ERR = HttpErrors(_UnavailableError, _TimeoutError, _ResponseError)


def _ok_response(payload: object) -> MagicMock:
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = payload
    resp.text = "ok"
    return resp


def _err_response(status: int, body: str = "boom") -> MagicMock:
    resp = MagicMock()
    resp.ok = False
    resp.status_code = status
    resp.text = body
    resp.json.side_effect = ValueError("not json")
    return resp


# ── bearer_headers ────────────────────────────────────────────────────


class TestBearerHeaders:
    def test_empty_when_no_key(self) -> None:
        assert bearer_headers(None) == {}
        assert bearer_headers("") == {}

    def test_attaches_bearer_when_key_present(self) -> None:
        assert bearer_headers("sk-abc") == {"Authorization": "Bearer sk-abc"}

    def test_extra_headers_merged(self) -> None:
        out = bearer_headers("sk-abc", extra={"X-Custom": "1"})
        assert out["Authorization"] == "Bearer sk-abc"
        assert out["X-Custom"] == "1"

    def test_extra_headers_can_override_authorization(self) -> None:
        """Caller-supplied ``Authorization`` wins — lets non-bearer
        schemes (Basic auth, custom signatures) slip past."""
        out = bearer_headers("sk-abc", extra={"Authorization": "Basic xyz"})
        assert out["Authorization"] == "Basic xyz"


# ── request_json: happy path ──────────────────────────────────────────


class TestRequestJsonHappyPath:
    def test_post_returns_parsed_dict(self) -> None:
        with patch("requests.post", return_value=_ok_response({"ok": True})) as mp:
            data = request_json(
                "POST",
                "http://x/api",
                timeout_s=1.0,
                errors=_ERR,
                label="test",
                json_body={"a": 1},
                api_key="sk",
            )
        assert data == {"ok": True}
        # Auth header attached, JSON body forwarded.
        kwargs = mp.call_args.kwargs
        assert kwargs["headers"]["Authorization"] == "Bearer sk"
        assert kwargs["json"] == {"a": 1}

    def test_get_dispatches_to_requests_get(self) -> None:
        with patch("requests.get", return_value=_ok_response({"ok": True})) as mg:
            request_json("GET", "http://x/api", timeout_s=1.0, errors=_ERR, label="test")
        assert mg.called

    def test_required_keys_present_passes(self) -> None:
        with patch("requests.post", return_value=_ok_response({"text": "hi", "extra": 1})):
            request_json(
                "POST",
                "http://x",
                timeout_s=1.0,
                errors=_ERR,
                label="test",
                required_keys=("text",),
            )

    def test_multipart_forwards_files_and_data(self) -> None:
        with patch("requests.post", return_value=_ok_response({"ok": True})) as mp:
            request_json(
                "POST",
                "http://x",
                timeout_s=1.0,
                errors=_ERR,
                label="test",
                files={"file": ("a.bin", b"x")},
                data={"model": "m"},
            )
        kwargs = mp.call_args.kwargs
        assert kwargs["files"] == {"file": ("a.bin", b"x")}
        assert kwargs["data"] == {"model": "m"}


# ── request_json: failure modes ───────────────────────────────────────


class TestRequestJsonFailures:
    def test_timeout_raises_timeout_error(self) -> None:
        import requests as _requests

        with (
            patch("requests.post", side_effect=_requests.Timeout()),
            pytest.raises(_TimeoutError),
        ):
            request_json("POST", "http://x", timeout_s=1.0, errors=_ERR, label="test")

    def test_connection_error_raises_unavailable(self) -> None:
        import requests as _requests

        with (
            patch("requests.post", side_effect=_requests.ConnectionError()),
            pytest.raises(_UnavailableError),
        ):
            request_json("POST", "http://x", timeout_s=1.0, errors=_ERR, label="test")

    def test_generic_request_exception_raises_unavailable(self) -> None:
        import requests as _requests

        with (
            patch("requests.post", side_effect=_requests.RequestException("boom")),
            pytest.raises(_UnavailableError),
        ):
            request_json("POST", "http://x", timeout_s=1.0, errors=_ERR, label="test")

    def test_401_raises_unavailable_with_auth_flag(self) -> None:
        with (
            patch("requests.post", return_value=_err_response(401)),
            pytest.raises(_UnavailableError, match=r"(?i)api key"),
        ):
            request_json("POST", "http://x", timeout_s=1.0, errors=_ERR, label="test")

    def test_403_raises_unavailable_with_auth_flag(self) -> None:
        with (
            patch("requests.post", return_value=_err_response(403)),
            pytest.raises(_UnavailableError),
        ):
            request_json("POST", "http://x", timeout_s=1.0, errors=_ERR, label="test")

    def test_401_raises_response_error_when_auth_flag_off(self) -> None:
        """With ``auth_to_unavailable=False`` (open local Ollama), 401
        is a normal response error — no special API-key bucket."""
        with (
            patch("requests.post", return_value=_err_response(401)),
            pytest.raises(_ResponseError),
        ):
            request_json(
                "POST",
                "http://x",
                timeout_s=1.0,
                errors=_ERR,
                label="test",
                auth_to_unavailable=False,
            )

    def test_non_2xx_raises_response_error(self) -> None:
        with (
            patch("requests.post", return_value=_err_response(500)),
            pytest.raises(_ResponseError, match="500"),
        ):
            request_json("POST", "http://x", timeout_s=1.0, errors=_ERR, label="test")

    def test_malformed_json_raises_response_error(self) -> None:
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = "not json"
        resp.json.side_effect = ValueError("nope")
        with (
            patch("requests.post", return_value=resp),
            pytest.raises(_ResponseError, match=r"(?i)malformed"),
        ):
            request_json("POST", "http://x", timeout_s=1.0, errors=_ERR, label="test")

    def test_non_object_json_raises_response_error(self) -> None:
        with (
            patch("requests.post", return_value=_ok_response(["unexpected"])),
            pytest.raises(_ResponseError, match=r"(?i)non-object"),
        ):
            request_json("POST", "http://x", timeout_s=1.0, errors=_ERR, label="test")

    def test_missing_required_key_raises_response_error(self) -> None:
        with (
            patch("requests.post", return_value=_ok_response({"other": 1})),
            pytest.raises(_ResponseError, match="text"),
        ):
            request_json(
                "POST",
                "http://x",
                timeout_s=1.0,
                errors=_ERR,
                label="test",
                required_keys=("text",),
            )


# ── health_check mode ────────────────────────────────────────────────


class TestHealthCheck:
    """In health-check mode every non-success collapses to Unavailable."""

    def test_timeout_becomes_unavailable(self) -> None:
        import requests as _requests

        with (
            patch("requests.get", side_effect=_requests.Timeout()),
            pytest.raises(_UnavailableError, match=r"(?i)did not respond"),
        ):
            request_json(
                "GET",
                "http://x",
                timeout_s=1.0,
                errors=_ERR,
                label="test",
                health_check=True,
            )

    def test_non_2xx_becomes_unavailable(self) -> None:
        with (
            patch("requests.get", return_value=_err_response(500)),
            pytest.raises(_UnavailableError),
        ):
            request_json(
                "GET",
                "http://x",
                timeout_s=1.0,
                errors=_ERR,
                label="test",
                health_check=True,
            )

    def test_malformed_json_becomes_unavailable(self) -> None:
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = "<html>"
        resp.json.side_effect = ValueError()
        with patch("requests.get", return_value=resp), pytest.raises(_UnavailableError):
            request_json(
                "GET",
                "http://x",
                timeout_s=1.0,
                errors=_ERR,
                label="test",
                health_check=True,
            )

    def test_missing_required_key_becomes_unavailable(self) -> None:
        with (
            patch("requests.get", return_value=_ok_response({"other": 1})),
            pytest.raises(_UnavailableError),
        ):
            request_json(
                "GET",
                "http://x",
                timeout_s=1.0,
                errors=_ERR,
                label="test",
                required_keys=("expected",),
                health_check=True,
            )
